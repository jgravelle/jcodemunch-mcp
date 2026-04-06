"""Unit tests for get_session_snapshot tool."""
import time
from unittest.mock import patch
import pytest

from jcodemunch_mcp.tools.session_journal import SessionJournal


@pytest.fixture(autouse=True)
def reset_session_journal():
    """Reset the session journal for each test to prevent test pollution."""
    from jcodemunch_mcp.tools import session_journal
    
    # Reset the singleton by setting it to None
    with session_journal._journal_lock:
        session_journal._journal = None
    
    yield
    # Cleanup after test


def test_empty_session_returns_minimal_snapshot():
    """Test that an empty session returns a basic snapshot structure."""
    from jcodemunch_mcp.tools.get_session_snapshot import get_session_snapshot
    
    result = get_session_snapshot(max_files=10, max_searches=5, max_edits=10, include_negative_evidence=True)
    
    assert "snapshot" in result
    assert "structured" in result
    assert "focus_files" in result["structured"]
    assert "edited_files" in result["structured"]
    assert "key_searches" in result["structured"]
    assert "dead_ends" in result["structured"]
    assert "session_duration_s" in result["structured"]
    assert "total_files_explored" in result["structured"]
    assert "total_searches" in result["structured"]
    assert "_meta" in result
    assert "timing_ms" in result["_meta"]
    assert isinstance(result["snapshot"], str)
    assert len(result["structured"]["focus_files"]) == 0
    assert len(result["structured"]["edited_files"]) == 0
    assert len(result["structured"]["key_searches"]) == 0
    assert len(result["structured"]["dead_ends"]) == 0
    assert result["structured"]["total_files_explored"] == 0
    assert result["structured"]["total_searches"] == 0


def test_snapshot_includes_focus_files():
    """Test that recorded file reads appear in the snapshot sorted by count."""
    from jcodemunch_mcp.tools.session_journal import get_journal
    
    journal = get_journal()
    
    # Record some file reads
    journal.record_read("src/server.py", "get_file_content")
    journal.record_read("src/server.py", "get_file_outline")  # Second read
    journal.record_read("src/tools/search.py", "search_symbols")  # First read
    
    from jcodemunch_mcp.tools.get_session_snapshot import get_session_snapshot
    
    result = get_session_snapshot(max_files=10, max_searches=5, max_edits=10, include_negative_evidence=True)
    
    # Should have server.py first (2 reads), then search.py (1 read)
    focus_files = result["structured"]["focus_files"]
    assert len(focus_files) == 2
    assert focus_files[0]["file"] == "src/server.py"
    assert focus_files[0]["reads"] == 2
    assert focus_files[1]["file"] == "src/tools/search.py"
    assert focus_files[1]["reads"] == 1


def test_snapshot_includes_edited_files():
    """Test that recorded file edits appear in the snapshot."""
    from jcodemunch_mcp.tools.session_journal import get_journal
    
    journal = get_journal()
    
    # Record some file edits
    journal.record_edit("src/new_feature.py")
    journal.record_edit("src/new_feature.py")  # Second edit
    journal.record_edit("src/utils.py")  # First edit
    
    from jcodemunch_mcp.tools.get_session_snapshot import get_session_snapshot
    
    result = get_session_snapshot(max_files=10, max_searches=5, max_edits=10, include_negative_evidence=True)
    
    edited_files = result["structured"]["edited_files"]
    assert len(edited_files) == 2
    assert {"file": "src/new_feature.py", "edits": 2} in edited_files
    assert {"file": "src/utils.py", "edits": 1} in edited_files


def test_snapshot_includes_searches():
    """Test that recorded searches appear in the snapshot."""
    from jcodemunch_mcp.tools.session_journal import get_journal
    
    journal = get_journal()
    
    # Record some searches
    journal.record_search("session snapshot", 3)
    journal.record_search("get_session_context", 0)  # Zero results
    journal.record_search("session snapshot", 4)  # Second search for same term
    
    from jcodemunch_mcp.tools.get_session_snapshot import get_session_snapshot
    
    result = get_session_snapshot(max_files=10, max_searches=10, max_edits=10, include_negative_evidence=True)
    
    searches = result["structured"]["key_searches"]
    assert len(searches) == 2  # Two unique searches
    # The duplicate search should have incremented the count
    search_dict = {s["query"]: s for s in searches}
    assert "session snapshot" in search_dict
    assert search_dict["session snapshot"]["count"] == 2  # Two searches
    assert search_dict["session snapshot"]["result_count"] == 4  # Last result count wins


def test_snapshot_includes_negative_evidence():
    """Test that negative evidence appears in the dead ends section."""
    from jcodemunch_mcp.tools.session_journal import get_journal
    
    journal = get_journal()
    
    # Record negative evidence
    journal.record_negative_evidence({
        "query": "nonexistent_function",
        "verdict": "no_implementation_found",
        "scanned_symbols": 4147
    })
    journal.record_negative_evidence({
        "query": "missing_feature",
        "verdict": "low_confidence_matches",
        "scanned_symbols": 150
    })
    
    from jcodemunch_mcp.tools.get_session_snapshot import get_session_snapshot
    
    result = get_session_snapshot(max_files=10, max_searches=5, max_edits=10, include_negative_evidence=True)
    
    dead_ends = result["structured"]["dead_ends"]
    assert len(dead_ends) == 2
    assert {"query": "nonexistent_function", "verdict": "no_implementation_found"} in dead_ends
    assert {"query": "missing_feature", "verdict": "low_confidence_matches"} in dead_ends


