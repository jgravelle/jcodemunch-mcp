"""v1.108.137: source-shaped exact seeding + openWorldHint + WAL bound.

- retrieval/query_shape.py classifies source-shaped query tokens (qualified /
  camel / snake); get_ranked_context pins exact-name symbol matches ahead of
  the ranked results with match_channel="exact_name" + _meta.query_shape.
  Pure-prose queries are byte-identical (no shaped tokens -> no seeding).
- Every tool annotation now carries openWorldHint (False except the
  network-capable set in server._OPEN_WORLD_TOOLS).
- All three SQLite stores bound WAL growth via PRAGMA journal_size_limit.
"""

import sqlite3
from pathlib import Path

from jcodemunch_mcp.retrieval.query_shape import source_shaped_tokens
from jcodemunch_mcp.tools.get_ranked_context import get_ranked_context
from jcodemunch_mcp.tools.index_folder import index_folder


def _make_repo(tmp_path: Path, files: dict[str, str]) -> tuple[str, str]:
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    storage = str(tmp_path / ".index")
    result = index_folder(str(tmp_path), use_ai_summaries=False, storage_path=storage)
    return result.get("repo", str(tmp_path)), storage


_REPO = {
    "store.py": (
        "class SqliteStore:\n"
        "    def incremental_save(self, delta):\n"
        "        return delta\n\n"
        "    def load_index(self):\n"
        "        return None\n"
    ),
    "probe.py": (
        "class FreshnessProbe:\n"
        "    def annotate(self, items):\n"
        "        pass\n"
    ),
    "utils.py": (
        "def format_date(d):\n"
        "    return str(d)\n\n"
        "def helper_for_dates(x):\n"
        "    # mentions incremental save logic in prose only\n"
        "    return x\n"
    ),
}


class TestQueryShapeClassifier:
    def test_qualified_dotted(self):
        toks = source_shaped_tokens("how does SqliteStore.incremental_save work?")
        assert any(t["shape"] == "qualified" and t["name"] == "incremental_save"
                   and t["parent"] == "SqliteStore" for t in toks)

    def test_qualified_double_colon(self):
        toks = source_shaped_tokens("where is Store::get defined")
        assert toks and toks[0]["name"] == "get" and toks[0]["parent"] == "Store"

    def test_camel_case(self):
        toks = source_shaped_tokens("explain the FreshnessProbe lifecycle")
        assert toks and toks[0]["shape"] == "camel" and toks[0]["name"] == "FreshnessProbe"

    def test_snake_case_and_call_spelling(self):
        toks = source_shaped_tokens("what does get_ranked_context() return")
        assert toks and toks[0]["name"] == "get_ranked_context"

    def test_dunder(self):
        toks = source_shaped_tokens("the __post_init__ hook")
        assert toks and toks[0]["name"] == "__post_init__"

    def test_filename_excluded(self):
        assert source_shaped_tokens("open server.py and config.jsonc please") == []

    def test_pure_prose_yields_nothing(self):
        assert source_shaped_tokens("how does the cache handle deleted files") == []

    def test_cap_and_dedup(self):
        q = "compare FooBar FooBar BazQux one_two three_four five_six"
        toks = source_shaped_tokens(q)
        assert len(toks) == 3
        assert len({t["token"] for t in toks}) == 3

    def test_punctuation_trimmed(self):
        toks = source_shaped_tokens("is 'FreshnessProbe' (the class) used?")
        assert toks and toks[0]["name"] == "FreshnessProbe"


