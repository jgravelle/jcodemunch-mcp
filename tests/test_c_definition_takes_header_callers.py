"""A C definition is called through the header that declares it.

`b.c` includes `a.h` and calls `f`; `a.c` defines `f`. The import graph links
`b.c` to `a.h`, not to `a.c`, so `get_call_hierarchy` filed the caller under
the header's declaration and the definition showed none. The definition now
takes the callers found for a same-named declaration in a header its own file
includes. A same-named function in a file that does not include the header
takes nothing.
"""

from __future__ import annotations

from jcodemunch_mcp.tools.get_call_hierarchy import get_call_hierarchy
from jcodemunch_mcp.tools.index_folder import index_folder


def _index(tmp_path, files: dict[str, str]):
    src = tmp_path / "src"
    for rel, text in files.items():
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    store = tmp_path / "store"
    r = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert r["success"], r
    return r["repo"], str(store)


def _callers(repo, store, symbol_id):
    h = get_call_hierarchy(
        repo=repo, symbol_id=symbol_id, direction="callers", storage_path=store
    )
    assert "error" not in h, h
    return h


HEADER = "#ifndef A_H\n#define A_H\nint f(int x);\n#endif\n"
DEFINES = '#include "a.h"\n\nint f(int x) {\n    return x + 1;\n}\n'
CALLS = '#include "a.h"\n\nint g(void) {\n    return f(2);\n}\n'


def test_the_definition_takes_the_header_callers(tmp_path):
    repo, store = _index(tmp_path, {"a.h": HEADER, "a.c": DEFINES, "b.c": CALLS})
    h = _callers(repo, store, "a.c::f#function")
    assert [(c["file"], c["name"]) for c in h["callers"]] == [("b.c", "g")], h


def test_the_header_declaration_keeps_its_callers(tmp_path):
    repo, store = _index(tmp_path, {"a.h": HEADER, "a.c": DEFINES, "b.c": CALLS})
    h = _callers(repo, store, "a.h::f#function")
    assert [(c["file"], c["name"]) for c in h["callers"]] == [("b.c", "g")], h


def test_a_cpp_definition_takes_the_header_callers(tmp_path):
    repo, store = _index(
        tmp_path,
        {
            "a.hpp": "#pragma once\nint f(int x);\n",
            "a.cpp": '#include "a.hpp"\n\nint f(int x) {\n    return x + 1;\n}\n',
            "b.cpp": '#include "a.hpp"\n\nint g() {\n    return f(2);\n}\n',
        },
    )
    h = _callers(repo, store, "a.cpp::f#function")
    assert [(c["file"], c["name"]) for c in h["callers"]] == [("b.cpp", "g")], h


def test_a_source_that_does_not_include_the_header_takes_nothing(tmp_path):
    repo, store = _index(
        tmp_path,
        {
            "a.h": HEADER,
            "a.c": DEFINES,
            "b.c": CALLS,
            "c.c": "static int f(int x) {\n    return x - 1;\n}\n",
        },
    )
    h = _callers(repo, store, "c.c::f#function")
    assert h["callers"] == [], h


STATIC_INCLUDES = '#include "a.h"\n\nstatic int f(int x) {\n    return x - 1;\n}\n\nint h(void) {\n    return f(1);\n}\n'


def test_a_static_definition_that_includes_the_header_takes_nothing(tmp_path):
    # A file-local f is not the f the header declares; b.c cannot reach it.
    # (L-339) Its own file's h does call it: this asserted `[]`, which held
    # only while the text search never read the definition's own file.
    repo, store = _index(
        tmp_path, {"a.h": HEADER, "a.c": DEFINES, "b.c": CALLS, "c.c": STATIC_INCLUDES}
    )
    h = _callers(repo, store, "c.c::f#function")
    assert [(c["file"], c["name"]) for c in h["callers"]] == [("c.c", "h")], h


def test_a_file_with_its_own_f_is_not_a_caller_of_the_definition(tmp_path):
    # c.c's h calls c.c's own static f, not the one a.c defines.
    repo, store = _index(
        tmp_path, {"a.h": HEADER, "a.c": DEFINES, "b.c": CALLS, "c.c": STATIC_INCLUDES}
    )
    h = _callers(repo, store, "a.c::f#function")
    assert [(c["file"], c["name"]) for c in h["callers"]] == [("b.c", "g")], h


def test_an_attribute_does_not_hide_static(tmp_path):
    attributed = STATIC_INCLUDES.replace("static int f", "__attribute__((unused)) static int f")
    repo, store = _index(
        tmp_path, {"a.h": HEADER, "a.c": DEFINES, "b.c": CALLS, "c.c": attributed}
    )
    h = _callers(repo, store, "c.c::f#function")
    assert [(c["file"], c["name"]) for c in h["callers"]] == [("c.c", "h")], h


def test_a_caller_that_repeats_the_prototype_is_still_a_caller(tmp_path):
    # A `;` prototype declares the header's f; it is not a definition of its own.
    repeats = CALLS.replace("int g(void)", "int f(int x);" + "\n" + "int g(void)")
    repo, store = _index(tmp_path, {"a.h": HEADER, "a.c": DEFINES, "b.c": repeats})
    h = _callers(repo, store, "a.c::f#function")
    assert [(c["file"], c["name"]) for c in h["callers"]] == [("b.c", "g")], h


def test_a_cpp_file_with_its_own_overload_is_still_a_caller(tmp_path):
    # f(2) in b.cpp resolves to the header's f(int), not b.cpp's f(double).
    repo, store = _index(
        tmp_path,
        {
            "a.hpp": "#pragma once\nint f(int x);\n",
            "a.cpp": '#include "a.hpp"\n\nint f(int x) {\n    return x + 1;\n}\n',
            "b.cpp": '#include "a.hpp"\n\nstatic int f(double d) {\n    return 0;\n}\n\nint g() {\n    return f(2);\n}\n',
        },
    )
    h = _callers(repo, store, "a.cpp::f#function")
    assert ("b.cpp", "g") in [(c["file"], c["name"]) for c in h["callers"]], h


def test_a_word_inside_a_macro_argument_is_not_static(tmp_path):
    # MACRO(static) is an argument, not the storage class: a.c's f is public.
    macro = DEFINES.replace("int f(int x) {", "EXPORT(static) int f(int x) {")
    repo, store = _index(tmp_path, {"a.h": HEADER, "a.c": macro, "b.c": CALLS})
    h = _callers(repo, store, "a.c::f#function")
    assert [(c["file"], c["name"]) for c in h["callers"]] == [("b.c", "g")], h
