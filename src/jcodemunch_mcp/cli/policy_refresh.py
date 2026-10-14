"""Bring an installed agent policy up to date, only when the user asks (owner's ruling, 2026-10-13).

`init` appends the policy block to CLAUDE.md once and every install path skips
a file that already holds it, so a corrected policy never reached an existing
install. `config --check` could say the block differed, and nothing else did.

⚠⚠ **A command the user types, never a migration.** ``run_refresh`` is the only
writer and only ``claude-md --refresh`` calls it. It shows the diff, asks (or
takes ``--yes``), copies the file to a new backup, and replaces only the lines
``policy.policy_block_span`` names. A server, a hook or an upgrade never calls
it; the notice below only names it.

⚠ "Differs" cannot tell an outdated block from one its owner edited on purpose,
so the diff is shown before every write and the notice says a block edited on
purpose can stay.

⚠ The notice (``policy_notice_for``) rides in the first successful
``resolve_repo`` answer, as the tool-surface notice does, because a user inside
an MCP client never sees the server's log. Its marker records the digest of the
policy it was about: the same policy is said once per install, and a later
version that ships a different policy says it again.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from .policy import active_policy, installed_policy_drift, policy_block_span

logger = logging.getLogger(__name__)

COMMAND = "jcodemunch-mcp claude-md --refresh"
_BOM = chr(0xFEFF)  # spelled by code point: a literal one is invisible in review
SCOPES = ("global", "project")

# Whether THIS PROCESS has already decided about the in-answer notice. In this
# module, which imports nothing heavy, so the suite can switch the notice off
# for every test (tests/conftest.py) without importing the server.
NOTICE_STATE = {"checked": False}
NOTICE_MARKER = "policy_notice_state.json"

RELAY_LINE = (
    "Tell the user this once, in a sentence or two, then carry on with their task. "
    "Do not run the command yourself: it rewrites their file, and it asks them first."
)


def _stdin_is_tty() -> bool:
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def policy_files() -> list[tuple[str, Path]]:
    """The CLAUDE.md files `init` writes the policy into, global first, each once."""
    from .init import _claude_md_path

    seen: set[str] = set()
    out: list[tuple[str, Path]] = []
    for scope in SCOPES:
        path = _claude_md_path(scope)
        key = os.path.normcase(os.path.abspath(path))
        if key not in seen:
            seen.add(key)
            out.append((scope, path))
    return out


def plan_refresh(path: Path) -> dict:
    """What a refresh of *path* would do. Reads only.

    ``{"state": "no_file" | "no_block" | "current" | "unreadable"}``, or
    ``{"state": "differs", "lines_differing", "old_text", "new_text", "diff"}``
    where ``new_text`` is the file with only the block replaced, in the file's
    own line endings.
    """
    if not path.is_file():
        return {"state": "no_file"}
    try:
        old_text = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return {"state": "unreadable", "reason": str(exc)}
    drift = installed_policy_drift(old_text)
    if drift is None:
        return {"state": "no_block"}
    if drift["state"] == "current":
        return {"state": "current"}

    # A byte-order mark is not part of the first line: kept aside, put back.
    bom = _BOM if old_text.startswith(_BOM) else ""
    raw = old_text[len(bom):].splitlines(keepends=True)
    span = policy_block_span([ln.rstrip("\r\n") for ln in raw])
    assert span is not None  # the drift check found one through the same rule
    start, end = span
    block = raw[start:end]
    # The block's own line ending, so a file mixing endings keeps each part's.
    if block[0].endswith("\n"):
        newline = "\r\n" if block[0].endswith("\r\n") else "\n"
    else:  # a one-line block with no line ending: the file's
        newline = "\r\n" if "\r\n" in old_text else "\n"
    # Blank lines closing the block are the gap before what follows; they stay.
    trailing: list[str] = []
    while block and not block[-1].strip():
        trailing.insert(0, block.pop())
    body = active_policy().replace("\r\n", "\n")
    if not body.endswith("\n"):
        body += "\n"
    body = body.replace("\n", newline)
    at_eof = end == len(raw)
    if at_eof and not trailing and not old_text.endswith(("\n", "\r")):
        body = body[: -len(newline)]  # the file had no final newline; it still has none
    new_text = bom + "".join(raw[:start]) + body + "".join(trailing) + "".join(raw[end:])

    after = installed_policy_drift(new_text)
    if after != {"state": "current"}:
        # Never write a file this module cannot then call current.
        return {"state": "unreadable", "reason": f"the replacement does not read as current ({after})"}
    diff = "".join(
        difflib.unified_diff(
            [ln.rstrip("\r\n") + "\n" for ln in raw[start:end]],
            [ln + "\n" for ln in body.replace("\r\n", "\n").rstrip("\n").split("\n")],
            fromfile=f"{path} (installed)",
            tofile=f"{path} (this version)",
        )
    )
    return {
        "state": "differs",
        "lines_differing": drift["lines_differing"],
        "old_text": old_text,
        "new_text": new_text,
        "diff": diff,
    }


def _backup_path(path: Path, now: datetime) -> Path:
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    candidate = path.with_name(f"{path.name}.jcodemunch-{stamp}.bak")
    n = 1
    while candidate.exists():
        n += 1
        candidate = path.with_name(f"{path.name}.jcodemunch-{stamp}-{n}.bak")
    return candidate


def apply_refresh(path: Path, plan: dict, *, now: datetime | None = None) -> Path:
    """Write *plan* to *path* after copying the current bytes to a new backup. Returns the backup.

    ⚠ A symlinked CLAUDE.md (a dotfiles manager) is written THROUGH: the link
    stays a link and its target gets the new text, with the target's mode. A
    replace of the link itself would leave the dotfiles source stale and the
    link broken (review, 2026-10-14).
    """
    now = now or datetime.now(timezone.utc)
    target = path.resolve()
    current = target.read_bytes()
    if current.decode("utf-8") != plan["old_text"]:
        raise RuntimeError(f"{path} changed after its diff was shown; run the command again")
    backup = _backup_path(target, now)
    backup.write_bytes(current)
    tmp = target.with_name(target.name + ".jcodemunch-refresh.tmp")
    tmp.write_bytes(plan["new_text"].encode("utf-8"))
    try:
        shutil.copymode(target, tmp)
    except OSError:
        logger.debug("policy refresh could not copy the file mode", exc_info=True)
    os.replace(tmp, target)
    return backup


def run_refresh(*, yes: bool = False, dry_run: bool = False, out=print) -> int:
    """`claude-md --refresh`. Exit status: 0 done or nothing to do, 1 a file could not be read or written."""
    from .init import ensure_config_loaded

    ensure_config_loaded()
    status = 0
    acted = False
    for scope, path in policy_files():
        plan = plan_refresh(path)
        state = plan["state"]
        if state in ("no_file", "no_block", "current"):
            continue
        acted = True
        if state == "unreadable":
            out(f"{path}: cannot refresh: {plan['reason']}")
            status = 1
            continue
        out(f"{path} ({scope}): the jcodemunch policy block differs from this version's "
            f"({plan['lines_differing']} line(s)). Everything outside the block stays as it is.")
        out(plan["diff"].rstrip("\n"))
        if dry_run:
            out("Dry run: nothing written.")
            continue
        if not yes:
            if not _stdin_is_tty():
                out("Not written: no terminal to ask on. Run it again with --yes to write it.")
                continue
            answer = input(f"Replace the block in {path}? A backup is kept. [y/N] ")
            if answer.strip().lower() not in ("y", "yes"):
                out("Not written.")
                continue
        try:
            backup = apply_refresh(path, plan)
        except Exception as exc:  # noqa: BLE001 - reported to the user, who typed the command
            logger.debug("policy refresh failed", exc_info=True)
            out(f"{path}: not written: {exc}")
            status = 1
            continue
        out(f"Replaced. The previous file is at {backup}")
    if not acted:
        out("Nothing to refresh: no CLAUDE.md here holds a jcodemunch policy block that differs from this version's.")
    return status


# -- the once-per-policy notice --------------------------------------------------


def _marker_path() -> Path:
    from ..surface_offer import notice_marker_path

    return notice_marker_path(NOTICE_MARKER)


def _policy_digest() -> str:
    return hashlib.sha256(active_policy().encode("utf-8")).hexdigest()


def policy_notice_for(name: str, result: object, *, tool: str = "resolve_repo", version: str = "") -> dict | None:
    """The refresh notice for the agent to relay, or None.

    ⚠ Only on a successful *tool* answer, so an error does not spend it. A
    process decides once. Nothing to say writes no marker: an install whose
    block is current is told when a later version's policy differs.

    ⚠ Fail-safe in the say-nothing direction for a read failure, and in the
    say-again direction for a marker that cannot be written.
    """
    if name != tool or not isinstance(result, dict) or "error" in result:
        return None
    if NOTICE_STATE["checked"]:
        return None
    NOTICE_STATE["checked"] = True
    try:
        digest = _policy_digest()
        marker = _marker_path()
        if marker.is_file():
            try:
                if json.loads(marker.read_text(encoding="utf-8")).get("policy_digest") == digest:
                    return None
            except (OSError, ValueError):
                logger.debug("policy notice marker unreadable; saying it again", exc_info=True)
        files = []
        for _scope, path in policy_files():
            plan = plan_refresh(path)
            if plan["state"] == "differs":
                files.append({"path": str(path), "lines_differing": plan["lines_differing"]})
    except Exception:
        logger.debug("policy notice skipped", exc_info=True)
        return None
    if not files:
        return None
    where = ", ".join(f"{f['path']} ({f['lines_differing']} line(s))" for f in files)
    message = (
        f"The jcodemunch policy block in {where} differs from the one this version installs. "
        f"`{COMMAND}` shows the difference, asks before writing, keeps a backup and replaces only that block. "
        "If the block was edited on purpose, it can stay as it is."
    )
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        tmp = marker.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(
                {
                    "shown_at": datetime.now(timezone.utc).isoformat(),
                    "tool": name,
                    "policy_digest": digest,
                    "version": version,
                }
            ),
            encoding="utf-8",
        )
        tmp.replace(marker)
    except OSError:
        logger.debug("policy notice marker not written", exc_info=True)
    return {"message": message, "files": files, "command": COMMAND, "relay": RELAY_LINE}
