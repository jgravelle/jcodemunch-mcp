"""Unified retrieval verdict — one honesty contract across the search tools.

An empty or weak retrieval result is positive, token-saving evidence: grounded
symbolic retrieval can prove "this is not here" where nearest-neighbour search
always returns its closest something. ``build_verdict`` centralises the logic
that ``search_symbols`` and ``get_ranked_context`` previously duplicated, and
extends it to ``search_text``.

The result carries two things:

* ``verdict`` — the unified ``_meta.verdict`` dict with a complete taxonomy
  (``ok`` / ``low_confidence`` / ``absent`` / ``degraded``), the scan counts that
  back an absence claim, per-channel status, and near-miss suggestions.
* ``negative_evidence`` — the legacy dict (or ``None``) with the same trigger and
  shape as before, so existing consumers and the injected agent policy keep
  working unchanged.
"""

from __future__ import annotations

import re
from typing import Optional, Sequence


def index_truncation_meta(cap: Optional[dict]) -> Optional[dict]:
    """Query-time ``_meta.index_truncated`` block from a persisted cap status (#366).

    ``cap`` is ``CodeIndex.file_cap_status``. Returns None unless the index was
    truncated by the max_folder_files walk cap, in which case it warns that whole
    files are missing from the corpus these results were drawn from — so an empty
    or thin result may be truncation, not genuine absence.
    """
    if not cap or not cap.get("truncated"):
        return None
    return {
        "truncated": True,
        "files_discovered": cap.get("files_discovered"),
        "files_indexed": cap.get("files_indexed"),
        "files_skipped_cap": cap.get("files_skipped_cap"),
        "max_folder_files": cap.get("max_folder_files"),
        "note": (
            "This index is incomplete: the max_folder_files cap dropped "
            f"{cap.get('files_skipped_cap')} file(s) at index time, so entire files "
            "are absent from search. A missing or thin result may be truncation, not "
            "absence. Raise max_folder_files in config.jsonc (or set "
            "JCODEMUNCH_MAX_FOLDER_FILES) and re-index."
        ),
    }


# Version pin for the confidence/score heuristics the verdict reports.
# Bump whenever the scoring formula (BM25 blending, thresholds, seeding
# floors) changes, so calibration claims tie to the scorer that produced
# them: "0.8 under scorer v1" is a measurable statement, "0.8" is not.
SCORER_VERSION = 1


def index_coverage_meta(index) -> Optional[dict]:
    """Query-time coverage disclosure backing an absence claim.

    Pulls the persisted coverage contract (recorded at the last full
    discovery walk) plus generation metadata off the index. Returns None when
    the index predates coverage recording — absence of the block means
    "coverage unknown", never "nothing was excluded".
    """
    cov = getattr(index, "coverage", None)
    if not isinstance(cov, dict) or not cov:
        return None
    out: dict = {
        "generation": {
            "indexed_at": getattr(index, "indexed_at", "") or None,
            "index_version": getattr(index, "index_version", None),
        },
        "files_indexed": cov.get("files_indexed"),
    }
    head = getattr(index, "git_head", "") or ""
    if head:
        out["generation"]["git_head"] = head[:12]
    scopes = getattr(index, "source_roots", None)
    if scopes:
        out["included_scopes"] = scopes
    skips = cov.get("skip_counts") or {}
    if skips:
        out["excluded"] = skips
    if cov.get("no_symbols_count"):
        out["no_symbols_files"] = cov["no_symbols_count"]
    # v1.108.176 (#375 sub-problem C). `complete` is tri-state on purpose:
    # True / False / None-for-unknown. Older indexes have no `files_accepted`
    # and report None, which must never be read as True — an index that cannot
    # account for itself is not thereby complete.
    if "complete" in cov:
        out["complete"] = cov["complete"]
    if cov.get("files_accepted") is not None:
        out["files_accepted"] = cov["files_accepted"]
    if cov.get("dropped_after_discovery"):
        out["dropped_after_discovery"] = cov["dropped_after_discovery"]
    if cov.get("unaccounted"):
        out["unaccounted"] = cov["unaccounted"]
    # v1.108.193: surfaced separately from `excluded` because it answers a
    # different question. `excluded` says what this corpus is not about; a
    # binary or a gitignored tree was never a candidate. `withheld` says a real,
    # current, wanted source file was refused by one of OUR limits, which is the
    # one exclusion reason a reader must not read as "the file is not there".
    if cov.get("withheld"):
        out["withheld"] = cov["withheld"]
    return out


def coverage_is_incomplete(coverage: Optional[dict]) -> bool:
    """True only when coverage PROVES files are missing from the corpus.

    Unknown coverage (`complete is None`, or no block at all) is not
    incompleteness: it is the absence of a measurement, and treating it as a
    defect would fire on every index built before this contract existed.
    """
    return bool(coverage) and coverage.get("complete") is False


