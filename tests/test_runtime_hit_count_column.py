"""#717 (@Torolosko): runtime consumers asked `runtime_calls` for a column it never had.

`check_delete_safe._runtime_hits` and `get_group_contracts._runtime_hits_for`
ran `SUM(hit_count)`; the schema's column is `count`. Both swallowed the
`OperationalError` and returned None, so ingested runtime evidence read as
"no runtime evidence" -- and `check_delete_safe` could certify a symbol with
observed traffic as `safe_to_delete`.

The existing tests never ingested a runtime row, so the query had never once
executed against a populated table. These insert one through the REAL schema
(the db `index_folder` creates), never a hand-built table: a fixture authored
from the consumer's idea of the schema would carry `hit_count` and pass.
"""

import ast
import sqlite3
from pathlib import Path

import pytest

from jcodemunch_mcp.storage import IndexStore
from jcodemunch_mcp.storage.sqlite_store import _SCHEMA_SQL
from jcodemunch_mcp.tools.check_delete_safe import check_delete_safe
from jcodemunch_mcp.tools.check_edit_safe import check_edit_safe
from jcodemunch_mcp.tools.index_folder import index_folder

SRC = Path(__file__).resolve().parents[1] / "src" / "jcodemunch_mcp"

_REPO = {
    "lonely.py": "def orphan_func():\n    return 'nobody imports me'\n",
    "other.py": "def other():\n    return 1\n",
}


def _indexed(tmp_path: Path):
    for rel, content in _REPO.items():
        (tmp_path / rel).write_text(content, encoding="utf-8")
    storage = str(tmp_path / ".index")
    result = index_folder(str(tmp_path), use_ai_summaries=False, storage_path=storage)
    repo = result["repo"]
    owner, name = repo.split("/", 1)
    store = IndexStore(base_path=storage)
    index = store.load_index(owner, name)
    symbol_id = next(s["id"] for s in index.symbols if s["name"] == "orphan_func")
    return repo, storage, store, owner, name, symbol_id


def _ingest(store, owner, name, symbol_id, hits_by_source):
    """Insert through the schema the product created. No CREATE TABLE here."""
    conn = sqlite3.connect(str(store._sqlite._db_path(owner, name)))
    try:
        for source, hits in hits_by_source.items():
            conn.execute(
                "INSERT INTO runtime_calls (symbol_id, source, count) VALUES (?, ?, ?)",
                (symbol_id, source, hits),
            )
        conn.commit()
    finally:
        conn.close()


def test_runtime_hits_reads_an_ingested_row(tmp_path):
    from jcodemunch_mcp.tools.check_delete_safe import _runtime_hits

    _, _, store, owner, name, symbol_id = _indexed(tmp_path)
    _ingest(store, owner, name, symbol_id, {"otel": 7, "stack": 5})
    assert _runtime_hits(store, owner, name, symbol_id) == 12


def test_group_contracts_helper_reads_an_ingested_row(tmp_path):
    from jcodemunch_mcp.tools.get_group_contracts import _runtime_hits_for

    _, _, store, owner, name, symbol_id = _indexed(tmp_path)
    _ingest(store, owner, name, symbol_id, {"otel": 3})
    assert _runtime_hits_for(store, owner, name, symbol_id) == 3


def test_a_symbol_with_observed_traffic_is_not_safe_to_delete(tmp_path):
    """The destructive surface: static signals say orphan, runtime says live."""
    repo, storage, store, owner, name, symbol_id = _indexed(tmp_path)

    before = check_delete_safe(repo, symbol="orphan_func", storage_path=storage)
    assert before["verdict"] != "runtime_observed"  # premise: static-only is permissive

    _ingest(store, owner, name, symbol_id, {"otel": 40})
    after = check_delete_safe(repo, symbol="orphan_func", storage_path=storage)
    assert after["verdict"] == "runtime_observed", after
    assert after["signals"]["runtime_hits"] == 40


def test_check_edit_safe_sees_the_same_hits(tmp_path):
    repo, storage, store, owner, name, symbol_id = _indexed(tmp_path)
    _ingest(store, owner, name, symbol_id, {"otel": 9})
    result = check_edit_safe(repo, symbol="orphan_func", storage_path=storage)
    assert "error" not in result, result
    observed = [b for b in result["blockers"] if b.get("kind") == "runtime_observed"]
    assert [b["hit_count"] for b in observed] == [9], result


# --------------------------------------------------------------------------- #
# The property: every SQL literal in src/ that reads a runtime_* table        #
# compiles against the schema the product actually creates.                   #
# --------------------------------------------------------------------------- #

def _sql_literals(tree: ast.AST):
    """Yield (lineno, sql) for str constants and f-strings; `{...}` -> `?`."""
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            text = "".join(
                part.value if isinstance(part, ast.Constant) else "?"
                for part in node.values
            )
            yield node.lineno, text
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.lineno, node.value


def _runtime_selects():
    found = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for lineno, sql in _sql_literals(tree):
            flat = " ".join(sql.split())
            if flat.upper().startswith("SELECT") and "FROM runtime_" in flat:
                found.append((f"{path.relative_to(SRC).as_posix()}:{lineno}", flat))
    return found


def _compile_error(sql: str):
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(_SCHEMA_SQL)
        try:
            conn.execute("EXPLAIN " + sql, [None] * sql.count("?"))
        except sqlite3.OperationalError as exc:
            if "no such column" in str(exc) or "no such table" in str(exc):
                return str(exc)
        except sqlite3.Error:
            pass  # binding-shape noise from a flattened f-string; not this guard's question
        return None
    finally:
        conn.close()


def test_every_runtime_select_in_src_names_real_columns():
    selects = _runtime_selects()
    assert len(selects) >= 5, selects  # the scan finds the known consumers, or it is blind
    broken = {where: err for where, sql in selects if (err := _compile_error(sql))}
    assert not broken, broken


def test_the_column_guard_sees_the_reported_query():
    """Non-vacuity: the reported spelling must fail the compile check."""
    err = _compile_error(
        "SELECT COALESCE(SUM(hit_count), 0) FROM runtime_calls WHERE symbol_id = ?"
    )
    assert err and "hit_count" in err


@pytest.mark.parametrize("module", ["check_delete_safe", "get_group_contracts"])
def test_one_reader_not_two(module):
    """The two helpers were copies; a second copy is how the next drift ships."""
    text = (SRC / "tools" / f"{module}.py").read_text(encoding="utf-8")
    tree = ast.parse(text)
    own = [sql for _, sql in _sql_literals(tree) if "runtime_calls WHERE symbol_id" in sql]
    assert not own, own
