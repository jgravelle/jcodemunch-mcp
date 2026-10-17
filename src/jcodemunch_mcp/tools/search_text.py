"""Full-text search across indexed file contents."""

import fnmatch as _fnmatch
import re
import time
from typing import Optional

from ..storage import IndexStore, record_savings, cost_avoided
from ._utils import index_status_to_tool_error, resolve_repo

# Detect nested quantifiers that cause catastrophic backtracking in Python's re engine.
# Matches patterns like (a+)+, (a*)+, (a+)*, (?:a+){2,}, etc.
_NESTED_QUANTIFIER_RE = re.compile(
    r'[+*]\s*\)'    # quantifier before closing group
    r'\s*[+*{]'     # quantifier after closing group
)

_MAX_REGEX_LEN = 200
_MAX_QUERY_LEN = 500


def validate_query_args(query, is_regex: bool) -> Optional[dict]:
    """Cheap argument validation, shared with server pre-dispatch (jcm#329).

    Returns an error dict, or None when the arguments pass. call_tool runs
    this BEFORE strict-freshness waits and auto-watch reindexing, so a call
    doomed to be rejected fails in milliseconds instead of first paying an
    unbounded pre-dispatch reindex (29s observed in the field).
    """
    if not isinstance(query, str):
        return {"error": "query must be a string"}
    if len(query) > _MAX_QUERY_LEN:
        return {"error": f"Query too long ({len(query)} chars, max {_MAX_QUERY_LEN})"}
    if is_regex:
        if len(query) > _MAX_REGEX_LEN:
            return {"error": f"Regex too long ({len(query)} chars, max {_MAX_REGEX_LEN})"}
        if _NESTED_QUANTIFIER_RE.search(query):
            return {"error": "Regex rejected: nested quantifiers can cause catastrophic backtracking"}
        try:
            re.compile(query, re.IGNORECASE)
        except re.error as e:
            return {"error": f"Invalid regex: {e}"}
    return None


# Wall-clock budget for regex evaluation across all files in a single
# search_text call. Python's `re` has no per-call timeout and the existing
# nested-quantifier guard only catches `(a+)+`-shape patterns; alternation
# overlap and look-ahead-driven blowup slip through. Cap total regex time
# so a crafted pattern can't pin a worker thread indefinitely.
_REGEX_BUDGET_SEC = 2.0