def _attach_coverage(verdict: dict, coverage: Optional[dict]) -> None:
    """Attach coverage disclosure to absent/degraded verdicts (in place).

    Only the states where "what wasn't scanned" changes the meaning of the
    result carry the block; ok/low_confidence stay lean.
    """
    if coverage and verdict.get("state") in (STATE_ABSENT, STATE_DEGRADED):
        verdict["coverage"] = coverage


# Emitted as verdict["state"].
STATE_OK = "ok"
STATE_LOW_CONFIDENCE = "low_confidence"
STATE_ABSENT = "absent"
STATE_DEGRADED = "degraded"

_NOTES = {
    STATE_OK: "Confident matches returned.",
    STATE_LOW_CONFIDENCE: (
        "Matches are below the confidence threshold; verify before relying on them."
    ),
    STATE_ABSENT: (
        "No match found after scanning the index. Treat this as strong evidence the "
        "target is not present; do not reformulate the same query expecting a hit."
    ),
    STATE_DEGRADED: (
        "A requested retrieval channel was unavailable or the scan was cut short. "
        "Results are partial and absence is NOT proven."
    ),
}


def refuse_absence(verdict: dict, note: str) -> bool:
    """Downgrade an ``absent`` verdict whose claim a later check refused.

    THE one way to rewrite ``absent`` after ``build_verdict`` (#872). The
    dispatcher attaches its refusal carrier on ``state == "absent"`` or on
    ``absence_refused``, and two downgrades that set neither -- a cache replay
    over a moved subject and a call with ignored arguments -- reached a default
    install (``meta_fields: []``) with no reason and no ``_meta`` at all.
    ``build_verdict`` sets the flag on every refused zero-result scan; this does
    the same for a refusal decided after it. Returns whether it downgraded.
    """
    if verdict.get("state") != STATE_ABSENT:
        return False
    verdict["state"] = STATE_DEGRADED
    verdict["note"] = note
    verdict["absence_refused"] = True
    return True


def _semantic_provider_available() -> bool:
    """Return True when an embedding provider is actually configured.

    Reuses ``embed_repo``'s live detection so we do not drift from the encoder the
    semantic path would really use. Called only when semantic was requested.
    """
    try:
        from ..tools.embed_repo import _detect_provider

        detected = _detect_provider()
        if isinstance(detected, tuple):
            return bool(detected and detected[0])
        return bool(detected)
    except Exception:
        return False


def _did_you_mean(
    source_files: Optional[Sequence[str]],
    query_terms: Optional[Sequence[str]],
    cap: int = 5,
) -> list:
    """Files whose basename contains a query term (near-miss candidates)."""
    if not source_files or not query_terms:
        return []
    out: list = []
    seen: set = set()
    for f in source_files:
        base = f.lower().replace("\\", "/").rsplit("/", 1)[-1]
        if any(t in base for t in query_terms):
            if f not in seen:
                seen.add(f)
                out.append(f)
                if len(out) >= cap:
                    break
    return out


