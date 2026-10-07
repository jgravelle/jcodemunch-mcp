"""A macro's docstring is the Jinja comment directly above it (LEDGER L-126).

The lookup took the FIRST `{# ... #}` of the file for any directive whose
preceding text ended in `#}`: it searched from the start of the file and
checked only how the text before the directive ended. So in a file of
documented macros every macro after the first carried the first one's
description, and a search by what a macro does found the wrong macro.

The cases go through `parse_file`, the entry point an index uses, for dbt SQL
and for a Jinja template (the template parsers share the directive loop).
"""

import pytest

import jcodemunch_mcp.config as config_mod
from jcodemunch_mcp.parser import sql_preprocessor
from jcodemunch_mcp.parser.extractor import parse_file
from jcodemunch_mcp.storage.index_store import PARSER_GENERATION


@pytest.fixture(autouse=True)
def _every_language_enabled(monkeypatch):
    # a developer config may disable a language; no symbol would then read as no docstring
    monkeypatch.setattr(config_mod, "is_language_enabled", lambda *a, **k: True)


def _docstrings(text: str) -> dict[str, str]:
    return {d.name: d.docstring for d in sql_preprocessor.extract_dbt_directives(text.encode("utf-8"))}


_THREE_MACROS = (
    "{# about first #}\n"
    "{% macro first() %}select 1{% endmacro %}\n"
    "\n"
    "{# about second #}\n"
    "{% macro second() %}select 2{% endmacro %}\n"
    "\n"
    "-- about third\n"
    "{% macro third() %}select 3{% endmacro %}\n"
)


def test_each_macro_of_a_dbt_file_gets_its_own_comment():
    symbols = {s.name: s for s in parse_file(_THREE_MACROS, "macros/keys.sql", "sql")}
    assert {"first", "second", "third"} <= set(symbols), sorted(symbols)
    assert symbols["first"].docstring == "about first"
    assert symbols["second"].docstring == "about second"
    assert symbols["third"].docstring == "about third"


def test_each_macro_of_a_jinja_template_gets_its_own_comment():
    text = (
        "{# renders a field #}\n{% macro field(name) %}<input name='{{ name }}'>{% endmacro %}\n"
        "{# renders a button #}\n{% macro button(label) %}<button>{{ label }}</button>{% endmacro %}\n"
    )
    symbols = {s.name: s for s in parse_file(text, "templates/forms.j2", "jinja")}
    assert {"field", "button"} <= set(symbols), sorted(symbols)
    assert symbols["field"].docstring == "renders a field"
    assert symbols["button"].docstring == "renders a button"


def test_each_macro_and_block_of_a_twig_template_gets_its_own_comment():
    text = (
        "{# renders a field #}\n{% macro field(name) %}<input name='{{ name }}'>{% endmacro %}\n"
        "{# renders a button #}\n{% macro button(label) %}<button>{{ label }}</button>{% endmacro %}\n"
        "{# the body #}\n{% block body %}<p>hi</p>{% endblock %}\n"
    )
    symbols = {s.name: s for s in parse_file(text, "templates/forms.twig", "twig")}
    assert {"field", "button", "body"} <= set(symbols), sorted(symbols)
    assert symbols["field"].docstring == "renders a field"
    assert symbols["button"].docstring == "renders a button"
    assert symbols["body"].docstring == "the body"


def test_a_comment_elsewhere_in_the_file_is_nobodys_docstring():
    found = _docstrings(
        "{# licence header #}\n"
        "select 1\n"
        "{% macro bare() %}{% endmacro %}\n"
        "{# trailing note #}\n"
    )
    assert found == {"bare": ""}


@pytest.mark.parametrize(
    "above, expected",
    [
        ("{# a #}{# b #}", "b"),                      # two comments on one line: the nearer one
        ("{# a #}\n\n\n{# b #}\n\n", "b"),             # blank lines before and after
        ("{# a #} x {# b #}", "b"),
        ("{#/**\n * builds the key\n * from two columns\n */#}", "builds the key\nfrom two columns"),
        ("{# a #}\n#}", ""),                           # a stray `#}` closes no comment
        ("{# a #}\nselect 1 #}", ""),                  # text that only ends like a comment
        ("{# a #}\n{#- b -#}", "b"),                    # the dashes trim whitespace; they are not the comment's
        ("{# a #}\n{#-b-#}", "b"),
        ("{# a #}\n{#--#}", ""),
        ("{# a #}\n{#-- b --#}", "- b -"),              # one mark per side, no more
        ("{# a #}\n{#+ b +#}", "b"),                    # Jinja's `+`
        ("{# a #}\n{#~ b ~#}", "b"),                    # Twig's `~`
        ("{# a #}\n{#- b +#}", "b"),
        ("{# a #}\n{# + b ~ #}", "+ b ~"),
        ("{# a #}\n{# - b - #}", "- b -"),              # a dash that is not at the delimiter stays
        ("{# a #}\n{# open", ""),                      # the comment above never closes
        ("{# a\n{# b #}", "a\n{# b"),                  # a comment runs to its first `#}`
        ("{# a #}\n-- b", "b"),                        # a `--` comment is nearer than the Jinja one
        ("-- a\n{# b #}", "b"),
        ("{##}", ""),
    ],
)
def test_the_docstring_is_the_comment_that_ends_just_above(above, expected):
    found = _docstrings("{# header #}\n{% macro head() %}{% endmacro %}\n" + above + "\n{% macro m() %}{% endmacro %}\n")
    assert found["head"] == "header"
    assert found["m"] == expected


def test_many_documented_macros_each_keep_their_own():
    count = 300
    text = "".join(f"{{# about m{i} #}}\n{{% macro m{i}() %}}{{% endmacro %}}\n" for i in range(count))
    assert _docstrings(text) == {f"m{i}": f"about m{i}" for i in range(count)}


def test_the_parser_generation_moved_so_an_existing_index_is_re_read():
    """The docstring changes on UNCHANGED content, and the incremental path
    never re-reads unchanged content (Standing lesson 08-05)."""
    assert PARSER_GENERATION >= 10
