"""Phase 6 tests: HTTP live-ingest endpoint.

Covers:
- Stream parsers (iter_otel_from_text / iter_sql_from_text /
  iter_stack_from_text) yield exactly the same records as the file-based
  parsers given equivalent input.
- ingest_*_stream orchestrators are interchangeable with ingest_*_file —
  same row counts, same redaction labels, same envelope.
- HTTP routes:
    * 503 when JCODEMUNCH_RUNTIME_INGEST_ENABLED is unset
    * 400 when no repo identifier is given
    * 404 when the repo isn't indexed
    * 413 when the body exceeds the configured size cap
    * 200 + ingest envelope on the happy path for all three routes
- Repo selection works from both X-JCM-Repo header and ?repo= query
- Content-Encoding: gzip is decompressed before parsing
- get_redaction_log MCP tool surfaces the runtime_redaction_log rows
  that the live-ingest path populated.
"""

from __future__ import annotations

import gzip
import json
import sqlite3
from pathlib import Path

import pytest
from importlib.util import find_spec


# Pretend the http extra is installed for the test session.
# ⚠⚠ `find_spec`, NOT `pytest.importorskip`. At module scope importorskip
# RAISES during import, so this whole file would collapse to a single
# "1 skipped" line however many tests it holds -- a CI box quietly missing
# starlette would look like a healthy run rather than one 17 tests short.
# find_spec asks the same question without raising: every test is collected
# and skipped individually. The imports below need the package present, so
# they move under the flag.
_HAS_STARLETTE = find_spec("starlette") is not None
pytestmark = pytest.mark.skipif(
    not _HAS_STARLETTE, reason="starlette not installed"
)

if _HAS_STARLETTE:
    from starlette.applications import Starlette
    from starlette.testclient import TestClient

    from jcodemunch_mcp.runtime import (
        iter_otel_from_text,
        iter_sql_from_text,
        iter_stack_from_text,
        ingest_otel_file,
        ingest_otel_stream,
        ingest_sql_log_file,
        ingest_sql_log_stream,
        ingest_stack_log_file,
        ingest_stack_log_stream,
    )
    from jcodemunch_mcp.runtime.http_routes import make_runtime_routes
    from jcodemunch_mcp.storage.sqlite_store import SQLiteIndexStore
    from jcodemunch_mcp.tools.get_redaction_log import get_redaction_log


# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def app(tmp_path, monkeypatch):
    """A Starlette app with just the Phase 6 routes mounted; ingest enabled.

    CODE_INDEX_PATH is pinned to tmp_path so each test gets a fresh
    isolated index store.
    """
    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path))
    monkeypatch.setenv("JCODEMUNCH_RUNTIME_INGEST_ENABLED", "1")
    # Second key of the two-key turn: a token must be set or the write endpoint
    # fails closed (v1.108.73). The route tests exercise the happy path, so set it.
    monkeypatch.setenv("JCODEMUNCH_HTTP_TOKEN", "test-token")
    # Reload config so the env var takes effect.
    from jcodemunch_mcp import config as cfg
    cfg.load_config()
    starlette_app = Starlette(routes=make_runtime_routes())
    return starlette_app


@pytest.fixture
def disabled_app(tmp_path, monkeypatch):
    """A Starlette app with the routes mounted but ingest *not* enabled."""
    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path))
    monkeypatch.delenv("JCODEMUNCH_RUNTIME_INGEST_ENABLED", raising=False)
    from jcodemunch_mcp import config as cfg
    cfg.load_config()
    starlette_app = Starlette(routes=make_runtime_routes())
    return starlette_app


