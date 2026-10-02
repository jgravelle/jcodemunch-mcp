"""A local model directory is not handed to a sentence-transformers that runs its code unasked.

GHSA-jhr6-gm9c-rqjv: before 5.6.0, loading a LOCAL model directory bypassed
`trust_remote_code` and executed the custom Python inside it. 1.108.326 raised
the `semantic` extra's floor, which is install metadata: `pip install -U
jcodemunch-mcp` without the extra named, or a `sentence-transformers` installed
directly, keeps the old release (LEDGER L-108). The call site checks the
release it actually imported.

The library is faked here. No test loads a real model, and the fake records
whether it was constructed, so a refusal that arrives after the load fails.
"""

from __future__ import annotations

import importlib.metadata
import sys
import types

import pytest

from jcodemunch_mcp.tools import embed_repo


def _fake_library(monkeypatch, version):
    built: list[str] = []

    class SentenceTransformer:
        def __init__(self, name):
            built.append(name)

        def encode(self, texts, **_kwargs):
            return [[0.0, 1.0] for _ in texts]

    module = types.ModuleType("sentence_transformers")
    module.SentenceTransformer = SentenceTransformer
    if version is not None:
        module.__version__ = version
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)
    return built


@pytest.fixture
def model_dir(tmp_path):
    path = tmp_path / "local-model"
    path.mkdir()
    return path


@pytest.mark.parametrize("version", ["2.2.0", "4.1.0", "5.3.0", "5.5.1", "5.5.1.post1", "5.5"])
def test_an_old_release_is_refused_for_a_local_directory(monkeypatch, model_dir, version):
    built = _fake_library(monkeypatch, version)
    with pytest.raises(RuntimeError) as exc:
        embed_repo._embed_sentence_transformers(["x"], str(model_dir))
    assert built == []
    message = str(exc.value)
    assert "GHSA-jhr6-gm9c-rqjv" in message and version in message
    assert "jcodemunch-mcp[semantic]" in message


@pytest.mark.parametrize("version", ["5.6.0", "5.6.1", "5.7.0", "6.1.0", "10.0.0"])
def test_a_fixed_release_loads_a_local_directory(monkeypatch, model_dir, version):
    built = _fake_library(monkeypatch, version)
    assert embed_repo._embed_sentence_transformers(["x"], str(model_dir)) == [[0.0, 1.0]]
    assert built == [str(model_dir)]


def test_an_old_release_still_loads_a_hub_model(monkeypatch, tmp_path):
    """The advisory is the local-directory path; a Hub name honours `trust_remote_code`."""
    monkeypatch.chdir(tmp_path)
    built = _fake_library(monkeypatch, "5.3.0")
    assert embed_repo._embed_sentence_transformers(["x"], "BAAI/bge-base-en-v1.5") == [[0.0, 1.0]]
    assert built == ["BAAI/bge-base-en-v1.5"]


def test_a_bare_name_that_is_a_directory_in_the_working_directory_is_local(monkeypatch, tmp_path):
    (tmp_path / "all-MiniLM-L6-v2").mkdir()
    monkeypatch.chdir(tmp_path)
    built = _fake_library(monkeypatch, "5.3.0")
    with pytest.raises(RuntimeError):
        embed_repo._embed_sentence_transformers(["x"], "all-MiniLM-L6-v2")
    assert built == []


def test_a_home_relative_path_is_local(monkeypatch, tmp_path):
    (tmp_path / "models" / "m").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    built = _fake_library(monkeypatch, "5.3.0")
    with pytest.raises(RuntimeError):
        embed_repo._embed_sentence_transformers(["x"], "~/models/m")
    assert built == []


def test_a_model_file_path_is_local(monkeypatch, tmp_path):
    target = tmp_path / "model.bin"
    target.write_bytes(b"")
    built = _fake_library(monkeypatch, "5.3.0")
    with pytest.raises(RuntimeError):
        embed_repo._embed_sentence_transformers(["x"], str(target))
    assert built == []


def test_an_unreadable_version_is_refused_for_a_local_directory(monkeypatch, model_dir):
    """UNKNOWN is not fixed: no `__version__` and no distribution metadata refuses."""
    built = _fake_library(monkeypatch, None)

    def missing(_name):
        raise importlib.metadata.PackageNotFoundError("sentence-transformers")

    monkeypatch.setattr(importlib.metadata, "version", missing)
    with pytest.raises(RuntimeError) as exc:
        embed_repo._embed_sentence_transformers(["x"], str(model_dir))
    assert built == []
    assert "could not be read" in str(exc.value)


