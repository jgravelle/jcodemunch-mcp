"""v1.108.162 — server-owned canonical handoff contract (#374).

finalize_handoff (tool) assembles a deterministic Markdown handoff from
caller-authored sections, attests evidence_refs against the session's
retrieval record (yield tracker served ids), persists it session-scoped, and
returns a compact receipt; munch://handoff/<id> (resource) serves the
immutable body. The server never authors analysis.

Test models per the issue's own citations: result shapes follow
test_v1_108_74.py; resource behavior follows test_v1_108_152.py; Counter
example parity is enforced by test_counter.py's live-schema validation.
"""

import asyncio
import hashlib
import json

import pytest

from jcodemunch_mcp import handoff
from jcodemunch_mcp.storage import token_tracker


SERVED = ["src/auth.py::login#function", "src/auth.py::Session#class",
          "src/billing.py::charge#function"]


def _finalize(**over):
    args = dict(
        repo="owner/name",
        task="Audit the auth surface",
        sections=[{"heading": "Findings", "content": "Auth is sound."}],
        evidence_refs=["src/auth.py::login#function"],
        served_ids=SERVED,
    )
    args.update(over)
    return handoff.finalize_handoff(**args)


@pytest.fixture(autouse=True)
def _clean():
    handoff.clear_handoffs()
    yield
    handoff.clear_handoffs()


class TestFinalize:
    def test_receipt_shape(self):
        r = _finalize()
        assert r["schema"] == "jcodemunch.handoff/v1"
        assert r["canonical"] is True
        assert r["content_type"] == "text/markdown"
        assert r["resource_uri"] == f"munch://handoff/{r['handoff_id']}"
        assert r["evidence_attested"] is True
        assert len(r["sha256"]) == 64
        assert r["length"] > 0

    def test_deterministic_same_inputs(self):
        a, b = _finalize(), _finalize()
        assert a["handoff_id"] == b["handoff_id"]
        assert a["sha256"] == b["sha256"]

    def test_different_inputs_different_id(self):
        a = _finalize()
        b = _finalize(task="Audit the billing surface")
        assert a["handoff_id"] != b["handoff_id"]

    def test_sha256_matches_body(self):
        r = _finalize()
        body = handoff.get_handoff(r["handoff_id"])["body"]
        assert hashlib.sha256(body.encode("utf-8")).hexdigest() == r["sha256"]
        assert len(body.encode("utf-8")) == r["length"]

    def test_file_level_ref_attests(self):
        r = _finalize(evidence_refs=["src/auth.py"])
        assert r["evidence_attested"] is True

    def test_unknown_ref_fails_closed(self):
        r = _finalize(evidence_refs=["src/ghost.py::nope#function"])
        assert "error" in r
        assert r["unknown_refs"] == ["src/ghost.py::nope#function"]

    def test_empty_evidence_rejected(self):
        assert "error" in _finalize(evidence_refs=[])

    def test_empty_sections_rejected(self):
        assert "error" in _finalize(sections=[])
        assert "error" in _finalize(sections=[{"heading": "H", "content": "  "}])

    def test_duplicate_appendix_rejected(self):
        r = _finalize(appendices=[
            {"name": "Report", "content": "a"},
            {"name": "Report", "content": "b"},
        ])
        assert "error" in r and "Report" in r["error"]

    def test_appendix_exactly_once(self):
        r = _finalize(appendices=[{"name": "CodeBurn report", "content": "raw"}])
        body = handoff.get_handoff(r["handoff_id"])["body"]
        assert body.count("## Appendix: CodeBurn report") == 1
        assert r["appendices"] == ["CodeBurn report"]

    def test_no_char_limit(self):
        big = "x" * 500_000
        r = _finalize(sections=[{"heading": "Big", "content": big}])
        assert "error" not in r
        assert big in handoff.get_handoff(r["handoff_id"])["body"]

    def test_evidence_refs_deduped_in_order(self):
        r = _finalize(evidence_refs=[
            "src/auth.py::login#function",
            "src/auth.py::login#function",
            "src/billing.py::charge#function",
        ])
        assert r["evidence_count"] == 2


