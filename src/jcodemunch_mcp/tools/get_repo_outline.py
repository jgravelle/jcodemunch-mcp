"""Get high-level repository outline."""

import os
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Optional

from ..storage import IndexStore, record_savings, estimate_savings, cost_avoided
from ._utils import resolve_repo


def get_repo_outline(
    repo: str,
    storage_path: Optional[str] = None,
) -> dict:
    """Get a high-level overview of an indexed repository.

    Returns: top-level directories, file counts, language breakdown,
    total symbol count. Lighter than get_file_tree.

    Args:
        repo: Repository identifier (owner/repo or just repo name).
        storage_path: Custom storage path.

    Returns:
        Dict with repo outline and _meta envelope.
    """
    start = time.perf_counter()

    try:
        owner, name = resolve_repo(repo, storage_path)
    except ValueError as e:
        return {"error": str(e)}

    store = IndexStore(base_path=storage_path)
    index = store.load_index(owner, name)

    if not index:
        return {"error": f"Repository not indexed: {owner}/{name}"}

    # Compute directory-level stats
    dir_file_counts: Counter = Counter()
    for f in index.source_files:
        parts = f.split("/")
        if len(parts) > 1:
            dir_file_counts[parts[0] + "/"] += 1
        else:
            dir_file_counts["(root)"] += 1

    # Symbol kind breakdown
    kind_counts: Counter = Counter()
    for sym in index.symbols:
        kind_counts[sym.get("kind", "unknown")] += 1

    # Token savings: sum of all raw file sizes (user would need to read all files)
    raw_bytes = 0
    content_dir = store._content_dir(owner, name)
    for f in index.source_files:
        try:
            raw_bytes += os.path.getsize(content_dir / f)
        except OSError:
            pass
    tokens_saved = estimate_savings(raw_bytes, 0)
    total_saved = record_savings(tokens_saved)

    elapsed = (time.perf_counter() - start) * 1000

    # Staleness warning
    staleness_warning = None
    try:
        staleness_days = int(os.environ.get("JCODEMUNCH_STALENESS_DAYS", "7"))
        indexed_dt = datetime.fromisoformat(index.indexed_at)
        if indexed_dt.tzinfo is None:
            indexed_dt = indexed_dt.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - indexed_dt).days
        if age_days >= staleness_days:
            staleness_warning = (
                f"Index is {age_days} days old. Run index_folder or index_repo to refresh."
            )
    except Exception:
        pass

    result = {
        "repo": f"{owner}/{name}",
        "indexed_at": index.indexed_at,
        "file_count": len(index.source_files),
        "symbol_count": len(index.symbols),
        "languages": index.languages,
        "directories": dict(dir_file_counts.most_common()),
        "symbol_kinds": dict(kind_counts.most_common()),
        "_meta": {
            "timing_ms": round(elapsed, 1),
            "tokens_saved": tokens_saved,
            "total_tokens_saved": total_saved,
            **cost_avoided(tokens_saved, total_saved),
        },
    }
    if staleness_warning:
        result["staleness_warning"] = staleness_warning
    return result
