"""THE folder-prefix rule for a containment check: `folder` plus one separator.

A filesystem root already ends in a separator (`C:\\`, `/`), so the spelling
`folder + os.sep` doubles it there and nothing under the root ever matches.
That spelling sat in the hooks (L-195), the indexer's path filter (L-197) and
the indexer's `.gitignore` prefixes, each found separately. Every caller asks
here instead; `tests/test_path_prefix_is_the_only_rule.py` fails on the old
spelling anywhere else in `src/`.

A leaf, stdlib only: the hooks import it on every call.
"""

from __future__ import annotations

import os


def dir_prefix(folder: str) -> str:
    """`folder` with exactly one trailing separator."""
    return folder if folder.endswith(os.sep) else folder + os.sep
