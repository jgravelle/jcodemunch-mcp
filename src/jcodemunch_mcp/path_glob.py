"""THE rule for matching a repo-relative path against a `file_pattern` glob.

A pattern that contains `/` names a place in the tree and is anchored at the
repository root: `homeassistant/**` matches `homeassistant/core.py` and not
`tests/homeassistant/core.py`. A bare pattern matches at any depth: `*.py`
and `route.ts` find the file wherever it sits. As in fnmatch, `*` crosses
`/`, so a bare `*detached*` also matches a directory name
(`pkg/detached/x.py`); callers have relied on that, and it is kept. `**/`
as a whole segment matches zero or more directories, so `src/**/*.py` matches
`src/a.py`; for up to three of them per pattern (`_MAX_GLOBSTARS`), past which
each needs at least one directory.

A backslash in the path or the pattern is read as `/`. Matching is
case-sensitive on every platform, as repository paths are.

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
    """A backslash reads as `/`; `./x` or `/x` means `x` at the root."""
    pattern = pattern.replace("\\", "/")
    while pattern.startswith("./"):
        pattern = pattern[2:]
    return pattern.lstrip("/")


def is_anchored(pattern: str) -> bool:
    """True when the pattern names a place in the tree (contains `/`)."""
    return "/" in _normalise(pattern)


#: `**/` segments that may match zero directories: 2**3 spellings at most.
#: Past the cap, each `**/` needs at least one directory, as plain fnmatch does.
_MAX_GLOBSTARS = 3

#: `**/` as a whole path segment: at the start, or right after a `/`.
#: `x**/y` is not a globstar and must never become `xy`.
_GLOBSTAR = re.compile(r"(?:^|(?<=/))\*\*/")


def _globstar_variants(pattern: str) -> list[str]:
    """The pattern with each subset of its `**/` segments removed, so `**/`
    also matches zero directories: `src/**/*.py` matches `src/a.py`."""
    parts = _GLOBSTAR.split(pattern)
    if len(parts) == 1 or len(parts) - 1 > _MAX_GLOBSTARS:
        return [pattern]
    out = [parts[0]]
    for part in parts[1:]:
        out = [v + glue + part for v in out for glue in ("**/", "")]
    return list(dict.fromkeys(out))


def _regex_source(pattern: str) -> str:
    pattern = _normalise(pattern)
    if is_anchored(pattern):
        return "|".join(
            f"(?:{fnmatch.translate(v)})" for v in _globstar_variants(pattern)
        )
    return f"(?:{fnmatch.translate(pattern)})|(?:{fnmatch.translate('*/' + pattern)})"


def compile_patterns(patterns: Iterable[str]) -> "re.Pattern[str]":
    """One compiled regex that matches a path when any pattern does."""
    return _compiled(tuple(patterns))


@functools.lru_cache(maxsize=256)
def _compiled(patterns: tuple[str, ...]) -> "re.Pattern[str]":
    sources = [f"(?:{_regex_source(p)})" for p in patterns]
    if not sources:
        return re.compile(r"(?!)")
    return re.compile("|".join(sources), re.DOTALL)


def matches(path: str, pattern: str) -> bool:
    """Does the repo-relative ``path`` match ``pattern`` under the rule above?

    A backslash in either is read as `/`.
    """
    return _compiled((pattern,)).match(path.replace("\\", "/")) is not None
