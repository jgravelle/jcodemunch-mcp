"""A `file_pattern` with `/` is anchored at the repo root; a bare one matches at any depth.

`search_text`, `search_symbols` (through the index store) and the
working-tree scope check each tried the pattern and then `*/` + pattern.
fnmatch's `*` crosses `/`, so the second try matched an anchored pattern at
every depth: on a large repository `homeassistant/**` also returned files
under `tests/homeassistant/`, and the agent spent steps filtering them out.
All three now ask `path_glob`, and each is tested here at its own entry point.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp import path_glob
from jcodemunch_mcp.retrieval import subject_state
from jcodemunch_mcp.tools.index_folder import index_folder
from jcodemunch_mcp.tools.search_symbols import search_symbols
from jcodemunch_mcp.tools.search_text import search_text


@pytest.mark.parametrize(
    "path, pattern, expected",
    [
        ("pkg/core.py", "pkg/**", True),
        ("tests/pkg/core.py", "pkg/**", False),
        ("pkg/core.py", "pkg/*.py", True),
        ("tests/pkg/core.py", "pkg/*.py", False),
        ("pkg/core.py", "./pkg/*.py", True),
        ("pkg/core.py", "/pkg/*.py", True),
        ("deep/down/route.ts", "route.ts", True),
        ("route.ts", "route.ts", True),
        ("deep/down/a.py", "*.py", True),
        ("a.py", "*.py", True),
        ("deep/down/a.py", "*.ts", False),
    ],
)
def test_the_rule(path, pattern, expected):
    assert path_glob.matches(path, pattern) is expected


def test_any_of_several_patterns():
    rx = path_glob.compile_patterns(["pkg/**", "*.md"])
    assert rx.match("pkg/a.py")
    assert rx.match("docs/x.md")
    assert not rx.match("tests/pkg/a.py")
    assert not path_glob.compile_patterns([]).match("a.py")


@pytest.fixture
def nested(tmp_path):
    files = {
        "pkg/core.py": "def core_fn():\n    return 'needle'\n",
        "tests/pkg/test_core.py": "def test_core_fn():\n    return 'needle'\n",
    }
    src = tmp_path / "src"
    for rel, text in files.items():
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    store = tmp_path / "store"
    res = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert res["success"] is True, res
    return res["repo"], str(store)


def test_search_text_keeps_an_anchored_pattern_at_the_root(nested):
    rid, store = nested
    res = search_text(rid, "needle", file_pattern="pkg/**", storage_path=store)
    assert {g["file"] for g in res["results"]} == {"pkg/core.py"}, res


def test_search_text_matches_a_bare_pattern_at_any_depth(nested):
    rid, store = nested
    res = search_text(rid, "needle", file_pattern="test_core.py", storage_path=store)
    assert {g["file"] for g in res["results"]} == {"tests/pkg/test_core.py"}, res


def test_search_symbols_keeps_an_anchored_pattern_at_the_root(nested):
    rid, store = nested
    res = search_symbols(rid, "core_fn", file_pattern="pkg/**", storage_path=store)
    files = {r["file"] for r in res["results"]}
    assert files == {"pkg/core.py"}, res


def test_the_scope_check_keeps_an_anchored_pattern_at_the_root():
    assert subject_state._in_scope("pkg/core.py", "pkg/**") is True
    assert subject_state._in_scope("tests/pkg/core.py", "pkg/**") is False
    assert subject_state._in_scope("src/deep/a.py", "*.py") is True


def test_search_symbols_matches_a_bare_pattern_at_any_depth(nested):
    """A bare file name used to match only at the root here, while search_text
    matched it anywhere: one rule now serves both."""
    rid, store = nested
    res = search_symbols(rid, "core_fn", file_pattern="test_core.py", storage_path=store)
    files = {r["file"] for r in res["results"]}
    assert files == {"tests/pkg/test_core.py"}, res


# --------------------------------------------------------------------------- ratchet

import ast  # noqa: E402
from pathlib import Path  # noqa: E402

_SRC = Path(__file__).resolve().parent.parent / "src" / "jcodemunch_mcp"
_GLOB_CALLS = {"fnmatch", "fnmatchcase", "_fnmatch", "translate"}


def _rogue_sites(root: Path) -> list[str]:
    """Call sites that match a path against `file_pattern`/`scope` by hand."""
    found = []
    for py in root.rglob("*.py"):
        if py.name == "path_glob.py":
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name not in _GLOB_CALLS:
                continue
            text = ast.unparse(node)
            if "file_pattern" in text or "*/{" in text:
                found.append(f"{py.relative_to(root)}:{node.lineno}: {text}")
    return found


def test_no_reader_matches_file_pattern_by_hand():
    assert _rogue_sites(_SRC) == []


def test_the_ratchet_sees_the_reintroduced_rule(tmp_path):
    (tmp_path / "copy.py").write_text(
        "import fnmatch\n"
        "def f(p, file_pattern):\n"
        "    return fnmatch.fnmatch(p, file_pattern) or fnmatch.fnmatch(p, f'*/{file_pattern}')\n",
        encoding="utf-8",
    )
    assert len(_rogue_sites(tmp_path)) == 2
