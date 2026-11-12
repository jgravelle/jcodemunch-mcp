"""No Anthropic or Google default names a model a new install cannot call.

Read on each provider's own pages on 2026-11-12:
- Anthropic (platform.claude.com, models overview and deprecations):
  claude-sonnet-4-20250514 was retired 2026-06-15 (claude-opus-4-20250514
  too), and claude-opus-4-20250115 is not a listed model ID. Current:
  claude-haiku-5-5, claude-sonnet-5-5, claude-opus-5-5.
- Google (aistudio.google.com, models and deprecations): gemini-2.0-flash
  shut down 2026-06-01 (named replacement gemini-3.6-flash); gemini-2.5-pro
  shuts down 2026-12-01, and Google names gemini-3.8-flash as its
  replacement; no stable Pro model is listed. gemini-2.5-flash-lite is not
  deprecated, but 2.5 API access is limited to users who have used it before,
  so a new install's default could not run; the page recommends
  gemini-3.5-flash-lite for new projects.

The agent selector's Anthropic and Google batting orders and the Gemini
summarizer default named those models, so an install that never named one
routed to, or summarized with, a model it could not call (gemini-2.5-pro
still answers until its 2026-12-01 shutdown). A model the user
sets is untouched.
"""

from __future__ import annotations

import re
from pathlib import Path

from jcodemunch_mcp.agent_selector import DEFAULT_PROVIDERS
from jcodemunch_mcp.summarizer.batch_summarize import GeminiBatchSummarizer

SRC = Path(__file__).resolve().parents[1] / "src" / "jcodemunch_mcp"
UNCALLABLE = (
    "claude-sonnet-4-20250514",
    "claude-opus-4-20250514",
    "claude-opus-4-20250115",
    "gemini-2.0-flash",
    "gemini-2.5-pro",
    "gemini-2.5-flash-lite",
)
_UNCALLABLE = re.compile(
    r"""["'](?:[\w.-]+/)?(?:""" + "|".join(re.escape(m) for m in UNCALLABLE) + r""")["']"""
)


def _scan(root: Path) -> tuple[list[str], set[str]]:
    hits, seen = [], set()
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        seen.add(rel)
        for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _UNCALLABLE.search(line):
                hits.append(f"{rel}:{no}: {line.strip()}")
    return hits, seen


def test_the_defaults_are_current_models():
    assert DEFAULT_PROVIDERS["anthropic"] == {
        "low": "claude-haiku-5-5",
        "medium": "claude-sonnet-5-5",
        "high": "claude-opus-5-5",
    }
    assert DEFAULT_PROVIDERS["google"] == {
        "low": "gemini-3.6-flash",
        "medium": "gemini-3.8-flash",
        "high": "gemini-3.8-flash",
    }
    assert GeminiBatchSummarizer.model == "gemini-3.5-flash-lite"


def test_no_source_literal_names_an_uncallable_model():
    hits, seen = _scan(SRC)
    assert {"agent_selector.py", "summarizer/batch_summarize.py", "config.py"} <= seen
    assert not hits, "a default still names a model a new install cannot call:\n" + "\n".join(hits)


def test_the_scan_fails_on_each_reintroduced_id(tmp_path):
    """Non-vacuity, over a tree the test owns."""
    lines = [f'X{i} = "{m}"' for i, m in enumerate(UNCALLABLE)]
    lines.append('Y = "google/gemini-2.0-flash"')
    (tmp_path / "m.py").write_text("\n".join(lines) + "\n", encoding="utf-8")
    hits, _ = _scan(tmp_path)
    assert len(hits) == len(UNCALLABLE) + 1


def test_a_current_or_longer_id_is_not_flagged(tmp_path):
    (tmp_path / "m.py").write_text(
        'X = ("claude-haiku-4-5-20251001", "gemini-2.0-flash-exp", "gemini-2.5-pro-preview", '
        '"gemini-3.8-flash", "claude-opus-5-5")\n',
        encoding="utf-8",
    )
    assert _scan(tmp_path)[0] == []
