"""get_delivery_metrics — durable-change delivery over a window.

Quantifies how much *stuck* over a time window, not how much activity happened.
For each non-merge commit in the window it asks: did this land and survive, or
was it reverted / re-touched (churn-back) within a short horizon? The durable
count is the honest numerator for a cost-per-outcome ratio — pair it with AI
spend to show how much got done for how little, instead of rewarding raw volume.

Read-only git archaeology over the repo's local working tree (requires a locally
indexed repo via ``index_folder``; GitHub-indexed repos have no git tree).

Buckets are mutually exclusive, one per commit, applied by precedence:

  revert_authored  the commit itself undoes earlier work (not forward delivery)
  reverted         a later commit reverts this one
  reworked         a file it touched was re-touched by a later commit within the
                   rework horizon (churn-back)
  durable          landed and stuck

Durability is a *trailing* signal: commits inside the last ``rework_horizon_days``
haven't had time to be reworked, so they are flagged ``provisional`` (counted
durable for now, but not yet settled). Attribution is commit-level and
approximate by design — this is a diagnostic trend, not a score to chase
(rewarding the number itself just re-invents the proxy-gaming it replaces).
"""

from __future__ import annotations

import logging
import re
import subprocess
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Optional

from ..storage import IndexStore
from ._utils import resolve_repo
from .get_symbol_provenance import _classify_commit

logger = logging.getLogger(__name__)

# Field/record separators that cannot appear in commit text.
_US = "\x1f"  # unit separator (between fields)
_RS = "\x1e"  # record separator (between commits)

# Safety cap so a long window on a busy repo can't spin unbounded.
_MAX_COMMITS = 3000

# Detect "git revert" bodies: `This reverts commit <40-hex>.`
_REVERTS_RE = re.compile(r"This reverts commit ([0-9a-f]{7,40})", re.IGNORECASE)
_REVERT_SUBJECT_RE = re.compile(r"^revert\b", re.IGNORECASE)


def _run_git(args: list[str], cwd: str, timeout: int = 30) -> tuple[int, str, str]:
    try:
        r = subprocess.run(
            ["git"] + args,
            cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, stdin=subprocess.DEVNULL,
        )
        return r.returncode, r.stdout, r.stderr.strip()
    except FileNotFoundError:
        return -1, "", "git not found on PATH"
    except subprocess.TimeoutExpired:
        return -2, "", "git command timed out"
    except Exception as exc:
        logger.debug("git subprocess error: %s", exc, exc_info=True)
        return -3, "", str(exc)


