"""Shared helpers for tool modules."""

import logging
import threading
from pathlib import Path
from typing import Optional

from ..storage import IndexStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Bare-name resolution cache (P5)
# ---------------------------------------------------------------------------
# Keyed by storage base_path string.
# Value: (dir_mtime: float, mapping: dict[bare_name -> sorted list of owner/name])
# Invalidated whenever the base_path directory mtime changes (repo added/removed).
# ---------------------------------------------------------------------------
_bare_name_cache: dict[str, tuple[float, dict[str, list[str]]]] = {}
_BARE_NAME_LOCK = threading.Lock()


def ledger_base_path(store) -> "Optional[str]":
    """The storage root a ranking-ledger row belongs to (v1.108.188).

    Every reader of ``ranking_events`` takes a base path — ``ranking_db_query``,
    ``WeightTuner``, ``analyze_perf`` — but the writers passed none, so rows landed
    in ``~/.code-index`` whatever ``storage_path`` the tool was handed. A search
    against a non-default store therefore wrote to one database and read from
    another. Returns ``None`` when the store cannot say, which restores the previous
    default rather than dropping the row.
    """
    try:
        base = getattr(store, "base_path", None)
        return str(base) if base is not None else None
    except Exception:  # pragma: no cover - a store that cannot name itself
        return None


def _get_bare_name_map(store: IndexStore) -> dict[str, list[str]]:
    """Return a cached bare-name → [owner/name] mapping for the store's base_path.

    Rebuilds when the directory mtime changes (repo indexed or cache invalidated).
    Cost when warm: one stat() call instead of N db reads.
    """
    path_str = str(store.base_path)
    try:
        mtime = store.base_path.stat().st_mtime
    except OSError:
        mtime = 0.0

    with _BARE_NAME_LOCK:
        cached = _bare_name_cache.get(path_str)
        if cached and cached[0] == mtime:
            return cached[1]

    # Miss: rebuild without holding the lock (list_repos does I/O)
    mapping: dict[str, list[str]] = {}
    for repo_entry in store.list_repos():
        owner_name = repo_entry["repo"]
        if not owner_name or "/" not in owner_name:
            continue
        _, repo_name = owner_name.split("/", 1)
        for key in (repo_name, repo_entry.get("display_name")):
            if key:
                mapping.setdefault(key, []).append(owner_name)

    # Deduplicate and sort so output is deterministic
    mapping = {k: sorted(set(v)) for k, v in mapping.items()}
    with _BARE_NAME_LOCK:
        _bare_name_cache[path_str] = (mtime, mapping)
    return mapping


def _looks_like_path(repo: str) -> bool:
    """Path-shaped repo arg — agents retry with repo='.' or a filesystem path
    when an owner/name id isn't at hand (observed in the 2026-07-22 bench run).
    Deliberately conservative: bare names and owner/name ids never match."""
    if repo in (".", "..") or repo.startswith(("./", ".\\", "../", "..\\", "~")):
        return True
    if "\\" in repo or repo.startswith("/"):
        return True
    # Windows drive prefix (C:/x) — checked explicitly so behavior is
    # platform-independent (PurePosixPath("C:/x").is_absolute() is False).
    if len(repo) >= 3 and repo[0].isalpha() and repo[1] == ":" and repo[2] in "/\\":
        return True
    try:
        return Path(repo).is_absolute()
    except (OSError, ValueError):
        return False


def _path_shaped_repo_error(repo: str) -> ValueError:
    """Actionable error for a `repo` arg that is a path, not a repository id (#376).

    Agents that don't have an owner/name id at hand guess a file or directory path.
    Name the two calls that produce a real id, and — when the arg looks like a file —
    the `file_pattern` arg they most likely wanted instead of a hard failure.
    """
    message = (
        f"'{repo}' is a path, not a repository id. "
        "Call resolve_repo(path) to get the repo id for a checkout, "
        "or index_folder(path) to index it."
    )
    if Path(repo).suffix:
        message += (
            " To scope a search to one file, pass the repo id as 'repo' and the "
            f"path as 'file_pattern' (file_pattern='{repo}')."
        )
    return ValueError(message)


