"""A tool's kind set derives from `KIND_ORDER`, never a typed literal (#806).

Five tools carried literal kind sets written before the state vocabulary
(`field`, `property`, `variable`) existed, so a class member that used to
arrive as `constant` -- and passed the filter, and ranked at the constant
tier -- dropped out or fell to a default nobody chose once its language
learned the real word (#735, #743, #732, #755, #769, #787, #807). #760's
lesson one layer over: "a consumer keyed on ONE kind string sees one of
four."

Rulings, per site (they are different questions, so they share the
DERIVATION, `symbols.STATE_KINDS`, not one answer):
- picking a file's representative symbol (`get_repo_map`,
  `get_repo_outline`, `get_symbol_importance`): every state kind ranks where
  `constant` ranks, which is where those members ranked before they had
  their own words; one table, `symbols.REPRESENTATIVE_KIND_RANK`, since the
  three copies were identical and answer one question;
- resolving a name for `find_implementations`: the same tier;
- a dead contract (`get_group_contracts`): a state kind is part of a
  module's importable surface only at MODULE scope (no parent). A class
  member is never imported by name, so listing every public field as a dead
  contract would be noise; `constant` keeps its existing unconditional row.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

from jcodemunch_mcp.parser.symbols import KIND_ORDER, STATE_KINDS

_TOOLS = Path(__file__).resolve().parents[1] / "src" / "jcodemunch_mcp" / "tools"


def _literal_kind_sets(source: str) -> list[tuple[int, set[str]]]:
    """Every set/dict/tuple/list literal naming a state kind AND another kind."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Dict):
            elts = node.keys
        elif isinstance(node, (ast.Set, ast.Tuple, ast.List)):
            elts = node.elts
        else:
            continue
        names = {e.value for e in elts if isinstance(e, ast.Constant) and isinstance(e.value, str)}
        kinds = names & set(KIND_ORDER)
        if kinds & set(STATE_KINDS) and len(kinds) >= 2:
            found.append((node.lineno, kinds))
    return found


def test_no_tool_types_a_kind_set_that_names_a_state_kind():
    offenders = []
    for path in sorted(_TOOLS.glob("*.py")):
        for line, kinds in _literal_kind_sets(path.read_text(encoding="utf-8")):
            offenders.append(f"{path.name}:{line} {sorted(kinds)}")
    assert not offenders, "derive from symbols.STATE_KINDS / KIND_ORDER:\n" + "\n".join(offenders)


def test_the_ratchet_fires_on_the_reintroduced_literal():
    """Non-vacuity: the exact shape #806 reported, in all three spellings."""
    assert _literal_kind_sets('P = {"class": 0, "function": 1, "constant": 4}\n')
    assert _literal_kind_sets('if k not in {"function", "class", "constant"}: pass\n')
    assert _literal_kind_sets('K = ("method", "field")\n')
    # A single-kind comparison is not a set and stays legal.
    assert not _literal_kind_sets('ok = kind == "constant"\n')


def _make_repo(tmp_path: Path, files: dict) -> tuple[str, str]:
    from jcodemunch_mcp.tools.index_folder import index_folder

    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    storage = str(tmp_path / ".index")
    result = index_folder(str(tmp_path), use_ai_summaries=False, storage_path=storage)
    return result.get("repo", str(tmp_path)), storage


def test_get_repo_map_ranks_a_field_at_the_constant_tier(tmp_path):
    """A field fell to the default (9) behind every constant; at the same
    tier the larger symbol comes first, so with two slots the field is shown."""
    from jcodemunch_mcp.tools.get_repo_map import get_repo_map

    repo, storage = _make_repo(tmp_path, {
        "config.py": "MAX = 1\n\nclass Config:\n    retries_allowed: int = 10\n",
    })
    result = get_repo_map(repo, max_per_file=2, storage_path=storage)
    files = {f["path"]: f for f in result["files"]}
    names = [s["name"] for s in files["config.py"]["symbols"]]
    assert names == ["Config", "retries_allowed"], names


def test_every_representative_table_ranks_state_with_constant():
    from jcodemunch_mcp.parser.symbols import REPRESENTATIVE_KIND_RANK

    for kind in STATE_KINDS:
        assert REPRESENTATIVE_KIND_RANK[kind] == REPRESENTATIVE_KIND_RANK["constant"], kind


def test_find_implementations_resolves_a_state_kind_at_the_constant_tier():
    """A `property` fell to 8, behind a `template` (5); `constant` ranks
    ahead of a template, and so does the member that used to carry it."""
    from jcodemunch_mcp.tools.find_implementations import _resolve_target_symbol

    index = SimpleNamespace(symbols=[
        {"id": "a::X#template", "name": "X", "kind": "template", "byte_length": 50},
        {"id": "a::K.X#property", "name": "X", "kind": "property", "byte_length": 10},
    ])
    assert _resolve_target_symbol(index, "X")["kind"] == "property"


def test_get_group_contracts_lists_a_module_binding_but_not_a_class_member(tmp_path):
    from jcodemunch_mcp.tools.get_group_contracts import get_group_contracts
    from jcodemunch_mcp.tools.index_folder import index_folder
    from jcodemunch_mcp.tools.package_registry import invalidate_registry_cache

    provider, consumer, store = tmp_path / "provider", tmp_path / "consumer", tmp_path / "store"
    for d in (provider, consumer, store):
        d.mkdir()
    (provider / "pyproject.toml").write_text('[project]\nname = "mylib"\n', encoding="utf-8")
    (provider / "__init__.py").write_text("def validate_token(t):\n    return bool(t)\n", encoding="utf-8")
    (provider / "state.js").write_text(
        "export let counter = 0;\nexport class Store {\n  count = 0;\n}\n", encoding="utf-8"
    )
    (consumer / "pyproject.toml").write_text('[project]\nname = "consumer"\n', encoding="utf-8")
    (consumer / "app.py").write_text(
        "from mylib import validate_token\n\ndef login(t):\n    return validate_token(t)\n", encoding="utf-8"
    )
    ids = []
    for folder in (provider, consumer):
        r = index_folder(str(folder), use_ai_summaries=False, storage_path=str(store))
        assert r["success"] is True, r
        ids.append(r["repo"])
    invalidate_registry_cache()

    result = get_group_contracts(repos=ids, min_importers=1, include_dead_contracts=True, storage_path=str(store))
    dead = {c["name"]: c["kind"] for c in result["contracts"] if c["verdict"] == "dead_contract"}
    assert dead.get("counter") == "variable", dead
    assert "count" not in dead, dead
