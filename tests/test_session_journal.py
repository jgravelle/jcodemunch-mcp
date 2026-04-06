"""Tests for session journal (Feature 2)."""

import pytest
import threading
import time
from pathlib import Path


class TestSessionJournal:
    """Tests for SessionJournal class."""

    def test_record_read_appears_in_context(self):
        """Record one read, verify files_accessed has correct info."""
        from jcodemunch_mcp.tools.session_journal import SessionJournal
        journal = SessionJournal()  # Fresh instance, not singleton
        journal.record_read("src/main.py", "get_symbol_source")
        ctx = journal.get_context()
        assert len(ctx["files_accessed"]) == 1
        entry = ctx["files_accessed"][0]
        assert entry["file"] == "src/main.py"
        assert entry["reads"] == 1
        assert entry["last_tool"] == "get_symbol_source"

    def test_duplicate_reads_increment_count(self):
        """Same file read twice → reads == 2, last_tool updated."""
        from jcodemunch_mcp.tools.session_journal import SessionJournal
        journal = SessionJournal()
        journal.record_read("src/utils.py", "get_file_content")
        journal.record_read("src/utils.py", "get_symbol_source")
        ctx = journal.get_context()
        assert len(ctx["files_accessed"]) == 1
        entry = ctx["files_accessed"][0]
        assert entry["reads"] == 2
        assert entry["last_tool"] == "get_symbol_source"

    def test_record_search_appears(self):
        """Record a search, verify recent_searches."""
        from jcodemunch_mcp.tools.session_journal import SessionJournal
        journal = SessionJournal()
        journal.record_search("my_func", 5)
        ctx = journal.get_context()
        assert len(ctx["recent_searches"]) == 1
        entry = ctx["recent_searches"][0]
        assert entry["query"] == "my_func"
        assert entry["result_count"] == 5

    def test_record_edit_appears(self):
        """Record an edit, verify files_edited."""
        from jcodemunch_mcp.tools.session_journal import SessionJournal
        journal = SessionJournal()
        journal.record_edit("src/auth.py")
        ctx = journal.get_context()
        assert len(ctx["files_edited"]) == 1
        entry = ctx["files_edited"][0]
        assert entry["file"] == "src/auth.py"
        assert entry["edits"] == 1

    def test_record_tool_call_counted(self):
        """Count tool calls."""
        from jcodemunch_mcp.tools.session_journal import SessionJournal
        journal = SessionJournal()
        journal.record_tool_call("search_symbols")
        journal.record_tool_call("search_symbols")
        journal.record_tool_call("get_symbol_source")
        ctx = journal.get_context()
        assert ctx["tool_calls"]["search_symbols"] == 2
        assert ctx["tool_calls"]["get_symbol_source"] == 1

    def test_max_files_limit(self):
        """get_context(max_files=N) limits output, not total_unique_files."""
        from jcodemunch_mcp.tools.session_journal import SessionJournal
        journal = SessionJournal()
        for i in range(30):
            journal.record_read(f"src/file{i}.py", "get_symbol_source")
        ctx = journal.get_context(max_files=10)
        assert len(ctx["files_accessed"]) == 10
        assert ctx["total_unique_files"] == 30

    def test_max_queries_limit(self):
        """get_context(max_queries=N) limits searches output."""
        from jcodemunch_mcp.tools.session_journal import SessionJournal
        journal = SessionJournal()
        for i in range(30):
            journal.record_search(f"query{i}", i)
        ctx = journal.get_context(max_queries=10)
        assert len(ctx["recent_searches"]) == 10
        assert ctx["total_unique_queries"] == 30

    def test_session_duration_positive(self):
        """session_duration_s >= 0."""
        from jcodemunch_mcp.tools.session_journal import SessionJournal
        journal = SessionJournal()
        time.sleep(0.01)  # tiny delay
        ctx = journal.get_context()
        assert ctx["session_duration_s"] >= 0

    def test_thread_safety(self):
        """5 threads × 100 writes each, no exceptions, correct totals."""
        from jcodemunch_mcp.tools.session_journal import SessionJournal
        journal = SessionJournal()
        errors = []

        def writer(thread_id: int):
            try:
                for i in range(100):
                    journal.record_read(f"src/file{thread_id}_{i}.py", "get_symbol_source")
                    journal.record_search(f"query{thread_id}_{i}", i)
                    journal.record_tool_call("search_symbols")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(errors) == 0
        ctx = journal.get_context(max_files=1000, max_queries=1000)
        assert ctx["total_unique_files"] == 500
        assert ctx["total_unique_queries"] == 500
        assert ctx["tool_calls"]["search_symbols"] == 500


