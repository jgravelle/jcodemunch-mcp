"""tokens_saved is a counted figure, with a measured table as the fallback.

`estimate_savings` divided every byte count by 4. Measured over the pinned
64-repo corpus in `benchmarks/savings_tokenizer/`, Claude's tokenizer reads
fewer bytes per token than that in every language measured, so the per-call
figure undercounted on Claude. A client billed on another tokenizer reads a
different number, so the table is chosen by `savings_tokenizer` and every
response names the one in use.

Expected values here are computed from the committed measurement files, never
typed: a typed number would pass against a table that drifted from them.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from jcodemunch_mcp import config as config_module
from jcodemunch_mcp import savings_tokenizer as st
from jcodemunch_mcp.storage import token_tracker as tt

BENCH = Path(__file__).resolve().parents[1] / "benchmarks" / "savings_tokenizer"
FILES = {"claude": "claude_table.json", "openai": "cl100k_table.json"}
RATIOS_FILE = BENCH / "claude_over_cl100k.json"


def _measured(name: str) -> dict:
    return json.loads((BENCH / FILES[name]).read_text(encoding="utf-8"))


@pytest.fixture
def tokenizer(monkeypatch):
    def use(value):
        monkeypatch.setitem(config_module._GLOBAL_CONFIG, "savings_tokenizer", value)

    return use


@pytest.mark.parametrize("name", sorted(FILES))
def test_the_shipped_table_is_the_measured_one(name):
    measured = _measured(name)
    table = st.TABLES[name]
    assert table["overall"] == measured["overall"]["bytes_per_token"]
    published = {
        lang: row["bytes_per_token"]
        for lang, row in measured["languages"].items()
        if row["published"]
    }
    assert table["languages"] == published, (
        "every published row ships, and nothing ships that was not published"
    )


def test_the_default_table_is_claude(tokenizer):
    tokenizer(None)
    assert st.active() == "claude"
    overall = _measured("claude")["overall"]["bytes_per_token"]
    assert tt.estimate_savings(100_000, 0) == int(100_000 / overall)
    assert tt.estimate_savings(100_000, 0) > 100_000 // 4, (
        "the old divisor undercounted on Claude's tokenizer"
    )


def test_a_measured_language_uses_its_own_row(tokenizer):
    tokenizer("claude")
    py = _measured("claude")["languages"]["python"]["bytes_per_token"]
    assert tt.estimate_savings(100_000, 20_000, language="python") == int(80_000 / py)


@pytest.mark.parametrize("language", [None, "no-such-language", "astro"])
def test_an_unmeasured_language_uses_the_overall_figure(tokenizer, language):
    """`astro` was measured in one repo only, so it is not published."""
    tokenizer("claude")
    overall = _measured("claude")["overall"]["bytes_per_token"]
    assert tt.estimate_savings(50_000, 0, language=language) == int(50_000 / overall)


def test_the_openai_table_is_cl100k(tokenizer):
    tokenizer("openai")
    assert st.active() == "openai"
    overall = _measured("openai")["overall"]["bytes_per_token"]
    assert tt.estimate_savings(100_000, 0) == int(100_000 / overall)
    assert "cl100k" in st.basis()


@pytest.mark.parametrize("bad", ["gpt", "", 7, ["claude"]])
def test_an_invalid_setting_falls_back_to_claude(tokenizer, bad):
    tokenizer(bad)
    assert st.active() == "claude"
    assert "claude" in st.basis()


def test_savings_never_go_negative(tokenizer):
    tokenizer("claude")
    assert tt.estimate_savings(100, 5_000) == 0


def test_the_key_is_documented_in_the_config_template():
    assert config_module.DEFAULTS["savings_tokenizer"] == "claude"
    assert '"savings_tokenizer"' in config_module.generate_template()


def test_the_basis_is_attached_beside_every_tokens_saved(tokenizer):
    from jcodemunch_mcp import server
    from jcodemunch_mcp.encoding.schema_driven import UNIVERSAL_META_JSON

    tokenizer("openai")
    result = {
        "_meta": {"tokens_saved": 5},
        "results": [{"_meta": {"tokens_saved": 2}}, {"_meta": {"timing_ms": 1}}],
    }
    server._label_savings_basis(result)
    assert result["_meta"]["tokens_saved_basis"] == st.basis(path="overall")
    assert result["results"][0]["_meta"]["tokens_saved_basis"] == st.basis(path="overall")
    assert "tokens_saved_basis" not in result["results"][1]["_meta"]
    assert "tokens_saved_basis" in UNIVERSAL_META_JSON, (
        "a compact encoder must not keep the figure and drop its basis"
    )


def test_the_dispatcher_labels_the_basis(tmp_path, monkeypatch, tokenizer):
    from jcodemunch_mcp import server
    from jcodemunch_mcp.tools.index_folder import index_folder

    store = tmp_path / "store"
    monkeypatch.setenv("CODE_INDEX_PATH", str(store))
    monkeypatch.setitem(config_module._GLOBAL_CONFIG, "meta_fields", ["tokens_saved"])
    tokenizer("claude")
    root = tmp_path / "src_repo"
    root.mkdir()
    (root / "widgets.py").write_text(_python_source(), encoding="utf-8")
    res = index_folder(str(root), use_ai_summaries=False, storage_path=str(store))
    assert res.get("success"), res
    from jcodemunch_mcp.storage import token_counts

    def meta_for(query):
        out = asyncio.run(
            server.call_tool(
                "search_symbols",
                {"repo": res["repo"], "query": query, "format": "json"},
            )
        )
        item = out[0] if isinstance(out, list) else out.content[0]
        return json.loads(item.text)["_meta"]

    first = meta_for("widget_00")
    assert first["tokens_saved"] > 0, first
    assert first["tokens_saved_basis"] == st.basis("claude", "table"), (
        "a file's first credit has no stored count yet",
        first,
    )
    token_counts.flush_pending()
    meta = meta_for("widget_01")
    assert meta["tokens_saved_basis"] == st.basis("claude", "count"), (
        "the meta_fields filter kept the figure; it must keep the unit it was counted in",
        meta,
    )


def _python_source(n: int = 40) -> str:
    body = "\n".join(
        f'def widget_{i:02d}(x):\n    """Widget {i} docstring."""\n'
        f"    total = x * {i} + sum(range({i}))\n    return total\n"
        for i in range(n)
    )
    return body + "\n"


@pytest.fixture
def indexed(tmp_path, monkeypatch):
    from jcodemunch_mcp.tools.index_folder import index_folder

    store = tmp_path / "store"
    monkeypatch.setenv("CODE_INDEX_PATH", str(store))
    root = tmp_path / "src_repo"
    root.mkdir()
    (root / "widgets.py").write_text(_python_source(), encoding="utf-8")
    res = index_folder(str(root), use_ai_summaries=False, storage_path=str(store))
    assert res.get("success"), res
    return res["repo"], str(store)


def test_search_symbols_credits_the_bytes_it_served(indexed, tokenizer):
    """A full row serves the source and a compact row does not, so the same
    query credits less at full. Crediting `byte_length` gave both the same."""
    from jcodemunch_mcp.tools.search_symbols import search_symbols

    tokenizer("claude")
    repo, store = indexed
    compact = search_symbols(repo, "widget", detail_level="compact", storage_path=store)
    full = search_symbols(repo, "widget", detail_level="full", storage_path=store)
    assert compact["results"] and full["results"]
    assert full["_meta"]["tokens_saved"] < compact["_meta"]["tokens_saved"], (
        compact["_meta"],
        full["_meta"],
    )


def test_search_symbols_counts_and_marks_a_cache_hit(indexed, tokenizer, monkeypatch):
    from jcodemunch_mcp.tools import search_symbols as ss

    tokenizer("claude")
    recorded = []
    monkeypatch.setattr(
        ss, "record_savings", lambda n, **k: recorded.append(n) or sum(recorded)
    )
    repo, store = indexed
    first = ss.search_symbols(repo, "widget_07", storage_path=store)
    second = ss.search_symbols(repo, "widget_07", storage_path=store)
    assert second["_meta"].get("cache_hit") is True
    assert "cache_hit" not in first["_meta"]
    assert second["_meta"]["tokens_saved"] == first["_meta"]["tokens_saved"] > 0
    assert recorded == [first["_meta"]["tokens_saved"]] * 2


def test_search_symbols_has_one_crediting_rule():
    """Three exits each carried a copy of the rule; one helper serves all."""
    import inspect

    from jcodemunch_mcp.tools import search_symbols as ss

    src = inspect.getsource(ss)
    assert "estimate_savings(" not in src.replace("def estimate_savings(", "")
    assert src.count("_credit_served_rows(") >= 4  # def + three exits


def test_the_recorded_ledger_is_not_rescaled(tmp_path, tokenizer, monkeypatch):
    """Totals recorded under bytes/4 stay as recorded; new calls add on top,
    and the lifetime total says it spans the change of divisor."""
    monkeypatch.setattr(tt, "_share_savings", lambda *a, **k: None)
    tokenizer("claude")
    (tmp_path / "_savings.json").write_text(
        json.dumps({"total_tokens_saved": 1_000, "basis_generation": 2}),
        encoding="utf-8",
    )
    state = tt._State()
    state.add(10, str(tmp_path), "get_symbol_source")
    state.flush()
    after = json.loads((tmp_path / "_savings.json").read_text(encoding="utf-8"))
    assert after["total_tokens_saved"] == 1_010
    assert after["basis_generation_first"] == 2
    assert after["basis_generation"] == tt.SAVINGS_BASIS_GENERATION > 2
    with state._lock:
        basis = state._build_stats_locked()["total_tokens_saved_basis"]
    assert basis["mixed_basis"] is True
    assert basis["tokenizer"] == "claude"


def test_the_shipped_ratios_are_the_measured_ones():
    measured = json.loads(RATIOS_FILE.read_text(encoding="utf-8"))
    assert st.RATIOS["overall"] == measured["overall"]["ratio"]
    published = {
        lang: row["ratio"] for lang, row in measured["languages"].items() if row["published"]
    }
    assert st.RATIOS["languages"] == published


@pytest.fixture
def fresh_encoding(monkeypatch):
    """Forget the process's loaded encoding so a test sees the load itself."""
    monkeypatch.setattr(st, "_ENCODING", None)
    monkeypatch.setattr(st, "_STATE", "not_loaded")
    monkeypatch.setattr(st, "_FETCH_THREAD", None)
    monkeypatch.setattr(st, "_FETCH_STARTED", None)


