"""Get a compact session snapshot for context continuity.

Returns a ~200 token markdown summary of files explored, edits made, 
searches performed, and dead ends. Designed for injection after 
context compaction to restore session orientation.
"""
from typing import Optional
import time


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
    
    # Use get_context with frequency sorting to get focus files first, with all the needed parameters
    context = journal.get_context(
        max_files=max_files, 
        max_queries=max_searches,
        max_edits=max_edits,  # Limit edited files as well
        sort_by="frequency"  # Sort by frequency to show focus files (most accessed) first
    )
    
    # Format the compact snapshot
    duration_mins = int(context["session_duration_s"] / 60)
    duration_str = f"{duration_mins}m" if duration_mins > 0 else f"{int(context['session_duration_s'])}s"
    
    snapshot_parts = [
        f"## Session Snapshot (jCodemunch)",
        f"**Duration:** {duration_str} | **Files explored:** {context['total_unique_files']} | **Searches:** {context['total_unique_queries']}"
    ]
    
    # Helper function to truncate long file paths for token efficiency
    def truncate_path(path: str, max_len: int = 50) -> str:
        """Truncate long paths to save tokens."""
        if len(path) <= max_len:
            return path
        # Handle both Windows and Unix path separators
        normalized = path.replace('\\', '/')
        parts = normalized.split('/')
        if len(parts) <= 2:
            return path
        return f".../{'/'.join(parts[-2:])}"
    
    # Focus files (most accessed)
    if context["files_accessed"]:
        snapshot_parts.append("\n### Focus files (most accessed)")
        for item in context["files_accessed"]:
            # Truncate path if it's very long
            truncated_file = truncate_path(item['file'])
            snapshot_parts.append(f"- {truncated_file} ({item['reads']} reads, last: {item['last_tool']})")
    
    # Edited files
    if context["files_edited"]:
        snapshot_parts.append("\n### Edited files")
        for item in context["files_edited"]:
            truncated_file = truncate_path(item['file'])
            snapshot_parts.append(f"- {truncated_file} ({item['edits']} edits)")
    
    # Key searches
    if context["recent_searches"]:
        snapshot_parts.append("\n### Key searches")
        for item in context["recent_searches"]:
            snapshot_parts.append(f"- \"{item['query']}\" → {item['result_count']} results")
    
    # Dead ends (negative evidence)
    dead_ends = []
    if include_negative_evidence:
        neg_log = journal.get_negative_evidence_log()  # Read held briefly to minimize lock time
        # Take last N items (most recent)
        recent_neg_log = neg_log[-max_searches:] if neg_log else []
        dead_ends.extend([
            {"query": entry["query"], "verdict": entry["verdict"]} 
            for entry in recent_neg_log
        ])
        if recent_neg_log:
            snapshot_parts.append("\n### Dead ends (don't re-search)")
            for entry in recent_neg_log:
                verdict_display = entry["verdict"].replace('_', ' ')
                if "scanned_symbols" in entry:
                    snapshot_parts.append(f"- \"{entry['query']}\" → {verdict_display} (scanned {entry['scanned_symbols']} symbols)")
                else:
                    snapshot_parts.append(f"- \"{entry['query']}\" → {verdict_display}")
    
    # Build structured data to match test expectations and align with plan
    structured = {
        "focus_files": [
            {"file": item["file"], "reads": item["reads"], "last_tool": item["last_tool"]}
            for item in context["files_accessed"]
        ],
        "edited_files": [
            {"file": item["file"], "edits": item["edits"]}
            for item in context["files_edited"]
        ],
        "key_searches": [
            {"query": item["query"], "count": item["count"], "result_count": item["result_count"]}
            for item in context["recent_searches"]
        ],
        "dead_ends": dead_ends,
        "session_duration_s": context["session_duration_s"],
        "total_files_explored": context["total_unique_files"],
        "total_searches": context["total_unique_queries"],
    }
    
    result = {
        "snapshot": "\n".join(snapshot_parts),
        "structured": structured,
        "_meta": {
            "timing_ms": round((time.perf_counter() - start) * 1000, 1),
        }
    }
    
    return result
