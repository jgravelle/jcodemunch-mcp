"""Compiler diagnostics mapped to symbols — the ingest half.

PRD: docs/prd-compiler-diagnostics.md. Provenance: trace-mcp#1181 shipped a
``get_diagnostics`` tool that RUNS tsc/mypy/pyright inside the server; we
INGEST the file the user's own CI already produced, as a fifth source on
``import_runtime_signal`` / ``import-trace``.

Fixtures under ``tests/fixtures/diagnostics/`` are the real tools' output
over ``sample_mod.py`` / ``sample_a.ts`` (see REGENERATE.md there): a fixture
authored from a tool's documentation tests nothing.

Covers:
- format auto-detection by CONTENT for mypy / pyright / tsc / ruff / generic
- each parser yields the same ``Diagnostic`` record, 1-based lines
  (pyright is 0-based on the wire), normalised severity
- (file, line) resolves to the INNERMOST symbol; backslash and absolute
  paths resolve; a line in no symbol is unmapped with a named reason
- a second ingest for the same tool REPLACES that tool's rows (a fixed error
  disappears) and leaves another tool's rows alone
- ``sample_message`` passes through the redaction chokepoint
- ``import_runtime_signal(source="diagnostics")`` is wired
- a pre-existing database without the table gets it at ingest, without an
  INDEX_VERSION bump
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

FIX = Path(__file__).parent / "fixtures" / "diagnostics"


# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────


def _make_repo(tmp_path: Path) -> tuple[str, str, Path]:
    """Index the fixture sources under the layout the checkers saw."""
    from jcodemunch_mcp.tools.index_folder import index_folder
    from jcodemunch_mcp.storage.sqlite_store import SQLiteIndexStore

    root = tmp_path / "diagfix"
    (root / "pkg").mkdir(parents=True)
    (root / "ts").mkdir()
    (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (root / "pkg" / "mod.py").write_text((FIX / "sample_mod.py").read_text(encoding="utf-8"), encoding="utf-8")
    (root / "ts" / "a.ts").write_text((FIX / "sample_a.ts").read_text(encoding="utf-8"), encoding="utf-8")
    storage = str(tmp_path / ".index")
    result = index_folder(str(root), use_ai_summaries=False, storage_path=storage)
    repo_id = result["repo"]
    owner, name = repo_id.split("/", 1)
    db_path = SQLiteIndexStore(base_path=storage)._db_path(owner, name)
    return repo_id, storage, db_path


def _rows(db_path: Path, tool: str | None = None) -> list[sqlite3.Row]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        if tool:
            return conn.execute("SELECT * FROM diagnostics WHERE tool = ? ORDER BY symbol_id, code", (tool,)).fetchall()
        return conn.execute("SELECT * FROM diagnostics ORDER BY tool, symbol_id, code").fetchall()
    finally:
        conn.close()


# ──────────────────────────────────────────────────────────────────────
# Parsers
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "fixture, expected",
    [
        ("mypy.jsonl", "mypy"),
        ("pyright.json", "pyright"),
        ("tsc.txt", "tsc"),
        ("ruff.json", "ruff"),
    ],
)
def test_format_is_detected_from_content_not_extension(fixture, expected):
    from jcodemunch_mcp.runtime.diagnostics_log import detect_format

    text = (FIX / fixture).read_text(encoding="utf-8")
    assert detect_format(text) == expected


def test_generic_jsonl_is_detected_and_carries_its_own_tool_name():
    from jcodemunch_mcp.runtime.diagnostics_log import detect_format, iter_diagnostics_from_text

    text = json.dumps({"file": "pkg/mod.py", "line": 10, "severity": "error", "message": "m", "code": "E1", "tool": "clippy"}) + "\n"
    assert detect_format(text) == "generic"
    (d,) = list(iter_diagnostics_from_text(text))
    assert (d.file, d.line, d.severity, d.code, d.tool) == ("pkg/mod.py", 10, "error", "E1", "clippy")


def test_unrecognised_text_raises_rather_than_guessing():
    from jcodemunch_mcp.runtime.diagnostics_log import detect_format

    with pytest.raises(ValueError):
        detect_format("this is a README, not a checker's output\n")


def test_mypy_parser_reads_every_row_with_1_based_lines():
    from jcodemunch_mcp.runtime.diagnostics_log import iter_diagnostics_from_text

    ds = list(iter_diagnostics_from_text((FIX / "mypy.jsonl").read_text(encoding="utf-8")))
    assert len(ds) == 5
    assert {d.tool for d in ds} == {"mypy"}
    assert [d.line for d in ds] == [10, 15, 19, 26, 26]  # line 26 carries two codes
    assert ds[0].code == "arg-type" and ds[0].severity == "error"
    assert ds[0].file.replace("\\", "/") == "pkg/mod.py"


def test_pyright_parser_converts_0_based_lines_and_reads_rule_as_code():
    from jcodemunch_mcp.runtime.diagnostics_log import iter_diagnostics_from_text

    ds = list(iter_diagnostics_from_text((FIX / "pyright.json").read_text(encoding="utf-8")))
    assert ds and {d.tool for d in ds} == {"pyright"}
    first = ds[0]
    assert first.line == 10  # wire says 9
    assert first.code == "reportArgumentType"
    assert first.severity == "error"
    assert first.file.lower().endswith("pkg\\mod.py") or first.file.lower().endswith("pkg/mod.py")


def test_tsc_parser_reads_path_line_col_code_and_message():
    from jcodemunch_mcp.runtime.diagnostics_log import iter_diagnostics_from_text

    ds = list(iter_diagnostics_from_text((FIX / "tsc.txt").read_text(encoding="utf-8")))
    assert [(d.file, d.line, d.column, d.code, d.severity) for d in ds] == [
        ("a.ts", 5, 14, "TS2345", "error"),
        ("a.ts", 9, 5, "TS2322", "error"),
    ]
    assert ds[0].message.startswith("Argument of type 'string'")


def test_ruff_parser_reads_rows_as_warnings_with_rule_codes():
    from jcodemunch_mcp.runtime.diagnostics_log import iter_diagnostics_from_text

    ds = list(iter_diagnostics_from_text((FIX / "ruff.json").read_text(encoding="utf-8")))
    assert ds and {d.tool for d in ds} == {"ruff"}
    assert {d.code for d in ds} >= {"F401"}
    assert {d.severity for d in ds} == {"warning"}  # a linter finding is not a type error
    assert all(d.line >= 1 for d in ds)


def test_explicit_format_overrides_detection():
    from jcodemunch_mcp.runtime.diagnostics_log import iter_diagnostics_from_text

    text = json.dumps({"file": "x.py", "line": 1, "severity": "error", "message": "m"}) + "\n"
    (d,) = list(iter_diagnostics_from_text(text, fmt="generic"))
    assert d.tool == "generic"
    with pytest.raises(ValueError):
        list(iter_diagnostics_from_text(text, fmt="not-a-format"))


# ──────────────────────────────────────────────────────────────────────
# Ingest: mapping
# ──────────────────────────────────────────────────────────────────────


def _ingest(db_path: Path, fixture: str, **kw):
    from jcodemunch_mcp.runtime.diagnostics_ingest import ingest_diagnostics_file

    return ingest_diagnostics_file(db_path=str(db_path), file_path=str(FIX / fixture), **kw)


def test_mypy_rows_map_to_the_innermost_symbol(tmp_path):
    _, _, db_path = _make_repo(tmp_path)
    result = _ingest(db_path, "mypy.jsonl")
    assert result["tool"] == "mypy"
    assert result["records"] == 5
    assert result["unmapped"] == 0, result["unmapped_reasons"]
    by_symbol = {r["symbol_id"]: r for r in _rows(db_path, "mypy")}
    names = {sid.split("::")[-1] for sid in by_symbol}
    # line 19 is `return 42` inside `inner`, which is nested inside `helper`:
    # the diagnostic belongs to inner, not to helper and not to Widget.
    assert any(n.endswith("inner#function") for n in names), names
    assert not any(n.endswith("helper#method") or n.endswith("helper#function") for n in names), names
    assert any(n.endswith("bad_call#function") for n in names)


def test_pyright_absolute_backslash_paths_resolve_by_suffix(tmp_path):
    _, _, db_path = _make_repo(tmp_path)
    result = _ingest(db_path, "pyright.json")
    assert result["tool"] == "pyright"
    assert result["mapped"] >= 3
    assert result["unmapped"] == 0, result["unmapped_reasons"]


def test_tsc_relative_to_subdir_resolves_by_suffix(tmp_path):
    _, _, db_path = _make_repo(tmp_path)
    result = _ingest(db_path, "tsc.txt")
    assert result["tool"] == "tsc"
    assert result["mapped"] == 2 and result["unmapped"] == 0, result
    names = {r["symbol_id"].split("::")[-1] for r in _rows(db_path, "tsc")}
    assert any(n.startswith("broken") for n in names)
    assert any("size" in n for n in names)


def test_a_line_outside_every_symbol_is_unmapped_with_a_reason(tmp_path):
    _, _, db_path = _make_repo(tmp_path)
    log = tmp_path / "g.jsonl"
    log.write_text(
        json.dumps({"file": "pkg/mod.py", "line": 1, "severity": "warning", "message": "unused import", "code": "F401", "tool": "x"}) + "\n"
        + json.dumps({"file": "nowhere/else.py", "line": 3, "severity": "error", "message": "m", "tool": "x"}) + "\n",
        encoding="utf-8",
    )
    from jcodemunch_mcp.runtime.diagnostics_ingest import ingest_diagnostics_file

    result = ingest_diagnostics_file(db_path=str(db_path), file_path=str(log))
    assert result["records"] == 2
    assert result["mapped"] == 0
    assert result["unmapped"] == 2
    assert set(result["unmapped_reasons"]) == {"no_enclosing_symbol", "file_not_indexed"}
    conn = sqlite3.connect(str(db_path))
    try:
        n = conn.execute("SELECT COUNT(*) FROM runtime_unmapped WHERE source = 'diagnostics:x'").fetchone()[0]
    finally:
        conn.close()
    assert n == 2


def test_a_deep_absolute_path_resolves_by_suffix(tmp_path):
    """Windows checkers emit C:/Users/<u>/AppData/Local/Temp/<tool>/<run>/...
    paths. The resolver's suffix walk was capped at 8 segments, so this
    twelve-segment path never reached `pkg/mod.py` and was unmapped."""
    _, _, db_path = _make_repo(tmp_path)
    deep = "C:\\Users\\u\\AppData\\Local\\Temp\\claude\\C--x\\0123abcd\\scratchpad\\diagfix\\pkg\\mod.py"
    assert deep.count("\\") >= 11
    log = tmp_path / "deep.jsonl"
    log.write_text(
        json.dumps({"file": deep, "line": 10, "severity": "error", "message": "m", "code": "E1", "tool": "x"}) + "\n"
        + json.dumps({"file": deep, "line": 1, "severity": "warning", "message": "w", "code": "W1", "tool": "x"}) + "\n",
        encoding="utf-8",
    )
    from jcodemunch_mcp.runtime.diagnostics_ingest import ingest_diagnostics_file

    result = ingest_diagnostics_file(db_path=str(db_path), file_path=str(log))
    assert result["mapped"] == 1, result
    # Line 1 is outside every symbol in a file that IS indexed: the reason
    # must say so, which needs the same suffix walk the resolver uses.
    assert result["unmapped_reasons"] == {"no_enclosing_symbol": 1}, result


def test_suffix_walk_is_bounded_by_the_path_not_a_constant(tmp_path):
    import sqlite3 as _sqlite3

    from jcodemunch_mcp.runtime.resolve import suffix_candidates
    from jcodemunch_mcp.storage.generation import connect_readonly

    _, _, db_path = _make_repo(tmp_path)
    conn = connect_readonly(db_path, isolation_level="")
    conn.row_factory = _sqlite3.Row
    try:
        forty = "/".join(f"seg{i}" for i in range(40)) + "/pkg/mod.py"
        assert suffix_candidates(conn, forty) == ["pkg/mod.py"]
        assert suffix_candidates(conn, forty, table="files", column="path", limit=1) == ["pkg/mod.py"]
        assert suffix_candidates(conn, "/".join(f"seg{i}" for i in range(40)) + "/nope.py") == []
    finally:
        conn.close()


# ──────────────────────────────────────────────────────────────────────
# Ingest: snapshot semantics
# ──────────────────────────────────────────────────────────────────────


def test_second_ingest_for_the_same_tool_replaces_its_rows(tmp_path):
    _, _, db_path = _make_repo(tmp_path)
    _ingest(db_path, "mypy.jsonl")
    before = {(r["symbol_id"], r["code"]) for r in _rows(db_path, "mypy")}
    assert len(before) == 5

    # The user fixed everything but bad_call: the new file carries ONE row.
    fixed = tmp_path / "mypy_fixed.jsonl"
    lines = (FIX / "mypy.jsonl").read_text(encoding="utf-8").splitlines()
    fixed.write_text(lines[0] + "\n", encoding="utf-8")
    from jcodemunch_mcp.runtime.diagnostics_ingest import ingest_diagnostics_file

    result = ingest_diagnostics_file(db_path=str(db_path), file_path=str(fixed))
    after = {(r["symbol_id"], r["code"]) for r in _rows(db_path, "mypy")}
    assert len(after) == 1
    assert after < before
    assert result["replaced"] == 5


def test_replacing_one_tool_leaves_another_tools_rows_alone(tmp_path):
    _, _, db_path = _make_repo(tmp_path)
    _ingest(db_path, "mypy.jsonl")
    _ingest(db_path, "tsc.txt")
    tsc_before = [(r["symbol_id"], r["code"]) for r in _rows(db_path, "tsc")]
    empty = tmp_path / "mypy_clean.jsonl"
    empty.write_text("", encoding="utf-8")
    from jcodemunch_mcp.runtime.diagnostics_ingest import ingest_diagnostics_file

    ingest_diagnostics_file(db_path=str(db_path), file_path=str(empty), fmt="mypy")
    assert _rows(db_path, "mypy") == []
    assert [(r["symbol_id"], r["code"]) for r in _rows(db_path, "tsc")] == tsc_before


def test_unmapped_rows_are_a_snapshot_per_tool_too(tmp_path):
    """A re-ingest that no longer carries an unmappable line removes its
    `runtime_unmapped` row; another tool's unmapped rows stay."""
    _, _, db_path = _make_repo(tmp_path)
    from jcodemunch_mcp.runtime.diagnostics_ingest import ingest_diagnostics_file

    def _write(name: str, rows: list[dict]) -> str:
        p = tmp_path / name
        p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        return str(p)

    bad_x = {"file": "nowhere/else.py", "line": 3, "severity": "error", "message": "m", "tool": "x"}
    bad_y = {"file": "nowhere/else.py", "line": 4, "severity": "error", "message": "m", "tool": "y"}
    good = {"file": "pkg/mod.py", "line": 10, "severity": "error", "message": "m", "tool": "x"}
    ingest_diagnostics_file(db_path=str(db_path), file_path=_write("x1.jsonl", [bad_x, good]))
    ingest_diagnostics_file(db_path=str(db_path), file_path=_write("y1.jsonl", [bad_y]))

    def _unmapped(source: str) -> int:
        conn = sqlite3.connect(str(db_path))
        try:
            return conn.execute("SELECT COUNT(*) FROM runtime_unmapped WHERE source = ?", (source,)).fetchone()[0]
        finally:
            conn.close()

    assert _unmapped("diagnostics:x") == 1 and _unmapped("diagnostics:y") == 1
    # x's second run: the unmappable line is gone.
    ingest_diagnostics_file(db_path=str(db_path), file_path=_write("x2.jsonl", [good]))
    assert _unmapped("diagnostics:x") == 0
    assert _unmapped("diagnostics:y") == 1