def _resolve_path_repo(repo: str, storage_path: Optional[str]) -> tuple[str, str]:
    """Map a filesystem path (repo='.', an absolute path) to its indexed repo id."""
    from .resolve_repo import _compute_repo_id  # noqa: PLC0415

    store = IndexStore(base_path=storage_path)
    resolved = Path(repo).expanduser().resolve()
    try:
        repo_id = _compute_repo_id(resolved, store)
    except Exception:
        logger.debug("Path→repo identity probe failed for %s", repo, exc_info=True)
        repo_id = ""
    if repo_id and "/" in repo_id:
        owner, name = repo_id.split("/", 1)
        if store.inspect_index(owner, name).index_present:
            return owner, name
    # Fallback: match the resolved path against indexed source roots
    for entry in store.list_repos():
        root = entry.get("source_root")
        if root and "/" in entry.get("repo", ""):
            try:
                if Path(root).resolve() == resolved:
                    return entry["repo"].split("/", 1)
            except OSError:
                continue
    raise _path_shaped_repo_error(repo)


def resolve_repo(repo: str, storage_path: Optional[str] = None) -> tuple[str, str]:
    """Resolve an indexed repository id or unique bare display/name.

    Also accepts a filesystem path ('.', './sub', an absolute path) and maps it
    to the indexed repo for that checkout (v1.108.159).

    Raises ValueError if the repo is not found or the bare name is ambiguous.
    """
    if _looks_like_path(repo):
        return _resolve_path_repo(repo, storage_path)

    if "/" in repo:
        owner, name = repo.split("/", 1)
        # A second separator means this was never an owner/name id — the store
        # rejects separators in `name` at write time, so such a pair can only
        # blow up in the storage layer, outside every caller's handler (#376).
        # Treat it as a path: a bare relative path like 'src/auth' is exactly
        # what _looks_like_path is too conservative to catch.
        if "/" in name or "\\" in name:
            try:
                return _resolve_path_repo(repo, storage_path)
            except ValueError:
                raise _path_shaped_repo_error(repo) from None
        return owner, name

    store = IndexStore(base_path=storage_path)
    mapping = _get_bare_name_map(store)
    candidates = mapping.get(repo, [])
    if len(candidates) > 1:
        from ._visibility import prefer_workspace  # noqa: PLC0415

        candidates = prefer_workspace(candidates, store.list_repos())

    if not candidates:
        raise ValueError(f"Repository not found: {repo}")
    if len(candidates) > 1:
        raise ValueError(
            f"Ambiguous repository name: {repo}. Use one of: {', '.join(candidates)}"
        )

    return candidates[0].split("/", 1)


def index_status_to_tool_error(status) -> dict:
    """Convert an index status probe into a consistent tool error."""
    hint = status.hint or "Re-index this repository to rebuild the index."
    return {
        "error": f"Repository index is not loadable: {status.repo}",
        "repo": status.repo,
        "index_present": status.index_present,
        "loadable": status.loadable,
        "status": status.status,
        "load_error": status.load_error or status.status,
        "hint": hint,
    }


def load_repo_index_or_error(
    repo: str,
    storage_path: Optional[str] = None,
    branch: str = "",
) -> tuple[Optional[object], Optional[dict], Optional[object]]:
    """Resolve and load a repo index, returning a structured error on failure."""
    try:
        owner, name = resolve_repo(repo, storage_path)
    except ValueError as e:
        return None, {"error": str(e)}, None

    store = IndexStore(base_path=storage_path)
    index = store.load_index(owner, name, branch=branch)
    if index is not None:
        return index, None, None

    status = store.inspect_index(owner, name, branch=branch)
    return None, index_status_to_tool_error(status), status


#: Recorded when an index would not load but a follow-up inspection could not
#: establish why. Unknown is a THIRD answer — it must never be reported as one
#: of the named causes, and a caller branching on `rebuild_reason` needs to be
#: able to tell "we know it was a future version" from "we could not find out".
UNLOADABLE_REASON_UNKNOWN = "unloadable_unknown"


