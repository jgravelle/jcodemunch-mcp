"""Per-symbol freshness classification (v1.77.0).

Four buckets:
  * ``fresh``               — index SHA matches HEAD AND the file mtime is
                              not newer than the index timestamp.
  * ``edited_uncommitted``  — index SHA matches HEAD but the on-disk file
                              has been edited since indexing (mtime newer
                              than indexed_at).
  * ``stale_index``         — the whole index lags behind: index SHA does
                              not match the current git HEAD.
  * ``unknown``             — the comparison could not be made at all: no
                              source root, the root moved, the file is gone
                              from the tree, the stat failed, or the index
                              recorded no timestamp to compare against.

``unknown`` exists for the same reason ``repo_freshness`` grew it in
v1.108.180 (#377 item 4), one level down: this classifier used to answer
``fresh`` whenever it could not measure, so a served symbol whose freshness
was never established claimed to be current. "I could not find out" is a
third fact, not a clean bill of health — and it is the answer for exactly
the population that cannot self-diagnose: a ``.db``-only starter pack, an
index built on another machine, a source root that has since moved.

The probe caches per-call git HEAD lookup and per-file mtime stats so
classifying many symbols in one tool call is cheap.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# prime_mtimes: below this many files a serial stat is already quick.
_PRIME_MIN_FILES = 256
_PRIME_WORKERS = 8


_FRESH = "fresh"
_EDITED = "edited_uncommitted"
_STALE = "stale_index"
# Per-file "could not measure" (v1.108.209). Same wire string as the repo-level
# _UNKNOWN below and deliberately a separate constant: these two answer
# different questions and only one of them is about a single returned file.
_UNKNOWN_FILE = "unknown"

# Repo-level freshness states (#377 item 4). Deliberately separate strings from
# the per-symbol buckets above: those classify one returned file, these classify
# whether the index as a whole can be compared to the tree at all.
_STALE_REPO = "stale"
_UNKNOWN = "unknown"
_NOT_TRACKED = "not_tracked"


def _parse_iso(ts: str) -> Optional[float]:
    """Parse the ISO timestamp recorded in the index. Returns Unix epoch
    seconds (float) or None on parse failure."""
    if not ts:
        return None
    try:
        # tolerate trailing Z
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts).astimezone(timezone.utc).timestamp()
    except Exception:
        return None


# Process-wide cache of resolved git HEAD per source root (B1). The subprocess
# is re-run only when a cheap stat-based signature of the refs that move with
# HEAD changes; when no signature can be computed (exotic layouts) a short TTL
# bounds reuse so a burst of tool calls shares one `git rev-parse` rather than
# spawning one each. key -> (signature, sha, monotonic_ts).
_HEAD_CACHE_TTL_S = 2.0
_head_cache: dict[str, tuple[Optional[tuple], Optional[str], float]] = {}


def _clear_head_cache() -> None:
    """Test hook: drop all cached HEAD lookups."""
    _head_cache.clear()


def _resolve_git_dir(source_root: Path) -> Optional[Path]:
    """Return the .git directory for *source_root*, resolving worktree/submodule
    `.git` files (``gitdir: <path>``). None when it isn't a git repo."""
    dotgit = source_root / ".git"
    try:
        if dotgit.is_dir():
            return dotgit
        if dotgit.is_file():
            text = dotgit.read_text(encoding="utf-8", errors="ignore").strip()
            if text.startswith("gitdir:"):
                p = Path(text[len("gitdir:"):].strip())
                return p if p.is_absolute() else (source_root / p).resolve()
    except OSError:
        return None
    return None


def _is_git_backed(source_root: Path) -> bool:
    """Is this path inside a git checkout? Walks up the way git itself does.

    Used only to tell `not_tracked` (nothing to compare, ever) from `unknown`
    (something to compare that could not be read) — never to resolve a
    revision, which stays with ``_git_head``.
    """
    try:
        p = source_root.resolve()
    except OSError:
        return False
    for candidate in (p, *p.parents):
        try:
            if (candidate / ".git").exists():
                return True
        except OSError:
            continue
    return False


