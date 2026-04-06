"""Session journal for tracking file reads, searches, and edits."""

import threading
import time
from collections import defaultdict
from typing import Optional


_MAX_JOURNAL_ENTRIES = 5000  # Per-dict cap to prevent unbounded memory growth


class SessionJournal:
    """Track file reads, searches, and edits during a session."""

    def __init__(self):
        self._lock = threading.Lock()
        self._start = time.time()
        # files: {file_path: {"reads": int, "last_tool": str, "last_ts": float}}
        self._files: dict[str, dict] = {}
        # queries: {query: {"count": int, "result_count": int, "last_ts": float}}
        self._queries: dict[str, dict] = {}
        # edits: {file_path: {"edits": int, "last_ts": float}}
        self._edits: dict[str, dict] = {}
        # tool_calls: {tool_name: count}
        self._tool_calls: dict[str, int] = defaultdict(int)
        # negative evidence log: [{query, repo, verdict, scanned_symbols, timestamp}]
        self._negative_evidence_log: list[dict] = []

    @staticmethod
    def _evict_oldest(d: dict, cap: int) -> None:
        """Evict oldest entries by last_ts when dict exceeds cap. Caller holds lock."""
        if len(d) <= cap:
            return
        by_ts = sorted(d.items(), key=lambda x: x[1].get("last_ts", 0))
        for key, _ in by_ts[: len(d) - cap]:
            del d[key]

    def record_read(self, file_path: str, tool_name: str) -> None:
        """Record a file read operation."""
        with self._lock:
            now = time.time()
            if file_path in self._files:
                self._files[file_path]["reads"] += 1
                self._files[file_path]["last_tool"] = tool_name
                self._files[file_path]["last_ts"] = now
            else:
                self._files[file_path] = {
                    "reads": 1,
                    "last_tool": tool_name,
                    "last_ts": now,
                }
                self._evict_oldest(self._files, _MAX_JOURNAL_ENTRIES)

    def record_search(self, query: str, result_count: int) -> None:
        """Record a search operation."""
        with self._lock:
            now = time.time()
            if query in self._queries:
                self._queries[query]["count"] += 1
                self._queries[query]["result_count"] = result_count
                self._queries[query]["last_ts"] = now
            else:
                self._queries[query] = {
                    "count": 1,
                    "result_count": result_count,
                    "last_ts": now,
                }
                self._evict_oldest(self._queries, _MAX_JOURNAL_ENTRIES)

    def record_edit(self, file_path: str) -> None:
        """Record a file edit operation."""
        with self._lock:
            now = time.time()
            if file_path in self._edits:
                self._edits[file_path]["edits"] += 1
                self._edits[file_path]["last_ts"] = now
            else:
                self._edits[file_path] = {
                    "edits": 1,
                    "last_ts": now,
                }
                self._evict_oldest(self._edits, _MAX_JOURNAL_ENTRIES)

    def record_negative_evidence(self, entry: dict) -> None:
        """Record a negative evidence result from search_symbols."""
        with self._lock:
            self._negative_evidence_log.append(entry)
            if len(self._negative_evidence_log) > _MAX_JOURNAL_ENTRIES:
                self._negative_evidence_log = self._negative_evidence_log[-_MAX_JOURNAL_ENTRIES:]

    def get_negative_evidence_log(self) -> list[dict]:
        """Return a copy of the negative evidence log."""
        with self._lock:
            return list(self._negative_evidence_log)

    def record_tool_call(self, tool_name: str) -> None:
        """Record a tool call."""
        with self._lock:
            self._tool_calls[tool_name] += 1

    def get_context(
        self,
        max_files: int = 50,
        max_queries: int = 20,
        max_edits: int = 20,  # Add max_edits parameter to support the get_session_snapshot use case
        sort_by: str = "timestamp",  # "timestamp" (by last_ts) or "frequency" (by access frequency count)
    ) -> dict:
        """Get session context summary.

        Args:
            max_files: Maximum number of files to return in files_accessed.
            max_queries: Maximum number of queries to return in recent_searches.
            max_edits: Maximum number of files to return in files_edited.
            sort_by: How to sort - 'timestamp' (by last_ts) or 'frequency' (by access frequency count).

        Returns:
            Dict with files_accessed, recent_searches, files_edited, tool_calls,
            session_duration_s, total_unique_files, total_unique_queries.
        """
        with self._lock:
            # Sort files based on the sort_by parameter
            if sort_by == "frequency":
                sorted_files = sorted(
                    self._files.items(),
                    key=lambda x: x[1]["reads"],  # Sort by read count
                    reverse=True,
                )
            else:  # default to "timestamp"
                sorted_files = sorted(
                    self._files.items(),
                    key=lambda x: x[1].get("last_ts", 0),  # Sort by timestamp
                    reverse=True,
                )
            # Take top N after sorting
            files_accessed = [
                {
                    "file": fp,
                    "reads": data["reads"],
                    "last_tool": data["last_tool"],
                }
                for fp, data in sorted_files[:max_files]
            ]

            # Sort queries based on the sort_by parameter
            if sort_by == "frequency":
                sorted_queries = sorted(
                    self._queries.items(),
                    key=lambda x: x[1]["count"],  # Sort by query count
                    reverse=True,
                )
            else:  # default to "timestamp"
                sorted_queries = sorted(
                    self._queries.items(),
                    key=lambda x: x[1].get("last_ts", 0),  # Sort by timestamp
                    reverse=True,
                )
            # Take top N after sorting
            recent_searches = [
                {
                    "query": q,
                    "count": data["count"],
                    "result_count": data["result_count"],
                }
                for q, data in sorted_queries[:max_queries]
            ]

            # For consistency, we need to sort edits as well and respect max_edits
            if sort_by == "frequency":
                sorted_edits = sorted(
                    self._edits.items(),
                    key=lambda x: x[1]["edits"],  # Sort by edit count
                    reverse=True,
                )
            else:  # default to "timestamp"
                sorted_edits = sorted(
                    self._edits.items(),
                    key=lambda x: x[1].get("last_ts", 0),  # Sort by timestamp
                    reverse=True,
                )
            # Take top N after sorting for edits
            files_edited = [
                {
                    "file": fp,
                    "edits": data["edits"],
                }
                for fp, data in sorted_edits[:max_edits]
            ]

            return {
                "files_accessed": files_accessed,
                "recent_searches": recent_searches,
                "files_edited": files_edited,
                "tool_calls": dict(self._tool_calls),
                "session_duration_s": round(time.time() - self._start, 2),
                "total_unique_files": len(self._files),
                "total_unique_queries": len(self._queries),
            }



# Singleton instance
_journal: Optional[SessionJournal] = None
_journal_lock = threading.Lock()


def get_journal() -> SessionJournal:
    """Get the singleton SessionJournal instance."""
    global _journal
    with _journal_lock:
        if _journal is None:
            _journal = SessionJournal()
        return _journal