@pytest.fixture
def empty_cache(monkeypatch, tmp_path):
    """tiktoken's cache directory, empty, and every socket refused."""
    import socket

    def refuse(*a, **k):
        raise AssertionError("a network connection was attempted")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    cache = tmp_path / "tiktoken-cache"
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(cache))
    return cache


def test_nothing_is_fetched_when_the_download_is_off(fresh_encoding, empty_cache, monkeypatch, tokenizer):
    import tiktoken

    def fetch(name):
        raise AssertionError("get_encoding called with tokenizer_download off")

    monkeypatch.setattr(tiktoken, "get_encoding", fetch)
    monkeypatch.setitem(config_module._GLOBAL_CONFIG, "tokenizer_download", False)
    tokenizer("claude")
    assert st.encoding() is None
    assert st.state() == "download_off"
    assert st._FETCH_THREAD is None, "no fetch thread may start"
    assert st.count("hello world") is None
    n, why = st.credit([("python", 26_040, None)], [("python", "x" * 2_604)])
    assert why.startswith(st.basis("claude", "table"))
    assert st.STATE_REASONS["download_off"] in why
    assert n == int(26_040 / st.bytes_per_token("python") - 2_604 / st.bytes_per_token("python"))
    assert not empty_cache.exists()


def test_the_env_var_turns_the_download_off(fresh_encoding, empty_cache, monkeypatch, tmp_path):
    import tiktoken

    assert config_module.ENV_VAR_MAPPING["JCODEMUNCH_TOKENIZER_DOWNLOAD"] == "tokenizer_download"
    assert config_module.DEFAULTS["tokenizer_download"] is True
    assert '"tokenizer_download"' in config_module.generate_template()
    monkeypatch.setattr(tiktoken, "get_encoding", lambda name: (_ for _ in ()).throw(AssertionError("fetch")))
    monkeypatch.setenv("JCODEMUNCH_TOKENIZER_DOWNLOAD", "0")
    monkeypatch.setattr(config_module, "_GLOBAL_CONFIG", {})
    monkeypatch.setattr(config_module, "_CONFIG_LOADED", False)
    config_module.load_config(storage_path=str(tmp_path / "cfg"))
    assert st.download_allowed() is False
    assert st.encoding() is None and st.state() == "download_off" and st._FETCH_THREAD is None


