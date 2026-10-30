"""A forced summary refresh keeps what it had when the provider fails (#1282, @swf1996120).

`summarize_repo(force=true)` cleared every summary before calling the provider.
When the provider failed (a 429, an error body, the circuit breaker tripping
after three failed batches), the summarizer gave each remaining symbol its
signature fallback, and that fallback was saved over the AI summary it
replaced. The result read `success: true` with `updated` equal to the symbol
count, the same as a healthy run, and a later plain `summarize_repo` found
nothing to do, so the summaries came back only with a second paid forced run.

Now a symbol whose new summary is only its signature fallback keeps its stored
summary when a provider is configured, `updated` counts summaries that differ
from what was stored, and the result names the provider fallbacks. With no
provider configured a forced run resets to the fallback as before.

The provider is replaced by a function that writes the fallback, the way the
summarizer does after a failed batch; everything else is the real indexer, the
real store and the real `summarize_repo`.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from jcodemunch_mcp.storage.sqlite_store import SQLiteIndexStore
from jcodemunch_mcp.summarizer.batch_summarize import signature_fallback
from jcodemunch_mcp.tools import summarize_repo as summarize_mod
from jcodemunch_mcp.tools.index_folder import index_folder

FILES = {
    "pkg/__init__.py": "",
    "pkg/work.py": "".join(f"def f{i}(x):\n    return x + {i}\n\n\n" for i in range(6)),
}


@pytest.fixture
def indexed(tmp_path: Path):
    root = tmp_path / "repo"
    for rel, text in FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    storage = str(tmp_path / "store")
    res = index_folder(str(root), use_ai_summaries=False, storage_path=storage, force_reparse=True)
    assert res.get("success"), res
    repo = res["repo"]
    owner, name = repo.split("/", 1)
    db = SQLiteIndexStore(base_path=storage)._db_path(owner, name)
    return repo, storage, db


def _summaries(db: Path) -> dict[str, str]:
    conn = sqlite3.connect(str(db))
    try:
        return {r[0]: r[1] for r in conn.execute("SELECT name, summary FROM symbols")}
    finally:
        conn.close()


def _healthy(symbols, use_ai=True, repo=None, source_for=None):
    for sym in symbols:
        if not sym.summary:
            sym.summary = f"AI: {sym.name}"
    return symbols


def _failing(symbols, use_ai=True, repo=None, source_for=None):
    """What the summarizer leaves after the circuit breaker trips."""
    for sym in symbols:
        if not sym.summary:
            sym.summary = signature_fallback(sym)
    return symbols


def _half_failing(symbols, use_ai=True, repo=None, source_for=None):
    for i, sym in enumerate(symbols):
        if not sym.summary:
            sym.summary = f"AI2: {sym.name}" if i % 2 else signature_fallback(sym)
    return symbols


@pytest.fixture
def provider(monkeypatch):
    """A configured provider, as `resolve_provider_name` sees it."""
    monkeypatch.setattr(summarize_mod, "resolve_provider_name", lambda repo=None: "openai", raising=False)


def test_a_failed_forced_run_keeps_the_stored_ai_summaries(indexed, monkeypatch, provider):
    """The reporter's sequence: healthy, then failing, then healthy again."""
    repo, storage, db = indexed
    monkeypatch.setattr(summarize_mod, "summarize_symbols", _healthy)
    first = summarize_mod.summarize_repo(repo, force=True, storage_path=storage)
    assert first["success"]
    stored = _summaries(db)
    n_ai = sum(1 for s in stored.values() if s.startswith("AI: "))
    assert n_ai >= 6, stored

    monkeypatch.setattr(summarize_mod, "summarize_symbols", _failing)
    out = summarize_mod.summarize_repo(repo, force=True, storage_path=storage)
    assert _summaries(db) == stored, "a provider failure overwrote stored AI summaries"
    assert out["updated"] == 0, out
    assert out["provider_fallbacks"] == n_ai, out
    assert out["previous_summaries_kept"] == n_ai, out
    assert "provider" in out["previous_summaries_kept_reason"], out


def test_a_partly_failed_run_keeps_only_the_failed_symbols_old_summaries(indexed, monkeypatch, provider):
    repo, storage, db = indexed
    monkeypatch.setattr(summarize_mod, "summarize_symbols", _healthy)
    summarize_mod.summarize_repo(repo, force=True, storage_path=storage)
    monkeypatch.setattr(summarize_mod, "summarize_symbols", _half_failing)
    out = summarize_mod.summarize_repo(repo, force=True, storage_path=storage)
    after = _summaries(db)
    assert all(s.startswith(("AI: ", "AI2: ")) for s in after.values()), after
    assert any(s.startswith("AI2: ") for s in after.values()), after
    assert out["updated"] == sum(1 for s in after.values() if s.startswith("AI2: ")), out
    assert out["previous_summaries_kept"] == sum(1 for s in after.values() if s.startswith("AI: ")), out


