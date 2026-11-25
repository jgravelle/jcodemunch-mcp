"""A forced re-index keeps stored summaries when the named provider cannot run (LEDGER L-323).

L-274 made a forced `summarize_repo` keep each stored summary when the
configured provider never gets a client (a missing key, a remote base the
localhost rule refuses). The indexer has the same exposure and did not get the
rule: `index_folder(paths=..., force_reparse=True)`, which `refresh` and the
`paths=` route use, re-parses files whose content did not change, re-summarizes
their symbols, and wrote each symbol's signature fallback over the AI summary it
had, with nothing in the result to say why.

A symbol whose body is unchanged keeps what was stored, and the result names
the provider that could not run. A symbol whose body changed is not given a
summary of its old body.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from jcodemunch_mcp.storage.sqlite_store import SQLiteIndexStore
from jcodemunch_mcp.summarizer import batch_summarize as bs
from jcodemunch_mcp.tools import summarize_repo as summarize_mod
from jcodemunch_mcp.parser.symbols import Symbol
from jcodemunch_mcp.tools.index_file import index_file
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
    return root, storage, db


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
def test_a_forced_reindex_keeps_stored_summaries_when_the_provider_cannot_run(
    ai_summarized, monkeypatch, configure, provider
):
    root, storage, db = ai_summarized
    before = _ai_names(db)
    assert len(before) >= 6, _summaries(db)
    configure(monkeypatch)
    assert bs._create_summarizer() is None, (
        "the fixture must leave the provider without a client"
    )

    out = index_folder(
        str(root), use_ai_summaries=True, storage_path=storage,
        paths=["pkg/work.py"], force_reparse=True,
    )

    assert out.get("success"), out
    assert _ai_names(db) == before, _summaries(db)
    assert out["provider_unavailable"] == provider, out
    assert out["previous_summaries_kept"] == len(before), out
    assert provider in out["provider_unavailable_reason"], out


def test_a_full_reindex_keeps_the_summaries_of_unchanged_bodies(ai_summarized, monkeypatch):
    """The full path: a file that changed is re-summarized, and the bodies in it that
    did not change keep what was stored. The new function gets its fallback."""
    root, storage, db = ai_summarized
    before = _ai_names(db)
    work = root / "pkg" / "work.py"
    work.write_text(
        work.read_text(encoding="utf-8") + "def g(x):\n    return x\n", encoding="utf-8"
    )
    _unbuildable_minimax(monkeypatch)

    out = index_folder(str(root), use_ai_summaries=True, storage_path=storage, incremental=False)

    assert out.get("success"), out
    after = _summaries(db)
    assert _ai_names(db) == before, after
    assert after["g"] == "def g(x)", after
    assert out["previous_summaries_kept"] == len(before), out
    assert out["provider_unavailable"] == "minimax", out


def test_a_changed_body_is_not_given_its_old_summary(ai_summarized, monkeypatch):
    root, storage, db = ai_summarized
    work = root / "pkg" / "work.py"
    work.write_text(
        work.read_text(encoding="utf-8").replace("return x + 0", "return x * 0"),
        encoding="utf-8",
    )
    _unbuildable_minimax(monkeypatch)

    out = index_folder(
        str(root), use_ai_summaries=True, storage_path=storage,
        paths=["pkg/work.py"], force_reparse=True,
    )

    assert out.get("success"), out
    after = _summaries(db)
    assert after["f0"] == "def f0(x)", "a summary of the old body must not survive an edit"
    assert {n for n in after if after[n].startswith("AI: ")} == {f"f{i}" for i in range(1, 6)}
    assert out["previous_summaries_kept"] == 5, out


def test_a_provider_that_fails_on_the_index_path_keeps_stored_summaries(ai_summarized, monkeypatch):
    """#1282's shape on the indexer: the provider is asked and answers nothing."""
    from jcodemunch_mcp.tools import _indexing_pipeline as pipeline

    root, storage, db = ai_summarized
    before = _ai_names(db)

    def failing(symbols, use_ai=True, repo=None, source_for=None, report=None):
        for sym in symbols:
            if not sym.summary:
                sym.summary = bs.signature_fallback(sym)
        if report is not None:
            report["provider_failed"] = {s.id for s in symbols}
        return symbols

    monkeypatch.setattr(pipeline, "summarize_symbols", failing)
    out = index_folder(
        str(root), use_ai_summaries=True, storage_path=storage,
        paths=["pkg/work.py"], force_reparse=True,
    )

    assert out.get("success"), out
    assert _ai_names(db) == before, _summaries(db)
    assert out["provider_fallbacks"] >= len(before), out
    assert out["previous_summaries_kept"] == len(before), out
    assert "provider_unavailable" not in out, out


