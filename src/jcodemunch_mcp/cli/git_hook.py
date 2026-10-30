"""The opt-in git post-checkout hook: re-index the files a branch switch changed.

`init --git-hook` writes a small block into the repository's `post-checkout`
hook. On a branch checkout git runs it with the old and new commits; the block
starts `jcodemunch-mcp hook-post-checkout <old> <new>` in the background, and
that command re-indexes only the files `git diff` lists between the two.

⚠⚠ **The command never builds a first index.** A partial path list handed to
`index_folder` on a folder with no index would publish a corpus of a few files
as if it were the repository. A folder with no index is left alone.

⚠ The block goes right after the shebang, not at the end: a user's hook that
ends in `exit` would otherwise skip it. It is backgrounded and its `if` returns
0 when skipped, so it never changes what the user's hook does or returns.

⚠ `git diff --no-renames`: with rename detection on, `--name-only` lists only
the new name, and the old file would stay in the index.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import stat
import subprocess
import time
from pathlib import Path
from typing import Callable, Optional

from .init import _hook_invocation

logger = logging.getLogger(__name__)

BLOCK_BEGIN = "# >>> jcodemunch post-checkout >>>"
BLOCK_END = "# <<< jcodemunch post-checkout <<<"
_DEFAULT_SHEBANG = "#!/bin/sh"
_SHELLS = {"sh", "bash", "dash", "zsh", "ksh", "ash"}
_NULL_SHA = re.compile(r"^0+$")
_GIT_TIMEOUT = 30


def _git(cwd: str, *args: str) -> Optional[str]:
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=_GIT_TIMEOUT,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        logger.debug("git %s failed in %s", args, cwd, exc_info=True)
        return None
    if out.returncode != 0:
        return None
    return out.stdout


def _hooks_dir(cwd: str) -> Optional[Path]:
    """The directory git reads hooks from, honoring core.hooksPath and worktrees."""
    out = _git(cwd, "rev-parse", "--git-path", "hooks")
    if out is None:
        return None
    p = Path(out.strip())
    if not p.is_absolute():
        p = Path(cwd) / p
    return p.resolve()


def _block() -> str:
    exe = _hook_invocation()
    return (
        f"{BLOCK_BEGIN}\n"
        "# Added by `jcodemunch-mcp init --git-hook`: on a branch checkout, re-index the\n"
        "# files that differ between the two commits, in the background.\n"
        "# Remove it with `jcodemunch-mcp uninstall`.\n"
        'if [ "$3" = "1" ]; then\n'
        f'  ({exe} hook-post-checkout "$1" "$2" </dev/null >/dev/null 2>&1 &)\n'
        "fi\n"
        f"{BLOCK_END}\n"
    )


def _strip_block(text: str) -> str:
    pattern = re.compile(
        r"^" + re.escape(BLOCK_BEGIN) + r"\n.*?^" + re.escape(BLOCK_END) + r"\n?",
        re.M | re.S,
    )
    return pattern.sub("", text)


def _shebang_shell(first_line: str) -> Optional[str]:
    """The interpreter a shebang names (`/usr/bin/env bash` -> `bash`), or None."""
    if not first_line.startswith("#!"):
        return None
    parts = first_line[2:].strip().split()
    if not parts:
        return None
    prog = os.path.basename(parts[0])
    if prog == "env" and len(parts) > 1:
        prog = os.path.basename(parts[1])
    return prog


def _backup(path: Path) -> None:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(path, path.with_name(f"{path.name}.jcodemunch-{stamp}.bak"))


def _make_executable(path: Path) -> None:
    try:
        mode = path.stat().st_mode
        path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError:
        logger.debug("chmod failed for %s", path, exc_info=True)


def install_post_checkout(cwd: str, dry_run: bool = False, backup: bool = True) -> str:
    """Write or refresh the jcodemunch block in this repository's post-checkout hook."""
    hooks = _hooks_dir(cwd)
    if hooks is None:
        return f"  skipped: {cwd} is not a git repository"
    path = hooks / "post-checkout"

    if path.exists():
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines(keepends=True)
        first = lines[0].rstrip("\r\n") if lines else ""
        shell = _shebang_shell(first)
        if first.startswith("#!") and shell not in _SHELLS:
            return (
                f"  left as is: {path} runs under {shell}, not a shell. To add the "
                f"re-index yourself, run `jcodemunch-mcp hook-post-checkout <old> <new>` "
                f"from it on a branch checkout"
            )
        body = _strip_block(text)
        body_lines = body.splitlines(keepends=True)
        if body_lines and body_lines[0].startswith("#!"):
            head, rest = body_lines[0], "".join(body_lines[1:])
            if not head.endswith("\n"):
                head += "\n"
        else:
            head, rest = _DEFAULT_SHEBANG + "\n", body
        new_text = head + _block() + rest
        verb = "refresh" if BLOCK_BEGIN in text else "add to"
        if new_text == text:
            return f"  already current: {path}"
        if dry_run:
            return f"  would {verb} {path}"
        if backup:
            _backup(path)
        path.write_text(new_text, encoding="utf-8", newline="\n")
        _make_executable(path)
        return f"  {'refreshed' if verb == 'refresh' else 'added to'} {path}"

    if dry_run:
        return f"  would create {path}"
    hooks.mkdir(parents=True, exist_ok=True)
    path.write_text(_DEFAULT_SHEBANG + "\n" + _block(), encoding="utf-8", newline="\n")
    _make_executable(path)
    return f"  created {path}"


