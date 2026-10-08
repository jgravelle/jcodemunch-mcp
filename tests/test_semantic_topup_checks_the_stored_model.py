"""LEDGER L-121: the semantic top-up asks what built the store before it writes.

`search_symbols(semantic=True)` embeds every symbol that has no vector and
writes it. `embed_repo` has compared the stored model with the active one since
#500; the top-up, the other of the two vector writers, compared nothing. After a
model change one semantic search on a repo with a new symbol left vectors of
two widths in the store, and `EmbeddingMatrix` keeps the first width and drops
the rest, so no symbol got a similarity score and each later search called the
provider for the dropped symbol again.

The rule lives in `storage/embedding_store.py` now (`read_meta`,
`stale_reason`) and both writers ask it. On a mismatch the search does NOT
rebuild: it writes nothing, calls the provider for nothing, scores no
similarity, and names the reason and the remedy in the response body.
`embed_repo` is the tool that rebuilds.
"""

import ast
from pathlib import Path

import pytest

from jcodemunch_mcp.storage import IndexStore
from jcodemunch_mcp.storage import embedding_store as es
from jcodemunch_mcp.storage.embedding_store import EmbeddingStore
from jcodemunch_mcp.tools import embed_repo as er
from jcodemunch_mcp.tools.index_folder import index_folder
from jcodemunch_mcp.tools.search_symbols import search_symbols

SRC = Path(__file__).resolve().parents[1] / "src" / "jcodemunch_mcp"
NL = chr(10)