def search_text(
    repo: str,
    query: str,
    file_pattern: Optional[str] = None,
    max_results: int = 20,
    context_lines: int = 0,
    is_regex: bool = False,
    storage_path: Optional[str] = None,
) -> dict:
    """Search for text across all indexed files in a repository.

    Useful when symbol search misses — e.g., searching for string literals,
    comments, configuration values, or patterns not captured as symbols.

    Args:
        repo: Repository identifier (owner/repo or just repo name).
        query: Text to search for. Case-insensitive substring by default;
               set is_regex=True for full regex (e.g. 'estimateToken|tokenEstimat').
               Limits: 500 chars plain, 200 chars regex; nested quantifiers
               (e.g. '(a+)+') are rejected to prevent catastrophic backtracking.
        file_pattern: Optional glob pattern to filter files.
        max_results: Maximum number of matching lines to return.
        context_lines: Lines of context before/after each match (like grep -C).
        is_regex: When True, treat query as a Python regex (re.search, IGNORECASE).
        storage_path: Custom storage path.

    Returns:
        Dict with matching lines grouped by file, plus _meta envelope.
    """
    arg_err = validate_query_args(query, is_regex)
    if arg_err is not None:
        return arg_err

    start = time.perf_counter()
    max_results = max(1, min(max_results, 100))
    context_lines = max(0, min(context_lines, 10))

    # For regex mode, compile the user pattern. For substring mode, use
    # Python's optimized `in` operator (faster than re.search per line).
    # Compile can't fail here — validate_query_args already vetted it
    # (re's internal cache makes the second compile free).
    pattern = re.compile(query, re.IGNORECASE) if is_regex else None
    query_lower = None if is_regex else query.lower()

    try:
        owner, name = resolve_repo(repo, storage_path)
    except ValueError as e:
        return {"error": str(e)}

    store = IndexStore(base_path=storage_path)
    index = store.load_index(owner, name)

    if not index:
        return index_status_to_tool_error(store.inspect_index(owner, name))

    # Pre-compile file pattern into a single regex (avoids double fnmatch per file)
    files = index.source_files
    if file_pattern:
        pat_re = re.compile(
            _fnmatch.translate(file_pattern) + "|" + _fnmatch.translate(f"*/{file_pattern}")
        )
        files = [f for f in files if pat_re.match(f)]

    # #377 item 6: identity before the scan, compared after it. search_text
    # walks files one by one, so it is the tool most likely to still be reading
    # when a watcher reindex or a checkout lands underneath it.
    from ..retrieval import subject_state as _subject
    _state_before = _subject.capture(index)

    content_dir = store._content_dir(owner, name)
    results = []
    result_count = 0
    files_searched = 0
    files_eligible = len(files)
    files_unreadable = 0
    truncated = False
    timed_out = False

    # Only enforce the wall-clock budget for regex mode — substring (`in`)
    # is linear and not a ReDoS vector.
    _budget_deadline = (start + _REGEX_BUDGET_SEC) if pattern is not None else None

    for file_path in files:
        if _budget_deadline is not None and time.perf_counter() > _budget_deadline:
            timed_out = True
            break
        # #377 hardening item 7: an eligible file the scan could not read is a
        # gap in the scan, not a file that contained no match. Counted so a
        # zero-result response cannot present a partial sweep as exhaustive.
        full_path = store._safe_content_path(content_dir, file_path)
        if not full_path:
            files_unreadable += 1
            continue
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError:
            files_unreadable += 1
            continue

        files_searched += 1
        file_matches = []
        for line_index, line in enumerate(lines):
            # Check the budget every 256 lines — cheap, and bounds worst-case
            # blowup on any single regex/line pair.
            if (
                _budget_deadline is not None
                and (line_index & 0xFF) == 0
                and time.perf_counter() > _budget_deadline
            ):
                timed_out = True
                break
            line = line.rstrip("\n")
            hit = pattern.search(line) if pattern else (query_lower in line.lower())
            if not hit:
                continue
            text = line.rstrip()[:200]
            match = {
                "line": line_index + 1,
                "text": text,
            }
            if context_lines > 0:
                before_start = max(0, line_index - context_lines)
                after_end = min(len(lines), line_index + context_lines + 1)
                before = [v.rstrip()[:200] for v in lines[before_start:line_index]]
                after = [v.rstrip()[:200] for v in lines[line_index + 1:after_end]]
                match["before"] = before
                match["after"] = after
            file_matches.append(match)
            result_count += 1
            if result_count >= max_results:
                truncated = True
                break

        if file_matches:
            results.append({"file": file_path, "matches": file_matches})

        if truncated or timed_out:
            break

    elapsed = (time.perf_counter() - start) * 1000

    # L-180: no credit. The answer is the matching lines, which Grep returns
    # as well; the old figure credited every matching file's whole size.
    # Zero until a measurement supports a number (savings_credit.py).
    tokens_saved = 0
    total_saved = record_savings(tokens_saved, tool_name="search_text")

    from ..retrieval.verdict import build_verdict as _build_verdict
    from ..retrieval.verdict import index_changed_since_load as _index_changed_since_load
    from ..retrieval.verdict import index_coverage_meta as _index_coverage_meta
    from ..retrieval.verdict import index_truncation_meta as _index_truncation_meta
    # #377 hardening items 7 and 9: nothing here may present a scan that never
    # ran, or ran over part of its inputs, as proof of absence.
    _incomplete = None
    if files_eligible == 0:
        _incomplete = {
            "reason": "empty_scope",
            "files_eligible": 0,
            "note": (
                "No indexed file matched this scope, so nothing was searched. "
                "An empty eligible set proves nothing about the corpus."
            ),
        }
    elif files_unreadable:
        _incomplete = {
            "reason": "unreadable_inputs",
            "files_eligible": files_eligible,
            "files_searched": files_searched,
            "files_unreadable": files_unreadable,
            "note": (
                f"{files_unreadable} of {files_eligible} eligible file(s) could not "
                "be read, so this scan was partial and absence is NOT proven."
            ),
        }
    # #377 hardening: search_text never passed freshness into the verdict, so a
    # stale-index scan could reach `absent` and mint a citable ref without the
    # stale gate its sibling tools enforce.
    from ..retrieval.freshness import FreshnessProbe as _FreshnessProbe
    _probe = _FreshnessProbe(
        source_root=getattr(index, "source_root", "") or None,
        indexed_at=getattr(index, "indexed_at", ""),
        index_sha=getattr(index, "git_head", None),
        file_mtimes=getattr(index, "file_mtimes", None),
    )
    _vres = _build_verdict(
        result_count=result_count,
        scanned_files=files_searched,
        query_terms=(query.lower().split() if not is_regex else None),
        source_files=index.source_files,
        timed_out=timed_out,
        coverage=_index_coverage_meta(index),
        index_changed=_index_changed_since_load(index),
        index_stale=_probe.repo_is_stale,
        freshness=_probe.repo_freshness,
        incomplete=_incomplete,
        moved_during_scan=_subject.moved_during_scan(
            _state_before, index, result_count=result_count
        ),
        working_tree=(
            _subject.working_tree_state(
                index, scope=file_pattern, freshness=_probe.repo_freshness
            )
            if result_count == 0 else None
        ),
    )
    _meta = {
        "timing_ms": round(elapsed, 1),
        "files_searched": files_searched,
        "truncated": truncated,
        "timed_out": timed_out,
        "tokens_saved": tokens_saved,
        "total_tokens_saved": total_saved,
        "verdict": _vres["verdict"],
        **cost_avoided(tokens_saved, total_saved),
    }
    _cap = getattr(index, "file_cap_status", None)
    if _cap and _cap.get("truncated"):
        _meta["index_truncated"] = _index_truncation_meta(_cap)
    return {
        "result_count": result_count,
        "results": results,
        "_meta": _meta,
    }
