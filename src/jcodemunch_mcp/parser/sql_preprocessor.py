"""
sql_preprocessor.py — strip Jinja templating from SQL before tree-sitter parsing.

Intended for dbt model files where {{ ref() }}, {% if %}, etc. make the SQL
syntactically invalid from tree-sitter's perspective.

Also extracts dbt directive metadata (macro, test, snapshot, materialization)
from Jinja blocks before stripping, so the caller can create symbols for them.
"""
import bisect
import re
from dataclasses import dataclass
from functools import lru_cache

from . import parse_budget


# Matches {{ expr }}, {% block %}, and {# comment #} in any order
JINJA_PATTERN = re.compile(
    r'\{\{.*?\}\}'      # {{ expression }}
    r'|\{%-?.*?-?%\}'   # {% block %} or {%- block -%}
    r'|\{#.*?#\}',      # {# comment #}
    re.DOTALL
)

# Default dbt directive keywords (macro, test, snapshot, materialization).
_DEFAULT_DBT_DIRECTIVES = ("macro", "test", "snapshot", "materialization")


@lru_cache(maxsize=None)
def _directive_pattern(directives: tuple[str, ...]) -> "re.Pattern[str]":
    """Build the Jinja-directive regex for a set of directive keywords.

    The shape ``{% <kw> name(params) %}`` is the same for every Jinja-family
    engine; only the keyword set differs. Shared by dbt SQL parsing (its default
    macro/test/snapshot/materialization set) and the generic template parser
    (which passes ``("macro", "block")``) so the scan/end-tag/docstring/offset
    logic in :func:`extract_dbt_directives` is reused rather than duplicated.
    """
    alternation = "|".join(re.escape(d) for d in directives)
    return re.compile(
        r'\{%-?\s*'
        r'(?P<directive>' + alternation + r')'
        r'\s+(?P<name>\w+)'
        r'(?:\s*\((?P<params>[^)]*)\))?'  # optional (params)
        r'[^%]*?-?%\}',
        re.DOTALL
    )


# dbt directive matcher — matches {% macro name(args) %}, {%- macro -%}, etc.
_DBT_DIRECTIVE_RE = _directive_pattern(_DEFAULT_DBT_DIRECTIVES)


@dataclass
class DbtDirective:
    """A dbt directive extracted from Jinja blocks before stripping."""
    directive: str    # "macro", "test", "snapshot", "materialization"
    name: str         # e.g. "generate_schema_name"
    params: str       # e.g. "custom_schema_name, node" or ""
    line: int         # 1-based line number
    end_line: int     # 1-based end line (of the end-tag, or same as line)
    docstring: str    # preceding {# comment #} or SQL -- comments
    byte_offset: int
    byte_length: int  # from directive start to endmacro/endsnapshot/etc.


# Every character `str.splitlines` ends a line on; CR LF is one break.
_LINE_BREAK_RE = re.compile(
    chr(0x0D) + chr(0x0A) + "|["
    + "".join(chr(c) for c in (0x0A, 0x0D, 0x0B, 0x0C, 0x1C, 0x1D, 0x1E, 0x85, 0x2028, 0x2029))
    + "]"
)
# A byte-order mark and a zero-width space print nothing and are not text: a
# file that starts with a BOM still starts with its first comment.
_INVISIBLE = chr(0xFEFF) + chr(0x200B)
_NON_SPACE_RE = re.compile(r"[^\s" + _INVISIBLE + "]")
# What may stand between a comment delimiter and its body, per dialect (L-131):
# `{#-` everywhere, `{#+` in Jinja and dbt, `{#~` in Twig. The other dialect's
# mark is the comment's own text.
JINJA_WHITESPACE_MARKS = ("-", "+")
TWIG_WHITESPACE_MARKS = ("-", "~")
# `{{`, `{%` and `{#`, and what closes each
_JINJA_OPENER_RE = re.compile(r"\{[{%#]")
_JINJA_CLOSER = {"{": "}}", "%": "%}", "#": "#}"}
# `{% raw %}` (Jinja) and `{% verbatim %}` (Twig): nothing up to the end tag is
# a delimiter, so a `{{` written there opens no expression.
_RAW_OPEN_RE = re.compile(r"\{%[-+~]?\s*(raw|verbatim)\s*[-+~]?%\}")
_RAW_END_RE = {
    word: re.compile(r"\{%[-+~]?\s*end" + word + r"\s*[-+~]?%\}")
    for word in ("raw", "verbatim")
}


