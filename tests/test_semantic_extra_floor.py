"""The `semantic` extra cannot resolve to a sentence-transformers that runs a local model's code unasked.

GHSA-jhr6-gm9c-rqjv (critical): before 5.6.0, loading a LOCAL model directory
bypassed `trust_remote_code` and executed the custom Python inside it.
`embed_repo` passes the `embed_model` config key, or `JCODEMUNCH_EMBED_MODEL`,
straight to `SentenceTransformer`, and that value may be a path. The extra declared `>=2.2.0`, so a fresh
`pip install "jcodemunch-mcp[semantic]"` took the latest release and was fine,
while an environment that already held an older one satisfied the requirement
and stayed exposed. The floor is the fixed release.

Three tables of `pyproject.toml` are read, by canonical name: `project.dependencies`, the
extras and the dependency groups. `build-system.requires` and a `[tool.*]` table are not; a
`tool.uv` constraint reaches the lock, which is checked below. Every tracked requirements,
constraints and `.pins` file is read too, with what it includes; a line there that names the
package and cannot be parsed fails the test: `benchmarks/requirements-rag-bench.txt`
pinned `<4.0`, which REQUIRED a vulnerable release, and a check of the
extras alone could not see it.

The lock is checked as well: the repository's own environments are built from
it, and it held 5.3.0 and a urllib3 with three open advisories
(GHSA-8988-9cw3-xx77, GHSA-vxq7-64xx-v4gw, GHSA-gh4c-6fx4-qh6g).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # 3.10: pytest depends on tomli there
    import tomli as tomllib

import pytest
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

ROOT = Path(__file__).resolve().parent.parent
FIXED = {"sentence-transformers": Version("5.6.0"), "urllib3": Version("2.8.0")}


_NAME = "sentence-transformers"
# Releases before the fix, the 5.x line in full, and a version just under the floor.
_VULNERABLE_SAMPLES = (
    "2.2.0", "2.7.0", "3.0.0", "3.4.1", "4.0.0", "4.1.0", "5.0.0", "5.1.0", "5.1.1", "5.1.2", "5.2.0",
    "5.2.1", "5.2.2", "5.2.3", "5.3.0", "5.4.0", "5.4.1", "5.5.0", "5.5.1", "5.5.999",
)
_FIXED_SAMPLES = ("5.6.0", "6.0.1", "6.1.0")


def _admitted(req: Requirement) -> list[str]:
    return [v for v in _VULNERABLE_SAMPLES if req.specifier.contains(v)]


def _pyproject_requirements(data: dict):
    """(table, key, Requirement) for every requirement string in the project's metadata."""
    project = data.get("project", {})
    tables = {
        "dependencies": {"": project.get("dependencies", [])},
        "optional-dependencies": project.get("optional-dependencies", {}),
        "dependency-groups": data.get("dependency-groups", {}),
    }
    for table, groups in tables.items():
        for key, entries in groups.items():
            for entry in entries:
                if isinstance(entry, str):  # a dependency group may hold {include-group = ...}
                    yield table, key, Requirement(entry)


def _named(data: dict):
    return [(t, k, r) for t, k, r in _pyproject_requirements(data) if canonicalize_name(r.name) == _NAME]


def test_every_extra_that_names_sentence_transformers_excludes_the_vulnerable_releases():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    found = _named(data)
    assert sorted((t, k) for t, k, _ in found) == [
        ("optional-dependencies", "all"),
        ("optional-dependencies", "semantic"),
    ], found
    for table, key, req in found:
        assert _admitted(req) == [], (table, key, str(req))
        assert req.specifier.contains(str(FIXED[_NAME])), (table, key, str(req))


@pytest.mark.parametrize(
    "toml,where",
    [
        ('[project.optional-dependencies]\nembed2 = ["sentence_transformers>=2.2.0"]\n', "optional-dependencies"),
        ('[dependency-groups]\nst = ["Sentence.Transformers>=2.2.0,<4.0", {include-group = "x"}]\nx = []\n', "dependency-groups"),
        ('[project]\ndependencies = ["sentence--transformers>=5.4.0,!=5.5.1"]\n', "dependencies"),
    ],
)
def test_the_pyproject_scan_reads_every_table_and_spelling(toml, where):
    found = _named(tomllib.loads(toml))
    assert [t for t, _, _ in found] == [where], found
    assert _admitted(found[0][2]) != []


_PIN_FILE = re.compile(
    r"(^|/)(requirements|constraints)/.+\.(txt|in|lock)$|(requirements|constraints)[^/]*\.(txt|in|lock)$|\.pins$",
    re.IGNORECASE,
)
_INCLUDE = re.compile(r"(?:-r|-c|--requirement|--constraint)[\s=]*(\S+)")


@pytest.mark.parametrize(
    "rel,is_pin_file",
    [
        ("requirements.txt", True),
        ("benchmarks/requirements-rag-bench.txt", True),
        ("requirements/bench.txt", True),
        ("requirements/x.lock", True),
        ("requirements/sub/x.txt", True),
        ("constraints/ci.txt", True),
        ("requirements.lock", True),
        ("constraints-ci.in", True),
        ("benchmarks/competitive/sandbox/aider.pins", True),
        ("deps/base.txt", False),  # read only when a pin file includes it
        ("docs/requirements.md", False),
        ("src/requirements/loader.py", False),
    ],
)
def test_the_file_names_read_as_pin_files(rel, is_pin_file):
    assert bool(_PIN_FILE.search(rel)) is is_pin_file


