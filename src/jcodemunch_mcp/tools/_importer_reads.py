"""Did a reference search READ every file it is about to be trusted on?

The one rule both preflights use before turning "no file names this symbol"
into a verdict: `check_delete_safe`'s `file_import_only` (LEDGER L-348) and
`check_edit_safe`'s (LEDGER L-349). ⚠⚠ L-349 is the second copy of L-348's
defect, one tool over, because the first fix lived inside one tool. This
module is the one home; `tests/test_check_edit_safe_file_import_only.py`
fails if either tool binds another.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def content_search_read_every(
    index, store, owner: str, name: str, files: list[str], target_name: str
) -> bool:
    """True only when the reference search READ every file in `files`.

    ⚠⚠ (LEDGER L-348 review) `check_references._check_single` skips a file in
    silence when it is not in `index.source_files`, when its cached content is
    missing or cannot be read, and when it holds a same-named definition with
    no line span. Finding no reference in a file it skipped proves nothing, so
    each of those cases answers False here. Mirrors that function's skip
    conditions; it does not search for the name itself.
    """
    if not files:
        return False
    try:
        source = set(index.source_files)
        unspanned = {
            s.get("file", "")
            for s in index.symbols
            if s.get("name") == target_name and not s.get("line")
        }
        content_dir = store._content_dir(owner, name)
        for f in files:
            if f not in source or f in unspanned:
                return False
            path = store._safe_content_path(content_dir, f)
            if not path or not path.exists():
                return False
            with open(path, "r", encoding="utf-8", errors="replace", newline="") as fh:
                fh.read(1)
    except Exception:  # noqa: BLE001
        logger.debug("importer content check failed", exc_info=True)
        return False
    return True
