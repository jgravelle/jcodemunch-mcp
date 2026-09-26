"""Git-root detection for index identity.

Walks up from a path looking for a `.git` directory; when found, derives
the repo identity from `git remote get-url origin` (so a clone of
`elastic/kibana` indexes as `elastic/kibana` regardless of the local
folder name) and falls back to the git-root basename for repos with no
configured remote.

Foundation for #288 — v1.95.0 uses this for identity only; the merge
logic that lets `index ./packages` and `index ./scripts` coalesce into
one `elastic/kibana` index lands in v1.96.
"""

import logging
import hashlib
import re
import subprocess
from pathlib import Path
from typing import NamedTuple, Optional

logger = logging.getLogger(__name__)


class GitRootIdentity(NamedTuple):
    """Result of git-root detection.

    Attributes:
        git_root: Absolute path of the enclosing git working tree.
        owner: Repo owner ("local" if no remote is configured).
        name: Repo name (from `origin` URL or git-root basename).
    """
    git_root: str
    owner: str
    name: str


class IdentityDecision(NamedTuple):
    """Resolved local-folder index identity."""

    mode: str
    owner: str
    name: str
    git_root: str
    walk_root: str


class IdentityModeConflict(ValueError):
    """Raised when an existing index blocks an explicit identity-mode switch."""


class IdentityModeAmbiguous(ValueError):
    """Raised when both local and git identity forms match a path."""


def _local_repo_name(folder_path: Path) -> str:
    digest = hashlib.sha1(str(folder_path).encode("utf-8")).hexdigest()[:8]
    return f"{folder_path.name}-{digest}"


def _contains_path(root: str, path: Path) -> bool:
    if not root:
        return False
    try:
        root_path = Path(root).expanduser().resolve()
        return path == root_path or path.is_relative_to(root_path)
    except Exception:
        return False


def _existing_git_identity(path: Path, store) -> Optional[IdentityDecision]:
    if store is None:
        return None
    try:
        entries = store.list_repos()
    except Exception:
        return None
    # Nearest enclosing working tree of the queried path. Used to refuse
    # containment matches that cross a worktree boundary (#372): a path
    # inside `<repo>/.claude/worktrees/<name>` is path-contained in the
    # parent's git_root but belongs to the worktree's own index.
    nearest_root = _find_git_root(path)
    for entry in entries:
        repo_id = entry.get("repo", "")
        if "/" not in repo_id:
            continue
        owner, name = repo_id.split("/", 1)
        if "git_root" in entry:
            git_root = entry.get("git_root", "") or ""
        else:
            try:
                index = store.load_index(owner, name)
            except Exception:
                index = None
            git_root = getattr(index, "git_root", "") if index is not None else ""
        if _contains_path(git_root, path):
            if (
                nearest_root is not None
                and nearest_root != Path(git_root).expanduser().resolve()
                and is_linked_worktree(nearest_root)
            ):
                continue
            return IdentityDecision(
                mode="git",
                owner=owner,
                name=name,
                git_root=str(Path(git_root).resolve()),
                walk_root=str(Path(git_root).resolve()),
            )
    return None


def _configured_identity_mode(folder_path: Path) -> str:
    try:
        from .. import config as _config
        configured = _config.get("identity_mode", None, repo=str(folder_path))
        if isinstance(configured, str) and configured in {"local", "git"}:
            return configured
        if _config.get("git_root_identity", False, repo=str(folder_path)):
            return "git"
    except Exception:
        pass
    return "local"


def _identity_conflict_message(existing: IdentityDecision, requested: str) -> str:
    return (
        f"Existing index {existing.owner}/{existing.name} uses {existing.mode} identity. "
        f"invalidate it before recreating this path with {requested} identity."
    )


def _local_identity_if_present(path: Path, local_name: str, store) -> Optional[IdentityDecision]:
    if store is None:
        return None
    try:
        if not store.inspect_index("local", local_name).index_present:
            return None
    except Exception:
        return None
    return IdentityDecision(
        mode="local",
        owner="local",
        name=local_name,
        git_root="",
        walk_root=str(path),
    )


def _path_has_git_root(folder_path: Path) -> bool:
    return _find_git_root(folder_path) is not None


def resolve_index_identity(
    path: str,
    mode: str = "config",
    store=None,
) -> IdentityDecision:
    """Resolve how a local path should be keyed in the index store."""
    folder_path = Path(path).expanduser().resolve()
    if folder_path.is_file():
        folder_path = folder_path.parent

    requested = (mode or "config").lower()
    if requested not in {"config", "local", "git"}:
        raise ValueError("identity_mode must be one of: config, local, git")

    local_name = _local_repo_name(folder_path)
    configured = _configured_identity_mode(folder_path) if requested == "config" else requested
    local_existing = _local_identity_if_present(folder_path, local_name, store)

    should_probe_git_identity = store is not None and (
        requested == "local" or _path_has_git_root(folder_path)
    )
    existing_git = _existing_git_identity(folder_path, store) if should_probe_git_identity else None

    # A linked worktree's git-mode index is keyed `local/<name>-<hash>` (#372),
    # the same key the local probe looks up, so both probes can name ONE index.
    # It records a git_root, so it is the git index, not a local one (#882).
    if (
        local_existing is not None
        and existing_git is not None
        and (local_existing.owner, local_existing.name) == (existing_git.owner, existing_git.name)
    ):
        local_existing = None

    if requested == "git" and local_existing is not None:
        raise IdentityModeConflict(_identity_conflict_message(local_existing, "git"))

    if local_existing is not None and existing_git is not None:
        raise IdentityModeAmbiguous(
            "Both local and git identity indexes already match this path. "
            "Invalidate one of them before indexing or resolving this path."
        )

    if requested == "config":
        if local_existing is not None:
            return local_existing
        if existing_git is not None:
            return existing_git
        requested = configured
    elif requested == "local":
        if existing_git is not None:
            raise IdentityModeConflict(_identity_conflict_message(existing_git, "local"))
        if local_existing is not None:
            return local_existing

    if requested == "git":
        ident = detect_git_root(str(folder_path))
        if ident is not None:
            git_root = str(Path(ident.git_root).resolve())
            return IdentityDecision(
                mode="git",
                owner=ident.owner,
                name=ident.name,
                git_root=git_root,
                walk_root=git_root,
            )

    return IdentityDecision(
        mode="local",
        owner="local",
        name=local_name,
        git_root="",
        walk_root=str(folder_path),
    )


