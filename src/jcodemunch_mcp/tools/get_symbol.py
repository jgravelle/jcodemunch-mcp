"""Get symbol source code."""

import hashlib
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Optional

from ..retrieval.verdict import suggest_symbol_ids, symbol_not_found, symbol_verdict_for_index
from ..storage import IndexStore, record_savings, estimate_savings, cost_avoided as _cost_avoided
from ._utils import index_status_to_tool_error, resolve_repo, resolve_fqn

logger = logging.getLogger(__name__)


def _offload():
    """The optional offloadable-work annotator, or None.

    ⚠ Imported lazily and allowed to be ABSENT. The module is optional and is
    not present in every build, so a missing import must degrade to "no
    annotation" rather than breaking symbol retrieval outright. Off by default
    regardless: the module's own env gate decides whether anything is emitted.
    """
    try:
        from ..retrieval import offload

        return offload
    except ImportError:
        return None


def _make_meta(timing_ms: float, **kwargs) -> dict:
    """Build a _meta envelope dict."""
    meta = {"timing_ms": round(timing_ms, 1)}
    meta.update(kwargs)
    return meta


def _utf8_safe_truncate(text: str, max_bytes: int) -> str:
    """Truncate ``text`` to at most ``max_bytes`` UTF-8 bytes without splitting a
    multibyte character (a trailing partial sequence is dropped)."""
    if max_bytes <= 0:
        return ""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _bound_source(
    source: str,
    symbol_line: int,
    symbol_end_line: int,
    source_start_line: Optional[int],
    source_end_line: Optional[int],
    max_source_lines: Optional[int],
    max_source_bytes: Optional[int],
    remaining_total_bytes: Optional[int],
) -> dict:
    """Shape one symbol's full body into a bounded slice + truncation metadata.

    Identity and verification still refer to the full indexed body; this only
    shapes the returned ``source`` and authors the server-side truncation
    contract. All line numbers are absolute file lines (matching ``line`` /
    ``end_line`` and ``get_file_content(start_line=, end_line=)``).

    Bounds apply in order — explicit line range (clamped to the symbol body) →
    ``max_source_lines`` → ``max_source_bytes`` → batch ``remaining_total_bytes``
    — so a later, tighter bound supersedes the reason of an earlier one.

    Returns ``{text, truncated, reason, range, total_range, total_lines,
    total_bytes}``.
    """
    full_lines = source.split("\n")
    total_lines = len(full_lines)
    total_bytes = len(source.encode("utf-8"))
    total_range = {"start_line": symbol_line, "end_line": symbol_end_line}

    # 1) Explicit absolute line range, clamped to the symbol body.
    rel_start = 0
    rel_end = total_lines  # exclusive
    reason = None
    if source_start_line is not None:
        rel_start = max(0, min(source_start_line - symbol_line, total_lines))
        if rel_start > 0:
            reason = "source_range"
    if source_end_line is not None:
        rel_end = max(rel_start, min(source_end_line - symbol_line + 1, total_lines))
        if rel_end < total_lines:
            reason = "source_range"
    sliced_lines = full_lines[rel_start:rel_end]
    start_abs = symbol_line + rel_start

    # 2) Max line cap on the (possibly range-limited) slice.
    if max_source_lines is not None and len(sliced_lines) > max_source_lines:
        sliced_lines = sliced_lines[:max_source_lines]
        reason = "max_source_lines"

    text = "\n".join(sliced_lines)

    # 3) Per-symbol byte cap (UTF-8 safe).
    if max_source_bytes is not None and len(text.encode("utf-8")) > max_source_bytes:
        text = _utf8_safe_truncate(text, max_source_bytes)
        reason = "max_source_bytes"

    # 4) Batch total-byte cap (caller-supplied running budget; overrides).
    if remaining_total_bytes is not None and len(text.encode("utf-8")) > remaining_total_bytes:
        text = _utf8_safe_truncate(text, remaining_total_bytes)
        reason = "max_total_source_bytes"

    truncated = text != source
    # Returned absolute range: the last line may be byte-truncated but is still
    # partially present, so it counts. An empty slice returns an empty range.
    end_abs = (start_abs + text.count("\n")) if text else (start_abs - 1)
    return {
        "text": text,
        "truncated": truncated,
        "reason": reason if truncated else None,
        "range": {"start_line": start_abs, "end_line": end_abs},
        "total_range": total_range,
        "total_lines": total_lines,
        "total_bytes": total_bytes,
    }