class TestExactSeeding:
    def test_qualified_query_pins_exact_symbol_first(self, tmp_path):
        repo, storage = _make_repo(tmp_path, _REPO)
        result = get_ranked_context(
            repo, "how does SqliteStore.incremental_save apply a delta?",
            token_budget=4000, storage_path=storage,
        )
        assert "error" not in result
        items = result["context_items"]
        assert items, "expected results for an exact-name query"
        assert items[0]["symbol_id"].endswith("incremental_save#method")
        assert items[0]["match_channel"] == "exact_name"
        qs = result["_meta"]["query_shape"]
        assert qs["exact_seeded"] >= 1
        assert "SqliteStore.incremental_save" in qs["source_shaped"]

    def test_camel_query_seeds_class(self, tmp_path):
        repo, storage = _make_repo(tmp_path, _REPO)
        result = get_ranked_context(
            repo, "walk me through FreshnessProbe annotation",
            token_budget=4000, storage_path=storage,
        )
        assert "error" not in result
        items = result["context_items"]
        assert items and items[0]["symbol_id"].endswith("FreshnessProbe#class")
        assert items[0]["match_channel"] == "exact_name"

    def test_prose_query_has_no_shape_meta_or_channel(self, tmp_path):
        repo, storage = _make_repo(tmp_path, _REPO)
        result = get_ranked_context(
            repo, "date formatting helpers", token_budget=4000, storage_path=storage,
        )
        assert "error" not in result
        assert "query_shape" not in result["_meta"]
        for item in result["context_items"]:
            assert "match_channel" not in item

    def test_exact_seed_survives_zero_bm25(self, tmp_path):
        """An exact name the BM25 tokenizer would miss still comes back."""
        repo, storage = _make_repo(tmp_path, _REPO)
        result = get_ranked_context(
            repo, "FreshnessProbe.annotate", token_budget=4000, storage_path=storage,
        )
        assert "error" not in result
        items = result["context_items"]
        assert items, "seeded exact match must not fall into negative evidence"
        assert items[0]["match_channel"] == "exact_name"
        assert "negative_evidence" not in result


class TestOpenWorldHint:
    def test_every_tool_carries_open_world_hint(self):
        from jcodemunch_mcp.server import _apply_readonly_annotations, _build_tools_list
        tools = _apply_readonly_annotations(_build_tools_list())
        missing = [t.name for t in tools
                   if t.annotations is None or t.annotations.open_world_hint is None]
        assert not missing, f"tools missing openWorldHint: {missing}"

    def test_hint_matches_network_set(self):
        from jcodemunch_mcp.server import (
            _OPEN_WORLD_TOOLS,
            _apply_readonly_annotations,
            _build_tools_list,
        )
        tools = _apply_readonly_annotations(_build_tools_list())
        by_name = {t.name: t for t in tools}
        for name, tool in by_name.items():
            expected = name in _OPEN_WORLD_TOOLS
            assert tool.annotations.open_world_hint is expected, (
                f"{name}: openWorldHint={tool.annotations.open_world_hint}, expected {expected}"
            )
        # Spot-check both directions on tools that must exist.
        assert by_name["index_repo"].annotations.open_world_hint is True
        assert by_name["search_symbols"].annotations.open_world_hint is False
        assert by_name["get_ranked_context"].annotations.open_world_hint is False


class TestWalBound:
    def test_index_store_pragma_listed(self):
        from jcodemunch_mcp.storage import sqlite_store
        assert any("journal_size_limit" in p for p in sqlite_store._PRAGMAS)

    def test_parse_cache_connection_bounds_wal(self, tmp_path):
        from jcodemunch_mcp.parser.parse_cache import _connect
        conn = _connect(str(tmp_path))
        try:
            limit = conn.execute("PRAGMA journal_size_limit").fetchone()[0]
            assert limit == 67108864
        finally:
            conn.close()

    def test_embedding_store_connection_bounds_wal(self, tmp_path):
        from jcodemunch_mcp.storage.embedding_store import EmbeddingStore
        store = EmbeddingStore(tmp_path / "emb.db")
        conn = store._connect()
        try:
            limit = conn.execute("PRAGMA journal_size_limit").fetchone()[0]
            assert limit == 67108864
        finally:
            conn.close()

    def test_index_db_connection_bounds_wal(self, tmp_path):
        _repo, storage = _make_repo(tmp_path, {"a.py": "def f():\n    pass\n"})
        dbs = list(Path(storage).glob("*.db"))
        assert dbs
        conn = sqlite3.connect(str(dbs[0]))
        try:
            for pragma in __import__("jcodemunch_mcp.storage.sqlite_store", fromlist=["_PRAGMAS"])._PRAGMAS:
                conn.execute(pragma)
            limit = conn.execute("PRAGMA journal_size_limit").fetchone()[0]
            assert limit == 67108864
        finally:
            conn.close()