class _PrecedingComments:
    """The comment immediately before an offset, for every directive of one file.

    Supports:
    - Jinja block comments: {# ... #}
    - SQL line comments: -- ...

    ⚠ Everything that depends on the file alone is computed ONCE, here
    (L-117). The function this replaces copied the file up to each directive,
    searched the copy for a Jinja comment and split it into lines, so N
    directives cost N passes over the file, and a file of unclosed ``{#`` cost
    far more. Per directive this now reads only the comment lines above it and
    the one line that ends them.

    ⚠⚠ The Jinja comment used is the one that ENDS where the text before the
    directive ends (L-126). The lookup this replaces searched from the start
    of the file and checked only that the text before the directive ended in
    ``#}``, so every documented macro after the first carried the FIRST
    comment of the file. A ``#}`` that closes no comment is not one.

    ⚠⚠ Three more rules, each a wrong docstring before it (L-129, L-130,
    L-131). The comment is FIRST ON ITS LINE, with only other comments before
    it there (or a ``--`` lead): one that trails code (``{% endmacro %} {# end of a #}``) belongs
    to that code, not to the directive on the next line. The file is read as
    Jinja reads it, ``{{ ... }}``, ``{% ... %}`` and ``{# ... #}`` left to
    right, so a ``{#`` inside an expression or a tag opens no comment, and
    nothing inside ``{% raw %}`` or ``{% verbatim %}`` opens anything. And a
    whitespace-control mark is dropped only if it is this dialect's.
    """

    def __init__(self, text: str, whitespace_marks: tuple[str, ...] = JINJA_WHITESPACE_MARKS) -> None:
        self._text = text
        self._marks = whitespace_marks
        self._line_starts = [0] + [m.end() for m in _LINE_BREAK_RE.finditer(text)]
        self._line_ends = [m.start() for m in _LINE_BREAK_RE.finditer(text)] + [len(text)]
        self._first_text: dict[int, int] = {}  # line index -> its first non-space character
        # where a comment ends -> where its body starts. Read left to right,
        # each `{{`, `{%` or `{#` running to the first closer of its kind.
        # When one has no closer, no later one of that kind has either, so
        # that kind is text from there on (one failed search per kind).
        self._comment_body_start: dict[int, int] = {}
        unclosed: set[str] = set()  # also a raw word whose end tag is nowhere ahead
        pos = 0
        while True:
            parse_budget.checkpoint()
            found = _JINJA_OPENER_RE.search(text, pos)
            if found is None:
                break
            opened = found.start()
            kind = text[opened + 1]
            if kind == "%":
                raw_tag = _RAW_OPEN_RE.match(text, opened)
                if raw_tag is not None and raw_tag.group(1) not in unclosed:
                    ended = _RAW_END_RE[raw_tag.group(1)].search(text, raw_tag.end())
                    if ended is not None:
                        pos = ended.end()
                        continue
                    unclosed.add(raw_tag.group(1))
            closed = -1 if kind in unclosed else text.find(_JINJA_CLOSER[kind], opened + 2)
            if closed < 0:
                unclosed.add(kind)
                pos = opened + 1
                continue
            if kind == "#":
                self._comment_body_start[closed + 2] = opened + 2
            pos = closed + 2

    def _first_text_of(self, idx: int) -> int:
        """Where line ``idx``'s first visible character is; its end when it has none."""
        first_text = self._first_text.get(idx)
        if first_text is None:
            found = _NON_SPACE_RE.search(self._text, self._line_starts[idx], self._line_ends[idx])
            first_text = self._first_text[idx] = found.start() if found else self._line_ends[idx]
        return first_text

    def _is_first_on_its_line(self, opened: int) -> bool:
        """Whether only space, and other comments, stand between a line start and ``opened``."""
        text = self._text
        pos = opened
        while True:
            parse_budget.checkpoint()
            first_text = self._first_text_of(bisect.bisect_right(self._line_starts, pos) - 1)
            # A line that starts with `--` is a comment up to here, not code:
            # `-- note {# doc #}` leaves the Jinja comment first of its kind.
            if first_text >= pos or text.startswith("--", first_text, pos):
                return True
            # text stands before `pos` on this line, so this stops at it
            while text[pos - 1].isspace() or text[pos - 1] in _INVISIBLE:
                parse_budget.checkpoint()
                pos -= 1
            body_start = self._comment_body_start.get(pos)  # a comment ends here
            if body_start is None:
                return False
            pos = body_start - 2

    @staticmethod
    def _clean(comment_body: str) -> str:
        comment_body = comment_body.strip()
        # Strip /** ... */ wrapper if present (common dbt pattern)
        if comment_body.startswith("/**"):
            comment_body = comment_body[3:]
        if comment_body.endswith("*/"):
            comment_body = comment_body[:-2]
        # Clean up leading * on each line
        cleaned_lines = []
        for line in comment_body.strip().splitlines():
            parse_budget.checkpoint()
            stripped = line.strip()
            if stripped.startswith("*"):
                stripped = stripped[1:].strip()
            cleaned_lines.append(stripped)
        return "\n".join(cleaned_lines).strip()

    def before(self, offset: int) -> str:
        text = self._text
        end = offset  # the text before the directive, less its trailing whitespace
        while end > 0 and text[end - 1].isspace():
            parse_budget.checkpoint()
            end -= 1
        if end == 0:
            return ""

        # Check for {# comment #} immediately before
        body_start = self._comment_body_start.get(end)
        if body_start is not None and self._is_first_on_its_line(body_start - 2):
            body = text[body_start:end - 2]
            # `{#- ... -#}` trims the whitespace around the comment; the
            # mark is the delimiter's, not the comment's. One mark per side,
            # read before any space, and only this dialect's.
            if body[:1] in self._marks:
                body = body[1:]
            if body[-1:] in self._marks:
                body = body[:-1]
            return self._clean(body)

        # Check for -- comment lines immediately before
        lines: list[str] = []
        idx = bisect.bisect_right(self._line_starts, end - 1) - 1
        line_end = end
        while idx >= 0:
            parse_budget.checkpoint()
            first_text = self._first_text_of(idx)
            if text.startswith("--", first_text, line_end):
                lines.append(text[first_text + 2:line_end].strip())
            elif first_text >= line_end:
                # Allow blank lines between comments
                if lines:
                    lines.append("")
            else:
                break
            idx -= 1
            line_end = self._line_ends[idx] if idx >= 0 else 0

        lines.reverse()
        return "\n".join(lines).strip()