def test_offline_the_table_is_the_fallback_and_the_call_does_not_wait(fresh_encoding, empty_cache, monkeypatch, tokenizer):
    import threading
    import time

    import tiktoken

    release = threading.Event()
    callers = []

    def offline(name):
        callers.append(threading.current_thread().name)
        release.wait(5)
        raise OSError("offline")

    monkeypatch.setattr(tiktoken, "get_encoding", offline)
    tokenizer("openai")
    t0 = time.monotonic()
    assert st.count("hello world") is None, "a call never waits for the fetch"
    assert time.monotonic() - t0 < 1.0
    assert st.state() == "downloading"
    _, why = st.credit([("go", 36_630, None)], [])
    assert st.STATE_REASONS["downloading"] in why
    release.set()
    assert st.encoding(wait=5) is None
    assert st.state() == "download_failed"
    assert callers == ["jcodemunch-cl100k-fetch"], "the fetch runs once, off the caller's thread"
    n, why = st.credit([("go", 36_630, None)], [])
    assert why.startswith(st.basis("openai", "table"))
    assert st.STATE_REASONS["download_failed"] in why
    assert n == int(36_630 / st.bytes_per_token("go", "openai"))
    assert st.count("again") is None and callers == ["jcodemunch-cl100k-fetch"], "a failed fetch is not retried per call"


