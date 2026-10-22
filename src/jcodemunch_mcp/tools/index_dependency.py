"""Index an installed third-party dependency of an already-indexed repo.

Resolves an npm or PyPI package to the version ACTUALLY INSTALLED in the
host repo's ``node_modules`` / virtualenv site-packages (the lockfile truth —
no registry lookup, nothing leaves the machine), copies a filtered snapshot
into the index store's own ``deps/`` area, and indexes it there as a normal
queryable local repo.

Why snapshot-then-index instead of indexing in place:
- a dep path inside a git-identity host's git_root would resolve to the HOST
  index (or raise IdentityModeConflict) via the contains-path identity match;
- ``dist``/``build`` are default skip dirs, and compiled npm packages ship
  only ``dist/`` — the snapshot is indexed via explicit ``paths=`` so its
  contents always count;
- pnpm symlink layouts and later version upgrades both fall out naturally
  (realpath before copy; a new version is a new snapshot dir → new repo id).
"""

import logging
import os
import re
import shutil
import time
from glob import glob
from pathlib import Path
from typing import Optional

from ..parser import LANGUAGE_EXTENSIONS, get_language_for_path
from ..security import get_max_file_size, is_binary_file, resolve_within, should_exclude_file
from ..storage import IndexStore
from .index_folder import index_folder
from ._utils import resolve_repo

logger = logging.getLogger(__name__)

_VENV_DIR_CANDIDATES = (".venv", "venv", "env")
_SKIP_COPY_DIRS = frozenset({"node_modules", ".git", "__pycache__", ".venv", "venv"})
_SKIP_COPY_SUFFIXES = (".min.js", ".min.ts", ".bundle.js", ".map", ".pyc", ".pyo")
_DOC_FILE_RE = re.compile(r"^(readme|changelog|history|license|licence|notice)", re.IGNORECASE)
_DEFAULT_MAX_FILES = 2000
# At most this many withheld dist-info paths are listed; the total and a
# truncated flag ride beside the list.
_WITHHELD_LIST_CAP = 20

_THIN_DOCS_NOTE = (
    "This package ships minimal doc files; symbol signatures and docstrings "
    "are still indexed and queryable."
)


# What a package name may look like. An npm name is one segment, or a scoped
# "@scope/name"; a PyPI distribution or import name is one segment. Neither may
# be absolute, contain "..", a backslash or a drive letter: the name is joined
# onto node_modules / site-packages, so anything else could name a directory
# outside them.
_NPM_NAME_RE = re.compile(r"^(?:@[A-Za-z0-9][A-Za-z0-9._~-]*/)?[A-Za-z0-9][A-Za-z0-9._~-]*$")
_PYPI_NAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")
# A top_level.txt entry is an importable module name.
_TOP_LEVEL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _valid_name(package: str, pattern: "re.Pattern[str]") -> bool:
    return bool(pattern.fullmatch(package)) and ".." not in package


def _contained(path: Path, root: Path) -> Optional[Path]:
    """``path`` resolved, if it stays inside ``root`` resolved; else None.

    The confinement rule is ``security.resolve_within``'s, applied to a path
    that is already absolute (joining an absolute path keeps it)."""
    return resolve_within(root, str(path))


def _store_roots(source_root: Path) -> set:
    """The repo root, its node_modules, and each repo-local site-packages and
    virtualenv root. A package directory may not be or contain any of them
    (``_is_store``): a link to one would snapshot the whole repo or every
    installed package as if it were one dependency."""
    roots = {source_root.resolve(), (source_root / "node_modules").resolve()}
    for sp in _site_packages_dirs(source_root):
        roots.add(sp.resolve())
        # The virtualenv root only when site-packages was found under it; a
        # workspace that is merely named env/ or venv/ is not a store.
        for venv_name in _VENV_DIR_CANDIDATES:
            venv = source_root / venv_name
            if venv in sp.parents:
                roots.add(venv.resolve())
    return roots