def _normalize_eol(text: str) -> str:
    """Collapse CRLF/CR to LF.

    ⚠ Applied to BOTH sides of the comparison, never one. Normalising a single
    side is the v1.108.223-and-earlier bug (#400): git's output was newline-
    translated by ``text=True`` while the cached side, read as raw bytes, was
    not — so a Windows clone under the installer-default ``core.autocrlf=true``
    reported every symbol in every file as diverged.
    """
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _verify_against_git_sha(
    cached_source: str,
    source_root: Optional[str],
    file_path: str,
    line: int,
    end_line: int,
    rev: Optional[str] = None,
) -> tuple[str, str]:
    """Compare cached source against the git content it was BUILT FROM (P1.6).

    Returns ``(status, rev_used)``. Statuses:
    - ``"git_sha_match"``       — the cached source matches the slice of the
                                   same file at ``rev`` (lines line..end_line).
    - ``"git_sha_mismatch"``    — the file exists at ``rev`` and the cache
                                   matches neither it nor the working tree.
                                   This is the genuine cache-divergence answer.
    - ``"git_sha_uncommitted"`` — the cache does not match ``rev`` but DOES
                                   match the working tree. Nothing is wrong with
                                   the cache; the checkout is simply ahead of
                                   the commit. See below.
    - ``"git_unavailable"``     — source_root unknown, file isn't tracked, or
                                   git is unreachable from this env.

    This is an externally-attested verification mode: the comparison target
    comes from git, not from the same cache the symbol's content_hash was
    derived from. The default ``verify_against="cache"`` mode is self-referential
    and only catches incoherent tamper of ``~/.code-index/<repo>/``; this mode
    catches divergence between the cache and the upstream source.

    ⚠⚠ **`rev` defaults to the commit the INDEX RECORDED, not live HEAD**
    (v1.108.227, @rknighton #402). The index is built at one moment and verified
    at another; comparing against live HEAD answers "did the working tree at
    index time happen to equal HEAD at verification time", which is a different
    question and is routinely false during normal development. The caller passes
    ``index.git_head``; ``HEAD`` remains the fallback when the index recorded no
    revision. **``rev_used`` is returned so a verdict can never be silently
    about a different commit than the reader assumes** — including when the
    recorded revision has been rebased or force-pushed away and this falls back
    to ``HEAD``.

    ⚠⚠ **A revision alone does NOT fix the reported case, which is why
    `git_sha_uncommitted` exists.** Measured on the #402 reproduction: a tree
    that was DIRTY at index time has ``index.git_head == live HEAD``, so passing
    the recorded revision changes nothing — no commit anywhere contains those
    bytes, because the bytes were never committed. Reporting that as
    ``git_sha_mismatch`` tells an operator their cache diverged when the cache
    is a byte-exact record of what it indexed, and it is indistinguishable from
    real corruption. The two cases are separated by asking a second question the
    caller already gave us everything to ask: does the cache match the WORKING
    TREE? If it does, the cache is faithful and the commit is simply behind.

    ⚠⚠ **The two sides are built differently and must be reconciled before they
    are compared.** Both halves of that were wrong until v1.108.224
    (@rknighton, #400 and #401), and between them they made the mode report
    failure for most of what it was asked about:

    * The cached side is a **byte range** starting at the declaration's first
      token (``parser/extractor.py`` records ``start_node.start_byte``), read
      back verbatim by ``sqlite_store.get_symbol_content`` and decoded as UTF-8
      with ``errors="replace"``. It therefore carries **no leading indentation
      on its first line** and stops at the symbol's last byte.
    * This side is a **line range**. So the slice is realigned to the cached
      extent below rather than either side being loosened.

    Two costs are accepted deliberately, and each is pinned by a test:

    1. **Line endings are normalised on both sides**, so a change that is only
       a line ending no longer reads as divergence. Required, not optional: a
       Windows clone's working tree is CRLF while its blob is LF and nothing is
       wrong (#400 situation 2).
    2. **Tokens outside the symbol are ignored** — a trailing comment after a
       method's closing brace is not part of the symbol. Cost: edits confined to
       that trailing text are invisible here. Interior indentation is still
       compared byte for byte, which is what keeps this from degrading into a
       whitespace-insensitive compare.
    """
    rev_used = (rev or "").strip() or "HEAD"
    if not source_root or not file_path:
        return "git_unavailable", rev_used
    root = Path(source_root)
    if not (root / ".git").exists() and not (root / ".git").is_file():
        # Not a git working tree (or worktree pointing elsewhere; bail rather
        # than guess).
        return "git_unavailable", rev_used

    def _show(revision: str):
        return subprocess.run(
            ["git", "-C", str(root), "show", f"{revision}:{file_path}"],
            capture_output=True,
            # ⚠ NO `text=True` (#400). It does three things that all work
            # against a byte-fidelity comparison: universal-newline translation
            # (so CRLF is silently rewritten on this side only), a decode with
            # `locale.getpreferredencoding(False)` rather than UTF-8 (cp1252 on
            # a default Windows box, which turns every non-ASCII symbol into a
            # false mismatch), and — worst — it performs that decode on a
            # `subprocess` reader THREAD. A UnicodeDecodeError there cannot
            # reach the `except` below at all: the thread dies, Python prints
            # the traceback itself, `stdout` comes back unset, and the empty
            # check further down converts a decoding problem into
            # `git_unavailable`. That sends an operator to check whether git is
            # installed when git was never the problem.
            timeout=10,
            check=False,
            # Windows stdio-MCP deadlock guard: never inherit the JSON-RPC pipe
            # as the git child's stdin (Git-for-Windows' cmd\git.exe wrapper
            # blocks forever holding the handle, even for commands that don't
            # read stdin). Mirrors the redirect across the other git spawns.
            stdin=subprocess.DEVNULL,
        )

    try:
        result = _show(rev_used)
        if result.returncode != 0 and rev_used != "HEAD":
            # ⚠ The recorded revision can be gone — rebased away, force-pushed
            # over, or absent from a shallow clone. Failing there would make
            # this mode WORSE than before for those repositories, so fall back
            # to HEAD; `rev_used` comes back naming what was actually compared,
            # which is the whole reason it is returned rather than assumed.
            logger.debug(
                "git_sha: recorded rev %s unreadable for %s, falling back to HEAD",
                rev_used, file_path,
            )
            rev_used = "HEAD"
            result = _show(rev_used)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return "git_unavailable", rev_used
    if result.returncode != 0:
        # File not tracked at that revision (untracked, new file, deleted).
        return "git_unavailable", rev_used
    # Decode here, explicitly, with the SAME codec and error policy the cached
    # side uses in storage/sqlite_store.py::get_symbol_content. Symmetry is the
    # invariant: an asymmetric error policy reintroduces false mismatches by
    # another route. Accepted cost, pinned by a test — two byte sequences that
    # differ only where neither is decodable as UTF-8 compare equal.
    head_content = result.stdout.decode("utf-8", errors="replace")
    if not head_content:
        return "git_unavailable", rev_used

    cached_slice = _normalize_eol(cached_source).rstrip("\n")
    if _slice_matches(head_content, cached_slice, line, end_line):
        return "git_sha_match", rev_used

    # The cache does not match the commit. Before calling that divergence, ask
    # the second question: does it match the WORKING TREE? If it does, the cache
    # is a byte-exact record of what it indexed and the commit is simply behind
    # it — an ordinary mid-change state, not corruption, and the one state a
    # developer is most likely to be in when they ask (#402).
    try:
        tree_path = root / file_path
        tree_content = tree_path.read_bytes().decode("utf-8", errors="replace")
    except OSError:
        tree_content = None
    if tree_content and _slice_matches(tree_content, cached_slice, line, end_line):
        return "git_sha_uncommitted", rev_used

    return "git_sha_mismatch", rev_used


