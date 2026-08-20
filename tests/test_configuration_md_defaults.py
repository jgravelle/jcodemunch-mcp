"""CONFIGURATION.md's `Default` column must agree with `config.DEFAULTS`.

Issue #515: the `disabled_tools` row read `[]` while the shipped default has
been `["test_summarizer"]` since it was introduced. Three other surfaces state
that default correctly (the generated config template, the `config --init`
comment, and `test_guide_respects_disabled_tools.py`'s pin); the reference
table was the only one that disagreed, and it is the page a user consults when
a tool they expected is missing from the schema.

This is written over the TABLE, not over the two reported rows: a row-specific
assertion would have said nothing about the next key someone documents.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from jcodemunch_mcp.config import DEFAULTS
from jcodemunch_mcp.server import _TOOL_TIER_CORE, _TOOL_TIER_STANDARD

_DOC = Path(__file__).resolve().parents[1] / "CONFIGURATION.md"
_HEADER = "| Key | Type | Default | Description |"

# Keys whose real default is too large to inline in a table cell. An entry here
# is NOT a licence to write prose — each one carries a check below that proves
# the documented cell tells the truth, and `test_non_literal_defaults_are_big`
# proves the escape hatch cannot be used to hide a small wrong value.
_NON_LITERAL_DEFAULTS = frozenset({"tool_tier_bundles"})

# A default whose repr is shorter than this fits in a cell, so it must be
# spelled out rather than described.
_INLINEABLE_REPR_CHARS = 200


def _documented_rows() -> list[tuple[str, str, str]]:
    """(key, type_cell, default_cell) for every row of every `Default` table."""
    rows: list[tuple[str, str, str]] = []
    in_table = False
    for line in _DOC.read_text(encoding="utf-8").splitlines():
        if line.startswith(_HEADER):
            in_table = True
            continue
        if not in_table:
            continue
        if not line.startswith("|"):
            in_table = False
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4 or set("".join(cells)) <= set("-: "):
            continue
        rows.append((cells[0].strip("`"), cells[1], cells[2]))
    return rows


_ROWS = _documented_rows()
_LITERAL_ROWS = [r for r in _ROWS if r[0] not in _NON_LITERAL_DEFAULTS]


def test_the_tables_were_actually_found() -> None:
    """Guard against a silent parse failure reporting a vacuous pass."""
    assert len(_ROWS) > 50, f"only {len(_ROWS)} rows parsed from {_DOC.name}"
    assert any(k == "disabled_tools" for k, _, _ in _ROWS)


@pytest.mark.parametrize("key,default_cell", [(k, d) for k, _, d in _LITERAL_ROWS])
def test_documented_default_matches_the_shipped_default(key: str, default_cell: str) -> None:
    assert key in DEFAULTS, f"CONFIGURATION.md documents `{key}`, which is not in DEFAULTS"
    literal = default_cell.strip()
    assert literal.startswith("`") and literal.endswith("`"), (
        f"`{key}` documents its default as {default_cell!r} rather than a "
        "backticked literal; add it to _NON_LITERAL_DEFAULTS with a check if "
        "the real value cannot be inlined"
    )
    try:
        documented = json.loads(literal.strip("`"))
    except ValueError:  # pragma: no cover - a malformed cell is the failure
        pytest.fail(f"`{key}`'s Default cell {literal} is not a JSON literal")
    assert documented == DEFAULTS[key], (
        f"CONFIGURATION.md says `{key}` defaults to {documented!r}; "
        f"config.DEFAULTS says {DEFAULTS[key]!r}"
    )


@pytest.mark.parametrize("key", sorted(_NON_LITERAL_DEFAULTS))
def test_non_literal_defaults_are_big(key: str) -> None:
    """The escape hatch only covers values a table cell genuinely cannot hold."""
    assert len(repr(DEFAULTS[key])) > _INLINEABLE_REPR_CHARS, (
        f"`{key}` is exempt from the literal check but its default fits in a "
        "cell — spell it out instead of describing it"
    )


@pytest.mark.parametrize("key", sorted(_NON_LITERAL_DEFAULTS))
def test_non_literal_defaults_are_documented(key: str) -> None:
    assert any(k == key for k, _, _ in _ROWS), f"`{key}` is exempt but has no row"


def test_tool_tier_bundles_ships_the_built_in_tiers() -> None:
    """The claim its cell makes, asserted rather than trusted.

    The row says the shipped bundles carry the same names as the built-in
    constants. If that ever stops being true the cell becomes wrong in the way
    #515 was wrong, and no literal comparison would catch it.
    """
    bundles = DEFAULTS["tool_tier_bundles"]
    assert sorted(bundles) == ["core", "standard"]
    assert set(bundles["core"]) == set(_TOOL_TIER_CORE)
    assert set(bundles["standard"]) == set(_TOOL_TIER_STANDARD)

    row = next(d for k, _, d in _ROWS if k == "tool_tier_bundles")
    assert row.strip() != "`{}`", "the empty-dict cell is the #515 defect"


def test_disabled_tools_row_names_the_tool_it_disables() -> None:
    """Acceptance criterion 2: a reader can tell why 90 of 91 tools mount."""
    key, _, default_cell = next(r for r in _ROWS if r[0] == "disabled_tools")
    assert json.loads(default_cell.strip().strip("`")) == DEFAULTS["disabled_tools"]
    description = next(
        line for line in _DOC.read_text(encoding="utf-8").splitlines()
        if line.startswith("| `disabled_tools` |")
    )
    for name in DEFAULTS["disabled_tools"]:
        assert re.search(rf"\b{re.escape(name)}\b", description), (
            f"the row does not name `{name}`"
        )