def _seed_index(tmp_path: Path) -> tuple[SQLiteIndexStore, Path, str, str]:
    """Index a small repo with symbols hit by all three live-ingest routes."""
    store = SQLiteIndexStore(base_path=str(tmp_path))
    db_path = store._db_path("local", "phase6")
    conn = store._connect(db_path)
    try:
        conn.executescript(
            """
            INSERT INTO symbols (id, file, name, kind, line, end_line) VALUES
                ('app/handlers.py::process_request#function', 'app/handlers.py', 'process_request', 'function', 10, 30),
                ('app/handlers.py::validate_input#function', 'app/handlers.py', 'validate_input', 'function', 35, 55),
                ('app/db.py::query#function', 'app/db.py', 'query', 'function', 1, 100),
                ('models/fact_orders.sql::fact_orders#table', 'models/fact_orders.sql', 'fact_orders', 'table', 1, 50);
            """
        )
        ctx = {"dbt_columns": {"fact_orders": {"order_id": "PK"}}}
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('context_metadata', ?)",
            (json.dumps(ctx),),
        )
        conn.commit()
    finally:
        conn.close()
    return store, db_path, "local", "phase6"


# ──────────────────────────────────────────────────────────────────────
# Stream-parser equivalence
# ──────────────────────────────────────────────────────────────────────


def _otel_envelope(file_path: str, line_no: int, function_name: str) -> dict:
    return {
        "resourceSpans": [{
            "resource": {"attributes": []},
            "scopeSpans": [{
                "scope": {"name": "test"},
                "spans": [{
                    "traceId": "a", "spanId": "b", "name": f"GET /{function_name}",
                    "startTimeUnixNano": "1000000000000000000",
                    "endTimeUnixNano": "1000000000001000000",
                    "attributes": [
                        {"key": "code.filepath", "value": {"stringValue": file_path}},
                        {"key": "code.lineno", "value": {"intValue": str(line_no)}},
                        {"key": "code.function", "value": {"stringValue": function_name}},
                    ],
                }],
            }],
        }]
    }


def test_iter_otel_from_text_matches_file_parser(tmp_path):
    """File and stream parsers must emit identical OtelSpan sequences."""
    from jcodemunch_mcp.runtime import parse_otel_file
    payload = _otel_envelope("app/handlers.py", 12, "process_request")
    text = json.dumps(payload) + "\n" + json.dumps(_otel_envelope("app/db.py", 5, "query")) + "\n"
    p = tmp_path / "trace.jsonl"
    p.write_text(text)
    file_spans = list(parse_otel_file(str(p)))
    text_spans = list(iter_otel_from_text(text))
    assert len(file_spans) == 2
    assert len(text_spans) == 2
    assert [(s.file_path, s.line_no, s.function_name) for s in file_spans] == \
           [(s.file_path, s.line_no, s.function_name) for s in text_spans]


def test_iter_sql_from_text_jsonl_matches_file_parser(tmp_path):
    from jcodemunch_mcp.runtime import parse_sql_log_file
    text = '{"sql": "SELECT * FROM fact_orders", "calls": 7}\n'
    p = tmp_path / "queries.jsonl"
    p.write_text(text)
    file_records = list(parse_sql_log_file(str(p)))
    text_records = list(iter_sql_from_text(text, fmt="jsonl"))
    assert len(file_records) == len(text_records) == 1
    assert file_records[0].sql == text_records[0].sql
    assert file_records[0].calls == text_records[0].calls == 7


def test_iter_stack_from_text_plain_matches_file_parser(tmp_path):
    from jcodemunch_mcp.runtime import parse_stack_log_file
    text = (
        '2026-05-10T12:00:00Z ERROR oh no:\n'
        'Traceback (most recent call last):\n'
        '  File "app/handlers.py", line 12, in process_request\n'
        '    pass\n'
        'ValueError: x\n'
    )
    p = tmp_path / "app.log"
    p.write_text(text)
    file_events = list(parse_stack_log_file(str(p)))
    text_events = list(iter_stack_from_text(text, fmt="plain"))
    assert len(file_events) == len(text_events) == 1
    assert file_events[0].severity == text_events[0].severity == "error"
    assert len(file_events[0].frames) == len(text_events[0].frames) == 1