class TestSessionRecord:
    def test_tracker_served_ids_are_the_record(self):
        token_tracker.note_served(["tests/x.py::probe_374#function"])
        assert "tests/x.py::probe_374#function" in token_tracker.served_symbol_ids()
        r = _finalize(
            evidence_refs=["tests/x.py::probe_374#function"],
            served_ids=token_tracker.served_symbol_ids(),
        )
        assert r["evidence_attested"] is True


class TestResource:
    def _receipt(self):
        return _finalize()

    def test_repeated_reads_byte_identical(self):
        r = self._receipt()
        from jcodemunch_mcp import server
        a = asyncio.run(server.read_resource(r["resource_uri"]))
        b = asyncio.run(server.read_resource(r["resource_uri"]))
        assert a[0].content == b[0].content
        assert a[0].mime_type == "text/markdown"
        assert hashlib.sha256(a[0].content.encode("utf-8")).hexdigest() == r["sha256"]

    def test_advertised_in_list_resources(self):
        r = self._receipt()
        from jcodemunch_mcp import server
        uris = [str(x.uri) for x in asyncio.run(server.list_resources())]
        assert r["resource_uri"] in uris
        assert "munch://runtime/identity" in uris  # #371 resource unaffected

    def test_unknown_id_raises(self):
        from jcodemunch_mcp import server
        with pytest.raises(ValueError):
            asyncio.run(server.read_resource("munch://handoff/0000000000000000"))

    def test_session_isolation_cleanup(self):
        r = self._receipt()
        handoff.clear_handoffs()  # new session == fresh process state
        assert handoff.get_handoff(r["handoff_id"]) is None


class TestServerDispatch:
    ARGS = {
        "repo": "owner/name",
        "task": "Audit",
        "sections": [{"heading": "H", "content": "C"}],
        "evidence_refs": ["src/dispatch_probe.py::run#function"],
    }

    def test_success_is_plain_content_list(self):
        token_tracker.note_served(["src/dispatch_probe.py::run#function"])
        from jcodemunch_mcp import server
        res = asyncio.run(server.call_tool("finalize_handoff", dict(self.ARGS)))
        assert isinstance(res, list)
        receipt = json.loads(res[0].text)
        assert receipt["schema"] == "jcodemunch.handoff/v1"

    def test_invalid_finalization_is_error_result(self):
        from jcodemunch_mcp import server
        from mcp.types import CallToolResult
        bad = dict(self.ARGS, evidence_refs=["never::served#function"])
        res = asyncio.run(server.call_tool("finalize_handoff", bad))
        assert isinstance(res, CallToolResult) and res.is_error
        body = json.loads(res.content[0].text)
        assert body["unknown_refs"] == ["never::served#function"]


class TestRegistration:
    def test_all_registration_surfaces(self):
        from jcodemunch_mcp import server
        assert "finalize_handoff" in server._CANONICAL_TOOL_NAMES
        cats = dict(server._SNIPPET_TOOL_CATEGORIES)
        assert "finalize_handoff" in cats["Session-Aware Routing"]
        tools = {t.name: t for t in server._build_tools_list()}
        assert "finalize_handoff" in tools

    def test_counter_gate_and_example(self):
        from jcodemunch_mcp import counter
        assert counter.is_state_changing("finalize_handoff")
        assert "finalize_handoff" in counter.EXAMPLES
        # order() without opt-in refuses (state-changing)
        names = ["finalize_handoff"]
        assert counter.order_gate("finalize_handoff", names, allow_state_change=False)
        assert counter.order_gate("finalize_handoff", names, allow_state_change=True) is None

    def test_read_only_hint_false(self):
        from jcodemunch_mcp import server
        tools = server._build_tools_list()
        t = next(t for t in tools if t.name == "finalize_handoff")
        assert t.annotations is not None and t.annotations.read_only_hint is False