def build_verdict(
    *,
    result_count: int,
    scanned_symbols: int = 0,
    scanned_files: int = 0,
    best_score: Optional[float] = None,
    threshold: Optional[float] = None,
    query_terms: Optional[Sequence[str]] = None,
    source_files: Optional[Sequence[str]] = None,
    semantic_requested: bool = False,
    index_stale: bool = False,
    index_changed: bool = False,
    timed_out: bool = False,
    coverage: Optional[dict] = None,
    matches_before_packing: Optional[int] = None,
    incomplete: Optional[dict] = None,
    moved_during_scan: Optional[str] = None,
    freshness: Optional[str] = None,
    working_tree: Optional[dict] = None,
    absence_unprovable: Optional[str] = None,
) -> dict:
    """Compute the unified verdict plus the legacy negative_evidence dict.

    Returns ``{"verdict": <_meta.verdict>, "negative_evidence": <dict|None>}``.

    Backward compatibility: ``negative_evidence`` fires on exactly the historical
    trigger (empty result, or best score below threshold) with the historical keys
    and verdict names, so existing tests and the agent policy are unaffected. The
    new ``verdict`` is purely additive.
    """
    terms = [t for t in (query_terms or []) if t]
    did_you_mean = _did_you_mean(source_files, terms)

    # #377 item 4. `index_stale` is a Boolean with nowhere to put "I could not
    # find out", and False was being rendered as `fresh` — so an index whose
    # freshness was never established claimed current-snapshot equivalence.
    # A producer that passes the richer state wins; one that does not keeps
    # exactly its previous two-state behavior.
    if freshness not in ("fresh", "stale", "unknown", "not_tracked"):
        freshness = "stale" if index_stale else "fresh"
    elif freshness == "stale":
        index_stale = True
    freshness_unknown = freshness == "unknown"

    semantic_available = _semantic_provider_available() if semantic_requested else True
    below_threshold = (
        threshold is not None and best_score is not None and best_score < threshold
    )

    # --- unified state (degraded takes precedence: partial scans can't prove absence) ---
    if timed_out:
        state = STATE_DEGRADED
    elif semantic_requested and not semantic_available:
        state = STATE_DEGRADED
    elif result_count == 0 and index_changed:
        # The index was rewritten underneath this scan, so "we looked and it is
        # not there" describes a tree that was moving while we read it — the
        # target may sit in rows written after we passed them. Same reasoning
        # as the stale and truncated gates: degraded cannot prove absence, so
        # the absence-evidence refusal rule falls out of the existing
        # "only `absent` proves absence" check with no new rule to keep in sync.
        # Deliberately scoped to the absence path: a scan that RETURNED results
        # still returns them (they were really in the index) and only discloses
        # the rebuild via channels.index below.
        state = STATE_DEGRADED
    elif result_count == 0 and coverage_is_incomplete(coverage):
        # #375 sub-problem C. Freshness answers "is the index BEHIND the tree in
        # time" (SHA vs HEAD) and was being read as "does the index COVER the
        # tree". A corpus that dropped files at index time sits at the same SHA
        # as the checkout, so it reported `fresh` while whole files were missing
        # — a user watched that combination hand back confident zero-results for
        # ~1,975 unindexed files and moved their code lookup to another tool.
        #
        # A file that never entered the corpus cannot be proven absent from it,
        # so the same degraded gate as the stale/truncated/rebuilding cases
        # applies and the absence-refusal rule falls out of the existing "only
        # `absent` proves absence" check with nothing new to keep in sync.
        state = STATE_DEGRADED
    elif result_count == 0 and (working_tree or {}).get("blocks"):
        # #377 hardening item 5. Git HEAD can sit still while the tree holds an
        # edit the corpus has not read, and a zero-result scan has no returned
        # file to hang per-file freshness on. Only work INSIDE the scanned
        # scope, and only work the index has not caught up with, gets here.
        state = STATE_DEGRADED
    elif result_count == 0 and freshness_unknown:
        # This subject HAS a revision we should be able to read and we could
        # not, so whether the index lags the tree is unestablished — and an
        # absence claim rests entirely on that. `not_tracked` is deliberately
        # NOT here: a subject with no revision at all is disclosed, not refused,
        # or every plain-folder index would lose absence evidence outright.
        state = STATE_DEGRADED
    elif result_count == 0 and moved_during_scan:
        # #377 hardening item 6. The scan started against one state and finished
        # against another, so "we looked and it is not there" describes neither
        # of them. Same degraded gate as every sibling case.
        state = STATE_DEGRADED
    elif result_count == 0 and incomplete:
        # #377 hardening items 7 and 9. Inputs the scan was supposed to read but
        # could not, or an eligible set that was empty before a byte was read:
        # either way nothing here proves the target is not in the corpus. Same
        # degraded gate, same single refusal rule.
        state = STATE_DEGRADED
    elif result_count == 0 and (matches_before_packing or 0) > 0:
        # #377 hardening item 1. `result_count` is what the RESPONSE carried,
        # and the response is packed after ranking: a token budget or a result
        # cap can empty it while matches really were found. An empty response
        # is not an empty search, so the scan cannot prove absence — same
        # degraded gate as the stale/truncated/rebuilding/partial cases, so the
        # refusal falls out of the existing "only `absent` proves absence"
        # check with no second rule to keep in sync.
        state = STATE_DEGRADED
    elif result_count == 0 and absence_unprovable:
        # v1.108.184. Checked LAST among the degraded gates on purpose: every gate
        # above names a condition the caller can do something about (re-index,
        # widen the scope, raise the budget, re-run the search). This one names a
        # permanent property of the retrieval mode that ran, so reporting a
        # fixable cause in preference to it is strictly more useful.
        #
        # The case it exists for: a zero result from an embedding ranking. The
        # lexical path's absence rests on a corpus fact — no symbol in the index
        # contains any query term. A cosine ranking's zero result rests on
        # embedding geometry, and a symbol can exist in the corpus while scoring
        # at or below zero against the query vector. That is a statement about the
        # model, not about the repository, and it must not be citable as one.
        state = STATE_DEGRADED
    elif result_count == 0:
        state = STATE_ABSENT
    elif below_threshold:
        state = STATE_LOW_CONFIDENCE
    else:
        state = STATE_OK

    if semantic_requested and not semantic_available:
        semantic_channel = "unavailable"
    elif semantic_requested:
        semantic_channel = "ok"
    else:
        semantic_channel = "off"

    verdict = {
        "state": state,
        "scanned": {"symbols": int(scanned_symbols), "files": int(scanned_files)},
        "best_score": round(best_score, 3) if best_score is not None else None,
        "channels": {
            "lexical": "ok",
            "semantic": semantic_channel,
            # Disclosed on EVERY state, not just the degraded one above: a
            # caller reading an `ok` result still deserves to know the index
            # moved under it, or does not cover the tree. Only the absence CLAIM
            # is refused.
            #
            # Order is by how badly each condition undermines the answer:
            # a rebuild in flight beats a known gap beats mere lag. "partial"
            # exists because `fresh` was answering the wrong question — it means
            # "not behind in time", never "covers everything" (#375).
            # `unknown` and `not_tracked` (#377 item 4) sit below `stale` and
            # above `fresh`: a known lag is worse than an unestablished one, and
            # both are worse than proven currency. `fresh` now means only what
            # it says.
            "index": index_channel(
                index_changed=index_changed,
                coverage=coverage,
                index_stale=index_stale,
                freshness=freshness,
            ),
        },
        "scorer": SCORER_VERSION,
        "note": _NOTES[state],
    }
    if state == STATE_DEGRADED and result_count == 0:
        # The verdict is the authority on this, so it says it here rather than
        # leaving the dispatcher to re-derive "was the response empty" from a
        # hand-kept tuple of per-tool result keys — which is exactly the class of
        # bug that tuple always has (a new producer is added, nobody adds its key,
        # and the disclosure silently stops firing for it).
        #
        # It matters because jcodemunch ships `meta_fields: []` by DEFAULT: on a
        # default install the verdict is deleted before the agent sees it, and only
        # the re-attached carrier survives. That carrier used to fire for `absent`
        # alone, so every REFUSED zero-result reached a default-configured caller
        # as a bare empty response with no reason attached.
        verdict["absence_refused"] = True
    if did_you_mean:
        verdict["did_you_mean"] = did_you_mean
    # Disclosed on EVERY state, not just the refused one: a caller reading a
    # short result list deserves to know matches were dropped to fit the
    # response, whether or not an absence claim is involved.
    if working_tree:
        # Disclosed whenever it was measured. It is measured only for a
        # zero-result scan: probing the tree on every search would price a
        # subprocess into the answers that do not need it.
        verdict["working_tree"] = {k: v for k, v in working_tree.items() if k != "blocks"}
        if working_tree.get("blocks") and state == STATE_DEGRADED and result_count == 0:
            _n = working_tree.get("files_not_in_index", 0)
            verdict["note"] = (
                f"{_n} uncommitted change(s) inside this scope are not in the index "
                "yet, so the target may sit in an edit the scan could not read. "
                "Absence is NOT proven; re-index or narrow the scope."
            )
    if freshness_unknown and state == STATE_DEGRADED and result_count == 0:
        verdict["note"] = (
            "Index freshness could not be established for this repository, so "
            "whether the index lags the tree is unknown and absence is NOT proven. "
            "Results a scan returns are still real; only the claim that nothing "
            "exists needs the comparison this scan could not make."
        )
    if moved_during_scan:
        verdict["moved_during_scan"] = {"reason": moved_during_scan}
        if state == STATE_DEGRADED and result_count == 0:
            verdict["note"] = (
                f"The subject moved while this scan ran: {moved_during_scan}. "
                "Absence is NOT proven against either state; re-run the search."
            )
    if absence_unprovable:
        # Disclosed on EVERY state, like every sibling block: a caller reading an
        # `ok` semantic result still deserves to know this mode cannot prove
        # absence, because the next thing they do may be to re-run it expecting
        # one. Only the CLAIM is refused.
        verdict["absence_unprovable"] = {"reason": absence_unprovable}
        if state == STATE_DEGRADED and result_count == 0:
            verdict["note"] = (
                f"Nothing was returned, and {absence_unprovable} Absence is NOT "
                "proven; re-run the search without the semantic channel to get an "
                "answer that can prove it."
            )
    if incomplete:
        verdict["incomplete"] = dict(incomplete)
        if state == STATE_DEGRADED and result_count == 0 and incomplete.get("note"):
            verdict["note"] = incomplete["note"]
    if matches_before_packing is not None and matches_before_packing > result_count:
        verdict["omitted"] = {
            "matches_found": int(matches_before_packing),
            "returned": int(result_count),
            "by_response_budget": int(matches_before_packing) - int(result_count),
        }
        if state == STATE_DEGRADED and result_count == 0:
            verdict["note"] = (
                f"{matches_before_packing} match(es) were found, but none fit the "
                "response budget, so the response is empty and the search is not. "
                "Absence is NOT proven; re-run with a larger token_budget or "
                "max_results."
            )
    _attach_coverage(verdict, coverage)

    # --- legacy negative_evidence: unchanged trigger + shape ---
    negative_evidence = None
    _packed_empty = result_count == 0 and (
        (matches_before_packing or 0) > 0
        or bool(incomplete)
        or bool(moved_during_scan)
        or freshness_unknown
        or bool((working_tree or {}).get("blocks"))
        or bool(absence_unprovable)
    )
    if _packed_empty:
        # The legacy block would say "no_implementation_found", and
        # search_symbols renders that as "Do not claim this feature exists" —
        # a false statement when matches were found and dropped by the packer,
        # and an unfounded one when the mode that ran cannot establish absence
        # at all (v1.108.184).
        pass
    elif result_count == 0 or below_threshold:
        negative_evidence = {
            "verdict": (
                "no_implementation_found" if result_count == 0 else "low_confidence_matches"
            ),
            "scanned_symbols": int(scanned_symbols),
            "scanned_files": int(scanned_files),
            "best_match_score": round(best_score, 3) if best_score else 0.0,
        }
        if did_you_mean:
            negative_evidence["related_existing"] = did_you_mean

    return {"verdict": verdict, "negative_evidence": negative_evidence}


