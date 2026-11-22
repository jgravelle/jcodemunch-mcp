"""The `mcp` floor admits no SDK release with a published advisory.

The lock resolved `mcp` 1.30.0, which carries no advisory, but the declared
floor was `>=1.10.0`. An installer that keeps an older `mcp` already in the
environment, or a scanner that reads the declared range, gets a release
affected by advisories fixed in 1.23.0, 1.27.2, 1.28.1 and 1.30.0 (OSV query,
2026-11-21): GHSA-9h52-p55h-vw2f, GHSA-jpw9-pfvf-9f58, GHSA-vj7q-gjh5-988w and
GHSA-qx49-fqc8-xw99 (high); GHSA-5h93-6whr-6q8j, GHSA-84m7-p3x7-pcfv,
GHSA-rwrf-2pqf-9j8j and GHSA-w4fh-qvv9-3v23 (moderate). A marketplace listing
scored the package down for the 1.30.0 five.

The floor and the lock's recorded specifier both say 1.30.0 now, and the locked
version is never below the floor.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIRST_UNADVISED = (1, 30, 0)


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(p) for p in text.split("."))


def _floor(spec: str) -> tuple[int, ...]:
    m = re.search(r">=\s*(\d+\.\d+\.\d+)", spec)
    assert m, f"no >= floor in {spec!r}"
    return _version(m.group(1))


def test_the_declared_floor_admits_no_advised_release():
    lines = [
        ln for ln in (ROOT / "pyproject.toml").read_text(encoding="utf-8").splitlines()
        if re.match(r'^\s*"mcp[<>=]', ln)
    ]
    assert len(lines) == 1, f"expected one mcp dependency line in pyproject.toml: {lines}"
    line = lines[0]
    assert _floor(line) >= FIRST_UNADVISED, line


def test_the_lock_records_the_same_floor_and_locks_at_or_above_it():
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    spec = re.search(r'\{ name = "mcp", specifier = "([^"]+)" \}', lock)
    assert spec, "uv.lock records no specifier for mcp"
    assert _floor(spec.group(1)) >= FIRST_UNADVISED, spec.group(1)
    locked = re.search(r'^name = "mcp"\nversion = "([^"]+)"', lock, re.M)
    assert locked and _version(locked.group(1)) >= _floor(spec.group(1))
