"""An import without an extension resolves to the importer's own language first (#1316).

Reported by @skibadubskiybadubs (split from #1315): on OpenStudio,
`require_relative '../measure'` in a Ruby test resolved to `../measure.py`
while `measure.rb` sat in the same folder. `_candidates` adds the known
extensions in one fixed order (JavaScript, then `.py`, then `.rb`) and never
asks what language the importing file is in, so the first file that exists
wins whatever its language.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.parser.imports import clear_resolution_caches, resolve_specifier


@pytest.fixture(autouse=True)
def _fresh_memo():
    clear_resolution_caches()
    yield
    clear_resolution_caches()


def test_a_ruby_require_relative_finds_the_rb_file_beside_a_py_one():
    files = {
        "templates/ModelMeasure/measure.rb",
        "templates/ModelMeasure/measure.py",
        "templates/ModelMeasure/tests/ModelMeasure_test.rb",
    }
    got = resolve_specifier(
        "../measure",
        "templates/ModelMeasure/tests/ModelMeasure_test.rb",
        frozenset(files),
    )
    assert got == "templates/ModelMeasure/measure.rb"


def test_a_ruby_require_from_the_root_finds_the_rb_file():
    files = {"lib/helper.rb", "lib/helper.js", "app/main.rb"}
    assert (
        resolve_specifier("lib/helper", "app/main.rb", frozenset(files))
        == "lib/helper.rb"
    )


def test_a_python_import_from_the_root_finds_the_py_file():
    files = {"utils.py", "utils.js", "app.py"}
    assert resolve_specifier("utils", "app.py", frozenset(files)) == "utils.py"


def test_a_js_import_keeps_its_order():
    files = {"web/util.js", "web/util.py", "web/app.js"}
    assert resolve_specifier("./util", "web/app.js", frozenset(files)) == "web/util.js"


def test_another_language_is_still_found_when_the_own_one_is_absent():
    files = {"lib/only.py", "app/main.rb"}
    assert (
        resolve_specifier("../lib/only", "app/main.rb", frozenset(files))
        == "lib/only.py"
    )


def test_the_shared_memo_does_not_carry_one_importers_answer_to_another():
    """The tail memo is keyed without the importer. A Ruby answer cached first
    must not be served to a Python importer, or the reverse."""
    files = frozenset({"utils.py", "utils.rb", "a.py", "b.rb"})
    assert resolve_specifier("utils", "b.rb", files) == "utils.rb"
    assert resolve_specifier("utils", "a.py", files) == "utils.py"
    assert resolve_specifier("utils", "b.rb", files) == "utils.rb"


def test_a_python_import_finds_a_package_folder_before_a_js_file():
    files = frozenset({"x/__init__.py", "x.js", "app.py"})
    assert resolve_specifier("x", "app.py", files) == "x/__init__.py"


def test_a_python_module_still_wins_over_its_package_folder():
    files = frozenset({"x.py", "x/__init__.py", "x.js", "app.py"})
    assert resolve_specifier("x", "app.py", files) == "x.py"
