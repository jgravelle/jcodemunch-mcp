"""THE rule for matching a repo-relative path against a `file_pattern` glob.

A pattern that contains `/` names a place in the tree and is anchored at the
repository root: `homeassistant/**` matches `homeassistant/core.py` and not
`tests/homeassistant/core.py`. A bare pattern names files wherever they sit:
`*.py` and `route.ts` match at any depth.

Three readers used to carry their own copy of a rule that also tried `*/` +
pattern. fnmatch's `*` crosses `/`, so that second try matched an anchored
pattern at every depth, and `homeassistant/**` returned files under
`tests/homeassistant/`. Ask this module instead of re-deriving the rule.

A leaf: stdlib only, so `storage` and `retrieval` can import it.
"""

from __future__ import annotations

import fnmatch
import functools
import re
from typing import Iterable


def _normalise(pattern: str) -> str:
    """A pattern written as `./x` or `/x` means `x` at the root."""
    while pattern.startswith("./"):
        pattern = pattern[2:]
    return pattern.lstrip("/")


def is_anchored(pattern: str) -> bool:
    """True when the pattern names a place in the tree (contains `/`)."""
    return "/" in _normalise(pattern)


def _regex_source(pattern: str) -> str:
    pattern = _normalise(pattern)
    if is_anchored(pattern):
        return fnmatch.translate(pattern)
    return fnmatch.translate(pattern) + "|" + fnmatch.translate(f"*/{pattern}")


def compile_patterns(patterns: Iterable[str]) -> "re.Pattern[str]":
    """One compiled regex that matches a path when any pattern does."""
    return _compiled(tuple(patterns))


@functools.lru_cache(maxsize=256)
def _compiled(patterns: tuple[str, ...]) -> "re.Pattern[str]":
    sources = [_regex_source(p) for p in patterns]
    if not sources:
        return re.compile(r"(?!)")
    return re.compile("|".join(sources))


def matches(path: str, pattern: str) -> bool:
    """Does the repo-relative ``path`` match ``pattern`` under the rule above?

    Paths use `/`; a backslash separator is read as `/`. Matching is
    case-sensitive on every platform, as repository paths are.
    """
    return _compiled((pattern,)).match(path.replace("\\", "/")) is not None