def retrieval_verdict_for_index(
    index,
    *,
    result_count: int,
    scanned_symbols: int = 0,
    scanned_files: int = 0,
    query_terms: Optional[Sequence[str]] = None,
    best_score: Optional[float] = None,
    threshold: Optional[float] = None,
    matches_before_packing: Optional[int] = None,
    scope: Optional[str] = None,
    state_before: Optional[dict] = None,
    semantic_channel: str = "off",
    incomplete: Optional[dict] = None,
    absence_unprovable: Optional[str] = None,
) -> dict:
    """Index-aware wrapper over :func:`build_verdict` (v1.108.185).

    Every retrieval exit needs the same five index-derived signals — the freshness
    probe, the rebuild check, the coverage contract, the movement comparison and
    the scope-level working-tree state — and every exit was assembling them by
    hand. That is how three exits ended up with no verdict at all: adding one
    meant reproducing five call patterns correctly, so the cheap thing was to skip
    it and hand-roll the answer instead.

    Sibling of :func:`symbol_verdict_for_index` and :func:`file_verdict_for_index`,
    which already do this for the file and symbol tools. Returns
    ``{"verdict", "negative_evidence"}`` exactly as ``build_verdict`` does.

    ``semantic_channel`` is stated by the caller rather than probed: an exit that
    has already resolved a provider and run the channel knows more than a
    re-detection would, and ``semantic_requested=True`` would make the verdict
    re-probe and possibly contradict the exit that just used it.
    """
    from . import subject_state as _subject
    from .freshness import FreshnessProbe

    probe = FreshnessProbe(
        source_root=getattr(index, "source_root", "") or None,
        indexed_at=getattr(index, "indexed_at", ""),
        index_sha=getattr(index, "git_head", None),
        file_mtimes=getattr(index, "file_mtimes", None),
    )
    freshness = probe.repo_freshness
    result = build_verdict(
        result_count=result_count,
        scanned_symbols=scanned_symbols,
        scanned_files=scanned_files,
        best_score=best_score,
        threshold=threshold,
        query_terms=query_terms,
        source_files=getattr(index, "source_files", None),
        semantic_requested=False,
        index_stale=probe.repo_is_stale,
        freshness=freshness,
        index_changed=index_changed_since_load(index),
        coverage=index_coverage_meta(index),
        matches_before_packing=matches_before_packing,
        incomplete=incomplete,
        absence_unprovable=absence_unprovable,
        moved_during_scan=_subject.moved_during_scan(
            state_before, index, result_count=result_count
        ),
        # Measured for a zero-result scan only: probing the tree on every search
        # would price a subprocess into the answers that do not need it.
        working_tree=(
            _subject.working_tree_state(index, scope=scope, freshness=freshness)
            if result_count == 0 else None
        ),
    )
    if semantic_channel != "off":
        result["verdict"]["channels"]["semantic"] = semantic_channel
    return result


