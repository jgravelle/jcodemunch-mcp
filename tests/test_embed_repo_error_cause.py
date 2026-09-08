"""`embed_repo` reports WHY a batch failed, not only how many symbols it skipped.

A provider failure (a bad key, a network outage, a model the endpoint does
not serve) reached the caller as `symbols_skipped_error: N` with the cause
written to the log only, so every failure read the same and a run in which
every batch failed came back in the success shape. Named by a competitor's
fix title (`surface embedding failures and avoid redundant retries`, zvec-grep
#81; docs/competitive/FINDINGS.md CF-66): the half that did apply here.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from jcodemunch_mcp.parser.symbols import Symbol
from jcodemunch_mcp.storage import IndexStore
from jcodemunch_mcp.tools.embed_repo import embed_repo


@pytest.fixture(autouse=True)
def _no_local_onnx():
    with patch(
        "jcodemunch_mcp.embeddings.local_encoder.is_model_available", return_value=False
    ), patch(
        "jcodemunch_mcp.embeddings.local_encoder.is_onnxruntime_available",
        return_value=False,
    ):
        yield


def _sym(id_, name):
    return Symbol(
        id=id_, file="src/a.py", name=name, qualified_name=name, kind="function",
        language="python", signature=f"def {name}():", byte_offset=0, byte_length=50,
        summary="",
    )


def _seed(tmp_path, n=3):
    symbols = [_sym(f"s{i}", f"fn_{i}") for i in range(n)]
    store = IndexStore(base_path=str(tmp_path))
    store.save_index(
        owner="test", name="cause", source_files=["src/a.py"], symbols=symbols,
        raw_files={"src/a.py": "x = 1\n"}, languages={"python": 1},
        file_languages={"src/a.py": "python"},
    )
    return "test/cause"


def _run(tmp_path, monkeypatch, side_effect, **kw):
    monkeypatch.setenv("JCODEMUNCH_EMBED_MODEL", "all-MiniLM-L6-v2")
    repo = _seed(tmp_path)
    with patch("jcodemunch_mcp.tools.embed_repo.embed_texts", side_effect=side_effect):
        return embed_repo(repo, storage_path=str(tmp_path), **kw)


def test_a_failed_batch_names_its_cause(tmp_path, monkeypatch):
    result = _run(tmp_path, monkeypatch, RuntimeError("401 Unauthorized: invalid api key"))
    assert result["symbols_skipped_error"] == 3
    assert result["symbols_embedded"] == 0
    causes = result["error_causes"]
    assert len(causes) == 1
    assert causes[0]["type"] == "RuntimeError"
    assert "401 Unauthorized" in causes[0]["message"]
    assert causes[0]["batches"] == 1
    # Every batch failed: the caller is told so in the response, not the log.
    assert result["all_batches_failed"] is True


def test_distinct_causes_are_listed_once_each_with_a_batch_count(tmp_path, monkeypatch):
    calls = iter([RuntimeError("timeout"), ValueError("bad model"), RuntimeError("timeout")])

    def _raise(texts, provider, model, task_type=None):
        raise next(calls)

    result = _run(tmp_path, monkeypatch, _raise, batch_size=1)
    causes = {(c["type"], c["message"]): c["batches"] for c in result["error_causes"]}
    assert causes == {("RuntimeError", "timeout"): 2, ("ValueError", "bad model"): 1}


def test_a_partial_failure_is_not_all_batches_failed(tmp_path, monkeypatch):
    calls = iter([RuntimeError("timeout"), None, None])

    def _one_bad(texts, provider, model, task_type=None):
        exc = next(calls)
        if exc:
            raise exc
        return [[1.0, 0.0] for _ in texts]

    result = _run(tmp_path, monkeypatch, _one_bad, batch_size=1)
    assert result["symbols_embedded"] == 2
    assert result["symbols_skipped_error"] == 1
    assert result["all_batches_failed"] is False
    assert result["error_causes"][0]["batches"] == 1


def test_a_success_carries_no_cause_fields(tmp_path, monkeypatch):
    result = _run(tmp_path, monkeypatch, lambda t, p, m, task_type=None: [[1.0, 0.0] for _ in t])
    assert result["symbols_skipped_error"] == 0
    assert "error_causes" not in result
    assert "all_batches_failed" not in result


def test_a_secret_in_a_provider_message_is_redacted(tmp_path, monkeypatch):
    result = _run(
        tmp_path, monkeypatch,
        RuntimeError("rejected key AKIAIOSFODNN7EXAMPLE for this endpoint"),
    )
    msg = result["error_causes"][0]["message"]
    assert "AKIAIOSFODNN7EXAMPLE" not in msg
    assert "REDACTED" in msg


# ── The second site: search_symbols' lazy top-up (review round 1) ──────────

def test_search_symbols_topup_failure_is_disclosed_in_meta(tmp_path, monkeypatch):
    """A failed top-up batch used to leave its symbols lexical-only in silence."""
    from jcodemunch_mcp.tools.search_symbols import search_symbols

    monkeypatch.setenv("JCODEMUNCH_EMBED_MODEL", "all-MiniLM-L6-v2")
    repo = _seed(tmp_path)

    def _query_ok_symbols_fail(texts, provider, model, task_type=None):
        if len(texts) == 1 and texts[0] == "fn":
            return [[1.0, 0.0]]
        raise RuntimeError("503 Service Unavailable")

    # search_symbols imports embed_texts from embed_repo at call time, so the
    # one patch reaches both the query embedding and the top-up loop.
    with patch("jcodemunch_mcp.tools.embed_repo.embed_texts", side_effect=_query_ok_symbols_fail):
        result = search_symbols(repo, "fn", semantic=True, storage_path=str(tmp_path))

    assert result.get("error") is None, result
    topup = result["_meta"]["semantic_topup"]
    assert topup["symbols_unscored"] == 3
    assert topup["batches_failed"] == 1
    assert topup["error_causes"][0]["type"] == "RuntimeError"
    assert "503" in topup["error_causes"][0]["message"]


def test_search_symbols_clean_topup_has_no_meta_entry(tmp_path, monkeypatch):
    from jcodemunch_mcp.tools.search_symbols import search_symbols

    monkeypatch.setenv("JCODEMUNCH_EMBED_MODEL", "all-MiniLM-L6-v2")
    repo = _seed(tmp_path)
    ok = lambda t, p, m, task_type=None: [[1.0, 0.0] for _ in t]  # noqa: E731
    with patch("jcodemunch_mcp.tools.embed_repo.embed_texts", side_effect=ok):
        result = search_symbols(repo, "fn", semantic=True, storage_path=str(tmp_path))
    assert "semantic_topup" not in result["_meta"]


def test_the_compact_encoder_keeps_semantic_topup():
    """A dict left off _META_JSON is silently dropped by the encoder (v1.108.169)."""
    from jcodemunch_mcp.encoding.schemas import search_symbols as schema

    response = {
        "result_count": 0, "results": [], "query": "q", "repo": "r",
        "_meta": {
            "timing_ms": 1.0,
            "semantic_topup": {
                "symbols_unscored": 3, "batches_failed": 1,
                "error_causes": [{"type": "RuntimeError", "message": "503", "batches": 1}],
            },
        },
    }
    payload, _ = schema.encode("search_symbols", response)
    back = schema.decode(payload)
    assert back["_meta"]["semantic_topup"] == response["_meta"]["semantic_topup"]


# ── The ledger itself ──────────────────────────────────────────────────────

def test_ledger_counts_what_it_cuts():
    from jcodemunch_mcp.embeddings.failures import LIST_MAX, FailureLedger

    ledger = FailureLedger()
    for i in range(LIST_MAX + 3):
        ledger.record(RuntimeError(f"cause {i}"), items=2)
    out: dict = {}
    ledger.disclose(out)
    assert len(out["error_causes"]) == LIST_MAX
    assert out["causes_omitted"] == 3
    assert ledger.batches == LIST_MAX + 3
    assert ledger.items == 2 * (LIST_MAX + 3)


def test_ledger_redacts_before_it_cuts():
    """A secret straddling the length cut must not survive as an unmatchable prefix."""
    from jcodemunch_mcp.embeddings.failures import MESSAGE_CHARS, FailureLedger

    key = "AKIAIOSFODNN7EXAMPLE"
    # A space before the key: the AWS pattern refuses a key glued to an
    # identifier character, which is right. The cut falls inside the key.
    msg = "x" * (MESSAGE_CHARS - 7) + " " + key
    ledger = FailureLedger()
    ledger.record(RuntimeError(msg))
    text = ledger.rows()[0]["message"]
    assert "AKIA" not in text
    # The marker itself would be split by the cut, so the cut moves before it.
    assert "[REDAC" not in text
    assert text.endswith("...")
    assert len(text) <= MESSAGE_CHARS + 3
