"""The `semantic` extra cannot resolve to a sentence-transformers that runs a local model's code unasked.

GHSA-jhr6-gm9c-rqjv (critical): before 5.6.0, loading a LOCAL model directory
bypassed `trust_remote_code` and executed the custom Python inside it.
`embed_repo` passes the `embed_model` config key, or `JCODEMUNCH_EMBED_MODEL`,
straight to `SentenceTransformer`, and that value may be a path. The extra declared `>=2.2.0`, so a fresh
`pip install "jcodemunch-mcp[semantic]"` took the latest release and was fine,
while an environment that already held an older one satisfied the requirement
and stayed exposed. The floor is the fixed release.

Every tracked requirements file is read too: `benchmarks/requirements-rag-bench.txt`
pinned `<4.0`, which REQUIRED a vulnerable release, and a check of the
extras alone could not see it.

The lock is checked as well: the repository's own environments are built from
it, and it held 5.3.0 and a urllib3 with three open advisories
(GHSA-8988-9cw3-xx77, GHSA-vxq7-64xx-v4gw, GHSA-gh4c-6fx4-qh6g).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # 3.10: pytest depends on tomli there
    import tomli as tomllib

import pytest
from packaging.requirements import Requirement
from packaging.version import Version

ROOT = Path(__file__).resolve().parent.parent
FIXED = {"sentence-transformers": Version("5.6.0"), "urllib3": Version("2.8.0")}


def _extras() -> dict[str, list[Requirement]]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return {k: [Requirement(r) for r in v] for k, v in data["project"]["optional-dependencies"].items()}


def test_every_extra_that_names_sentence_transformers_excludes_the_vulnerable_releases():
    fixed = FIXED["sentence-transformers"]
    seen = []
    for extra, reqs in _extras().items():
        for req in reqs:
            if req.name == "sentence-transformers":
                seen.append(extra)
                vulnerable = [v for v in ("2.2.0", "4.1.0", "5.3.0", "5.5.1") if req.specifier.contains(v)]
                assert vulnerable == [], (extra, str(req), vulnerable)
                assert req.specifier.contains(str(fixed)), (extra, str(req))
    assert sorted(seen) == ["all", "semantic"], seen


_OUTSIDE_THE_TREE = {".venv", ".git", "node_modules", "build", "dist", ".claude", "__pycache__"}
_VULNERABLE_SAMPLES = ("2.2.0", "3.4.1", "4.1.0", "5.3.0", "5.5.1")


def _requirement_lines():
    for path in sorted(ROOT.rglob("*requirements*.txt")):
        if _OUTSIDE_THE_TREE & set(path.relative_to(ROOT).parts):
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line.lower().startswith("sentence-transformers"):
                yield path.relative_to(ROOT).as_posix(), Requirement(line)


def test_no_requirements_file_admits_a_vulnerable_sentence_transformers():
    found = list(_requirement_lines())
    assert [rel for rel, _ in found] == ["benchmarks/requirements-rag-bench.txt"], found
    for rel, req in found:
        vulnerable = [v for v in _VULNERABLE_SAMPLES if req.specifier.contains(v)]
        assert vulnerable == [], (rel, str(req), vulnerable)
        assert any(req.specifier.contains(v) for v in ("5.6.0", "6.1.0")), (rel, str(req))


@pytest.mark.parametrize("name", sorted(FIXED))
def test_the_lock_holds_a_fixed_release(name):
    text = (ROOT / "uv.lock").read_text(encoding="utf-8")
    versions = re.findall(rf'(?m)^name = "{re.escape(name)}"\r?\nversion = "([^"]+)"', text)
    assert versions, name
    assert all(Version(v) >= FIXED[name] for v in versions), (name, versions)