def _harness(tmp_path, monkeypatch, store_name):
    """Five indexed symbols, and a fake provider whose calls are counted."""
    src = tmp_path / "src"
    store_dir = tmp_path / store_name
    src.mkdir()
    store_dir.mkdir()
    for i in range(5):
        (src / f"m{i}.py").write_text(f"def handler_{i}():{NL}    return {i}{NL}")
    indexed = index_folder(str(src), use_ai_summaries=False, storage_path=str(store_dir))
    assert indexed["success"] is True, indexed
    owner, name = indexed["repo"].split("/", 1)
    db_path = IndexStore(base_path=str(store_dir))._sqlite._db_path(owner, name)

    class Harness:
        calls: list = []

        @staticmethod
        def provider(model, width, provider_name="fake_provider", task_aware=False, down=False):
            def _embed(texts, provider, model_name, task_type=None):
                Harness.calls.append((len(texts), model_name, task_type))
                if down:
                    raise RuntimeError("provider is down")
                return [[0.1] * width for _ in texts]

            monkeypatch.setattr(
                er, "_detect_provider_detailed",
                lambda: ((provider_name, model), "test_fixture", []),
            )
            monkeypatch.setattr(er, "_detect_provider", lambda: (provider_name, model))
            monkeypatch.setattr(er, "_gemini_task_aware", lambda: task_aware)
            monkeypatch.setattr(er, "embed_texts", _embed)
            Harness.calls = []

        @staticmethod
        def embed(**kwargs):
            return er.embed_repo(
                repo=indexed["repo"], storage_path=str(store_dir), **kwargs
            )

        @staticmethod
        def search(**kwargs):
            return search_symbols(
                repo=indexed["repo"], query="handler", semantic=True,
                storage_path=str(store_dir), max_results=10, **kwargs
            )

        @staticmethod
        def store():
            return EmbeddingStore(db_path)

        @staticmethod
        def widths():
            counts: dict = {}
            for _sid, blob in EmbeddingStore(db_path).iter_raw():
                counts[len(blob) // 4] = counts.get(len(blob) // 4, 0) + 1
            return counts

        @staticmethod
        def add_symbol(n, full=False):
            (src / f"extra{n}.py").write_text(f"def extra_{n}():{NL}    return {n}{NL}")
            result = index_folder(
                str(src), use_ai_summaries=False, storage_path=str(store_dir),
                incremental=not full,
            )
            assert result["success"] is True, result

        @staticmethod
        def true_widths():
            """Over the read-write connection: `iter_raw` reads nothing under
            a storage path that holds `#` (LEDGER L-137)."""
            conn = EmbeddingStore(db_path)._connect()
            try:
                rows = conn.execute(
                    "SELECT length(embedding) / 4, COUNT(*) FROM symbol_embeddings GROUP BY 1"
                ).fetchall()
            finally:
                conn.close()
            return dict(rows)

        @staticmethod
        def drop_vectors():
            conn = EmbeddingStore(db_path)._connect()
            try:
                conn.execute("DELETE FROM symbol_embeddings")
                conn.commit()
            finally:
                conn.close()

        @staticmethod
        def stamp():
            store = EmbeddingStore(db_path)
            return (store.get_dimension(), store.get_model(), store.get_task_type())

        @staticmethod
        def drop_meta(*keys):
            conn = EmbeddingStore(db_path)._connect()
            try:
                for key in keys:
                    conn.execute("DELETE FROM meta WHERE key = ?", (key,))
                conn.commit()
            finally:
                conn.close()

    return Harness


@pytest.fixture
def repo(tmp_path, monkeypatch):
    return _harness(tmp_path, monkeypatch, "store")


@pytest.fixture
def hash_repo(tmp_path, monkeypatch):
    """The same, stored under a directory whose name holds `#`."""
    return _harness(tmp_path, monkeypatch, "c#proj")


def _semantic_channel(response):
    return ((response.get("_meta") or {}).get("verdict") or {}).get("channels", {}).get("semantic")


class TestTheReportedCase:
    """Model A at width 8, a new symbol, a semantic search under model B at width 4."""

    @pytest.mark.parametrize("full_reindex", [False, True], ids=["incremental", "full"])
    def test_the_search_writes_no_vector_of_the_other_model(self, repo, full_reindex):
        repo.provider("model-a", 8)
        repo.embed()
        assert repo.widths() == {8: 5}
        repo.add_symbol(1, full=full_reindex)

        repo.provider("model-b", 4)
        response = repo.search()

        assert repo.widths() == {8: 5}, repo.widths()
        store = repo.store()
        assert (store.get_dimension(), store.get_model()) == (8, "model-a")
        assert "error" not in response, response

    def test_the_response_names_the_reason_and_the_remedy(self, repo):
        repo.provider("model-a", 8)
        repo.embed()
        repo.add_symbol(1)
        repo.provider("model-b", 4)
        response = repo.search()

        mismatch = response.get("semantic_store_mismatch")
        assert mismatch, sorted(response)
        assert mismatch["reason"] == "embedding_model_changed"
        assert mismatch["stored_model"] == "model-a"
        assert mismatch["active_model"] == "model-b"
        assert "embed_repo" in mismatch["remedy"]
        assert _semantic_channel(response) == "unavailable"

    def test_the_provider_is_called_for_nothing(self, repo):
        """Before the fix each search embedded the dropped symbol again."""
        repo.provider("model-a", 8)
        repo.embed()
        repo.add_symbol(1)
        repo.provider("model-b", 4)
        repo.search()
        repo.search()
        assert repo.calls == []

    def test_a_hybrid_search_still_answers_from_the_lexical_channel(self, repo):
        repo.provider("model-a", 8)
        repo.embed()
        repo.add_symbol(1)
        repo.provider("model-b", 4)
        response = repo.search()
        names = {row["name"] for row in response["results"]}
        assert {f"handler_{i}" for i in range(5)} <= names, names

    def test_a_semantic_only_search_returns_no_row_and_says_why(self, repo):
        repo.provider("model-a", 8)
        repo.embed()
        repo.add_symbol(1)
        repo.provider("model-b", 4)
        response = repo.search(semantic_only=True)
        assert response["result_count"] == 0
        assert response["semantic_store_mismatch"]["reason"] == "embedding_model_changed"

    def test_embed_repo_then_rebuilds_and_the_search_scores_again(self, repo):
        repo.provider("model-a", 8)
        repo.embed()
        repo.add_symbol(1)
        repo.provider("model-b", 4)
        repo.search()

        rebuilt = repo.embed()
        assert rebuilt.get("rebuild_reason") == "embedding_model_changed", rebuilt
        assert repo.widths() == {4: 6}

        response = repo.search()
        assert "semantic_store_mismatch" not in response
        assert _semantic_channel(response) == "ok"


class TestTheOtherSpellings:
    """The same write reached by a different difference."""

    def test_a_task_type_change_writes_nothing(self, repo):
        repo.provider("gemini-model", 8, provider_name="gemini", task_aware=False)
        repo.embed()
        assert repo.store().get_task_type() == ""
        repo.add_symbol(1)

        repo.provider("gemini-model", 8, provider_name="gemini", task_aware=True)
        response = repo.search()

        assert repo.widths() == {8: 5}
        assert repo.store().get_task_type() == ""
        assert response["semantic_store_mismatch"]["reason"] == "embedding_task_type_changed"
        assert repo.calls == []

    def test_vectors_with_no_metadata_are_not_stamped_with_the_active_model(self, repo):
        """The state a full re-index left before #522."""
        repo.provider("model-a", 8)
        repo.embed()
        repo.drop_meta("embed_dimension", "embed_model", "embed_task_type")
        repo.add_symbol(1)

        repo.provider("model-b", 4)
        response = repo.search()

        store = repo.store()
        assert repo.widths() == {8: 5}
        assert (store.get_dimension(), store.get_model()) == (None, None)
        assert response["semantic_store_mismatch"]["reason"] == "embedding_metadata_missing"

    def test_an_unknown_model_at_another_width_writes_nothing(self, repo):
        """No name to compare, so the width of the stored vectors decides."""
        repo.provider("model-a", 8)
        repo.embed()
        repo.drop_meta("embed_model")
        repo.add_symbol(1)

        repo.provider("model-b", 4)
        response = repo.search()

        assert repo.widths() == {8: 5}
        mismatch = response["semantic_store_mismatch"]
        assert mismatch["reason"] == "embedding_dimension_mismatch"
        assert (mismatch["stored_dimension"], mismatch["active_dimension"]) == (8, 4)
        assert _semantic_channel(response) == "unavailable"
        # The query only: the width is known before any top-up batch is paid for.
        assert [n for n, _model, _task in repo.calls] == [1]

    @pytest.mark.parametrize("semantic_only", [False, True], ids=["hybrid", "semantic_only"])
    def test_an_unknown_model_at_another_width_is_named_with_no_symbol_missing(
        self, repo, semantic_only
    ):
        """Nothing to top up, so a check at the write would never run."""
        repo.provider("model-a", 8)
        repo.embed()
        repo.drop_meta("embed_model")

        repo.provider("model-b", 4)
        response = repo.search(semantic_only=semantic_only)

        assert response["semantic_store_mismatch"]["reason"] == "embedding_dimension_mismatch"
        assert _semantic_channel(response) == "unavailable"
        assert repo.widths() == {8: 5}

    def test_a_zero_row_answer_says_the_channel_did_not_run(self, repo):
        repo.provider("model-a", 8)
        repo.embed()
        repo.provider("model-b", 4)
        response = repo.search(semantic_only=True)

        assert response["result_count"] == 0
        verdict = repr(response["_meta"]["verdict"])
        assert "the semantic channel did not run" in verdict, verdict
        assert "against this query vector" not in verdict


class TestAnEmptyStoreHasNoStamp:
    """Review round 3: the rule reads the stamp, so the stamp must not outlive
    its vectors. `clear()` kept the three rows, and a rebuild whose batches all
    failed left an empty store named for the OLD model; the next writer wrote
    the new model's vectors under it, and the search refused them."""

    def test_a_failed_rebuild_leaves_no_stamp(self, repo):
        repo.provider("model-a", 8)
        repo.embed()
        repo.provider("model-b", 4, down=True)
        failed = repo.embed()

        assert failed.get("all_batches_failed") is True, failed
        assert repo.widths() == {}
        assert repo.stamp() == (None, None, None)

    def test_a_retry_after_a_failed_rebuild_stamps_what_it_wrote(self, repo):
        repo.provider("model-a", 8)
        repo.embed()
        repo.provider("model-b", 4, down=True)
        repo.embed()

        repo.provider("model-b", 4)
        retried = repo.embed()

        assert repo.widths() == {4: 5}
        assert repo.stamp() == (4, "model-b", "")
        assert retried.get("embedding_dimension") == 4
        assert "rebuild_reason" not in retried, retried

        response = repo.search()
        assert "semantic_store_mismatch" not in response
        assert _semantic_channel(response) == "ok"

    def test_a_search_after_a_failed_rebuild_embeds_and_stamps(self, repo):
        repo.provider("model-a", 8)
        repo.embed()
        repo.provider("model-b", 4, down=True)
        repo.embed()

        repo.provider("model-b", 4)
        first = repo.search()
        assert repo.widths() == {4: 5}
        assert repo.stamp() == (4, "model-b", "")
        assert "semantic_store_mismatch" not in first

        repo.calls.clear()
        second = repo.search()
        assert [n for n, _model, _task in repo.calls] == [1]
        assert "semantic_store_mismatch" not in second
        assert _semantic_channel(second) == "ok"

    @pytest.mark.parametrize("writer", ["search", "embed_repo"])
    def test_a_stamp_left_beside_no_vectors_is_replaced_by_the_next_writer(self, repo, writer):
        """The state a store emptied before this fix is still in."""
        repo.provider("model-a", 8)
        repo.embed()
        repo.drop_vectors()
        assert repo.stamp() == (8, "model-a", "")

        repo.provider("model-b", 4)
        result = repo.search() if writer == "search" else repo.embed()

        assert repo.widths() == {4: 5}
        assert repo.stamp() == (4, "model-b", "")
        assert "semantic_store_mismatch" not in result
        assert "rebuild_reason" not in result, result
        assert "model_changed_from" not in result, result

        repo.calls.clear()
        again = repo.search()
        assert [n for n, _model, _task in repo.calls] == [1]
        assert _semantic_channel(again) == "ok"

    def test_the_width_is_read_off_the_vectors_when_the_metadata_cannot_be_read(
        self, repo, monkeypatch
    ):
        repo.provider("model-a", 8)
        repo.embed()
        monkeypatch.setattr(EmbeddingStore, "read_meta", lambda self, for_writer=False: None)

        repo.provider("model-b", 4)
        response = repo.search()

        mismatch = response["semantic_store_mismatch"]
        assert mismatch["reason"] == "embedding_dimension_mismatch"
        assert (mismatch["stored_dimension"], mismatch["active_dimension"]) == (8, 4)
        assert _semantic_channel(response) == "unavailable"
        assert repo.widths() == {8: 5}


class TestAWriterIsNotBlindWhereItCanRead:
    """Review rounds 4 and 5: unknown is not a change, so a writer whose
    reading of the store fails skips the rebuild. Under a storage path holding
    `#` the read-only open opens another, empty file and the read fails
    (LEDGER L-137); `embed_repo` reads over the connection it writes with."""

    def test_a_model_change_under_a_hash_path_rebuilds(self, hash_repo):
        """No mock: the real read-only failure, whatever its shape."""
        hash_repo.provider("model-a", 8)
        hash_repo.embed()
        assert hash_repo.store().read_meta() is None
        hash_repo.add_symbol(1)

        hash_repo.provider("model-b", 4)
        result = hash_repo.embed()

        assert hash_repo.true_widths() == {4: 6}, hash_repo.true_widths()
        assert hash_repo.stamp() == (4, "model-b", "")
        assert result.get("rebuild_reason") == "embedding_model_changed"
        assert result.get("symbols_embedded") == 6

    def test_the_same_model_under_a_hash_path_reports_no_rebuild(self, hash_repo):
        hash_repo.provider("model-a", 8)
        hash_repo.embed()
        hash_repo.add_symbol(1)

        result = hash_repo.embed()

        assert hash_repo.true_widths() == {8: 6}
        assert result.get("symbols_embedded") == 1
        assert "rebuild_reason" not in result and "model_changed_from" not in result

    def test_embed_repo_does_not_depend_on_the_read_only_open(self, repo, monkeypatch):
        def refuse(_path):
            raise OSError("read-only open refused")

        repo.provider("model-a", 8)
        repo.embed()
        repo.add_symbol(1)
        repo.provider("model-b", 4)
        with monkeypatch.context() as patch:
            patch.setattr(es._generation, "connect_readonly", refuse)
            assert repo.store().read_meta() is None
            result = repo.embed()

        assert repo.widths() == {4: 6}, repo.widths()
        assert result.get("rebuild_reason") == "embedding_model_changed"

    def test_an_unreadable_store_reports_no_rebuild_it_did_not_do(self, repo, monkeypatch):
        """One reading: with nothing read, nothing is claimed about a change."""
        repo.provider("model-a", 8)
        repo.embed()
        repo.add_symbol(1)
        repo.provider("model-b", 4)
        monkeypatch.setattr(EmbeddingStore, "read_meta", lambda self, for_writer=False: None)

        result = repo.embed()

        assert result.get("symbols_embedded") == 1
        assert "rebuild_reason" not in result and "model_changed_from" not in result

    def test_a_stamp_beside_no_vectors_is_removed_whole(self, repo):
        """With no model name to write, the old name must still go."""
        repo.provider("model-a", 8)
        repo.embed()
        repo.drop_vectors()

        repo.provider("", 4)
        repo.embed()

        assert repo.widths() == {4: 5}
        assert repo.stamp() == (4, None, "")


class TestTheWriteIsDecidedAtTheWrite:
    """Review round 6: the search read the store once, read-only, before its
    provider calls, and decided its write (and a `clear()`) from that reading.
    A store another client rebuilt meanwhile, and a store the read-only reading
    could not see, lost vectors. The write reads again over the connection it
    writes with, and a stamp is removed only by a statement that cannot delete
    a vector."""

    def test_a_store_rebuilt_during_the_top_up_is_left_alone(self, repo, monkeypatch):
        repo.provider("model-a", 8)
        repo.embed()
        ids = [sid for sid, _blob in repo.store().iter_raw()]
        repo.drop_vectors()  # a stamp beside no vectors, as a failed rebuild left it

        repo.provider("model-b", 4)
        inner = er.embed_texts

        def other_client_rebuilds_first(texts, provider, model_name, task_type=None):
            if len(texts) > 1:  # the top-up batch, not the query
                store = repo.store()
                store.clear()
                store.set_dimension(16, "model-c")
                store.set_task_type("")
                store.set_many({sid: [0.2] * 16 for sid in ids})
            return inner(texts, provider, model_name, task_type=task_type)

        monkeypatch.setattr(er, "embed_texts", other_client_rebuilds_first)
        response = repo.search()

        assert repo.widths() == {16: 5}, repo.widths()
        assert repo.stamp() == (16, "model-c", "")
        mismatch = response["semantic_store_mismatch"]
        assert mismatch["reason"] == "embedding_model_changed"
        assert mismatch["stored_model"] == "model-c"

    def test_a_search_under_a_hash_path_does_not_overwrite_another_models_vectors(self, hash_repo):
        """The read-only reading sees nothing there (LEDGER L-137), so only the
        reading at the write can refuse. Same width: no width check helps."""
        hash_repo.provider("model-a", 8)
        hash_repo.embed()
        assert hash_repo.true_widths() == {8: 5}

        hash_repo.provider("model-b", 8)
        response = hash_repo.search()

        assert hash_repo.true_widths() == {8: 5}
        assert hash_repo.stamp() == (8, "model-a", "")
        assert response["semantic_store_mismatch"]["reason"] == "embedding_model_changed"

    def test_dropping_an_orphan_stamp_cannot_delete_a_vector(self, repo):
        repo.provider("model-a", 8)
        repo.embed()
        repo.store().drop_orphan_stamp()
        assert repo.widths() == {8: 5}
        assert repo.stamp() == (8, "model-a", "")

        repo.drop_vectors()
        repo.store().drop_orphan_stamp()
        assert repo.stamp() == (None, None, None)

    def test_no_writer_clears_the_store_on_a_stamp_beside_no_vectors(self):
        """`clear()` deletes vectors; only `embed_repo`'s rebuild may call it."""
        callers = []
        for path in sorted(SRC.rglob("*.py")):
            if path.name == "embedding_store.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "clear"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "emb_store"
                ):
                    callers.append((path.name, node.lineno))
        assert [name for name, _line in callers] == ["embed_repo.py"], callers