def uninstall_post_checkout(
    cwd: str, dry_run: bool = False, backup: bool = True
) -> str:
    """Remove the jcodemunch block; delete the file if nothing but a shebang is left."""
    hooks = _hooks_dir(cwd)
    if hooks is None:
        return "  not a git repository here"
    path = hooks / "post-checkout"
    if not path.exists():
        return "  not installed"
    text = path.read_text(encoding="utf-8", errors="replace")
    if BLOCK_BEGIN not in text:
        return "  not installed"
    rest = _strip_block(text)
    left = [ln for ln in rest.splitlines() if ln.strip()]
    only_shebang = len(left) == 0 or (len(left) == 1 and left[0].startswith("#!"))
    if dry_run:
        return f"  would {'delete' if only_shebang else 'remove the block from'} {path}"
    if backup:
        _backup(path)
    if only_shebang:
        path.unlink()
        return f"  deleted {path}"
    path.write_text(rest, encoding="utf-8", newline="\n")
    return f"  removed the block from {path}"


def _indexed_root(root: Path, storage_path: Optional[str]) -> bool:
    """Is there an index whose source_root is this folder? (refresh's rule)."""
    from ..storage.sqlite_store import SQLiteIndexStore

    base = (
        storage_path
        or os.environ.get("CODE_INDEX_PATH")
        or str(Path.home() / ".code-index")
    )
    if not Path(base).is_dir():
        return False
    store = SQLiteIndexStore(base_path=base)
    for entry in store.list_repos():
        src = entry.get("source_root") or ""
        try:
            if src and Path(src).resolve() == root:
                return True
        except OSError:
            continue
    return False


def run_post_checkout(
    prev: str,
    new: str,
    flag: str = "1",
    *,
    cwd: Optional[str] = None,
    storage_path: Optional[str] = None,
    indexer: Optional[Callable[..., dict]] = None,
) -> int:
    """Re-index the files that differ between ``prev`` and ``new``. Always returns 0.

    Git passes ``flag`` 1 for a branch checkout and 0 for a file checkout. A
    file checkout, an unmoved HEAD, a clone (all-zero old commit), an unindexed
    folder, an empty diff and any error all do nothing.
    """
    try:
        if flag != "1" or not prev or not new or prev == new or _NULL_SHA.match(prev):
            return 0
        here = cwd or os.getcwd()
        top = _git(here, "rev-parse", "--show-toplevel")
        if not top:
            return 0
        root = Path(top.strip()).resolve()
        if not _indexed_root(root, storage_path):
            return 0
        out = _git(
            str(root),
            "diff",
            "--name-only",
            "--no-renames",
            "-z",
            "--ignore-submodules=all",
            prev,
            new,
        )
        if out is None:
            return 0
        paths = sorted({p for p in out.split("\0") if p})
        if not paths:
            return 0
        if indexer is None:
            from ..tools.index_folder import index_folder as indexer
        indexer(
            path=str(root),
            paths=paths,
            use_ai_summaries=False,
            storage_path=storage_path or os.environ.get("CODE_INDEX_PATH"),
        )
    except Exception:
        logger.debug("hook-post-checkout failed", exc_info=True)
    return 0