def _slice_matches(file_content: str, cached_slice: str, line: int, end_line: int) -> bool:
    """Does ``cached_slice`` equal lines ``line..end_line`` of ``file_content``?

    Shared by the revision and working-tree comparisons so the two can never
    drift into answering the same question two ways — the exact hazard that made
    #400 and #401 possible, where the cached side and the compared side were
    built by different rules.
    """
    lines = _normalize_eol(file_content).split("\n")
    if line < 1 or end_line < line or end_line > len(lines):
        # The symbol's line range no longer falls within this file's shape.
        return False
    file_slice = "\n".join(lines[line - 1:end_line]).rstrip("\n")
    # Realign the line slice onto the cached byte range's extent: drop the
    # first line's indentation (the cached side starts at the first token) and
    # stop where the cached side stops (the cached side ends at the symbol's
    # last byte, not its last line).
    #
    # ⚠ The indent is measured on the FIRST LINE only, not with
    # `file_slice.lstrip()`. `lstrip` walks across newlines, so a first line
    # that is entirely whitespace would consume the line break and realign onto
    # the wrong line — measuring within the line cannot.
    first_line = file_slice.split("\n", 1)[0]
    indent = len(first_line) - len(first_line.lstrip())
    candidate = file_slice[indent:indent + len(cached_slice)]
    # ⚠⚠ The truncation the length-bounded slice above would otherwise admit.
    # Slicing to `len(cached_slice)` turns this into a PREFIX match, so a cache
    # holding only the first part of a symbol — a bad `byte_length`, a partial
    # write, exactly the corruption this mode exists to catch — would be
    # attested as matching on the part it does hold.
    #
    # ⚠ The line-count check below closes ONLY the whole-line case. It is not
    # sufficient on its own and this comment used to claim that it was (#412):
    # a cache cut mid-way through its FINAL line keeps the newline count intact
    # and sailed through. The boundary check after the equality test is the
    # other half. Neither guard is redundant: line-count rejects a missing line
    # whose text would never have matched anyway, the boundary check rejects a
    # same-line-count prefix that does.
    #
    # The one legitimate shortfall stays permitted: the cached byte range stops
    # at the symbol's last byte, so trailing text on the LAST line goes
    # uncompared. No line may go missing.
    if cached_slice.count("\n") != file_slice.count("\n"):
        return False
    if candidate != cached_slice:
        return False
    # ⚠⚠ #412: the line-count guard above does NOT close truncation, and the
    # comment that said it did was wrong. Dropping bytes from INSIDE the final
    # line preserves the newline count, so `return 4` against a committed
    # `return 42` stayed a same-line-count prefix and was attested as matching.
    #
    # The remaining signal is what FOLLOWS the cached extent on that last line.
    # The one legitimate shortfall is trailing text after the symbol ends, and
    # text that trails a symbol is SEPARATED from it — a comment, a closing
    # delimiter of an enclosing construct. A truncation instead cuts mid-token,
    # so the next character continues the token the cache ends on. Requiring the
    # remainder to start on whitespace separates the two without needing to know
    # the language.
    #
    # ⚠ This is deliberately biased toward REFUSING. A symbol whose last line is
    # followed immediately by text with no separating space (`return 42# note`)
    # now reports divergence rather than a match. That is the safe direction for
    # a verification path: a false `git_sha_mismatch` costs a re-read, a false
    # `git_sha_match` attests bytes nobody checked — and this verdict rides an
    # evidence receipt, where it becomes a provenance claim.
    remainder = file_slice[indent + len(cached_slice):]
    return remainder == "" or remainder[0].isspace()


