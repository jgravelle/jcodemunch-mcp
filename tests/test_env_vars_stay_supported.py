"""Environment variables stay supported, and nothing promises a 2.0 removal (LEDGER L-193).

The config loader logged "Deprecated: Using <VAR> environment variable. This
will be removed in v2.0. Use config.jsonc instead." for every mapped variable,
and CONFIGURATION.md said the same. No 2.0 is planned (jjg, 2026-10-15), and the
promise had a cost beyond being untrue: `JCODEMUNCH_SHARE_SAVINGS=0` is the
opt-out from anonymous savings sharing that SECURITY.md and five user docs give,
so an announced removal said that opting out that way would stop working.
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# A removal promised for a 2.0 release, in any of the spellings the tree used.
_REMOVAL = re.compile(
    r"remov(?:ed|al)\s+in\s+v?2(?:\.0)?\b|v?2\.0\s+will\s+remove|deprecat\w*\s+env"
    r"|env\s+vars?\b[^.]{0,60}\b(?:deprecation\s+warning|but\s+are\s+deprecated)",
    re.IGNORECASE,
)


@pytest.mark.parametrize("var, key, value, expected", [
    ("JCODEMUNCH_SHARE_SAVINGS", "share_savings", "0", False),
    ("JCODEMUNCH_MAX_FOLDER_FILES", "max_folder_files", "5000", 5000),
])
def test_an_env_var_applies_and_logs_no_deprecation(tmp_path, monkeypatch, caplog, var, key, value, expected):
    from jcodemunch_mcp import config

    (tmp_path / "config.jsonc").write_text("{}", encoding="utf-8")
    monkeypatch.setenv(var, value)
    with caplog.at_level(logging.DEBUG, logger="jcodemunch_mcp.config"):
        config.load_config(str(tmp_path))
    assert config.get(key) == expected
    text = caplog.text.lower()
    assert "deprecat" not in text, caplog.text
    assert "2.0" not in text, caplog.text


def test_the_loader_source_promises_no_removal():
    src = (ROOT / "src" / "jcodemunch_mcp" / "config.py").read_text(encoding="utf-8")
    assert not _REMOVAL.search(src), _REMOVAL.search(src).group(0)


def test_no_tracked_doc_promises_a_2_0_removal():
    """CHANGELOG, ISSUE-HISTORY and the ledger are records and keep what was said then."""
    listed = subprocess.run(
        ["git", "ls-files", "*.md", "src/*.py", "tests/*.py"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL,
    )
    if listed.returncode != 0:
        pytest.skip("not a git checkout")
    skip = {"CHANGELOG.md", "ISSUE-HISTORY.md", "docs/workflows/LEDGER.md", "tests/test_env_vars_stay_supported.py"}
    files = [rel for rel in listed.stdout.splitlines() if rel not in skip]
    assert len(files) > 100, f"the scan saw {len(files)} files; it is measuring nothing"
    hits = []
    for rel in files:
        text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
        for n, line in enumerate(text.splitlines(), 1):
            if _REMOVAL.search(line):
                hits.append(f"{rel}:{n}")
    assert hits == [], f"a 2.0 removal is promised at: {hits}"
