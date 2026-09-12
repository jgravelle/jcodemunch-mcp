"""get_symbol_provenance — git archaeology for a symbol: who wrote it, when, and *why*.

Traces the complete authorship lineage of a symbol through git history:
commit messages, authors, date ranges, semantic change categories, and
a distilled "origin story" that explains the symbol's evolution.

Goes far beyond simple blame: reconstructs the *narrative* behind a
symbol's existence by classifying each commit that touched it into
semantic categories (creation, bugfix, refactor, feature, perf, docs,
test, config, rename, revert) and extracting the motivating intent
from commit messages.

Requires a locally indexed repo (``index_folder``).
"""

from __future__ import annotations

import logging
import re
import sqlite3
import subprocess
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Optional

from ..storage import IndexStore
from ..storage.generation import connect_readonly
from ._utils import resolve_repo

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Semantic commit classification
# ---------------------------------------------------------------------------

# Patterns checked against the first line of a commit message (case-insensitive).
# Order matters: first match wins.
_CATEGORY_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("revert",   re.compile(r"^revert", re.IGNORECASE)),
    ("rename",   re.compile(r"\brename[ds]?\b|\bmove[ds]?\b", re.IGNORECASE)),
    ("bugfix",   re.compile(r"\bfix(?:e[ds])?\b|\bbug\b|\bpatch\b|\bhotfix\b|\bcorrect\b|\bresolve[ds]?\b", re.IGNORECASE)),
    ("perf",     re.compile(r"\bperf(?:ormance)?\b|\boptimiz[e]?\b|\bspeed\b|\bfaster\b|\bcache\b", re.IGNORECASE)),
    ("refactor", re.compile(r"\brefactor\b|\bclean\s*up\b|\brestructur\b|\bsimplif\b|\bextract\b|\bdecompos\b", re.IGNORECASE)),
    ("test",     re.compile(r"\btest[s]?\b|\bspec[s]?\b|\bcoverage\b", re.IGNORECASE)),
    ("docs",     re.compile(r"\bdoc[s]?\b|\breadme\b|\bchangelog\b|\bcomment\b", re.IGNORECASE)),
    ("config",   re.compile(r"\bconfig\b|\bci\b|\bdocker\b|\byaml\b|\benv\b|\bdeps?\b|\bbump\b|\bupgrade\b", re.IGNORECASE)),
    ("feature",  re.compile(r"\badd[s]?\b|\bfeat(?:ure)?\b|\bimplement\b|\bintroduc\b|\bnew\b|\bsupport\b|\benable\b", re.IGNORECASE)),
]


def _classify_commit(subject: str) -> str:
    """Classify a commit subject line into a semantic category."""
    for category, pattern in _CATEGORY_PATTERNS:
        if pattern.search(subject):
            return category
    return "evolution"  # generic catch-all


def _extract_intent(message: str) -> str:
    """Extract the motivating intent from a commit message.

    Takes the first non-empty body line (after the subject) that looks
    like a reason/motivation.  Falls back to the subject itself.
    """
    lines = message.strip().splitlines()
    if len(lines) <= 1:
        return lines[0] if lines else ""

    # Skip subject + blank line, look for a meaningful body line
    for line in lines[1:]:
        stripped = line.strip()
        if not stripped:
            continue
        # Skip conventional-commit trailers and sign-offs
        if re.match(r"^(Signed-off-by|Co-authored-by|Reviewed-by|Acked-by|Fixes|Closes|Refs?):", stripped, re.IGNORECASE):
            continue
        if stripped.startswith("#"):
            continue
        # Found a body line — likely explains intent
        return stripped

    return lines[0]  # fall back to subject


# ---------------------------------------------------------------------------
# Phase 5 — runtime stack-frequency enrichment
# ---------------------------------------------------------------------------


