"""The token benchmark runs a team's own queries on a team's own folder.

`run_benchmark.py` measured only the queries in `benchmarks/tasks.json`, on
repos named `owner/repo`. A self-run A/B kit needs both moved: `--tasks FILE`
reads someone's own queries, and a repo argument that is a local folder
resolves to that folder's index through `resolve_repo`. A run over custom
queries can never write the published reference or judge the Floors, which
describe the published corpus only.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

import pytest

HARNESS = pathlib.Path(__file__).resolve().parents[1] / "benchmarks" / "harness"


@pytest.fixture(scope="module")
def rb():
    pytest.importorskip("tiktoken")
    spec = importlib.util.spec_from_file_location(
        "_bench_run_benchmark_own_tasks", HARNESS / "run_benchmark.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_a_task_file_takes_the_tasks_json_shape(rb, tmp_path):
    f = tmp_path / "t.json"
    f.write_text(
        json.dumps({"tasks": [{"query": "login handler"}, {"query": " retry "}]}),
        encoding="utf-8",
    )
    assert rb.load_task_file(f) == ["login handler", "retry"]


def test_a_task_file_takes_a_plain_list(rb, tmp_path):
    f = tmp_path / "t.json"
    f.write_text(json.dumps(["cache", "auth token"]), encoding="utf-8")
    assert rb.load_task_file(f) == ["cache", "auth token"]


@pytest.mark.parametrize("body", [[], {"tasks": []}, [""], [{"q": "x"}], {"nope": 1}])
def test_an_empty_or_malformed_task_file_is_refused(rb, tmp_path, body):
    f = tmp_path / "t.json"
    f.write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(ValueError):
        rb.load_task_file(f)


@pytest.mark.parametrize("flag", ["--reference", "--floor"])
def test_own_tasks_never_write_the_reference_or_judge_the_floors(
    rb, tmp_path, monkeypatch, flag
):
    f = tmp_path / "t.json"
    f.write_text(json.dumps(["cache"]), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["run_benchmark.py", "--tasks", str(f), flag])
    monkeypatch.setattr(rb, "benchmark_repo", lambda *a: pytest.fail("measured anyway"))
    assert rb.main() == 2


def test_an_id_that_is_not_a_folder_is_unchanged(rb):
    assert rb.resolve_repo_arg("expressjs/express") == "expressjs/express"


def test_an_indexed_local_folder_resolves_to_its_index(rb, tmp_path, monkeypatch):
    proj = tmp_path / "myapp"
    proj.mkdir()
    (proj / "app.py").write_text("def handler():\n    return 1\n", encoding="utf-8")
    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path / "store"))
    monkeypatch.setenv("JCODEMUNCH_TRUSTED_FOLDERS", str(tmp_path))
    from jcodemunch_mcp.tools.index_folder import index_folder

    res = index_folder(str(proj), use_ai_summaries=False)
    assert res["success"]
    assert rb.resolve_repo_arg(str(proj)) == res["repo"]


def test_an_unindexed_folder_is_named_in_the_error(rb, tmp_path, monkeypatch):
    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path / "store"))
    folder = tmp_path / "plain"
    folder.mkdir()
    assert rb.resolve_repo_arg(str(folder)) == str(folder)
    out = rb.benchmark_repo(str(folder))
    assert "jcodemunch-mcp index" in out["error"]
