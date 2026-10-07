"""A macro's docstring is the Jinja comment directly above it (LEDGER L-126).

The lookup took the FIRST `{# ... #}` of the file for any directive whose
preceding text ended in `#}`: it searched from the start of the file and
checked only how the text before the directive ended. So in a file of
documented macros every macro after the first carried the first one's
description, and a search by what a macro does found the wrong macro.

Three rules came after (L-129, L-130, L-131): the comment is first on its line
(only other comments may stand before it there), a `{#` inside `{{ ... }}` or
`{% ... %}` opens no comment, and a whitespace-control mark is its own
dialect's (`+` in Jinja and dbt, `~` in Twig, `-` in both).

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
        ("{# a #} x {# b #}", ""),                     # L-129: `b` trails code on its line
        ("x {# b #}", ""),
        ("{% macro z() %}x{% endmacro %} {# end of z #}", ""),
        ("{% macro z() %}x{% endmacro %} {# end of z #}\n\n", ""),
        ("x {# a #}{# b #}", ""),                       # a comment before it does not make it start the line
        ("  \t{# b #}", "b"),                           # indented is still first on its line
        ("-- note {# b #}", "b"),                        # a `--` lead is a comment, not code
        ("  -- note {# a #} {# b #}", "b"),
        ("select 1 -- note {# b #}", ""),                # but code before the `--` is code
        ("{# a\nstill a #} {# b #}", "b"),               # only comments before it, back to a line start
        ("x {# a\nstill a #} {# b #}", ""),
        ("{{ x }}{# b #}", ""),
        ("{{ '{#' }}\n{# b #}", "b"),                  # L-130: a `{#` in an expression opens no comment
        ("{% set x = '{#' %}\n{# b #}", "b"),
        ("{% if '{#' %}\n{# b #}", "b"),
        ("{% raw %}{{ {% endraw %}\n{# b #}", "b"),       # a raw block: nothing in it is a delimiter
        ("{%- raw -%}{# no {% x {{ y {%- endraw -%}\n{# b #}", "b"),
        ("{% raw %}{{ {% endraw %} {# b #}", ""),          # and it is still code before a trailing comment
        ("{% raw %}\n{# b #}", "b"),                      # no end tag anywhere: `raw` is an ordinary tag
        (chr(0x200B) + "{# b #}", "b"),                  # a zero-width space is not text
        (chr(0xFEFF) + "-- b", "b"),
        ("{# b {{ x }} {% y %} #}", "b {{ x }} {% y %}"),  # and an expression in a comment is the comment's
        ("{# a #}\n{#~approx 5 rows#}", "~approx 5 rows"),  # L-131: `~` is not a Jinja mark
        ("{#/**\n * builds the key\n * from two columns\n */#}", "builds the key\nfrom two columns"),
        ("{# a #}\n#}", ""),                           # a stray `#}` closes no comment
        ("{# a #}\nselect 1 #}", ""),                  # text that only ends like a comment
        ("{# a #}\n{#- b -#}", "b"),                    # the dashes trim whitespace; they are not the comment's
        ("{# a #}\n{#-b-#}", "b"),
        ("{# a #}\n{#--#}", ""),
        ("{# a #}\n{#-- b --#}", "- b -"),              # one mark per side, no more
        ("{# a #}\n{#+ b +#}", "b"),                    # Jinja's `+`
        ("{# a #}\n{#~ b ~#}", "~ b ~"),                # L-131: Twig's `~` is text in dbt and Jinja
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


def test_an_opener_with_no_closer_anywhere_in_the_file_is_text():
    found = _docstrings("{{ never closed\n{# about m #}\n{% macro m() %}{% endmacro %}\n")
    assert found == {"m": "about m"}


def test_a_stray_opener_with_a_later_closer_is_one_expression_to_that_closer():
    """LEDGER L-132, pinned as it reads today. Jinja refuses this file (the
    `{{` runs into the comment), so there is no right docstring to serve; the
    lexer reads one expression up to the first `}}`, and the comment inside it
    is not a comment. 1.108.332 served `about m` here."""
    found = _docstrings("{{ never closed\n{# about m #}\n{% macro m() %}{% endmacro %}\nselect {{ x }}\n")
    assert found == {"m": ""}


@pytest.mark.parametrize(
    "filename, language", [("m.sql", "sql"), ("t.j2", "jinja"), ("t.twig", "twig")]
)
def test_a_file_that_starts_with_a_byte_order_mark_keeps_its_first_docstring(filename, language):
    text = chr(0xFEFF) + "{# doc #}\n{% macro m() %}{{ x }}{% endmacro %}\n"
    symbols = {s.name: s for s in parse_file(text, filename, language)}
    assert symbols["m"].docstring == "doc"


def test_a_twig_verbatim_block_opens_nothing():
    text = "{% verbatim %}write {{ to open{% endverbatim %}\n{# doc #}\n{% macro m() %}{{ x }}{% endmacro %}\n"
    symbols = {s.name: s for s in parse_file(text, "t.twig", "twig")}
    assert symbols["m"].docstring == "doc"


@pytest.mark.parametrize(
    "filename, language, above, expected",
    [
        ("t.twig", "twig", "{#~ b ~#}", "b"),                  # Twig's mark
        ("t.twig", "twig", "{#- b -#}", "b"),
        ("t.twig", "twig", "{#+1 to the offset #}", "+1 to the offset"),  # `+` is not a Twig mark
        ("t.j2", "jinja", "{#+ b +#}", "b"),                   # Jinja's mark
        ("t.j2", "jinja", "{#- b -#}", "b"),
        ("t.j2", "jinja", "{#~approx#}", "~approx"),            # `~` is not a Jinja mark
        ("t.sql", "sql", "{#~approx#}", "~approx"),
        ("t.sql", "sql", "{#+ b +#}", "b"),
    ],
)
def test_a_whitespace_mark_is_its_own_dialects(filename, language, above, expected):
    text = "{# header #}\n{% macro head() %}x{% endmacro %}\n" + above + "\n{% macro m() %}x{% endmacro %}\n"
    symbols = {s.name: s for s in parse_file(text, filename, language)}
    assert symbols["head"].docstring == "header"
    assert symbols["m"].docstring == expected


def test_many_documented_macros_each_keep_their_own():
    count = 300
    text = "".join(f"{{# about m{i} #}}\n{{% macro m{i}() %}}{{% endmacro %}}\n" for i in range(count))
    assert _docstrings(text) == {f"m{i}": f"about m{i}" for i in range(count)}


def test_the_parser_generation_moved_so_an_existing_index_is_re_read():
    """The docstring changes on UNCHANGED content, and the incremental path
    never re-reads unchanged content (Standing lesson 08-05)."""
    assert PARSER_GENERATION >= 11