def _tracked_pin_files() -> list[str]:
    """Tracked files that pin packages: requirements and constraints files and directories, and `.pins`."""
    out = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files"], capture_output=True, text=True, encoding="utf-8", check=True
    ).stdout
    return sorted(rel for rel in out.splitlines() if _PIN_FILE.search(rel))


def _names_the_package(text: str) -> bool:
    return _NAME in re.sub(r"[-_.]+", "-", text.lower())


def _read_pin_file(rel: str, done: set[str]):
    """(file, Requirement or None) per line naming the package; None is a line that could not be parsed.

    A `-r` or `-c` target is followed. A line `Requirement` rejects (a URL, a VCS
    link, an editable, a wheel path) is not dropped: if its text names the
    package it is yielded as unreadable, and the caller fails on it.
    """
    if rel in done:
        return
    done.add(rel)
    path = ROOT / rel
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = re.sub(r"(^|\s+)#.*$", "", raw).strip()
        include = _INCLUDE.match(line)
        if include:
            target = path.parent / include.group(1)
            if target.is_file():
                yield from _read_pin_file(target.resolve().relative_to(ROOT.resolve()).as_posix(), done)
            continue
        line = re.split(r"\s+--", line, maxsplit=1)[0].rstrip(chr(92)).strip()
        if not line:
            continue
        try:
            req = None if line.startswith("-") else Requirement(line)
        except InvalidRequirement:
            req = None
        if req is None:
            if _names_the_package(line):
                yield rel, None
        elif canonicalize_name(req.name) == _NAME:
            yield rel, req


def _requirement_lines():
    done: set[str] = set()
    for rel in _tracked_pin_files():
        yield from _read_pin_file(rel, done)


def test_no_requirements_file_admits_a_vulnerable_sentence_transformers():
    """Over TRACKED pin files and what they include, by canonical name.

    The files read are the ones `_PIN_FILE` names plus every `-r`/`-c` target;
    a pin written anywhere else is not seen here.
    """
    found = list(_requirement_lines())
    assert [rel for rel, _ in found] == [
        "benchmarks/competitive/sandbox/cocoindex.pins",
        "benchmarks/requirements-rag-bench.txt",
    ], found
    for rel, req in found:
        assert req is not None, (rel, "a line names the package and could not be parsed")
        assert _admitted(req) == [], (rel, str(req))
        assert any(req.specifier.contains(v) for v in _FIXED_SAMPLES), (rel, str(req))


_GIT = "git+https://github.com/UKPLab/sentence-transformers@v3.0.0"


@pytest.mark.parametrize(
    "line,seen",
    [
        ("sentence_transformers==3.0.0", "parsed"),
        ("Sentence.Transformers>=2.2.0,<4.0", "parsed"),
        ("sentence-transformers==6.0.1 " + chr(92), "parsed"),
        ("sentence__transformers==3.0.0", "parsed"),
        ("sentence-transformers[train]==3.0.0 --hash=sha256:00", "parsed"),
        ("sentence-transformers==3.0.0" + chr(9) + "--hash=sha256:00", "parsed"),
        ("sentence-transformers @ " + _GIT, "parsed"),
        (_GIT + "#egg=sentence-transformers", "unreadable"),
        ("git+https://example.invalid/st.git#egg=sentence_transformers", "unreadable"),
        ("-e " + _GIT, "unreadable"),
        ("https://example.invalid/sentence_transformers-3.0.0-py3-none-any.whl", "unreadable"),
        ("./vendor/sentence_transformers-3.0.0-py3-none-any.whl", "unreadable"),
        ("sentence-transformers-extras==1.0", "absent"),
        ("-r other.txt", "absent"),
        ("numpy>=1.0  # sentence-transformers is pinned elsewhere", "absent"),
    ],
)
def test_the_scan_reads_every_spelling_of_the_name(tmp_path, monkeypatch, line, seen):
    (tmp_path / "requirements-x.txt").write_text("numpy>=1.0\n" + line + "\n", encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "ROOT", tmp_path)
    monkeypatch.setattr(sys.modules[__name__], "_tracked_pin_files", lambda: ["requirements-x.txt"])
    found = list(_requirement_lines())
    if seen == "absent":
        assert found == []
    else:
        assert len(found) == 1 and (found[0][1] is None) is (seen == "unreadable"), found
        if seen == "parsed":
            assert _admitted(found[0][1]) != [] or found[0][1].specifier.contains("6.0.1")


def test_the_scan_reads_past_a_byte_order_mark_and_follows_includes(tmp_path, monkeypatch):
    (tmp_path / "deps").mkdir()
    (tmp_path / "deps" / "base.txt").write_text("sentence-transformers==3.0.0\n-r ../requirements.txt\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("-r deps/base.txt\n--constraint=deps/base.txt\n", encoding="utf-8")
    (tmp_path / "constraints.txt").write_text("sentence-transformers==3.4.1\n", encoding="utf-8-sig")
    monkeypatch.setattr(sys.modules[__name__], "ROOT", tmp_path)
    monkeypatch.setattr(sys.modules[__name__], "_tracked_pin_files", lambda: ["constraints.txt", "requirements.txt"])
    assert [(rel, str(req)) for rel, req in _requirement_lines()] == [
        ("constraints.txt", "sentence-transformers==3.4.1"),
        ("deps/base.txt", "sentence-transformers==3.0.0"),
    ]


@pytest.mark.parametrize("name", sorted(FIXED))
def test_the_lock_holds_a_fixed_release(name):
    text = (ROOT / "uv.lock").read_text(encoding="utf-8")
    versions = re.findall(rf'(?m)^name = "{re.escape(name)}"\r?\nversion = "([^"]+)"', text)
    assert versions, name
    assert all(Version(v) >= FIXED[name] for v in versions), (name, versions)
