"""#717 (@Torolosko): runtime consumers asked `runtime_calls` for a column it never had.

`check_delete_safe._runtime_hits` and `get_group_contracts._runtime_hits_for`
ran `SUM(hit_count)`; the schema's column is `count`. Both swallowed the
`OperationalError` and returned None, so ingested runtime evidence read as
"no runtime evidence" -- and `check_delete_safe` could certify a symbol with
observed traffic as `safe_to_delete`.

No test of THESE tools inserted a `runtime_calls` row (the phase-4 and phase-7
runtime tests do, for other readers), so these two queries had never executed
against a populated table. These insert one through the REAL schema
(the db `index_folder` creates), never a hand-built table: a fixture authored
from the consumer's idea of the schema would carry `hit_count` and pass.
"""

import ast
import re
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
# The property: every SQL statement in src/ that touches a runtime-evidence    #
# table compiles against the schema the product actually creates.             #
#                                                                             #
# Scoped by TABLE NAME (any verb, JOINs included), never by the spelling      #
# `SELECT ... FROM runtime_`: the first draft was, and a `hit_count` planted   #
# in find_hot_paths' `JOIN runtime_calls rc` sailed through it.               #
# --------------------------------------------------------------------------- #

_EVIDENCE_TABLES = [
    t for t in re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", _SCHEMA_SQL)
    if t.startswith(("runtime_", "scip_")) or t == "diagnostics"
]
_TABLE_RE = re.compile(r"\b(" + "|".join(_EVIDENCE_TABLES) + r")\b")
_VERB_RE = re.compile(r"^\s*(SELECT|INSERT|UPDATE|DELETE)\b", re.IGNORECASE)
_COLUMN_ERRORS = ("no such column", "no such table", "has no column")

# An f-string hole may stand for a bind list, a clause or nothing, and a literal
# may be the PREFIX of a concatenated statement (`... IN (` + marks + `)`).
# Every filling and completion is tried; one clean compile checks every column.
_FILLS = ("?", "", "1", "x")
_TAILS = ("", " ?)", " 1", " ?")


def _statements(tree: ast.AST):
    """Yield (lineno, parts); an f-string hole is None."""
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            yield node.lineno, [
                p.value if isinstance(p, ast.Constant) else None for p in node.values
            ]
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.lineno, [node.value]


def _verdict(parts):
    """('ok'|'bad'|'uncompilable', detail). A column error in ANY variant is bad."""
    first_error = None
    for fill in _FILLS:
        for tail in _TAILS:
            sql = " ".join("".join(fill if p is None else p for p in parts).split()) + tail
            conn = sqlite3.connect(":memory:")
            try:
                conn.executescript(_SCHEMA_SQL)
                conn.execute("EXPLAIN " + sql, [None] * sql.count("?"))
                return "ok", None
            except sqlite3.Error as exc:
                if any(marker in str(exc) for marker in _COLUMN_ERRORS):
                    return "bad", str(exc)
                first_error = first_error or str(exc)
            finally:
                conn.close()
    return "uncompilable", first_error


def _scan(root: Path):
    results = {}
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for lineno, parts in _statements(tree):
            flat = "".join("?" if p is None else p for p in parts)
            if _VERB_RE.match(flat) and _TABLE_RE.search(flat):
                where = f"{path.relative_to(root).as_posix()}:{lineno}"
                results[where] = _verdict(parts)
    return results


def test_every_statement_on_an_evidence_table_names_real_columns():
    results = _scan(SRC)
    assert len(results) >= 60, len(results)  # a scan that finds little is blind
    # A statement the guard cannot compile is a FAILURE, never a silent pass:
    # that branch is where a wrong column would hide.
    not_ok = {where: v for where, v in results.items() if v[0] != "ok"}
    assert not not_ok, not_ok


def test_the_scan_reaches_joins_prefixes_and_writes():
    """Blindness check on the shapes the first draft could not see."""
    results = _scan(SRC)
    for needle in (
        "tools/find_hot_paths.py",      # JOIN runtime_calls rc
        "tools/get_pr_risk_profile.py",  # a concatenated `IN (` prefix
        "runtime/ingest.py",            # INSERT / DELETE
        "tools/_diagnostics_consume.py",
    ):
        assert any(where.startswith(needle) for where in results), needle


@pytest.mark.parametrize("parts", [
    # the reported query
    ["SELECT COALESCE(SUM(hit_count), 0) FROM runtime_calls WHERE symbol_id = ?"],
    # through a JOIN alias, with an f-string clause hole (find_hot_paths' shape)
    ["SELECT s.id, SUM(rc.hit_count) FROM symbols s JOIN runtime_calls rc "
     "ON rc.symbol_id = s.id ", None, " GROUP BY s.id"],
    # the prefix of a concatenated statement
    ["SELECT symbol_id, SUM(hit_count) AS n FROM runtime_calls WHERE symbol_id IN ("],
    # a write, lowercase
    ["insert into runtime_calls (symbol_id, source, hit_count) values (?, ?, ?)"],
])
def test_the_guard_sees_a_wrong_column_in_every_shape(parts):
    verdict, detail = _verdict(parts)
    assert verdict == "bad" and "hit_count" in detail, (verdict, detail)


def test_an_uncompilable_statement_is_not_waved_through():
    assert _verdict(["SELECT FROM WHERE runtime_calls"])[0] == "uncompilable"


def test_one_reader_of_a_symbols_hit_count():
    """The two helpers were copies; a copy in ANY module is how the next drift ships."""
    owners = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for _, parts in _statements(tree):
            flat = " ".join("".join("?" if p is None else p for p in parts).split())
            if re.search(r"SUM\(\w*count\).*FROM runtime_calls WHERE symbol_id = \?", flat, re.I):
                owners.append(path.relative_to(SRC).as_posix())
    assert owners == ["runtime/confidence.py"], owners