def suggest_paths(
    requested_path: Optional[str],
    source_files: Optional[Sequence[str]],
    cap: int = 5,
) -> list:
    """Indexed paths that plausibly match a missing ``requested_path``.

    Exact-basename matches in a different directory come first (the agent had
    the filename right, the directory wrong), then stem substring matches. The
    requested path itself is never suggested.
    """
    if not requested_path or not source_files:
        return []
    req = str(requested_path).replace("\\", "/")
    req_base = req.rsplit("/", 1)[-1].lower()
    req_stem = req_base.rsplit(".", 1)[0] if "." in req_base else req_base
    exact: list = []
    partial: list = []
    seen: set = set()
    for f in source_files:
        norm = str(f).replace("\\", "/")
        if norm == req or f in seen:
            continue
        base = norm.rsplit("/", 1)[-1].lower()
        stem = base.rsplit(".", 1)[0] if "." in base else base
        if base == req_base:
            exact.append(f)
            seen.add(f)
        elif req_stem and len(req_stem) >= 3 and (req_stem in stem or stem in req_stem):
            partial.append(f)
            seen.add(f)
    return (exact + partial)[:cap]


def _symbol_name_of(symbol_id: Optional[str]) -> str:
    """Bare name from a symbol id like ``path::Name#kind`` (or a plain name)."""
    if not symbol_id:
        return ""
    s = str(symbol_id)
    if "::" in s:
        s = s.rsplit("::", 1)[-1]
    if "#" in s:
        s = s.split("#", 1)[0]
    return s.lower()