def get_symbol_source(
    repo: str,
    symbol_id: Optional[str] = None,
    symbol_ids: Optional[list[str]] = None,
    verify: bool = False,
    context_lines: int = 0,
    storage_path: Optional[str] = None,
    fqn: Optional[str] = None,
    verify_against: str = "cache",
    source_start_line: Optional[int] = None,
    source_end_line: Optional[int] = None,
    max_source_lines: Optional[int] = None,
    max_source_bytes: Optional[int] = None,
    max_total_source_bytes: Optional[int] = None,
) -> dict:
    """Get full source of one or more symbols by ID.

    Pass symbol_id (string) for one symbol — returns flat symbol object.
    Pass symbol_ids (array) for batch — returns {symbols, errors}.
    Both modes support verify and context_lines.
    Pass fqn (PHP FQN like 'App\\Models\\User') to resolve via PSR-4.

    Bounded-source mode (all optional, default off — when none are supplied the
    response is byte-for-byte the full-source default). Lets large symbols or
    broad batches return an explicitly-labeled source *slice* so a downstream
    client/context clip can't silently hand the agent a partial body:

    - ``source_start_line`` / ``source_end_line``: absolute file line numbers
      (same frame as ``line`` / ``end_line``), clamped to the symbol body.
    - ``max_source_lines``: keep at most the first N lines of the (ranged) slice.
    - ``max_source_bytes``: UTF-8-safe per-symbol byte cap.
    - ``max_total_source_bytes``: batch cap across all returned symbols, so a
      large batch returns bounded entries instead of an N x per-symbol blowup;
      oversized symbols come back partial, never dropped.

    When a bound shortens the source, the entry carries server-authored metadata:
    ``source_truncated``, ``source_range``, ``source_total_range``,
    ``source_total_lines``, ``source_total_bytes``, ``source_truncated_reason``,
    and ``source_is_bounded_view`` (so a verified entry shows the returned source
    is a slice, not the verified bytes). ``verify`` always hashes the *full*
    indexed body. ``context_lines`` may not be combined with any source bound
    (rejected) so it can never expand the payload past the requested bound.
    """
    # FQN resolution: translate PHP FQN → symbol_id
    if fqn and symbol_id is None and symbol_ids is None:
        resolved, fqn_error = resolve_fqn(repo, fqn, storage_path)
        if resolved is None:
            return {"error": fqn_error or f"Could not resolve FQN '{fqn}'."}
        symbol_id = resolved

    # Normalize: some MCP clients send symbol_ids=[] alongside symbol_id when they mean singular mode
    if symbol_id is not None and symbol_ids is not None and len(symbol_ids) == 0:
        symbol_ids = None
    if symbol_id is None and symbol_ids is None:
        return {"error": "Provide symbol_id (string), symbol_ids (array), or fqn (PHP FQN)."}
    if symbol_id is not None and symbol_ids is not None:
        return {"error": "Provide symbol_id or symbol_ids, not both."}

    batch_mode = symbol_ids is not None
    ids = symbol_ids if batch_mode else [symbol_id]

    start = time.perf_counter()
    context_lines = max(0, min(context_lines, 50))

    # Bounded-source mode: validated up-front so a bad bound rejects fast and the
    # contract is unambiguous (see docstring). Default (no bounds) is untouched.
    bounds_requested = any(
        v is not None for v in (
            source_start_line, source_end_line,
            max_source_lines, max_source_bytes, max_total_source_bytes,
        )
    )
    if bounds_requested:
        if context_lines > 0:
            return {"error": (
                "context_lines cannot be combined with source bounds "
                "(source_start_line / source_end_line / max_source_lines / "
                "max_source_bytes / max_total_source_bytes); it would expand the "
                "payload past the requested bound. Request context in a separate "
                "unbounded call."
            )}
        for label, val in (
            ("source_start_line", source_start_line),
            ("source_end_line", source_end_line),
            ("max_source_lines", max_source_lines),
            ("max_source_bytes", max_source_bytes),
            ("max_total_source_bytes", max_total_source_bytes),
        ):
            if val is not None and val < 1:
                return {"error": f"{label} must be >= 1 when provided."}
        if (source_start_line is not None and source_end_line is not None
                and source_end_line < source_start_line):
            return {"error": "source_end_line must be >= source_start_line."}

    try:
        owner, name = resolve_repo(repo, storage_path)
    except ValueError as e:
        return {"error": str(e)}

    store = IndexStore(base_path=storage_path)
    # #398 Arc 2: this is the canonical narrow call — the caller already knows
    # every id it wants, so there is nothing for a full hydration to contribute.
    # The view answers `get_symbol` and the file-level metadata exactly, and
    # promotes on `index.symbols` below, which is reached only on a MISS to
    # build `did_you_mean`. Paying for the corpus to spell-check a wrong id is
    # the right trade; paying for it to serve a correct one is not.
    index = store.open_selective(owner, name, symbol_ids=list(ids))
    if index is None:
        index = store.load_index(owner, name)

    if not index:
        return index_status_to_tool_error(store.inspect_index(owner, name))

    symbols_out = []
    errors_out = []
    unavailable_source_ids: list = []
    seen_files: set = set()
    raw_bytes = 0
    response_bytes = 0
    total_source_used = 0  # running byte total for the batch max_total_source_bytes cap

    for sid in ids:
        symbol = index.get_symbol(sid)

        if not symbol:
            err = {"id": sid, **symbol_not_found(sid, index.symbols)}
            _sug = suggest_symbol_ids(sid, index.symbols)
            if _sug:
                err["did_you_mean"] = _sug
            errors_out.append(err)
            continue

        source = store.get_symbol_content(owner, name, sid, _index=index)
        content_dir = store._content_dir(owner, name)
        file_full_path = content_dir / symbol["file"]

        context_before = ""
        context_after = ""
        if context_lines > 0 and source and file_full_path.exists():
            try:
                all_lines = file_full_path.read_text(encoding="utf-8", errors="replace").split("\n")
                s_line = symbol["line"] - 1  # 0-indexed
                e_line = symbol["end_line"]   # exclusive
                before_start = max(0, s_line - context_lines)
                after_end = min(len(all_lines), e_line + context_lines)
                if before_start < s_line:
                    context_before = "\n".join(all_lines[before_start:s_line])
                if e_line < after_end:
                    context_after = "\n".join(all_lines[e_line:after_end])
            except Exception:
                pass

        # Bounded-source mode shapes the returned `source` into an explicitly
        # labeled slice; `source` (the variable) stays the full body so verify
        # below still hashes the complete indexed bytes.
        display_source = source or ""
        bound_meta = None
        if bounds_requested and source:
            remaining = None
            if max_total_source_bytes is not None:
                remaining = max(0, max_total_source_bytes - total_source_used)
            bound_meta = _bound_source(
                source,
                symbol["line"],
                symbol["end_line"],
                source_start_line,
                source_end_line,
                max_source_lines,
                max_source_bytes,
                remaining,
            )
            display_source = bound_meta["text"]
            total_source_used += len(display_source.encode("utf-8"))

        entry = {
            "id": symbol["id"],
            "kind": symbol["kind"],
            "name": symbol["name"],
            "file": symbol["file"],
            "line": symbol["line"],
            "end_line": symbol["end_line"],
            "signature": symbol["signature"],
            "decorators": symbol.get("decorators", []),
            "docstring": symbol.get("docstring", ""),
            # Always populated here so the evidence producer can read it off the
            # served row (it never re-reads the index). The dispatcher strips it
            # again after minting unless `verify` or `receipt` was requested —
            # see the content_hash block in server.py's call_tool. Do not gate
            # it at this line: that downgrades every receipt's hash_source.
            "content_hash": symbol.get("content_hash", ""),
            "source": display_source,
        }
        if bound_meta is not None:
            entry["source_truncated"] = bound_meta["truncated"]
            if bound_meta["truncated"]:
                # Verified entries: flag that `source` is a slice, not the bytes
                # `content_verified` attests to (which is always the full body).
                entry["source_is_bounded_view"] = True
                entry["source_range"] = bound_meta["range"]
                entry["source_total_range"] = bound_meta["total_range"]
                entry["source_total_lines"] = bound_meta["total_lines"]
                entry["source_total_bytes"] = bound_meta["total_bytes"]
                entry["source_truncated_reason"] = bound_meta["reason"]
        # P1.4: distinguish "empty source" from "no body cached because we're
        # in metadata_only mode" so downstream agents don't treat the empty
        # string as the symbol's actual source.
        if not source:
            try:
                from .. import config as _cfg
                if _cfg.get("cache_mode", "full") == "metadata_only":
                    entry["source_status"] = "metadata_only_mode"
            except Exception:
                pass
            # `None` means the content file could not be read at all, which is a
            # different fact from a zero-length body: an index whose content
            # cache was pruned, copied without its sibling directory, or never
            # shipped (a starter pack carries `.db` files only) resolves the row
            # and cannot produce the bytes. Left unlabeled, the empty string
            # reads as the symbol's actual source under a confident verdict.
            if source is None and "source_status" not in entry:
                entry["source_status"] = "content_cache_missing"
                entry["source_unavailable_reason"] = (
                    f"No cached content for {symbol['file']} under {content_dir}. "
                    "Re-index the repo to rebuild it."
                )
                unavailable_source_ids.append(symbol["id"])
        if context_before:
            entry["context_before"] = context_before
        if context_after:
            entry["context_after"] = context_after

        if verify and source:
            actual_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
            stored_hash = symbol.get("content_hash", "")
            entry["content_verified"] = actual_hash == stored_hash if stored_hash else None
            # P1.6: externally-attested mode compares cached source against the
            # working-tree git HEAD slice of the same file. Surfaced alongside
            # the cache-only verification so callers can see both signals.
            if verify_against == "git_sha":
                # v1.108.227 (#402): compare against the commit the INDEX
                # RECORDED, not live HEAD. Live HEAD answers "did the working
                # tree at index time happen to equal HEAD at verification time",
                # which is a different question and routinely false mid-change.
                # `git_sha_rev` reports which revision actually backed the
                # verdict, so it can never be silently about another commit —
                # the recorded revision may have been rebased away, in which
                # case this falls back to HEAD and says so.
                _status, _rev = _verify_against_git_sha(
                    cached_source=source,
                    source_root=getattr(index, "source_root", None),
                    file_path=symbol["file"],
                    line=symbol["line"],
                    end_line=symbol["end_line"],
                    rev=getattr(index, "git_head", "") or None,
                )
                entry["git_sha_verification"] = _status
                entry["git_sha_rev"] = _rev

        symbols_out.append(entry)

        # Accumulate token savings
        f = symbol["file"]
        if f not in seen_files:
            seen_files.add(f)
            try:
                raw_bytes += os.path.getsize(file_full_path)
            except OSError:
                pass
        response_bytes += symbol.get("byte_length", 0)

    tokens_saved = estimate_savings(raw_bytes, response_bytes)
    total_saved = record_savings(tokens_saved, tool_name="get_symbol_source")
    elapsed = (time.perf_counter() - start) * 1000
    meta = _make_meta(elapsed, tokens_saved=tokens_saved, total_tokens_saved=total_saved,
                      **_cost_avoided(tokens_saved, total_saved))

    from ..retrieval.freshness import FreshnessProbe as _FreshnessProbe
    _probe = _FreshnessProbe(
        source_root=getattr(index, "source_root", "") or None,
        indexed_at=getattr(index, "indexed_at", ""),
        index_sha=getattr(index, "git_head", None),
        file_mtimes=getattr(index, "file_mtimes", None),
    )
    _probe.annotate(symbols_out)

    # Phase 2: runtime confidence — zero-cost no-op when no traces ingested.
    from ..runtime.confidence import attach_runtime_confidence as _attach_runtime
    _runtime_summary = _attach_runtime(
        symbols_out,
        str(store._sqlite._db_path(owner, name)),
        id_field="id",
    )

    if batch_mode:
        meta["symbol_count"] = len(symbols_out)
        meta["freshness"] = _probe.summary(symbols_out)
        meta["verdict"] = symbol_verdict_for_index(
            index,
            found_count=len(symbols_out),
            requested_id=(ids[0] if len(ids) == 1 else None),
            unavailable_source_count=len(unavailable_source_ids),
        )
        if unavailable_source_ids:
            meta["unavailable_source_ids"] = unavailable_source_ids
        if _runtime_summary:
            meta["runtime_freshness"] = _runtime_summary
        out = {"symbols": symbols_out, "errors": errors_out, "_meta": meta}
        _mod = _offload()
        if _mod is not None:
            # Attached LAST, so the shape it reads is the payload actually
            # served — verdict, freshness and all. Reading a half-built meta
            # would classify something the caller never receives.
            # One adjudicating call PER symbol, named explicitly. A single
            # `args` would have to pick one of N names and imply the rest were
            # covered; `args_each` says what it means.
            _names = [
                s.get("name") for s in symbols_out
                if isinstance(s, dict) and s.get("name")
            ]
            _mod.annotate(
                out,
                retrieval_mode=_mod.MODE_IDENTITY,
                unit_field="symbols",
                verify_with=(
                    {
                        "tool": "check_references",
                        "args_each": [{"identifier": n} for n in _names],
                    }
                    if _names
                    else None
                ),
            )
        return out

    # Single mode: flat object or error
    if errors_out:
        verdict = symbol_verdict_for_index(
            index, found_count=0, requested_id=errors_out[0]["id"]
        )
        # The not-found authority's keys, not `error` alone: single mode kept
        # only the message and would have dropped `near_miss_ids` (#869).
        # ⚠ `did_you_mean` stays batch-only: surfacing it here is a new 1.x
        # response field, and it ships with no total/truncated disclosure.
        err_out = {k: v for k, v in errors_out[0].items() if k not in ("id", "did_you_mean")}
        err_out["_meta"] = {"verdict": verdict}
        _mod = _offload()
        if _mod is not None:
            # ⚠ Explicitly `not_evaluated`, not silence. With the gate ON, a
            # missing block would be indistinguishable from the gate being OFF,
            # and "we did not assess it" is a third state that has to be
            # sayable — the same reason the verdict itself is tri-state.
            _mod.not_evaluated(err_out, reason="error payload carries no evidence")
        return err_out
    result = symbols_out[0]
    meta["hint"] = "Use get_context_bundle(symbol_id) to retrieve source + imports in one call"
    meta["verdict"] = symbol_verdict_for_index(
        index,
        found_count=len(symbols_out),
        requested_id=symbol_id,
        unavailable_source_count=len(unavailable_source_ids),
    )
    meta["freshness"] = _probe.summary(symbols_out)
    if _runtime_summary:
        meta["runtime_freshness"] = _runtime_summary
    result["_meta"] = meta
    _mod = _offload()
    if _mod is not None:
        _name = result.get("name") if isinstance(result, dict) else None
        _mod.annotate(
            result,
            retrieval_mode=_mod.MODE_IDENTITY,
            verify_with=(
                {"tool": "check_references", "args": {"identifier": _name}}
                if _name
                else None
            ),
        )
    return result
