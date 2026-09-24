"""Get high-level repository outline."""

import json
import os
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Optional

from .. import config as _config
from ..storage import IndexStore, record_savings, estimate_savings, cost_avoided
from ..storage.index_store import _get_git_head
from ..parser.imports import resolve_specifier
from ..parser.symbols import REPRESENTATIVE_KIND_RANK
from ._utils import load_repo_index_or_error
from .pagerank import compute_pagerank


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

    index, error, _status = load_repo_index_or_error(repo, storage_path)
    if error:
        return error
    owner, name = index.owner, index.name
    store = IndexStore(base_path=storage_path)

    # Compute directory-level stats
    # For large repos, use 2-level grouping so agents get useful navigation hints.
    _LARGE_REPO_THRESHOLD = 500  # files
    _MAX_DIR_ENTRIES = 40

    dir_file_counts: Counter = Counter()
    for f in index.source_files:
        parts = f.split("/")
        if len(parts) > 1:
            dir_file_counts[parts[0] + "/"] += 1
        else:
            dir_file_counts["(root)"] += 1

    if len(index.source_files) > _LARGE_REPO_THRESHOLD:
        # Expand large top-level dirs into 2-level groupings
        expanded: Counter = Counter()
        for f in index.source_files:
            parts = f.split("/")
            if len(parts) >= 3:
                key = parts[0] + "/" + parts[1] + "/"
            elif len(parts) == 2:
                key = parts[0] + "/"
            else:
                key = "(root)"
            expanded[key] += 1
        # Only use 2-level if it gives more granularity than 1-level
        if len(expanded) > len(dir_file_counts):
            # Cap at _MAX_DIR_ENTRIES, keeping highest-count dirs
            dir_file_counts = Counter(dict(expanded.most_common(_MAX_DIR_ENTRIES)))

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
    # Most-imported files: count in-degree from import graph (PageRank-lite)
    most_imported: list = []
    if index.imports is not None:
        in_degree: Counter = Counter()
        source_files_set = frozenset(index.source_files)
        for src_file, file_imports in index.imports.items():
            for imp in file_imports:
                target = resolve_specifier(imp["specifier"], src_file, source_files_set, index.alias_map, getattr(index, "psr4_map", None))
                if target and target != src_file:
                    in_degree[target] += 1
        most_imported = [
            {"file": f, "imported_by": c}
            for f, c in in_degree.most_common(10)
            if c > 1
        ]

    # Most central symbols: top symbols by PageRank score on the import graph
    most_central: list = []
    if index.imports is not None:
        try:
            pr_scores, _ = compute_pagerank(index.imports, index.source_files, index.alias_map, psr4_map=getattr(index, "psr4_map", None))
            # Kind priority for picking the representative symbol per file:
            # the one table, derived from `KIND_ORDER` (#806).
            _KIND_PRIO = REPRESENTATIVE_KIND_RANK
            file_to_best: dict = {}
            for sym in index.symbols:
                f = sym.get("file", "")
                if not pr_scores.get(f):
                    continue
                kp = _KIND_PRIO.get(sym.get("kind", ""), 5)
                bl = sym.get("byte_length", 0)
                prev = file_to_best.get(f)
                if prev is None or (kp, -bl) < (prev[0], prev[1]):
                    file_to_best[f] = (kp, -bl, sym)
            top_files = sorted(pr_scores.items(), key=lambda x: x[1], reverse=True)[:10]
            for f, pr_score in top_files:
                entry = file_to_best.get(f)
                if entry and pr_score > 0:
                    most_central.append({
                        "symbol_id": entry[2]["id"],
                        "score": round(pr_score, 6),
                        "kind": entry[2].get("kind", ""),
                    })
        except Exception:
            pass

    payload_content = {
        "repo": f"{owner}/{name}",
        "indexed_at": index.indexed_at,
        "file_count": len(index.source_files),
        "symbol_count": len(index.symbols),
        "languages": index.languages,
        "directories": dict(dir_file_counts.most_common()),
        "symbol_kinds": dict(kind_counts.most_common()),
    }
    if most_imported:
        payload_content["most_imported_files"] = most_imported
    if most_central:
        payload_content["most_central_symbols"] = most_central
    response_bytes = len(json.dumps(payload_content).encode("utf-8"))
    tokens_saved = estimate_savings(raw_bytes, response_bytes)
    total_saved = record_savings(tokens_saved, tool_name="get_repo_outline")

    elapsed = (time.perf_counter() - start) * 1000

    # Staleness check — SHA-based (accurate) with time-based fallback
    staleness_warning = None
    is_stale = None
    try:
        from pathlib import Path

        if index.source_root and index.git_head:
            # Local repo: compare SHAs
            current_sha = _get_git_head(Path(index.source_root))
            if current_sha is not None:
                is_stale = current_sha != index.git_head
                if is_stale:
                    staleness_warning = (
                        f"Index SHA ({index.git_head[:12]}) does not match current HEAD "
                        f"({current_sha[:12]}). Run index_folder to refresh."
                    )
        else:
            # GitHub repo or no git: fall back to time-based check.
            # Project-overridable (#301): per-repo freshness expectations.
            staleness_days = _config.get("staleness_days", 7, repo=repo)
            indexed_dt = datetime.fromisoformat(index.indexed_at)
            if indexed_dt.tzinfo is None:
                indexed_dt = indexed_dt.replace(tzinfo=timezone.utc)
            age_days = (datetime.now(timezone.utc) - indexed_dt).days
            if age_days >= staleness_days:
                is_stale = True
                staleness_warning = (
                    f"Index is {age_days} days old. Run index_repo to refresh."
                )
    except Exception:
        pass

    result = {
        **payload_content,
        "_meta": {
            "timing_ms": round(elapsed, 1),
            "tokens_saved": tokens_saved,
            "total_tokens_saved": total_saved,
            "is_stale": is_stale,
            **cost_avoided(tokens_saved, total_saved),
        },
    }
    if staleness_warning:
        result["staleness_warning"] = staleness_warning
    return result