def test_a_fetch_that_never_ends_reads_stalled_not_fetching(fresh_encoding, empty_cache, monkeypatch, tokenizer):
    import threading

    import tiktoken

    release = threading.Event()

    def hung(name):
        release.wait(5)
        raise OSError("gave up")

    monkeypatch.setattr(tiktoken, "get_encoding", hung)
    tokenizer("claude")
    assert st.count("x") is None and st.state() == "downloading"
    clock = [st.time.monotonic()]
    monkeypatch.setattr(st.time, "monotonic", lambda: clock[0])
    clock[0] += st.STALL_SECONDS + 1
    assert st.state() == "download_stalled"
    _, why = st.credit([("python", 1_000, None)], [])
    assert st.STATE_REASONS["download_stalled"] in why
    release.set()
    st._FETCH_THREAD.join(5)
    assert st.state() == "download_failed"


def test_a_cached_copy_with_a_bad_hash_is_not_refetched_with_the_download_off(fresh_encoding, empty_cache, monkeypatch):
    import hashlib

    import tiktoken

    empty_cache.mkdir()
    (empty_cache / hashlib.sha1(st.ENCODING_URL.encode()).hexdigest()).write_bytes(b"not the file")
    monkeypatch.setattr(tiktoken, "get_encoding", lambda name: (_ for _ in ()).throw(AssertionError("refetch")))
    monkeypatch.setitem(config_module._GLOBAL_CONFIG, "tokenizer_download", False)
    assert st.encoding() is None and st.state() == "download_off"


def test_a_bad_cached_copy_is_hashed_once_with_the_download_off(fresh_encoding, empty_cache, monkeypatch):
    import hashlib

    empty_cache.mkdir()
    (empty_cache / hashlib.sha1(st.ENCODING_URL.encode()).hexdigest()).write_bytes(b"not the file")
    monkeypatch.setattr(st, "_BAD_COPY", None)
    monkeypatch.setitem(config_module._GLOBAL_CONFIG, "tokenizer_download", False)
    hashed = []
    real = hashlib.sha256
    monkeypatch.setattr(st.hashlib, "sha256", lambda b=b"": hashed.append(1) or real(b))
    for _ in range(5):
        assert st.count("hello") is None
    assert len(hashed) == 1


def test_without_tiktoken_the_table_is_the_fallback(fresh_encoding, monkeypatch, tokenizer):
    import sys

    monkeypatch.setitem(sys.modules, "tiktoken", None)  # import raises ImportError
    tokenizer("openai")
    assert st.encoding() is None
    n, why = st.credit([("go", 36_630, None)], [])
    assert why.startswith(st.basis("openai", "table"))
    assert n == int(36_630 / st.bytes_per_token("go", "openai"))


def test_the_package_ships_no_encoding_file():
    import jcodemunch_mcp

    pkg = Path(jcodemunch_mcp.__file__).parent
    assert not list(pkg.rglob("*.tiktoken")), "the encoding is fetched by tiktoken, never bundled"
    assert "MIT License" in (pkg / "data" / "NOTICE.md").read_text(encoding="utf-8")


