"""Answer a redirected Grep from the index, inside the redirect itself.

A PreToolUse redirect that only says "use search_text" costs the agent two
turns: the refused Grep, then the search it was told to make. This module runs
that search in the hook and returns its page, so the refusal carries the answer
and the agent's next turn reads it instead of asking for it.

It answers only a large Grep. The whole match set is collected first; when it
is under both size thresholds (matching lines, matching files) the Grep runs
untouched, because a small Grep is the agent's cheap path and an answer there
saves nothing. At or over either threshold the answer counts the whole match
set without the lines: by file and by the function or class each match sits
in, with each symbol's id and span. When that does not fit the size cap it
counts by file only, and then lists the busiest files and sums the rest. Every
form counts every match; a page that drops matches is never sent. (Measured
2026-11-05: the matching lines themselves never cost less than the Grep.)

It answers at most once per (session, search root, Grep arguments): the same
Grep sent again runs normally, which is the escape hatch to raw output. It
answers nothing, and the Grep runs untouched, when the switch is off, the
search root is not an indexed repo, the arguments have no index translation
(file type, multiline, context lines, paging, a count, a `**` glob), an indexed
file changed on disk since it was indexed, or the search errors, finds nothing,
or is cut short (time budget, the scan cap, unreadable files).

The answer says what it is: a case-insensitive search of the files the index
holds, as they were indexed. Files the index does not cover (most docs, config
and data) are not in it, so it is never presented as the Grep's own output.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

ANSWER_MARKER = "Answered from the jCodeMunch index"
# Seeded, not calibrated: past this the hook gives up and the Grep runs.
DEFAULT_BUDGET_SECONDS = 4.0
# A page bigger than this is not put into a refusal: the Grep runs instead.
MAX_ANSWER_CHARS = 8000
# Seeded, not calibrated: a Grep under both is left to run. Overridden by
# JCODEMUNCH_HOOK_GREP_ANSWER_MIN_LINES / _MIN_FILES.
DEFAULT_MIN_LINES = 50
DEFAULT_MIN_FILES = 10
# The most matching lines collected to answer one Grep. A match set that
# reaches it is not answered, since search_text marks the page truncated there
# and the count could be partial.
SCAN_LIMIT = 2000
# Bounded memory of answered Greps, newest kept.
MAX_REMEMBERED = 200
_STATE_FILE = "_grep_answered.json"
_ON = {"1", "true", "yes", "on"}
# Grep arguments whose output the index answer cannot reproduce.
_UNTRANSLATABLE = (
    "type",
    "multiline",
    "-A",
    "-B",
    "-C",
    "context",
    "head_limit",
    "offset",
)
_REGEX_CHARS = re.compile(r"[\\^$.|?*+()\[\]{}]")


def answer_enabled() -> bool:
    """Off unless JCODEMUNCH_HOOK_GREP_ANSWER is set to 1 (or true/yes/on).

    Off by default: on short bug-fix tasks the answered Grep raised session
    cost without solving more of them.
    """
    val = os.environ.get("JCODEMUNCH_HOOK_GREP_ANSWER", "0").strip().lower()
    return val in _ON


def _budget_seconds() -> float:
    raw = os.environ.get("JCODEMUNCH_HOOK_ANSWER_BUDGET_SECONDS", "")
    try:
        val = float(raw)
    except ValueError:
        return DEFAULT_BUDGET_SECONDS
    return val if val > 0 else DEFAULT_BUDGET_SECONDS


def _positive_int_env(name: str, default: int) -> int:
    """A positive integer from the environment; anything else is the default,
    so a typo cannot switch the size gate off."""
    raw = os.environ.get(name, "").strip()
    # isascii: str.isdigit() also accepts digits such as "²" that int() rejects.
    if not (raw.isascii() and raw.isdigit()) or int(raw) <= 0:
        return default
    return int(raw)


def _min_lines() -> int:
    return _positive_int_env("JCODEMUNCH_HOOK_GREP_ANSWER_MIN_LINES", DEFAULT_MIN_LINES)


def _min_files() -> int:
    return _positive_int_env("JCODEMUNCH_HOOK_GREP_ANSWER_MIN_FILES", DEFAULT_MIN_FILES)


def _state_path() -> Path:
    base = os.environ.get("CODE_INDEX_PATH") or str(Path.home() / ".code-index")
    return Path(base) / _STATE_FILE


def _key(session_id: str, search_root: str, tool_input: dict) -> str:
    # The root is part of the key: a relative Grep sent from another directory
    # is a different search, not the escape-hatch retry of the first one.
    raw = json.dumps([session_id, search_root, tool_input], sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _load_state() -> list:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except FileNotFoundError:
        return []
    except Exception:
        logger.debug("grep-answer state read failed", exc_info=True)
        return []


def _remember(key: str) -> None:
    try:
        keys = [k for k in _load_state() if k != key] + [key]
        path = _state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(keys[-MAX_REMEMBERED:]), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        logger.debug("grep-answer state write failed", exc_info=True)


def _translate(tool_input: dict, cwd: str):
    """(search root, query, is_regex, file_pattern) or None when untranslatable."""
    pattern = tool_input.get("pattern")
    if not isinstance(pattern, str) or not pattern.strip():
        return None
    if any(tool_input.get(k) not in (None, False, "", 0) for k in _UNTRANSLATABLE):
        return None  # No index equivalent; let the Grep run.
    if tool_input.get("output_mode") == "count":
        return None
    is_regex = bool(_REGEX_CHARS.search(pattern))
    if len(pattern) > (200 if is_regex else 500):
        return None
    base = cwd or os.getcwd()
    path = (tool_input.get("path") or "").strip()
    root = path if (path and os.path.isabs(path)) else os.path.join(base, path)
    root = os.path.realpath(root)
    glob = tool_input.get("glob")
    glob = glob.strip() if isinstance(glob, str) and glob.strip() else None
    if glob and "**" in glob:
        # Grep's `**/` also matches zero directories; the index's fnmatch
        # needs one, so `src/**/*.ts` would drop `src/a.ts`.
        return None
    return root, pattern, is_regex, glob


def _scope(repo_root: str, search_root: str, glob):
    """The file_pattern for a search below the repo root, or False if none fits."""
    try:
        rel = os.path.relpath(search_root, repo_root).replace(os.sep, "/")
    except ValueError:
        return False
    if rel == ".":
        return glob
    if rel.startswith(".."):
        return False
    if os.path.isfile(search_root):
        return False if glob else rel
    return False if glob else f"{rel}/*"


def _complete(res) -> bool:
    """True only for a page that is the whole answer over what was searched."""
    if not isinstance(res, dict) or res.get("error") or not res.get("result_count"):
        return False
    if res.get("timed_out") or res.get("truncated") or res.get("note"):
        return False
    verdict = (res.get("_meta") or {}).get("verdict") or {}
    if verdict.get("incomplete"):
        return False
    if (verdict.get("coverage") or {}).get("complete") is False:
        return False
    return True


def _index_matches_disk(repo: str) -> bool:
    """True when every indexed file's mtime still matches what was indexed.

    A changed file would be answered from its indexed copy, which is a wrong
    answer, so any file that is not provably unchanged refuses the answer.
    """
    try:
        from ...retrieval.freshness import FreshnessProbe
        from ...storage import IndexStore

        owner, _, name = repo.partition("/")
        store = IndexStore()
        # Every field read below is file metadata, which the selective view
        # holds exactly without reading a symbol row. A hook process is always
        # cold, so this skips the largest read on its path.
        index = store.open_selective(owner, name) or store.load_index(owner, name)
        if index is None:
            return False
        probe = FreshnessProbe(
            source_root=getattr(index, "source_root", "") or None,
            indexed_at=getattr(index, "indexed_at", ""),
            index_sha=getattr(index, "git_head", None),
            file_mtimes=getattr(index, "file_mtimes", None),
        )
        return all(probe.classify(f) == "fresh" for f in index.source_files)
    except Exception:
        logger.debug("grep-answer freshness check failed", exc_info=True)
        return False


def _run_with_budget(fn, budget: float):
    box: dict = {}

    def target():
        try:
            box["value"] = fn()
        except Exception:
            logger.debug("grep-answer search failed", exc_info=True)

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(budget)
    if worker.is_alive():
        return None
    return box.get("value")


def answer_grep(tool_input: dict, cwd: str, session_id: str) -> "str | None":
    """The refusal text carrying the index's answer, or None to let the Grep run."""
    if not answer_enabled():
        return None
    translated = _translate(tool_input, cwd)
    if translated is None:
        return None
    search_root, query, is_regex, glob = translated
    key = _key(session_id or "", search_root, tool_input)
    if key in _load_state():
        return None  # Answered once already: this Grep is the escape hatch.

    started = time.monotonic()
    budget = _budget_seconds()
    # Seconds per stage, so a slow answer says where its time went.
    stages: dict = {}

    def timed(stage, fn, *args, **kwargs):
        t0 = time.monotonic()
        try:
            return fn(*args, **kwargs)
        finally:
            stages[stage] = time.monotonic() - t0

    def search():
        from ...tools.resolve_repo import resolve_repo
        from ...tools.search_text import search_text

        start = (
            search_root if os.path.isdir(search_root) else os.path.dirname(search_root)
        )
        resolved = timed("resolve", resolve_repo, start)
        if not resolved.get("indexed") or not resolved.get("repo"):
            return None
        repo_root = resolved.get("source_root") or ""
        file_pattern = _scope(repo_root, search_root, glob) if repo_root else glob
        if file_pattern is False:
            return None
        if not timed("freshness", _index_matches_disk, resolved["repo"]):
            return None
        res = timed(
            "search",
            search_text,
            resolved["repo"],
            query,
            file_pattern=file_pattern,
            is_regex=is_regex,
            selective_load=True,
            # The whole match set, so the gate counts it and the answer can
            # be whole; past the cap the page is truncated and declined.
            scan_limit=SCAN_LIMIT,
        )
        return resolved["repo"], res

    out = _run_with_budget(search, budget)
    stages["total"] = time.monotonic() - started
    logger.debug(
        "grep-answer stages: %s budget=%.3fs outcome=%s",
        " ".join(f"{k}={v:.3f}s" for k, v in list(stages.items())),
        budget,
        "result" if out is not None else "none",
    )
    if out is None:
        return None
    _repo, res = out
    if not _complete(res):
        return None
    line_count = res.get("result_count") or 0
    file_count = len(res.get("results") or [])
    if line_count < _min_lines() and file_count < _min_files():
        return None  # A small Grep is the cheap path; not remembered.
    res = {k: v for k, v in res.items() if k != "_meta"}
    try:
        from ...redact import is_redaction_enabled, redact_dict

        if is_redaction_enabled():
            res, _ = redact_dict(res)
    except Exception:
        logger.debug("grep-answer redaction failed", exc_info=True)
        return None
    scope = (
        "search_text, case-insensitive, over the source files the index holds, "
        "none changed since indexing. Files the index does not hold (most docs, "
        "config and data) and files added since were not searched."
    )
    retry = "Send the same Grep again to run it as written."
    body = _counted(res)
    if body is None:
        return None
    form, payload = body
    if form == "symbols":
        how = (
            "by file and by the function or class they sit in. "
            "get_symbol_source with an id reads that symbol."
        )
    elif form == "files":
        how = "by file."
    else:
        how = (
            "by file for the files with the most matches; `other_files` and "
            "`other_matches` count the rest."
        )
    text = (
        f"{ANSWER_MARKER}: {scope} All {line_count} matching lines in "
        f"{file_count} files are counted below, {how} The lines themselves are "
        f"not shown. {retry}\n{payload}"
    )
    _remember(key)
    elapsed = time.monotonic() - started
    logger.debug("grep-answer answered in %.3fs", elapsed)
    return text


