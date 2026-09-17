"""Get a compact session snapshot for context continuity.

Returns a ~200 token markdown summary of files explored, edits made,
searches performed, and dead ends. Designed for injection after
context compaction to restore session orientation.
"""
from typing import Optional
import time

# #711: the same predicate `citable_absence` applies, not a local copy of its
# conditions. This consumer is session-wide and has no repository to match
# against, which is why it calls the repo-independent half rather than
# `citable_absence` itself.
from .session_journal import SessionJournal


def _truncate_path(path: str, max_len: int = 50) -> str:
    """Truncate long paths to save tokens."""
    if len(path) <= max_len:
        return path
    # Handle both Windows and Unix path separators
    normalized = path.replace('\\', '/')
    parts = normalized.split('/')
    if len(parts) <= 2:
        return path
    return f".../{'/'.join(parts[-2:])}"


def _render_snapshot(
    context: dict,
    neg_log: list,
    max_files: int = 10,
    max_searches: int = 5,
    max_edits: int = 10,
    include_negative_evidence: bool = True,
) -> dict:
    """Render a snapshot ({snapshot, structured}) from a journal context dict.

    Shared by the live in-process tool (get_session_snapshot) and the
    out-of-process PreCompact hook (snapshot_from_live), so both produce
    byte-identical output. The context lists are sliced defensively, so a
    caller may pass un-capped lists (e.g. from the persisted live journal).
    """
    files_accessed = context.get("files_accessed", [])[:max_files]
    recent_searches = context.get("recent_searches", [])[:max_searches]
    files_edited = context.get("files_edited", [])[:max_edits]
    total_files = context.get("total_unique_files", len(context.get("files_accessed", [])))
    total_queries = context.get("total_unique_queries", len(context.get("recent_searches", [])))
    duration_s = context.get("session_duration_s", 0)

    duration_mins = int(duration_s / 60)
    duration_str = f"{duration_mins}m" if duration_mins > 0 else f"{int(duration_s)}s"

    snapshot_parts = [
        "## Session Snapshot (jCodemunch)",
        f"**Duration:** {duration_str} | **Files explored:** {total_files} | **Searches:** {total_queries}",
    ]

    # Focus files (most accessed)
    if files_accessed:
        snapshot_parts.append("\n### Focus files (most accessed)")
        for item in files_accessed:
            truncated_file = _truncate_path(item['file'])
            snapshot_parts.append(f"- {truncated_file} ({item['reads']} reads, last: {item['last_tool']})")

    # Edited files
    if files_edited:
        snapshot_parts.append("\n### Edited files")
        for item in files_edited:
            truncated_file = _truncate_path(item['file'])
            snapshot_parts.append(f"- {truncated_file} ({item['edits']} edits)")

    # Key searches
    if recent_searches:
        snapshot_parts.append("\n### Key searches")
        for item in recent_searches:
            snapshot_parts.append(f"- \"{item['query']}\" → {item['result_count']} results")

    # Dead ends (negative evidence)
    #
    # ⚠⚠ #711, second consumer. This block used to render EVERY entry in the
    # log under "don't re-search", dropping the `repo` field and keeping every
    # verdict -- so a miss in repo A told the model not to re-search it while
    # working in repo B, and a `low_confidence_matches` (weak matches FOUND)
    # was published as a dead end. This is the snapshot the model reads at
    # every compact and resume, so it is the widest surface the defect had.
    # An entry earns the heading only if it is the same absence claim
    # `SessionJournal.citable_absence` would honour, and the repository it was
    # measured in is named ON THE LINE, because this snapshot is session-wide
    # and has no repo of its own to filter against.
    dead_ends = []
    if include_negative_evidence:
        recent_neg_log = [
            entry
            for entry in (neg_log or [])
            if SessionJournal.entry_is_citable(entry)
        ][-max_searches:]
        dead_ends.extend([
            {
                "query": entry["query"],
                "verdict": entry["verdict"],
                "repo": entry.get("repo", ""),
            }
            for entry in recent_neg_log
        ])
        if recent_neg_log:
            snapshot_parts.append("\n### Dead ends (don't re-search in the named repo)")
            for entry in recent_neg_log:
                verdict_display = entry["verdict"].replace('_', ' ')
                where = f" in {entry['repo']}" if entry.get("repo") else ""
                if "scanned_symbols" in entry:
                    snapshot_parts.append(f"- \"{entry['query']}\"{where} → {verdict_display} (scanned {entry['scanned_symbols']} symbols)")
                else:
                    snapshot_parts.append(f"- \"{entry['query']}\"{where} → {verdict_display}")

    structured = {
        "focus_files": [
            {"file": item["file"], "reads": item["reads"], "last_tool": item["last_tool"]}
            for item in files_accessed
        ],
        "edited_files": [
            {"file": item["file"], "edits": item["edits"]}
            for item in files_edited
        ],
        "key_searches": [
            {"query": item["query"], "count": item["count"], "result_count": item["result_count"]}
            for item in recent_searches
        ],
        "dead_ends": dead_ends,
        "session_duration_s": duration_s,
        "total_files_explored": total_files,
        "total_searches": total_queries,
    }

    return {
        "snapshot": "\n".join(snapshot_parts),
        "structured": structured,
    }


