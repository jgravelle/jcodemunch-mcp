"""A warning in a tool's answer survives compact encoding.

``search_symbols`` answers a weak match with ``⚠ warning`` ("Low-confidence
matches ... Verify before claiming this feature exists") and
``negative_evidence``. Neither was declared in its compact schema, and the
schema encoder drops what a schema does not declare, so on the default
``server_output: adaptive``, which picks the compact form whenever it is
smaller, a low-confidence answer with ten rows reached the agent as ten rows
and no warning. The guard against claiming a feature exists was lost exactly
when there were results to mislead.

The encoder now carries every top-level key whose name contains "warning", and
``negative_evidence``, for every schema, the way ``UNIVERSAL_META_JSON`` carries
the verdict inside ``_meta``.
"""

from __future__ import annotations

import pathlib

import pytest

from jcodemunch_mcp.encoding import decoder, encode_response
from jcodemunch_mcp.encoding.schemas import registry
from jcodemunch_mcp.tools.index_folder import index_folder
from jcodemunch_mcp.tools.search_symbols import search_symbols


@pytest.fixture
def weak_match_repo(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    (root / "a.py").write_text(
        "".join(
            f"def handler_{i}(x):\n    '''Process the payment batch {i}.'''\n    return x\n"
            for i in range(40)
        ),
        encoding="utf-8",
    )
    store = tmp_path / "store"
    out = index_folder(
        str(root),
        use_ai_summaries=False,
        storage_path=str(store),
        identity_mode="local",
    )
    assert out.get("success"), out
    return out["repo"], str(store)


def _warning_keys(d):
    return sorted(k for k in d if "warning" in k.lower())


def test_a_low_confidence_answer_keeps_its_warning_in_the_compact_form(weak_match_repo):
    rid, store = weak_match_repo
    raw = search_symbols(
        repo=rid,
        query="payment reconciliation ledger",
        max_results=10,
        storage_path=store,
    )
    assert (
        raw["results"] and _warning_keys(raw) and "negative_evidence" in raw
    )  # non-vacuity
    payload, meta = encode_response("search_symbols", raw, requested_format="compact")
    assert isinstance(payload, str), meta  # the compact form really was chosen
    back = decoder.decode(payload)
    assert _warning_keys(back) == _warning_keys(raw)
    for k in _warning_keys(raw):
        assert back[k] == raw[k]
    assert back["negative_evidence"] == raw["negative_evidence"]


def _schema_modules():
    registry._discover()  # noqa: SLF001 - the registry is the authority on which encoders exist
    return sorted({m for m in registry._BY_TOOL.values()}, key=lambda m: m.__name__)  # noqa: SLF001


@pytest.mark.parametrize(
    "key,value",
    [
        ("warning", "3 of 5 patterns matched no file"),
        (
            "⚠ warning",
            "Low-confidence matches. Verify before claiming this feature exists.",
        ),
        ("low_confidence_warning", "weak"),
        (
            "negative_evidence",
            {"verdict": "low_confidence_matches", "best_match_score": 0.4},
        ),
    ],
)
def test_every_compact_encoder_carries_contract_keys(key, value):
    """Not a per-schema list: a schema written next inherits the rule."""
    modules = _schema_modules()
    assert len(modules) > 10  # non-vacuity: the registry really loaded
    dropped, encoded = [], 0
    for mod in modules:
        tool = mod.TOOLS[0]
        try:
            payload, _ = mod.encode(tool, {key: value})
        except Exception:
            continue  # an encoder that refuses falls back to JSON, which keeps the key
        encoded += 1
        back = decoder.decode(payload)
        if back.get(key) != value:
            dropped.append(tool)
    assert encoded > 10, encoded  # non-vacuity: the encoders really ran
    assert not dropped, f"{key!r} dropped by: {dropped}"


# --- shapes a schema reshapes before encoding ------------------------------------

CONTRACT = {
    "budget_warning": "Token budget 80% used.",
    "⚠ warning": "Verify before claiming.",
}


def _round_trip(tool, response):
    from jcodemunch_mcp.encoding.schemas import registry as reg

    reg._discover()  # noqa: SLF001
    mod = reg.for_tool(tool)
    assert mod is not None, tool
    payload, _ = mod.encode(tool, response)
    return decoder.decode(payload)


def test_a_batch_file_outline_keeps_a_warning(weak_match_repo, tmp_path):
    """The batch outline rebuilds the response before encoding (its flatten)."""
    from jcodemunch_mcp.tools.get_file_outline import get_file_outline

    rid, store = weak_match_repo
    res = get_file_outline(repo=rid, file_paths=["a.py", "a.py"], storage_path=store)
    assert "results" in res, res  # non-vacuity: the batch shape
    back = _round_trip("get_file_outline", {**res, **CONTRACT})
    for k, v in CONTRACT.items():
        assert back.get(k) == v, (k, sorted(back))


def test_search_text_and_find_references_keep_a_warning(weak_match_repo):
    from jcodemunch_mcp.tools.find_references import find_references
    from jcodemunch_mcp.tools.search_text import search_text

    rid, store = weak_match_repo
    st = search_text(repo=rid, query="payment", storage_path=store)
    assert st["results"], st
    fr = find_references(repo=rid, identifier="handler_1", storage_path=store)
    for tool, res in (("search_text", st), ("find_references", fr)):
        back = _round_trip(tool, {**res, **CONTRACT})
        for k, v in CONTRACT.items():
            assert back.get(k) == v, (tool, k, sorted(back))


def test_a_compact_form_that_loses_a_warning_falls_back_to_json(monkeypatch):
    """Whatever a future schema does, the response the agent gets keeps it."""
    from jcodemunch_mcp.encoding import schema_driven as sd
    from jcodemunch_mcp.encoding.schemas import registry as reg

    class Lossy:
        TOOLS = ("lossy_tool",)

        @staticmethod
        def encode(tool, response):
            return sd.encode(
                tool,
                {"result_count": response["result_count"]},
                "lz1",
                scalars=("result_count",),
            ), "lz1"

    monkeypatch.setattr(
        reg, "for_tool", lambda name: Lossy if name == "lossy_tool" else None
    )
    response = {"result_count": 3, "⚠ warning": "Verify before claiming."}
    payload, meta = encode_response("lossy_tool", response, requested_format="compact")
    assert (
        meta["encoding"] == "json" and payload["⚠ warning"] == "Verify before claiming."
    )
    # Non-vacuity: without the warning, the same lossy encoder is used.
    payload, meta = encode_response(
        "lossy_tool", {"result_count": 3}, requested_format="compact"
    )
    assert meta["encoding"] == "lz1"