def test_a_counted_file_uses_the_ratio_for_claude_and_the_count_for_openai(tokenizer):
    tokenizer("claude")
    n, why = st.credit([("python", 999_999, 1_000)], [("python", "hello world")])
    assert why == st.basis("claude", "count")
    assert n == int(1_000 * st.ratio("python") - 2 * st.ratio("python"))
    tokenizer("openai")
    n, why = st.credit([("python", 999_999, 1_000)], [("python", "hello world")])
    assert why == st.basis("openai", "count")
    assert n == 998


def test_a_mix_of_counted_and_uncounted_files_says_so(tokenizer):
    tokenizer("claude")
    _, why = st.credit([("python", 100, 50), ("python", 100, None)], [])
    assert "1 of 2 files" in why


def _on_disk(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


def _counts(store: str) -> tuple[dict, dict]:
    import sqlite3

    dbs = list(Path(store).rglob("*.db"))
    assert len(dbs) == 1, dbs
    conn = sqlite3.connect(dbs[0])
    try:
        files = dict(conn.execute("SELECT path, hash FROM files"))
        counts = dict(conn.execute("SELECT hash, cl100k FROM token_counts"))
    finally:
        conn.close()
    return {p: counts.get(h) for p, h in files.items()}, counts


def _credit_and_flush(repo: str, store: str, files) -> None:
    """Credit ``files`` once, as a response would, then run the write-back."""
    from jcodemunch_mcp.storage import sqlite_store, token_counts
    from jcodemunch_mcp.tools._utils import load_repo_index_or_error

    sqlite_store._cache_clear()
    index, error, _status = load_repo_index_or_error(repo, store)
    assert error is None, error
    st.credit_files(index, files, [])
    token_counts.flush_pending()


@pytest.fixture
def _no_queued_counts():
    from jcodemunch_mcp.storage import token_counts

    with token_counts._pending_lock:
        token_counts._pending.clear()
    yield
    with token_counts._pending_lock:
        token_counts._pending.clear()


def test_a_full_index_counts_nothing_and_a_credit_counts_its_file(tmp_path, _no_queued_counts):
    """The save pays no tokenizer pass; the first credit of a file stores its count."""
    from jcodemunch_mcp.tools.index_folder import index_folder

    root = tmp_path / "src_repo"
    root.mkdir()
    (root / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    (root / "b.py").write_text(_python_source(5), encoding="utf-8")
    store = tmp_path / "store"
    res = index_folder(str(root), use_ai_summaries=False, storage_path=str(store))
    assert res.get("success"), res
    _, counts = _counts(str(store))
    assert counts == {}, "a full save counted files no response has credited"
    _credit_and_flush(res["repo"], str(store), ["a.py"])
    by_path, _ = _counts(str(store))
    # The bytes on disk: write_text writes CRLF on Windows, and the count
    # is of what the index holds.
    expected = st.count(_on_disk(root / "a.py"))
    assert expected is not None, "no encoding loaded: run `harness warm`"
    assert by_path["a.py"] == expected
    assert by_path["b.py"] is None, "only the credited file is counted"


def test_a_second_process_credits_a_counted_file_by_its_count(tmp_path, _no_queued_counts):
    from jcodemunch_mcp.storage import sqlite_store
    from jcodemunch_mcp.tools._utils import load_repo_index_or_error
    from jcodemunch_mcp.tools.index_folder import index_folder

    root = tmp_path / "src_repo"
    root.mkdir()
    (root / "a.py").write_text(_python_source(5), encoding="utf-8")
    store = tmp_path / "store"
    res = index_folder(str(root), use_ai_summaries=False, storage_path=str(store))
    assert res.get("success"), res
    _credit_and_flush(res["repo"], str(store), ["a.py"])
    sqlite_store._cache_clear()
    index, error, _status = load_repo_index_or_error(res["repo"], str(store))
    assert error is None, error
    _saved, why = st.credit_files(index, ["a.py"], [])
    assert why == st.basis(st.active(), "count"), why


def test_a_reindex_drops_the_count_of_content_no_file_holds(tmp_path, _no_queued_counts):
    from jcodemunch_mcp.tools.index_folder import index_folder

    root = tmp_path / "src_repo"
    root.mkdir()
    (root / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    (root / "b.py").write_text("def b():\n    return 2\n", encoding="utf-8")
    store = tmp_path / "store"
    res = index_folder(str(root), use_ai_summaries=False, storage_path=str(store))
    assert res.get("success"), res
    _credit_and_flush(res["repo"], str(store), ["a.py", "b.py"])
    _, before = _counts(str(store))
    assert len(before) == 2
    (root / "a.py").write_text("def a(x):\n    return x * 3 + 1\n", encoding="utf-8")
    res = index_folder(str(root), use_ai_summaries=False, storage_path=str(store), incremental=True)
    assert res.get("success"), res
    by_path, after = _counts(str(store))
    assert len(after) == 1, "the old a.py count is pruned with its hash"
    assert set(after) < set(before) and by_path["b.py"] is not None and by_path["a.py"] is None


def test_a_write_back_without_the_encoding_reads_no_indexed_copies(tmp_path, monkeypatch, _no_queued_counts):
    """Offline or with the download off, a queued file must not be read only to be discarded."""
    from jcodemunch_mcp.storage import token_counts
    from jcodemunch_mcp.tools.index_folder import index_folder

    root = tmp_path / "src_repo"
    root.mkdir()
    (root / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    store = tmp_path / "store"
    res = index_folder(str(root), use_ai_summaries=False, storage_path=str(store))
    assert res.get("success"), res
    reads = []
    real = token_counts._read_copy
    monkeypatch.setattr(token_counts, "_read_copy", lambda *a, **k: reads.append(a) or real(*a, **k))
    monkeypatch.setattr(st, "encoding", lambda wait=None: None)
    _credit_and_flush(res["repo"], str(store), ["a.py"])
    assert reads == [], "no copy is read when nothing can count it"
    _, counts = _counts(str(store))
    assert counts == {}


def _one_file_index(tmp_path, text="def a():\n    return 1\n"):
    from jcodemunch_mcp.tools.index_folder import index_folder

    root = tmp_path / "src_repo"
    root.mkdir()
    (root / "a.py").write_text(text, encoding="utf-8")
    store = tmp_path / "store"
    res = index_folder(str(root), use_ai_summaries=False, storage_path=str(store))
    assert res.get("success"), res
    (db,) = list(store.rglob("*.db"))
    return res["repo"], str(store), db


def test_an_index_without_the_table_gains_a_count_at_first_credit(tmp_path, _no_queued_counts):
    """An index saved before token counts existed has no table; the first
    credit of a file creates it and stores the count."""
    import sqlite3

    repo, store, db = _one_file_index(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("DROP TABLE token_counts")
    conn.commit()
    conn.close()
    _credit_and_flush(repo, store, ["a.py"])
    by_path, _ = _counts(store)
    assert by_path["a.py"] is not None


def test_a_file_with_no_indexed_copy_is_not_queued_again(tmp_path, _no_queued_counts):
    """No copy to count from (``cache_mode: metadata_only``, a deleted copy):
    the file is tried once, then not queued again by later credits."""
    from jcodemunch_mcp.storage import token_counts

    repo, store, db = _one_file_index(tmp_path)
    (db.with_suffix("") / "a.py").unlink()
    _credit_and_flush(repo, store, ["a.py"])
    _, counts = _counts(store)
    assert counts == {}
    _credit_and_flush(repo, store, ["a.py"])
    assert token_counts.pending_count() == 0
    from jcodemunch_mcp.storage import sqlite_store
    from jcodemunch_mcp.tools._utils import load_repo_index_or_error

    sqlite_store._cache_clear()
    index, error, _status = load_repo_index_or_error(repo, store)
    assert error is None, error
    st.credit_files(index, ["a.py"], [])
    assert token_counts.pending_count() == 0, "a file with no copy was queued again"


def test_a_copy_that_no_longer_matches_its_hash_is_not_counted(tmp_path, _no_queued_counts):
    """A count belongs to its bytes: text that does not hash to the queued key
    is not stored under it."""
    repo, store, db = _one_file_index(tmp_path)
    (db.with_suffix("") / "a.py").write_text("x = 'something else entirely'\n", encoding="utf-8")
    _credit_and_flush(repo, store, ["a.py"])
    _, counts = _counts(store)
    assert counts == {}


def test_a_foreign_commit_right_after_our_write_back_is_not_forgiven(tmp_path, monkeypatch, _no_queued_counts):
    """Another writer that commits between our COMMIT and our stat (an
    embedding or a runtime ingest takes no ``indexwrite``) must still read as a
    rewrite. The seam runs the foreign commit at exactly that point."""
    import sqlite3

    from jcodemunch_mcp.storage import generation, sqlite_store
    from jcodemunch_mcp.tools._utils import load_repo_index_or_error

    repo, store, db = _one_file_index(tmp_path)
    sqlite_store._cache_clear()
    index, error, _status = load_repo_index_or_error(repo, store)
    assert error is None, error
    before = index._loaded_mtime_ns

    def foreign_commit(_db_path):
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE IF NOT EXISTS foreign_writer (x INTEGER)")
        conn.execute("INSERT INTO foreign_writer VALUES (1)")
        conn.commit()
        conn.close()

    monkeypatch.setattr(sqlite_store, "_after_write_back_commit", foreign_commit)
    assert sqlite_store.store_token_counts_for_loaded_index(index, [("f" * 64, 3)])
    now = sqlite_store._db_mtime_ns(db)
    assert now != before
    assert not generation.only_benign_rewrites(str(db), before, now)


def test_our_own_write_back_alone_is_forgiven(tmp_path, _no_queued_counts):
    from jcodemunch_mcp.storage import generation, sqlite_store
    from jcodemunch_mcp.tools._utils import load_repo_index_or_error

    repo, store, db = _one_file_index(tmp_path)
    sqlite_store._cache_clear()
    index, error, _status = load_repo_index_or_error(repo, store)
    assert error is None, error
    before = index._loaded_mtime_ns
    assert sqlite_store.store_token_counts_for_loaded_index(index, [("f" * 64, 3)])
    now = sqlite_store._db_mtime_ns(db)
    assert generation.only_benign_rewrites(str(db), before, now)


def test_one_write_back_counts_a_bounded_amount_and_keeps_the_rest(tmp_path, monkeypatch, _no_queued_counts):
    """The next call waits for a write-back in flight, so one flush stays short."""
    from jcodemunch_mcp.storage import token_counts
    from jcodemunch_mcp.tools.index_folder import index_folder

    root = tmp_path / "src_repo"
    root.mkdir()
    for k in range(6):
        # Distinct content: counts are keyed by content hash.
        (root / f"m{k}.py").write_text(_python_source(3) + f"VALUE_{k} = {k}" + chr(10), encoding="utf-8")
    store = tmp_path / "store"
    res = index_folder(str(root), use_ai_summaries=False, storage_path=str(store))
    assert res.get("success"), res
    one = len(_on_disk(root / "m0.py"))
    monkeypatch.setattr(token_counts, "_FLUSH_MAX_BYTES", one * 2)
    _credit_and_flush(res["repo"], str(store), [f"m{k}.py" for k in range(6)])
    _, counts = _counts(str(store))
    assert counts, "the first flush stored nothing"
    assert token_counts.pending_count() == 1, "the rest was dropped instead of kept"
    token_counts.flush_pending()
    token_counts.flush_pending()
    by_path, _ = _counts(str(store))
    assert all(v is not None for v in by_path.values()), by_path


def test_the_dispatcher_stores_the_counts_after_the_call(tmp_path, monkeypatch, tokenizer, _no_queued_counts):
    from jcodemunch_mcp import server
    from jcodemunch_mcp.tools.index_folder import index_folder

    store = tmp_path / "store"
    monkeypatch.setenv("CODE_INDEX_PATH", str(store))
    tokenizer("claude")
    root = tmp_path / "src_repo"
    root.mkdir()
    (root / "widgets.py").write_text(_python_source(), encoding="utf-8")
    res = index_folder(str(root), use_ai_summaries=False, storage_path=str(store))
    assert res.get("success"), res
    server._bm25_outer_calls[0] = 0
    server._bm25_flush_task[0] = None

    async def call_then_wait():
        await server.call_tool("search_symbols", {"repo": res["repo"], "query": "widget_00"})
        task = server._bm25_flush_task[0]
        assert task is not None, "nothing was scheduled after the call"
        await task

    asyncio.run(call_then_wait())
    by_path, _ = _counts(str(store))
    assert by_path["widgets.py"] == st.count(_on_disk(root / "widgets.py"))


def test_search_symbols_without_counts_says_it_used_the_table(indexed, tokenizer):
    import sqlite3

    from jcodemunch_mcp.tools.search_symbols import search_symbols

    tokenizer("claude")
    repo, store = indexed
    db = next(Path(store).rglob("*.db"))
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM token_counts")
    conn.commit()
    conn.close()
    out = search_symbols(repo, "widget_03", storage_path=store)
    assert out["_meta"]["tokens_saved_basis"] == st.basis("claude", "table")
