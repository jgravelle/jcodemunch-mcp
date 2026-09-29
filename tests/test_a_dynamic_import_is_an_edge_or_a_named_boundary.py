"""A dynamic import is an import edge when its target is a literal, and a named boundary when not (#876).

Split from #718 (finding 4), reported by @Torolosko. The static model stopped at
a dynamic dispatch and said nothing:

    def _passthrough(module_name, argv):
        mod = __import__(module_name)
        return int(mod.main(argv) or 0)

    if known.command == "g-gates":
        return _passthrough("run_g_gates", rest)

An impact query on `run_g_gates.main` came back empty although a real consumer
reaches it through `__import__`. Fixed at the import authority
(`parser/imports.py`), so every graph consumer inherits it:
- a literal target (`__import__("x")`, `importlib.import_module("x")`) is an edge;
- a literal passed one step into a parameter that feeds such a call is an edge
  (the reported case; the issue's bounded "one-step finite literal
  propagation", not symbolic execution);
- a target still not a literal is recorded with the scope it can reach; an
  empty blast radius refuses inside a package/prefix scope and discloses an
  opaque site (jjg, 2026-09-29).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jcodemunch_mcp.parser.imports import extract_imports
from jcodemunch_mcp.storage.index_store import PARSER_GENERATION
from jcodemunch_mcp.tools.get_blast_radius import get_blast_radius
from jcodemunch_mcp.tools.index_folder import index_folder

PASSTHROUGH = '''\
def _passthrough(module_name: str, argv: list) -> int:
    mod = __import__(module_name)
    return int(mod.main(argv) or 0)


def dispatch(command: str, rest: list) -> int:
    if command == "g-gates":
        return _passthrough("run_g_gates", rest)
    return 0
'''
TARGET = "def main(argv):\n    return 0\n"


def _index(tmp_path: Path, files: dict[str, str]) -> tuple[str, str]:
    root = tmp_path / "r"
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    storage = str(tmp_path / "store")
    res = index_folder(str(root), use_ai_summaries=False, storage_path=storage, identity_mode="local")
    return res["repo"], storage


def _dependents(r: dict) -> set[str]:
    return {e.get("file") for key in ("confirmed", "potential") for e in r.get(key, []) if isinstance(e, dict)}


def _specs(content: str) -> list[str]:
    return [e["specifier"] for e in extract_imports(content, "m.py", "python")]


def test_the_reported_shape_a_literal_passed_into_dunder_import_is_an_edge(tmp_path):
    repo, storage = _index(tmp_path, {"cli.py": PASSTHROUGH, "run_g_gates.py": TARGET})
    r = get_blast_radius(repo=repo, symbol="main", storage_path=storage)
    assert "cli.py" in _dependents(r), "a real consumer through __import__ was invisible"
    assert "run_g_gates" in _specs(PASSTHROUGH)


def test_a_literal_target_is_an_edge_in_both_spellings():
    src = 'import importlib\nA = importlib.import_module("pkg.alpha")\nB = __import__("pkg.beta")\n'
    specs = _specs(src)
    assert "pkg.alpha" in specs and "pkg.beta" in specs


def test_propagation_is_one_step_and_by_position_or_keyword():
    src = (
        "import importlib\n"
        "def load(name):\n    return importlib.import_module(name)\n"
        'load("pkg.one")\n'
        'load(name="pkg.two")\n'
    )
    specs = _specs(src)
    assert "pkg.one" in specs and "pkg.two" in specs


def test_an_opaque_target_is_disclosed_beside_an_empty_blast_not_refused(tmp_path):
    """jjg, 2026-09-29: a name computed from data is disclosed; only a scope that
    reaches the file refuses (a refusal on every empty result is ignored)."""
    repo, storage = _index(tmp_path, {
        "plugins.py": "import os\n\ndef load():\n    return __import__(os.environ['PLUGIN'])\n",
        "lonely.py": "def alone():\n    return 0\n",
    })
    r = get_blast_radius(repo=repo, symbol="alone", storage_path=storage)
    assert _dependents(r) == set()
    v = r["_meta"]["verdict"]
    assert v["state"] == "absent", v
    assert v["dynamic_imports_unfollowed"]["files"] == ["plugins.py"]


def test_a_literal_prefix_refuses_inside_that_package_only(tmp_path):
    """`import_module(f"adapters.{name}")` can load only `adapters.*`."""
    repo, storage = _index(tmp_path, {
        "run.py": "import importlib\n\ndef make(name):\n    return importlib.import_module(f'adapters.{name}')\n",
        "adapters/__init__.py": "",
        "adapters/alpha.py": "def build():\n    return 1\n",
        "lonely.py": "def alone():\n    return 0\n",
    })
    inside = get_blast_radius(repo=repo, symbol="build", storage_path=storage)["_meta"]["verdict"]
    assert inside["state"] == "degraded" and inside["incomplete"]["reason"] == "dynamic_import_boundary"
    assert inside["incomplete"]["files"] == ["run.py"]
    outside = get_blast_radius(repo=repo, symbol="alone", storage_path=storage)["_meta"]["verdict"]
    assert outside["state"] == "absent"


def test_control_without_a_dynamic_import_absence_is_provable(tmp_path):
    repo, storage = _index(tmp_path, {
        "plugins.py": "import os\n\ndef load():\n    return os.getcwd()\n",
        "lonely.py": "def alone():\n    return 0\n",
    })
    r = get_blast_radius(repo=repo, symbol="alone", storage_path=storage)
    assert r["_meta"]["verdict"]["state"] == "absent"


def test_control_a_fully_resolved_dynamic_import_does_not_block_absence(tmp_path):
    """Only an UNRESOLVED target is a boundary; a literal one is just an edge."""
    repo, storage = _index(tmp_path, {
        "cli.py": PASSTHROUGH,
        "run_g_gates.py": TARGET,
        "lonely.py": "def alone():\n    return 0\n",
    })
    r = get_blast_radius(repo=repo, symbol="alone", storage_path=storage)
    assert r["_meta"]["verdict"]["state"] == "absent"


def test_the_parser_generation_names_the_change():
    """New edges on unchanged content: an existing index must re-parse (Standing lesson 08-05)."""
    assert PARSER_GENERATION >= 9


def test_a_target_from_a_module_level_literal_table_is_a_finite_set_of_edges():
    """`TABLE[name][1]` can only be one of the table's strings (this repo's grammar_pack shape)."""
    src = (
        "import importlib\n"
        'GRAMMARS = {"fs": ("tree-sitter-fs", "pkg.fs_grammar")}\n'
        "def load(name):\n    return importlib.import_module(GRAMMARS[name][1])\n"
    )
    edges = extract_imports(src, "m.py", "python")
    assert "pkg.fs_grammar" in [e["specifier"] for e in edges]
    assert not any(e.get("dynamic_unresolved") for e in edges), "a finite literal set is not a boundary"


def test_a_package_relative_target_is_a_boundary_only_inside_that_package(tmp_path):
    """The #569 self-enumeration shape can load only its own package's modules."""
    repo, storage = _index(tmp_path, {
        "pkg/__init__.py": "",
        "pkg/schemas/__init__.py": "",
        "pkg/schemas/registry.py": (
            "import importlib, pkgutil\n"
            "def discover():\n"
            "    from . import __path__ as p, __name__ as pkg_name\n"
            "    for m in pkgutil.iter_modules(p):\n"
            '        importlib.import_module(f"{pkg_name}.{m.name}")\n'
        ),
        "pkg/schemas/alpha.py": "def encode():\n    return 1\n",
        "pkg/other.py": "def elsewhere():\n    return 0\n",
    })
    inside = get_blast_radius(repo=repo, symbol="encode", storage_path=storage)["_meta"]["verdict"]
    assert inside["state"] == "degraded" and inside["incomplete"]["reason"] == "dynamic_import_boundary"
    outside = get_blast_radius(repo=repo, symbol="elsewhere", storage_path=storage)["_meta"]["verdict"]
    assert outside["state"] == "absent", "a package-scoped loader cannot reach a module outside its package"