#: The most near-miss ids a not-found error names; `near_miss_total` and
#: `near_miss_truncated` disclose the rest (a list field ships with its cap).
SYMBOL_CANDIDATES_CAP = 10

_ID_SUFFIX_RE = re.compile(r"~\d+$")
_QUALIFIER_RE = re.compile(r"\.|::")


def _split_symbol_id(symbol_id: Optional[str]) -> Optional[tuple[str, str, str]]:
    """`(file, bare name, kind)` of a `file::qualified#kind[~N]` id, or None
    for a bare name. The owner qualifier and the `~N` suffix are exactly what
    this drops, because they are what a near miss may differ by."""
    if not symbol_id or "::" not in symbol_id:
        return None
    file_path, rest = symbol_id.split("::", 1)
    rest = _ID_SUFFIX_RE.sub("", rest)
    qualified, _, kind = rest.rpartition("#") if "#" in rest else (rest, "", "")
    return file_path, _QUALIFIER_RE.split(qualified)[-1], kind


def symbol_id_candidates(
    requested_id: Optional[str],
    symbols: Optional[Sequence[dict]],
    cap: Optional[int] = SYMBOL_CANDIDATES_CAP,
) -> tuple[list, int]:
    """Indexed ids that differ from a missing `requested_id` ONLY by the owner
    qualifier or the `~N` suffix (#869): same file, same kind, same bare name.
    Returns `(first cap ids in index order, total)`.

    ⚠⚠ A NAMING, never a resolution. Two classes in one file can each own a
    `pick`, and choosing one would answer a question about a different symbol,
    so callers name these and still fail the call. A different kind or file is
    a different symbol, not a near miss (`suggest_symbol_ids` is the looser
    same-name search, for `did_you_mean`).
    """
    wanted = _split_symbol_id(requested_id)
    if wanted is None or not symbols:
        return [], 0
    # A near miss shares the file, so the prefix test skips the parse for
    # every symbol elsewhere (one linear pass per miss on a large index).
    prefix = wanted[0] + "::"
    found = [
        s["id"]
        for s in symbols
        if s.get("id", "").startswith(prefix)
        and s["id"] != requested_id
        and _split_symbol_id(s["id"]) == wanted
    ]
    return (found if cap is None else found[:cap]), len(found)


def symbol_not_found(requested, symbols: Optional[Sequence[dict]]) -> dict:
    """THE not-found error for a symbol argument, shared by every tool that
    takes one (#869: sixteen sites wrote their own, and none named the id one
    qualifier away). `requested` is one id or name, or a list of them.

    With near misses, the error names them in `near_miss_ids` and says it did
    not pick one. Without, it points at `search_symbols`. A caller adds its own
    keys beside these; it never rewrites the error text.

    ⚠⚠ NOT `candidates`: four of the tools that call this already answer an
    AMBIGUOUS name with `candidates` holding `{name, file, id}` dicts, so one
    key would carry two element shapes depending on the branch (review round 1).
    """
    wanted = [requested] if isinstance(requested, str) or requested is None else list(requested)
    # The UNION of every request's near misses, uncapped, then one cap: two
    # missing ids can share near misses, and a total summed per request
    # counted them twice (`near_miss_total: 4` over two ids, review round 2).
    found: list = []
    for one in wanted:
        ids, _ = symbol_id_candidates(one, symbols, cap=None)
        found += [i for i in ids if i not in found]
    total = len(found)
    shown = found[:SYMBOL_CANDIDATES_CAP]
    named = ", ".join(str(w) for w in wanted)
    label = "Symbol" if len(wanted) == 1 else "Symbol(s)"
    if not shown:
        return {"error": f"{label} not found: {named}. Try search_symbols first."}
    return {
        "error": (
            f"{label} not found: {named}. {total} indexed id(s) differ only by the owner "
            "qualifier or the ~N suffix; pass one from `near_miss_ids` (none was chosen for you)."
        ),
        "near_miss_ids": shown,
        "near_miss_total": total,
        "near_miss_truncated": total > len(shown),
    }


def suggest_symbol_ids(
    requested_id: Optional[str],
    symbols: Optional[Sequence[dict]],
    cap: int = 5,
) -> list:
    """Indexed symbol ids whose name matches a missing ``requested_id``.

    Same-name symbols (right name, wrong file/kind) rank ahead of substring
    matches. Operates on the index's raw symbol dicts.
    """
    name = _symbol_name_of(requested_id)
    if not name or not symbols:
        return []
    exact: list = []
    partial: list = []
    seen: set = set()
    for s in symbols:
        sid = s.get("id")
        if not sid or sid == requested_id or sid in seen:
            continue
        sname = str(s.get("name", "")).lower()
        if not sname:
            continue
        if sname == name:
            exact.append(sid)
            seen.add(sid)
        elif len(name) >= 3 and (name in sname or sname in name):
            partial.append(sid)
            seen.add(sid)
        if len(exact) >= cap:
            break
    return (exact + partial)[:cap]


