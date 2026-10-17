"""`get_dead_code_v2` keeps JSX-consumed components and React lifecycle methods.

Components of a `.tsx` file consumed only as JSX (`<WorkspaceShell />`) were
offered for deletion at confidence 1.0, as were the error-boundary lifecycle
methods `componentDidCatch` and `getDerivedStateFromError`.

A component consumed as `<WorkspaceShell />` holds no `call_expression`, so no
`call_references` row names it, and a .tsx file whose language records calls
anywhere in the repo is not read as text either — the use sits inside another
component's body, the one region every text sweep deliberately excludes. A
React class-component lifecycle method is invoked by the framework, never by
repo code. Both read `no_callers` by construction: live code, offered for
deletion, in a tool whose rows feed delete decisions.

A second defect is here too: `get_repo_health` divided the `dead_symbols`
list — capped at `max_results=100` — by every function/method, so a repo with
more than 100 candidates had its `dead_code_pct` understated.

Every fixture goes through `index_folder` and the tool, on its own store.
"""

from __future__ import annotations

from jcodemunch_mcp.tools import get_dead_code_v2 as v2
from jcodemunch_mcp.tools.get_repo_health import get_repo_health
from jcodemunch_mcp.tools.index_folder import index_folder


def _index(tmp_path, files: dict) -> tuple:
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    store = tmp_path / "store"
    store.mkdir(exist_ok=True)
    for name, body in files.items():
        target = src / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    r = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert r["success"] is True
    return r["repo"], str(store)


def _signals(repo: str, store: str) -> dict:
    out = v2.get_dead_code_v2(
        repo=repo, min_confidence=0.0, max_results=0, storage_path=store
    )
    assert "error" not in out, out
    assert out.get("_meta", {}).get("mode") != "call_graph_only", out.get("_meta")
    rows: dict = {}
    for row in out["dead_symbols"]:
        rows[(row["file"], row["name"])] = row["signals"]
    return rows


# The reported shape: an entry file rendering App, and App.tsx where every
# sibling is consumed only as JSX. ⚠ The `React.useState` call inside App is
# load-bearing: with no call reference anywhere in the .tsx corpus, every file
# is read as text and the JSX mention already counted — it is a recording
# language that makes the file's bodies invisible, which is the reported case.
APP_TSX = """import React from 'react'

function WelcomeScreen() { return <p>welcome</p> }

function WorkspaceFallback() { return <p>loading</p> }

function WorkspaceShell() {
  return (
    <div>
      <WelcomeScreen title="welcome" />
      <WorkspaceFallback pending={true} />
    </div>
  )
}

class Boundary extends React.Component {
  static getDerivedStateFromError() { return { hasError: true } }
  componentDidCatch() { /* report */ }
  render() { return this.props.children }
  helperNothingCalls() { return 3 }
}

export default function App() {
  const [n] = React.useState(0)
  return <Boundary><WorkspaceShell /></Boundary>
}

function trulyUnusedHelper(): number { return 1 }
"""

MAIN_TSX = """import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
console.log('boot')
createRoot(document.getElementById('root')!).render(<App />)
"""

NIMA_APP = {
    "package.json": '{"name":"explorer","main":"explorer/src/main.tsx"}',
    "explorer/src/main.tsx": MAIN_TSX,
    "explorer/src/App.tsx": APP_TSX,
}


def test_a_component_consumed_as_jsx_is_not_reported_without_callers(tmp_path):
    repo, store = _index(tmp_path, NIMA_APP)
    rows = _signals(repo, store)
    for component in ("WorkspaceShell", "WorkspaceFallback", "WelcomeScreen", "App"):
        assert "no_callers" not in rows.get(("explorer/src/App.tsx", component), []), (
            f"`{component}` is consumed as JSX: {rows}"
        )


def test_a_react_error_boundary_lifecycle_method_is_not_reported_without_callers(
    tmp_path,
):
    repo, store = _index(tmp_path, NIMA_APP)
    rows = _signals(repo, store)
    for method in ("componentDidCatch", "getDerivedStateFromError", "render"):
        assert "no_callers" not in rows.get(("explorer/src/App.tsx", method), []), (
            f"`{method}` is invoked by React: {rows}"
        )


def test_no_import_tsx_fallback_keeps_jsx_and_framework_callers(tmp_path):
    """call_graph_only mode must use the same additive JSX/framework evidence."""
    repo, store = _index(
        tmp_path,
        {
            "package.json": '{"name":"standalone","main":"src/App.tsx"}',
            "src/App.tsx": "function Child() { return <span>child</span> }\nfunction Parent() { console.log('render'); return <Child /> }\nclass Boundary {\n  componentDidCatch() { /* framework */ }\n  render() { return <Child /> }\n}\nfunction trulyUnusedHelper(): number { return 1 }\n",
        },
    )
    out = v2.get_dead_code_v2(
        repo=repo, min_confidence=0.0, max_results=0, storage_path=store
    )

    assert out.get("_meta", {}).get("mode") == "call_graph_only", out.get("_meta")
    rows = {(row["file"], row["name"]): row["signals"] for row in out["dead_symbols"]}
    for live in ("Child", "componentDidCatch", "render"):
        assert "no_callers" not in rows.get(("src/App.tsx", live), []), rows
    assert "no_callers" in rows.get(("src/App.tsx", "trulyUnusedHelper"), []), rows