def _edges(src: str) -> list[dict]:
    return extract_imports(src, "m.py", "python")


def _markers(src: str) -> list[str]:
    return sorted(e.get("dynamic_scope") for e in _edges(src) if e.get("dynamic_unresolved"))


def test_a_module_name_attribute_scopes_to_that_module():
    src = (
        "import importlib\n"
        "from pkg.encoding import schemas as schemas_pkg\n"
        "def load(n):\n    return importlib.import_module(f'{schemas_pkg.__name__}.{n}')\n"
    )
    assert "prefix:pkg.encoding.schemas" in _markers(src)


def test_a_loop_over_a_literal_sequence_is_a_set_of_edges():
    src = "import importlib\nmods = {m: importlib.import_module(m) for m in ('adapter', 'run')}\n"
    specs = [e["specifier"] for e in _edges(src)]
    assert "adapter" in specs and "run" in specs
    assert _markers(src) == []


def test_a_public_loader_with_no_caller_here_is_an_opaque_site():
    """Review: a library loader called from another file emitted nothing."""
    src = "import importlib\ndef load(name):\n    return importlib.import_module(name)\n"
    assert _markers(src) == ["opaque"]


def test_a_private_loader_resolved_by_its_callers_is_no_site():
    assert _markers(PASSTHROUGH) == []