def test_a_symbol_with_no_stored_summary_still_gets_its_fallback(indexed, monkeypatch, provider):
    """Nothing to keep: the fallback is written and counted as a provider fallback."""
    repo, storage, db = indexed
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("UPDATE symbols SET summary = ''")
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(summarize_mod, "summarize_symbols", _failing)
    out = summarize_mod.summarize_repo(repo, force=True, storage_path=storage)
    assert all(_summaries(db).values()), "a symbol was left with no summary"
    assert out["provider_fallbacks"] == out["symbol_count"], out
    assert "previous_summaries_kept" not in out, out


def test_with_no_provider_a_forced_run_resets_to_the_fallback(indexed, monkeypatch):
    """Unchanged: with no provider configured, the fallback is the summary."""
    repo, storage, db = indexed
    monkeypatch.setattr(summarize_mod, "resolve_provider_name", lambda repo=None: "openai", raising=False)
    monkeypatch.setattr(summarize_mod, "summarize_symbols", _healthy)
    summarize_mod.summarize_repo(repo, force=True, storage_path=storage)
    monkeypatch.setattr(summarize_mod, "resolve_provider_name", lambda repo=None: None, raising=False)
    monkeypatch.setattr(summarize_mod, "summarize_symbols", _failing)
    out = summarize_mod.summarize_repo(repo, force=True, storage_path=storage)
    assert not any(s.startswith("AI: ") for s in _summaries(db).values())
    assert "provider_fallbacks" not in out and "previous_summaries_kept" not in out, out


def test_control_a_healthy_forced_run_reports_no_fallbacks(indexed, monkeypatch, provider):
    repo, storage, db = indexed
    monkeypatch.setattr(summarize_mod, "summarize_symbols", _healthy)
    out = summarize_mod.summarize_repo(repo, force=True, storage_path=storage)
    assert out["updated"] == out["symbol_count"], out
    assert "provider_fallbacks" not in out and "previous_summaries_kept" not in out, out


def test_resolve_provider_name_is_the_one_rule(monkeypatch):
    """`_create_summarizer` and `summarize_repo` answer "is a provider configured" the same way."""
    from jcodemunch_mcp.summarizer import batch_summarize as bs

    monkeypatch.setattr(bs, "get_provider_name", lambda repo=None: "openai")
    monkeypatch.setattr(bs._config, "get", lambda key, default=None, repo=None: "false" if key == "use_ai_summaries" else default)
    assert bs.resolve_provider_name() is None
    assert bs._create_summarizer() is None
    monkeypatch.setattr(bs._config, "get", lambda key, default=None, repo=None: "auto" if key == "use_ai_summaries" else default)
    assert bs.resolve_provider_name() == "openai"


class _Endpoint:
    """A local OpenAI-compatible endpoint whose answer is switched between calls."""

    def __init__(self):
        import http.server
        import threading

        self.mode = "healthy"
        self.calls = 0
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                import json

                length = int(self.headers.get("Content-Length", 0))
                self.rfile.read(length)
                outer.calls += 1
                if outer.mode == "429":
                    body = json.dumps({"error": {"message": "rate limited"}}).encode()
                    self.send_response(429)
                else:
                    text = "\n".join(f"{i}. AI summary {i}" for i in range(1, 51))
                    body = json.dumps({"choices": [{"message": {"content": text}}]}).encode()
                    self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def test_the_reported_sequence_through_the_real_summarizer(tmp_path, monkeypatch):
    """The report's three calls, against a real local endpoint: healthy, 429, healthy."""
    endpoint = _Endpoint()
    try:
        monkeypatch.setenv("JCODEMUNCH_SUMMARIZER_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_API_BASE", f"http://127.0.0.1:{endpoint.port}/v1")
        monkeypatch.setenv("OPENAI_API_KEY", "placeholder")
        monkeypatch.setenv("OPENAI_CONCURRENCY", "1")
        root = tmp_path / "repo"
        root.mkdir()
        (root / "many.py").write_text(
            "".join(f"def g{i}(x):\n    return x * {i}\n\n\n" for i in range(45)), encoding="utf-8"
        )
        storage = str(tmp_path / "store")
        repo = index_folder(str(root), use_ai_summaries=False, storage_path=storage, force_reparse=True)["repo"]
        owner, name = repo.split("/", 1)
        db = SQLiteIndexStore(base_path=storage)._db_path(owner, name)

        def ai_count():
            return sum(1 for s in _summaries(db).values() if s.startswith("AI summary"))

        healthy = summarize_mod.summarize_repo(repo, force=True, storage_path=storage)
        assert healthy["updated"] == 45 and ai_count() == 45, healthy

        endpoint.mode = "429"
        failed = summarize_mod.summarize_repo(repo, force=True, storage_path=storage)
        assert ai_count() == 45, "the 429 run overwrote stored AI summaries"
        assert failed["updated"] == 0, failed
        assert failed["provider_fallbacks"] == 45 and failed["previous_summaries_kept"] == 45, failed
    finally:
        endpoint.close()
