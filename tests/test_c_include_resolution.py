"""A C or C++ `#include` resolves the way the compiler finds it (#1315).

Reported by @skibadubskiybadubs on OpenStudio: `resolve_specifier` had no
C/C++ rule. Only a specifier starting with `.` was joined to the importer's
folder, so `#include "X.hpp"` beside the importer (the most common C++ form)
resolved only when the header sat at the repo root, and `#include
<model/Model.hpp>` never resolved when the include root was `src/`. 12,291 of
31,329 file edges were missing on a 4,507-file repo.

The extracted edge keeps only the path text, not the quotes or brackets, so the
rule cannot tell `"x.h"` from `<x.h>`. Both are tried from the importer's
folder, then the repo root, then `include/` and `src/`.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.parser.imports import resolve_specifier

FILES = {
    "python/engine/PythonEngine.cpp",
    "python/engine/PythonEngine.hpp",
    "src/modelica/ModelicaFileImpl.cpp",
    "src/modelica/antlr/modelicaLexer.h",
    "src/model/AirLoopHVAC.cpp",
    "src/model/Model.hpp",
    "src/utilities/core/Assert.hpp",
    "developer/cmake/test.cpp",
    "include/api/Public.h",
    "lib/widget.c",
    "lib/widget.h",
    "app/view.mm",
    "app/view.h",
    "sketch/blink.ino",
    "sketch/pins.h",
    "Root.h",
}


@pytest.mark.parametrize(
    "spec, importer, expected",
    [
        # same folder, quoted, no slash (12,135 of the missing edges)
        (
            "PythonEngine.hpp",
            "python/engine/PythonEngine.cpp",
            "python/engine/PythonEngine.hpp",
        ),
        # below the importer's folder
        (
            "antlr/modelicaLexer.h",
            "src/modelica/ModelicaFileImpl.cpp",
            "src/modelica/antlr/modelicaLexer.h",
        ),
        # through the src/ include root
        ("model/Model.hpp", "developer/cmake/test.cpp", "src/model/Model.hpp"),
        # through the include/ include root
        ("api/Public.h", "src/model/AirLoopHVAC.cpp", "include/api/Public.h"),
        # each C-family language, not only C++
        ("widget.h", "lib/widget.c", "lib/widget.h"),
        ("view.h", "app/view.mm", "app/view.h"),
        ("pins.h", "sketch/blink.ino", "sketch/pins.h"),
        # a header including a sibling header
        ("Model.hpp", "src/model/Model.hpp", "src/model/Model.hpp"),
    ],
)
def test_an_include_resolves_where_the_compiler_finds_it(spec, importer, expected):
    assert resolve_specifier(spec, importer, FILES) == expected


def test_the_importer_folder_wins_over_the_repo_root():
    files = FILES | {"lib/Root.h"}
    assert resolve_specifier("Root.h", "lib/widget.c", files) == "lib/Root.h"
    assert resolve_specifier("Root.h", "src/model/AirLoopHVAC.cpp", files) == "Root.h"


def test_a_leading_dot_keeps_todays_reading():
    assert resolve_specifier(
        "../utilities/core/Assert.hpp", "src/model/AirLoopHVAC.cpp", FILES
    ) == ("src/utilities/core/Assert.hpp")


def test_a_system_header_does_not_land_on_another_languages_file():
    """`<vector>` names a file exactly. Trying JS or Python extensions on it
    would link a C++ file to `vector.py` beside it."""
    files = FILES | {"lib/vector.py", "lib/string.js"}
    assert resolve_specifier("vector", "lib/widget.c", files) is None
    assert resolve_specifier("string", "lib/widget.c", files) is None


def test_a_non_c_importer_does_not_take_the_c_rule():
    """A bare JS specifier is a package name, not a file beside the importer."""
    files = {"web/app.js", "web/lodash.js"}
    assert resolve_specifier("lodash", "web/app.js", files) is None


def test_find_importers_sees_a_same_folder_include(tmp_path):
    """The user's entry point: the edge reaches find_importers."""
    from jcodemunch_mcp.tools.find_importers import find_importers
    from jcodemunch_mcp.tools.index_folder import index_folder

    proj = tmp_path / "proj"
    (proj / "engine").mkdir(parents=True)
    (proj / "src" / "model").mkdir(parents=True)
    (proj / "engine" / "Engine.hpp").write_text(
        "class Engine { public: int run(); };\n", encoding="utf-8"
    )
    (proj / "engine" / "Engine.cpp").write_text(
        '#include "Engine.hpp"\n#include <model/Model.hpp>\nint Engine::run() { return 0; }\n',
        encoding="utf-8",
    )
    (proj / "src" / "model" / "Model.hpp").write_text(
        "struct Model { int x; };\n", encoding="utf-8"
    )
    store = tmp_path / "store"
    res = index_folder(str(proj), use_ai_summaries=False, storage_path=str(store))
    assert res["success"] is True

    def importers(path):
        out = find_importers(res["repo"], path, storage_path=str(store))
        return {i["file"] for i in out.get("importers", [])}

    assert importers("engine/Engine.hpp") == {"engine/Engine.cpp"}
    assert importers("src/model/Model.hpp") == {"engine/Engine.cpp"}