def test_a_table_is_indexed_along_its_subscript_path_never_by_its_keys():
    src = (
        "import importlib\n"
        "T = {'os': ('dist-name', 'pkg.a'), 'json': ('other-dist', 'pkg.b')}\n"
        "def load(k):\n    return importlib.import_module(T[k][1])\n"
    )
    specs = {e["specifier"] for e in _edges(src) if e.get("dynamic")}
    assert specs == {"pkg.a", "pkg.b"}, "keys and other tuple positions are not candidates"


def test_a_method_feeder_skips_self():
    src = (
        "import importlib\n"
        "class L:\n"
        "    def load(self, name):\n        return importlib.import_module(name)\n"
        "    def go(self):\n        return self.load('pkg.x')\n"
    )
    edges = _edges(src)
    assert "pkg.x" in [e["specifier"] for e in edges]


def test_an_unparseable_file_is_not_a_boundary():
    assert _markers("print 'py2'\nimportlib.import_module(x)\n") == []


# --- review round 2 -------------------------------------------------------

OPAQUE = {
    "plugins.py": "import os\n\ndef load():\n    return __import__(os.environ['PLUGIN'])\n",
    "lonely.py": "def alone():\n    return 0\n",
}


@pytest.mark.asyncio
async def test_the_opaque_disclosure_survives_the_default_meta_fields(tmp_path, monkeypatch):
    """`meta_fields: []` is the shipped default and strips `_meta` (Standing lesson 08-30)."""
    import json

    from jcodemunch_mcp import config as _config
    from jcodemunch_mcp import server

    from jcodemunch_mcp.storage.token_tracker import result_cache_invalidate

    # Storage env BEFORE the index, a cleared shared cache and an explicit
    # `format`: whatever an earlier test on this worker left behind must not
    # choose the store, the cached answer or the encoding (the sibling
    # dispatcher tests' fixture does the same).
    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path / "store"))
    repo, storage = _index(tmp_path, OPAQUE)
    result_cache_invalidate()
    real = _config.get
    monkeypatch.setattr(
        _config, "get", lambda key, default=None, **kw: [] if key == "meta_fields" else real(key, default, **kw)
    )
    res = await server.call_tool("get_blast_radius", {"repo": repo, "symbol": "alone", "format": "json"})
    content = getattr(res, "content", res)
    body = json.loads(content[0].text)
    assert "error" not in body, body
    assert "verdict" not in body.get("_meta", {}), "the default strips the verdict"
    assert body["dynamic_imports_unfollowed"]["files"] == ["plugins.py"]


@pytest.mark.parametrize("use", [
    "results = list(map(_load, CONFIG))\n",
    "registry.register(_load)\n",
    "a._load(cfg)\n",
    "",
])
def test_a_private_feeder_used_other_than_by_a_direct_call_is_a_site(use):
    """Round 1's public-feeder rule, for every spelling of an escaping private one."""
    src = (
        "import importlib\n"
        "def _load(name):\n    return importlib.import_module(name)\n"
        + use
    )
    assert _markers(src) == ["opaque"], use


