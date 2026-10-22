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
    dropped = []
    for mod in modules:
        tool = mod.TOOLS[0]
        try:
            payload, _ = mod.encode(tool, {key: value})
        except Exception:
            continue  # an encoder that refuses falls back to JSON, which keeps the key
        back = decoder.decode(payload)
        if back.get(key) != value:
            dropped.append(tool)
    assert not dropped, f"{key!r} dropped by: {dropped}"