def _is_store(real: Path, source_root: Path, roots: set) -> bool:
    """Is ``real`` (resolved) a store rather than one package?

    True when it is or contains a store root: that covers the root itself and
    every directory between a virtualenv and its site-packages (``.venv/Lib``,
    ``venv/lib/python3.12``), whatever the layout. An ``@scope`` directory
    directly under node_modules holds a whole scope of packages, so it counts
    too.
    """
    if any(_contained(r, real) is not None for r in roots):
        return True
    node_modules = (source_root / "node_modules").resolve()
    return real.parent == node_modules and real.name.startswith("@")


_STORE_REFUSAL = (
    "resolves to the repo root, node_modules, a site-packages or a virtualenv "
    "rather than to one package; only a package directory is read."
)


def _metadata_in_repo(directory: Path, source_root: Path, names: tuple) -> bool:
    """Are ``directory`` and each of its metadata ``names`` that exist inside
    the repo after symlinks? Metadata is read for a package's name and
    version, which become the snapshot's name, so it must come from the repo
    like the package itself."""
    if _contained(directory, source_root) is None:
        return False
    for name in names:
        path = directory / name
        if (path.exists() or path.is_symlink()) and _contained(path, source_root) is None:
            return False
    return True


def _pep503_normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _safe_snapshot_name(name: str, version: str) -> str:
    """One directory name for a snapshot. Both parts come from the installed
    package's own metadata, so neither may carry a separator or ".."."""
    safe = name.lstrip("@").replace("/", "__")
    safe = re.sub(r"[^A-Za-z0-9._@\-]", "_", safe).replace("..", "_")
    safe_version = re.sub(r"[^A-Za-z0-9._+\-]", "_", version or "unknown").replace("..", "_")
    return f"{safe}@{safe_version}"


def _read_package_json(pkg_dir: Path) -> dict:
    import json
    try:
        return json.loads((pkg_dir / "package.json").read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}


def _resolve_npm(source_root: Path, package: str) -> Optional[dict]:
    """Locate ``node_modules/<package>`` (scoped names ok, pnpm symlinks followed).

    The name must be an npm name, and the resolved directory (after symlinks,
    which is where pnpm and workspaces keep the real files) must stay inside
    the repo. A link to a directory outside the repo is not followed.
    """
    if not _valid_name(package, _NPM_NAME_RE):
        return None
    node_modules = source_root / "node_modules"
    candidate = node_modules / Path(*package.split("/"))
    if not candidate.is_dir():
        return None
    real = _contained(candidate, source_root)
    if real is None:
        return {"ecosystem": "npm", "error": (
            f"node_modules/{package} links to a directory outside the repo (an "
            "npm link, or a store outside the repo); only installs inside the "
            "repo are read."
        )}
    if _is_store(real, source_root, _store_roots(source_root)):
        return {"ecosystem": "npm", "error": f"node_modules/{package} {_STORE_REFUSAL}"}
    # An npm package is a directory with its own package.json. That property,
    # not a list of store names, is what refuses a scope, yarn's .store or any
    # other directory holding many packages.
    if not (real / "package.json").is_file():
        return {"ecosystem": "npm", "error": (
            f"node_modules/{package} has no package.json of its own, so it is not "
            "one npm package (a scope, a package-manager store, or an incomplete "
            "install); only a package directory is read."
        )}
    if not _metadata_in_repo(real, source_root, ("package.json",)):
        return {"ecosystem": "npm", "error": (
            f"node_modules/{package}/package.json links to a file outside the "
            "repo; only installs inside the repo are read."
        )}
    meta = _read_package_json(real)
    return {
        "ecosystem": "npm",
        "name": meta.get("name") or package,
        "version": str(meta.get("version") or ""),
        "installed_path": str(real),
        "resolved_from": str(candidate.relative_to(source_root)).replace("\\", "/"),
    }


