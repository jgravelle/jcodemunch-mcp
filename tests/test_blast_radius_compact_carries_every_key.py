"""The compact `get_blast_radius` answer carries every key the tool returns (LEDGER L-58).

A key the schema does not declare is not carried: the encoder drops it with
nothing raised, and a caller of the compact form reads the answer without it.
`cross_repo_confirmed_count` was dropped while the list it counts survived,
and `decisions` (the `include_decisions` git archaeology) was dropped whole.

The guard reads the producer's SOURCE, so a key added to the tool later fails
here until the schema declares it, rather than vanishing from compact answers.
"""

from __future__ import annotations

import ast
import inspect

import pytest

from jcodemunch_mcp.encoding import decoder
from jcodemunch_mcp.encoding.schemas import get_blast_radius as enc
from jcodemunch_mcp.tools import get_blast_radius as producer
from jcodemunch_mcp.tools.get_blast_radius import get_blast_radius
from jcodemunch_mcp.tools.index_folder import index_folder


def _declared() -> set[str]:
    return (
        set(enc._SCALARS)
        | set(enc._JSON)
        | {t.key for t in enc._TABLES}
        | set(enc._NESTED)
    )


def _keys_the_producer_sets() -> set[str]:
    """Every string key the module writes into a dict named `result`."""
    tree = ast.parse(inspect.getsource(producer))
    keys: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id == "result"
                    and isinstance(node.value, ast.Dict)
                ):
                    keys.update(
                        k.value for k in node.value.keys
                        if isinstance(k, ast.Constant) and isinstance(k.value, str)
                    )
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "result"
                    and isinstance(target.slice, ast.Constant)
                    and isinstance(target.slice.value, str)
                ):
                    keys.add(target.slice.value)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "setdefault"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "result"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            keys.add(node.args[0].value)
    return {k for k in keys if not k.startswith("_")}


def test_the_scan_finds_the_keys_it_must_check():
    """Non-vacuity: the scan sees the keys this row is about."""
    keys = _keys_the_producer_sets()
    assert {"cross_repo_confirmed_count", "decisions", "caller_count", "confirmed"} <= keys


def test_every_key_the_tool_sets_is_declared_by_the_schema():
    missing = _keys_the_producer_sets() - _declared()
    assert not missing, (
        f"get_blast_radius sets {sorted(missing)}, which its compact schema "
        "does not declare, so a compact answer drops them"
    )


def test_a_full_response_round_trips():
    """Every optional key at once, encoded and decoded through the registry."""
    response = {
        "repo": "o/r",
        "symbol": {"id": "a.py::f#function", "name": "f", "kind": "function", "file": "a.py", "line": 1},
        "depth": 1,
        "importer_count": 1,
        "direct_dependents_count": 1,
        "overall_risk_score": 0.5,
        "confirmed_count": 1,
        "potential_count": 0,
        "confirmed": [{"file": "b.py", "references": 1, "has_test_reach": False}],
        "potential": [],
        "caller_count": 1,
        "direct_caller_count": 1,
        "callers_by_depth": {"1": 1},
        "callers": [{"id": "b.py::g#function", "depth": 1}],
        "cross_repo_confirmed": [{"repo": "o/other", "file": "c.py"}],
        "cross_repo_confirmed_count": 1,
        "decisions": {"available": False, "reason": "not a git checkout"},
        "_meta": {"timing_ms": 1.0},
    }
    payload, enc_id = enc.encode("get_blast_radius", response)
    assert enc_id == enc.ENCODING_ID
    back = decoder.decode(payload)
    assert back["cross_repo_confirmed_count"] == 1
    assert back["decisions"] == {"available": False, "reason": "not a git checkout"}


@pytest.fixture
def repo(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "utils.py").write_text("def helper():\n    return 42\n", encoding="utf-8")
    (src / "main.py").write_text(
        "from utils import helper\n\ndef run():\n    return helper()\n", encoding="utf-8",
    )
    store = tmp_path / "store"
    out = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert out["success"] is True
    return out["repo"], str(store)


def test_the_tools_decisions_survive_the_compact_form(repo):
    """At the user's entry point: the real tool with include_decisions."""
    rid, store = repo
    res = get_blast_radius(rid, "helper", include_decisions=True, storage_path=store)
    assert "decisions" in res
    payload, _ = enc.encode("get_blast_radius", res)
    assert decoder.decode(payload)["decisions"] == res["decisions"]


def test_the_previous_encoding_id_stays_readable():
    assert enc.ENCODING_ID != "br4"
    assert "br4" in enc.LEGACY_ENCODING_IDS