def test_a_private_feeder_escaping_keeps_its_direct_call_edges_out():
    src = (
        "import importlib\n"
        "def _load(name):\n    return importlib.import_module(name)\n"
        "_load('pkg.x')\n"
        "hooks = [_load]\n"
    )
    assert _markers(src) == ["opaque"]


def test_a_rebound_feeder_parameter_is_not_the_callers_literal(tmp_path):
    src = (
        "import importlib\n"
        "def _load(name):\n    name = 'plugins.' + name\n    return importlib.import_module(name)\n"
        "_load('foo')\n"
    )
    assert "foo" not in [e["specifier"] for e in _edges(src)], "a wrong edge to an unrelated foo"
    assert _markers(src) == ["opaque"]
    repo, storage = _index(tmp_path, {
        "run.py": src,
        "foo.py": "def unrelated():\n    return 0\n",
        "plugins/__init__.py": "",
        "plugins/foo.py": "def hook():\n    return 1\n",
    })
    r = get_blast_radius(repo=repo, symbol="unrelated", storage_path=storage)
    assert "run.py" not in _dependents(r)
    assert r["dynamic_imports_unfollowed"]["files"] == ["run.py"]


def test_a_rebound_loop_variable_is_not_its_loop_literals():
    src = (
        "import importlib\n"
        "for m in ('a', 'b'):\n    pass\n"
        "m = cfg\n"
        "importlib.import_module(m)\n"
    )
    assert not {"a", "b"} & {e["specifier"] for e in _edges(src)}
    assert _markers(src) == ["opaque"]


@pytest.mark.parametrize("call", [
    "importlib.import_module(f'.{m.name}', __package__)",
    "importlib.import_module('.' + m.name, package=__name__)",
])
def test_a_relative_name_anchored_on_this_package_is_package_scope(call):
    src = (
        "import importlib, pkgutil\n"
        "for m in pkgutil.iter_modules(__path__):\n"
        f"    {call}\n"
    )
    assert _markers(src) == ["package"], call


def test_a_parent_relative_name_is_not_this_package():
    src = "import importlib\nimportlib.import_module('..' + n, __package__)\n"
    assert _markers(src) == ["opaque"]


def test_a_name_defined_twice_is_not_propagated_through():
    src = (
        "import importlib\n"
        "class A:\n    def load(self, name):\n        return importlib.import_module(name)\n"
        "class B:\n    def load(self, name):\n        return name\n"
        "def load(name):\n    return importlib.import_module(name)\n"
        "load('pkg.x')\n"
    )
    assert "pkg.x" not in [e["specifier"] for e in _edges(src)]
    assert _markers(src) == ["opaque"]


def test_the_marker_names_no_package_for_any_cross_repo_reader():
    """One layer down: every reader of this helper skips the marker (review of #876)."""
    from jcodemunch_mcp.parser.imports import DYNAMIC_IMPORT_UNRESOLVED
    from jcodemunch_mcp.tools.package_registry import extract_root_package_from_specifier

    for lang in ("python", "javascript"):
        assert extract_root_package_from_specifier(DYNAMIC_IMPORT_UNRESOLVED, lang) == ""


def test_a_comprehension_target_is_bounded_even_when_its_name_is_reused():
    """The comprehension's variable is local to it; a later `m = ...` is another name."""
    src = (
        "import importlib\n"
        "mods = {m: importlib.import_module(m) for m in ('adapter', 'run')}\n"
        "m = cfg\n"
    )
    assert {"adapter", "run"} <= {e["specifier"] for e in _edges(src)}
    assert _markers(src) == []


# --- review round 3 -------------------------------------------------------


@pytest.mark.parametrize("change", [
    "T = load_config()\n",
    "T['b'] = cfg\n",
    "T.update(cfg)\n",
    "del T['a']\n",
    "register(T)\n",
    "U = T\n",
])
def test_a_table_changed_anywhere_is_not_its_literal_values(change):
    """A seeded registry filled at runtime can hold anything (review of #876)."""
    src = (
        "import importlib\n"
        "T = {'a': 'pkg.a'}\n"
        + change
        + "def _l(k):\n    return importlib.import_module(T[k])\n"
        "_l(x)\n"
    )
    assert "pkg.a" not in [e["specifier"] for e in _edges(src)], change
    assert _markers(src) == ["opaque"], change