def _site_packages_dirs(source_root: Path) -> list[Path]:
    """Repo-local virtualenv site-packages candidates, Windows + POSIX layouts."""
    found: list[Path] = []
    for venv_name in _VENV_DIR_CANDIDATES:
        venv = source_root / venv_name
        if not venv.is_dir():
            continue
        win = venv / "Lib" / "site-packages"
        if win.is_dir():
            found.append(win)
        for posix in glob(str(venv / "lib" / "python*" / "site-packages")):
            if Path(posix).is_dir():
                found.append(Path(posix))
    return found


def _read_metadata_fields(dist_info: Path) -> tuple[str, str, bool]:
    """(Name, Version, has_long_description) from a dist-info METADATA file."""
    name, version, has_body = "", "", False
    try:
        text = (dist_info / "METADATA").read_text(encoding="utf-8", errors="replace")
    except Exception:
        return name, version, has_body
    header, sep, body = text.partition("\n\n")
    has_body = bool(sep) and len(body.strip()) > 200
    for line in header.splitlines():
        if line.startswith("Name:") and not name:
            name = line[5:].strip()
        elif line.startswith("Version:") and not version:
            version = line[8:].strip()
        elif line.startswith("Description:"):
            has_body = True
    return name, version, has_body


def _top_levels(dist_info: Path) -> list[str]:
    """Importable names from top_level.txt. A line that is not a module name
    (a path, "..", anything with a separator) is dropped: the file comes from
    the installed package, and each name is joined onto site-packages."""
    try:
        lines = (dist_info / "top_level.txt").read_text(encoding="utf-8", errors="replace").splitlines()
        return [ln.strip() for ln in lines if _TOP_LEVEL_RE.fullmatch(ln.strip())]
    except Exception:
        return []


