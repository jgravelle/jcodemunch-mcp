"""winnow_symbols — compose multiple signals into one ranked symbol query.

Agents often need to intersect several axes in a single step:
"functions with cyclomatic > 10, churned recently, calling db.Exec, no tests."
Today that takes 4–5 round trips and client-side merging. winnow_symbols
accepts an ordered list of criteria, evaluates them against the index in
one pass, and returns a ranked survivor set.

The name fits the munching idiom: winnowing separates grain from chaff —
each criterion tosses more chaff; the remainder is what the agent asked for.

Criteria are ``{axis, op, value}`` triples combined with AND semantics.
Supported axes in v1:

    kind         in | eq                        [str | list]
    language     in | eq                        [str | list]
    name         eq | matches                   [str]           (matches = regex)
    file         matches                        [str]           (glob)
    complexity   > | < | >= | <= | ==           [int]           (cyclomatic)
    decorator    contains                       [str | list]    (case-insensitive)
    calls        contains                       [str | list]    (any call_reference)
    summary      contains                       [str]           (case-insensitive)
    churn        > | < | >= | <= | ==           [int]           + optional window_days
"""

from __future__ import annotations

import fnmatch
import logging
import re
import subprocess
import time
from collections import defaultdict
from typing import Any, Optional

from ..storage import IndexStore, record_savings, estimate_savings, cost_avoided
from ._utils import symbol_span_bytes
from ._utils import resolve_repo
from .pagerank import compute_pagerank

logger = logging.getLogger(__name__)


_SUPPORTED_AXES = frozenset({
    "kind", "language", "name", "file", "complexity",
    "decorator", "calls", "summary", "churn",
})
_NUMERIC_OPS = {">", "<", ">=", "<=", "=="}
_SET_OPS = {"in", "eq"}
_REGEX_OPS = {"matches"}
_CONTAINS_OPS = {"contains"}

_RANK_AXES = frozenset({"importance", "complexity", "churn", "name"})


def _run_git(args: list[str], cwd: str, timeout: int = 30) -> tuple[int, str]:
    try:
        r = subprocess.run(
            ["git"] + args,
            cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, stdin=subprocess.DEVNULL,
        )
        return r.returncode, r.stdout.strip()
    except FileNotFoundError:
        return -1, ""
    except subprocess.TimeoutExpired:
        return -2, ""
    except Exception as exc:
        logger.debug("git subprocess error: %s", exc, exc_info=True)
        return -3, ""


def _get_file_churn(cwd: str, days: int) -> dict[str, int]:
    # (#685) `--relative`: `--name-only` prints paths from the git TOP LEVEL, the
    # index holds them from `source_root`, and an index rooted below the top
    # level (`identity_mode="local"`) would miss every name: churn 0 reads as a
    # cold file, not as an error.
    rc, out = _run_git(
        ["log", f"--since={days} days ago", "--relative", "--name-only", "--format="],
        cwd=cwd, timeout=60,
    )
    if rc != 0:
        # A failing git is not a repository with no churn; say so.
        logger.warning("churn unavailable: git log exited %s in %s", rc, cwd)
        return {}
    if not out:
        return {}
    counts: dict[str, int] = defaultdict(int)
    for line in out.splitlines():
        line = line.strip()
        if line:
            counts[line] += 1
    return dict(counts)


def _normalize_list(val: Any) -> list[str]:
    if isinstance(val, list):
        return [str(v) for v in val]
    return [str(val)]


def _apply_numeric(op: str, sym_val: Any, threshold: Any) -> bool:
    try:
        sv = float(sym_val or 0)
        tv = float(threshold)
    except (TypeError, ValueError):
        return False
    if op == ">":   return sv > tv
    if op == "<":   return sv < tv
    if op == ">=":  return sv >= tv
    if op == "<=":  return sv <= tv
    if op == "==":  return sv == tv
    return False


