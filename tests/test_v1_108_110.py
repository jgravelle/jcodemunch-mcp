"""v1.108.110 — check_embedding_drift is annotation-only non-read-only (suite parity).

jdoc (v1.93.0) and jdata (v1.17.0) mark `check_embedding_drift` as a write tool in
their readOnlyHint annotations because `force=true` re-pins the drift canary. jcm
was the outlier, marking it read-only. This aligns jcm to the siblings: the tool is
now readOnlyHint=False (conservative — it CAN mutate), but it stays OUT of
counter.STATE_CHANGING_ACTIONS so the order() dispatcher doesn't force
allow_state_change on its common drift-report path.
"""

from __future__ import annotations

import jcodemunch_mcp.server as server
from jcodemunch_mcp import counter


def test_check_embedding_drift_marked_mutating():
    by_name = {t.name: t for t in server._build_tools_list()}
    assert "check_embedding_drift" in by_name
    assert by_name["check_embedding_drift"].annotations.read_only_hint is False


def test_check_embedding_drift_not_in_state_changing_actions():
    # Must NOT gate the order() dispatcher: its default path is a pure read, so
    # order("check_embedding_drift") drift reports run without allow_state_change.
    assert "check_embedding_drift" not in counter.STATE_CHANGING_ACTIONS
    assert counter.is_state_changing("check_embedding_drift") is False


def test_order_allows_check_embedding_drift_read_without_optin():
    catalog = server._catalog_names()
    assert "check_embedding_drift" in catalog
    reason = counter.order_gate("check_embedding_drift", catalog, allow_state_change=False)
    assert reason is None


def test_check_embedding_drift_in_annotation_writeset():
    assert "check_embedding_drift" in server._ANNOTATION_ONLY_WRITERS
    assert "check_embedding_drift" in server._NON_READONLY_TOOLS