def describe_unloadable_index(store, owner: str, name: str) -> tuple[str, str]:
    """Name the cause of an unreadable on-disk index, and the remedy for it.

    ``load_index`` collapses seven distinct failures into ``None`` — absent
    file, two delete-during-load races, empty ``meta``, unparseable
    ``index_version``, future ``index_version``, and a corrupt database. The
    write side used to report every one of them as "created by a newer version
    of jcodemunch-mcp" and tell the user to delete their whole index
    directory, which is the wrong cause and a considerably larger remedy than
    six of the seven call for (#413, @LuigiNicaPRO). ``inspect_index``
    (PR #291) already discriminates them for the read side; this puts the same
    discriminator on the write side instead of a second copy of the checks.

    Returns ``(reason, message)`` — ``reason`` is the machine-readable status a
    caller can branch on, ``message`` the prose warning.
    """
    status = None
    try:
        status = store.inspect_index(owner, name)
    except Exception:
        logger.debug("inspect_index failed for %s/%s", owner, name, exc_info=True)

    if status is None or status.loadable:
        # inspect_index disagrees with load_index. That is what a transient
        # failure looks like from here (the file was removed mid-load, or the
        # load lost a race with a concurrent writer), and we must not invent a
        # cause for it.
        return (
            UNLOADABLE_REASON_UNKNOWN,
            "Existing index could not be read, and a follow-up inspection could "
            "not establish why — performing a full re-index.",
        )

    reason = status.load_error or status.status or UNLOADABLE_REASON_UNKNOWN
    hint = status.hint or "Re-index this repository to rebuild the index."
    message = (
        f"Existing index could not be read ({reason}) — performing a full "
        f"re-index. {hint}"
    )
    if reason == "sqlite_future_version":
        # The ONE cause that really is a downgrade keeps saying so — and keeps
        # the remedy, narrowed from "delete every index you have" to this one.
        message += (
            " It was created by a newer version of jcodemunch-mcp; if you "
            "downgraded the package, delete this repository's index under "
            "~/.code-index/ (or your CODE_INDEX_PATH directory) to remove the "
            "stale index."
        )
    return reason, message


#: How many oversize paths ``size_cap_warning`` names before it stops and
#: reports a remainder. A repo full of generated clients would otherwise turn
#: one warning into a wall of them, which is its own kind of silence.
SIZE_CAP_WARNING_SAMPLE = 5


def size_cap_warning(dropped: list, max_size: int) -> "Optional[str]":
    """One aggregate warning naming files the per-file size cap withheld (#429).

    ``too_large`` is a WITHHELD reason, not an ordinary exclusion: the file is
    real, current and wanted, and only OUR limit kept it out. Before this the
    local walk recorded it as a ``coverage.excluded.too_large`` counter and
    said nothing else, so the drop surfaced only when some later query happened
    to ask about absence and got refused; the GitHub walk did not even count
    it. An index that quietly missed the largest file in the tree read as
    healthy on both.

    ⚠ ``resolve_explicit_paths`` already warned per entry. Same limit, same
    repo, and whether you heard about it depended on whether you passed
    ``paths=`` — the inconsistency is the defect, not the wording.

    Lives here, not beside either caller, because both discovery paths need it
    and a hazard fixed on one path is not fixed.

    Returns None when nothing was withheld, so a clean index is unchanged.
    """
    if not dropped:
        return None
    ordered = sorted(dropped)
    shown = ", ".join(ordered[:SIZE_CAP_WARNING_SAMPLE])
    remainder = len(ordered) - SIZE_CAP_WARNING_SAMPLE
    if remainder > 0:
        shown += f", and {remainder} more"
    return (
        f"Size cap reached: {len(ordered)} file(s) over max_file_size="
        f"{max_size} bytes are MISSING from the index ({shown}). Symbols "
        f"defined in them cannot be found, and absence cannot be proven for "
        f"anything they contain. Pass max_size on this call for a one-off, or "
        f"raise max_file_size in config (the project's .jcodemunch.jsonc, or "
        f"the global config.jsonc) / set JCODEMUNCH_MAX_FILE_SIZE to make it "
        f"stick, then re-index."
    )


#: Warning text for the one-off re-parse an extraction-semantics bump forces.
PARSER_UPGRADE_WARNING = (
    "This index's symbols were extracted by an older version of the parser "
    "whose output is no longer trusted - re-parsing every file once. A normal "
    "incremental run cannot repair it: the affected files are unchanged, so "
    "they would never be re-read."
)


