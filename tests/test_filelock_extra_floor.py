"""The `semantic` and `all` extras cannot resolve to a filelock with a published advisory.

filelock reaches an install only through `sentence-transformers` (torch and
huggingface-hub depend on it, with no useful floor). pip resolves without our
lock, so before this file the extras let pip take whatever filelock was newest
for the interpreter. The advisories leave two clean ranges:

- 3.20.3 alone. Everything below it is GHSA-qmgc-5h2g-mvrw (and
  GHSA-w853-jp5j-5j7f below 3.20.1); 3.20.4 to 4.2.0 is GHSA-v4x8-3wr5-3wq8
  and GHSA-9vhv-jxww-xfm5, joined by four more further up the range.
- 4.2.1 and later. Those need Python 3.11.

So on Python 3.10 the newest installable filelock is a vulnerable 4.1.0, and a
plain floor (`>=3.20.3`) still admits it there. A floor at 4.2.1 without a
marker is wrong the other way: nothing satisfies it on 3.10, so the extra
cannot be installed there at all. The 3.10 requirement is an exact
pin; 3.11 and later get a floor at the fix.

Each extra is evaluated for each interpreter the project supports, with the
markers applied, so a requirement that is right for one Python and wrong for
another fails here.
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
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version

ROOT = Path(__file__).resolve().parent.parent
_NAME = "filelock"

# Releases inside the advisory ranges, with the edges.
_VULNERABLE = (
    "3.10.0", "3.16.1", "3.20.0", "3.20.1", "3.20.2", "3.20.4", "3.22.0", "3.25.2",
    "4.0.0", "4.1.0", "4.1.1", "4.2.0",
)
_CLEAN = ("3.20.3", "4.2.1", "4.2.4", "5.0.0")
# The newest filelock each interpreter can install, at the time of writing:
# 4.1.1 and later need Python 3.11 (PyPI `requires_python`).
_INSTALLABLE_MAX = {"3.10": Version("4.1.0")}

PYTHONS = ("3.10", "3.11", "3.12", "3.13", "3.14")


def _env(python: str) -> dict[str, str]:
    return {"python_version": python, "python_full_version": python + ".0", "extra": ""}


def _effective(entries: list[str], python: str) -> SpecifierSet | None:
    """The combined filelock specifier an extra applies on `python`, or None if it applies none."""
    spec = None
    for entry in entries:
        req = Requirement(entry)
        if canonicalize_name(req.name) != _NAME:
            continue
        if req.marker is not None and not req.marker.evaluate(_env(python)):
            continue
        spec = req.specifier if spec is None else spec & req.specifier
    return spec


def _installable(version: str, python: str) -> bool:
    cap = _INSTALLABLE_MAX.get(python)
    return cap is None or Version(version) <= cap


def _installable_clean(spec: SpecifierSet | None, python: str) -> list[str]:
    """Clean releases the spec admits that `python` can install; empty means the extra cannot install."""
    return [v for v in _CLEAN if _installable(v, python) and (spec is None or spec.contains(v))]


def _admitted(spec: SpecifierSet | None, python: str) -> list[str]:
    cap = _INSTALLABLE_MAX.get(python)
    candidates = [v for v in _VULNERABLE if _installable(v, python)]
    if spec is None:
        return candidates
    return [v for v in candidates if spec.contains(v)]


def _extras() -> dict[str, list[str]]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["optional-dependencies"]


def _pulls_sentence_transformers(entries: list[str]) -> bool:
    return any(canonicalize_name(Requirement(e).name) == "sentence-transformers" for e in entries)


def test_the_extras_that_pull_filelock_are_semantic_and_all():
    """Non-vacuity: a new extra that pulls sentence-transformers is checked below too."""
    assert sorted(k for k, v in _extras().items() if _pulls_sentence_transformers(v)) == ["all", "semantic"]


@pytest.mark.parametrize("python", PYTHONS)
@pytest.mark.parametrize("extra", ["all", "semantic"])
def test_the_extra_admits_no_vulnerable_filelock(extra, python):
    entries = _extras()[extra]
    spec = _effective(entries, python)
    assert _admitted(spec, python) == [], (extra, python, str(spec))
    assert spec is not None and _installable_clean(spec, python), (extra, python, str(spec))


@pytest.mark.parametrize("python", ["3.11", "3.12", "3.13", "3.14"])
def test_python_311_and_later_keep_every_fixed_release(python):
    """Nothing changes for anyone already safe: the floor admits every clean release from 4.2.1 on."""
    for extra in ("all", "semantic"):
        spec = _effective(_extras()[extra], python)
        assert all(spec.contains(v) for v in ("4.2.1", "4.2.4", "5.0.0")), (extra, python, str(spec))


@pytest.mark.parametrize(
    "entries,python,expect_admitted",
    [
        (["sentence-transformers>=5.6.0"], "3.10", True),
        (["sentence-transformers>=5.6.0", "filelock>=3.20.3"], "3.10", True),
        (["sentence-transformers>=5.6.0", "filelock>=4.2.1; python_version >= '3.11'"], "3.10", True),
        (["sentence-transformers>=5.6.0", "filelock==3.20.3; python_version < '3.11'"], "3.10", False),
        (["sentence-transformers>=5.6.0", "filelock==3.20.3; python_version < '3.11'"], "3.12", True),
        (["sentence-transformers>=5.6.0", "filelock>=4.2.1; python_version >= '3.11'"], "3.12", False),
    ],
)
def test_the_check_sees_a_floor_that_is_wrong_for_one_interpreter(entries, python, expect_admitted):
    assert bool(_admitted(_effective(entries, python), python)) is expect_admitted


@pytest.mark.parametrize(
    "entries",
    [
        ["sentence-transformers>=5.6.0", "filelock>=4.2.1"],
        ["sentence-transformers>=5.6.0", "filelock>=4.2.1; python_version < '3.11'"],
        ["sentence-transformers>=5.6.0", "filelock>=4.2.4"],
    ],
)
def test_the_check_sees_a_floor_that_makes_the_extra_uninstallable_on_310(entries):
    """No vulnerable release is admitted, and no release 3.10 can install is either."""
    spec = _effective(entries, "3.10")
    assert _admitted(spec, "3.10") == []
    assert _installable_clean(spec, "3.10") == []


def test_the_pinned_extras_install_on_310():
    for extra in ("all", "semantic"):
        assert _installable_clean(_effective(_extras()[extra], "3.10"), "3.10") == ["3.20.3"], extra


def test_the_lock_holds_only_clean_filelock_releases():
    text = (ROOT / "uv.lock").read_text(encoding="utf-8")
    versions = re.findall(r'(?m)^name = "filelock"\r?\nversion = "([^"]+)"', text)
    assert versions
    assert all(v not in _VULNERABLE and (v == "3.20.3" or Version(v) >= Version("4.2.1")) for v in versions), versions