class TestSessionJournalSingleton:
    """Tests for get_journal() singleton."""

    def test_get_journal_returns_same_instance(self):
        """get_journal() returns the same instance."""
        from jcodemunch_mcp.tools.session_journal import get_journal
        j1 = get_journal()
        j2 = get_journal()
        assert j1 is j2

    def test_singleton_records_persist(self):
        """Records via singleton persist across calls."""
        from jcodemunch_mcp.tools.session_journal import get_journal
        journal = get_journal()
        # Clear any existing state
        journal._files.clear()
        journal._queries.clear()
        journal._edits.clear()
        journal._tool_calls.clear()
        
        journal.record_read("src/test.py", "get_symbol_source")
        journal2 = get_journal()
        ctx = journal2.get_context()
        assert len(ctx["files_accessed"]) == 1

class TestSessionJournalSortBy:
    """Tests for get_context() sort_by parameter."""

    def test_get_context_sort_by_frequency(self):
        """Test that get_context sorts all components by frequency."""
        from jcodemunch_mcp.tools.session_journal import SessionJournal
        journal = SessionJournal()

        # Files with different read counts
        journal.record_read("least_read.py", "get_file_outline")
        journal.record_read("most_read.py", "get_file_outline")
        journal.record_read("most_read.py", "get_file_content")
        journal.record_read("most_read.py", "get_file_content")
        journal.record_read("moderately_read.py", "get_file_outline")
        journal.record_read("moderately_read.py", "get_file_content")

        # Queries with different frequencies
        journal.record_search("least_searched", 5)
        journal.record_search("most_searched", 2)
        journal.record_search("most_searched", 3)
        journal.record_search("moderately_searched", 4)

        # Edits with different counts
        journal.record_edit("least_edited.py")
        journal.record_edit("most_edited.py")
        journal.record_edit("most_edited.py")
        journal.record_edit("most_edited.py")
        journal.record_edit("moderately_edited.py")
        journal.record_edit("moderately_edited.py")

        context = journal.get_context(
            max_files=10, max_queries=10, max_edits=10, sort_by="frequency"
        )

        # Verify files sorted by read count descending
        files = context["files_accessed"]
        assert files[0]["file"] == "most_read.py"
        assert files[0]["reads"] == 3
        assert files[1]["file"] == "moderately_read.py"
        assert files[1]["reads"] == 2
        assert files[2]["file"] == "least_read.py"
        assert files[2]["reads"] == 1

        # Verify queries sorted by count descending
        searches = context["recent_searches"]
        assert searches[0]["query"] == "most_searched"
        assert searches[0]["count"] == 2

        # Verify edits sorted by edit count descending
        edits = context["files_edited"]
        assert edits[0]["file"] == "most_edited.py"
        assert edits[0]["edits"] == 3
        assert edits[1]["file"] == "moderately_edited.py"
        assert edits[1]["edits"] == 2
        assert edits[2]["file"] == "least_edited.py"
        assert edits[2]["edits"] == 1

    def test_get_context_sort_by_timestamp(self):
        """Test that get_context sorts by timestamp when sort_by='timestamp'."""
        from jcodemunch_mcp.tools.session_journal import SessionJournal
        journal = SessionJournal()

        journal.record_read("first_read.py", "get_file_outline")
        journal.record_read("second_read.py", "get_file_content")

        context_default = journal.get_context(max_files=10, max_queries=10, max_edits=10)
        context_timestamp = journal.get_context(
            max_files=10, max_queries=10, max_edits=10, sort_by="timestamp"
        )

        assert context_default["files_accessed"] == context_timestamp["files_accessed"]

    def test_get_context_default_sort_is_timestamp(self):
        """Test that default sorting is by timestamp."""
        from jcodemunch_mcp.tools.session_journal import SessionJournal
        journal = SessionJournal()

        journal.record_read("first_read.py", "get_file_outline")
        journal.record_read("second_read.py", "get_file_content")

        context_default = journal.get_context(max_files=10, max_queries=10, max_edits=10)
        context_timestamp = journal.get_context(
            max_files=10, max_queries=10, max_edits=10, sort_by="timestamp"
        )

        assert len(context_default["files_accessed"]) == len(context_timestamp["files_accessed"])