def test_direct_storeless_tsx_fallback_keeps_framework_evidence_without_crashing():
    """The no-store direct path cannot read JSX tags, but React lifecycle
    evidence must still work instead of raising AttributeError."""
    import time

    class _FakeIndex:
        symbols = [
            {
                "id": "src/App.tsx::Child#function",
                "name": "Child",
                "kind": "function",
                "file": "src/App.tsx",
            },
            {
                "id": "src/App.tsx::Boundary.componentDidCatch#method",
                "name": "componentDidCatch",
                "kind": "method",
                "file": "src/App.tsx",
            },
            {
                "id": "src/App.tsx::Boundary.render#method",
                "name": "render",
                "kind": "method",
                "file": "src/App.tsx",
            },
        ]

        def get_callers_by_name(self):
            return {}

    out = v2._call_graph_only_dead_code(
        _FakeIndex(), "local", "standalone", time.monotonic(), store=None
    )

    assert out["_meta"]["mode"] == "call_graph_only", out
    names = {row["name"] for row in out["dead_symbols"]}
    assert "Child" in names  # without a store, tag text cannot prove a caller
    assert "componentDidCatch" not in names, out
    assert "render" not in names, out


def test_a_function_and_method_nothing_uses_are_still_reported(tmp_path):
    """The sweep must not blunt the signal: an unused sibling and an unused
    method of the very same class keep `no_callers`."""
    repo, store = _index(tmp_path, NIMA_APP)
    rows = _signals(repo, store)
    assert "no_callers" in rows.get(
        ("explorer/src/App.tsx", "trulyUnusedHelper"), []
    ), rows
    assert "no_callers" in rows.get(
        ("explorer/src/App.tsx", "helperNothingCalls"), []
    ), rows


def test_a_lowercase_intrinsic_tag_does_not_keep_a_dead_function(tmp_path):
    """`<div/>` names an intrinsic element, not a function called `div`."""
    files = {
        "app.tsx": (
            "import React from 'react'\n"
            "export function Shell() { React.useState(0); return <div><span /></div> }\n"
            "function div(): number { return 1 }\n"
        ),
    }
    repo, store = _index(tmp_path, files)
    rows = _signals(repo, store)
    assert "no_callers" in rows.get(("app.tsx", "div"), []), rows


def test_a_component_consumed_in_another_file_through_jsx_is_not_reported(tmp_path):
    """Cross-file: the tag lives in an importer, and a member tag
    (`<ui.Card/>`) reaches the component's own name."""
    files = {
        "main.tsx": (
            "import { render } from './ui'\n"
            "import { UsedElsewhere } from './used'\n"
            "import * as ui from './card'\n"
            "render(<UsedElsewhere title='fixture' /><ui.Card selected={true} />)\n"
        ),
        "ui.tsx": "export function render(node: unknown) { console.log(node) }\n",
        "used.tsx": "import React from 'react'\nexport function UsedElsewhere() { return <p>hi</p> }\n",
        "card.tsx": "import React from 'react'\nexport function Card() { return <p>card</p> }\n"
        "export function DeadComponent() { return <p>dead</p> }\n",
    }
    repo, store = _index(tmp_path, files)
    rows = _signals(repo, store)
    assert "no_callers" not in rows.get(("used.tsx", "UsedElsewhere"), []), rows
    assert "no_callers" not in rows.get(("card.tsx", "Card"), []), rows
    assert "no_callers" in rows.get(("card.tsx", "DeadComponent"), []), rows


def test_health_counts_dead_code_past_the_result_cap(tmp_path):
    """`dead_count`/`dead_code_pct` must use v2's uncapped `total_matches`,
    not the 100-row `dead_symbols` list. 150 dead of 1151 functions: the
    capped list would say 100 (8.7%), the true count is 150 (13.0%). The
    1000-function live chain keeps every signal under the 90% degeneracy
    cutoff so v2 actually returns candidates at min_confidence=0.67."""
    n_live, n_dead = 1000, 150
    files = {
        "live.py": "\n".join(
            f"def f{i}():\n    return f{i - 1}() if {i} else 0\n" for i in range(n_live)
        ),
        "dead.py": "\n".join(
            f"def dead_{i}():\n    return {i}\n" for i in range(n_dead)
        ),
        "pkg/__init__.py": "from live import "
        + ", ".join(f"f{i}" for i in range(n_live))
        + "\n",
        "main.py": "from live import f999\n\ndef main():\n    f999()\n",
    }
    repo, store = _index(tmp_path, files)
    uncapped = v2.get_dead_code_v2(repo=repo, min_confidence=0.67, storage_path=store)
    assert uncapped["_meta"]["total_matches"] == n_dead, uncapped["_meta"]
    assert uncapped["_meta"]["truncated"] is True
    assert len(uncapped["dead_symbols"]) == 100  # the cap the old code divided by
    health = get_repo_health(repo=repo, storage_path=store)
    assert health["dead_code_measurable"] is True, health.get(
        "dead_code_signal_warning"
    )
    assert health["dead_count"] == n_dead, health["dead_count"]
    assert health["dead_code_pct"] == round(n_dead / health["fn_method_count"] * 100, 1)