def test_ingest_stamps_git_head_or_none_never_a_guess(tmp_path):
    _, _, db_path = _make_repo(tmp_path)
    result = _ingest(db_path, "tsc.txt")
    # The fixture repo is not a git repository: the honest answer is None.
    assert result["git_head"] is None
    rows = _rows(db_path, "tsc")
    assert all(r["git_head"] is None for r in rows)
    assert all(r["ingested_at"] for r in rows)


def test_sample_message_is_redacted_at_the_chokepoint(tmp_path):
    _, _, db_path = _make_repo(tmp_path)
    log = tmp_path / "g.jsonl"
    log.write_text(
        json.dumps({"file": "pkg/mod.py", "line": 10, "severity": "error",
                    "message": "contact alice@example.com about arg type", "code": "E1", "tool": "x"}) + "\n",
        encoding="utf-8",
    )
    from jcodemunch_mcp.runtime.diagnostics_ingest import ingest_diagnostics_file

    result = ingest_diagnostics_file(db_path=str(db_path), file_path=str(log))
    assert result["redactions_fired"].get("email_address", 0) >= 1
    (row,) = _rows(db_path, "x")
    assert "alice@example.com" not in (row["sample_message"] or "")
    assert "arg type" in (row["sample_message"] or "")


def test_table_is_created_on_a_database_that_predates_it(tmp_path):
    """A pre-existing index has no ``diagnostics`` table. Ingest creates it;
    nothing bumps INDEX_VERSION, so no user's index is invalidated."""
    _, _, db_path = _make_repo(tmp_path)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("DROP TABLE IF EXISTS diagnostics")
        version_before = conn.execute("SELECT value FROM meta WHERE key='index_version'").fetchone()[0]
    finally:
        conn.close()
    result = _ingest(db_path, "tsc.txt")
    assert result["mapped"] == 2
    conn = sqlite3.connect(str(db_path))
    try:
        assert conn.execute("SELECT value FROM meta WHERE key='index_version'").fetchone()[0] == version_before
    finally:
        conn.close()


