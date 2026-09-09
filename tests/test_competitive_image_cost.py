"""STANDARD criterion 6 (install friction), the measured half: the image
build's wall seconds and the image's size travel in the pin record of every
result file (DESIGN s2; CF-61).

`sandbox.build` had the seconds in hand and dropped them into a log; the
size was never read. Now `BuildResult` carries `size_bytes` (from the same
`docker image inspect` that reads the digest) and `run.pin_record` puts
`image_build_seconds` and `image_size_bytes` beside the pin, None for an
adapter that has no image (the nulls, and a `none`-sandbox run of ours);
`latest.md` prints both. The prerequisite count DESIGN s2 also names stays
designed, not measured (CF-61), as does 3(b).
"""
from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMPETE = ROOT / "benchmarks" / "competitive"


@pytest.fixture(scope="module")
def mods():
    sys.path.insert(0, str(COMPETE))
    try:
        for m in ("adapter", "sandbox", "run"):
            sys.modules.pop(m, None)
        return {m: importlib.import_module(m) for m in ("adapter", "sandbox", "run")}
    finally:
        sys.path.remove(str(COMPETE))


def test_build_reads_the_size_from_the_same_inspect_as_the_digest(mods, tmp_path):
    sandbox = mods["sandbox"]
    df = tmp_path / "x.Dockerfile"
    df.write_text("FROM scratch\n", encoding="utf-8")
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if cmd[:2] == ["docker", "build"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="sha256:abc\n", stderr="")
        assert cmd[:3] == ["docker", "image", "inspect"], cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="sha256:0123|123456789\n", stderr="")

    with mock.patch.object(sandbox.subprocess, "run", fake_run):
        r = sandbox.build("img:1", df, tmp_path)
    assert r.digest == "sha256:0123" and r.size_bytes == 123456789
    assert isinstance(r.seconds, float) and r.seconds >= 0
    assert sum(1 for c in calls if c[:3] == ["docker", "image", "inspect"]) == 1


def test_a_size_docker_did_not_report_is_none_never_zero(mods, tmp_path):
    sandbox = mods["sandbox"]
    df = tmp_path / "x.Dockerfile"
    df.write_text("FROM scratch\n", encoding="utf-8")

    def fake_run(cmd, **kw):
        if cmd[:2] == ["docker", "build"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="sha256:0123|\n", stderr="")

    with mock.patch.object(sandbox.subprocess, "run", fake_run):
        r = sandbox.build("img:1", df, tmp_path)
    assert r.digest == "sha256:0123" and r.size_bytes is None


def test_the_pin_record_carries_build_seconds_and_size_or_none(mods):
    run, adapter, sandbox = mods["run"], mods["adapter"], mods["sandbox"]

    class Built:
        name = "cymbal"
        pin = adapter.Pin(registry="github-release", package="cymbal", version="0.14.0")
        interface = "cli"
        _image = sandbox.BuildResult(tag="t", digest="sha256:" + "a" * 64, seconds=41.3, dockerfile_sha256="d" * 64, size_bytes=98765432)

        def image(self):
            return self._image

        def version(self):
            return "0.14.0"

    class Unbuilt:
        name = "null_grep"
        pin = adapter.Pin(registry="none", package="grep", version="0")
        interface = "python"

        def version(self):
            return "0"

    rec = run.pin_record(Built())
    assert rec["image_digest"] == "sha256:" + "a" * 64
    assert rec["image_build_seconds"] == 41.3 and rec["image_size_bytes"] == 98765432
    rec = run.pin_record(Unbuilt())
    assert rec["image_digest"] is None and rec["image_build_seconds"] is None and rec["image_size_bytes"] is None


def test_the_summary_prints_build_seconds_and_size_beside_the_image(mods):
    run = mods["run"]
    pins = [{"name": "cymbal", "registry": "github-release", "package": "cymbal", "version": "0.14.0", "ran_as": "0.14.0",
             "image_digest": "sha256:0123456789abcdef", "image_build_seconds": 41.3, "image_size_bytes": 98765432},
            {"name": "zero", "registry": "npm", "package": "z", "version": "1", "ran_as": "1",
             "image_digest": "sha256:fedcba9876543210", "image_build_seconds": 2.0, "image_size_bytes": 0},
            {"name": "unsized", "registry": "npm", "package": "u", "version": "1", "ran_as": "1",
             "image_digest": "sha256:aaaaaaaaaaaaaaaa", "image_build_seconds": 3.0, "image_size_bytes": None},
            {"name": "null_grep", "registry": "none", "package": "grep", "version": "0", "ran_as": "0", "image_digest": None}]
    header = {"date": "d", "jcm_commit": "c", "jcm_version": "v", "runs": 3, "corpora": [], "sandbox": "docker",
              "tree_dirty": False, "scorer_sha256": "f" * 64, "pins": pins}
    md = run.render_md({"header": header, "rows": [], "runs": [], "capability_only": [], "tools_not_called": [], "not_runnable": []})
    assert "built in 41.3 s, 94.2 MiB" in md
    # a reported zero and an unreported size are different facts and render differently (review round 1)
    assert "built in 2.0 s, 0.0 MiB" in md and "built in 3.0 s)" in md
    assert md.count("built in") == 3  # the null has no image and no build line


def test_an_older_result_file_without_the_fields_still_renders(mods):
    run = mods["run"]
    pins = [{"name": "cymbal", "registry": "github-release", "package": "cymbal", "version": "0.14.0", "ran_as": "0.14.0",
             "image_digest": "sha256:0123456789abcdef"}]
    header = {"date": "d", "jcm_commit": "c", "jcm_version": "v", "runs": 3, "corpora": [], "sandbox": "docker",
              "tree_dirty": False, "scorer_sha256": "f" * 64, "pins": pins}
    md = run.render_md({"header": header, "rows": [], "runs": [], "capability_only": [], "tools_not_called": [], "not_runnable": []})
    assert "image `0123456789ab`" in md and "built in" not in md