def extract_dbt_directives(
    sql_bytes: bytes,
    directive_keywords: tuple[str, ...] = _DEFAULT_DBT_DIRECTIVES,
    whitespace_marks: tuple[str, ...] = JINJA_WHITESPACE_MARKS,
) -> list[DbtDirective]:
    """Extract Jinja ``{% <kw> name(params) %}`` directive blocks as metadata.

    Scans for ``{% macro name(params) %}`` and matching ``{% endmacro %}`` blocks,
    and similarly for test, snapshot, and materialization directives.

    ``directive_keywords`` selects which directive shapes to match; it defaults to
    the dbt set, and the generic template parser passes ``("macro", "block")`` to
    reuse this same scan/end-tag/docstring/offset path for Jinja/Twig templates.

    Returns a list of DbtDirective with name, params, line range, and docstring.

    ⚠ Line numbers, end tags and docstrings are read from tables built once
    per file (L-117): a count, a search or a copy from the start of the file
    per directive is the whole file again for each one.
    """
    sql_str = sql_bytes.decode("utf-8", errors="replace")
    directives: list[DbtDirective] = []
    newline_offsets = [m.start() for m in re.finditer("\n", sql_str)]
    comments = _PrecedingComments(sql_str, tuple(whitespace_marks))
    # directive keyword -> (starts, ends) of every end tag of that keyword
    end_tags: dict[str, tuple[list[int], list[int]]] = {}

    for m in _directive_pattern(tuple(directive_keywords)).finditer(sql_str):
        parse_budget.checkpoint()
        directive = m.group("directive")
        name = m.group("name")
        params = (m.group("params") or "").strip()
        start_offset = m.start()
        start_line = bisect.bisect_left(newline_offsets, start_offset) + 1

        # Find the matching end tag: {% endmacro %}, {% endsnapshot %}, etc.
        # An end tag holds no `{` after its first character, so two never
        # overlap and one pass lists every one a search from any offset finds.
        tags = end_tags.get(directive)
        if tags is None:
            end_tag_re = re.compile(
                r'\{%-?\s*end' + re.escape(directive) + r'\s*-?%\}',
                re.DOTALL
            )
            spans = [found.span() for found in end_tag_re.finditer(sql_str)]
            tags = end_tags[directive] = ([s for s, _ in spans], [e for _, e in spans])
        following = bisect.bisect_left(tags[0], m.end())
        end_offset = tags[1][following] if following < len(tags[1]) else m.end()
        end_line = bisect.bisect_left(newline_offsets, end_offset) + 1

        # Extract docstring from preceding {# comment #} or -- comments
        docstring = comments.before(start_offset)

        directives.append(DbtDirective(
            directive=directive,
            name=name,
            params=params,
            line=start_line,
            end_line=end_line,
            docstring=docstring,
            byte_offset=start_offset,
            byte_length=end_offset - start_offset,
        ))

    return directives


def strip_jinja(sql_bytes: bytes) -> bytes:
    """
    Replace Jinja expressions with SQL-valid placeholder identifiers so
    tree-sitter can parse the rest of the file cleanly.

    Example:
        b"SELECT * FROM {{ ref('orders') }}"
        → b"SELECT * FROM __jinja__"
    """
    sql_str = sql_bytes.decode("utf-8", errors="replace")
    cleaned = JINJA_PATTERN.sub("__jinja__", sql_str)
    return cleaned.encode("utf-8")


def is_jinja_sql(sql_bytes: bytes) -> bool:
    """Return True if the file appears to contain Jinja templating."""
    return b"{{" in sql_bytes or b"{%" in sql_bytes