def test_the_distribution_version_is_read_when_the_module_has_none(monkeypatch, model_dir):
    built = _fake_library(monkeypatch, None)
    monkeypatch.setattr(importlib.metadata, "version", lambda _name: "6.1.0")
    assert embed_repo._embed_sentence_transformers(["x"], str(model_dir)) == [[0.0, 1.0]]
    assert built == [str(model_dir)]


def test_the_refusal_reaches_the_caller_of_embed_texts(monkeypatch, model_dir):
    built = _fake_library(monkeypatch, "5.3.0")
    with pytest.raises(RuntimeError, match="GHSA-jhr6-gm9c-rqjv"):
        embed_repo.embed_texts(["x"], "sentence_transformers", str(model_dir))
    assert built == []


def test_embed_repo_names_the_refusal_and_embeds_nothing(monkeypatch, tmp_path, model_dir):
    """At the user's entry point: the tool's response carries the reason, and no vector is stored."""
    from unittest.mock import patch

    from jcodemunch_mcp.parser.symbols import Symbol
    from jcodemunch_mcp.storage import IndexStore

    storage = tmp_path / "store"
    symbol = Symbol(
        id="s1", file="src/a.py", name="foo", qualified_name="foo", kind="function", language="python",
        signature="def foo():", byte_offset=0, byte_length=50, summary="",
    )
    IndexStore(base_path=str(storage)).save_index(
        owner="test", name="floor", source_files=["src/a.py"], symbols=[symbol],
        raw_files={"src/a.py": "def foo(): pass"}, languages={"python": 1}, file_languages={"src/a.py": "python"},
    )
    built = _fake_library(monkeypatch, "5.3.0")
    monkeypatch.setenv("JCODEMUNCH_EMBED_MODEL", str(model_dir))
    with patch("jcodemunch_mcp.embeddings.local_encoder.is_model_available", return_value=False), patch(
        "jcodemunch_mcp.embeddings.local_encoder.is_onnxruntime_available", return_value=False
    ):
        result = embed_repo.embed_repo("test/floor", storage_path=str(storage))
    assert built == []
    assert result.get("symbols_embedded") == 0, result
    causes = result.get("error_causes") or []
    assert len(causes) == 1 and causes[0]["type"] == "RuntimeError", result
    # The ledger keeps 300 characters; the cause and the remedy must both be inside them.
    assert "GHSA-jhr6-gm9c-rqjv" in causes[0]["message"], result
    assert "jcodemunch-mcp[semantic]" in causes[0]["message"], result
    assert "sentence-transformers 5.3.0" in causes[0]["message"], result


@pytest.mark.parametrize("version", ["5.6.0.dev0", "5.6.0rc1", "5.6.0a1", "5.6.0-beta.1", "5.6.0.RC2"])
def test_a_pre_release_of_the_fixed_release_is_refused(monkeypatch, model_dir, version):
    """A development build numbered 5.6.0 may predate the fix; it is not the fixed release."""
    built = _fake_library(monkeypatch, version)
    with pytest.raises(RuntimeError, match="GHSA-jhr6-gm9c-rqjv"):
        embed_repo._embed_sentence_transformers(["x"], str(model_dir))
    assert built == []


@pytest.mark.parametrize("version", ["5.6.0.post1", "5.6.0+cpu", "5.7.0.dev0", "6.0.0rc1"])
def test_a_suffix_that_is_not_a_pre_release_of_the_fixed_release_loads(monkeypatch, model_dir, version):
    built = _fake_library(monkeypatch, version)
    assert embed_repo._embed_sentence_transformers(["x"], str(model_dir)) == [[0.0, 1.0]]
    assert built == [str(model_dir)]


def test_a_distribution_with_no_version_field_is_refused_with_the_remedy(monkeypatch, model_dir):
    built = _fake_library(monkeypatch, None)
    monkeypatch.setattr(importlib.metadata, "version", lambda _name: None)
    with pytest.raises(RuntimeError) as exc:
        embed_repo._embed_sentence_transformers(["x"], str(model_dir))
    assert built == []
    assert "could not be read" in str(exc.value) and "jcodemunch-mcp[semantic]" in str(exc.value)


def test_the_raw_name_is_checked_as_the_library_checks_it(monkeypatch, tmp_path):
    """The library tests the RAW name. A directory literally named `$JCM_X` exists raw and not expanded."""
    (tmp_path / "$JCM_X").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JCM_X", "no-such-directory")
    built = _fake_library(monkeypatch, "5.3.0")
    with pytest.raises(RuntimeError):
        embed_repo._embed_sentence_transformers(["x"], "$JCM_X")
    assert built == []


def test_a_path_through_an_environment_variable_is_refused(monkeypatch, tmp_path):
    (tmp_path / "m").mkdir()
    monkeypatch.setenv("JCM_MODELS", str(tmp_path))
    built = _fake_library(monkeypatch, "5.3.0")
    with pytest.raises(RuntimeError):
        embed_repo._embed_sentence_transformers(["x"], "$JCM_MODELS/m")
    assert built == []


