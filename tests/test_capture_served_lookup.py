"""The served-lookup capture writes the texts the benchmark counts, and no secrets.

`benchmarks/harness/capture_served_lookup.py` exists to diff two machines'
served text for the same lookup byte for byte. It is only useful if what it
writes is exactly what `run_benchmark` counted, so this test asserts the
captured files add up to the benchmark's own totals, computed by a second,
uncaptured call. Its environment record must carry variable names and never
their values: a CI runner's environment holds tokens.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import sys
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parents[1] / "benchmarks" / "harness"


@pytest.fixture
def indexed_repo(tmp_path, monkeypatch):
    src = tmp_path / "demo"
    src.mkdir()
    (src / "router.py").write_text(
        "def add_route(path, handler):\n    return (path, handler)\n\n\n"
        "def dispatch(routes, path):\n    for p, h in routes:\n        if p == path:\n            return h\n",
        encoding="utf-8",
    )
    (src / "params.py").write_text(
        "def bind_params(request, model):\n    return model(**request)\n",
        encoding="utf-8",
    )
    storage = tmp_path / "index"
    monkeypatch.setenv("CODE_INDEX_PATH", str(storage))
    from jcodemunch_mcp.tools.index_folder import index_folder

    repo = index_folder(str(src), use_ai_summaries=False, storage_path=str(storage))["repo"]
    monkeypatch.syspath_prepend(str(_HARNESS))
    for mod in ("capture_served_lookup", "run_benchmark"):
        sys.modules.pop(mod, None)
    return repo


def test_the_capture_is_what_the_benchmark_counts(indexed_repo, tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TEST_SECRET", "do-not-write-this-value")
    cap = importlib.import_module("capture_served_lookup")
    rb = cap.rb
    real = rb._served_text
    # The capture must run the benchmark's own setup, not a copy of it: a
    # second, uncaptured call in this process cannot see a divergence.
    setups = []
    real_setup = rb.served_setup
    monkeypatch.setattr(rb, "served_setup", lambda: (setups.append(1), real_setup())[1])
    out = tmp_path / "capture"
    queries = ["route handler", "param bind"]

    manifest = cap.capture(indexed_repo, queries, out)

    assert setups == [1], "capture() must call run_benchmark.served_setup()"
    assert rb._served_text is real, "the wrapper must be removed after the capture"
    assert any(row["hits_fetched"] for row in manifest["lookups"]), "the fixture must fetch"
    for query, row in zip(queries, manifest["lookups"]):
        expected = rb._measure_jmunch_served(manifest["repo_id"], query)
        assert row["tokens"] == expected["tokens"], query
        assert row["hits_fetched"] == expected["hits_fetched"], query
        mine = [c for c in manifest["calls"] if c["query"] == query]
        assert len(mine) == 1 + row["hits_fetched"], mine
        assert mine[0]["tool"] == "search_symbols"
        assert sum(c["tokens"] for c in mine) == row["tokens"]
        for call in mine:
            raw = (out / call["file"]).read_bytes()
            assert hashlib.sha256(raw).hexdigest() == call["sha256"]
            assert len(raw) == call["bytes"]

    env_text = (out / "environment.json").read_text(encoding="utf-8")
    env = json.loads(env_text)
    assert "CAPTURE_TEST_SECRET" in env["env_var_names"]
    assert "do-not-write-this-value" not in env_text
    assert env["packages"]["mcp"]
    assert env["config_path"]


def test_a_folder_no_index_resolves_to_is_refused(indexed_repo, tmp_path):
    cap = importlib.import_module("capture_served_lookup")
    stray = tmp_path / "not-indexed"
    stray.mkdir()
    with pytest.raises(SystemExit):
        cap.capture(str(stray), ["route handler"], tmp_path / "out")


def test_the_served_setup_keeps_a_runner_off_the_savings_total(indexed_repo, monkeypatch):
    """cicd C-23: the setup replaces the config the env opt-out sets, so it pins it."""
    monkeypatch.setenv("JCODEMUNCH_SHARE_SAVINGS", "0")
    cap = importlib.import_module("capture_served_lookup")
    from jcodemunch_mcp import config

    cap.rb.served_setup()
    assert config.get("share_savings") is False
