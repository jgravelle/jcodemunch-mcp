"""THE word rule: how a symbol's name is matched as a whole token in text.

A leaf, standard library only: ``tools``, ``parser`` and ``enrichment`` all
build patterns from names, and none of them may import another to do it.

⚠⚠ **Never write ``\\b`` beside ``re.escape(name)`` by hand**, in a
concatenation, an f-string or a ``{name}\\b`` template. The rule was written on
84 lines of six modules (LEDGER L-154) and every one was wrong for a name
that starts or ends in a mark: a word boundary beside a mark exists only when a
WORD character is on its other side, so ``\\bvalid\\?\\b`` matched ``valid?x``
and never ``valid?``, ``valid?(x)`` or ``valid? x``. The dead-code tools read
such a function as never named, and the rename plan skipped its uses.
``tests/test_a_name_is_matched_as_a_token_whatever_its_ends.py`` scans
``src/`` for a copy.

What each side of a name gets, decided by the name's own first or last
character:

- a word character: ``\\b``, as it always was. A name with a word character at
  both ends keeps ``\\b`` + name + ``\\b`` byte for byte.
- whitespace, or a name with no word character at all (an operator): no
  condition. The space is the separator, and nothing says where an operator
  ends, so ``==`` is named in ``a==b``, ``a == b`` and ``a===b``.
- a mark the name is WRITTEN with where it is used: no condition on the left
  for ``.`` and ``#`` (a CSS selector ``.btn`` is named in ``a.btn`` and
  ``#main`` in ``div#main``), and on the right for ``=`` only that it is not
  the start of ``==``, ``=~`` or ``=>`` (a Ruby setter ``name=`` is named in
  ``u.name=value`` and in ``self.name = 1``, never in ``name == x``).
- any other mark (``?``, ``!``, ``-``, ``$``, ``@``, ``*``): the name must not
  be glued to a word character on that side. ``valid?x`` does not name
  ``valid?``, and ``run-main`` does not name ``-main``.

⚠ ``\\w`` stands in for "a character that continues a name", which no single
rule can know for every language (LEDGER L-163 lists what that costs: ``-``
still ends a token, a ``$`` glued to a word is not a sigil).
"""

from __future__ import annotations

import re

_WORD_CHAR_RE = re.compile(r"\w")
# Marks a name is written with at its use, so a word may touch them.
_LEFT_MARKS_A_WORD_MAY_TOUCH = ".#"
# After a trailing ``=``: not the rest of ``==``, ``=~`` or ``=>``.
_AFTER_A_TRAILING_EQUALS = r"(?![=~>])"


def word_left(name: str) -> str:
    """The pattern that must hold just BEFORE *name* for it to start a token."""
    if not name or _WORD_CHAR_RE.match(name[0]):
        return r"\b"
    if name[0].isspace() or not _WORD_CHAR_RE.search(name):
        return ""
    if name[0] in _LEFT_MARKS_A_WORD_MAY_TOUCH:
        return ""
    return r"(?<!\w)"


def word_right(name: str) -> str:
    """The pattern that must hold just AFTER *name* for it to end a token."""
    if not name or _WORD_CHAR_RE.match(name[-1]):
        return r"\b"
    if name[-1].isspace() or not _WORD_CHAR_RE.search(name):
        return ""
    if name[-1] == "=":
        return _AFTER_A_TRAILING_EQUALS
    return r"(?!\w)"


def word_pattern(name: str) -> str:
    """Regex source that matches *name* as a whole token."""
    return word_left(name) + re.escape(name) + word_right(name)


def fill_name_template(template: str, name: str) -> str:
    """Fill a pattern template that marks the name with ``{left}{name}{right}``.

    For the tables of definition patterns that are written once per language
    and filled per name. A template may leave ``{left}`` or ``{right}`` out
    where its own text already bounds that side (``def\\s+{name}{right}``).

    ⚠ By replacement, never ``str.format``: the templates are regex source and
    hold literal braces (``\\s*\\{`` in the CSS ones), which ``format`` reads
    as a field and raises on.
    """
    return (
        template.replace("{left}", word_left(name))
        .replace("{right}", word_right(name))
        .replace("{name}", re.escape(name))
    )