def test_control_a_table_only_read_by_subscript_stays_its_values():
    src = (
        "import importlib\n"
        "T = {'a': 'pkg.a'}\n"
        "def _l(k):\n    return importlib.import_module(T[k])\n"
        "_l(x)\n"
        "print(T['a'])\n"
    )
    assert "pkg.a" in [e["specifier"] for e in _edges(src)]
    assert _markers(src) == []


def test_control_a_table_read_by_membership_iteration_and_read_methods_stays_its_values():
    """This repo's `grammar_pack.STANDALONE_GRAMMARS` shape: `in`, `sorted`, `.get` are reads."""
    src = (
        "import importlib\n"
        "T = {'a': 'pkg.a'}\n"
        "def _l(k):\n"
        "    if k in T:\n        return importlib.import_module(T[k])\n"
        "    return ', '.join(sorted(T)) or T.get(k)\n"
        "_l(x)\n"
        "for name in T:\n    print(f'{T}')\n"
    )
    assert "pkg.a" in [e["specifier"] for e in _edges(src)]
    assert _markers(src) == []


@pytest.mark.parametrize("comp", [
    "[[importlib.import_module(m) for m in cfg] for m in ('a', 'b')]",
    "[importlib.import_module(m) for m in ('a', 'b') for m in cfg]",
])
def test_a_shadowed_comprehension_target_is_not_the_outer_literals(comp):
    src = f"import importlib\nx = {comp}\n"
    assert not {"a", "b"} & {e["specifier"] for e in _edges(src)}, comp
    assert _markers(src) == ["opaque"], comp


def test_changed_symbols_carries_the_opaque_disclosure_for_an_empty_blast(tmp_path):
    """The second consumer of `blast_verdict` publishes the disclosure too."""
    import subprocess

    from jcodemunch_mcp.tools.get_changed_symbols import get_changed_symbols

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=str(root), check=True, capture_output=True, text=True,
            encoding="utf-8", stdin=subprocess.DEVNULL,
        ).stdout.strip()

    root = tmp_path / "r"
    root.mkdir()
    for rel, text in OPAQUE.items():
        (root / rel).write_text(text, encoding="utf-8")
    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    git("add", "-A")
    git("commit", "-qm", "c0")
    base = git("rev-parse", "HEAD")
    (root / "lonely.py").write_text("def alone():\n    return 1\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "c1")
    storage = str(tmp_path / "store")
    repo = index_folder(str(root), use_ai_summaries=False, storage_path=storage, identity_mode="local")["repo"]
    r = get_changed_symbols(repo, since_sha=base, include_blast_radius=True, storage_path=storage)
    entry = next(e for e in r["changed_symbols"] if e["name"] == "alone")
    assert entry["blast_radius"] == []
    assert entry["blast_verdict"]["dynamic_imports_unfollowed"]["files"] == ["plugins.py"]
    assert r["blast_verdicts"]["lonely.py"]["dynamic_imports_unfollowed"]["files"] == ["plugins.py"]


# --- review round 4 -------------------------------------------------------


@pytest.mark.parametrize("table, change, read", [
    ("{'a': {'m': 'pkg.a'}}", "T['a']['m'] = cfg\n", "T[k]['m']"),
    ("{'a': ['pkg.a']}", "T['a'].append(cfg)\n", "T[k][0]"),
    ("{'a': {'m': 'pkg.a'}}", "T['a'].update(m=cfg)\n", "T[k]['m']"),
    ("{'a': ['pkg.a']}", "for v in T.values():\n    v.append(cfg)\n", "T[k][0]"),
])
def test_a_mutable_value_inside_a_table_makes_the_table_a_site(table, change, read):
    """Only the table's NAME is guarded; a list or dict inside it can change unseen."""
    src = (
        "import importlib\n"
        f"T = {table}\n"
        + change
        + f"def _l(k):\n    return importlib.import_module({read})\n"
        "_l(x)\n"
    )
    assert "pkg.a" not in [e["specifier"] for e in _edges(src)], change
    assert _markers(src) == ["opaque"], change