def _match_criterion(
    sym: dict,
    criterion: dict,
    file_churn: dict[str, int],
) -> bool:
    axis = criterion.get("axis")
    op = criterion.get("op")
    value = criterion.get("value")

    if axis == "kind":
        if op not in _SET_OPS:
            return False
        allowed = _normalize_list(value)
        return sym.get("kind", "") in allowed

    if axis == "language":
        if op not in _SET_OPS:
            return False
        allowed = _normalize_list(value)
        return sym.get("language", "") in allowed

    if axis == "name":
        name = sym.get("name", "") or ""
        if op == "eq":
            return name == str(value)
        if op == "matches":
            try:
                return re.search(str(value), name) is not None
            except re.error:
                return False
        return False

    if axis == "file":
        if op != "matches":
            return False
        file_path = (sym.get("file", "") or "").replace("\\", "/")
        pattern = str(value).replace("\\", "/")
        return fnmatch.fnmatch(file_path, pattern)

    if axis == "complexity":
        return _apply_numeric(op, sym.get("cyclomatic"), value)

    if axis == "decorator":
        if op != "contains":
            return False
        needles = [n.lower() for n in _normalize_list(value)]
        haystack = [d.lower() for d in (sym.get("decorators") or [])]
        return any(any(n in d for d in haystack) for n in needles)

    if axis == "calls":
        if op != "contains":
            return False
        needles = [n.lower() for n in _normalize_list(value)]
        haystack = [c.lower() for c in (sym.get("call_references") or [])]
        return any(any(n in c for c in haystack) for n in needles)

    if axis == "summary":
        if op != "contains":
            return False
        needle = str(value).lower()
        hay = f"{sym.get('summary','')} {sym.get('docstring','')}".lower()
        return needle in hay

    if axis == "churn":
        file_norm = (sym.get("file", "") or "").replace("\\", "/")
        count = file_churn.get(file_norm, 0)
        return _apply_numeric(op, count, value)

    return False


def _validate_criteria(criteria: list) -> Optional[str]:
    if not isinstance(criteria, list):
        return "criteria must be a list"
    for i, c in enumerate(criteria):
        if not isinstance(c, dict):
            return f"criteria[{i}] must be an object"
        axis = c.get("axis")
        op = c.get("op")
        if axis not in _SUPPORTED_AXES:
            return (
                f"criteria[{i}].axis '{axis}' not supported. "
                f"Supported: {sorted(_SUPPORTED_AXES)}"
            )
        if op not in (_NUMERIC_OPS | _SET_OPS | _REGEX_OPS | _CONTAINS_OPS):
            return f"criteria[{i}].op '{op}' not recognized"
        if "value" not in c:
            return f"criteria[{i}] missing 'value'"
    return None


