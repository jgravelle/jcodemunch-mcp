"""Published text carries no leftover chat between a writer and an assistant.

USER_GUIDE.md shipped "If you document this section, I would keep it framed as
optional power-user tuning", a remark meant for the author, not the reader.
This scans the files a user reads (Markdown, the plugin folder and the package
source, where tool descriptions live) for phrases that only make sense in that
conversation. The list is narrow on purpose: user-facing "you" wording is
normal here, so only phrases with no legitimate use are banned.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# History files quote old text verbatim and are not published guidance. A
# permanent decision, registered in scripts/gap_ledgers.py NOT_LEDGERS.
SKIPPED_HISTORY_FILES = {"CHANGELOG.md", "ISSUE-HISTORY.md"}

LEFTOVERS = re.compile(
    r"if you document this"
    r"|\bI would keep it\b"
    r"|let me know if"
    r"|hope this helps"
    r"|\bwant me to\b"
    r"|here(?:'|’)?s the (?:updated|revised|rewritten)"
    r"|here is the (?:updated|revised|rewritten)"
    r"|\[insert ",
    re.IGNORECASE,
)


def _published_files() -> list[str]:
    out = subprocess.run(
        ["git", "-c", "core.quotepath=off", "ls-files", "-z", "*.md", "src/**/*.py", "plugin/**"],
        cwd=ROOT, capture_output=True, check=True,
    ).stdout.decode("utf-8")
    return [f for f in out.split("\0") if f and f not in SKIPPED_HISTORY_FILES]


def test_the_scan_reaches_the_files_it_claims():
    files = set(_published_files())
    assert "USER_GUIDE.md" in files
    assert "src/jcodemunch_mcp/server.py" in files
    assert any(f.startswith("plugin/") for f in files)


def test_no_writer_facing_leftovers_in_published_text():
    hits, unreadable = [], []
    for rel in _published_files():
        try:
            text = (ROOT / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            unreadable.append(f"{rel}: {exc}")
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if LEFTOVERS.search(line):
                hits.append(f"{rel}:{n}: {line.strip()[:120]}")
    assert not unreadable, "files the scan could not read:\n" + "\n".join(unreadable)
    assert not hits, "assistant-to-author leftovers:\n" + "\n".join(hits)


def test_the_pattern_catches_the_line_that_shipped():
    shipped = "If you document this section, I would keep it framed as optional power-user tuning."
    assert LEFTOVERS.search(shipped)
    assert not LEFTOVERS.search("If you use Codex, add the server to config.toml.")