# ──────────────────────────────────────────────────────────────────────
# ingest_*_stream equivalence with ingest_*_file
# ──────────────────────────────────────────────────────────────────────


def test_ingest_otel_stream_matches_file_envelope(tmp_path):
    store, db_path, owner, name = _seed_index(tmp_path)
    text = json.dumps(_otel_envelope("app/handlers.py", 12, "process_request")) + "\n"

    p = tmp_path / "trace.jsonl"
    p.write_text(text)
    file_result = ingest_otel_file(db_path=str(db_path), file_path=str(p))

    # Reset the runtime tables so the second ingest starts clean.
    conn = sqlite3.connect(str(db_path))
    conn.execute("DELETE FROM runtime_calls")
    conn.execute("DELETE FROM runtime_unmapped")
    conn.execute("DELETE FROM runtime_redaction_log")
    conn.commit()
    conn.close()

    stream_result = ingest_otel_stream(db_path=str(db_path), text=text)

    # Drop the eviction count (depends on prior table state) when comparing,
    # and the ingest id (#875: one per run by design, so two runs differ).
    for d in (file_result, stream_result):
        d.pop("evicted", None)
        assert d.pop("ingest_id", None), "every ingest names itself"
    assert file_result == stream_result


# ──────────────────────────────────────────────────────────────────────
# HTTP routes — gating
# ──────────────────────────────────────────────────────────────────────


def test_returns_503_when_ingest_disabled(disabled_app, tmp_path):
    _seed_index(tmp_path)
    client = TestClient(disabled_app)
    resp = client.post("/runtime/otel", content="{}", headers={"X-JCM-Repo": "local/phase6"})
    assert resp.status_code == 503
    assert "disabled" in resp.json()["error"]


def test_returns_400_when_no_repo(app):
    client = TestClient(app)
    resp = client.post("/runtime/otel", content="{}")
    assert resp.status_code == 400
    assert "repo identifier" in resp.json()["error"]


def test_returns_404_when_repo_not_indexed(app):
    client = TestClient(app)
    resp = client.post(
        "/runtime/otel",
        content="{}",
        headers={"X-JCM-Repo": "local/never-existed"},
    )
    assert resp.status_code == 404
    assert "not indexed" in resp.json()["error"]


def test_returns_413_on_oversized_body(app, tmp_path, monkeypatch):
    _seed_index(tmp_path)
    # Shrink the body cap so the test is cheap.
    monkeypatch.setenv("JCODEMUNCH_RUNTIME_INGEST_MAX_BODY_BYTES", "1024")
    from jcodemunch_mcp import config as cfg
    cfg.load_config()
    client = TestClient(app)
    big_payload = "x" * 4096
    resp = client.post(
        "/runtime/otel",
        content=big_payload,
        headers={"X-JCM-Repo": "local/phase6"},
    )
    assert resp.status_code == 413
    assert "too large" in resp.json()["error"]


def test_unknown_fmt_query_param_is_400(app, tmp_path):
    _seed_index(tmp_path)
    client = TestClient(app)
    resp = client.post(
        "/runtime/sql?fmt=garbage",
        content='{"sql":"SELECT 1 FROM fact_orders"}',
        headers={"X-JCM-Repo": "local/phase6"},
    )
    assert resp.status_code == 400


# ──────────────────────────────────────────────────────────────────────
# HTTP routes — happy path
# ──────────────────────────────────────────────────────────────────────