def _resolve_pypi(source_root: Path, package: str, looked_in: list) -> Optional[dict]:
    """Match a distribution (PEP-503 name) or import name in repo-local venvs.

    The name must be a PyPI name, and every directory it resolves to must be
    a package directory inside the repo: a link to elsewhere in the repo is
    followed, one out of it is not, and nothing that is or contains a store
    (``_is_store``) is read.
    """
    if not _valid_name(package, _PYPI_NAME_RE):
        return None
    want = _pep503_normalize(package)
    linked_out: list[str] = []
    roots = _store_roots(source_root)
    store_link: list[str] = []  # this site-packages only
    any_store_link: list[str] = []  # every site-packages, for the final report
    pkg_linked_out: list[str] = []  # package dirs or metadata that link out of the repo

    def linked_out_error() -> dict:
        return {"ecosystem": "pypi", "error": (
            f"{pkg_linked_out[0]} links to a location outside the repo; only "
            "installs inside the repo are read."
        )}

    def package_dir(path: Path) -> Optional[Path]:
        real = _contained(path, source_root)
        if real is None:
            if path.exists() or path.is_symlink():
                pkg_linked_out.append(path.relative_to(source_root).as_posix())
            return None
        if _is_store(real, source_root, roots):
            rel = path.relative_to(source_root).as_posix()
            store_link.append(rel)
            any_store_link.append(rel)
            return None
        return real

    for sp in _site_packages_dirs(source_root):
        looked_in.append(str(sp))
        store_link.clear()  # report a store link only for the site-packages it is in
        if _contained(sp, source_root) is None:
            linked_out.append(sp.relative_to(source_root).as_posix())
            continue
        # A dist-info (or its METADATA / top_level.txt) linked out of the repo
        # is not read: its name and version would name the snapshot.
        dist_infos = []
        dropped_dist_infos: list[str] = []  # in this site-packages, metadata outside the repo
        for p in glob(str(sp / "*.dist-info")):
            di = Path(p)
            if not di.is_dir():
                continue
            if _metadata_in_repo(di, source_root, ("METADATA", "top_level.txt")):
                dist_infos.append(di)
                continue
            dropped_dist_infos.append(di.relative_to(source_root).as_posix())
            if _pep503_normalize(di.name[: -len(".dist-info")].rsplit("-", 1)[0]) == want:
                # The matching distribution's metadata is outside the repo:
                # refuse rather than fall back to a version-less directory.
                pkg_linked_out.append(di.relative_to(source_root).as_posix())
        match: Optional[Path] = None
        for di in dist_infos:
            dist_name = di.name[: -len(".dist-info")].rsplit("-", 1)[0]
            if _pep503_normalize(dist_name) == want:
                match = di
                break
        if match is None:
            # Import-name query ("yaml" for PyYAML): scan top_level.txt files.
            for di in dist_infos:
                if package in _top_levels(di):
                    match = di
                    break
        if match is None and pkg_linked_out:
            return linked_out_error()
        if match is not None:
            name, version, readme_in_meta = _read_metadata_fields(match)
            tops = _top_levels(match)
            pkg_dir: Optional[Path] = None
            for top in ([package] + tops) if package in tops else (tops or [package]):
                # `top` is a validated module name, so the join cannot climb;
                # a link to elsewhere in the repo is followed, one out of it is not.
                before = len(pkg_linked_out)
                cand = package_dir(sp / top)
                if top == package and len(pkg_linked_out) > before:
                    return linked_out_error()  # the package asked for is the one linked out
                if cand is not None and cand.is_dir():
                    pkg_dir = cand
                    break
            if pkg_dir is None and store_link:
                return {"ecosystem": "pypi", "error": f"{store_link[0]} {_STORE_REFUSAL}"}
            if pkg_dir is None and pkg_linked_out:
                return linked_out_error()
            if pkg_dir is None:
                single = [
                    t for t in (tops or [package])
                    if (f := _contained(sp / f"{t}.py", source_root)) is not None
                    and f.is_file()
                ]
                if single:
                    return {"ecosystem": "pypi", "error": (
                        f"{name or package!r} is a single-module distribution "
                        f"({single[0]}.py); module-file dependencies are not "
                        "supported yet — read it directly instead."
                    )}
                return {"ecosystem": "pypi", "error": (
                    f"Matched distribution {match.name!r} but found no importable "
                    f"package directory (top_level: {tops or 'missing'})."
                )}
            return {
                "ecosystem": "pypi",
                "name": name or package,
                "version": version,
                "installed_path": str(pkg_dir),
                "resolved_from": str(sp.relative_to(source_root)).replace("\\", "/"),
                "readme_in_metadata": readme_in_meta,
                "additional_top_levels": [t for t in tops if t != pkg_dir.name],
            }
        # No dist-info matched; a bare package dir still counts, version unknown.
        # Not when a dist-info here was dropped for linking out: it may be this
        # package's (matched by import name), and serving it version-less would
        # hide that.
        # A dist-info dropped for linking out may be this package's (matched by
        # import name). Refuse when its in-repo top_level.txt says so; when it
        # cannot be read, serve the directory and name what was withheld,
        # rather than refuse an install that may be unrelated.
        withheld: list[str] = []
        if (sp / package).is_dir():
            for rel in dropped_dist_infos:
                di = source_root / rel
                top = di / "top_level.txt"
                if (_contained(di, source_root) is not None and top.is_file()
                        and _contained(top, source_root) is not None):
                    if package in _top_levels(di):
                        pkg_linked_out.append(rel)
                        return linked_out_error()
                    continue
                withheld.append(rel)
        bare = package_dir(sp / package)
        if bare is not None and bare.is_dir():
            resolved_bare = {
                "ecosystem": "pypi", "name": package, "version": "",
                "installed_path": str(bare),
                "resolved_from": str(sp.relative_to(source_root)).replace("\\", "/"),
                "readme_in_metadata": False,
                "additional_top_levels": [],
            }
            if withheld:
                # The version is unknown, not absent: metadata here links out of
                # the repo and was not read; it may or may not be this package's.
                resolved_bare["withheld_metadata"] = withheld[:_WITHHELD_LIST_CAP]
                resolved_bare["withheld_metadata_total"] = len(withheld)
                resolved_bare["withheld_metadata_truncated"] = len(withheld) > _WITHHELD_LIST_CAP
            return resolved_bare
    if any_store_link:
        return {"ecosystem": "pypi", "error": f"{any_store_link[0]} {_STORE_REFUSAL}"}
    if pkg_linked_out:
        return linked_out_error()
    if linked_out:
        return {"ecosystem": "pypi", "error": (
            f"{package!r} was not found, and {', '.join(linked_out)} links to a "
            "directory outside the repo (a virtualenv kept elsewhere); only "
            "installs inside the repo are read."
        )}
    return None


