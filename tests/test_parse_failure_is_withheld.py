"""A file whose parse fails is withheld, so absence over the index is refused.

A source file that ran past the parse budget, or whose parse raised, is real,
current and wanted: it is missing because of our limit or our parser, not the
caller's intent. That is the definition of a withheld file
(`WITHHELD_SKIP_REASONS`), and an index holding one cannot prove that a name is
absent, because the name may sit in that file.
"""
from __future__ import annotations

import pytest

from jcodemunch_mcp.parser.parse_budget import ParseBudgetExceeded
from jcodemunch_mcp.tools import _indexing_pipeline
from jcodemunch_mcp.tools.index_folder import index_folder


FAILURES = {
    "budget": lambda: ParseBudgetExceeded("parse exceeded the 1s budget"),
    "crash": lambda: RuntimeError("grammar raised"),
}


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path / "storage"))
    root = tmp_path / "project"
    root.mkdir()
    (root / "slow_module.py").write_text(
        "def only_in_the_slow_file():\n    return 1\n", encoding="utf-8"
    )
    (root / "helper.py").write_text(
        "def ordinary_helper():\n    return 2\n", encoding="utf-8"
    )
    return root


def _fail_one_file(monkeypatch, failure):
    """Make the parse of `slow_module.py` raise on every indexing route: the
    full walk calls `index_folder.parse_file`, the targeted and incremental
    routes call `_indexing_pipeline.parse_file`."""
    from jcodemunch_mcp.tools import index_folder as index_folder_module

    for module in (_indexing_pipeline, index_folder_module):
        real = module.parse_file

        def parse(content, rel_path, language, *args, _real=real, **kwargs):
            if rel_path.endswith("slow_module.py"):
                raise failure()
            return _real(content, rel_path, language, *args, **kwargs)

        monkeypatch.setattr(module, "parse_file", parse)


def _index(project, tmp_path):
    return index_folder(
        str(project),
        use_ai_summaries=False,
        storage_path=str(tmp_path / "store"),
        incremental=False,
        context_providers=False,
    )


@pytest.mark.parametrize("kind", sorted(FAILURES))
def test_a_failed_parse_makes_the_corpus_incomplete(project, tmp_path, monkeypatch, kind):
    _fail_one_file(monkeypatch, FAILURES[kind])
    result = _index(project, tmp_path)
    assert result.get("success"), result
    assert any("slow_module.py" in w for w in result.get("warnings") or []), result

    from jcodemunch_mcp.tools._utils import load_repo_index_or_error

    index, error, _ = load_repo_index_or_error(result["repo"], str(tmp_path / "store"))
    assert error is None
    coverage = index.coverage or {}
    assert coverage.get("complete") is not True, coverage
    assert coverage.get("withheld"), coverage


@pytest.mark.parametrize("kind", sorted(FAILURES))
def test_absence_of_a_name_in_the_failed_file_is_not_citable(project, tmp_path, monkeypatch, kind):
    _fail_one_file(monkeypatch, FAILURES[kind])
    result = _index(project, tmp_path)
    assert result.get("success"), result

    from jcodemunch_mcp.tools.search_symbols import search_symbols

    found = search_symbols(
        result["repo"], "only_in_the_slow_file",
        storage_path=str(tmp_path / "store"), max_results=10,
    )
    names = [r.get("name") for r in found.get("results") or []]
    assert "only_in_the_slow_file" not in names
    # The dispatcher mints the `absent:` token only from a verdict whose index
    # channel is clean; `partial` is the channel that refuses it.
    verdict = (found.get("_meta") or {}).get("verdict") or {}
    assert (verdict.get("channels") or {}).get("index") == "partial", verdict


def test_a_clean_parse_still_proves_absence(project, tmp_path):
    """Non-vacuity: with no failure the same corpus is complete."""
    result = _index(project, tmp_path)
    from jcodemunch_mcp.tools._utils import load_repo_index_or_error

    index, error, _ = load_repo_index_or_error(result["repo"], str(tmp_path / "store"))
    assert error is None
    assert (index.coverage or {}).get("complete") is True, index.coverage

    from jcodemunch_mcp.tools.search_symbols import search_symbols

    found = search_symbols(
        result["repo"], "name_nobody_wrote",
        storage_path=str(tmp_path / "store"), max_results=10,
    )
    verdict = (found.get("_meta") or {}).get("verdict") or {}
    assert verdict, found
    assert (verdict.get("channels") or {}).get("index") != "partial", verdict


# ── every route that parses records the failure, and later runs keep it true ──


def _coverage(result, tmp_path):
    from jcodemunch_mcp.tools._utils import load_repo_index_or_error

    index, error, _ = load_repo_index_or_error(result["repo"], str(tmp_path / "store"))
    assert error is None
    return index.coverage or {}


