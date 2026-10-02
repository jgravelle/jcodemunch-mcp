"""THE one answer to "is this path a test file?" (LEDGER L-101).

Six rules answered this under `src/` and no two agreed. `find_dead_code` and
`get_dead_code_v2` tested `"/tests/" in path`, which needs a LEADING slash,
so a test directory at the repository root was not one: `tests/helpers.py`
was reported dead at confidence 1.0 where `pkg/tests/helpers.py` was skipped.
`check_delete_safe` and `find_similar_symbols` saw the root directory and
missed `a_test.py`, `a.spec.ts` and `__tests__/`. `get_pr_risk_profile`
matched `"/test" in path`, which calls `src/testimonials.tsx` a test.

Every caller imports `is_test_file` from here. Add a spelling HERE, never
beside a tool.

⚠⚠ `check_delete_safe` and `check_edit_safe` read this rule to decide that a
use is a TEST use, which downgrades a blocking verdict. A false positive here
is therefore worse than a false negative, and each suffix is tied to the
extensions that carry its convention: `_spec` is RSpec (`.rb`) and
`.spec.`/`.test.` are JavaScript and TypeScript. `models/pod_spec.py` and
`api_spec.yaml` are not tests.
⚠⚠ Three spellings are kept although a production file can carry them:
`*_test.<ext>`, `tests.py` and a `*_tests/` directory. A use in
`experiments/ab_test.py` reads `test_coverage_only` to the delete preflight
(never `safe_to_delete`) and `safe_to_edit` to the edit preflight.
⚠ `spec/` is not a test directory here: it also names a folder of
specifications.
⚠ The rule reads the PATH, relative to the repository. A JUnit `FooTest.java`
outside `src/test/`, a bare `test.py`, and Rust's inline `#[cfg(test)]`
module are not recognised (LEDGER L-104).
"""

from __future__ import annotations

import re

_TEST_DIR_RE = re.compile(r"^(tests?|__tests?__|test_.+|.+_tests)$", re.IGNORECASE)
_TEST_NAME_RE = re.compile(
    r"^(test_.*|.+_test\.[^.]+|.+_spec\.rb|.+\.(test|spec)\.[cm]?[jt]sx?|conftest\.py|tests\.py)$",
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