def _load_stack_frequency(
    db_path,
    symbol_id: str,
    since_days: int,
) -> Optional[dict]:
    """Return the per-severity stack-event frequency for ``symbol_id``.

    Reads ``runtime_stack_events`` over the last ``since_days``. Returns
    None when:
      * the table doesn't exist (pre-v16 DB),
      * the table exists but has no rows for this symbol within the window,
      * the table is empty entirely (no stack logs ingested).

    Read-only / immutable connection so the LRU cache can't be evicted
    by an mtime bump (matches the Phase 2 confidence-probe pattern).
    """
    try:
        conn = connect_readonly(db_path, isolation_level="")
    except sqlite3.OperationalError:
        return None
    conn.row_factory = sqlite3.Row
    try:
        try:
            row = conn.execute(
                "SELECT 1 FROM runtime_stack_events LIMIT 1"
            ).fetchone()
        except sqlite3.OperationalError:
            return None
        if row is None:
            return None
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, since_days))).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        rows = conn.execute(
            """
            SELECT severity, SUM(count) AS total, MAX(last_seen) AS last_seen,
                   MIN(first_seen) AS first_seen
            FROM runtime_stack_events
            WHERE symbol_id = ? AND last_seen >= ?
            GROUP BY severity
            """,
            (symbol_id, cutoff),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return None

    by_sev: dict[str, int] = {"error": 0, "warn": 0, "info": 0}
    last_seen: Optional[str] = None
    first_seen: Optional[str] = None
    for r in rows:
        sev = (r["severity"] or "info").lower()
        if sev not in by_sev:
            by_sev[sev] = 0
        by_sev[sev] += int(r["total"] or 0)
        ls = r["last_seen"]
        fs = r["first_seen"]
        if ls and (last_seen is None or ls > last_seen):
            last_seen = ls
        if fs and (first_seen is None or fs < first_seen):
            first_seen = fs

    total = sum(by_sev.values())
    if total == 0:
        return None
    return {
        "since_days": since_days,
        "total_events": total,
        "by_severity": by_sev,
        "first_seen": first_seen,
        "last_seen": last_seen,
    }


# ---------------------------------------------------------------------------
# Main tool
# ---------------------------------------------------------------------------

def get_symbol_provenance(
    repo: str,
    symbol: str,
    max_commits: int = 25,
    storage_path: Optional[str] = None,
) -> dict:
    """Trace the complete authorship lineage and evolution narrative of a symbol.

    Args:
        repo:         Repository identifier (owner/repo or bare name).
        symbol:       Symbol name or full ID as returned by ``search_symbols``.
        max_commits:  Maximum number of commits to analyse (default 25, max 100).
        storage_path: Optional index storage path override.

    Returns:
        Dict with:
          - symbol: {name, kind, file, line, id}
          - origin: first commit that introduced the symbol's file
          - lineage: list of commits, each with {sha, author, date, subject,
            category, intent}
          - authors: ranked list of contributors by commit count
          - evolution_summary: {total_commits, categories (count per category),
            dominant_category, lifespan_days, avg_commits_per_month}
          - narrative: a single-paragraph human-readable origin story
          - _meta
    """
    t0 = time.perf_counter()
    max_commits = max(1, min(max_commits, 100))

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
                "get_symbol_provenance requires a locally indexed repo (index_folder). "
                "GitHub-indexed repos (index_repo) do not have a local git working tree."
            )
        }
    cwd = index.source_root

    # Resolve symbol
    sym = next((s for s in index.symbols if s.get("id") == symbol), None)
    if sym is None:
        # Try by name
        by_name = [s for s in index.symbols if s.get("name") == symbol]
        if len(by_name) == 1:
            sym = by_name[0]
        elif len(by_name) > 1:
            return {
                "error": f"Ambiguous symbol name '{symbol}': found {len(by_name)} definitions. Use the symbol ID to disambiguate.",
                "candidates": [{"name": s["name"], "file": s["file"], "id": s["id"]} for s in by_name],
            }
        else:
            return {"error": f"Symbol not found: '{symbol}'. Try search_symbols first."}

    sym_name: str = sym.get("name", "")
    sym_file: str = sym.get("file", "")
    sym_kind: str = sym.get("kind", "")
    sym_line: int = sym.get("line", 0)
    sym_end_line: int = sym.get("end_line", sym_line)
    sym_id: str = sym.get("id", "")

    # Verify git availability
    rc, _, err = _run_git(["rev-parse", "--git-dir"], cwd=cwd)
    if rc != 0:
        if rc == -1:
            return {"error": "git not found on PATH."}
        return {"error": f"Not a git repository: {err}"}

    # Strategy: git log with line-range filtering when we have line info,
    # otherwise fall back to file-level log + commit-message grep.
    #
    # git log -L is the gold standard: it tracks the function across renames
    # and only reports commits that actually touched those lines.
    # Format: hash|author|date|subject
    log_args: list[str]
    use_line_log = bool(sym_line and sym_end_line and sym_end_line > sym_line)

    if use_line_log:
        # -L tracks the line range across renames — no --follow needed
        log_args = [
            "log",
            f"-L{sym_line},{sym_end_line}:{sym_file}",
            f"-n{max_commits}",
            "--no-patch",
            "--format=%H|%an|%aI|%s%n%b%n---PROVENANCE_DELIM---",
        ]
    else:
        # Fall back to file-level log
        log_args = [
            "log",
            "--follow",
            f"-n{max_commits}",
            "--format=%H|%an|%aI|%s%n%b%n---PROVENANCE_DELIM---",
            "--",
            sym_file,
        ]

    rc2, log_out, log_err = _run_git(log_args, cwd=cwd, timeout=30)

    # git log -L may fail on some git versions or binary files; fall back
    if rc2 != 0 and use_line_log:
        use_line_log = False
        log_args = [
            "log",
            "--follow",
            f"-n{max_commits}",
            "--format=%H|%an|%aI|%s%n%b%n---PROVENANCE_DELIM---",
            "--",
            sym_file,
        ]
        rc2, log_out, log_err = _run_git(log_args, cwd=cwd, timeout=30)

    if rc2 != 0:
        return {"error": f"git log failed: {log_err}"}

    # Parse commits from delimited output
    lineage: list[dict] = []
    raw_blocks = log_out.split("---PROVENANCE_DELIM---")

    for block in raw_blocks:
        block = block.strip()
        if not block:
            continue
        lines = block.splitlines()
        if not lines:
            continue
        header = lines[0]
        parts = header.split("|", 3)
        if len(parts) < 4:
            continue

        sha, author, date, subject = parts[0], parts[1], parts[2], parts[3]
        # Reconstruct full message (body after header line)
        full_message = subject + "\n" + "\n".join(lines[1:]) if len(lines) > 1 else subject

        category = _classify_commit(subject)
        intent = _extract_intent(full_message)

        lineage.append({
            "sha": sha[:12],
            "author": author,
            "date": date[:10],
            "subject": subject,
            "category": category,
            "intent": intent,
        })

    # If file-level log, filter to commits that likely touched this symbol
    # by checking if the commit's diff mentions the symbol name
    if not use_line_log and lineage and sym_name:
        filtered: list[dict] = []
        for entry in lineage:
            # Check if this commit's diff mentions the symbol
            rc3, diff_out, _ = _run_git(
                ["show", "--no-patch", "--format=", "-p", entry["sha"][:12], "--", sym_file],
                cwd=cwd, timeout=10,
            )
            if rc3 == 0 and sym_name in diff_out:
                filtered.append(entry)
            elif not filtered:
                # Always include the first (most recent) commit as context
                filtered.append(entry)
        # If filtering removed everything, keep the originals
        if filtered:
            lineage = filtered

    # Build author rankings
    author_counts = Counter(c["author"] for c in lineage)
    authors_ranked = [
        {"author": author, "commits": count}
        for author, count in author_counts.most_common()
    ]

    # Evolution summary
    category_counts = Counter(c["category"] for c in lineage)
    dominant_category = category_counts.most_common(1)[0][0] if category_counts else "unknown"

    # Lifespan calculation
    lifespan_days = 0
    avg_commits_per_month = 0.0
    if len(lineage) >= 2:
        try:
            from datetime import datetime
            first_date = datetime.fromisoformat(lineage[-1]["date"])
            last_date = datetime.fromisoformat(lineage[0]["date"])
            lifespan_days = (last_date - first_date).days
            if lifespan_days > 0:
                avg_commits_per_month = round(len(lineage) / (lifespan_days / 30.0), 2)
        except Exception:
            logger.debug("Date parsing failed for lifespan calc", exc_info=True)

    # Origin commit (last in the list = oldest)
    origin = lineage[-1] if lineage else None

    # Generate narrative
    narrative = _build_narrative(sym_name, sym_kind, lineage, authors_ranked, dominant_category, lifespan_days)

    # Phase 5: optional runtime stack-frequency enrichment. Zero-cost when
    # runtime_stack_events is empty (or the table doesn't exist on a
    # pre-v16 DB). Surfaces the "this symbol last appeared in N error
    # stacks over the past D days" signal.
    db_path = store._sqlite._db_path(owner, name)  # type: ignore[attr-defined]
    stack_freq = _load_stack_frequency(db_path, sym_id, since_days=30) if sym_id else None
    if stack_freq is not None and stack_freq.get("by_severity", {}).get("error", 0) >= 3:
        # Append a sentence to the narrative when the symbol shows up in
        # production error stacks repeatedly — that's a load-bearing signal
        # the static narrative can't otherwise convey.
        err_n = stack_freq["by_severity"]["error"]
        last = stack_freq.get("last_seen") or ""
        narrative += (
            f" Runtime: this symbol appeared in {err_n} error stack(s) over the "
            f"past {stack_freq['since_days']} days"
            + (f" (most recently {last})" if last else "")
            + " — review it carefully."
        )

    elapsed = (time.perf_counter() - t0) * 1000
    response: dict = {
        "repo": f"{owner}/{name}",
        "symbol": {
            "name": sym_name,
            "kind": sym_kind,
            "file": sym_file,
            "line": sym_line,
            "id": sym_id,
        },
        "origin": origin,
        "lineage": lineage,
        "lineage_count": len(lineage),
        "authors": authors_ranked,
        "evolution_summary": {
            "total_commits": len(lineage),
            "categories": dict(category_counts.most_common()),
            "dominant_category": dominant_category,
            "lifespan_days": lifespan_days,
            "avg_commits_per_month": avg_commits_per_month,
        },
        "narrative": narrative,
        "_meta": {
            "timing_ms": round(elapsed, 1),
            "methodology": "git_log_line_range" if use_line_log else "git_log_file_filtered",
            "confidence_level": "high" if use_line_log else "medium",
            "tip": (
                "lineage = commits from newest to oldest. "
                "category = semantic classification of each commit. "
                "intent = extracted motivation from commit body. "
                "narrative = human-readable summary of the symbol's evolution. "
                "stack_frequency (when present) = stack-frame appearances by "
                "severity from runtime_stack_events; signals operational risk."
            ),
        },
    }
    if stack_freq is not None:
        response["stack_frequency"] = stack_freq

    # Compiler-diagnostics snapshot beside the runtime one. None = no data
    # ingested = no key; a symbol the checker cleared gets a real zero.
    from ._diagnostics_consume import (  # noqa: PLC0415
        diagnostics_currency, diagnostics_snapshot, live_git_head, load_symbol_diagnostics,
    )
    diag_map = load_symbol_diagnostics(db_path, [sym_id]) if sym_id else None
    if diag_map is not None:
        d = diag_map.get(sym_id) or {"errors": 0, "warnings": 0, "infos": 0, "tools": [], "codes": []}
        snap = diagnostics_snapshot(db_path) or {}
        response["diagnostics"] = {
            "errors": d["errors"],
            "warnings": d["warnings"],
            "infos": d["infos"],
            "tools": d["tools"],
            "codes": d["codes"],
            "as_of": snap.get("as_of"),
            "current": diagnostics_currency(snap.get("as_of"), live_git_head(cwd)),
        }
    return response