class TestWhatMustNotChange:

    def test_the_same_model_still_tops_up(self, repo):
        repo.provider("model-a", 8)
        repo.embed()
        repo.add_symbol(1)
        response = repo.search()

        assert repo.widths() == {8: 6}
        assert "semantic_store_mismatch" not in response
        assert _semantic_channel(response) == "ok"

    def test_a_first_semantic_search_embeds_and_records_what_built_it(self, repo):
        repo.provider("model-a", 8)
        response = repo.search()

        store = repo.store()
        assert repo.widths() == {8: 5}
        assert (store.get_dimension(), store.get_model(), store.get_task_type()) == (8, "model-a", "")
        assert "semantic_store_mismatch" not in response

    def test_an_unknown_stored_model_at_the_same_width_is_not_a_change(self, repo):
        """Unknown is not a change (#500): the top-up proceeds."""
        repo.provider("model-a", 8)
        repo.embed()
        repo.drop_meta("embed_model")
        repo.add_symbol(1)

        repo.provider("model-b", 8)
        response = repo.search()

        assert repo.widths() == {8: 6}
        assert "semantic_store_mismatch" not in response

    def test_reading_the_stored_metadata_does_not_touch_the_file(self, repo):
        """A read-write connection moves the .db mtime, and the search's own
        movement check then reports a rebuild it caused (Standing lesson 08-24)."""
        db = Path(repo.store()._db_path)

        def stamp():
            out = []
            for suffix in ("", "-wal", "-shm"):
                side = db.with_name(db.name + suffix)
                out.append((side.stat().st_size, side.stat().st_mtime_ns) if side.exists() else None)
            return out

        # A never-embedded index, the state of a first semantic search: the
        # embeddings table does not exist yet, so a read-write open creates it.
        # (After an embed the table and the WAL are there and a read-write
        # open moves nothing, so that state alone cannot see the defect.)
        before = stamp()
        meta = repo.store().read_meta()
        assert stamp() == before
        assert meta == {"has_vectors": False, "dimension": None, "model": None, "task_type": None}

        repo.provider("model-a", 8)
        repo.embed()
        before = stamp()
        meta = repo.store().read_meta()
        assert stamp() == before
        assert meta == {"has_vectors": True, "dimension": 8, "model": "model-a", "task_type": ""}

    def test_an_unreadable_dimension_leaves_the_other_rows_readable(self, repo):
        """One bad row is one unknown, and the model comparison still runs."""
        repo.provider("model-a", 8)
        repo.embed()
        conn = repo.store()._connect()
        try:
            conn.execute("UPDATE meta SET value = 'x' WHERE key = 'embed_dimension'")
            conn.commit()
        finally:
            conn.close()
        meta = repo.store().read_meta()
        assert meta == {"has_vectors": True, "dimension": None, "model": "model-a", "task_type": ""}
        assert es.stale_reason(meta, "model-b", "") == "embedding_model_changed"

    def test_the_ranking_ledger_records_whether_the_semantic_channel_ran(self, repo, monkeypatch):
        """`semantic_used` labels the row for `tuning` and `regret`."""
        from jcodemunch_mcp.storage import token_tracker

        seen = []
        monkeypatch.setattr(
            token_tracker, "record_ranking_event",
            lambda **kwargs: seen.append(kwargs["semantic_used"]),
        )
        repo.provider("model-a", 8)
        repo.embed()
        repo.search()
        repo.add_symbol(1)
        repo.provider("model-b", 4)
        repo.search()
        assert seen == [True, False]


