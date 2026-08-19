"""#500: a model change must not leave a store holding two vector widths.

`embed_repo` carried the comment "Detect dimension mismatch - if the stored
model differs, force a rebuild" while implementing no such detection.
`stored_dim` was read only to seed `dim`, nothing compared the stored model
against the active one, and `set_dimension` fired exclusively on a first-ever
embed. A store therefore accumulated vectors of two widths behind a meta row
still naming the first.

`EmbeddingMatrix` infers its dimension from the FIRST row and drops every row
that disagrees, so symbols embedded after the change stopped being searchable -
silently, and cumulatively, since the gap grows with every new file.

⚠ The read path is NOT the defect. Excluding a mismatched row reaches the same
answer `_cosine_similarity` gave before the matrix existed. The defect is that a
mixed store could come into existence, plus that the exclusion count was
computed and thrown away.
"""

import pytest

from jcodemunch_mcp.storage import IndexStore
from jcodemunch_mcp.storage import embedding_matrix as em
from jcodemunch_mcp.storage.embedding_store import EmbeddingStore
from jcodemunch_mcp.tools import embed_repo as er
from jcodemunch_mcp.tools.index_folder import index_folder


def _fixed_width(width, value=0.1):
    def _embed(texts, provider, model, task_type=None):
        return [[value] * width for _ in texts]
    return _embed


@pytest.fixture
def embedded(tmp_path, monkeypatch):
    """An indexed repo plus helpers to embed it under a named fake provider."""
    src = tmp_path / "src"
    src.mkdir()
    store_dir = tmp_path / "store"
    store_dir.mkdir()
    for i in range(5):
        (src / f"m{i}.py").write_text(f"def handler_{i}():\n    return {i}\n")
    result = index_folder(
        str(src), use_ai_summaries=False, storage_path=str(store_dir),
    )
    assert result["success"] is True
    repo_id = result["repo"]
    owner, name = repo_id.split("/", 1)
    index_store = IndexStore(base_path=str(store_dir))
    db_path = index_store._sqlite._db_path(owner, name)

    class Harness:
        repo = repo_id
        path = src
        store_path = str(store_dir)
        db = db_path

        @staticmethod
        def embed(model, width, **kwargs):
            # ⚠ Patch the DETAILED resolver: `embed_repo` calls that one, and
            # `_detect_provider` is now a thin wrapper over it (#488). Patching
            # the wrapper leaves the real resolver in play and the fake provider
            # never reaches the code under test.
            monkeypatch.setattr(
                er, "_detect_provider_detailed",
                lambda: (("fake_provider", model), "test_fixture", []),
            )
            monkeypatch.setattr(
                er, "_detect_provider", lambda: ("fake_provider", model)
            )
            monkeypatch.setattr(er, "embed_texts", _fixed_width(width))
            return er.embed_repo(
                repo=repo_id, storage_path=str(store_dir), **kwargs
            )

        @staticmethod
        def store():
            return EmbeddingStore(db_path)

        @staticmethod
        def widths():
            counts = {}
            for _sid, blob in EmbeddingStore(db_path).iter_raw():
                w = len(blob) // 4
                counts[w] = counts.get(w, 0) + 1
            return counts

        @staticmethod
        def add_symbol(n):
            (src / f"extra{n}.py").write_text(f"def extra_{n}():\n    return {n}\n")
            index_folder(
                str(src), use_ai_summaries=False, storage_path=str(store_dir),
            )

    return Harness


