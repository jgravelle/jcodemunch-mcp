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

# History files quote old text verbatim and are not published guidance.
HISTORY_FILES = {"CHANGELOG.md", "ISSUE-HISTORY.md", "tests/test_no_assistant_leftovers.py"}

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


def _published_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "*.md", "src/**/*.py", "plugin/**"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    return [ROOT / f for f in out if f not in HISTORY_FILES]


def test_no_writer_facing_leftovers_in_published_text():
    hits = []
    for path in _published_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if LEFTOVERS.search(line):
                hits.append(f"{path.relative_to(ROOT).as_posix()}:{n}: {line.strip()[:120]}")
    assert not hits, "assistant-to-author leftovers:\n" + "\n".join(hits)


def test_the_pattern_catches_the_line_that_shipped():
    shipped = "If you document this section, I would keep it framed as optional power-user tuning."
    assert LEFTOVERS.search(shipped)
    assert not LEFTOVERS.search("If you use Codex, add the server to config.toml.")