def test_fusion_search_names_the_refusal_instead_of_reading_off(monkeypatch, tmp_path, model_dir):
    """`fusion=True` caught the refusal and answered `semantic: off`, the same as a repo never embedded."""
    from unittest.mock import patch

    from jcodemunch_mcp.parser.symbols import Symbol
    from jcodemunch_mcp.storage import IndexStore
    from jcodemunch_mcp.storage.embedding_store import EmbeddingStore
    from jcodemunch_mcp.tools.search_symbols import search_symbols

    storage = tmp_path / "store"
    symbol = Symbol(
        id="s1", file="src/a.py", name="foo", qualified_name="foo", kind="function", language="python",
        signature="def foo():", byte_offset=0, byte_length=50, summary="",
    )
    store = IndexStore(base_path=str(storage))
    store.save_index(
        owner="test", name="fusion", source_files=["src/a.py"], symbols=[symbol],
        raw_files={"src/a.py": "def foo(): pass"}, languages={"python": 1}, file_languages={"src/a.py": "python"},
    )
    EmbeddingStore(store._sqlite._db_path("test", "fusion")).set_many({"s1": [0.0, 1.0]})
    monkeypatch.setenv("JCODEMUNCH_EMBED_MODEL", str(model_dir))
    off = patch("jcodemunch_mcp.embeddings.local_encoder.is_model_available", return_value=False)
    off2 = patch("jcodemunch_mcp.embeddings.local_encoder.is_onnxruntime_available", return_value=False)

    built = _fake_library(monkeypatch, "5.3.0")
    with off, off2:
        refused = search_symbols("test/fusion", "foo", fusion=True, storage_path=str(storage))
    assert built == []
    assert [r["name"] for r in refused["results"]] == ["foo"]  # the lexical answer still arrives
    error = refused.get("semantic_channel_error")
    assert error and error["type"] == "RuntimeError", refused
    assert "GHSA-jhr6-gm9c-rqjv" in error["message"] and "jcodemunch-mcp[semantic]" in error["message"]
    assert refused["_meta"]["verdict"]["channels"]["semantic"] == "unavailable"

    built = _fake_library(monkeypatch, "6.1.0")
    with off, off2:
        # The SAME query: a refused answer must not be replayed from the result cache after the upgrade.
        ran = search_symbols("test/fusion", "foo", fusion=True, storage_path=str(storage))
    assert built == [str(model_dir)]
    assert "semantic_channel_error" not in ran
    assert ran["_meta"]["verdict"]["channels"]["semantic"] == "ok"


def test_the_compact_encoder_keeps_the_refusal():
    """An undeclared body dict is dropped by the compact encoder (v1.108.169)."""
    from jcodemunch_mcp.encoding.schemas import search_symbols as schema

    response = {
        "result_count": 0, "results": [], "query": "q", "repo": "r",
        "semantic_channel_error": {"type": "RuntimeError", "message": "Refused: GHSA-jhr6-gm9c-rqjv"},
        "_meta": {"timing_ms": 1.0},
    }
    payload, _ = schema.encode("search_symbols", response)
    assert schema.decode(payload)["semantic_channel_error"] == response["semantic_channel_error"]


def _embedded_repo(tmp_path, monkeypatch, name):
    """A one-symbol repo with a stored vector, a provider selected, and ONNX off."""
    from jcodemunch_mcp.embeddings import local_encoder
    from jcodemunch_mcp.parser.symbols import Symbol
    from jcodemunch_mcp.storage import IndexStore
    from jcodemunch_mcp.storage.embedding_store import EmbeddingStore

    storage = tmp_path / "store"
    symbol = Symbol(
        id="s1", file="src/a.py", name="foo", qualified_name="foo", kind="function", language="python",
        signature="def foo():", byte_offset=0, byte_length=50, summary="",
    )
    store = IndexStore(base_path=str(storage))
    store.save_index(
        owner="test", name=name, source_files=["src/a.py"], symbols=[symbol],
        raw_files={"src/a.py": "def foo(): pass"}, languages={"python": 1}, file_languages={"src/a.py": "python"},
    )
    EmbeddingStore(store._sqlite._db_path("test", name)).set_many({"s1": [0.0, 1.0]})
    monkeypatch.setenv("JCODEMUNCH_EMBED_MODEL", "BAAI/bge-base-en-v1.5")
    monkeypatch.setattr(local_encoder, "is_model_available", lambda: False)
    monkeypatch.setattr(local_encoder, "is_onnxruntime_available", lambda: False)
    return str(storage)


