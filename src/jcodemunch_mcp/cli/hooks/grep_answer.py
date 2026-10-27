"""Answer a redirected Grep from the index, inside the redirect itself.

A PreToolUse redirect that only says "use search_text" costs the agent two
turns: the refused Grep, then the search it was told to make. This module runs
that search in the hook and returns its page, so the refusal carries the answer
and the agent's next turn reads it instead of asking for it.

It answers at most once per (session, Grep arguments): the same Grep sent again
runs normally, which is the escape hatch to raw output. It answers nothing, and
the Grep runs untouched, when the switch is off, the search root is not an
indexed repo, the arguments have no index translation, the search errors,
finds nothing, runs past its time budget, or returns a page over the size cap.
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
# Seeded, not measured: past this the hook gives up and the Grep runs.
DEFAULT_BUDGET_SECONDS = 2.0
# A page bigger than this is not put into a refusal: the Grep runs instead.
MAX_ANSWER_CHARS = 8000
# Bounded memory of answered Greps, newest kept.
MAX_REMEMBERED = 200
_STATE_FILE = "_grep_answered.json"
_OFF = {"0", "false", "no", "off"}
_REGEX_CHARS = re.compile(r"[\\^$.|?*+()\[\]{}]")


def answer_enabled() -> bool:
    """JCODEMUNCH_HOOK_GREP_ANSWER=0 (or false/no/off) turns answering off."""
    val = os.environ.get("JCODEMUNCH_HOOK_GREP_ANSWER", "1").strip().lower()
    return val not in _OFF


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
    parts = {
        k: tool_input.get(k)
        for k in ("pattern", "path", "glob", "type", "-i", "output_mode")
    }
    raw = json.dumps([session_id, parts], sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _load_state() -> list:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
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
    if tool_input.get("type") or tool_input.get("multiline"):
        return None  # No index equivalent; let the Grep run.
    is_regex = bool(_REGEX_CHARS.search(pattern))
    if len(pattern) > (200 if is_regex else 500):
        return None
    base = cwd or os.getcwd()
    path = (tool_input.get("path") or "").strip()
    root = path if (path and os.path.isabs(path)) else os.path.join(base, path)
    root = os.path.realpath(root)
    glob = tool_input.get("glob")
    glob = glob.strip() if isinstance(glob, str) and glob.strip() else None
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

    def search():
        from ...tools.resolve_repo import resolve_repo
        from ...tools.search_text import search_text

        start = (
            search_root if os.path.isdir(search_root) else os.path.dirname(search_root)
        )
        resolved = resolve_repo(start)
        if not resolved.get("indexed") or not resolved.get("repo"):
            return None
        repo_root = resolved.get("source_root") or ""
        file_pattern = _scope(repo_root, search_root, glob) if repo_root else glob
        if file_pattern is False:
            return None
        res = search_text(
            resolved["repo"], query, file_pattern=file_pattern, is_regex=is_regex
        )
        return resolved["repo"], res

    out = _run_with_budget(search, budget)
    if out is None:
        return None
    repo, res = out
    if not isinstance(res, dict) or res.get("error") or not res.get("result_count"):
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
        f"{ANSWER_MARKER} (search_text, case-insensitive). This is the result "
        "of the Grep you asked for; call the same Grep again if you need raw "
        f"output.\n{payload}"
    )