def needs_parser_upgrade(index) -> bool:
    """True when this index's symbols predate the current extraction semantics.

    A missing stamp reads as generation 0, so every pre-1.108.244 index takes
    the upgrade exactly once (#414). Fails toward re-parsing: this decides
    whether to spend one full walk, and the cost of a needless one is time,
    while the cost of a skipped one is symbols that stay wrong forever.
    """
    if index is None:
        return False
    from ..storage.index_store import PARSER_GENERATION

    return getattr(index, "parser_generation", 0) < PARSER_GENERATION


def racket_reparse_reason(index) -> Optional[str]:
    """Why a local index holding Racket files must be re-parsed in full, or None.

    Three reasons, told apart because a caller reads `rebuild_reason`:

    - ``racket_index_predates_gate``: the index carries NO
      `racket_config_digest` stamp, so it was built before the Racket
      extraction changes of 2026-08-27 (the `#lang` gate above all, which
      REMOVES fabricated symbols a 1.108.297-.301 index still holds). This is
      the narrow substitute for a `PARSER_GENERATION` bump: it reaches exactly
      the indexes that hold Racket files instead of every language for
      everybody, and an absent key stays detectable at any later date.
    - ``racket_reader_changed``: the index's `racket_reader_generation` is
      not the current `READER_GENERATION` -- absent means its `.rkt` files
      were parsed by tree-sitter, before the Python reader. Same substitute
      for a generation bump, for the reader instead of the gate.
    - ``racket_config_changed``: `racket_definition_forms` / `racket_langs`
      differ from the stamp. They change what the parser emits for UNCHANGED
      content, which the incremental path never re-reads, so a declaration
      added after the index was built applied to nothing (measured:
      `check-admin ABSENT` across an incremental reindex, present only after a
      full one).

    Same shape as `needs_parser_upgrade`, scoped to one project. A remote
    index has no source root and never qualifies.
    """
    if index is None:
        return None
    if "racket" not in (getattr(index, "languages", None) or {}):
        return None
    source_root = getattr(index, "source_root", "") or ""
    if not source_root:
        return None
    stamp = getattr(index, "racket_config_digest", None)
    if stamp is None:
        return "racket_index_predates_gate"
    from ..parser.racket_reader import READER_GENERATION
    if getattr(index, "racket_reader_generation", None) != READER_GENERATION:
        return "racket_reader_changed"
    from .. import config as _config

    if _config.racket_config_digest(source_root) != stamp:
        return "racket_config_changed"
    return None


def stamp_incremental_outcome(
    result: dict,
    requested: bool,
    performed: bool,
    rebuild_reason: Optional[str] = None,
) -> None:
    """Record requested-vs-performed indexing mode on a result dict (#413).

    A requested incremental can be replaced by a full rebuild — an unreadable
    on-disk index, a forced invalidation, or simply no index to diff against.
    Before this, the only signal was prose inside ``warnings[]``, and the
    rebuild re-stamped ``meta.index_version`` on its way out, so a caller that
    did not capture the warning at the moment of the call could not learn
    afterwards that the substitution had happened at all.

    ``rebuild_reason`` is attached only when an unreadable index forced the
    rebuild; its absence is not a claim that no substitution occurred — read
    ``performed_incremental`` for that.
    """
    if not isinstance(result, dict):
        return
    result["requested_incremental"] = requested
    result["performed_incremental"] = performed
    if rebuild_reason:
        result["rebuild_reason"] = rebuild_reason


def resolve_fqn(
    repo: str, fqn: str, storage_path: Optional[str] = None
) -> tuple[Optional[str], Optional[str]]:
    """Resolve a PHP FQN to a jcodemunch symbol_id.

    Returns ``(symbol_id, None)`` on success or ``(None, error_message)`` on failure.
    """
    from ..parser.fqn import fqn_to_symbol
    from ..parser.imports import build_psr4_map

    try:
        owner, name = resolve_repo(repo, storage_path)
    except ValueError as e:
        return None, f"Repository not found: {e}"
    store = IndexStore(base_path=storage_path)
    index = store.load_index(owner, name)
    if not index:
        status = store.inspect_index(owner, name)
        err = index_status_to_tool_error(status)
        return None, f"{err['error']} ({err['load_error']}). {err['hint']}"
    if not getattr(index, "source_root", None):
        return None, "Index has no source_root (remote indexes don't support FQN resolution)"
    psr4 = build_psr4_map(index.source_root)
    if not psr4:
        return None, "No PSR-4 autoload config found in composer.json"
    resolved = fqn_to_symbol(fqn, psr4, frozenset(index.source_files))
    if not resolved:
        return None, f"FQN '{fqn}' could not be resolved. File not in index or namespace mismatch."
    return resolved, None


