"""No default in src/ names an OpenAI model OpenAI has retired or is retiring (backlog 96).

OpenAI's deprecations notice of 2026-10-14 removes gpt-4o, gpt-4o-mini,
gpt-4.1 and gpt-4.1-mini on 2027-01-14 and names gpt-6-luna as the replacement
for all four; gpt-4.1-nano was shut down 2026-10-23. The summarizer fell back
to gpt-4o-mini and the agent selector's OpenAI order was gpt-4o-mini / gpt-4o,
so an install that never named a model would start failing that day.

A user's explicit model (OPENAI_MODEL, `summarizer_model`, an agent_selector
batting order) is untouched. `model_tier_map`'s "gpt-4o" key stays: it matches
the CLIENT's model id to pick a tool tier and calls no API. Only its two sites
in config.py (the DEFAULTS dict and the config template) are exempt.
"""

from __future__ import annotations

import re
from pathlib import Path

from jcodemunch_mcp.agent_selector import DEFAULT_PROVIDERS
from jcodemunch_mcp.summarizer import batch_summarize as bs

SRC = Path(__file__).resolve().parents[1] / "src" / "jcodemunch_mcp"
# A quoted retired id: bare, dated snapshot, or provider-prefixed.
_RETIRED = re.compile(
    r"""["'](?:[\w.-]+/)?gpt-4(?:o(?:-mini)?|\.1(?:-mini|-nano)?)(?:-\d{4}-\d{2}-\d{2})?["']"""
)
# `"gpt-4o": "full"` routes a client model to a tool tier; exempt in config.py only.
_TIER_MAP_ENTRY = re.compile(r'^\s*"gpt-4o":\s*"full",?\s*$')
_TIER_MAP_FILE = "config.py"


def _scan(root: Path) -> tuple[list[str], set[str]]:
    hits, seen = [], set()
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        seen.add(rel)
        for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if rel == _TIER_MAP_FILE and _TIER_MAP_ENTRY.match(line):
                continue
            if _RETIRED.search(line):
                hits.append(f"{rel}:{no}: {line.strip()}")
    return hits, seen


def test_the_openai_defaults_are_the_named_successors():
    assert bs.OPENAI_DEFAULT_MODEL == "gpt-6-luna"
    assert DEFAULT_PROVIDERS["openai"] == {
        "low": "gpt-6-luna",
        "medium": "gpt-6-sol",
        "high": "o3",
    }


def test_no_source_literal_names_a_retired_openai_model():
    hits, seen = _scan(SRC)
    assert {
        "summarizer/batch_summarize.py",
        "agent_selector.py",
        "server.py",
        "config.py",
    } <= seen
    assert not hits, "a default still names a retired OpenAI model:\n" + "\n".join(hits)


def test_the_scan_fails_on_each_reintroduced_spelling(tmp_path):
    """Non-vacuity, over a tree the test owns: every spelling is flagged, and
    the tier-map line is exempt in config.py only."""
    (tmp_path / "summarizer").mkdir()
    (tmp_path / "summarizer" / "batch_summarize.py").write_text(
        'model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")\n', encoding="utf-8"
    )
    (tmp_path / "agent_selector.py").write_text(
        '    "medium": "gpt-4o",\n    "gpt-4o": "full",\n', encoding="utf-8"
    )
    (tmp_path / "pricing.py").write_text(
        'A = \'gpt-4.1-mini-2025-04-14\'\nB = "openai/gpt-4o-mini"\nC = "gpt-4.1-nano"\n',
        encoding="utf-8",
    )
    (tmp_path / "config.py").write_text('    "gpt-4o": "full",\n', encoding="utf-8")
    hits, _ = _scan(tmp_path)
    flagged = {h.split(":")[0] + ":" + h.split(":")[1] for h in hits}
    assert flagged == {
        "summarizer/batch_summarize.py:1",
        "agent_selector.py:1",
        "agent_selector.py:2",
        "pricing.py:1",
        "pricing.py:2",
        "pricing.py:3",
    }


def test_a_successor_or_another_family_is_not_flagged(tmp_path):
    (tmp_path / "m.py").write_text(
        'X = ("gpt-6-luna", "gpt-6-sol", "o3", "gpt-5.4-nano", "gpt-4")\n',
        encoding="utf-8",
    )
    assert _scan(tmp_path)[0] == []