@pytest.mark.parametrize(
    "source, path, lang, expected",
    [
        (
            '#import "View.h"\n#import <Foundation/Foundation.h>\n',
            "a/b.m",
            "objc",
            ["View.h", "Foundation/Foundation.h"],
        ),
        ('  #include "x.h"\n', "a/b.c", "c", ["x.h"]),
        ('# include "x.h"\n', "a/b.c", "c", ["x.h"]),
        ("#if A\n#  include <y.h>\n#endif\n", "a/b.cpp", "cpp", ["y.h"]),
        ("#include_next <q.h>\n", "a/b.c", "c", ["q.h"]),
        ('#include "plain.h"\n', "a/b.c", "c", ["plain.h"]),
    ],
)
def test_every_spelling_of_an_include_is_an_edge(source, path, lang, expected):
    """The same input in the spellings a preprocessor accepts. Only the
    unindented `#include` used to be read, so an Objective-C file's `#import`
    lines, the usual form there, gave no edge at all."""
    from jcodemunch_mcp.parser.imports import extract_imports

    assert [e["specifier"] for e in extract_imports(source, path, lang)] == expected


def test_a_comment_or_string_that_names_include_is_not_an_edge():
    from jcodemunch_mcp.parser.imports import extract_imports

    src = '// #include "nope.h"\nconst char *s = "#include <nope2.h>";\n'
    assert extract_imports(src, "a/b.c", "c") == []


def test_the_parser_generation_moved_with_the_stored_imports():
    """`files.imports` changes on unchanged content, so only a re-parse gives an
    existing index the new edges."""
    from jcodemunch_mcp.storage.index_store import PARSER_GENERATION

    assert PARSER_GENERATION >= 28


@pytest.mark.parametrize(
    "spec, importer, extra",
    [
        # a system header beside a root .py file of the same stem
        ("vector", "main.cpp", {"vector.py", "main.cpp"}),
        # the SQL stem fallback
        ("vector", "lib/widget.c", {"db/vector.sql"}),
        # the Python module-path reading of a dotted name
        ("config.h", "lib/widget.c", {"config/h.py"}),
    ],
)
def test_an_include_the_rule_cannot_place_reaches_no_other_languages_rules(
    spec, importer, extra
):
    """A C include names a file. On a miss, the other languages' readings
    (extensions, stems, module paths) can only invent an edge."""
    assert resolve_specifier(spec, importer, FILES | extra) is None


def test_include_is_tried_before_src_as_an_include_root():
    files = FILES | {"include/model/Model.hpp"}
    assert (
        resolve_specifier("model/Model.hpp", "developer/cmake/test.cpp", files)
        == "include/model/Model.hpp"
    )


def test_clearing_the_resolution_caches_forgets_the_language_gate(monkeypatch):
    """The gate is cached by path; a changed extension table is seen once the
    caches are cleared (review of #1315)."""
    from jcodemunch_mcp.parser import imports
    from jcodemunch_mcp.parser import languages

    assert imports._is_c_family_importer("x/a.zzc") is False
    monkeypatch.setitem(languages.LANGUAGE_EXTENSIONS, ".zzc", "c")
    imports.clear_resolution_caches()
    assert imports._is_c_family_importer("x/a.zzc") is True
    monkeypatch.undo()
    imports.clear_resolution_caches()
