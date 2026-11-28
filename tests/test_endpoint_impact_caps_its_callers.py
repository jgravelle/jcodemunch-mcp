"""`get_endpoint_impact` caps each impact's callers list and says so (2026-11-27).

`get_endpoint_impact` calls `get_blast_radius` with `call_depth` 2 by default
and copied its whole `callers` list into every matched impact. Once
`get_blast_radius` gained its cap (L-353), this tool still passed no
`max_results`, so it got the Python default (every caller) and dropped
`callers_truncated` (LEDGER L-354).

The property:
- the MCP tool lists at most `max_results` callers per impact (default 100;
  0 lists all), the same default `get_blast_radius` publishes;
- `caller_count`, `direct_caller_count` and `callers_by_depth` stay the whole
  list's, and `callers_truncated` travels with the list;
- the Python function lists all by default.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from jcodemunch_mcp.tools.get_endpoint_impact import get_endpoint_impact
from jcodemunch_mcp.tools.index_folder import index_folder

REPO = Path(__file__).resolve().parents[1]
N = 30


@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("endpoint_rows")
    src = tmp / "proj"
    src.mkdir()
    (src / "app.py").write_text(
        "from flask import Flask\n"
        "app = Flask(__name__)\n\n"
        "@app.get('/users')\n"
        "def list_users():\n"
        "    return 1\n",
        encoding="utf-8",
    )
    for i in range(N):
        (src / f"user{i:02d}.py").write_text(
            f"from app import list_users\n\n\ndef caller_{i:02d}():\n    return list_users()\n",
            encoding="utf-8",
        )
    store = tmp / "store"
    r = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert r.get("success"), r
    return r["repo"], str(store)


def _impact(repo, **kw):
    repo_id, store = repo
    out = get_endpoint_impact(repo_id, endpoint="GET /users", call_depth=1, storage_path=store, **kw)
    assert "error" not in out, out
    imp = next(i for i in out["impacts"] if i["handler"]["name"] == "list_users")
    return imp


def test_the_cap_cuts_callers_and_keeps_the_counts(repo):
    imp = _impact(repo, max_results=10)
    assert len(imp["callers"]) == 10
    assert imp["caller_count"] == N
    assert imp["direct_caller_count"] == N
    assert imp["callers_by_depth"] == {"1": N}
    assert imp["callers_truncated"] is True


def test_a_list_under_the_cap_reads_complete(repo):
    imp = _impact(repo, max_results=100)
    assert len(imp["callers"]) == N and imp["callers_truncated"] is False


@pytest.mark.parametrize("cap", [0, -4])
def test_zero_or_less_lists_every_caller(repo, cap):
    imp = _impact(repo, max_results=cap)
    assert len(imp["callers"]) == N and imp["callers_truncated"] is False


def test_the_python_default_lists_every_caller(repo):
    imp = _impact(repo)
    assert len(imp["callers"]) == N and imp["callers_truncated"] is False


def test_the_mcp_tool_defaults_to_one_hundred_and_publishes_it():
    from jcodemunch_mcp import server

    tool = next(
        t for t in server._build_tools_list(surface_override="full") if t.name == "get_endpoint_impact"
    )
    prop = tool.inputSchema["properties"]["max_results"]
    assert prop["default"] == 100 and "callers_truncated" in prop["description"]
    tree = ast.parse((REPO / "src" / "jcodemunch_mcp" / "server.py").read_text(encoding="utf-8"))
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and any(isinstance(a, ast.Name) and a.id == "get_endpoint_impact" for a in n.args)
    ]
    assert calls, "the dispatcher never calls get_endpoint_impact"
    for c in calls:
        kw = next((k for k in c.keywords if k.arg == "max_results"), None)
        assert kw is not None, "the dispatcher does not pass max_results"
        assert isinstance(kw.value, ast.Call) and kw.value.args[1].value == 100, ast.dump(kw.value)


def test_the_handler_forwards_the_cap_to_get_blast_radius():
    path = REPO / "src" / "jcodemunch_mcp" / "tools" / "get_endpoint_impact.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "get_blast_radius"
    ]
    assert len(calls) == 1, calls
    kw = next((k for k in calls[0].keywords if k.arg == "max_results"), None)
    assert isinstance(getattr(kw, "value", None), ast.Name) and kw.value.id == "max_results"


def test_no_caller_in_src_but_the_dispatcher_passes_a_cap_to_get_endpoint_impact():
    """The guard one hop up: a cap set anywhere else would bypass the MCP default."""
    stray = []
    for path in (REPO / "src").rglob("*.py"):
        if path.name == "server.py" and path.parent.name == "jcodemunch_mcp":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            name = getattr(n.func, "id", None) or getattr(n.func, "attr", None)
            if name == "get_endpoint_impact" and (
                len(n.args) > 6 or any(k.arg == "max_results" for k in n.keywords)
            ):
                stray.append((str(path.relative_to(REPO)), n.lineno))
            if name == "_impact_for_handler":
                kw = next((k for k in n.keywords if k.arg == "max_results"), None)
                if not (isinstance(getattr(kw, "value", None), ast.Name) and kw.value.id == "max_results"):
                    stray.append((str(path.relative_to(REPO)), n.lineno))
    assert stray == [], stray


def test_the_cap_is_not_rebound_inside_the_tool():
    """The forwarded `max_results` must be the caller's value, never reassigned."""
    path = REPO / "src" / "jcodemunch_mcp" / "tools" / "get_endpoint_impact.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    rebinds = [
        n.lineno for n in ast.walk(tree)
        if isinstance(n, (ast.Assign, ast.AugAssign, ast.AnnAssign))
        and any(
            isinstance(t, ast.Name) and t.id == "max_results"
            for t in (n.targets if isinstance(n, ast.Assign) else [n.target])
        )
    ]
    assert rebinds == [], rebinds
