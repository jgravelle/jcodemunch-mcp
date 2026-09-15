"""Git blame context provider — attaches last_author and last_modified to files.

History bound:
    The naive ``git log --name-only`` walks the entire commit history, which is
    fine on a fresh repo but pathological on a long-lived one — 25-30 s on a
    monorepo with hundreds of thousands of commits, easily blowing past the
    MCP client's request-timeout budget. We bound the work two ways:

      * ``-n GIT_BLAME_COMMIT_LIMIT`` caps the commit count
      * ``--since=GIT_BLAME_SINCE`` bounds the time window

    Together these mean blame data covers recent activity (where it matters
    for `last_author` / `last_modified` queries) without paying for ancient
    history. Files untouched in the window simply won't appear in the blame
    map, and ``get_file_context`` will return ``None`` for them — the same
    behaviour as files outside a git working tree.

Operator override:
    Setting ``git_blame_enabled = false`` in the project or global config
    short-circuits ``detect()`` so the provider never runs. Useful for huge
    histories where even the bounded walk is too slow.
"""

import logging
import subprocess
from pathlib import Path
from typing import Optional

from ... import config as _config
from .base import ContextProvider, FileContext, register_provider

logger = logging.getLogger(__name__)


# Walk at most this many commits when building the blame map. Combined with
# ``GIT_BLAME_SINCE``, this keeps the load() pass bounded on legacy repos
# with deep history. 20_000 covers ~5 years of active development for a
# team committing a couple dozen times a day.
GIT_BLAME_COMMIT_LIMIT = 20_000

# Time window for the blame walk. ``git log --since`` accepts approximate
# strings ("2.years.ago", "6 months ago"). Two years catches the
# overwhelming majority of files an agent will ask about; older files
# silently fall off the map and report no blame.
GIT_BLAME_SINCE = "2.years.ago"

# Hard wall-clock bound on the subprocess. Tight enough that we don't blow
# past a 30-s MCP request timeout even when both bounds above fail to fire
# (e.g. a 20 k-commit window all within the last 2 years).
GIT_BLAME_TIMEOUT_S = 10.0


@register_provider
class GitBlameProvider(ContextProvider):
    """Context provider that reads per-file last-commit metadata from git.

    Detected automatically when a ``.git`` directory is present in the indexed
    folder and ``git_blame_enabled`` config is true (default). Runs a single
    bounded ``git log`` command during ``load()`` to build a
    {relative_path: (author, iso_date)} map; subsequent ``get_file_context``
    calls are O(1) dict lookups.

    Adds to each file's ``FileContext.properties``:
      - ``last_author``: display name of the most recent committer
      - ``last_modified``: ISO-8601 date of the most recent commit
    """

    def __init__(self) -> None:
        self._blame: dict[str, tuple[str, str]] = {}  # path -> (author, date)
        self._folder: Optional[Path] = None

    @property
    def name(self) -> str:
        return "git_blame"

    def detect(self, folder_path: Path) -> bool:
        """Return True if the folder is inside a git repository and blame is enabled."""
        if not _config.get("git_blame_enabled", True, repo=str(folder_path)):
            return False
        return (folder_path / ".git").exists() or self._find_git_root(folder_path) is not None

    def _find_git_root(self, folder_path: Path) -> Optional[Path]:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=str(folder_path),
                capture_output=True,
                text=True, encoding="utf-8", errors="replace",
                timeout=5,
                stdin=subprocess.DEVNULL,
            )
            if result.returncode == 0:
                return Path(result.stdout.strip())
        except Exception:
            pass
        return None

    def load(self, folder_path: Path) -> None:
        """Run one bounded ``git log`` to populate the blame map.

        The walk is capped by ``GIT_BLAME_COMMIT_LIMIT`` (commit count) and
        ``GIT_BLAME_SINCE`` (time window). On a fresh repo both bounds are
        no-ops; on a legacy 100k-commit history they keep load() under a
        second. Output beyond ``GIT_BLAME_TIMEOUT_S`` is dropped (with a
        warning) rather than blowing past the indexing budget.
        """
        self._folder = folder_path
        try:
            result = subprocess.run(
                [
                    "git", "log",
                    # (#685) keyed by index path: `--name-only` alone prints
                    # top-level paths, which miss an index rooted below it.
                    "--relative",
                    "--name-only",
                    "--format=COMMIT %an|%aI",
                    "--diff-filter=AM",
                    "--no-merges",
                    "-n", str(GIT_BLAME_COMMIT_LIMIT),
                    f"--since={GIT_BLAME_SINCE}",
                    "--",
                    # `.` spends `-n` on commits under the index root, not on
                    # the whole monorepo (#685).
                    ".",
                ],
                cwd=str(folder_path),
                capture_output=True,
                text=True, encoding="utf-8", errors="replace",
                timeout=GIT_BLAME_TIMEOUT_S,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            logger.warning(
                "GitBlameProvider: git log exceeded %.0fs budget — "
                "blame data will be partial or empty. Set "
                "`git_blame_enabled: false` in config to skip the probe "
                "entirely on repos with very deep history.",
                GIT_BLAME_TIMEOUT_S,
            )
            return
        except Exception as exc:
            logger.warning("GitBlameProvider: git log failed: %s", exc)
            return

        current_author = ""
        current_date = ""
        for line in result.stdout.splitlines():
            line = line.rstrip()
            if line.startswith("COMMIT "):
                rest = line[7:]
                parts = rest.split("|", 1)
                current_author = parts[0].strip()
                current_date = parts[1][:10] if len(parts) > 1 else ""
            elif line and current_author:
                # Only record the first (most recent) entry per file
                if line not in self._blame:
                    self._blame[line] = (current_author, current_date)

        logger.debug("GitBlameProvider: loaded blame for %d files", len(self._blame))

    def get_file_context(self, file_path: str) -> Optional[FileContext]:
        if not self._blame:
            return None
        # Try exact path, then basename fallback
        entry = self._blame.get(file_path) or self._blame.get(Path(file_path).name)
        if not entry:
            return None
        author, date = entry
        return FileContext(properties={"last_author": author, "last_modified": date})

    def stats(self) -> dict:
        return {"files_with_blame": len(self._blame)}

    def get_metadata(self) -> dict:
        """Expose blame data in index for structured access."""
        return {"git_blame": {path: {"author": a, "date": d} for path, (a, d) in self._blame.items()}}
