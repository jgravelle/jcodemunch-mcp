"""`get_dead_code_v2` and `menu` say in the reply body when they cut a list (2026-11-27).

`get_dead_code_v2` caps `dead_symbols` at 100, and its only signal of the cut
was `_meta.total_matches` / `_meta.truncated`. A default install strips
`_meta` (`meta_fields: []`), so a capped list of 100 read as complete.
`menu` caps `actions` at `limit` and said only how many it returned.

The property:
- every `get_dead_code_v2` reply that lists candidates carries
  `dead_symbols_total` and `dead_symbols_truncated` beside the list, equal to
  the `_meta` twins, which stay for existing readers;
- `menu` carries `matched_total`, the number of actions the query matched,
  beside `count`, the page.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from jcodemunch_mcp.tools.get_dead_code_v2 import get_dead_code_v2
from jcodemunch_mcp.tools.index_folder import index_folder

REPO = Path(__file__).resolve().parents[1]
DEAD = 8


@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("disc")
    src = tmp / "proj"
    (src / "pkg").mkdir(parents=True)
    (src / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (src / "pkg" / "orphan.py").write_text(
        "".join(f"def unused_{i}():\n    return {i}\n\n\n" for i in range(DEAD)), encoding="utf-8"
    )
    store = tmp / "store"
    r = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert r.get("success"), r
    return r["repo"], str(store)


def _dead(repo, **kw):
    repo_id, store = repo
    out = get_dead_code_v2(repo_id, storage_path=store, **kw)
    assert "error" not in out, out
    return out


def test_a_cut_list_says_so_in_the_body(repo):
    whole = _dead(repo, max_results=0)
    found = len(whole["dead_symbols"])
    assert found >= 3, whole
    out = _dead(repo, max_results=2)
    assert len(out["dead_symbols"]) == 2
    assert out["dead_symbols_total"] == found
    assert out["dead_symbols_truncated"] is True
    assert out["dead_symbols_total"] == out["_meta"]["total_matches"]
    assert out["dead_symbols_truncated"] == out["_meta"]["truncated"]


def test_an_uncut_list_reads_complete(repo):
    out = _dead(repo, max_results=0)
    assert out["dead_symbols_total"] == len(out["dead_symbols"])
    assert out["dead_symbols_truncated"] is False


def test_every_builder_of_a_candidate_list_discloses_the_cut():
    """A reply dict that lists computed candidates names their total and the
    cut; a literal empty list (a refusal) is not a cut."""
    path = REPO / "src" / "jcodemunch_mcp" / "tools" / "get_dead_code_v2.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    builders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = [k.value for k in node.keys if isinstance(k, ast.Constant)]
        if "dead_symbols" not in keys:
            continue
        value = node.values[keys.index("dead_symbols")]
        if isinstance(value, ast.List) and not value.elts:
            continue
        builders.append(node.lineno)
        assert "dead_symbols_total" in keys and "dead_symbols_truncated" in keys, node.lineno
    assert len(builders) >= 2, builders


def _menu(arguments):
    from jcodemunch_mcp import server

    return json.loads(server._handle_menu(arguments)[0].text)


def test_menu_says_how_many_actions_matched():
    out = _menu({"query": "dead code unused function", "limit": 1})
    assert out["count"] == 1
    assert out["matched_total"] > 1
    wide = _menu({"query": "dead code unused function", "limit": 200})
    assert wide["count"] == wide["matched_total"] == out["matched_total"]
    assert wide["actions"][:1] == out["actions"]


def test_menu_with_no_query_matches_the_whole_catalog():
    out = _menu({"limit": 3})
    assert out["count"] == 3
    whole = _menu({"limit": 200})
    assert out["matched_total"] == whole["matched_total"] == whole["count"]


def test_a_refusal_carries_no_total(tmp_path):
    """When no candidate can reach min_confidence the empty list is a refusal
    (`signal_warning`), so no total of 0 is published beside it."""
    src = tmp_path / "proj"
    src.mkdir()
    (src / "a.py").write_text(
        "".join(f"def f{i}():\n    return {i}\n\n\n" for i in range(30)), encoding="utf-8"
    )
    # an import edge, so the three-signal path runs; nothing calls anything
    (src / "b.py").write_text("import a\n\n\ndef g():\n    return 0\n", encoding="utf-8")
    r = index_folder(str(src), use_ai_summaries=False, storage_path=str(tmp_path / "store"))
    assert r.get("success"), r
    out = get_dead_code_v2(r["repo"], storage_path=str(tmp_path / "store"))
    assert "signal_diagnostics" in out["_meta"], out["_meta"]
    ceiling = out["_meta"]["signal_diagnostics"]["max_achievable_confidence"]
    assert "signal_warning" in out and ceiling < 0.5, (ceiling, out.get("signal_warning"))
    assert out["dead_symbols"] == []
    assert "dead_symbols_total" not in out and "dead_symbols_truncated" not in out