# ──────────────────────────────────────────────────────────────────────
# Wiring
# ──────────────────────────────────────────────────────────────────────


def test_import_runtime_signal_accepts_the_diagnostics_source(tmp_path):
    repo_id, storage, _ = _make_repo(tmp_path)
    from jcodemunch_mcp.runtime import VALID_SOURCES
    from jcodemunch_mcp.tools.import_runtime_signal import import_runtime_signal

    assert "diagnostics" in VALID_SOURCES
    result = import_runtime_signal(source="diagnostics", path=str(FIX / "mypy.jsonl"), repo=repo_id, storage_path=storage)
    assert result["success"] is True, result
    assert result["source"] == "diagnostics"
    assert result["tool"] == "mypy"
    assert result["mapped"] == 5


def test_import_runtime_signal_passes_an_explicit_format_through(tmp_path):
    repo_id, storage, _ = _make_repo(tmp_path)
    from jcodemunch_mcp.tools.import_runtime_signal import import_runtime_signal

    text = json.dumps({"file": "pkg/mod.py", "line": 10, "severity": "error", "message": "m"}) + "\n"
    log = tmp_path / "plain.log"
    log.write_text(text, encoding="utf-8")
    result = import_runtime_signal(source="diagnostics", path=str(log), repo=repo_id, storage_path=storage, format="generic")
    assert result["success"] is True, result
    assert result["tool"] == "generic"


def test_cli_import_trace_routes_the_diagnostics_flag(monkeypatch, capsys):
    """`import-trace --diagnostics <path> [--format <tool>]` reaches the tool
    with source='diagnostics' and the format passed through."""
    from jcodemunch_mcp import server
    from jcodemunch_mcp.tools import import_runtime_signal as mod

    seen: dict = {}

    def fake(**kw):
        seen.update(kw)
        return {"success": True, "tool": kw.get("format") or "mypy", "mapped": 0}

    monkeypatch.setattr(mod, "import_runtime_signal", fake)
    server.main(["import-trace", "--diagnostics", "x.jsonl", "--format", "mypy"])  # returns on success
    assert seen["source"] == "diagnostics"
    assert seen["path"] == "x.jsonl"
    assert seen["format"] == "mypy"


def test_cli_import_trace_still_refuses_two_sources(monkeypatch, capsys):
    from jcodemunch_mcp import server

    with pytest.raises(SystemExit) as exc:
        server.main(["import-trace", "--diagnostics", "x.jsonl", "--otel", "y.json"])
    assert exc.value.code == 2
    assert "--diagnostics" in capsys.readouterr().err