def _build_narrative(
    sym_name: str,
    sym_kind: str,
    lineage: list[dict],
    authors: list[dict],
    dominant_category: str,
    lifespan_days: int,
) -> str:
    """Build a human-readable origin story for the symbol."""
    if not lineage:
        return f"No git history found for {sym_kind} `{sym_name}`."

    origin = lineage[-1]
    latest = lineage[0]
    num_authors = len(authors)
    num_commits = len(lineage)

    # Opening: who created it and when
    parts: list[str] = []
    parts.append(
        f"`{sym_name}` was introduced by {origin['author']} on {origin['date']}"
    )
    if origin["intent"] and origin["intent"] != origin["subject"]:
        parts.append(f' ("{origin["intent"][:120]}")')
    else:
        parts.append(f' ("{origin["subject"][:120]}")')
    parts.append(".")

    # Middle: evolution summary
    if num_commits > 1:
        parts.append(f" Over {lifespan_days} days and {num_commits} commits")
        if num_authors > 1:
            parts.append(f" by {num_authors} contributors")
        parts.append(f", the dominant change pattern is **{dominant_category}**")

        # Highlight if it's been heavily bugfixed
        bugfix_count = sum(1 for c in lineage if c["category"] == "bugfix")
        if bugfix_count >= 3:
            parts.append(f" (with {bugfix_count} bug fixes — consider reviewing for structural issues)")
        parts.append(".")

    # Closing: last change
    if num_commits > 1:
        parts.append(
            f" Last modified by {latest['author']} on {latest['date']}"
            f" ({latest['category']}: \"{latest['subject'][:80]}\")."
        )

    return "".join(parts)