def build_file_verdict(
    *,
    present: bool,
    requested_path: Optional[str] = None,
    source_files: Optional[Sequence[str]] = None,
    index_stale: bool = False,
    empty_symbols: bool = False,
    index_changed: bool = False,
    freshness: Optional[str] = None,
) -> dict:
    """`_meta.verdict` for the file-read tools.

    * ``present=False`` — the path is not in the index: ``absent`` plus a
      ``did_you_mean`` list of near-miss paths.
    * ``present=True, empty_symbols=True`` — the file is indexed but yields no
      symbols (data/config file, or constructs the parser does not surface):
      ``absent`` with no suggestions, so the agent does not retry the outline.
    * otherwise — ``ok``.
    """
    if not present:
        state = STATE_ABSENT
        note = "Path is not in the index. " + _NOTES[STATE_ABSENT]
        suggestions = suggest_paths(requested_path, source_files)
    elif empty_symbols:
        state = STATE_ABSENT
        note = (
            "File is indexed but exposes no extractable symbols (a data/config "
            "file, or constructs the parser does not surface). Re-requesting the "
            "outline will not change this."
        )
        suggestions = []
    else:
        state = STATE_OK
        note = _NOTES[STATE_OK]
        suggestions = []
    if index_changed and state == STATE_ABSENT:
        # #93 class: a rebuild deletes and reinserts rows, so a present file
        # can read as missing — and an indexed file can transiently expose no
        # symbols — for the duration. Neither absence is provable here, and the
        # empty_symbols note ("re-requesting will not change this") would be
        # actively wrong mid-rewrite.
        state = STATE_DEGRADED
        note = _NOTES[STATE_DEGRADED]
        suggestions = []
    verdict = {
        "state": state,
        "channels": {
            "index": index_channel(
                index_changed=index_changed,
                index_stale=index_stale,
                freshness=freshness,
            )
        },
        "note": note,
    }
    if suggestions:
        verdict["did_you_mean"] = suggestions
    return verdict


def symbol_verdict_for_index(
    index,
    *,
    found_count: int,
    requested_id: Optional[str] = None,
    unavailable_source_count: int = 0,
) -> dict:
    """Index-aware wrapper over :func:`build_symbol_verdict`."""
    verdict = build_symbol_verdict(
        found_count=found_count,
        requested_id=requested_id,
        symbols=getattr(index, "symbols", None) if found_count == 0 else None,
        index_stale=_index_is_stale(index),
        index_changed=index_changed_since_load(index),
        unavailable_source_count=unavailable_source_count,
        freshness=_index_freshness(index),
    )
    _attach_coverage(verdict, index_coverage_meta(index))
    return verdict


def _index_source_files(index) -> list:
    """Best-effort list of indexed source paths (keys of ``file_languages``)."""
    langs = getattr(index, "file_languages", None)
    if isinstance(langs, dict):
        return list(langs.keys())
    return []


def index_changed_since_load(index) -> bool:
    """Whether the .db was rewritten since this index was loaded (never raises).

    Answers a question ``_index_is_stale`` cannot: that probe compares the
    stored git SHA against live HEAD, so it sees a repo that moved ON DISK but
    is blind to a reindex of an UNCHANGED tree — a watcher rebuild after an
    uncommitted edit reports ``fresh`` while rows are being rewritten.

    Deliberately a filesystem signal, not ``reindex_state``: that module is
    in-memory and per-process, so a server answering a search cannot see a
    reindex driven by a separate ``watch-all`` service. The .db/.db-wal mtime
    crosses process boundaries.

    Unknown is NOT changed: an index with no stamped provenance (a test double,
    a hand-built CodeIndex) returns False rather than degrading every verdict.
    """
    try:
        from ..storage.generation import describe

        return describe(index).rewritten_since_load
    except Exception:
        return False


def index_channel(
    *,
    index_changed: bool = False,
    coverage: Optional[dict] = None,
    index_stale: bool = False,
    freshness: Optional[str] = None,
) -> str:
    """The single expression that renders ``verdict.channels.index``.

    Extracted because there were THREE copies of it and they had drifted:
    :func:`build_verdict` honoured the #377 item 4 tri-state while
    :func:`build_file_verdict` and :func:`build_symbol_verdict` still carried
    the two-state ``"stale" if index_stale else "fresh"``. On an index whose
    freshness cannot be established, ``get_symbol_source`` /
    ``get_file_content`` / ``get_file_outline`` therefore reported
    ``channels.index: "fresh"`` in the same payload whose ``_meta.freshness``
    correctly said ``unknown`` — measured against ``django/django``, whose
    index carries an empty ``source_root``.

    Ordering is by how badly each condition undermines the answer: a rebuild in
    flight beats a known coverage gap beats a known lag. ``unknown`` and
    ``not_tracked`` sit below ``stale`` and above ``fresh``: a known lag is
    worse than an unestablished one, and both are worse than proven currency.

    ``freshness`` of ``None`` keeps a caller that supplies only the Boolean on
    exactly its previous two-state behaviour, so adding the parameter changes
    no existing result.
    """
    if index_changed:
        return "rebuilding"
    if coverage is not None and coverage_is_incomplete(coverage):
        return "partial"
    if index_stale:
        return "stale"
    if freshness in ("fresh", "stale", "unknown", "not_tracked"):
        return freshness
    return "fresh"


