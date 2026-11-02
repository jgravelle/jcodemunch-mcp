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


def test_the_old_compact_encoding_id_stays_readable(chain):
    """Encode with the br2 scalars and id, then decode through the registry."""
    from jcodemunch_mcp.encoding import decoder
    from jcodemunch_mcp.encoding import schema_driven as sd
    from jcodemunch_mcp.encoding.schemas import get_blast_radius as enc

    assert enc.ENCODING_ID != "br2"
    assert "br2" in enc.LEGACY_ENCODING_IDS
    br2_scalars = tuple(s for s in enc._SCALARS if s not in ("caller_count", "direct_caller_count"))
    br2_json = tuple(j for j in enc._JSON if j != "callers_by_depth")
    rid, store = chain
    res = get_blast_radius(rid, "helper", call_depth=3, storage_path=store)
    payload, _ = sd.encode(
        "get_blast_radius", res, "br2", enc._TABLES, br2_scalars,
        nested_dicts=enc._NESTED, meta_keys=enc._META,
        meta_json_blobs=enc._META_JSON, json_blobs=br2_json,
    )
    back = decoder.decode(payload)
    assert back["symbol"]["name"] == "helper"
    assert len(back["callers"]) == 4


def test_endpoint_impact_carries_the_direct_count(tmp_path):
    """`get_endpoint_impact` republishes the callers at a default depth of 2."""
    from jcodemunch_mcp.tools.get_endpoint_impact import get_endpoint_impact

    app = tmp_path / "app"
    app.mkdir()
    (app / "views.py").write_text(
        "from flask import Flask\napp = Flask(__name__)\n\n"
        "@app.route('/users')\ndef list_users():\n    return []\n",
        encoding="utf-8",
    )
    (app / "jobs.py").write_text(
        "from views import list_users\n\ndef trigger():\n    return list_users()\n\n"
        "def nightly():\n    return trigger()\n",
        encoding="utf-8",
    )
    store = tmp_path / "store"
    out = index_folder(str(app), use_ai_summaries=False, storage_path=str(store))
    res = get_endpoint_impact(out["repo"], endpoint="GET /users", storage_path=str(store))
    imp = next(i for i in res["impacts"] if i["handler"]["name"] == "list_users")
    assert imp["caller_count"] == 2, imp
    assert imp["direct_caller_count"] == 1
    assert imp["callers_by_depth"] == {"1": 1, "2": 1}