META = {"has_vectors": True, "dimension": 8, "model": "model-a", "task_type": ""}


@pytest.mark.parametrize(
    "stored, model, task_type, expected",
    [
        (META, "model-a", "", None),
        (META, "model-b", "", "embedding_model_changed"),
        (META, "", "", None),
        ({**META, "model": None}, "model-b", "", None),
        (META, "model-a", "RETRIEVAL_DOCUMENT", "embedding_task_type_changed"),
        ({**META, "task_type": "RETRIEVAL_DOCUMENT"}, "model-a", "", "embedding_task_type_changed"),
        ({**META, "task_type": None}, "model-a", "", None),
        ({**META, "task_type": None}, "model-a", "RETRIEVAL_DOCUMENT", None),
        ({**META, "task_type": None, "dimension": None, "model": None}, "model-a", "",
         "embedding_metadata_missing"),
        ({**META, "model": "model-b", "task_type": "X"}, "model-a", "", "embedding_model_changed"),
        ({**META, "has_vectors": False}, "model-b", "X", None),
        ({"has_vectors": False, "dimension": None, "model": None, "task_type": None}, "m", "", None),
        (None, "model-b", "", None),
    ],
)
def test_stale_reason_over_every_stored_state(stored, model, task_type, expected):
    """`None` (unreadable) and an absent row are UNKNOWN, and unknown is not a change."""
    assert es.stale_reason(stored, model, task_type) == expected