def _parse_iso(raw: str) -> Optional[datetime]:
    """Parse git's strict-ISO committer date into an aware datetime."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        # Fall back: drop a trailing 'Z' or compact +0000 offset variations.
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def get_delivery_metrics(
    repo: str,
    window_days: int = 30,
    rework_horizon_days: int = 14,
    storage_path: Optional[str] = None,
) -> dict:
    """Return durable-change delivery metrics for a repo over a window.

    The ``commits_durable`` count is the intended numerator for a cost-per-outcome
    ratio (divide AI spend over the same window by it): how much landed and stuck
    per unit cost, rather than how much raw activity occurred.

    Args:
        repo:                Repository identifier (owner/repo or bare name).
        window_days:         Look-back window in days (default 30).
        rework_horizon_days: Days within which a re-touch counts as churn-back
                             (default 14). Also defines the ``provisional`` tail.
        storage_path:        Optional index storage path override.

    Returns:
        ``{repo, window_days, rework_horizon_days, commits_total, commits_durable,
           commits_reverted, commits_reworked, commits_revert_authored,
           commits_provisional, durable_rate, rework_rate, files_touched,
           by_category, assessment, _meta}``
    """
    t0 = time.perf_counter()
    window_days = max(1, window_days)
    rework_horizon_days = max(0, rework_horizon_days)

    try:
        owner, name = resolve_repo(repo, storage_path)
    except ValueError as e:
        return {"error": str(e)}

    store = IndexStore(base_path=storage_path)
    index = store.load_index(owner, name)
    if index is None:
        return {"error": f"No index found for {repo!r}. Run index_folder first."}

    if not index.source_root:
        return {
            "error": (
                "get_delivery_metrics requires a locally indexed repo (index_folder). "
                "GitHub-indexed repos (index_repo) do not have a local git working tree."
            )
        }
    cwd = index.source_root

    rc, _, err = _run_git(["rev-parse", "--git-dir"], cwd=cwd)
    if rc != 0:
        if rc == -1:
            return {"error": "git not found on PATH."}
        return {"error": f"Not a git repository: {err}"}

    # One pass: per-commit metadata + body + changed files. Reworkers of any
    # in-window commit are themselves more recent, hence also in-window, so a
    # single window pass is sufficient (no lookahead needed).
    rc2, out, err2 = _run_git(
        [
            "log",
            "--no-merges",
            f"--since={window_days} days ago",
            # (#685) index-root-relative paths, and only changes under it.
            "--relative",
            "--name-only",
            f"--format={_RS}%H{_US}%cI{_US}%s{_US}%b{_US}",
            # (#685) `-- .`: `--relative` alone still LISTS a commit that
            # changes nothing under the root, with an empty file set, and an
            # empty set can never be reworked, so it counted as durable.
            "--",
            ".",
        ],
        cwd=cwd,
        timeout=45,
    )
    if rc2 != 0:
        return {"error": f"git log failed: {err2 or 'unknown error'}"}

    truncated = False
    commits: list[dict] = []
    reverted_targets: set[str] = set()
    file_events: dict[str, list[tuple[datetime, str]]] = {}

    records = [r for r in out.split(_RS) if r.strip()]
    if len(records) > _MAX_COMMITS:
        records = records[:_MAX_COMMITS]
        truncated = True

    for rec in records:
        # rec = "<sha>\x1f<cISO>\x1f<subject>\x1f<body>\x1f\n<file>\n<file>..."
        head, _, tail = rec.partition(f"{_US}\n")
        fields = head.split(_US)
        if len(fields) < 4:
            continue
        sha, date_raw, subject, body = fields[0].strip(), fields[1], fields[2], fields[3]
        dt = _parse_iso(date_raw)
        if dt is None:
            continue
        files = {ln.strip() for ln in tail.splitlines() if ln.strip()}

        is_revert_author = bool(
            _REVERT_SUBJECT_RE.match(subject.strip())
        )
        for m in _REVERTS_RE.finditer(body):
            is_revert_author = True
            reverted_targets.add(m.group(1).lower())

        commits.append({
            "sha": sha,
            "short": sha[:10],
            "date": dt,
            "subject": subject.strip(),
            "files": files,
            "is_revert_author": is_revert_author,
        })
        for f in files:
            file_events.setdefault(f, []).append((dt, sha))

    total = len(commits)
    if total == 0:
        return {
            "repo": repo,
            "window_days": window_days,
            "rework_horizon_days": rework_horizon_days,
            "commits_total": 0,
            "commits_durable": 0,
            "commits_reverted": 0,
            "commits_reworked": 0,
            "commits_revert_authored": 0,
            "commits_provisional": 0,
            "durable_rate": 0.0,
            "rework_rate": 0.0,
            "files_touched": 0,
            "by_category": {},
            "assessment": f"No non-merge commits in the last {window_days} days.",
            "_meta": {
                "timing_ms": round((time.perf_counter() - t0) * 1000, 1),
                "note": "Numerator for cost-per-outcome is commits_durable.",
            },
        }

    # Resolve 7-40 char revert targets to the full shas present in the window.
    reverted_full: set[str] = set()
    for tgt in reverted_targets:
        for c in commits:
            if c["sha"].lower().startswith(tgt):
                reverted_full.add(c["sha"])
                break

    # Hub files (CHANGELOG, version files, a monolithic server/dispatch module)
    # are co-touched by most commits by construction, so a re-touch of one is not
    # evidence that a specific earlier commit was redone. Exclude them from the
    # rework signal: a file touched by an outsized share of window commits can't
    # localize churn-back. Threshold is auditable via _meta.hub_files_excluded.
    hub_cutoff = max(4, -(-total * 20 // 100))  # ceil(0.20 * total), floor 4
    hub_files = {f for f, evs in file_events.items() if len(evs) >= hub_cutoff}

    horizon = timedelta(days=rework_horizon_days)
    now = datetime.now(timezone.utc)
    provisional_edge = now - horizon

    durable = reverted = reworked = revert_authored = provisional = 0
    by_category: Counter = Counter()

    for c in commits:
        # Bucket by precedence.
        if c["is_revert_author"]:
            revert_authored += 1
            continue
        if c["sha"] in reverted_full:
            reverted += 1
            continue
        # Reworked: a later, distinct commit touched a shared NON-hub file within
        # the horizon (localized churn-back, not shared-ledger co-editing).
        is_reworked = False
        if rework_horizon_days > 0:
            for f in c["files"] - hub_files:
                for (other_dt, other_sha) in file_events.get(f, ()):
                    if other_sha == c["sha"]:
                        continue
                    delta = other_dt - c["date"]
                    if timedelta(0) < delta <= horizon:
                        is_reworked = True
                        break
                if is_reworked:
                    break
        if is_reworked:
            reworked += 1
            continue
        durable += 1
        by_category[_classify_commit(c["subject"])] += 1
        if c["date"] > provisional_edge:
            provisional += 1

    settled = total - revert_authored  # forward-delivery candidates
    not_durable = reverted + reworked
    durable_rate = round(durable / total, 3) if total else 0.0
    rework_rate = round((not_durable + revert_authored) / total, 3) if total else 0.0

    prov_note = (
        f" {provisional} too recent to be final (< {rework_horizon_days}d)."
        if provisional else ""
    )
    assessment = (
        f"{durable} durable change(s) in {window_days} days; "
        f"rework {int(rework_rate * 100)}%.{prov_note}"
    )

    return {
        "repo": repo,
        "window_days": window_days,
        "rework_horizon_days": rework_horizon_days,
        "commits_total": total,
        "commits_durable": durable,
        "commits_reverted": reverted,
        "commits_reworked": reworked,
        "commits_revert_authored": revert_authored,
        "commits_provisional": provisional,
        "durable_rate": durable_rate,
        "rework_rate": rework_rate,
        "files_touched": len(file_events),
        "by_category": dict(by_category.most_common()),
        "assessment": assessment,
        "_meta": {
            "timing_ms": round((time.perf_counter() - t0) * 1000, 1),
            "note": "Numerator for cost-per-outcome is commits_durable; divide AI "
                    "spend over the same window by it. Durability is trailing — see "
                    "commits_provisional.",
            "truncated": truncated,
            "settled_candidates": settled,
            "hub_files_excluded": len(hub_files),
        },
    }