def test_fusion_search_names_any_failure_of_its_similarity_channel(tmp_path, monkeypatch):
    """Not only the L-108 refusal: a provider that times out is named the same way, and is not cached."""
    from jcodemunch_mcp.tools.search_symbols import search_symbols

    storage = _embedded_repo(tmp_path, monkeypatch, "anyfail")
    calls = {"n": 0}

    def flaky(texts, provider, model, task_type=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("provider timed out")
        return [[0.0, 1.0] for _ in texts]

    monkeypatch.setattr(embed_repo, "embed_texts", flaky)
    first = search_symbols("test/anyfail", "foo", fusion=True, storage_path=storage)
    assert first["semantic_channel_error"] == {"type": "TimeoutError", "message": "provider timed out"}
    assert first["_meta"]["verdict"]["channels"]["semantic"] == "unavailable"

    second = search_symbols("test/anyfail", "foo", fusion=True, storage_path=storage)
    assert calls["n"] == 2  # the channel was tried again, not replayed
    assert "semantic_channel_error" not in second
    assert second["_meta"]["verdict"]["channels"]["semantic"] == "ok"


def test_a_zero_row_fusion_answer_with_a_failed_channel_is_absent_and_says_the_channel_failed(tmp_path, monkeypatch):
    """Decided, not accidental (v1.108.185's ruling stands): fusion's lexical and identity
    passes score every candidate, so zero rows is a corpus fact whether or not the
    similarity channel ran. The failed channel is labelled and its cause carried beside it.

    A real folder index: a hand-built one has no source root, freshness reads
    `unknown`, and that gate degrades the verdict before this one is reached.
    """
    from jcodemunch_mcp.embeddings import local_encoder
    from jcodemunch_mcp.storage import IndexStore
    from jcodemunch_mcp.storage.embedding_store import EmbeddingStore
    from jcodemunch_mcp.tools.index_folder import index_folder
    from jcodemunch_mcp.tools.search_symbols import search_symbols

    storage = tmp_path / "idx"
    monkeypatch.setenv("CODE_INDEX_PATH", str(storage))
    project = tmp_path / "proj"
    (project / "src").mkdir(parents=True)
    (project / "src" / "a.py").write_text("def refresh_token(user):\n    return user\n", encoding="utf-8")
    repo = index_folder(path=str(project), use_ai_summaries=False, storage_path=str(storage))["repo"]
    owner, name = repo.split("/", 1)
    store = IndexStore(base_path=str(storage))
    ids = [s["id"] for s in store.load_index(owner, name).symbols]
    EmbeddingStore(store._sqlite._db_path(owner, name)).set_many({i: [0.0, 1.0] for i in ids})
    monkeypatch.setenv("JCODEMUNCH_EMBED_MODEL", "BAAI/bge-base-en-v1.5")
    monkeypatch.setattr(local_encoder, "is_model_available", lambda: False)
    monkeypatch.setattr(local_encoder, "is_onnxruntime_available", lambda: False)

    def down(texts, provider, model, task_type=None):
        raise TimeoutError("provider timed out")

    monkeypatch.setattr(embed_repo, "embed_texts", down)
    result = search_symbols(repo, "zzzqqqxyzzy", fusion=True, storage_path=str(storage))
    assert result["results"] == []
    verdict = result["_meta"]["verdict"]
    assert verdict["state"] == "absent", verdict
    assert verdict["channels"]["semantic"] == "unavailable"
    assert result["semantic_channel_error"] == {"type": "TimeoutError", "message": "provider timed out"}

    def up(texts, provider, model, task_type=None):
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(embed_repo, "embed_texts", up)
    again = search_symbols(repo, "zzzqqqxyzzy", fusion=True, storage_path=str(storage))
    assert "semantic_channel_error" not in again  # the failed answer was not cached
    assert again["_meta"]["verdict"]["channels"]["semantic"] == "ok"


def test_the_fusion_error_is_redacted_and_cut(tmp_path, monkeypatch):
    """The key is in the body, which survives the default `_meta` strip, so a provider's secret must not ride in it."""
    from jcodemunch_mcp.embeddings.failures import MESSAGE_CHARS
    from jcodemunch_mcp.tools.search_symbols import search_symbols

    storage = _embedded_repo(tmp_path, monkeypatch, "redact")
    secret = "sk-ant-" + "api03-" + "A" * 95

    def leaky(texts, provider, model, task_type=None):
        raise RuntimeError("401 for key " + secret + " " + "x" * 600)

    monkeypatch.setattr(embed_repo, "embed_texts", leaky)
    message = search_symbols("test/redact", "foo", fusion=True, storage_path=storage)["semantic_channel_error"]["message"]
    assert secret not in message and "[REDACTED" in message
    assert len(message) <= MESSAGE_CHARS + 3