def test_with_no_provider_named_the_fallback_is_written(ai_summarized, monkeypatch):
    """The boundary: no provider configured is the pipeline's answer, not an unavailable one."""
    root, storage, db = ai_summarized
    monkeypatch.setattr(bs, "_named_provider", lambda repo=None: None)
    monkeypatch.setattr(bs, "_create_summarizer", lambda repo=None: None)

    out = index_folder(
        str(root), use_ai_summaries=True, storage_path=storage,
        paths=["pkg/work.py"], force_reparse=True,
    )

    assert out.get("success"), out
    assert _ai_names(db) == set(), _summaries(db)
    assert "provider_unavailable" not in out, out
    assert "previous_summaries_kept" not in out, out


def test_the_edit_route_keeps_the_summaries_of_untouched_bodies(ai_summarized, monkeypatch):
    """`index_file`, which the edit hook runs, re-summarizes the whole file it is given."""
    root, storage, db = ai_summarized
    work = root / "pkg" / "work.py"
    work.write_text(
        work.read_text(encoding="utf-8").replace("return x + 0", "return x * 0"),
        encoding="utf-8",
    )
    _unbuildable_minimax(monkeypatch)

    out = index_file(str(work), use_ai_summaries=True, storage_path=storage)

    assert out.get("success"), out
    after = _summaries(db)
    assert after["f0"] == "def f0(x)", after
    assert {n for n in after if after[n].startswith("AI: ")} == {f"f{i}" for i in range(1, 6)}
    assert out["previous_summaries_kept"] == 5, out
    assert out["provider_unavailable"] == "minimax", out


def _bare(content_hash: str) -> Symbol:
    sym = Symbol(
        id="a.cbl::PARA#function", file="a.cbl", name="PARA", qualified_name="PARA",
        kind="function", language="cobol", signature="PARA.", content_hash=content_hash,
    )
    sym.summary = bs.signature_fallback(sym)
    return sym


@pytest.mark.parametrize(
    "parsed_hash,stored_hash",
    [("", "deadbeef"), ("newhash", ""), ("", "")],
    ids=["parsed-has-no-hash", "stored-has-no-hash", "neither-has-a-hash"],
)
@pytest.mark.parametrize("report", [{"provider_unavailable": "minimax"}, None], ids=["unavailable", "failed"])
def test_a_reparse_keeps_nothing_it_cannot_compare(parsed_hash, stored_hash, report):
    """Several extractors build symbols with no content hash, and migrated rows store ''.
    A comparison that could not be made is not an unchanged body."""
    sym = _bare(parsed_hash)
    fallback = sym.summary
    if report is None:
        report = {"provider_failed": {sym.id}}
    counts = bs.keep_stored_summaries(
        [sym], {sym.id: ("AI summary of the old body", stored_hash)}, report, require_hash=True,
    )
    assert sym.summary == fallback
    assert counts["failed_kept"] == counts["unavailable_kept"] == 0


def test_a_reparse_keeps_a_summary_whose_hash_matches():
    """The positive half, so the guard above cannot pass by never keeping anything."""
    sym = _bare("samehash")
    counts = bs.keep_stored_summaries(
        [sym], {sym.id: ("AI summary", "samehash")}, {"provider_unavailable": "minimax"},
        require_hash=True,
    )
    assert sym.summary == "AI summary"
    assert counts["unavailable_kept"] == 1


@pytest.mark.parametrize("stored_hash", ["deadbeef", ""], ids=["stored-hashed", "stored-unhashed"])
def test_the_reparse_path_itself_refuses_a_hashless_keep(monkeypatch, stored_hash):
    """The pipeline call site, not the helper: a re-parse of a symbol with no
    content hash (an extractor that records none) keeps no stored summary.
    Python symbols always carry a hash, so the index-level tests cannot see this."""
    from jcodemunch_mcp.tools import _indexing_pipeline as pipeline

    def failing(symbols, use_ai=True, repo=None, source_for=None, report=None):
        for sym in symbols:
            sym.summary = bs.signature_fallback(sym)
        if report is not None:
            report["provider_failed"] = {s.id for s in symbols}
        return symbols

    monkeypatch.setattr(pipeline, "summarize_symbols", failing)
    sym = _bare("")
    sym.summary = ""
    report: dict = {}
    out = pipeline.summarize_with_kept(
        [sym], True, None, None, {sym.id: ("AI summary of the old body", stored_hash)}, report,
    )

    assert out[0].summary == bs.signature_fallback(sym)
    assert report.get("failed_kept", 0) == 0, report
    assert report["provider_fallbacks"] == 1, report