def get_session_snapshot(
    max_files: int = 10,
    max_searches: int = 5,
    max_edits: int = 10,
    include_negative_evidence: bool = True,
    storage_path: Optional[str] = None,  # API consistency
) -> dict:
    """Get a compact session snapshot for context continuity.

    Args:
        max_files: Maximum focus files to include.
        max_searches: Maximum key searches to include.
        max_edits: Maximum edited files to include.
        include_negative_evidence: Include dead-end searches (negative evidence) in snapshot.
        storage_path: For API consistency, unused.

    Returns:
        Dict with:
            - snapshot: Compact markdown text for context injection (~200 tokens)
            - structured: Machine-readable version with detailed fields
            - _meta: Timing information
    """
    start = time.perf_counter()
    from .session_journal import get_journal
    journal = get_journal()

    # Use get_context with frequency sorting to get focus files first.
    context = journal.get_context(
        max_files=max_files,
        max_queries=max_searches,
        max_edits=max_edits,
        sort_by="frequency",
    )
    neg_log = journal.get_negative_evidence_log() if include_negative_evidence else []

    result = _render_snapshot(
        context, neg_log,
        max_files=max_files, max_searches=max_searches, max_edits=max_edits,
        include_negative_evidence=include_negative_evidence,
    )
    result["_meta"] = {"timing_ms": round((time.perf_counter() - start) * 1000, 1)}
    return result


def snapshot_from_live(
    base_path: Optional[str] = None,
    max_files: int = 10,
    max_searches: int = 5,
    max_edits: int = 10,
    include_negative_evidence: bool = True,
    max_age_minutes: Optional[int] = None,
) -> Optional[dict]:
    """Build a snapshot from the persisted live journal (#334).

    The PreCompact hook runs in a separate process from the MCP server, so its
    in-process journal is empty. This reads the live journal the server writes
    incrementally and renders it with the same formatter the live tool uses.

    Returns the snapshot dict (with ``_context`` for landmark seeding), or None
    when no live journal data is available or it records no activity — letting
    the caller emit an explicit fallback rather than a misleading zero-state
    snapshot.
    """
    start = time.perf_counter()
    from .session_state import load_live_journal
    data = load_live_journal(base_path=base_path, max_age_minutes=max_age_minutes)
    if not data:
        return None

    context = {
        "files_accessed": data.get("files_accessed", []),
        "recent_searches": data.get("recent_searches", []),
        "files_edited": data.get("files_edited", []),
        "session_duration_s": data.get("session_duration_s", 0),
        "total_unique_files": data.get("total_unique_files", 0),
        "total_unique_queries": data.get("total_unique_queries", 0),
    }
    if (
        not context["total_unique_files"]
        and not context["total_unique_queries"]
        and not context["files_edited"]
    ):
        return None

    neg_log = data.get("negative_evidence_log", []) if include_negative_evidence else []
    result = _render_snapshot(
        context, neg_log,
        max_files=max_files, max_searches=max_searches, max_edits=max_edits,
        include_negative_evidence=include_negative_evidence,
    )
    result["_meta"] = {
        "timing_ms": round((time.perf_counter() - start) * 1000, 1),
        "source": "live_journal",
    }
    result["_context"] = context  # for the hook's landmark enrichment
    return result