class TestAModelChangeForcesOneWidth:
    """The reported defect."""

    def test_the_store_holds_exactly_one_vector_width(self, embedded):
        embedded.embed("model-a", 384)
        assert embedded.widths() == {384: 5}

        embedded.add_symbol(1)
        embedded.embed("model-b", 768)

        widths = embedded.widths()
        assert len(widths) == 1, (
            f"store holds two vector widths after a model change: {widths}"
        )
        assert 768 in widths

    def test_the_meta_row_agrees_with_the_vectors(self, embedded):
        embedded.embed("model-a", 384)
        embedded.add_symbol(1)
        embedded.embed("model-b", 768)

        store = embedded.store()
        assert store.get_dimension() == 768
        assert store.get_model() == "model-b"

    def test_the_rebuild_is_disclosed_not_silent(self, embedded):
        """A forced re-embed is expensive on a large corpus and the caller did
        not ask for one."""
        embedded.embed("model-a", 384)
        embedded.add_symbol(1)
        result = embedded.embed("model-b", 768)

        assert result.get("model_changed_from") == "model-a"
        assert result.get("rebuild_reason") == "embedding_model_changed"

    def test_every_symbol_survives_into_the_matrix(self, embedded):
        embedded.embed("model-a", 384)
        embedded.add_symbol(1)
        embedded.embed("model-b", 768)

        matrix = em.get_matrix(str(embedded.db))
        assert matrix is not None
        assert matrix.skipped_dim_mismatch == 0
        assert len(matrix.ids) == embedded.store().count()


class TestWhatMustNotChange:
    """The two criteria that constrain the fix, plus the disclosure boundary."""

    def test_re_embedding_the_same_model_does_not_rebuild(self, embedded):
        """Otherwise the common path becomes a full re-embed on every call."""
        embedded.embed("model-a", 384)
        embedded.add_symbol(1)
        result = embedded.embed("model-a", 384)

        assert "model_changed_from" not in result
        assert result.get("rebuild_reason") is None

    def test_an_unknown_stored_model_is_not_treated_as_a_change(self, embedded):
        """⚠ Unknown is not a change. A store written before the model name was
        persisted has no row, and forcing a rebuild on that would bill every
        existing user a full re-embed for a model that may be identical."""
        embedded.embed("model-a", 384)
        store = embedded.store()
        conn = store._connect()
        try:
            conn.execute("DELETE FROM meta WHERE key = 'embed_model'")
            conn.commit()
        finally:
            conn.close()
        assert embedded.store().get_model() is None

        embedded.add_symbol(1)
        result = embedded.embed("model-b", 384)

        assert "model_changed_from" not in result, (
            "an absent stored model name was read as a mismatch"
        )

    def test_a_first_ever_embed_records_dimension_and_model(self, embedded):
        result = embedded.embed("model-a", 384)
        store = embedded.store()

        assert store.get_dimension() == 384
        assert store.get_model() == "model-a"
        assert "model_changed_from" not in result


class TestTheCountIsNoLongerThrownAway:
    """`skipped_dim_mismatch` was computed, stored on the object, and read
    nowhere. The producer is fixed, but stores already mixed stay mixed until
    the next model change or a forced re-embed."""

    def test_get_model_exists_and_is_readable(self, embedded):
        """⚠ `evidence/capability.py` has called `get_model()` behind a
        `type: ignore` and a bare except since v1.108.221, so the capability
        certificate reported `model: "unknown"` for every repo."""
        embedded.embed("model-a", 384)
        assert embedded.store().get_model() == "model-a"

    def test_a_pre_existing_mixed_store_is_disclosed_by_search(self, embedded):
        """Build the mixed state directly, since the producer can no longer
        create it, and assert a search says so rather than returning a short
        result that reads as a finding."""
        from jcodemunch_mcp.tools.search_symbols import search_symbols

        embedded.embed("model-a", 384)
        store = embedded.store()
        ids = [sid for sid, _ in store.iter_raw()]
        assert len(ids) >= 2
        # One row at the wrong width: exactly what a pre-fix model change left.
        store.set_many({ids[-1]: [0.2] * 768})
        em.invalidate(str(embedded.db)) if hasattr(em, "invalidate") else None

        response = search_symbols(
            repo=embedded.repo, query="handler", semantic=True,
            storage_path=embedded.store_path, max_results=10,
        )
        meta = response.get("_meta") or {}
        partial = meta.get("semantic_partial")
        assert partial, (
            "a store excluding rows from semantic search reported nothing; "
            f"_meta was {sorted(meta)}"
        )
        assert partial["symbols_excluded"] >= 1
        assert partial["reason"] == "embedding_dimension_mismatch"
        assert (meta.get("verdict") or {}).get("channels", {}).get(
            "semantic"
        ) == "partial"