def _functions_calling(tree, attr):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names = set()
            for call in ast.walk(node):
                if isinstance(call, ast.Call):
                    func = call.func
                    names.add(func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", ""))
            if attr in names:
                yield node.name, names


def test_every_function_that_writes_vectors_asks_what_built_the_store():
    """The #500 guard covered one of two writers. A third inherits the rule here."""
    writers = []
    for path in sorted(SRC.rglob("*.py")):
        if path.name == "embedding_store.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for name, names in _functions_calling(tree, "set_many"):
            writers.append((path.name, name, "stale_reason" in names))
    assert {(f, n) for f, n, _ in writers} >= {
        ("embed_repo.py", "embed_repo"),
        ("search_symbols.py", "_search_symbols_semantic"),
    }, writers
    assert [w for w in writers if not w[2]] == [], writers


def test_the_compact_encoder_keeps_the_mismatch():
    from jcodemunch_mcp.encoding.schemas import search_symbols as schema

    response = {
        "result_count": 0,
        "results": [],
        "semantic_store_mismatch": {
            "reason": "embedding_model_changed",
            "stored_model": "model-a",
            "active_model": "model-b",
            "remedy": "run embed_repo",
        },
        "_meta": {"timing_ms": 1.0},
    }
    payload, _ = schema.encode("search_symbols", response)
    assert schema.decode(payload)["semantic_store_mismatch"] == response["semantic_store_mismatch"]
