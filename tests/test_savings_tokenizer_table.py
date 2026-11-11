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
    assert result["_meta"]["tokens_saved_basis"] == st.basis()
    assert result["results"][0]["_meta"]["tokens_saved_basis"] == st.basis()
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
    out = asyncio.run(
        server.call_tool(
            "search_symbols",
            {"repo": res["repo"], "query": "widget_00", "format": "json"},
        )
    )
    item = out[0] if isinstance(out, list) else out.content[0]
    meta = json.loads(item.text)["_meta"]
    assert meta["tokens_saved"] > 0, meta
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


def test_the_env_var_turns_the_download_off(monkeypatch):
    monkeypatch.setenv("JCODEMUNCH_TOKENIZER_DOWNLOAD", "0")
    assert config_module.ENV_VAR_MAPPING["JCODEMUNCH_TOKENIZER_DOWNLOAD"] == "tokenizer_download"
    assert config_module.DEFAULTS["tokenizer_download"] is True
    assert '"tokenizer_download"' in config_module.generate_template()


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


def test_a_cached_copy_with_a_bad_hash_is_not_refetched_with_the_download_off(fresh_encoding, empty_cache, monkeypatch):
    import hashlib

    import tiktoken

    empty_cache.mkdir()
    (empty_cache / hashlib.sha1(st.ENCODING_URL.encode()).hexdigest()).write_bytes(b"not the file")
    monkeypatch.setattr(tiktoken, "get_encoding", lambda name: (_ for _ in ()).throw(AssertionError("refetch")))
    monkeypatch.setitem(config_module._GLOBAL_CONFIG, "tokenizer_download", False)
    assert st.encoding() is None and st.state() == "download_off"


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


def test_a_full_index_stores_a_count_for_every_file(tmp_path):
    from jcodemunch_mcp.tools.index_folder import index_folder

    root = tmp_path / "src_repo"
    root.mkdir()
    (root / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    (root / "b.py").write_text(_python_source(5), encoding="utf-8")
    store = tmp_path / "store"
    res = index_folder(str(root), use_ai_summaries=False, storage_path=str(store))
    assert res.get("success"), res
    by_path, _ = _counts(str(store))
    for name in ("a.py", "b.py"):
        # The bytes on disk: write_text writes CRLF on Windows, and the count
        # is of what the index holds.
        assert by_path[name] == st.count(_on_disk(root / name)), name


def test_a_reindex_counts_the_changed_file_and_drops_the_old_count(tmp_path):
    from jcodemunch_mcp.tools.index_folder import index_folder

    root = tmp_path / "src_repo"
    root.mkdir()
    (root / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    (root / "b.py").write_text("def b():\n    return 2\n", encoding="utf-8")
    store = tmp_path / "store"
    assert index_folder(str(root), use_ai_summaries=False, storage_path=str(store)).get("success")
    _, before = _counts(str(store))
    changed = "def a(x):\n    return x * 3 + 1\n"
    (root / "a.py").write_text(changed, encoding="utf-8")
    res = index_folder(str(root), use_ai_summaries=False, storage_path=str(store), incremental=True)
    assert res.get("success"), res
    by_path, after = _counts(str(store))
    assert by_path["a.py"] == st.count(_on_disk(root / "a.py"))
    assert len(after) == 2, "the old a.py count is pruned with its hash"
    assert set(after) != set(before)


def test_an_index_saved_before_counts_existed_gets_them_on_its_next_save(tmp_path):
    import sqlite3

    from jcodemunch_mcp.tools.index_folder import index_folder

    root = tmp_path / "src_repo"
    root.mkdir()
    (root / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    (root / "b.py").write_text("def b():\n    return 2\n", encoding="utf-8")
    store = tmp_path / "store"
    assert index_folder(str(root), use_ai_summaries=False, storage_path=str(store)).get("success")
    db = next(Path(store).rglob("*.db"))
    conn = sqlite3.connect(db)
    conn.execute("DROP TABLE token_counts")
    conn.commit()
    conn.close()
    (root / "b.py").write_text("def b():\n    return 22\n", encoding="utf-8")
    assert index_folder(
        str(root), use_ai_summaries=False, storage_path=str(store), incremental=True
    ).get("success")
    by_path, _ = _counts(str(store))
    assert by_path["a.py"] == st.count(_on_disk(root / "a.py")), (
        "an unchanged file is counted from the indexed copy, without a re-parse"
    )


def test_a_save_without_the_encoding_reads_no_indexed_copies(tmp_path, monkeypatch):
    """Offline or with the download off, a save must not read every uncounted file."""
    import sqlite3

    from jcodemunch_mcp.storage import token_counts
    from jcodemunch_mcp.tools.index_folder import index_folder

    root = tmp_path / "src_repo"
    root.mkdir()
    (root / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    (root / "b.py").write_text("def b():\n    return 2\n", encoding="utf-8")
    store = tmp_path / "store"
    assert index_folder(str(root), use_ai_summaries=False, storage_path=str(store)).get("success")
    db = next(Path(store).rglob("*.db"))
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM token_counts")
    conn.commit()
    conn.close()
    reads = []
    real = token_counts._read_copy
    monkeypatch.setattr(token_counts, "_read_copy", lambda *a, **k: reads.append(a) or real(*a, **k))
    monkeypatch.setattr(st, "encoding", lambda wait=None: None)
    monkeypatch.setattr(token_counts, "_PENDING", type(token_counts._PENDING)())
    (root / "b.py").write_text("def b():\n    return 22\n", encoding="utf-8")
    assert index_folder(
        str(root), use_ai_summaries=False, storage_path=str(store), incremental=True
    ).get("success")
    assert reads == [], "no copy is read when nothing can count it"
    _, counts = _counts(str(store))
    assert counts == {}


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
