"""Producer registration and canonical projectors (#377 Phase 2, P2).

An envelope without registration is a contract anything can mint into, which is
the defect this phase exists to fix. A tool must not be able to mint evidence by
returning a dict that happens to look right. A reviewed producer registers, once:

    verdict shape + scorer pin      proof kinds it may mint
    canonical projector            completeness semantics
    freshness semantics            coverage semantics
    integrity rules                snapshot fields

**This is where the hardest lesson from the 1.x hardening work goes.**
``get_ranked_context`` asserted "Do not claim this feature exists" from an early
return that never called ``build_verdict``, so eight shipped absence gates were
invisible to it until v1.108.179 went looking. An early return is a second
implementation of the answer and inherits nothing. Registration makes that
structural rather than remembered:

    minting requires a verdict of the shape this producer registered.

An exit that asserts absence without building a verdict therefore cannot mint —
not because it is listed as an exception, but because it never produced the
thing a receipt is derived from.

**The gate has now fired twice on its own author, which is the record worth
keeping.** In v1.108.183 three exits could not mint because they emitted no
verdict: ``search_symbols``' semantic and fusion paths, and
``get_ranked_context``'s fusion path. v1.108.184 gave the semantic one a verdict
and v1.108.185 gave both fusion ones a verdict — and each time that made the exit
mintable under a ``completeness`` declaration written for a different operation
("lexical BM25 over the inverted index", false for an embedding ranking and
imprecise for a four-channel fusion). Each time the pinning test failed, the mode
got reviewed, and it now declares its own completeness through
``completeness_by_mode``. A capability that reads enforced and is not is the
failure this whole phase exists to prevent, so the gate catching its own author is
the design working, not a nuisance.

The two reviews reached OPPOSITE conclusions, which is the point of reviewing
modes rather than porting a rule: a semantic zero result cannot prove absence
(it rests on embedding geometry), while a fusion zero result can (its lexical and
identity channels are complete passes, so an empty fused set is a corpus fact).

The canonical projector is also the structural fix for the class of bug the
hand-kept ``handoff._SCOPE_ARGS`` tuple keeps having (review item 12): a new
result-changing argument is added and nobody remembers to list it. Here each
producer declares its own narrowing and mode keys beside the exit that reads
them, and the projector emits a descriptor of the operation that actually ran
rather than leaving the finalizer to reverse-engineer semantics from caller
arguments.

Phase 2 does not BUILD the snapshot. Every field in it already ships:
``retrieval/subject_state.capture`` (generation, .db mtime, live HEAD),
``FreshnessProbe.repo_freshness`` (v1.108.180), ``verdict.index_coverage_meta``
(v1.108.176) and ``verdict.working_tree`` (v1.108.181). This module binds them
and derives the evidence id from them, so different snapshots cannot share an id.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Callable, Optional

from . import receipts

logger = logging.getLogger(__name__)

#: Receipts minted for one call. A batch of hundreds of symbols would otherwise
#: fill the whole session store from a single response. Truncation is DISCLOSED
#: in the carrier (`omitted`), never silent — a capped list that reads as
#: complete is worse than no list.
_MAX_PER_CALL = 25

#: Verdict shapes a producer may register against.
#: ``retrieval`` = ``verdict.build_verdict`` output (carries ``scanned`` and the
#: full channel set). ``symbol`` = ``verdict.build_symbol_verdict`` output
#: (channels + note; no scan counts, so it can never back an absence claim).
SHAPE_RETRIEVAL = "retrieval"
SHAPE_SYMBOL = "symbol"


def _norm_file(path) -> str:
    return str(path or "").replace("\\", "/").lstrip("./")


def _verdict_shape(verdict: Optional[dict]) -> Optional[str]:
    if not isinstance(verdict, dict) or "channels" not in verdict:
        return None
    if "scanned" in verdict:
        return SHAPE_RETRIEVAL
    return SHAPE_SYMBOL


@dataclass(frozen=True)
class Producer:
    """One reviewed exit family, and everything a receipt from it may assert."""

    tool: str
    #: Verdict shape this producer's registered exits emit. A result without one
    #: cannot mint, which is the early-return gate.
    verdict_shape: str
    #: Proof kinds this operation can support. Anything else is refused even if
    #: a caller or a future projector asks for it.
    proof_kinds: frozenset
    #: Argument keys that NARROW what was searched. "Not found" means nothing
    #: without the scope it was not found in.
    scope_args: tuple
    #: Argument keys that change WHICH operation ran (ranking mode, channels,
    #: thresholds). A literal search and a regex search of one string are
    #: different operations and must not collide on one id.
    mode_args: tuple
    #: How this producer knows its scan covered what it claims to have covered.
    #: This is the DEFAULT, meaning the exit that does not label its search mode.
    completeness: str
    #: Which freshness signal backs a claim from this producer.
    freshness: str
    #: What the coverage block means here.
    coverage: str
    #: What, if anything, this producer can attest about the bytes themselves.
    integrity: str
    #: Where the served rows live in the response, and how a subject is read
    #: from one. ``None`` for absence-only producers.
    rows_key: Optional[str] = None
    flat_row: bool = False
    id_field: str = "id"
    #: Extra per-producer guard on the result, beyond the verdict-shape gate.
    accepts: Optional[Callable] = None
    #: Per-MODE completeness, keyed by the response's own `_meta.search_mode`.
    #:
    #: Review item 20 asks for each mode to be reviewed rather than to inherit
    #: capability by accident, and a shared `completeness` string is precisely how
    #: that inheritance happens: v1.108.184 gave the semantic exit a real verdict,
    #: which made it mintable under a declaration that said "lexical BM25 over the
    #: inverted index" — a capability that reads enforced and is not. A mode that
    #: is not listed here uses the default, so adding a mode without reviewing it
    #: is a visible omission rather than a silent inheritance.
    completeness_by_mode: dict = field(default_factory=dict)
    snapshot_fields: tuple = field(
        default=(
            "index_generation",
            "indexed_revision",
            "live_revision",
            "freshness",
            "working_tree",
            "coverage_fingerprint",
            "scorer_version",
            "conditions",
        )
    )

    def may_mint(self, proof_kind: str) -> bool:
        return proof_kind in self.proof_kinds

    def completeness_for(self, search_mode: Optional[str]) -> str:
        """The completeness declared for the exit that actually ran."""
        if search_mode and search_mode in self.completeness_by_mode:
            return self.completeness_by_mode[search_mode]
        return self.completeness


#: What a fusion exit's zero result rests on, reviewed for v1.108.185 and shared
#: by both fusion producers because both build their channels the same way.
#:
#: Fusion CAN prove absence, and the reason is structural rather than a judgement
#: call. ``build_lexical_channel`` and ``build_identity_channel`` each score EVERY
#: eligible candidate with no cap, and ``fuse`` keeps every symbol that appears in
#: ANY channel — so an empty fused set means every candidate scored zero on both
#: BM25 and identity, which is the same corpus fact the lexical path's absence
#: rests on. The similarity channel can only ADD symbols, never remove one, so its
#: absence weakens the RANKING and cannot manufacture a false absence. That
#: asymmetry is precisely what the semantic exit lacked in v1.108.184, which is why
#: this mode gets no ``absence_unprovable`` and that one does.
_FUSION_COMPLETENESS = (
    "Weighted Reciprocal Rank over lexical BM25, identity, structural PageRank "
    "and (when embeddings exist) similarity; the lexical and identity channels "
    "each score every eligible candidate with no cap, so an empty fused set is "
    "the same corpus fact a lexical absence rests on. Candidate-cap and packing "
    "drops are disclosed as verdict.omitted"
)


PRODUCERS: dict[str, Producer] = {
    "get_symbol_source": Producer(
        tool="get_symbol_source",
        verdict_shape=SHAPE_SYMBOL,
        # Positive only, deliberately. Its "not in the index" answer carries no
        # scan counts, no coverage and no query, so it cannot back an absence
        # claim and is not registered to try.
        proof_kinds=frozenset({"symbol_definition"}),
        scope_args=(),
        mode_args=(
            "verify",
            "verify_against",
            "context_lines",
            "source_start_line",
            "source_end_line",
            "max_source_lines",
            "max_source_bytes",
            "max_total_source_bytes",
        ),
        completeness=(
            "exact id lookup: the subject is the symbol the caller named, so "
            "completeness is not a ranking question here"
        ),
        freshness="index revision vs live revision (FreshnessProbe.repo_freshness)",
        coverage="index coverage contract at the generation that served the body",
        integrity=(
            "content_sha256 is the index's stored hash of the full body; when the "
            "body was served, the served bytes are hashed and compared"
        ),
        rows_key="symbols",
        flat_row=True,
    ),
    "search_symbols": Producer(
        tool="search_symbols",
        verdict_shape=SHAPE_RETRIEVAL,
        proof_kinds=frozenset({"symbol_definition", "symbol_lookup_absence"}),
        scope_args=("kind", "file_pattern", "language", "decorator"),
        mode_args=(
            "sort_by",
            "fuzzy",
            "fuzzy_threshold",
            "max_edit_distance",
            "detail_level",
            "max_results",
            "token_budget",
            # v1.108.184: a semantic search and a lexical search of the same
            # string are different operations and must not collide on one evidence
            # id. `channels.semantic` alone could not tell hybrid from
            # semantic_only, and the weight changes the ranking outright.
            "semantic",
            "semantic_only",
            "semantic_weight",
            # v1.108.185: a fused search is a different operation again.
            "fusion",
        ),
        completeness=(
            "lexical BM25 over the inverted index, with the full symbol set as "
            "the fallback candidate basis; the response's own packing is "
            "disclosed as verdict.omitted"
        ),
        completeness_by_mode={
            # Reviewed for v1.108.184, when this exit gained a real verdict. It
            # scores EVERY filtered symbol rather than narrowing through the
            # inverted index, so its candidate basis is wider — and its ordering
            # is embedding cosine, which is why a zero result here carries
            # verdict.absence_unprovable and can never reach `absent`.
            "hybrid": (
                "hybrid BM25 + embedding cosine over every filtered symbol (no "
                "inverted-index narrowing); ranked by similarity, so this "
                "receipt speaks for what was returned and never for what was not"
            ),
            "semantic_only": (
                "embedding cosine alone over every filtered symbol, with the "
                "lexical channel switched off; ranked by similarity, so this "
                "receipt speaks for what was returned and never for what was not"
            ),
            # Reviewed for v1.108.185, when both fusion exits gained a verdict.
            "fusion": _FUSION_COMPLETENESS,
        },
        freshness="FreshnessProbe.repo_freshness, four-state (v1.108.180)",
        coverage="index_coverage_meta; complete is tri-state and null never reads as true",
        integrity=(
            "row identity only unless detail_level='full' served the body, in "
            "which case the served bytes are hashed"
        ),
        rows_key="results",
    ),
    "get_ranked_context": Producer(
        tool="get_ranked_context",
        verdict_shape=SHAPE_RETRIEVAL,
        proof_kinds=frozenset({"symbol_definition", "symbol_lookup_absence"}),
        scope_args=("scope", "include_kinds"),
        mode_args=("strategy", "token_budget", "compress", "fusion"),
        completeness=(
            "BM25 + PageRank over the candidate basis, with the candidate count "
            "disclosed as verdict.omitted; BOTH exits build the verdict since "
            "v1.108.179"
        ),
        completeness_by_mode={
            # Reviewed for v1.108.185. ⚠ This tool's fusion exit builds NO
            # similarity channel at all — lexical, identity and structural only.
            "fusion": _FUSION_COMPLETENESS,
        },
        freshness="FreshnessProbe.repo_freshness, four-state (v1.108.180)",
        coverage="index_coverage_meta; complete is tri-state and null never reads as true",
        integrity="served body bytes are hashed; a compressed or pruned body is named as a slice",
        rows_key="context_items",
        id_field="symbol_id",
    ),
    "search_text": Producer(
        tool="search_text",
        verdict_shape=SHAPE_RETRIEVAL,
        # Absence only for now, and said plainly rather than quietly: a text hit
        # is a (file, line, matched text) tuple, not a symbol definition, and
        # giving it a positive proof kind would mean inventing one whose subject
        # nothing downstream knows how to compare. The absence half is the
        # valuable one and is the half Phase 3 already tokenizes.
        proof_kinds=frozenset({"literal_text_absence"}),
        scope_args=("file_pattern",),
        mode_args=("is_regex", "max_results", "context_lines"),
        completeness=(
            "walks the cached file bodies in scope; files it could not open are "
            "disclosed as verdict.incomplete.unreadable_inputs, and an empty "
            "eligible set as empty_scope (v1.108.177)"
        ),
        freshness="FreshnessProbe.repo_freshness, four-state (v1.108.180)",
        coverage="index_coverage_meta; complete is tri-state and null never reads as true",
        integrity=(
            "no byte attestation: an absence serves nothing to hash; the "
            "matching lines and any `sources` a page carries are not hashed"
        ),
    ),
}


def registered(tool: str) -> Optional[Producer]:
    """The reviewed producer for *tool*, or None.

    None means no receipts, never a broken call. Registration is additive per
    producer, so the gate cannot block a legitimate caller — it can only decline
    to mint for an exit nobody has reviewed yet.
    """
    return PRODUCERS.get(tool)


def _repo_identity(index, repo, owner: str = "", name: str = "") -> str:
    """Resolved identity, not the caller's argument string.

    ``.``, an alias, a path and a worktree can all name one repo (review item
    15), so a receipt is bound to what the server resolved. ``display_name`` is
    deliberately NOT consulted: it is a display value, it is not unique across
    indexed folders, and identity must never be one (review item 21 — redaction
    changes the rendered form, never the identity).
    """
    repo_id = getattr(index, "repo_id", None)
    if isinstance(repo_id, str) and repo_id:
        return repo_id
    if owner and name:
        return f"{owner}/{name}"
    return str(repo or "")


def _repo_freshness(index) -> str:
    """The four-state freshness for this index, probed directly. Never raises."""
    try:
        from ..retrieval.freshness import FreshnessProbe

        return FreshnessProbe(
            source_root=getattr(index, "source_root", "") or None,
            indexed_at=getattr(index, "indexed_at", ""),
            index_sha=getattr(index, "git_head", None),
            file_mtimes=getattr(index, "file_mtimes", None),
        ).repo_freshness
    except Exception:
        logger.debug("Freshness probe failed while minting a receipt", exc_info=True)
        return "unknown"


def _snapshot(index, verdict: dict, coverage: Optional[dict], *, trust_channel: bool) -> dict:
    """Bind the shipped snapshot primitives. Builds nothing new."""
    from ..retrieval import subject_state as _subject
    from ..retrieval.verdict import SCORER_VERSION
    from ..storage.generation import describe

    # #398 Arc 1: the generation half now comes from the one contract rather
    # than from a second, differently-normalised reading of the index object.
    # `capture` is still what probes the LIVE revision (a git subprocess behind
    # a TTL cache), which is a property of the subject rather than of the
    # index, so it is not part of the generation contract and stays here.
    gen = describe(index)
    state = _subject.capture(index)
    channel = ((verdict.get("channels") or {}).get("index")) or ""
    # channels.index also carries `rebuilding` and `partial`, which are not
    # freshness values — they are corpus conditions, and collapsing them into a
    # freshness word would be the v1.108.180 mistake in reverse. They stay in
    # `channels` and are named in `limitations`.
    #
    # `trust_channel` is the other half, and it matters: `build_symbol_verdict`
    # never got the item-4 four-state treatment, so ITS channels.index says
    # "fresh" for a plain indexed folder that has no revision at all. Reading
    # that as freshness would be the v1.108.176 mistake — believing a signal that
    # answers a slightly different question than the one it is read as. Only the
    # retrieval-shaped verdict, which is built from `repo_freshness`, is trusted
    # here; everything else probes.
    if trust_channel and channel in ("fresh", "stale", "unknown", "not_tracked"):
        freshness = channel
    else:
        freshness = _repo_freshness(index)
    tree = verdict.get("working_tree") or {}
    return {
        "index_generation": gen.generation,
        # Empty string is not a revision. It means the index stored none, which
        # is `unknown`, and null is how this envelope says that. The
        # normalisation moved into `storage.generation` so it happens once.
        "indexed_revision": gen.indexed_revision,
        "live_revision": state.get("live_sha") or None,
        "freshness": freshness,
        # Measured only for a zero-result scan (probing the tree on every search
        # would price a subprocess into answers that do not need it), so a
        # positive receipt reports `unknown` — which is what it is, not `clean`.
        "working_tree": tree.get("state") or "unknown",
        # v1.108.221: bound to the CAPABILITY certificate, not the coverage
        # block alone. ⚠ This changes minted evidence ids, deliberately: two
        # installs with different grammar packs or file-size limits index the
        # same commit differently and both report `complete: true`, so their
        # receipts previously shared a fingerprint while describing different
        # corpora. Ids are session-scoped and in memory, so nothing stored
        # breaks; what changes is that a receipt now attests the corpus it was
        # actually measured against.
        "coverage_fingerprint": receipts.coverage_fingerprint(coverage, index),
        "scorer_version": SCORER_VERSION,
        "conditions": _conditions(verdict, freshness, coverage),
    }


def _project(producer: Producer, arguments: dict, result: dict) -> dict:
    """The canonical projector: what operation actually ran.

    Not the raw caller input. Absent and empty-valued keys are dropped so a call
    that passes ``kind=None`` cannot mint a different id from one that omits it,
    and everything that survives is something that changes the answer.
    """
    args = arguments or {}

    def _present(keys):
        out = {}
        for key in keys:
            val = args.get(key)
            if val not in (None, "", [], {}):
                out[key] = val
        return out

    projected = {
        "tool": producer.tool,
        "scope": _present(producer.scope_args),
        "mode": _present(producer.mode_args),
    }
    query = args.get("query")
    if isinstance(query, str) and query.strip():
        projected["query"] = query.strip()
    meta = result.get("_meta") or {}
    if producer.tool == "search_symbols":
        # The fuzzy pass runs when explicitly requested OR when the best BM25
        # score falls below the near-miss threshold, so `fuzzy=False` in the
        # arguments does not mean it did not run. Rows carry `match_type` when it
        # did, and a pass that changed which rows came back belongs in identity.
        rows = result.get("results") or []
        if any(isinstance(r, dict) and r.get("match_type") for r in rows):
            projected["mode"]["fuzzy_pass_ran"] = True
    if isinstance(meta.get("exact_match"), dict):
        projected["mode"]["exact_match"] = bool(meta["exact_match"].get("found"))
    if isinstance(meta.get("search_mode"), str):
        # The exit labels its own mode, so identity records what actually ran
        # rather than what the arguments asked for.
        projected["mode"]["search_mode"] = meta["search_mode"]
    channels = (result.get("_meta") or {}).get("verdict", {}).get("channels") or {}
    if channels.get("semantic") and channels["semantic"] != "off":
        projected["mode"]["semantic"] = channels["semantic"]
    return projected


def _row_subject(row: dict, producer: Producer, repo_identity: str):
    """``(subject, integrity, limitations)`` from ONE served row.

    Read from the served row only, never re-read from the index. A receipt
    describes what the caller was handed: if the row carried no end line, the
    receipt says so instead of quietly filling one in from a lookup the caller
    never saw.
    """
    sid = row.get(producer.id_field) or row.get("id")
    if not isinstance(sid, str) or not sid:
        return None, None, None
    subject: dict = {"repo_identity": repo_identity, "symbol_id": sid}
    limitations: list = []
    file_path = _norm_file(row.get("file") or sid.split("::", 1)[0])
    if file_path:
        subject["file"] = file_path
    line = row.get("line")
    if isinstance(line, int):
        subject["start_line"] = line
    else:
        limitations.append("start_line was not served with this row")
    end_line = row.get("end_line")
    if isinstance(end_line, int):
        subject["end_line"] = end_line
    else:
        limitations.append(
            "end_line was not served with this row, so the receipt binds a "
            "start line but not an exact range"
        )

    integrity: dict = {}
    source = row.get("source")
    bounded = bool(
        row.get("source_is_bounded_view")
        or row.get("source_truncated")
        or row.get("pruned")
        or row.get("prune_reason")
    )
    stored = row.get("content_hash")
    if isinstance(stored, str) and stored:
        subject["content_sha256"] = stored
        integrity["hash_source"] = "index_content_hash"
    if isinstance(source, str) and source and not bounded:
        served = hashlib.sha256(source.encode("utf-8")).hexdigest()
        integrity["served_bytes_sha256"] = served
        if "content_sha256" in subject:
            integrity["content_hash_matches_served_bytes"] = served == subject["content_sha256"]
        else:
            subject["content_sha256"] = served
            integrity["hash_source"] = "served_bytes"
    elif bounded:
        integrity["served_bytes_sha256"] = None
        limitations.append(
            "the body served with this row is a bounded or pruned view, so the "
            "served bytes were not hashed and this receipt attests identity, "
            "not the bytes the caller received"
        )
    if "content_sha256" not in subject:
        limitations.append(
            "no content hash is available for this row, so the receipt binds a "
            "symbol identity and a snapshot but cannot prove the body's bytes"
        )
    if row.get("match_type") and row["match_type"] != "exact":
        limitations.append(
            f"this row reached the response through the {row['match_type']} pass, "
            "so its RANKING is approximate; its identity is not"
        )
    return subject, integrity, limitations


#: What each snapshot condition costs the receipt. Keys are the condition tokens
#: recorded in ``snapshot.conditions``; values are the sentence that goes in
#: ``limitations``.
#:
#: The mapping is the point, not a convenience. Because every limitation is
#: derived from a token that is itself INSIDE the identity payload, two receipts
#: with the same evidence id necessarily carry the same limitations — so the
#: fail-closed collision check can compare whole envelopes without a moved
#: subject presenting as a hash collision. Getting this wrong the other way round
#: is worse than it sounds: a cached replay whose verdict gained `revalidated`
#: would have produced one id with two different bodies, and fail-closed would
#: then have deleted a receipt an agent was already holding.
_CONDITION_LIMITS: dict = {
    "not_tracked": (
        "the subject has no revision control, so this receipt cannot claim the "
        "index matches any particular revision of it, and never will"
    ),
    "freshness_unknown": (
        "index freshness could not be established, so whether this snapshot "
        "describes the current tree is unknown"
    ),
    "stale": (
        "the index lagged the live revision at this call, so this receipt "
        "describes an older tree than the one being audited"
    ),
    "rebuilding": (
        "the index was being rewritten during this call, so rows may have been "
        "read from a generation in mid-publication"
    ),
    "partial": (
        "the corpus had known gaps at scan time, so this receipt cannot speak "
        "for files that never entered the index"
    ),
    "no_coverage_contract": (
        "no coverage contract was recorded for this index, so what the corpus "
        "excluded is unknown rather than empty"
    ),
    "semantic_unavailable": (
        "a requested semantic channel was unavailable, so this response is not "
        "what the call asked for"
    ),
    "replayed_from_stale_cache": (
        "this response was replayed from cache and the subject it was measured "
        "against has since moved"
    ),
    "moved_during_scan": (
        "the subject moved while the scan ran, so it describes neither the state "
        "it started against nor the one it finished in"
    ),
    "incomplete_inputs": (
        "the scan did not cover its own eligible inputs (verdict.incomplete)"
    ),
    "omitted_matches": (
        "matches existed before the response was packed and did not fit it "
        "(verdict.omitted), so this response is not the whole result"
    ),
}


def _conditions(verdict: dict, freshness: str, coverage: Optional[dict]) -> list:
    """Sorted condition tokens for this response.

    Recorded IN the snapshot, so a subject measured under different conditions
    gets a different evidence id — which is what "different snapshot, different
    id" has to mean if the conditions are the thing that changed.
    """
    found: set = set()
    if freshness in ("not_tracked", "stale"):
        found.add(freshness)
    elif freshness == "unknown":
        found.add("freshness_unknown")
    channels = verdict.get("channels") or {}
    if channels.get("index") in ("rebuilding", "partial"):
        found.add(channels["index"])
    if channels.get("semantic") == "unavailable":
        found.add("semantic_unavailable")
    if coverage is None:
        found.add("no_coverage_contract")
    if (verdict.get("revalidated") or {}).get("stale_cache"):
        found.add("replayed_from_stale_cache")
    if verdict.get("moved_during_scan"):
        found.add("moved_during_scan")
    if verdict.get("incomplete"):
        found.add("incomplete_inputs")
    if verdict.get("omitted"):
        found.add("omitted_matches")
    return sorted(found)


def mint(
    tool: str,
    arguments: Optional[dict],
    result,
    repo,
    storage_path: Optional[str] = None,
) -> Optional[dict]:
    """Mint receipts for one registered producer's response.

    Returns the ``_meta.receipts`` carrier, or None when nothing was minted. A
    caller never sees an error from here: an unregistered tool, an unreviewed
    exit, a missing index or a refused proof kind all mean "no receipt", which
    is the safe outcome by construction.
    """
    producer = registered(tool)
    if producer is None or not isinstance(result, dict) or "error" in result:
        return None
    verdict = (result.get("_meta") or {}).get("verdict")
    if _verdict_shape(verdict) != producer.verdict_shape:
        # The early-return gate. An exit that asserts an answer without building
        # the verdict this producer registered has not been reviewed for
        # evidence capability and cannot mint.
        logger.debug(
            "No receipt for %s: verdict shape %r does not match registered %r",
            tool,
            _verdict_shape(verdict),
            producer.verdict_shape,
        )
        return None
    if producer.accepts is not None and not producer.accepts(result):
        return None

    try:
        from ..storage import IndexStore
        # `tools._utils.resolve_repo` is the (repo -> owner, name) helper every
        # tool uses. `tools.resolve_repo.resolve_repo` is the TOOL of the same
        # name and takes a filesystem path — a genuinely easy mix-up, so the
        # import stays explicit about which one this is.
        from ..tools._utils import resolve_repo as _resolve

        owner, name = _resolve(repo, storage_path)
        index = IndexStore(base_path=storage_path).load_index(owner, name)
    except Exception:
        logger.debug("Receipt minting could not resolve the index for %r", repo, exc_info=True)
        return None
    if index is None:
        return None

    from ..retrieval.verdict import index_coverage_meta as _coverage_meta

    coverage = verdict.get("coverage") or _coverage_meta(index)
    snapshot = _snapshot(
        index,
        verdict,
        coverage,
        trust_channel=(producer.verdict_shape == SHAPE_RETRIEVAL),
    )
    effective = _project(producer, arguments or {}, result)
    search_mode = (result.get("_meta") or {}).get("search_mode")
    identity = _repo_identity(index, repo, owner, name)
    channels = dict(verdict.get("channels") or {})
    # Every condition limitation comes from a token that is inside the identity
    # payload, so limitations can never diverge between two receipts sharing an id.
    condition_limits = [
        _CONDITION_LIMITS[token]
        for token in snapshot["conditions"]
        if token in _CONDITION_LIMITS
    ]

    ids: list[str] = []
    omitted = 0
    kinds: set = set()

    # --- positive receipts: one per served row -------------------------------
    if producer.rows_key and producer.may_mint("symbol_definition"):
        rows = result.get(producer.rows_key)
        if rows is None and producer.flat_row and result.get(producer.id_field):
            rows = [result]
        rows = [r for r in (rows or []) if isinstance(r, dict)]
        for row in rows[:_MAX_PER_CALL]:
            subject, integrity, limits = _row_subject(row, producer, identity)
            if subject is None:
                continue
            envelope = receipts.build_envelope(
                proof_kind="symbol_definition",
                subject=subject,
                effective_search=effective,
                snapshot=snapshot,
                channels=channels,
                coverage=coverage,
                integrity=integrity,
                capabilities={
                    "proves_symbol_identity": True,
                    "proves_body_bytes": bool(
                        integrity.get("served_bytes_sha256")
                        or integrity.get("hash_source") == "index_content_hash"
                    ),
                    "proves_absence": False,
                    "completeness": producer.completeness_for(search_mode),
                    "freshness_semantics": producer.freshness,
                    "coverage_semantics": producer.coverage,
                    "integrity_semantics": producer.integrity,
                },
                limitations=(limits or []) + condition_limits,
            )
            eid = receipts.record_receipt(envelope)
            if eid:
                ids.append(eid)
                kinds.add("symbol_definition")
        omitted += max(0, len(rows) - _MAX_PER_CALL)

    # --- absence receipts ----------------------------------------------------
    absence_kind = next(
        (k for k in sorted(producer.proof_kinds) if receipts.is_absence_kind(k)), None
    )
    if absence_kind and verdict.get("state") in ("absent", "low_confidence", "degraded"):
        query = (arguments or {}).get("query")
        if isinstance(query, str) and query.strip():
            from .. import handoff as _handoff

            # The SAME ref note_absence recorded under, so citability is decided
            # by handoff.absence_refusal over that one record. A receipt does not
            # re-implement the refusal rules; it points at the scan they judge.
            absence_ref = _handoff.absence_ref_for(tool, repo, query, arguments)
            record = _handoff.absence_record(absence_ref)
            refusal = _handoff.absence_refusal(record) if record else None
            envelope = receipts.build_envelope(
                proof_kind=absence_kind,
                subject={"repo_identity": identity, "query": query.strip()},
                effective_search=effective,
                snapshot=snapshot,
                channels=channels,
                coverage=coverage,
                integrity={"scanned": verdict.get("scanned") or {}},
                capabilities={
                    "proves_symbol_identity": False,
                    "proves_body_bytes": False,
                    # Advisory only. Finalization re-derives this by running the
                    # SAME refusal rules over the frozen record below, so there
                    # is exactly one implementation of them and a receipt read
                    # hours later cannot disagree with the gate.
                    "proves_absence": refusal is None,
                    "completeness": producer.completeness_for(search_mode),
                    "freshness_semantics": producer.freshness,
                    "coverage_semantics": producer.coverage,
                    "integrity_semantics": producer.integrity,
                },
                limitations=(
                    ([f"this scan cannot prove absence: {refusal}"] if refusal else [])
                    + condition_limits
                ),
                absence_ref=absence_ref,
                # v1.108.192 (#377 P3): freeze the scan INTO the receipt. The
                # ref's key carries no snapshot, so a later scan of the same
                # query overwrites the record it points at.
                absence_record=record,
            )
            eid = receipts.record_receipt(envelope)
            if eid:
                ids.append(eid)
                kinds.add(absence_kind)

    if not ids:
        return None
    carrier = {
        "schema": receipts.EVIDENCE_SCHEMA,
        "count": len(ids),
        "ids": ids,
        "proof_kinds": sorted(kinds),
        "resource_uri_prefix": receipts.EVIDENCE_URI_PREFIX,
    }
    if omitted:
        carrier["omitted"] = omitted
        carrier["omitted_note"] = (
            f"{omitted} further served row(s) did not get a receipt: at most "
            f"{_MAX_PER_CALL} are minted per call. Narrow the request to receipt "
            "the rest."
        )
    return carrier
