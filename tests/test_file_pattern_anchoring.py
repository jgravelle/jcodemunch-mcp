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
from jcodemunch_mcp.tools.get_ranked_context import get_ranked_context
from jcodemunch_mcp.tools.get_untested_symbols import get_untested_symbols
from jcodemunch_mcp.tools.index_folder import index_folder
from jcodemunch_mcp.tools.search_symbols import search_symbols
from jcodemunch_mcp.tools.search_text import search_text
from jcodemunch_mcp.tools.winnow_symbols import winnow_symbols


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
        # fnmatch's `*` crosses `/`, so a bare pattern can match a directory
        # name; existing callers rely on `*name*` meaning "anywhere".
        ("pkg/detached/x.py", "*detached*", True),
        # `**/` matches zero or more directories.
        ("src/a.py", "src/**/*.py", True),
        ("src/d/e/a.py", "src/**/*.py", True),
        ("lib/src/a.py", "src/**/*.py", False),
        ("a.py", "**/*.py", True),
        ("src/x/sub/a.py", "src/**/sub/**/*.py", True),
        ("src/sub/a.py", "src/**/sub/**/*.py", True),
        # A backslash reads as `/`, in the pattern as in the path.
        ("pkg/core.py", r"pkg\*.py", True),
        ("tests/pkg/core.py", r"pkg\*.py", False),
        (r"pkg\core.py", "pkg/*.py", True),
        # Case-sensitive on every platform.
        ("pkg/core.py", "PKG/*.py", False),
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


def test_get_ranked_context_scope_follows_the_rule(nested):
    """The scan and the working-tree scope check behind its absence verdict
    use one rule, so they cannot disagree about which files are in scope."""
    rid, store = nested
    res = get_ranked_context(rid, "core_fn", scope="PKG/*", storage_path=store)
    assert res.get("context_items", []) == [], res
    assert subject_state._in_scope("pkg/core.py", "PKG/*") is False
    res = get_ranked_context(rid, "core_fn", scope="pkg/*", storage_path=store)
    assert {i["file"] for i in res["context_items"]} == {"pkg/core.py"}, res


def test_winnow_file_axis_matches_a_bare_pattern_at_any_depth(nested):
    rid, store = nested
    res = winnow_symbols(
        rid, [{"axis": "file", "op": "matches", "value": "test_core.py"}], storage_path=store
    )
    assert {r["file"] for r in res["results"]} == {"tests/pkg/test_core.py"}, res


def test_get_untested_symbols_reads_a_backslash_pattern(nested):
    rid, store = nested
    res = get_untested_symbols(rid, file_pattern=r"pkg\*.py", storage_path=store)
    assert {r["file"] for r in res["symbols"]} == {"pkg/core.py"}, res


# --------------------------------------------------------------------------- ratchet

import ast  # noqa: E402
from pathlib import Path  # noqa: E402

_SRC = Path(__file__).resolve().parent.parent / "src" / "jcodemunch_mcp"
_GLOB_CALLS = {"fnmatch", "fnmatchcase", "_fnmatch", "translate"}

# Every glob match in src/ that is NOT a repo path against a user's path
# pattern, and why. A new call site is either one of these jobs or a reader
# that must ask `path_glob`; the ratchet makes the author say which.
_ALLOWED = {
    "secret_classifier.py::_path_matches": "secret-file name patterns, case-sensitive by design",
    "secret_classifier.py::classify_secret_file": "secret-file name patterns",
    "tier_resolver.py::resolve_model_to_tier": "model ids, not paths",
    "tools/_entry_points.py::matches": "framework entry-point profiles, their own three dialects",
    "tools/find_dead_code.py::_matches_any_pattern": "live-root patterns, a documented separate matcher",
    "tools/list_workspaces.py::_apply_glob_set": "workspace manifest globs (npm/pnpm semantics)",
    "tools/list_workspaces.py::_expand_glob": "workspace manifest globs (npm/pnpm semantics)",
    "tools/search_ast.py::_custom_call_search": "callee and symbol name globs, not paths",
    "tools/search_columns.py::search_columns": "dbt model names, not paths",
}


def _glob_sites(root: Path) -> list[str]:
    """`file::function` for every fnmatch/translate call outside path_glob."""
    found = []
    for py in sorted(root.rglob("*.py")):
        if py.name == "path_glob.py":
            continue
        rel = py.relative_to(root).as_posix()

        def walk(node, fn):
            for ch in ast.iter_child_nodes(node):
                inner = ch.name if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)) else fn
                if isinstance(ch, ast.Call):
                    f = ch.func
                    name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
                    # str.translate is not a glob; only fnmatch.translate is.
                    is_str_translate = (
                        name == "translate"
                        and isinstance(f, ast.Attribute)
                        and not (isinstance(f.value, ast.Name) and f.value.id == "fnmatch")
                    )
                    if name in _GLOB_CALLS and not is_str_translate:
                        found.append(f"{rel}::{fn}")
                walk(ch, inner)

        walk(ast.parse(py.read_text(encoding="utf-8")), "<module>")
    return found


def _rogue_sites(root: Path) -> list[str]:
    return sorted({s for s in _glob_sites(root) if s not in _ALLOWED})


def test_no_reader_matches_a_path_pattern_by_hand():
    assert _rogue_sites(_SRC) == []


def test_every_allowed_site_still_exists():
    assert sorted(set(_ALLOWED) - set(_glob_sites(_SRC))) == []


def test_the_ratchet_sees_the_reintroduced_rule(tmp_path):
    """Keyed on the call, never on a parameter's spelling: a hand-written
    matcher under any argument name is found."""
    (tmp_path / "copy.py").write_text(
        "import fnmatch" + chr(10)
        + "def f(p, pattern):" + chr(10)
        + "    return fnmatch.fnmatch(p, pattern) or fnmatch.fnmatch(p, f'*/{pattern}')" + chr(10),
        encoding="utf-8",
    )
    assert _rogue_sites(tmp_path) == ["copy.py::f"]
