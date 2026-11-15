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