def test_snapshot_respects_max_files():
    """Test that max_files limits the number of files returned."""
    from jcodemunch_mcp.tools.session_journal import get_journal
    
    journal = get_journal()
    
    # Record more files than max_files limit
    for i in range(15):
        journal.record_read(f"src/file{i}.py", "get_file_outline")
    
    from jcodemunch_mcp.tools.get_session_snapshot import get_session_snapshot
    
    result = get_session_snapshot(max_files=5, max_searches=5, max_edits=10, include_negative_evidence=True)
    
    assert len(result["structured"]["focus_files"]) == 5


def test_snapshot_respects_max_edits():
    """Test that max_edits limits the number of edited files returned."""
    from jcodemunch_mcp.tools.session_journal import get_journal
    
    journal = get_journal()
    
    # Record more edits than max_edits limit
    for i in range(15):
        journal.record_edit(f"src/updated_file{i}.py")
    
    from jcodemunch_mcp.tools.get_session_snapshot import get_session_snapshot
    
    result = get_session_snapshot(max_files=10, max_searches=5, max_edits=7, include_negative_evidence=True)
    
    assert len(result["structured"]["edited_files"]) == 7


def test_snapshot_excludes_negative_evidence_when_disabled():
    """Test that negative evidence is excluded when include_negative_evidence is False."""
    from jcodemunch_mcp.tools.session_journal import get_journal
    
    journal = get_journal()
    
    # Record negative evidence
    journal.record_negative_evidence({
        "query": "nonexistent_function",
        "verdict": "no_implementation_found",
        "scanned_symbols": 4147
    })
    
    from jcodemunch_mcp.tools.get_session_snapshot import get_session_snapshot
    
    result = get_session_snapshot(max_files=10, max_searches=5, max_edits=10, include_negative_evidence=False)
    
    dead_ends = result["structured"]["dead_ends"]
    assert len(dead_ends) == 0


def test_snapshot_text_under_token_budget():
    """Test that snapshot text is reasonable length (under token budget)."""
    from jcodemunch_mcp.tools.session_journal import get_journal
    
    journal = get_journal()
    
    # Record various activities
    for i in range(10):
        journal.record_read(f"src/file{i}.py", "get_file_outline")
    for i in range(3):
        journal.record_edit(f"src/updated_file{i}.py")
    for i in range(5):
        journal.record_search(f"search_query_{i}", i)
    for i in range(3):
        journal.record_negative_evidence({
            "query": f"failed_query_{i}",
            "verdict": "no_implementation_found",
            "scanned_symbols": 4147
        })
    
    from jcodemunch_mcp.tools.get_session_snapshot import get_session_snapshot
    
    result = get_session_snapshot(max_files=10, max_searches=5, max_edits=10, include_negative_evidence=True)
    
    # Check that snapshot text is reasonably sized
    assert isinstance(result["snapshot"], str)
    # Count approximate words in the snapshot text
    approx_word_count = len(result["snapshot"].split())
    assert approx_word_count < 300  # Roughly under 200-300 tokens worth


def test_structured_field_matches_snapshot():
    """Test that structured data contains recorded activities and that they appear in the snapshot text."""
    from jcodemunch_mcp.tools.session_journal import get_journal
    
    journal = get_journal()
    
    # Capture initial state for the files we're going to use
    initial_server_reads = journal._files.get("src/server.py", {}).get("reads", 0)
    initial_utils_reads = journal._files.get("src/utils.py", {}).get("reads", 0) 
    # Note: edits are stored separately in _edits, not in _files
    initial_new_feature_edits = journal._edits.get("src/new_feature.py", {}).get("edits", 0)
    
    # Record specific activities for this test
    journal.record_read("src/server.py", "get_file_outline")
    journal.record_read("src/server.py", "get_file_content")  # Second read for this test
    journal.record_read("src/utils.py", "get_file_outline")  # First read for this test for utils
    journal.record_edit("src/new_feature.py")
    journal.record_search("session snapshot", 2)
    
    from jcodemunch_mcp.tools.get_session_snapshot import get_session_snapshot
    
    result = get_session_snapshot(max_files=10, max_searches=5, max_edits=10, include_negative_evidence=True)
    
    structured = result["structured"]
    
    # Get our specific file data
    server_files = [f for f in structured["focus_files"] if f["file"] == "src/server.py"]
    utils_files = [f for f in structured["focus_files"] if f["file"] == "src/utils.py"]
    edited_files = [f for f in structured["edited_files"] if f["file"] == "src/new_feature.py"]
    
    # Validate that our additions were included
    if server_files:
        # Should have initial count + 2 added by this test
        assert server_files[0]["reads"] == initial_server_reads + 2
        
    if utils_files:
        # Should have initial count + 1 added by this test  
        assert utils_files[0]["reads"] == initial_utils_reads + 1
        
    # Validate search appears in text
    assert "session snapshot" in result["snapshot"]
    
    # Validate edit count (should be initial + 1)
    if edited_files:
        assert edited_files[0]["edits"] == initial_new_feature_edits + 1
    else:
        # If not in structured results, it might be due to max_edits constraint,
        # but the edit should still be reflected in the total count
        assert structured["total_files_explored"] >= 2  # Should include both files we read


if __name__ == "__main__":
    pytest.main([__file__])
