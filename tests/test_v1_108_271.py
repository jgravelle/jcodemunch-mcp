"""#436: get_dead_code_v2 told callers to pass a parameter it did not accept.

Both degradation warnings said "Pass entry_point_patterns". The function had no
such parameter, the MCP schema did not expose one, and the dispatcher forwarded
nothing. The advice fired on the degenerate path, which is precisely where the
caller has been told the answer is untrustworthy and is reaching for a remedy.

The last class here is the general one and matters more than the instance: a
parameter named in a user-facing warning must be a parameter the tool accepts.
Nothing catches that by reading, because the string is correct English about a
real feature belonging to a different tool.
"""
import ast
import inspect
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src" / "jcodemunch_mcp"


def _build_repo(tmp_path, files: dict):
    from jcodemunch_mcp.tools.index_folder import index_folder
    src = tmp_path / "src"
    for rel, body in files.items():
        f = src / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body, encoding="utf-8")
    store = tmp_path / "store"
    store.mkdir()
    r = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    return r["repo"], str(store)


# ---------------------------------------------------------------------------
# The three layers the issue named
# ---------------------------------------------------------------------------

class TestParameterReachable:
    def test_signature_accepts_it(self):
        from jcodemunch_mcp.tools.get_dead_code_v2 import get_dead_code_v2
        assert "entry_point_patterns" in inspect.signature(get_dead_code_v2).parameters

    def test_mcp_schema_exposes_it(self):
        """A parameter absent from the schema is unreachable over the transport
        every real user is on, which was half of #429's lesson too."""
        from jcodemunch_mcp import server
        tools = {t.name: t for t in server._build_tools_list()}
        props = tools["get_dead_code_v2"].input_schema["properties"]
        assert "entry_point_patterns" in props
        assert props["entry_point_patterns"]["type"] == "array"

    def test_dispatcher_forwards_it(self):
        src = (SRC / "server.py").read_text(encoding="utf-8")
        block = src.split('elif name == "get_dead_code_v2":', 1)[1][:1200]
        assert 'entry_point_patterns=arguments.get("entry_point_patterns")' in block


# ---------------------------------------------------------------------------
# It actually does something
# ---------------------------------------------------------------------------

class TestPatternsRescueSignal1:
    def test_declared_root_becomes_an_entry_point(self, tmp_path):
        """A framework root the filename heuristic cannot see."""
        repo, store = _build_repo(tmp_path, {
            "lib/util.py": "def helper():\n    return 1\n",
            # The import is load-bearing: with an EMPTY import graph the tool
            # early-returns in call_graph_only mode, before entry-point logic
            # runs at all, and every assertion here would be vacuous.
            "handlers/lambda_one.py":
                "from lib.util import helper\n\ndef handle(event, ctx):\n    return helper()\n",
        })
        from jcodemunch_mcp.tools.get_dead_code_v2 import get_dead_code_v2

        without = get_dead_code_v2(repo, storage_path=store)
        with_pat = get_dead_code_v2(
            repo, storage_path=store, entry_point_patterns=["handlers/*.py"])

        before = without["_meta"]["signal_diagnostics"]["entry_points_detected"]
        after = with_pat["_meta"]["signal_diagnostics"]["entry_points_detected"]
        assert after > before, (before, after)

    def test_matches_bare_filename_too(self, tmp_path):
        """Same matcher semantics as find_dead_code, which matches either."""
        repo, store = _build_repo(tmp_path, {
            "lib/dep.py": "def dep():\n    return 1\n",
            "deep/nested/entry_thing.py":
                "from lib.dep import dep\n\ndef go():\n    return dep()\n",
        })
        from jcodemunch_mcp.tools.get_dead_code_v2 import get_dead_code_v2
        r = get_dead_code_v2(repo, storage_path=store,
                             entry_point_patterns=["entry_thing.py"])
        assert r["_meta"]["signal_diagnostics"]["entry_points_detected"] >= 1

    def test_shares_one_matcher_with_find_dead_code(self):
        """Two implementations of 'what a pattern means' is #436 in a new costume."""
        from jcodemunch_mcp.tools import get_dead_code_v2 as v2
        from jcodemunch_mcp.tools import find_dead_code as v1
        assert v2._matches_any_pattern is v1._matches_any_pattern

    def test_warning_stops_advising_a_spent_remedy(self, tmp_path):
        """Having taken the advice, the caller must not be given it again."""
        repo, store = _build_repo(tmp_path, {
            "lib/d.py": "def d():\n    return 1\n",
            "handlers/h.py": "from lib.d import d\n\ndef handle():\n    return d()\n",
        })
        from jcodemunch_mcp.tools.get_dead_code_v2 import get_dead_code_v2
        r = get_dead_code_v2(repo, storage_path=store,
                             entry_point_patterns=["handlers/*.py"])
        # Guard against a vacuous pass: in call_graph_only mode no warning is
        # emitted at all and the loop below would assert nothing.
        assert "signal_diagnostics" in r["_meta"], "fixture fell into a degraded mode"
        emitted = [k for k in ("signal_warning", "framework_warning") if r.get(k)]
        for key in emitted:
            assert "Pass entry_point_patterns" not in r[key], (key, r[key])


# ---------------------------------------------------------------------------
# The general rule
# ---------------------------------------------------------------------------

def _tool_modules():
    for path in sorted((SRC / "tools").glob("*.py")):
        if path.name.startswith("_"):
            continue
        yield path


def _advised_params(func_node: ast.FunctionDef) -> set[str]:
    """Parameter names a string in this function tells the caller to pass."""
    import re
    found: set[str] = set()
    for node in ast.walk(func_node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.update(re.findall(r"\bPass\s+([a-z_][a-z0-9_]*)\b", node.value))
    return found


@pytest.mark.parametrize("path", list(_tool_modules()), ids=lambda p: p.name)
def test_advised_parameter_exists_on_the_tool_that_advises_it(path):
    """A warning naming a parameter the tool does not accept is unfollowable.

    This is the #436 class. It survives review because the sentence is true
    English about a real parameter, just one belonging to a different tool.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        params = {a.arg for a in node.args.args} | {a.arg for a in node.args.kwonlyargs}
        for advised in _advised_params(node):
            # Only bind on names that look like OUR parameters somewhere in the
            # tree; prose like "Pass a value" must not trip this.
            if advised in params:
                continue
            if advised in {"the", "a", "an", "in", "it", "this", "that", "your", "them"}:
                continue
            offenders.append(f"{path.name}:{node.name} advises '{advised}'")
    assert not offenders, (
        "tool advises passing a parameter it does not accept: " + "; ".join(offenders)
    )
