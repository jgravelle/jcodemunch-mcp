"""`embed_repo`'s task-type gate: an unknown stored value is not a change (#523).

The gate compared the stored `embed_task_type` with the provider's, and a store
with no such row read as `None`, which never equals `""`: every provider but
task-aware Gemini was billed a full re-embed for a row that was merely absent.
The model gate three lines above it already said "unknown is NOT a change".

Three states, and the gate must tell them apart:
- recorded and different: a real toggle, rebuild (the case the gate exists for);
- never recorded, beside a recorded dimension: unknown, NOT a change;
- no embedding metadata at all beside existing vectors: nothing says what
  produced them, so they are rebuilt, and the response names that reason.
"""

import sqlite3

import pytest

from jcodemunch_mcp.storage import IndexStore
from jcodemunch_mcp.storage.embedding_store import EmbeddingStore
from jcodemunch_mcp.tools import embed_repo as er
from jcodemunch_mcp.tools.index_folder import index_folder

WIDTH = 16


@pytest.fixture
def repo(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    store_dir = tmp_path / "store"
    store_dir.mkdir()
    for i in range(5):
        (src / f"m{i}.py").write_text(f"def handler_{i}():\n    return {i}\n")
    result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store_dir))
    assert result["success"] is True
    repo_id = result["repo"]
    owner, name = repo_id.split("/", 1)
    db_path = IndexStore(base_path=str(store_dir))._sqlite._db_path(owner, name)

    class Harness:
        calls: list = []

        @classmethod
        def embed(cls, provider="fake_provider", task_aware=False, **kwargs):
            cls.calls = []

            def _embed(texts, provider, model, task_type=None):
                cls.calls.append((len(texts), task_type))
                return [[0.1] * WIDTH for _ in texts]

            monkeypatch.setattr(
                er, "_detect_provider_detailed",
                lambda: ((provider, "model-a"), "test_fixture", []),
            )
            monkeypatch.setattr(er, "_detect_provider", lambda: (provider, "model-a"))
            monkeypatch.setattr(er, "_gemini_task_aware", lambda: task_aware)
            monkeypatch.setattr(er, "embed_texts", _embed)
            return er.embed_repo(repo=repo_id, storage_path=str(store_dir), **kwargs)

        @staticmethod
        def store():
            return EmbeddingStore(db_path)

        @staticmethod
        def drop_meta(*keys):
            conn = sqlite3.connect(str(db_path))
            for key in keys:
                conn.execute("DELETE FROM meta WHERE key = ?", (key,))
            conn.commit()
            conn.close()

        @staticmethod
        def full_reindex_with_a_new_file():
            (src / "extra.py").write_text("def extra():\n    return 9\n")
            result = index_folder(
                str(src), use_ai_summaries=False, storage_path=str(store_dir),
                incremental=False,
            )
            assert result["success"] is True

    return Harness


def test_a_full_reindex_then_the_same_model_rebuilds_nothing(repo):
    """The pair the reporter measured (#522 with #523): a full re-index used to
    erase the rows and the gate then billed a whole re-embed."""
    repo.embed()
    repo.full_reindex_with_a_new_file()

    result = repo.embed()

    assert repo.calls == [(1, None)], f"more than the one new symbol was embedded: {repo.calls}"
    assert result["symbols_embedded"] == 1
    assert "rebuild_reason" not in result
    assert repo.store().count() == 6


def test_a_store_with_no_task_type_row_is_not_re_embedded(repo):
    """The reported defect: a row that was never written is not a toggle."""
    first = repo.embed()
    assert first["symbols_embedded"] == 5
    # a first embed of an empty store is not a rebuild of anything
    assert "rebuild_reason" not in first
    repo.drop_meta("embed_task_type")
    assert repo.store().get_task_type() is None
    assert repo.store().get_dimension() == WIDTH

    second = repo.embed()

    assert repo.calls == [], f"re-embedded for an absent row: {repo.calls}"
    assert second["symbols_embedded"] == 0
    assert second.get("cached") is True
    assert "rebuild_reason" not in second


def test_a_recorded_empty_task_type_is_still_a_change_when_gemini_turns_task_aware(repo):
    """The other direction: `""` is a recorded value, not an unknown one."""
    repo.embed()
    assert repo.store().get_task_type() == ""

    result = repo.embed(provider="gemini", task_aware=True)

    assert result["symbols_embedded"] == 5
    assert {task_type for _n, task_type in repo.calls} == {"RETRIEVAL_DOCUMENT"}
    assert repo.store().get_task_type() == "RETRIEVAL_DOCUMENT"
    assert result.get("rebuild_reason") == "embedding_task_type_changed"


def test_a_recorded_task_type_that_matches_is_cached(repo):
    repo.embed(provider="gemini", task_aware=True)
    assert repo.store().get_task_type() == "RETRIEVAL_DOCUMENT"

    result = repo.embed(provider="gemini", task_aware=True)

    assert repo.calls == []
    assert result.get("cached") is True


def test_vectors_with_no_metadata_at_all_are_rebuilt_and_the_reason_is_named(repo):
    """Nothing records what produced these vectors, so nothing may be appended
    to them: the rebuild is kept, and it is disclosed instead of silent."""
    repo.embed()
    repo.drop_meta("embed_task_type", "embed_dimension", "embed_model")
    assert repo.store().count() == 5

    result = repo.embed()

    assert result["symbols_embedded"] == 5
    assert result.get("rebuild_reason") == "embedding_metadata_missing"
    store = repo.store()
    assert (store.get_dimension(), store.get_model(), store.get_task_type()) == (WIDTH, "model-a", "")
