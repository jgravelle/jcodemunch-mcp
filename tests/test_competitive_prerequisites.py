"""Criterion 6's prerequisite count is read from the Dockerfile the image was
built from, per pin, beside the build cost (competitive FINDINGS CF-61).

DESIGN s2 names the count of prerequisites a Dockerfile had to install beyond
the package as a proxy for install friction, labelled as one, and until now
it was designed and not measured. `sandbox.prerequisites` reads the system
packages every `apt-get install` in the Dockerfile names (flags dropped,
continuation lines joined); `sandbox.build` carries them on the BuildResult
from the same file whose sha it records; `run.pin_record` carries the list
and the count per pin, None for an adapter with no image, and a measured
zero stays 0 (an image that installs nothing beyond its base is a real fact,
the opposite of an unreported one); `latest.md` prints the count beside the
build line. pip and npm dependencies are the package's own tree and are not
counted: the proxy is what a user must have installed BEFORE the package.
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
    # Never pop these from sys.modules first (see test_competitive_reindex_one's
    # fixture): a re-import makes a second `Pin` class behind `adapters.*`.
    sys.path.insert(0, str(COMPETE))
    try:
        return {m: importlib.import_module(m) for m in ("adapter", "sandbox", "run")}
    finally:
        sys.path.remove(str(COMPETE))


DOCKERFILE = """# comment
FROM node:20-bookworm-slim@sha256:0000
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates git python3 make g++ \\
    && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/x
RUN apt-get install -y jq \\
    && npm ci --no-audit --no-fund
RUN pip install --no-cache-dir -r /opt/requirements.txt
"""


def test_the_parser_reads_every_apt_package_and_drops_flags(mods, tmp_path):
    sandbox = mods["sandbox"]
    df = tmp_path / "x.Dockerfile"
    df.write_text(DOCKERFILE, encoding="utf-8")
    assert sandbox.prerequisites(df) == ["ca-certificates", "g++", "git", "jq", "make", "python3"]


def test_a_dockerfile_that_installs_nothing_reads_as_an_empty_list(mods, tmp_path):
    sandbox = mods["sandbox"]
    df = tmp_path / "x.Dockerfile"
    df.write_text("FROM python:3.13-slim\nRUN pip install --no-deps /wheels/*.whl\n", encoding="utf-8")
    assert sandbox.prerequisites(df) == []


def test_build_carries_the_prerequisites_of_the_file_it_built(mods, tmp_path):
    sandbox = mods["sandbox"]
    df = tmp_path / "x.Dockerfile"
    df.write_text(DOCKERFILE, encoding="utf-8")

    def fake_run(cmd, **kw):
        if cmd[:2] == ["docker", "build"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="sha256:abc\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="sha256:0123|123\n", stderr="")

    with mock.patch.object(sandbox.subprocess, "run", fake_run):
        r = sandbox.build("img:1", df, tmp_path)
    assert list(r.prerequisites) == ["ca-certificates", "g++", "git", "jq", "make", "python3"]


def test_the_pin_record_carries_the_count_or_none(mods):
    run, adapter, sandbox = mods["run"], mods["adapter"], mods["sandbox"]

    class Built:
        name = "graft"
        pin = adapter.Pin(registry="npm", package="graft", version="1.0")
        interface = "cli"
        _image = sandbox.BuildResult(tag="t", digest="sha256:" + "a" * 64, seconds=41.3, dockerfile_sha256="d" * 64,
                                     size_bytes=1, prerequisites=("ca-certificates", "git", "python3"))

        def image(self):
            return self._image

        def version(self):
            return "1.0"

    class Bare:
        name = "jcodemunch"
        pin = adapter.Pin(registry="pypi", package="jcodemunch-mcp", version="1")
        interface = "python"
        _image = sandbox.BuildResult(tag="t", digest="sha256:" + "b" * 64, seconds=1.0, dockerfile_sha256="e" * 64, size_bytes=1, prerequisites=())

        def image(self):
            return self._image

        def version(self):
            return "1"

    class Unbuilt:
        name = "null_grep"
        pin = adapter.Pin(registry="none", package="grep", version="0")
        interface = "python"

        def version(self):
            return "0"

    rec = run.pin_record(Built())
    assert rec["prerequisites"] == ["ca-certificates", "git", "python3"] and rec["prerequisite_count"] == 3
    rec = run.pin_record(Bare())
    assert rec["prerequisites"] == [] and rec["prerequisite_count"] == 0, "a measured zero is a fact, not an absence"
    rec = run.pin_record(Unbuilt())
    assert rec["prerequisites"] is None and rec["prerequisite_count"] is None


def test_the_summary_prints_the_count_beside_the_build_line(mods):
    run = mods["run"]
    pins = [{"name": "graft", "registry": "npm", "package": "graft", "version": "1", "ran_as": "1",
             "image_digest": "sha256:0123456789abcdef", "image_build_seconds": 41.3, "image_size_bytes": 98765432,
             "prerequisites": ["ca-certificates", "git", "python3"], "prerequisite_count": 3},
            {"name": "bare", "registry": "pypi", "package": "b", "version": "1", "ran_as": "1",
             "image_digest": "sha256:fedcba9876543210", "image_build_seconds": 2.0, "image_size_bytes": 0,
             "prerequisites": [], "prerequisite_count": 0},
            {"name": "older", "registry": "pypi", "package": "o", "version": "1", "ran_as": "1",
             "image_digest": "sha256:aaaaaaaaaaaaaaaa", "image_build_seconds": 3.0, "image_size_bytes": 5}]
    header = {"date": "d", "jcm_commit": "c", "jcm_version": "v", "runs": 3, "corpora": [], "sandbox": "docker",
              "tree_dirty": False, "scorer_sha256": "f" * 64, "pins": pins}
    md = run.render_md({"header": header, "rows": [], "runs": [], "capability_only": [], "tools_not_called": [], "not_runnable": []})
    assert "3 prerequisites (ca-certificates, git, python3)" in md
    assert "0 prerequisites" in md
    assert md.count("prerequisite") == 2, "a result file written before the field prints no count"


def test_every_shipped_dockerfile_parses_and_ours_installs_nothing_beyond_its_base(mods):
    """Non-vacuity over the real files: the parser reads every sandbox Dockerfile,
    and our own image needs no system package the base does not carry."""
    sandbox = mods["sandbox"]
    files = sorted((COMPETE / "sandbox").glob("*.Dockerfile"))
    assert files
    counts = {f.name: sandbox.prerequisites(f) for f in files}
    assert counts["jcodemunch.Dockerfile"] == []
    assert any(counts[k] for k in counts if k != "jcodemunch.Dockerfile"), "the parser read nothing from any competitor file"
