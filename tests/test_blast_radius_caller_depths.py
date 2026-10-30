"""`get_blast_radius` says which of its callers are direct (LEDGER L-240).

With `call_depth` above 1, every caller of every depth sat under one
`caller_count`, so a reader taking the count at its word read indirect callers
as direct ones. `get_call_hierarchy` fixed the same shape with a direct count
and a per-depth count; `get_blast_radius` now reports both, computed by the
same function, and its compact encoding carries them (it also never carried
`caller_count`).
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.tools.get_blast_radius import get_blast_radius
from jcodemunch_mcp.tools.index_folder import index_folder


@pytest.fixture
def chain(tmp_path):
    """helper <- process <- handle <- run, plus a second direct caller of helper."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "utils.py").write_text("def helper():\n    return 42\n", encoding="utf-8")
    (src / "services.py").write_text(
        "from utils import helper\n\ndef process():\n    return helper() + 1\n\n"
        "def audit():\n    return helper()\n",
        encoding="utf-8",
    )
    (src / "controllers.py").write_text(
        "from services import process\n\ndef handle(req):\n    return process()\n",
        encoding="utf-8",
    )
    (src / "main.py").write_text(
        "from controllers import handle\n\ndef run():\n    return handle(None)\n",
        encoding="utf-8",
    )
    store = tmp_path / "store"
    out = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert out["success"] is True
    return out["repo"], str(store)


def test_deep_callers_are_counted_apart_from_direct_ones(chain):
    rid, store = chain
    res = get_blast_radius(rid, "helper", call_depth=3, storage_path=store)
    assert res["caller_count"] == 4  # the total, unchanged
    assert res["direct_caller_count"] == 2
    assert res["callers_by_depth"] == {"1": 2, "2": 1, "3": 1}


def test_callers_are_ordered_by_depth(chain):
    rid, store = chain
    res = get_blast_radius(rid, "helper", call_depth=3, storage_path=store)
    depths = [r["depth"] for r in res["callers"]]
    assert depths == sorted(depths), depths


def test_depth_one_has_only_direct_callers(chain):
    rid, store = chain
    res = get_blast_radius(rid, "helper", call_depth=1, storage_path=store)
    assert res["caller_count"] == res["direct_caller_count"] == 2
    assert res["callers_by_depth"] == {"1": 2}


def test_no_counts_without_a_call_walk(chain):
    rid, store = chain
    res = get_blast_radius(rid, "helper", storage_path=store)
    assert "direct_caller_count" not in res and "callers_by_depth" not in res


def test_one_rule_counts_depths_for_both_tools():
    """Neither tool carries its own copy of the depth rule."""
    import inspect

    from jcodemunch_mcp.tools import get_blast_radius as br, get_call_hierarchy as ch

    for mod in (br, ch):
        assert "label_row_depths" in inspect.getsource(mod), mod.__name__
        assert "def depth_of" not in inspect.getsource(mod), mod.__name__


def test_the_compact_encoding_carries_the_counts(chain):
    from jcodemunch_mcp.encoding import decoder
    from jcodemunch_mcp.encoding.schemas import get_blast_radius as enc

    rid, store = chain
    res = get_blast_radius(rid, "helper", call_depth=3, storage_path=store)
    payload, enc_id = enc.encode("get_blast_radius", res)
    assert enc_id == enc.ENCODING_ID
    back = decoder.decode(payload)
    assert back["caller_count"] == 4
    assert back["direct_caller_count"] == 2
    assert back["callers_by_depth"] == {"1": 2, "2": 1, "3": 1}


def test_the_old_compact_encoding_id_stays_readable():
    from jcodemunch_mcp.encoding.schemas import get_blast_radius as enc

    assert enc.ENCODING_ID != "br2"
    assert "br2" in enc.LEGACY_ENCODING_IDS