def _head_signature(git_dir: Path) -> Optional[tuple]:
    """Stat-based signature that changes whenever HEAD's commit moves.

    Covers ordinary commits (loose ref + reflog), ref packing (packed-refs),
    and branch switch / detach (HEAD content). Returns None when nothing could
    be stat'd, signalling the caller to fall back to TTL-bounded caching.
    """
    paths = [
        git_dir / "HEAD",
        git_dir / "packed-refs",
        git_dir / "logs" / "HEAD",
    ]
    try:
        head_txt = (git_dir / "HEAD").read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        head_txt = ""
    if head_txt.startswith("ref:"):
        ref = head_txt[4:].strip()
        paths.append(git_dir / ref)
        # Worktrees keep shared refs (loose + packed) in the common dir.
        try:
            commondir = (git_dir / "commondir").read_text(encoding="utf-8", errors="ignore").strip()
        except OSError:
            commondir = ""
        if commondir:
            base = (git_dir / commondir).resolve()
            paths.append(base / ref)
            paths.append(base / "packed-refs")
    sig: list[tuple[str, Optional[int]]] = []
    found = False
    for p in paths:
        try:
            sig.append((str(p), p.stat().st_mtime_ns))
            found = True
        except OSError:
            sig.append((str(p), None))
    return tuple(sig) if found else None


def _git_head_uncached(source_root: Path) -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(source_root),
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=2,
            stdin=subprocess.DEVNULL,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        logger.debug("git rev-parse HEAD failed at %s", source_root, exc_info=True)
    return None


def _git_head(source_root: Path) -> Optional[str]:
    """Cached ``git rev-parse HEAD``. Re-runs the subprocess only when the HEAD
    signature changes, or — when no signature is available — at most once per
    TTL window per repo. Always safe: a cache miss just recomputes."""
    key = str(source_root)
    git_dir = _resolve_git_dir(source_root)
    sig = _head_signature(git_dir) if git_dir else None
    now = time.monotonic()

    cached = _head_cache.get(key)
    if cached is not None:
        c_sig, c_sha, c_ts = cached
        if sig is not None and c_sig is not None and sig == c_sig:
            return c_sha
        if sig is None and c_sig is None and (now - c_ts) < _HEAD_CACHE_TTL_S:
            return c_sha

    sha = _git_head_uncached(source_root)
    _head_cache[key] = (sig, sha, now)
    return sha