def winnow_symbols(
    repo: str,
    criteria: list,
    rank_by: str = "importance",
    order: str = "desc",
    max_results: int = 20,
    storage_path: Optional[str] = None,
) -> dict:
    """Run a constraint-chain query against an indexed repo.

    Args:
        repo:          Repo id (owner/repo or bare name).
        criteria:      Ordered list of ``{axis, op, value}`` filters (AND).
        rank_by:       ``importance`` (default) | ``complexity`` | ``churn`` | ``name``.
        order:         ``desc`` (default) | ``asc``.
        max_results:   Hard cap on returned symbols.
        storage_path:  Optional storage override.

    Returns:
        Dict with ``repo``, ``criteria``, ``rank_by``, ``matched``, ``total_scanned``,
        ``results``, ``_meta``. Each result: ``{symbol_id, name, kind, language,
        file, line, signature, summary, cyclomatic, churn, importance}``.
    """
    t0 = time.perf_counter()

    if rank_by not in _RANK_AXES:
        return {"error": f"rank_by '{rank_by}' invalid. Must be one of {sorted(_RANK_AXES)}"}
    if order not in ("asc", "desc"):
        return {"error": f"order '{order}' invalid. Must be 'asc' or 'desc'"}

    err = _validate_criteria(criteria or [])
    if err:
        return {"error": err}

    try:
        owner, name = resolve_repo(repo, storage_path)
    except ValueError as e:
        return {"error": str(e)}

    store = IndexStore(base_path=storage_path)
    index = store.load_index(owner, name)
    if index is None:
        return {"error": f"No index found for {repo!r}. Run index_folder first."}

    # Churn window: largest window requested across churn criteria, default 90.
    churn_window = 90
    for c in criteria:
        if c.get("axis") == "churn":
            w = c.get("window_days")
            if isinstance(w, int) and w > 0:
                churn_window = max(churn_window, w)

    file_churn: dict[str, int] = {}
    git_available = False
    needs_churn = rank_by == "churn" or any(c.get("axis") == "churn" for c in criteria)
    if needs_churn and index.source_root:
        rc, _ = _run_git(["rev-parse", "--git-dir"], cwd=index.source_root)
        if rc == 0:
            git_available = True
            raw = _get_file_churn(index.source_root, churn_window)
            file_churn = {k.replace("\\", "/"): v for k, v in raw.items()}

    # File-level PageRank → per-symbol importance (file score inherited).
    importance_by_file: dict[str, float] = {}
    if rank_by == "importance" and index.imports:
        try:
            source_files = [s.get("file", "") for s in index.symbols if s.get("file")]
            source_files = sorted(set(source_files))
            scores, _iters = compute_pagerank(
                index.imports, source_files,
                alias_map=getattr(index, "alias_map", None),
                psr4_map=getattr(index, "psr4_map", None),
            )
            importance_by_file = scores
        except Exception as exc:
            logger.debug("pagerank failed for %s/%s: %s", owner, name, exc, exc_info=True)

    survivors: list[dict] = []
    total = 0
    for sym in index.symbols:
        total += 1
        ok = True
        for c in criteria:
            if not _match_criterion(sym, c, file_churn):
                ok = False
                break
        if not ok:
            continue

        file_norm = (sym.get("file", "") or "").replace("\\", "/")
        churn = file_churn.get(file_norm, 0)
        importance = round(importance_by_file.get(sym.get("file", ""), 0.0), 6)

        survivors.append({
            "symbol_id": sym.get("id", ""),
            "name": sym.get("name", ""),
            "kind": sym.get("kind", ""),
            "language": sym.get("language", ""),
            "file": sym.get("file", ""),
            "line": sym.get("line") or 0,
            "signature": sym.get("signature", ""),
            "summary": sym.get("summary", ""),
            "cyclomatic": sym.get("cyclomatic") or 0,
            "churn": churn,
            "importance": importance,
        })

    reverse = (order == "desc")
    if rank_by == "importance":
        survivors.sort(key=lambda x: x["importance"], reverse=reverse)
    elif rank_by == "complexity":
        survivors.sort(key=lambda x: x["cyclomatic"], reverse=reverse)
    elif rank_by == "churn":
        survivors.sort(key=lambda x: x["churn"], reverse=reverse)
    elif rank_by == "name":
        survivors.sort(key=lambda x: x["name"].lower(), reverse=reverse)

    capped = max(1, int(max_results))
    results = survivors[:capped]

    # Telemetry: estimate tokens saved vs agent doing the work by hand.
    try:
        raw_bytes = symbol_span_bytes(index.symbols)
        response_bytes = sum(
            len(r.get("signature", "")) + len(r.get("summary", "")) + 200
            for r in results
        )
        tokens_saved = estimate_savings(raw_bytes, response_bytes)
        total_saved = record_savings(tokens_saved, base_path=storage_path, tool_name="winnow_symbols")
        cost = cost_avoided(tokens_saved, total_saved)
    except Exception as exc:
        logger.debug("telemetry failed: %s", exc, exc_info=True)
        tokens_saved, total_saved, cost = 0, 0, {}

    return {
        "repo": f"{owner}/{name}",
        "criteria": criteria,
        "rank_by": rank_by,
        "order": order,
        "matched": len(survivors),
        "total_scanned": total,
        "truncated": len(survivors) > capped,
        "git_available": git_available,
        "results": results,
        "_meta": {
            "timing_ms": round((time.perf_counter() - t0) * 1000, 1),
            "tokens_saved": tokens_saved,
            "total_tokens_saved": total_saved,
            "cost_avoided": cost,
            "supported_axes": sorted(_SUPPORTED_AXES),
        },
    }
