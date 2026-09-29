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
- a target still not a literal is recorded, and an empty Python blast radius
  refuses its absence claim, naming the site.
"""

from __future__ import annotations

from pathlib import Path

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