def _incremental(project, tmp_path, **kwargs):
    return index_folder(
        str(project),
        use_ai_summaries=False,
        storage_path=str(tmp_path / "store"),
        incremental=True,
        context_providers=False,
        **kwargs,
    )


def test_an_incremental_run_that_skips_the_file_keeps_it_withheld(project, tmp_path, monkeypatch):
    """The failed file is unchanged, so the next run does not re-parse it and
    must carry the failure rather than call the corpus complete."""
    _fail_one_file(monkeypatch, FAILURES["budget"])
    _index(project, tmp_path)
    (project / "helper.py").write_text("def ordinary_helper():\n    return 3\n", encoding="utf-8")
    result = _incremental(project, tmp_path)
    cov = _coverage(result, tmp_path)
    assert cov.get("complete") is False, cov
    assert (cov.get("withheld") or {}).get("parse_failed") == 1, cov


def test_a_file_that_parses_again_clears_its_failure(project, tmp_path, monkeypatch):
    _fail_one_file(monkeypatch, FAILURES["crash"])
    _index(project, tmp_path)
    monkeypatch.undo()
    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path / "storage"))
    (project / "slow_module.py").write_text(
        "def only_in_the_slow_file():\n    return 10\n", encoding="utf-8"
    )
    result = _incremental(project, tmp_path)
    cov = _coverage(result, tmp_path)
    assert cov.get("complete") is True, cov
    assert "parse_failed" not in (cov.get("withheld") or {}), cov


def test_a_deleted_failed_file_leaves_no_failure(project, tmp_path, monkeypatch):
    _fail_one_file(monkeypatch, FAILURES["crash"])
    _index(project, tmp_path)
    (project / "slow_module.py").unlink()
    result = _incremental(project, tmp_path)
    cov = _coverage(result, tmp_path)
    assert "parse_failed" not in (cov.get("withheld") or {}), cov
    assert cov.get("complete") is True, cov


def _complete_then_break(project, tmp_path, monkeypatch):
    first = _index(project, tmp_path)
    assert _coverage(first, tmp_path).get("complete") is True
    (project / "slow_module.py").write_text(
        "def only_in_the_slow_file():\n    return 20\n", encoding="utf-8"
    )
    _fail_one_file(monkeypatch, FAILURES["budget"])
    return first


def test_a_paths_reindex_records_the_failure(project, tmp_path, monkeypatch):
    _complete_then_break(project, tmp_path, monkeypatch)
    result = _incremental(project, tmp_path, paths=[str(project / "slow_module.py")])
    assert result.get("success"), result
    cov = _coverage(result, tmp_path)
    assert cov.get("complete") is False, cov
    assert (cov.get("withheld") or {}).get("parse_failed") == 1, cov


def test_a_watcher_reindex_records_the_failure(project, tmp_path, monkeypatch):
    from jcodemunch_mcp.reindex_state import WatcherChange

    _complete_then_break(project, tmp_path, monkeypatch)
    result = _incremental(project, tmp_path, changed_paths=[
        WatcherChange("modified", str((project / "slow_module.py").resolve()), "__cache_miss__"),
    ])
    assert result.get("success"), result
    cov = _coverage(result, tmp_path)
    assert cov.get("complete") is False, cov
    assert (cov.get("withheld") or {}).get("parse_failed") == 1, cov


def test_an_index_file_reindex_records_the_failure(project, tmp_path, monkeypatch):
    from jcodemunch_mcp.tools.index_file import index_file

    first = _complete_then_break(project, tmp_path, monkeypatch)
    result = index_file(
        str(project / "slow_module.py"), use_ai_summaries=False,
        storage_path=str(tmp_path / "store"), context_providers=False,
    )
    assert result.get("success"), result
    cov = _coverage(first, tmp_path)
    assert cov.get("complete") is False, cov
    assert (cov.get("withheld") or {}).get("parse_failed") == 1, cov


def test_the_failed_file_names_stay_out_of_the_answer():
    """The names are kept for carrying; the answer carries only the count."""
    from types import SimpleNamespace

    from jcodemunch_mcp.retrieval.verdict import index_coverage_meta
    from jcodemunch_mcp.tools.index_folder import PARSE_FAILED_FILES_KEY, _with_parse_failures

    cov = _with_parse_failures(
        {"complete": True, "files_indexed": 2, "files_accepted": 2, "skip_counts": {}},
        ["a.py"],
    )
    out = index_coverage_meta(SimpleNamespace(coverage=cov, indexed_at="", index_version=1, git_head="", source_roots=None))
    assert out["complete"] is False
    assert out["withheld"] == {"parse_failed": 1}
    assert PARSE_FAILED_FILES_KEY not in out