def _host_gitignore_root(source_root: Path, installed: Path) -> Optional[Path]:
    """The repo root whose .gitignore applies to a copy from ``installed``.

    Package files under node_modules or a repo-local virtualenv are ignored by
    convention and are what this tool exists to read, so they get no
    .gitignore. A directory elsewhere in the repo (a workspace package linked
    from node_modules) is ordinary repo content, and its gitignored files stay
    out of the snapshot as they stay out of the repo's own index.
    """
    root = source_root.resolve()
    try:
        real = installed.resolve()
        rel = real.relative_to(root).parts
    except (OSError, ValueError):
        return None
    # Exempt only the stores this tool reads: the repo's own top-level
    # node_modules, and the site-packages directories it found. A directory
    # that merely has one of those names (a workspace at env/, or a nested
    # tools/node_modules) is repo content and keeps the repo's .gitignore.
    if rel and rel[0] == "node_modules":
        return None
    for sp in _site_packages_dirs(source_root):
        if _contained(real, sp) is not None:
            return None
    return root


def _copy_snapshot(
    src: Path, dest: Path, max_files: int, repo: Optional[str] = None,
    gitignore_root: Optional[Path] = None,
) -> dict:
    """Copy code + doc files from the installed package into the snapshot dir.

    Returns {code_rels, docs, files_copied, truncated}. We control the
    contents, so ``dist``/``build`` code survives (indexed later via explicit
    ``paths=`` which bypasses the walker's dir skips).
    """
    code_rels: list[str] = []
    docs = {"readme": None, "changelog": None, "docs_dir": None}
    files_copied = 0
    truncated = False
    skipped: dict[str, int] = {}
    # The copy is a second way into the index store, so every file passes the
    # checks the indexer applies to a walked file before it is copied: no
    # secret or credential file, nothing over the size cap, nothing binary,
    # nothing that resolves outside the package.
    src_root = Path(os.path.realpath(src))
    max_size = get_max_file_size(None, repo=repo)
    from .index_folder import _gitignore_specs_on_path, _is_gitignored_fast

    chains: dict = {}

    for dirpath, dirnames, filenames in os.walk(str(src), followlinks=False):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_COPY_DIRS]
        rel_dir = os.path.relpath(dirpath, str(src))
        rel_dir = "" if rel_dir == "." else rel_dir.replace("\\", "/")
        for fname in sorted(filenames):
            rel = f"{rel_dir}/{fname}" if rel_dir else fname
            lower = fname.lower()
            if lower.endswith(_SKIP_COPY_SUFFIXES):
                continue
            is_doc = bool(_DOC_FILE_RE.match(fname)) or (
                rel_dir.split("/", 1)[0].lower() in ("docs", "doc")
                and lower.endswith((".md", ".mdx", ".rst", ".txt"))
            )
            is_code = (
                Path(fname).suffix in LANGUAGE_EXTENSIONS
                or get_language_for_path(fname) is not None
            )
            if not (is_doc or is_code):
                continue
            if is_code and len(code_rels) >= max_files:
                truncated = True
                continue
            src_file = Path(dirpath) / fname
            if src_file.is_symlink():
                skipped["symlink"] = skipped.get("symlink", 0) + 1
                continue
            reason = should_exclude_file(
                Path(os.path.realpath(src_file)), src_root, max_file_size=max_size,
            )
            if reason is None and is_binary_file(src_file):
                reason = "binary_content"
            if reason is None and gitignore_root is not None:
                real_file = Path(os.path.realpath(src_file))
                parent = real_file.parent
                if parent not in chains:
                    try:
                        chains[parent] = _gitignore_specs_on_path(gitignore_root, real_file)
                    except ValueError:
                        chains[parent] = []
                if chains[parent] and _is_gitignored_fast(str(real_file), chains[parent]):
                    reason = "gitignore"
            if reason is not None:
                skipped[reason] = skipped.get(reason, 0) + 1
                continue
            dest_file = dest / rel
            try:
                dest_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_file, dest_file)
            except OSError:
                logger.debug("snapshot copy failed for %s", src_file, exc_info=True)
                continue
            files_copied += 1
            if is_code:
                code_rels.append(rel)
            if _DOC_FILE_RE.match(fname):
                key = ("readme" if lower.startswith("readme")
                       else "changelog" if lower.startswith(("changelog", "history"))
                       else None)
                if key and docs.get(key) is None:
                    docs[key] = rel
            if rel_dir.split("/", 1)[0].lower() in ("docs", "doc") and docs["docs_dir"] is None:
                docs["docs_dir"] = rel_dir.split("/", 1)[0]
    return {"code_rels": code_rels, "docs": docs,
            "files_copied": files_copied, "truncated": truncated,
            "skipped": skipped}


