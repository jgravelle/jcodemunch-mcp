"""Answer a redirected Grep from the index, inside the redirect itself.

A PreToolUse redirect that only says "use search_text" costs the agent two
turns: the refused Grep, then the search it was told to make. This module runs
that search in the hook and returns its page, so the refusal carries the answer
and the agent's next turn reads it instead of asking for it.

It answers at most once per (session, Grep arguments): the same Grep sent again
runs normally, which is the escape hatch to raw output. It answers nothing, and
the Grep runs untouched, when the switch is off, the search root is not an
indexed repo, the arguments have no index translation (file type, multiline,
context lines, paging, a count, a `**` glob), an indexed file changed on disk
since it was indexed, or the search errors, finds nothing, is cut short (time
budget, result cap, unreadable files) or returns a page over the size cap.

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
# Bounded memory of answered Greps, newest kept.
MAX_REMEMBERED = 200
_STATE_FILE = "_grep_answered.json"
_ON = {"1", "true", "yes", "on"}
# Grep arguments whose output the index answer cannot reproduce.
_UNTRANSLATABLE = (
    "type", "multiline", "-A", "-B", "-C", "context", "head_limit", "offset",
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


def _state_path() -> Path:
    base = os.environ.get("CODE_INDEX_PATH") or str(Path.home() / ".code-index")
    return Path(base) / _STATE_FILE


def _key(session_id: str, tool_input: dict) -> str:
    raw = json.dumps([session_id, tool_input], sort_keys=True, default=str)
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
    key = _key(session_id or "", tool_input)
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
    repo, res = out
    if not _complete(res):
        return None
    res = {k: v for k, v in res.items() if k != "_meta"}
    try:
        from ...redact import is_redaction_enabled, redact_dict

        if is_redaction_enabled():
            res, _ = redact_dict(res)
    except Exception:
        logger.debug("grep-answer redaction failed", exc_info=True)
        return None
    try:
        from ...encoding import encode_response

        payload, _ = encode_response("search_text", res, repo=repo)
    except Exception:
        logger.debug("grep-answer encoding failed", exc_info=True)
        return None
    if not isinstance(payload, str):
        payload = json.dumps(payload, separators=(",", ":"), default=str)
    if len(payload) > MAX_ANSWER_CHARS:
        return None
    _remember(key)
    elapsed = time.monotonic() - started
    logger.debug("grep-answer answered in %.3fs", elapsed)
    return (
        f"{ANSWER_MARKER}: search_text, case-insensitive, over the source files "
        "the index holds, none changed since indexing. Files the index does not "
        "hold (most docs, config and data) and files added since were not "
        "searched. Send the same Grep again to run it as written.\n"
        f"{payload}"
    )