def test_otel_route_ingests_via_header(app, tmp_path):
    _seed_index(tmp_path)
    payload = json.dumps(_otel_envelope("app/handlers.py", 12, "process_request"))
    client = TestClient(app)
    resp = client.post(
        "/runtime/otel",
        content=payload,
        headers={"X-JCM-Repo": "local/phase6"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert body["repo"] == "local/phase6"
    assert body["source"] == "otel"
    assert body["records"] == 1
    assert body["mapped"] == 1


def test_otel_route_accepts_query_repo_param(app, tmp_path):
    _seed_index(tmp_path)
    payload = json.dumps(_otel_envelope("app/handlers.py", 12, "process_request"))
    client = TestClient(app)
    resp = client.post(
        "/runtime/otel?repo=local/phase6",
        content=payload,
    )
    assert resp.status_code == 200
    assert resp.json()["mapped"] == 1


def test_sql_route_ingests_jsonl(app, tmp_path):
    _seed_index(tmp_path)
    payload = '{"sql":"SELECT order_id FROM fact_orders","calls":3}\n'
    client = TestClient(app)
    resp = client.post(
        "/runtime/sql",
        content=payload,
        headers={"X-JCM-Repo": "local/phase6"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source"] == "sql_log"
    assert body["records"] == 1
    assert body["mapped"] >= 3
    assert body.get("columns_recorded", 0) >= 1


def test_stack_route_ingests_python_traceback(app, tmp_path):
    _seed_index(tmp_path)
    payload = (
        '2026-05-10T12:00:00Z ERROR boom:\n'
        'Traceback (most recent call last):\n'
        '  File "app/handlers.py", line 12, in process_request\n'
        '    pass\n'
        'ValueError: x\n'
    )
    client = TestClient(app)
    resp = client.post(
        "/runtime/stack",
        content=payload,
        headers={"X-JCM-Repo": "local/phase6"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source"] == "stack_log"
    assert body["frames"] >= 1
    assert body["mapped"] >= 1
    assert body["severity_counts"]["error"] >= 1


def test_otel_route_handles_gzip_content_encoding(app, tmp_path):
    _seed_index(tmp_path)
    payload = json.dumps(_otel_envelope("app/handlers.py", 12, "process_request")).encode()
    gz = gzip.compress(payload)
    client = TestClient(app)
    resp = client.post(
        "/runtime/otel",
        content=gz,
        headers={
            "X-JCM-Repo": "local/phase6",
            "Content-Encoding": "gzip",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["mapped"] == 1


# ──────────────────────────────────────────────────────────────────────
# get_redaction_log MCP tool
# ──────────────────────────────────────────────────────────────────────


def test_get_redaction_log_surfaces_live_ingest_redactions(app, tmp_path):
    _seed_index(tmp_path)
    payload = (
        '2026-05-10T12:00:00Z ERROR contacting alice@example.com:\n'
        'Traceback (most recent call last):\n'
        '  File "app/handlers.py", line 12, in process_request\n'
        '    pass\n'
        'ValueError: contacting alice@example.com\n'
    )
    client = TestClient(app)
    resp = client.post(
        "/runtime/stack",
        content=payload,
        headers={"X-JCM-Repo": "local/phase6"},
    )
    assert resp.status_code == 200
    out = get_redaction_log(repo="local/phase6", storage_path=str(tmp_path))
    assert "error" not in out, out
    assert out["total_redactions"] >= 1
    assert "stack_log" in out["sources"]
    labels = {p["pattern"] for p in out["patterns"]}
    assert "email_address" in labels


def test_get_redaction_log_filters_by_source(app, tmp_path):
    _seed_index(tmp_path)
    # Fire a stack-log ingest so there's at least one source row.
    payload = (
        'ERROR something:\n'
        'Traceback (most recent call last):\n'
        '  File "app/handlers.py", line 12, in process_request\n'
        '    pass\n'
        'ValueError: alice@example.com\n'
    )
    TestClient(app).post(
        "/runtime/stack",
        content=payload,
        headers={"X-JCM-Repo": "local/phase6"},
    )
    out = get_redaction_log(repo="local/phase6", source="otel", storage_path=str(tmp_path))
    # No otel ingests happened → no patterns surface for that source.
    assert out["total_redactions"] == 0


def test_get_redaction_log_rejects_unknown_source(tmp_path):
    _seed_index(tmp_path)
    out = get_redaction_log(repo="local/phase6", source="bogus", storage_path=str(tmp_path))
    assert "error" in out
    assert "unknown source" in out["error"]