def file_byte_mass(symbols) -> tuple[dict[str, int], list[str]]:
    """Per-file source bytes the symbol table actually spans, counting overlap once.

    Summing ``byte_length`` over every symbol in a file DOUBLE-COUNTS nesting: a
    class's span already covers its methods, and each method is a symbol of its
    own. The inflation is not uniform — it tracks how class-heavy a file is — so
    any cross-file comparison built on the naive sum (a Gini coefficient, a
    concentration ranking, a corpus-wide savings baseline) is biased, not merely
    scaled. Measured on this repo at 33.4% overall and up to 2.28x on a single
    file. Merging the spans is the fix: a byte covered by both a class and its
    method is one byte of source.

    Returns ``(mass_by_file, unmeasurable_files)``. A file lands in the second
    list when its spans cannot be trusted — more than one symbol carrying real
    length at ``byte_offset == 0``, which is the signature of a parser that never
    set offsets (the field defaults to 0, so "unset" and "starts at byte 0" are
    indistinguishable for a single symbol but not for two). Those files are
    UNKNOWN, never 0: a caller that folds them in as zero mass reports a
    confident wrong answer, and one that folds in their naive sum reports the
    defect this exists to remove. They are absent from ``mass_by_file``.
    """
    spans: dict[str, list[tuple[int, int]]] = {}
    zero_offset: dict[str, int] = {}
    for sym in symbols:
        f = (sym.get("file", "") or "").replace("\\", "/")
        if not f:
            continue
        length = int(sym.get("byte_length", 0) or 0)
        offset = int(sym.get("byte_offset", 0) or 0)
        spans.setdefault(f, [])
        if length <= 0 or offset < 0:
            continue
        if offset == 0:
            zero_offset[f] = zero_offset.get(f, 0) + 1
        spans[f].append((offset, offset + length))

    mass: dict[str, int] = {}
    unmeasurable: list[str] = []
    for f, intervals in spans.items():
        if zero_offset.get(f, 0) > 1:
            unmeasurable.append(f)
            continue
        total = 0
        start = end = None
        for lo, hi in sorted(intervals):
            if start is None:
                start, end = lo, hi
            elif lo <= end:
                end = max(end, hi)
            else:
                total += end - start
                start, end = lo, hi
        if start is not None:
            total += end - start
        mass[f] = total
    return mass, sorted(unmeasurable)


def symbol_span_bytes(symbols) -> int:
    """Bytes of distinct source a symbol collection covers — the savings baseline.

    ``sum(byte_length)`` over a symbol collection is NOT this number. A class's
    span already covers its methods, so the naive sum counts those bytes once per
    level of nesting and lands at 2.85x the real size of the files it describes
    on this repo. A savings baseline is a claim about what someone would
    otherwise have READ, and nobody reads a method twice for being inside a
    class.

    Deliberately CONSERVATIVE in two ways, both understating rather than
    overstating a number we publish about ourselves: bytes outside every symbol
    span (imports, comments, module docstrings, data literals) are not counted,
    and a file whose spans cannot be trusted contributes 0 instead of a guess.
    """
    mass, _ = file_byte_mass(symbols)
    return sum(mass.values())


def distinct_file_bytes(file_sizes: dict, symbols) -> int:
    """Bytes of the distinct FILES a symbol collection touches.

    Looking `file_sizes` up per symbol charges a file once per symbol selected
    from it — 84.89x over this repo's symbol table. Reading a file once yields
    all of its symbols, so the file is the unit.
    """
    files = {(s.get("file", "") or "").replace("\\", "/") for s in symbols}
    normalised = {k.replace("\\", "/"): v for k, v in (file_sizes or {}).items()}
    return sum(int(normalised.get(f, 0) or 0) for f in files if f)