def index_dependency(
    repo: str,
    package: str,
    ecosystem: str = "auto",
    max_files: int = _DEFAULT_MAX_FILES,
    storage_path: Optional[str] = None,
) -> dict:
    """Resolve an installed dependency of ``repo`` and index it locally.

    Args:
        repo:         Host repository identifier (must be locally indexed —
                      its source_root anchors the resolution).
        package:      npm package (``lodash``, ``@tanstack/react-query``) or
                      PyPI distribution/import name (``requests``, ``yaml``).
        ecosystem:    ``auto`` (node_modules first, then repo-local venvs),
                      ``npm``, or ``pypi``.
        max_files:    Cap on code files copied into the snapshot (default 2000;
                      truncation is reported, never silent).
        storage_path: Custom storage path.

    Returns:
        ``{package, resolved, snapshot_path, indexed_repo, symbol_count,
        file_count, docs, hint, _meta}`` — or an honest ``{error, looked_in}``
        when the package isn't installed where we can see it.
    """
    start = time.time()
    if ecosystem not in ("auto", "npm", "pypi"):
        return {"error": "ecosystem must be one of: auto, npm, pypi"}
    if not package or not isinstance(package, str):
        return {"error": "Provide 'package' (npm or PyPI name as installed)."}
    if not (_valid_name(package, _NPM_NAME_RE) or _valid_name(package, _PYPI_NAME_RE)):
        return {"error": (
            f"{package!r} is not a package name. Give an npm name (lodash, "
            "@scope/name) or a PyPI name (requests); paths are not accepted."
        )}

    try:
        owner, name = resolve_repo(repo, storage_path)
    except Exception as e:
        return {"error": str(e)}
    store = IndexStore(base_path=storage_path)
    index = store.load_index(owner, name)
    if index is None:
        return {"error": f"No index found for {repo!r}. Run index_folder first."}
    source_root = Path(getattr(index, "source_root", "") or "")
    if not source_root.is_dir():
        return {"error": (
            f"{repo!r} has no local source_root; dependency resolution needs a "
            "locally indexed repo (index_folder)."
        )}

    looked_in: list = []
    resolved: Optional[dict] = None
    if ecosystem in ("auto", "npm"):
        looked_in.append(str(source_root / "node_modules" / package))
        resolved = _resolve_npm(source_root, package)
    if resolved is None and ecosystem in ("auto", "pypi"):
        resolved = _resolve_pypi(source_root, package, looked_in)
    if resolved is None:
        return {
            "error": (
                f"{package!r} is not installed where I can see it. Only "
                "repo-local installs are resolved (node_modules, or a "
                ".venv/venv/env virtualenv under the repo root)."
            ),
            "looked_in": looked_in,
        }
    if "error" in resolved:
        return {"error": resolved["error"], "looked_in": looked_in}

    snapshot_root = store.base_path / "deps"
    snapshot_name = _safe_snapshot_name(resolved["name"], resolved["version"])
    snapshot = resolve_within(snapshot_root, snapshot_name)
    # The snapshot must be one directory strictly inside deps/: both the
    # rebuild below and the copy write there.
    if snapshot is None or snapshot.parent != snapshot_root.resolve():
        return {"error": f"Cannot place a snapshot named {snapshot_name!r}.", "resolved": resolved}
    cache_hit = snapshot.is_dir()
    if cache_hit:
        # Rebuild rather than add to it: a snapshot written by an older
        # version may hold files today's copy filters would not copy.
        shutil.rmtree(snapshot, ignore_errors=True)
        if snapshot.exists():
            return {"error": (
                f"Could not clear the earlier snapshot at {snapshot}; remove it "
                "and run index_dependency again."
            ), "resolved": resolved}
    snapshot.mkdir(parents=True, exist_ok=True)
    installed = Path(resolved["installed_path"])
    copied = _copy_snapshot(
        installed, snapshot, max_files, repo=str(source_root),
        gitignore_root=_host_gitignore_root(source_root, installed),
    )
    if not copied["code_rels"]:
        return {
            "error": (
                f"Resolved {resolved['name']}@{resolved['version'] or '?'} at "
                f"{resolved['installed_path']} but found no indexable code files."
            ),
            "resolved": resolved,
            **({"skipped_on_copy": copied["skipped"]} if copied.get("skipped") else {}),
        }

    result = index_folder(
        str(snapshot),
        use_ai_summaries=False,
        storage_path=storage_path,
        paths=copied["code_rels"],
        # The snapshot is our own copy, already filtered file by file above;
        # a package's .gitignore (often listing dist/) must not drop its code.
        paths_honor_gitignore=False,
    )
    if not (isinstance(result, dict) and result.get("success")):
        return {"error": f"Indexing the snapshot failed: {result}", "resolved": resolved}

    docs = dict(copied["docs"])
    if resolved.get("readme_in_metadata") is not None:
        docs["readme_in_metadata"] = resolved.pop("readme_in_metadata")
    notes: list = []
    if not docs.get("readme") and not docs.get("readme_in_metadata"):
        notes.append(_THIN_DOCS_NOTE)
    if copied["truncated"]:
        notes.append(f"Snapshot capped at max_files={max_files}; some code files were skipped.")
    extra_tops = resolved.pop("additional_top_levels", None)
    if extra_tops:
        notes.append(f"Distribution ships additional top-level packages not indexed: {extra_tops}.")

    dep_repo = result.get("repo", "")
    symbol_count = result.get("symbol_count")
    file_count = result.get("file_count")
    if symbol_count is None or file_count is None:
        # Incremental no-change responses omit counts; read them from the store.
        try:
            d_owner, d_name = dep_repo.split("/", 1)
            dep_index = store.load_index(d_owner, d_name)
            symbol_count = len(getattr(dep_index, "symbols", []) or [])
            file_count = len(getattr(dep_index, "source_files", {}) or {})
        except Exception:
            symbol_count = symbol_count or 0
            file_count = file_count or len(copied["code_rels"])
    response = {
        "package": package,
        "resolved": resolved,
        "snapshot_path": str(snapshot),
        "indexed_repo": dep_repo,
        "symbol_count": symbol_count,
        "file_count": file_count,
        "docs": docs,
        "hint": (
            f"Query it like any repo: search_symbols(repo={dep_repo!r}, ...) / "
            f"get_symbol_source / get_file_outline. Markdown docs in the "
            f"snapshot can be indexed with jdocmunch index_local."
        ),
        "_meta": {
            "timing_ms": round((time.time() - start) * 1000, 1),
            "cache_hit": cache_hit,
            "files_copied": copied["files_copied"],
            "truncated": copied["truncated"],
        },
    }
    if copied.get("skipped"):
        # In the body, not _meta: _meta is stripped on a default install, and
        # what the copy withheld is part of the answer.
        response["skipped_on_copy"] = copied["skipped"]
    if notes:
        response["_meta"]["notes"] = notes
    return response
