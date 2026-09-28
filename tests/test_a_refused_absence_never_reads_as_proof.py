"""A scan whose absence claim is refused never tells the caller absence is proven (#872).

Found in review of #719's fix. Two paths, one defect: the refusal is decided in
the dispatcher (`handoff.absence_refusal`), after `build_verdict` has already
written the verdict's prose.

- Fresh and stale: the index was committed past, so the refusal says "the index
  was stale at query time", while the `absent` note beside it still says
  "Treat this as strong evidence the target is not present; do not reformulate
  the same query expecting a hit" -- telling the agent not to search again in
  exactly the case a re-index would change the answer.
- Cached and stale: a cached `absent` replayed after the subject moved is
  downgraded by `revalidate_verdict`, which set neither `state == "absent"` nor
  `absence_refused`, so the dispatcher attached no carrier. With `meta_fields`
  unset the response carried no reason; with `meta_fields: []` (the default) no
  `_meta` at all.

Both run through `server.call_tool` over a real git checkout, because the
refusal and the carrier live in the dispatcher.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from jcodemunch_mcp.retrieval.verdict import _NOTES, STATE_ABSENT

QUERY = "qxzvvwyjkkbb_872"


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(
        ["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True,
        encoding="utf-8", stdin=subprocess.DEVNULL,
    )


def _content(res):
    from mcp.types import CallToolResult

    return res.content if isinstance(res, CallToolResult) else res


def _meta_fields(monkeypatch, value):
    from jcodemunch_mcp import config as _config

    real = _config.get

    def _get(key, default=None, **kw):
        if key == "meta_fields":
            return value
        return real(key, default, **kw)

    monkeypatch.setattr(_config, "get", _get)


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    """A git repo indexed at c0; the caller commits past it when it wants staleness."""
    from jcodemunch_mcp.tools.index_folder import index_folder
    from jcodemunch_mcp.tools.search_symbols import _result_cache

    root = tmp_path / "repo"
    (root / "app").mkdir(parents=True)
    (root / "app" / "__init__.py").write_text("", encoding="utf-8")
    (root / "app" / "engine.py").write_text("def run():\n    return 0\n", encoding="utf-8")
    _git(["init", "-q"], root)
    _git(["config", "user.email", "t@t"], root)
    _git(["config", "user.name", "t"], root)
    _git(["add", "-A"], root)
    _git(["commit", "-qm", "c0"], root)
    store = tmp_path / "store"
    monkeypatch.setenv("CODE_INDEX_PATH", str(store))
    res = index_folder(str(root), use_ai_summaries=False, storage_path=str(store), identity_mode="local")
    _result_cache.clear()

    def commit_past():
        (root / "app" / "later.py").write_text("def later():\n    return 1\n", encoding="utf-8")
        _git(["add", "-A"], root)
        _git(["commit", "-qm", "c1"], root)

    return {"repo": res["repo"], "commit_past": commit_past}


async def _search(repo: str, fmt: str = "json") -> dict:
    from jcodemunch_mcp import server

    out = _content(await server.call_tool("search_symbols", {"repo": repo, "query": QUERY, "format": fmt}))
    return json.loads(out[0].text)


@pytest.mark.asyncio
async def test_a_stale_scan_whose_absence_is_refused_does_not_call_itself_strong_evidence(checkout, monkeypatch):
    """The reported shape: `absent`, refused for staleness, with the proof note beside it."""
    _meta_fields(monkeypatch, ["verdict"])
    checkout["commit_past"]()

    v = (await _search(checkout["repo"]))["_meta"]["verdict"]
    assert v["absence_citable"] is False
    assert "stale" in v["absence_blocked_by"], "precondition: the refusal is the staleness one"
    assert v["note"] != _NOTES[STATE_ABSENT], (
        "the note still says the absence is strong evidence while the refusal beside it says it is not"
    )
    assert "strong evidence" not in v["note"]
    assert "do not reformulate" not in v["note"].lower()
    assert v["absence_blocked_by"] in v["note"], "the note names the refusal"


@pytest.mark.asyncio
async def test_a_fresh_absent_scan_keeps_the_proof_note(checkout, monkeypatch):
    """Control: a scan that CAN prove absence keeps saying so."""
    _meta_fields(monkeypatch, ["verdict"])

    v = (await _search(checkout["repo"]))["_meta"]["verdict"]
    assert v["state"] == "absent"
    assert v["evidence_ref"].startswith("absent:")
    assert v["note"] == _NOTES[STATE_ABSENT]


@pytest.mark.asyncio
async def test_a_cached_scan_replayed_after_a_commit_says_why_it_is_refused(checkout, monkeypatch):
    """The second path, with the verdict visible: a reason must arrive."""
    _meta_fields(monkeypatch, None)
    first = await _search(checkout["repo"])
    assert first["_meta"]["verdict"]["state"] == "absent", "precondition: cached as absent"
    checkout["commit_past"]()

    second = await _search(checkout["repo"])
    assert second["_meta"].get("cache_hit") is True, "precondition: replayed from cache"
    v = second["_meta"]["verdict"]
    assert v["state"] == "degraded"
    assert "evidence_ref" not in v
    assert v.get("absence_citable") is False
    assert "replayed from cache" in (v.get("absence_blocked_by") or "")


@pytest.mark.parametrize("fmt", ["json", "compact"])
@pytest.mark.asyncio
async def test_a_cached_scan_replayed_after_a_commit_carries_its_refusal_on_a_default_install(
    checkout, monkeypatch, fmt
):
    """`meta_fields: []` is the shipped default: the carrier is all a caller gets."""
    from jcodemunch_mcp import server

    _meta_fields(monkeypatch, [])
    args = {"repo": checkout["repo"], "query": QUERY, "format": fmt}
    await server.call_tool("search_symbols", args)
    checkout["commit_past"]()

    out = _content(await server.call_tool("search_symbols", args))[0].text
    assert "absence_evidence" in out, "a refused cached scan reached a default install with no reason"
    assert "replayed from cache" in out


@pytest.mark.asyncio
async def test_a_scan_with_ignored_arguments_carries_its_refusal_on_a_default_install(checkout, monkeypatch):
    """Review: `_arg_contract` was a second downgrade that set no `absence_refused`."""
    from jcodemunch_mcp import server

    _meta_fields(monkeypatch, [])
    args = {"repo": checkout["repo"], "query": QUERY, "format": "json", "bogus_arg": 1}
    out = json.loads(_content(await server.call_tool("search_symbols", args))[0].text)
    carrier = out.get("_meta", {}).get("absence_evidence")
    assert carrier, "a refused scan with ignored arguments reached a default install with no reason"
    assert carrier["citable"] is False
    assert "bogus_arg" in carrier["blocked_by"], "the refusal names the ignored argument"


def _degraded_writes(root: Path) -> list[str]:
    """Every `x["state"] = <degraded>` outside `refuse_absence`."""
    import ast

    bad = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        allowed: set[int] = set()
        for fn in ast.walk(tree):
            if isinstance(fn, ast.FunctionDef) and fn.name == "refuse_absence":
                allowed |= {n.lineno for n in ast.walk(fn) if hasattr(n, "lineno")}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign) or node.lineno in allowed:
                continue
            val = node.value
            degraded = (isinstance(val, ast.Constant) and val.value == "degraded") or (
                isinstance(val, ast.Name) and val.id == "STATE_DEGRADED"
            )
            for t in node.targets:
                if (
                    degraded
                    and isinstance(t, ast.Subscript)
                    and isinstance(t.slice, ast.Constant)
                    and t.slice.value == "state"
                ):
                    bad.append(f"{path.relative_to(root).as_posix()}:{node.lineno}")
    return bad


def test_every_absence_downgrade_goes_through_refuse_absence():
    """One layer down: a third downgrade inherits the flag, or this fails."""
    src = Path(__file__).resolve().parents[1] / "src" / "jcodemunch_mcp"
    assert _degraded_writes(src) == []


def test_the_downgrade_scan_sees_a_reintroduced_write(tmp_path):
    (tmp_path / "m.py").write_text(
        'STATE_DEGRADED = "degraded"\n'
        "def f(v):\n"
        '    v["state"] = "degraded"\n'
        '    v["state"] = STATE_DEGRADED\n'
        "def refuse_absence(v):\n"
        '    v["state"] = STATE_DEGRADED\n',
        encoding="utf-8",
    )
    assert _degraded_writes(tmp_path) == ["m.py:3", "m.py:4"]
