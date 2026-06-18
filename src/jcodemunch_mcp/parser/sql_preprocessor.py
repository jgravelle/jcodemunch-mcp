"""
sql_preprocessor.py — strip Jinja templating from SQL before tree-sitter parsing.

Intended for dbt model files where {{ ref() }}, {% if %}, etc. make the SQL
syntactically invalid from tree-sitter's perspective.

Also extracts dbt directive metadata (macro, test, snapshot, materialization)
from Jinja blocks before stripping, so the caller can create symbols for them.
"""
import re
from dataclasses import dataclass
from functools import lru_cache


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

# Jinja block comment: {# ... #}
_JINJA_COMMENT_RE = re.compile(r'\{#(.*?)#\}', re.DOTALL)


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
    """
    sql_str = sql_bytes.decode("utf-8", errors="replace")
    directives: list[DbtDirective] = []

    for m in _directive_pattern(tuple(directive_keywords)).finditer(sql_str):
        directive = m.group("directive")
        name = m.group("name")
        params = (m.group("params") or "").strip()
        start_offset = m.start()
        start_line = sql_str[:start_offset].count("\n") + 1

        # Find the matching end tag: {% endmacro %}, {% endsnapshot %}, etc.
        end_tag_re = re.compile(
            r'\{%-?\s*end' + re.escape(directive) + r'\s*-?%\}',
            re.DOTALL
        )
        end_match = end_tag_re.search(sql_str, m.end())
        if end_match:
            end_offset = end_match.end()
            end_line = sql_str[:end_offset].count("\n") + 1
        else:
            end_offset = m.end()
            end_line = sql_str[:end_offset].count("\n") + 1

        # Extract docstring from preceding {# comment #} or -- comments
        docstring = _extract_preceding_docstring(sql_str, start_offset)

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


def _extract_preceding_docstring(sql_str: str, offset: int) -> str:
    """Extract docstring from comments immediately before the given offset.

    Supports:
    - Jinja block comments: {# ... #}
    - SQL line comments: -- ...
    """
    # Look at the text before the directive
    preceding = sql_str[:offset].rstrip()
    if not preceding:
        return ""

    lines: list[str] = []

    # Check for {# comment #} immediately before
    jinja_comment = _JINJA_COMMENT_RE.search(preceding)
    if jinja_comment and preceding.rstrip().endswith("#}"):
        comment_body = jinja_comment.group(1).strip()
        # Strip /** ... */ wrapper if present (common dbt pattern)
        if comment_body.startswith("/**"):
            comment_body = comment_body[3:]
        if comment_body.endswith("*/"):
            comment_body = comment_body[:-2]
        # Clean up leading * on each line
        cleaned_lines = []
        for line in comment_body.strip().splitlines():
            stripped = line.strip()
            if stripped.startswith("*"):
                stripped = stripped[1:].strip()
            cleaned_lines.append(stripped)
        return "\n".join(cleaned_lines).strip()

    # Check for -- comment lines immediately before
    for line in reversed(preceding.splitlines()):
        stripped = line.strip()
        if stripped.startswith("--"):
            lines.insert(0, stripped[2:].strip())
        elif not stripped:
            # Allow blank lines between comments
            if lines:
                lines.insert(0, "")
        else:
            break

    return "\n".join(lines).strip()


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