def _dump(obj) -> str:
    return json.dumps(obj, separators=(",", ":"), default=str)


def _counted(res: dict):
    """(form, JSON) for the largest counted form that fits MAX_ANSWER_CHARS.

    Every form counts the whole match set; a smaller form drops detail, never
    matches. "symbols" counts by file and enclosing symbol; "files" by file;
    "top_files" lists the busiest files and sums the rest. None only when not
    even the totals fit.
    """
    grouped = _grouped(res)
    text = _dump(grouped)
    if len(text) <= MAX_ANSWER_CHARS:
        return "symbols", text
    files = [{"file": g["file"], "matches": g["matches"]} for g in grouped["groups"]]
    head = {
        "result_count": grouped["result_count"],
        "file_count": grouped["file_count"],
    }
    text = _dump({**head, "files": files})
    if len(text) <= MAX_ANSWER_CHARS:
        return "files", text
    ranked = sorted(files, key=lambda f: (-f["matches"], str(f["file"])))
    total = grouped["result_count"]
    # Largest prefix of the busiest files that fits, found by bisection.
    lo, hi, best = 0, len(ranked), None
    while lo <= hi:
        mid = (lo + hi) // 2
        shown = ranked[:mid]
        rest = ranked[mid:]
        cand = _dump(
            {
                **head,
                "files": shown,
                "other_files": len(rest),
                "other_matches": total - sum(f["matches"] for f in shown),
            }
        )
        if len(cand) <= MAX_ANSWER_CHARS:
            best, lo = cand, mid + 1
        else:
            hi = mid - 1
    return ("top_files", best) if best is not None else None


def _grouped(res: dict) -> dict:
    """Every match counted by file and by enclosing symbol, without the lines.

    Built from the same whole page as the line form, so the counts are the
    page's own: per-file counts sum to ``result_count``.
    """
    groups = []
    for group in res.get("results") or []:
        symbols: dict = {}
        outside = 0
        for match in group.get("matches") or []:
            enclosing = match.get("in") or {}
            sym_id = enclosing.get("id")
            if not sym_id:
                outside += 1
                continue
            entry = symbols.setdefault(
                sym_id,
                {
                    "id": sym_id,
                    "line": enclosing.get("line"),
                    "end_line": enclosing.get("end_line"),
                    "matches": 0,
                },
            )
            entry["matches"] += 1
        row = {
            "file": group.get("file"),
            "matches": len(group.get("matches") or []),
            "in": list(symbols.values()),
        }
        if outside:
            row["outside_symbols"] = outside
        groups.append(row)
    return {
        "result_count": res.get("result_count") or 0,
        "file_count": len(groups),
        "groups": groups,
    }