class FreshnessProbe:
    """Per-call freshness classifier.

    Construct once per tool invocation, then call ``classify(file_path)``
    for each returned symbol's file. The probe holds:
      * ``index_sha``  — the SHA stored at index time.
      * ``current_sha`` — fresh git HEAD (lazy, cached).
      * ``indexed_ts`` — Unix epoch of the index timestamp.
      * ``mtime_cache`` — per-file mtime memo (str → float | None).
    """

    def __init__(
        self,
        source_root: Optional[str],
        indexed_at: str,
        index_sha: Optional[str],
        *,
        current_sha: Optional[str] = None,
        file_mtimes: Optional[dict] = None,
    ):
        self._source_root = Path(source_root) if source_root else None
        self._index_sha = index_sha or None
        self._indexed_ts = _parse_iso(indexed_at)
        self._current_sha = current_sha  # may be None (lazy)
        self._current_sha_resolved = current_sha is not None
        self._mtime_cache: dict[str, Optional[float]] = {}
        # Per-file mtime recorded at index time (CodeIndex.file_mtimes is in
        # nanoseconds; convert to seconds). When available, comparison is
        # per-file rather than against a single index-wide indexed_at.
        self._indexed_mtimes_s: dict[str, float] = {}
        if file_mtimes:
            for path, ns in file_mtimes.items():
                try:
                    self._indexed_mtimes_s[path] = float(ns) / 1e9
                except (TypeError, ValueError):
                    pass

    @property
    def repo_is_stale(self) -> bool:
        """True iff index SHA differs from the live HEAD (and we know HEAD)."""
        cur = self._lazy_current_sha()
        if not cur or not self._index_sha:
            return False
        return cur != self._index_sha

    @property
    def repo_freshness(self) -> str:
        """One of ``fresh`` / ``stale`` / ``unknown`` / ``not_tracked`` (#377 item 4).

        ``repo_is_stale`` is a Boolean, and a Boolean has nowhere to put "I could
        not find out". Both "the SHAs match" and "one of them is missing"
        answered False, and the verdict then rendered False as ``fresh`` — so an
        index whose freshness was never established claimed current-snapshot
        equivalence. These are different facts and only one of them is proof:

          fresh        indexed revision equals the live revision
          stale        they differ
          unknown      this subject HAS a revision we should be able to read and
                       we could not: git failed, the binary is missing, the
                       source root moved, or the index stored no SHA
          not_tracked  this subject has no revision at all (a plain folder), so
                       there is nothing to compare and there never will be

        The split matters because the two non-answers deserve opposite
        treatment: `unknown` is a capability we have, failing, so an absence
        claim cannot be current-state proven; `not_tracked` is a capability the
        subject does not support, which is disclosed rather than refused (the
        call jDataMunch made in v1.26.0, for the same reason).
        """
        if not self._source_root or not self._source_root.exists():
            return _UNKNOWN
        cur = self._lazy_current_sha()
        if cur:
            # The revision reads, so this subject is tracked either way. An
            # index that stored no SHA of its own leaves the comparison
            # unmade, which is unknown rather than fresh.
            return (
                _FRESH if cur == self._index_sha
                else _STALE_REPO if self._index_sha
                else _UNKNOWN
            )
        # No revision came back. Distinguish "there is none to read" from
        # "there is one and reading it failed" by looking for a repository the
        # way git does, walking up: a monorepo SUBDIRECTORY is tracked by the
        # checkout above it, and calling that not_tracked would understate what
        # we know about it.
        return _NOT_TRACKED if not _is_git_backed(self._source_root) else _UNKNOWN

    def _lazy_current_sha(self) -> Optional[str]:
        if self._current_sha_resolved:
            return self._current_sha
        if self._source_root and self._source_root.exists():
            self._current_sha = _git_head(self._source_root)
        self._current_sha_resolved = True
        return self._current_sha

    def _file_mtime(self, file_rel: str) -> Optional[float]:
        if file_rel in self._mtime_cache:
            return self._mtime_cache[file_rel]
        if not self._source_root:
            self._mtime_cache[file_rel] = None
            return None
        try:
            # One stat: a missing file raises, which reads the same as absent.
            mtime = (self._source_root / file_rel).stat().st_mtime
        except OSError:
            mtime = None
        self._mtime_cache[file_rel] = mtime
        return mtime

    def prime_mtimes(self, files) -> None:
        """Stat every file not yet cached, on a few threads, before classify() runs.

        Each file gets the same single ``stat`` that ``_file_mtime`` makes, with
        the same missing-reads-as-absent rule, so ``classify`` returns exactly
        what it would have; only the waiting overlaps, and the per-file path
        object is skipped (on a 14,432-file scope that overhead was about as
        large as the stats themselves). A scope that is mostly cached, or a
        probe with no source root, costs nothing here.
        """
        if not self._source_root:
            return
        todo = [f for f in dict.fromkeys(files) if f and f not in self._mtime_cache]
        if len(todo) < _PRIME_MIN_FILES:
            return
        root = str(self._source_root)

        def one(rel: str) -> Optional[float]:
            try:
                return os.stat(os.path.join(root, rel)).st_mtime
            except OSError:
                return None

        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=_PRIME_WORKERS, thread_name_prefix="jcm-stat") as ex:
            for rel, mtime in zip(todo, ex.map(one, todo, chunksize=256)):
                self._mtime_cache[rel] = mtime

    def classify(self, file_rel: str) -> str:
        """Return one of fresh / edited_uncommitted / stale_index / unknown.

        Every ``unknown`` below is a comparison that could not be made. None of
        them may answer ``fresh``: that would assert current-snapshot
        equivalence off a measurement that never happened.

        ⚠⚠ **Per-file evidence outranks the repo-wide SHA** (v1.108.225,
        @rknighton #405). ``repo_is_stale`` used to short-circuit here, so one
        commit touching one file answered ``stale_index`` for *every* file in
        the repository — including files whose recorded mtime proves they are
        byte-identical to what was indexed. That is not conservatism: ``unknown``
        would cost nothing, but ``stale_index`` is a positive claim that the
        indexed content no longer describes the file, made against a measurement
        we already had and did not read. It also made the signal carry no
        information exactly where it should discriminate — on a repository under
        active commit, every row read ``stale_index`` until the next full
        reindex, which is the cost side of #395.

        The repo-wide signal still decides the **label** for a file that did
        move, so a committed change reads ``stale_index`` rather than degrading
        to ``edited_uncommitted``, and it remains available whole on
        ``repo_freshness`` / ``_meta.freshness.repo_is_stale``.

        ⚠ So ``repo_is_stale: true`` alongside rows reading ``fresh`` is now a
        legitimate, non-contradictory payload: the repository moved, these files
        did not. A reader expecting "stale repo implies stale rows" is reading
        the old contract.

        ⚠ **Disclosed residual: mtime is a proxy for content.** Checking a file
        out and back again advances its mtime without changing its bytes, so it
        reads ``stale_index`` when the content matches. That is the safe
        direction and is what this already did for the uncommitted case. The
        exact answer is ``git diff --name-only <index_sha>..HEAD``, one
        subprocess per call rather than a stat, and it is not paid here.
        """
        if not file_rel:
            # No path to stat, so nothing to compare. An entry that carries no
            # file cannot be attested either way.
            return _UNKNOWN_FILE
        mtime_now = self._file_mtime(file_rel)
        # Prefer per-file indexed mtime when available (more accurate than
        # the single index-wide indexed_at timestamp) AND more specific than
        # the repo-wide SHA, which is why this now runs before it.
        per_file_indexed = self._indexed_mtimes_s.get(file_rel)
        if mtime_now is not None and per_file_indexed is not None:
            if mtime_now > per_file_indexed + 1.0:
                return _STALE if self.repo_is_stale else _EDITED
            return _FRESH
        if self.repo_is_stale:
            return _STALE
        if mtime_now is None:
            # No source root, the root moved, the file is gone from the tree,
            # or the stat raised. All four are "could not find out".
            return _UNKNOWN_FILE
        # We have a current mtime but no baseline to measure it against: the
        # index recorded no per-file mtime AND no parseable indexed_at.
        if not self._indexed_ts:
            return _UNKNOWN_FILE
        if mtime_now > self._indexed_ts + 1.0:
            return _EDITED
        return _FRESH

    def annotate(self, entries: list[dict], file_field: str = "file") -> list[dict]:
        """In-place ``_freshness`` annotation on a list of result entries.

        Returns the same list for chaining.
        """
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            file_rel = entry.get(file_field) or ""
            entry["_freshness"] = self.classify(file_rel)
        return entries

    def summary(self, entries: list[dict]) -> dict:
        """Bucket-count summary of ``_freshness`` across entries.

        An entry carrying no ``_freshness`` at all counts as ``unknown``, not
        ``fresh`` — an un-annotated row has not been measured either.
        """
        counts = {_FRESH: 0, _EDITED: 0, _STALE: 0, _UNKNOWN_FILE: 0}
        for e in entries:
            if isinstance(e, dict):
                bucket = e.get("_freshness", _UNKNOWN_FILE)
                counts[bucket] = counts.get(bucket, 0) + 1
        return {
            "fresh": counts.get(_FRESH, 0),
            "edited_uncommitted": counts.get(_EDITED, 0),
            "stale_index": counts.get(_STALE, 0),
            "unknown": counts.get(_UNKNOWN_FILE, 0),
            "repo_is_stale": self.repo_is_stale,
        }
