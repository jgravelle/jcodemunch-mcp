"""A Ruby import without an extension never resolves to another language's file (L-294).

Ruby's `require` and `require_relative` load `.rb` files (and compiled
extensions, which are not indexed). Before this, an extensionless Ruby
specifier tried `.rb` first and then fell through to the JavaScript and
Python forms, so `require_relative '../utils'` with only `utils.py` present
became an edge to `utils.py`, a file Ruby can never load. That edge makes a
dead Python file look imported. The resolver's rule for a guess across
languages is to err toward no edge.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.parser.imports import clear_resolution_caches, resolve_specifier


@pytest.fixture(autouse=True)
def _fresh_memo():
    clear_resolution_caches()
    yield
    clear_resolution_caches()


@pytest.mark.parametrize("other", ["utils.py", "utils.js", "utils.ts", "utils/index.js", "utils/__init__.py", "utils.go"])
def test_a_relative_ruby_require_takes_no_other_language(other):
    files = frozenset({"lib/" + other, "app/main.rb"})
    assert resolve_specifier("../lib/utils", "app/main.rb", files) is None


@pytest.mark.parametrize("other", ["utils.py", "utils.js", "utils/__init__.py"])
def test_a_root_ruby_require_takes_no_other_language(other):
    files = frozenset({other, "app/main.rb"})
    assert resolve_specifier("utils", "app/main.rb", files) is None


def test_a_dotted_ruby_name_takes_no_other_language():
    files = frozenset({"lib/foo.service.ts", "app/main.rb"})
    assert resolve_specifier("../lib/foo.service", "app/main.rb", files) is None


def test_a_ruby_require_still_finds_its_rb_file():
    files = frozenset({"lib/utils.rb", "lib/utils.py", "app/main.rb"})
    assert resolve_specifier("../lib/utils", "app/main.rb", files) == "lib/utils.rb"
    assert resolve_specifier("lib/utils", "app/main.rb", files) == "lib/utils.rb"


def test_a_ruby_require_naming_the_file_exactly_still_resolves():
    files = frozenset({"lib/utils.rb", "app/main.rb"})
    assert resolve_specifier("../lib/utils.rb", "app/main.rb", files) == "lib/utils.rb"


def test_other_importers_keep_their_cross_language_order():
    files = frozenset({"lib/utils.py", "app/main.js", "app/main.py"})
    assert resolve_specifier("../lib/utils", "app/main.js", files) == "lib/utils.py"
    files = frozenset({"lib/utils.js", "app/main.py"})
    assert resolve_specifier("../lib/utils", "app/main.py", files) == "lib/utils.js"


def test_a_rake_importer_is_ruby_too():
    files = frozenset({"lib/utils.py", "tasks/build.rake"})
    assert resolve_specifier("../lib/utils", "tasks/build.rake", files) is None
    files = frozenset({"lib/utils.py", "lib/utils.rb", "tasks/build.rake"})
    assert resolve_specifier("../lib/utils", "tasks/build.rake", files) == "lib/utils.rb"


def test_every_ruby_extension_is_a_ruby_importer():
    from jcodemunch_mcp.parser import imports
    from jcodemunch_mcp.parser.languages import LANGUAGE_EXTENSIONS

    ruby = {e for e, lang in LANGUAGE_EXTENSIONS.items() if lang == "ruby"}
    assert ruby and ruby == set(imports._RUBY_IMPORTERS)


def test_a_bare_ruby_require_takes_no_sql_file_by_stem():
    files = frozenset({"models/utils.sql", "app/main.rb"})
    assert resolve_specifier("utils", "app/main.rb", files) is None


def test_the_sql_stem_fallback_still_serves_other_importers():
    files = frozenset({"models/dim_client.sql", "models/fct.sql"})
    assert resolve_specifier("dim_client", "models/fct.sql", files) == "models/dim_client.sql"
