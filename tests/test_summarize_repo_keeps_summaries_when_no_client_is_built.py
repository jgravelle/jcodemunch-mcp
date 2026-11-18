"""A forced refresh keeps stored summaries when the named provider cannot run (LEDGER L-274).

#1282 made a forced `summarize_repo` keep a stored summary for each symbol the
provider was ASKED for and did not answer. A provider that is configured but
never gets a client is never asked: a missing key under an explicit `minimax`,
`glm` or `openrouter`, or a remote `OPENAI_API_BASE` that the localhost rule
refuses. Then `_create_summarizer` returned None, the forced run cleared every
summary, the signature fallback was saved over each stored AI summary, and the
result read like a healthy run.

The user named a provider, so a signature fallback is not the answer they
asked for. Each symbol keeps what was stored, and the result names the
provider that could not run. With no provider named at all, the fallback is
the pipeline's answer and is written as before (the #1282 file pins that).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from jcodemunch_mcp.storage.sqlite_store import SQLiteIndexStore
from jcodemunch_mcp.summarizer import batch_summarize as bs
from jcodemunch_mcp.tools import summarize_repo as summarize_mod
from jcodemunch_mcp.tools.index_folder import index_folder

FILES = {
    "pkg/__init__.py": "",
    "pkg/work.py": "".join(f"def f{i}(x):\n    return x + {i}\n\n\n" for i in range(6)),
}


@pytest.fixture
def ai_summarized(tmp_path: Path, monkeypatch):
    """An index whose stored summaries came from a healthy AI run."""
    root = tmp_path / "repo"
    for rel, text in FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    storage = str(tmp_path / "store")
    res = index_folder(
        str(root), use_ai_summaries=False, storage_path=storage, force_reparse=True
    )
    assert res.get("success"), res
    repo = res["repo"]
    owner, name = repo.split("/", 1)
    db = SQLiteIndexStore(base_path=storage)._db_path(owner, name)

    def healthy(symbols, use_ai=True, repo=None, source_for=None, report=None):
        for sym in symbols:
            if not sym.summary:
                sym.summary = f"AI: {sym.name}"
        if report is not None:
            report["provider_failed"] = set()
        return symbols

    monkeypatch.setattr(summarize_mod, "summarize_symbols", healthy)
    assert summarize_mod.summarize_repo(repo, force=True, storage_path=storage)[
        "success"
    ]
    monkeypatch.setattr(summarize_mod, "summarize_symbols", bs.summarize_symbols)
    return repo, storage, db


def _summaries(db: Path) -> dict[str, str]:
    conn = sqlite3.connect(str(db))
    try:
        return {r[0]: r[1] for r in conn.execute("SELECT name, summary FROM symbols")}
    finally:
        conn.close()


def _ai_names(db: Path) -> set[str]:
    return {n for n, s in _summaries(db).items() if s.startswith("AI: ")}


def _unbuildable_minimax(monkeypatch):
    monkeypatch.setenv("JCODEMUNCH_SUMMARIZER_PROVIDER", "minimax")
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)


def _refused_remote_openai(monkeypatch):
    monkeypatch.setenv("JCODEMUNCH_SUMMARIZER_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_BASE", "https://example.invalid/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "placeholder")


@pytest.mark.parametrize(
    "configure,provider",
    [(_unbuildable_minimax, "minimax"), (_refused_remote_openai, "openai")],
    ids=["missing-key", "remote-base-refused"],
)
def test_a_forced_run_keeps_stored_summaries_when_the_provider_cannot_run(
    ai_summarized, monkeypatch, configure, provider
):
    repo, storage, db = ai_summarized
    before = _ai_names(db)
    assert len(before) >= 6, _summaries(db)
    configure(monkeypatch)
    assert bs._create_summarizer() is None, (
        "the fixture must leave the provider without a client"
    )

    out = summarize_mod.summarize_repo(repo, force=True, storage_path=storage)

    assert out["success"], out
    assert _ai_names(db) == before, _summaries(db)
    assert out["updated"] == 0, out
    assert out["provider_unavailable"] == provider, out
    assert out["previous_summaries_kept"] == len(before), out
    assert provider in out["provider_unavailable_reason"], out
    assert f"{len(before)} symbol(s) kept" in out["provider_unavailable_reason"], out
    assert "provider_fallbacks" not in out, "no request was sent, so nothing failed"


def test_a_symbol_with_no_stored_summary_still_gets_its_fallback(tmp_path, monkeypatch):
    """Nothing to keep: the fallback fills the blank, and nothing is counted as kept."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.py").write_text("def a(x):\n    return x\n", encoding="utf-8")
    storage = str(tmp_path / "store")
    res = index_folder(
        str(root), use_ai_summaries=False, storage_path=storage, force_reparse=True
    )
    _unbuildable_minimax(monkeypatch)
    out = summarize_mod.summarize_repo(res["repo"], force=True, storage_path=storage)
    assert out["success"], out
    assert out["provider_unavailable"] == "minimax", out
    assert "previous_summaries_kept" not in out, out
    assert "kept" not in out["provider_unavailable_reason"], (
        "nothing was kept, so the reason must not say so"
    )


def test_with_no_provider_named_the_report_names_none(ai_summarized, monkeypatch):
    """The other side of the boundary: no provider configured is not an unavailable one."""
    repo, storage, db = ai_summarized
    monkeypatch.delenv("JCODEMUNCH_SUMMARIZER_PROVIDER", raising=False)
    for var, _name in bs._AUTO_DETECT_ORDER:
        monkeypatch.delenv(var, raising=False)
    out = summarize_mod.summarize_repo(repo, force=True, storage_path=storage)
    assert out["success"], out
    assert "provider_unavailable" not in out, out
    assert not _ai_names(db), "with no provider the forced run resets to the fallback"