def _index_freshness(index) -> str:
    """Tri-state repo freshness for *index* (never raises).

    ⚠ Failure returns ``unknown``, NOT ``fresh``. The whole point of the
    tri-state is that "we could not find out" must not render as proof of
    currency, and an exception here is precisely that case.
    """
    try:
        from .freshness import FreshnessProbe

        probe = FreshnessProbe(
            source_root=getattr(index, "source_root", "") or None,
            indexed_at=getattr(index, "indexed_at", ""),
            index_sha=getattr(index, "git_head", None),
            file_mtimes=getattr(index, "file_mtimes", None),
        )
        return probe.repo_freshness
    except Exception:
        return "unknown"


def _index_is_stale(index) -> bool:
    """Whether the index SHA lags the live git HEAD (never raises)."""
    try:
        from .freshness import FreshnessProbe

        probe = FreshnessProbe(
            source_root=getattr(index, "source_root", "") or None,
            indexed_at=getattr(index, "indexed_at", ""),
            index_sha=getattr(index, "git_head", None),
            file_mtimes=getattr(index, "file_mtimes", None),
        )
        return probe.repo_is_stale
    except Exception:
        return False


def file_verdict_for_index(
    index,
    *,
    present: bool,
    requested_path: Optional[str] = None,
    empty_symbols: bool = False,
) -> dict:
    """Index-aware wrapper over :func:`build_file_verdict` for the file tools."""
    verdict = build_file_verdict(
        present=present,
        requested_path=requested_path,
        source_files=_index_source_files(index) if not present else None,
        index_stale=_index_is_stale(index),
        empty_symbols=empty_symbols,
        index_changed=index_changed_since_load(index),
        freshness=_index_freshness(index),
    )
    _attach_coverage(verdict, index_coverage_meta(index))
    return verdict


def build_symbol_verdict(
    *,
    found_count: int,
    requested_id: Optional[str] = None,
    symbols: Optional[Sequence[dict]] = None,
    index_stale: bool = False,
    index_changed: bool = False,
    unavailable_source_count: int = 0,
    freshness: Optional[str] = None,
) -> dict:
    """`_meta.verdict` for ``get_symbol_source``.

    ``found_count == 0`` yields ``absent`` plus ``did_you_mean`` symbol ids that
    share the requested name; any resolved symbol yields ``ok`` (a partial batch
    is still a hit).

    ``unavailable_source_count`` is the number of resolved symbols whose body
    could not be read back. Resolving a symbol and producing its source are two
    different successes, and an index can do the first without the second: the
    row lives in the ``.db`` while the bytes live in a separate content
    directory. When that directory is absent the tool used to return the row
    with ``source: ""`` under ``state: ok``, which asserts an answer it does not
    have. A body it cannot produce makes the result degraded.
    """
    if found_count == 0 and index_changed:
        # jdoc/jcm #93 class: a rebuild deletes and reinserts rows, so a
        # genuinely-present symbol reads as missing for the duration. Absence
        # is not provable against an index being rewritten.
        state = STATE_DEGRADED
        note = _NOTES[STATE_DEGRADED]
        suggestions = []
    elif found_count == 0:
        state = STATE_ABSENT
        note = "Symbol id is not in the index. " + _NOTES[STATE_ABSENT]
        suggestions = suggest_symbol_ids(requested_id, symbols)
    elif unavailable_source_count > 0:
        state = STATE_DEGRADED
        plural = "s" if unavailable_source_count != 1 else ""
        note = (
            f"{unavailable_source_count} symbol{plural} resolved but the body could not "
            "be read: the cached file content is missing for that path. Signatures, "
            "docstrings and line ranges are accurate; `source` is empty because it is "
            "unavailable, NOT because the symbol is empty. Re-index the repo to "
            "rebuild the content cache."
        )
        suggestions = []
    else:
        state = STATE_OK
        note = _NOTES[STATE_OK]
        suggestions = []
    verdict = {
        "state": state,
        "channels": {
            "index": index_channel(
                index_changed=index_changed,
                index_stale=index_stale,
                freshness=freshness,
            )
        },
        "note": note,
    }
    if unavailable_source_count > 0:
        verdict["channels"]["content_cache"] = "missing"
        verdict["unavailable_source_count"] = unavailable_source_count
    if suggestions:
        verdict["did_you_mean"] = suggestions
    return verdict
