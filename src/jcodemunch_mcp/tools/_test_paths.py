"""THE one answer to "is this path a test file?" (LEDGER L-101).

Six modules each kept their own rule and no two agreed. `find_dead_code` and
`get_dead_code_v2` tested `"/tests/" in path`, which needs a LEADING slash,
so a test directory at the repository root was not one: `tests/helpers.py`
was reported dead at confidence 1.0 where `pkg/tests/helpers.py` was skipped.
`check_delete_safe` and `find_similar_symbols` saw the root directory and
missed `a_test.py`, `a.spec.ts` and `__tests__/`. `get_pr_risk_profile`
matched `"/test" in path`, which calls `src/testimonials.tsx` a test.

Every caller imports `is_test_file` from here. A seventh rule written beside
a tool fails `tests/test_one_test_file_predicate.py`.

⚠ The rule reads the PATH. A JUnit `FooTest.java` outside `src/test/` and
Rust's inline `#[cfg(test)]` module are not recognised.
⚠ `spec/` is not a test directory here: it also names a folder of
specifications. A `_spec.rb` or `.spec.ts` FILE is a test by its name.
"""

from __future__ import annotations

import re

_TEST_DIR_RE = re.compile(r"^(tests?|__tests__|test_.+)$", re.IGNORECASE)
_TEST_NAME_RE = re.compile(
    r"^(test_.*|.+_(test|spec)\.[^.]+|.+\.(test|spec)\.[^.]+|conftest\.py|tests\.py)$",
    re.IGNORECASE,
)


def is_test_file(file_path: str) -> bool:
    """True when a directory on the path, or the filename, names a test."""
    parts = [p for p in (file_path or "").replace("\\", "/").split("/") if p]
    if not parts:
        return False
    if any(_TEST_DIR_RE.match(p) for p in parts[:-1]):
        return True
    return bool(_TEST_NAME_RE.match(parts[-1]))