# git@github.com:owner/repo.git   |   https://github.com/owner/repo(.git)
# Also covers gitlab, bitbucket, and generic git hosts — we just want
# the trailing two path segments.
_REMOTE_OWNER_REPO = re.compile(
    r"""(?:[:/])(?P<owner>[^/:]+)/(?P<name>[^/]+?)(?:\.git)?/?\s*$"""
)


def is_linked_worktree(path: Path) -> bool:
    """True when `path` is the top of a linked `git worktree` checkout.

    A linked worktree marks its root with a `.git` FILE whose `gitdir:`
    target lives under the main checkout's `.git/worktrees/<name>`.
    Submodules also use a `.git` file but point at `.git/modules/<name>`,
    so they deliberately do NOT match (#372 — worktree exclusion must not
    change submodule indexing behavior).
    """
    dotgit = path / ".git"
    try:
        if not dotgit.is_file():
            return False
        text = dotgit.read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        return False
    if not text.startswith("gitdir:"):
        return False
    target = Path(text[len("gitdir:"):].strip())
    if not target.is_absolute():
        target = path / target
    return target.parent.name == "worktrees"


def _find_git_root(start: Path) -> Optional[Path]:
    """Walk up from `start` looking for a `.git` directory or file.

    Returns the absolute path of the enclosing working tree, or None if
    no `.git` is found anywhere up to the filesystem root. Handles
    `.git` as a file (worktrees, submodules) the same as a directory —
    its presence still marks a working tree we should anchor to.
    """
    p = start.resolve()
    if not p.exists():
        return None
    if p.is_file():
        p = p.parent
    for candidate in (p, *p.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _read_origin_url(git_root: Path) -> Optional[str]:
    """Return the `origin` remote URL for a git working tree, or None.

    Defensive subprocess posture (jcm#303 follow-up): explicit
    `stdin=subprocess.DEVNULL` so the call never blocks waiting on stdin,
    plus env-neutralisation that matches `tools/resolve_repo._git_toplevel`:

      - GIT_CONFIG_NOSYSTEM=1: system config doesn't influence the probe.
      - GIT_CONFIG_GLOBAL=/dev/null: same for ~/.gitconfig.
      - GIT_TERMINAL_PROMPT=0: prevent credential prompts on remotes
        requiring auth.

    Without these, large-worktree environments on Windows under
    `git_root_identity=true` could hang here during resolve_repo's
    provisional-id computation (reported by @rknighton).
    """
    import os as _os
    env = _os.environ.copy()
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = _os.devnull
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        result = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"],
            cwd=str(git_root),
            check=False,
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=5,
            stdin=subprocess.DEVNULL,
            env=env,
        )
    except (OSError, subprocess.SubprocessError):
        logger.debug("git config probe failed for %s", git_root, exc_info=True)
        return None
    if result.returncode != 0:
        return None
    url = result.stdout.strip()
    return url or None


def _parse_owner_repo(remote_url: str) -> Optional[tuple[str, str]]:
    """Extract `(owner, name)` from a git remote URL.

    Returns None when the URL does not contain a recognizable
    owner/name suffix (e.g. a bare server-relative path).
    """
    m = _REMOTE_OWNER_REPO.search(remote_url)
    if not m:
        return None
    owner = m.group("owner").strip()
    name = m.group("name").strip()
    if not owner or not name:
        return None
    return owner, name


def detect_git_root(path: str) -> Optional[GitRootIdentity]:
    """Detect the enclosing git root and derive a repo identity.

    Resolution order:

    1. No `.git` found anywhere up the tree -> return None.  Caller
       falls back to the basename-keyed identity (today's behavior).
    2. `.git` found and `origin` remote URL parses to `<owner>/<name>`
       -> return that identity.  This makes a clone of
       `https://github.com/elastic/kibana` index as `elastic/kibana`
       regardless of the local folder name.
    3. `.git` found but no usable origin -> return identity
       `("local", <git-root-basename>)`.  Caller may append a
       path-derived hash for stable disambiguation.

    The returned `git_root` is always the absolute path of the working
    tree, suitable for storing on the index manifest as the canonical
    repo location.
    """
    root = _find_git_root(Path(path).expanduser())
    if root is None:
        return None

    if is_linked_worktree(root):
        # A linked worktree shares `origin` with its main checkout, so the
        # origin-derived identity would claim the parent repo's index slot
        # and the two would collide (#372: watch-claude vs watch-all).
        # Key each worktree by its own path instead.
        return GitRootIdentity(
            git_root=str(root), owner="local", name=_local_repo_name(root)
        )

    url = _read_origin_url(root)
    if url:
        parsed = _parse_owner_repo(url)
        if parsed:
            owner, name = parsed
            return GitRootIdentity(git_root=str(root), owner=owner, name=name)

    return GitRootIdentity(git_root=str(root), owner="local", name=root.name)
