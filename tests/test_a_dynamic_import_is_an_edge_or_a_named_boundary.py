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


def test_an_unresolved_target_refuses_an_empty_blast_and_names_the_site(tmp_path):
    repo, storage = _index(tmp_path, {
        "plugins.py": "import os\n\ndef load():\n    return __import__(os.environ['PLUGIN'])\n",
        "lonely.py": "def alone():\n    return 0\n",
    })
    r = get_blast_radius(repo=repo, symbol="alone", storage_path=storage)
    assert _dependents(r) == set()
    v = r["_meta"]["verdict"]
    assert v["state"] == "degraded" and v["absence_refused"] is True, v
    assert v["incomplete"]["reason"] == "dynamic_import_boundary"
    assert "plugins.py" in v["incomplete"]["files"]


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
