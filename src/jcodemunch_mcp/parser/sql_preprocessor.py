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
_NON_SPACE_RE = re.compile(r"\S")


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

    ⚠ The Jinja comment used is the FIRST ``{# ... #}`` of the file, whichever
    comment the directive follows (LEDGER L-126). That is the old behaviour,
    kept as it was: this change moves no docstring.
    """

    def __init__(self, text: str) -> None:
        self._text = text
        self._line_starts = [0] + [m.end() for m in _LINE_BREAK_RE.finditer(text)]
        self._line_ends = [m.start() for m in _LINE_BREAK_RE.finditer(text)] + [len(text)]
        self._first_text: dict[int, int] = {}  # line index -> its first non-space character
        # A lazy `{#(.*?)#}` search finds the first `{#` and the first `#}`
        # after it; when that `{#` has no `#}`, no later one has either.
        opened = text.find("{#")
        closed = text.find("#}", opened + 2) if opened >= 0 else -1
        self._comment_end = closed + 2 if closed >= 0 else -1
        self._comment_text = self._clean(text[opened + 2:closed]) if closed >= 0 else ""

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
        if 0 <= self._comment_end <= end and text.endswith("#}", 0, end):
            return self._comment_text

        # Check for -- comment lines immediately before
        lines: list[str] = []
        idx = bisect.bisect_right(self._line_starts, end - 1) - 1
        line_end = end
        while idx >= 0:
            parse_budget.checkpoint()
            first_text = self._first_text.get(idx)
            if first_text is None:
                found = _NON_SPACE_RE.search(text, self._line_starts[idx], self._line_ends[idx])
                first_text = self._first_text[idx] = found.start() if found else self._line_ends[idx]
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
    comments = _PrecedingComments(sql_str)
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
