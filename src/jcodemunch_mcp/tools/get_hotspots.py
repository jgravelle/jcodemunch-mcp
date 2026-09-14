"""get_hotspots — identify the highest-risk symbols by combining complexity and churn.

Methodology (Adam Tornhill / CodeScene):
    hotspot_score = cyclomatic_complexity * log(1 + commits_last_N_days)

A symbol is a hotspot when it is both complex (hard to understand) and frequently changed
(high probability of introducing bugs).  Files with no local git history return a score
based on complexity alone (churn treated as 0).

Requires the repo to have been indexed with ``index_folder`` (local repos only).
Requires INDEX_VERSION >= 7 (jcodemunch-mcp >= 1.16) for complexity data.
"""

from __future__ import annotations

import logging
import math
import subprocess
import time
from collections import defaultdict
from typing import Optional

from ..storage import IndexStore
from ._utils import resolve_repo

logger = logging.getLogger(__name__)


from ._git_history import attach_history_coverage, churn_is_measurable


def _run_git(args: list[str], cwd: str, timeout: int = 30) -> tuple[int, str, str]:
    try:
        r = subprocess.run(
            ["git"] + args,
            cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, stdin=subprocess.DEVNULL,
        )
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except FileNotFoundError:
        return -1, "", "git not found on PATH"
    except subprocess.TimeoutExpired:
        return -2, "", "git command timed out"
    except Exception as exc:
        logger.debug("git subprocess error: %s", exc, exc_info=True)
        return -3, "", str(exc)


def _get_file_churn(cwd: str, days: int) -> dict[str, int]:
    """Return {relative_file_path: commit_count} for all files changed in the window.

    Uses a single ``git log --name-only`` pass for efficiency.
    """
    # (#685) `--relative`: `--name-only` prints paths from the git TOP LEVEL, the
    # index holds them from `source_root`, and an index rooted below the top
    # level (`identity_mode="local"`) would miss every name: churn 0 reads as a
    # cold file, not as an error.
    rc, out, err = _run_git(
        ["log", f"--since={days} days ago", "--relative", "--name-only", "--format="],
        cwd=cwd,
        timeout=60,
    )
    if rc != 0:
        # A failing git is not a repository with no churn; say so.
        logger.warning("churn unavailable: git log exited %s in %s: %s", rc, cwd, (err or "").strip()[:500])
        return {}
    if not out:
        return {}

    counts: dict[str, int] = defaultdict(int)
    for line in out.splitlines():
        line = line.strip()
        if line:
            counts[line] += 1
    return dict(counts)


def get_hotspots(
    repo: str,
    top_n: int = 20,
    days: int = 90,
    min_complexity: int = 2,
    storage_path: Optional[str] = None,
) -> dict:
    """Return the top-N highest-risk symbols ranked by hotspot score.

    hotspot_score = cyclomatic_complexity × log(1 + commits_last_N_days)

    Only functions and methods are included.  Symbols with cyclomatic < min_complexity
    are excluded to filter trivial getters/setters.

    Args:
        repo:            Repository identifier (owner/repo or bare name).
        top_n:           Number of results to return (default 20).
        days:            Churn look-back window in days (default 90).
        min_complexity:  Minimum cyclomatic complexity to include (default 2).
        storage_path:    Optional index storage path override.

    Returns:
        ``{repo, top_n, days, hotspots, git_available, _meta}``
        Each entry: ``{symbol_id, name, kind, file, line, cyclomatic,
                       max_nesting, param_count, churn, hotspot_score,
                       assessment}``
        ``assessment`` is ``"low"`` (score ≤ 3), ``"medium"`` (≤ 10),
        or ``"high"`` (> 10). Allows relay without interpreting the score.
    """
    t0 = time.perf_counter()

    try:
        owner, name = resolve_repo(repo, storage_path)
    except ValueError as e:
        return {"error": str(e)}

    store = IndexStore(base_path=storage_path)
    index = store.load_index(owner, name)
    if index is None:
        return {"error": f"No index found for {repo!r}. Run index_folder first."}

    # Gather file churn from git (best-effort; graceful fallback)
    git_available = False
    file_churn: dict[str, int] = {}
    if index.source_root:
        rc_check, _, _ = _run_git(["rev-parse", "--git-dir"], cwd=index.source_root)
        if rc_check == 0:
            git_available = True
            file_churn = _get_file_churn(index.source_root, days)

    # Normalise file paths: git outputs forward-slash paths; index may use either
    file_churn_norm = {k.replace("\\", "/"): v for k, v in file_churn.items()}

    candidates: list[dict] = []
    for sym in index.symbols:
        if sym.get("kind") not in ("function", "method"):
            continue
        cyclomatic = sym.get("cyclomatic") or 0
        if cyclomatic < min_complexity:
            continue

        file_path = sym.get("file", "")
        file_norm = file_path.replace("\\", "/")
        churn = file_churn_norm.get(file_norm, 0)

        hotspot_score = round(cyclomatic * math.log1p(churn), 4)

        if hotspot_score > 10:
            assessment = "high"
        elif hotspot_score > 3:
            assessment = "medium"
        else:
            assessment = "low"

        candidates.append({
            "symbol_id": sym.get("id", ""),
            "name": sym.get("name", ""),
            "kind": sym.get("kind", ""),
            "file": file_path,
            "line": sym.get("line") or 0,
            "cyclomatic": cyclomatic,
            "max_nesting": sym.get("max_nesting") or 0,
            "param_count": sym.get("param_count") or 0,
            "churn": churn,
            "hotspot_score": hotspot_score,
            "assessment": assessment,
        })

    candidates.sort(key=lambda x: -x["hotspot_score"])
    top = candidates[:max(1, top_n)]

    has_complexity_data = any(c.get("cyclomatic", 0) > 0 for c in top)
    note = None
    if not has_complexity_data:
        note = (
            "No complexity data found — re-index with jcodemunch-mcp >= 1.16 "
            "to populate cyclomatic complexity metrics."
        )

    # ⚠⚠ `git_available` answers "is there a repo here", NOT "does its history
    # reach back `days`". A shallow clone answers every churn question with a
    # small number and exit status 0, so `hotspot_score` collapses toward
    # complexity alone and the ranking looks plausible while measuring nothing.
    # Measured on this repo at ONE commit: shallow 82.2 (B) vs full 75.5 (C).
    # `churn_measurable` is the honest gate; `_meta.git_history` names why.
    churn_measurable = True
    if git_available and index.source_root:
        churn_measurable = churn_is_measurable(index.source_root, days)

    result: dict = {
        "repo": f"{owner}/{name}",
        "top_n": top_n,
        "days": days,
        "git_available": git_available,
        "churn_measurable": churn_measurable,
        "hotspots": top,
        "_meta": {
            "timing_ms": round((time.perf_counter() - t0) * 1000, 1),
            "methodology": "complexity_x_churn",
            "confidence_level": "medium" if churn_measurable else "low",
        },
    }
    if git_available and index.source_root:
        attach_history_coverage(result, index.source_root, days)
    if note:
        result["note"] = note
    return result
