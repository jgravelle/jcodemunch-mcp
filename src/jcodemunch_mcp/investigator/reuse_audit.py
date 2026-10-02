"""Reuse-before-write as proof obligations rather than a keyword search.

The claim under test is always the same shape:

    "Nothing in <repo> already implements <intent>, so writing it new is
    justified."

That claim is refused until every obligation below is either satisfied or
refuted, and an obligation nobody could settle can never produce a
``write_justified`` verdict. Same rule, same single enforcement point, as
:mod:`.deletion_safety`.

Why this exists
---------------
The generation-time failure is the mirror image of the deletion-time one. An
agent about to write a date formatter has every tool it needs to discover that
``formatIsoDate`` already exists, and does not look, because nothing raised the
obligation. The result is a second implementation, and the cost is not the
tokens spent writing it — it is that the repository now has two.

``search_symbols`` can already find the existing one. It answers "what matches
this query?". This answers "may I write this?", which is a different question
with three properties the search does not have:

1. **Absence must be provable before it is asserted.** A stale index, a
   truncated corpus, or a rebuild in flight all make "nothing matches" a
   statement about our reading rather than about the repository. Those states
   downgrade the verdict instead of silently passing.
2. **A match that is itself dead is not a reuse candidate.** Pointing a writer
   at an unreferenced helper does not prevent duplication; it doubles the dead
   code. Every candidate's liveness is established before it is offered.
3. **The semantic channel is load-bearing and often absent.** "modal" finding
   an existing ``Dialog`` is an embedding result, not a lexical one. When no
   embedding provider is configured, or the repository was never embedded, a
   lexical sweep that finds nothing has NOT ruled out the synonym case — and
   the verdict says so rather than reporting the same clean bill of health it
   would give a fully-searched repository.

Point 3 is the whole reason this is not a search wrapper. A tool that reports
"no existing implementation" identically whether or not it could see synonyms
is wrong exactly when the writer most needs it to be right.

Charter
-------
Read-only. This plans and reads. It never edits, executes, or generates code.
"""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from ..tools._test_paths import is_test_file as _is_test_path

from .deletion_safety import (
    Obligation,
    REFUTED,
    SATISFIED,
    UNESTABLISHED,
)

# Evidence channels. Which signal could have settled the obligation, so the
# verdict can distinguish "we searched every way we can and found nothing" from
# "the only channel that could have found a synonym was switched off".
LEXICAL = "lexical"
SEMANTIC = "semantic"
STRUCTURAL = "structural"

# Verdicts
REUSE_AVAILABLE = "reuse_available"    # a live implementation already exists
ADAPT_CANDIDATE = "adapt_candidate"    # related work exists; extend, don't restart
WRITE_JUSTIFIED = "write_justified"    # searched every channel, nothing is there
LEXICAL_ONLY = "lexical_only"          # lexically clear; synonyms NOT ruled out
NOT_ESTABLISHED = "not_established"    # something answerable went unanswered

#: Seeded, not calibrated. These pick which bucket a candidate lands in; they
#: do not decide anything on the caller's behalf, because both buckets are
#: returned with their evidence either way. Exposed as parameters so a repo can
#: move them without editing this file, and named here so a future measurement
#: has something to move.
_STRONG_MATCH = 0.80    # at or above: refutes "nothing already does this"
_ADAPT_FLOOR = 0.55     # between the two: related, worth reading before writing

#: How many candidates to establish liveness for. Each costs one index round
#: trip, and a writer does not read a twelfth candidate.
_MAX_CANDIDATES = 8

#: Tokens that carry no retrieval signal in a one-line intent. Deliberately
#: short: this strips the phrasing an intent arrives wrapped in ("I need a
#: function that ..."), not domain words.
_INTENT_STOPWORDS = frozenset({
    "a", "an", "and", "add", "another", "any", "are", "as", "at", "be", "build",
    "by", "can", "code", "create", "do", "does", "for", "from", "function",
    "get", "handle", "handles", "have", "helper", "i", "if", "implement", "in",
    "into", "is", "it", "make", "me", "method", "need", "needs", "new", "of",
    "on", "or", "own", "should", "so", "some", "that", "the", "then", "there",
    "this", "to", "up", "use", "using", "utility", "want", "was", "we", "what",
    "when", "which", "will", "with", "would", "write", "you",
})

_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CAMEL_SPLIT_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


@dataclass
class Candidate:
    """One existing symbol that might already do what the caller is about to write.

    ``live`` is tri-state on purpose. ``False`` means we established the symbol
    has no references; ``None`` means we could not find out. Collapsing the two
    would either hide a dead candidate behind "unknown" or, worse, report an
    unchecked candidate as live — which is the exact shape of claim this module
    exists to refuse.
    """

    symbol_id: str
    name: str
    kind: str
    file: str
    line: int
    channel: str
    strength: float
    signature: str = ""
    summary: str = ""
    live: Optional[bool] = None
    reference_count: Optional[int] = None
    why: str = ""

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "symbol_id": self.symbol_id,
            "name": self.name,
            "kind": self.kind,
            "file": self.file,
            "line": self.line,
            "channel": self.channel,
            "match_strength": round(self.strength, 3),
            "live": self.live,
            "why": self.why,
        }
        if self.signature:
            d["signature"] = self.signature
        if self.summary:
            d["summary"] = self.summary
        if self.reference_count is not None:
            d["reference_count"] = self.reference_count
        return d


@dataclass
class _Channels:
    """What each retrieval channel was actually able to do, and why."""

    lexical: str = "off"
    semantic: str = "off"
    structural: str = "off"
    notes: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {
            "lexical": self.lexical,
            "semantic": self.semantic,
            "structural": self.structural,
        }
        if self.notes:
            d["notes"] = self.notes
        return d


def _squash(score: float, ceiling: float) -> float:
    """Soft-squash a raw score to 0-1 against its own scorer's ceiling.

    The same curve ``retrieval.confidence`` uses for its ``strength`` signal,
    and reused rather than re-derived for the reason that module's docstring
    gives at length: a threshold applied to raw scores grades units, not
    quality. BM25 scores in the tens and a cosine is bounded at 1.0, so a
    single cut-off across both channels would mean two different things.
    """
    if score <= 0 or ceiling <= 0:
        return 0.0
    return 1.0 - math.exp(-3.0 * score / ceiling)


def _intent_terms(intent: str) -> list[str]:
    """Content words from a free-text intent, camelCase and snake_case split.

    'render a Modal dialog' and 'renderModalDialog' must reduce to the same
    bag, or the caller's phrasing decides whether the search works.
    """
    out: list[str] = []
    seen: set[str] = set()
    for raw in _WORD_RE.findall(intent or ""):
        for part in _CAMEL_SPLIT_RE.split(raw):
            for piece in part.split("_"):
                t = piece.lower()
                if len(t) < 2 or t in _INTENT_STOPWORDS or t in seen:
                    continue
                seen.add(t)
                out.append(t)
    return out


def _identifier_forms(terms: list[str]) -> list[str]:
    """Identifier spellings a writer would plausibly reach for.

    Only the shapes that are cheap to check and common in real code. This is a
    name probe, not a generator: missing a spelling costs one weaker channel,
    and the lexical and semantic obligations still run.
    """
    if not terms:
        return []
    head, *rest = terms
    camel = head + "".join(t.capitalize() for t in rest)
    forms = [
        "".join(t.capitalize() for t in terms),   # PascalCase
        camel,                                    # camelCase
        "_".join(terms),                          # snake_case
    ]
    if len(terms) == 1:
        forms.append(terms[0])
    out: list[str] = []
    for f in forms:
        if f and f not in out:
            out.append(f)
    return out


def _index_was_rewritten(index) -> bool:
    """Sample the .db-rewritten probe. Never raises; unknown is not changed.

    ⚠⚠ Sample this BEFORE any channel runs, and never after. The probe is a
    .db/-wal mtime comparison, and our own semantic channel MOVES that mtime:
    the embedding read opens a read-write connection, which runs PRAGMA and
    CREATE TABLE and touches the file (the same read-write-versus-readonly
    split ``embedding_store`` documents across its five read paths). Sampled
    afterwards it reports True on every run in which the synonym channel was
    available -- so the one verdict that requires that channel,
    ``write_justified``, could never be reached, and it would be blocked by a
    rewrite this module performed itself.

    That is not a stricter guard, it is a broken proxy. A file mtime stands in
    for "rows were rewritten"; our own connection is a known false positive for
    it, and excluding a known false positive is repairing the proxy rather than
    relaxing it.

    What the earlier sample can and cannot see is stated in the payload rather
    than left to be inferred: a rebuild already in flight when the
    investigation starts is caught, and one that starts mid-scan is not.
    """
    from ..retrieval.verdict import index_changed_since_load  # noqa: PLC0415

    try:
        return bool(index_changed_since_load(index))
    except Exception:  # pragma: no cover - defensive
        return False


def _absence_blockers(index, *, index_changed: bool) -> list[str]:
    """Reasons this index cannot prove that something is absent.

    Each entry is a state in which "we looked and it is not there" describes
    our reading rather than the repository. Reused wholesale from the retrieval
    verdict contract so this module cannot drift from what the search tools
    already tell callers about the same index.

    ``index_changed`` is passed in rather than sampled here; see
    :func:`_index_was_rewritten` for why the sample point is load-time.
    """
    from ..retrieval.verdict import (  # noqa: PLC0415
        _index_is_stale,
        coverage_is_incomplete,
        index_coverage_meta,
        index_truncation_meta,
    )

    blockers: list[str] = []
    if index_changed:
        blockers.append(
            "the index was already being rewritten when this scan started, so "
            "rows may have been written after we passed them"
        )
    try:
        if coverage_is_incomplete(index_coverage_meta(index)):
            blockers.append(
                "the indexed corpus is known to be missing files, so an "
                "unmatched intent may sit in a file that was never scanned"
            )
    except Exception:  # pragma: no cover - defensive
        pass
    try:
        if index_truncation_meta(getattr(index, "file_cap_status", None)):
            blockers.append(
                "the max_folder_files cap dropped files at index time"
            )
    except Exception:  # pragma: no cover - defensive
        pass
    try:
        if _index_is_stale(index):
            blockers.append(
                "the index lags live HEAD, so an implementation added since "
                "the last index would not be found"
            )
    except Exception:  # pragma: no cover - defensive
        pass
    return blockers


def _semantic_state(store, owner: str, name: str) -> tuple[str, str]:
    """(state, note) for the semantic channel: provider AND corpus, separately.

    Two different failures wear the same "no semantic results" face, and they
    need opposite advice. ``pip install`` is wrong for a repo that has an
    encoder and was never embedded; ``embed_repo`` is wrong for one that was
    embedded by a provider that is no longer installed. Same distinction
    ``find_similar_symbols`` had to learn (#398).
    """
    from ..retrieval.verdict import _semantic_provider_available  # noqa: PLC0415

    if not _semantic_provider_available():
        return "no_provider", (
            "No embedding provider is configured, so a synonym match "
            "(an intent of 'modal' against an existing 'Dialog') could not be "
            "attempted. Install jcodemunch-mcp[local-embed] or set an embedding "
            "provider, then re-run before treating absence as established."
        )
    try:
        from ..storage.embedding_store import EmbeddingStore  # noqa: PLC0415

        emb = EmbeddingStore(store._sqlite._db_path(owner, name))
        any_vectors = emb.has_any()
        if any_vectors is False:
            return "repo_not_embedded", (
                "An embedding provider is configured but this repository has no "
                "vectors, so the synonym channel had nothing to search. Run "
                "embed_repo, then re-run."
            )
        if any_vectors is None:
            # `has_any` is tri-state and `None` means it could not find out --
            # a locked file, a corrupt page, a permission error. Falling
            # through would run a semantic search whose empty result is
            # indistinguishable from a clean sweep, and SATISFY the one
            # obligation this module exists to keep honest.
            return "unknown", (
                "The embedding store could not be read, so it is not known "
                "whether this repository has vectors at all. Treat the semantic "
                "channel as unavailable rather than clean."
            )
    except Exception:  # pragma: no cover - defensive
        return "unknown", (
            "The embedding store could not be inspected; treat the semantic "
            "channel as unavailable rather than clean."
        )
    return "used", ""


def _collect(
    rows: list,
    *,
    channel: str,
    ceiling: float,
    why: str,
    include_tests: bool,
    into: dict,
) -> None:
    """Fold ranked search rows into the candidate map, keeping the best channel.

    A symbol found both lexically and semantically keeps whichever channel
    scored it higher, so ``channel`` on the returned candidate names the signal
    that actually carried it rather than whichever search happened to run last.
    """
    for row in rows or []:
        sid = row.get("id") or ""
        if not sid:
            continue
        path = row.get("file", "") or ""
        if not include_tests and _is_test_path(path):
            continue
        strength = _squash(float(row.get("score", 0.0) or 0.0), ceiling)
        existing = into.get(sid)
        if existing is not None and existing.strength >= strength:
            continue
        into[sid] = Candidate(
            symbol_id=sid,
            name=row.get("name", "") or "",
            kind=row.get("kind", "") or "",
            file=path,
            line=int(row.get("line", 0) or 0),
            channel=channel,
            strength=strength,
            signature=(row.get("signature") or "").strip(),
            summary=(row.get("summary") or "").strip(),
            why=why,
        )


def _establish_liveness(
    repo: str,
    candidates: list,
    storage_path: Optional[str],
) -> int:
    """Set ``live``/``reference_count`` on each candidate. Returns index calls.

    Unknown stays ``None``. A candidate we could not check is never promoted to
    live, because "we did not look" and "it is used" must not render the same
    in the payload a writer reads.
    """
    calls = 0
    try:
        from ..tools.check_references import check_references  # noqa: PLC0415
    except Exception:  # pragma: no cover - defensive
        return calls

    for cand in candidates:
        if not cand.name:
            continue
        try:
            res = check_references(
                repo, identifier=cand.name, storage_path=storage_path
            )
            calls += 1
        except Exception:  # pragma: no cover - defensive
            continue
        if not isinstance(res, dict) or "error" in res:
            continue
        # `check_references` already strips the symbol's own defining span, so
        # a same-file hit is a sibling calling it, not the definition counting
        # itself. Excluding the whole file would under-count a module-private
        # helper — which is exactly the kind of symbol a writer most wants
        # offered back rather than rewritten.
        cand.reference_count = (
            int(res.get("import_count", 0) or 0)
            + int(res.get("content_count", 0) or 0)
        )
        cand.live = bool(res.get("is_referenced"))
    return calls


def _verdict(
    obligations: list,
    *,
    strong: list,
    partial: list,
    absence_blockers: list,
    dead_only_refutation: bool = False,
) -> str:
    """The one place a conclusion is allowed to be drawn.

    Ordering, and why each step comes where it does:

    * A live strong match refutes the claim outright. Nothing else matters —
      the thing exists and is used.
    * A refutation backed ONLY by symbols we proved dead is not a reuse
      instruction. The intent IS implemented and nothing calls it, which is a
      revive-or-delete decision; sending a writer to reuse it would be advice
      to depend on dead code, and reporting ``write_justified`` would hide a
      second copy about to be created next to the first. It becomes
      ``adapt_candidate``, and the symbols are surfaced under ``dead_matches``
      rather than ``reuse_candidates``.
    * Absence blockers outrank a clean sweep. An index that cannot account for
      itself cannot license "write it, nothing is there"; that is the same rule
      the retrieval verdict applies to every other absence claim, and relaxing
      it here would make the one tool whose whole job is absence the one tool
      that asserts it most cheaply.
    * A partial match does not block writing. It changes what a writer should
      read first, which is a recommendation, not a refusal.
    * ``lexical_only`` sits between ``write_justified`` and ``not_established``
      because it is a real, useful result — the lexical sweep genuinely found
      nothing — that simply must not be read as the stronger claim.
    """
    if strong:
        return REUSE_AVAILABLE
    refuted = any(o.status == REFUTED for o in obligations)
    if refuted and dead_only_refutation:
        # Ahead of the absence blockers deliberately: this is not an absence
        # claim. We found the thing. An index that cannot prove absence can
        # still show us a positive hit.
        return ADAPT_CANDIDATE
    if absence_blockers:
        return NOT_ESTABLISHED
    if refuted:
        return REUSE_AVAILABLE
    settled_lexical = all(
        o.status == SATISFIED for o in obligations if o.channel != SEMANTIC
    )
    if not settled_lexical:
        return NOT_ESTABLISHED
    semantic_settled = all(
        o.status == SATISFIED for o in obligations if o.channel == SEMANTIC
    )
    if partial:
        return ADAPT_CANDIDATE
    return WRITE_JUSTIFIED if semantic_settled else LEXICAL_ONLY


def _confidence(obligations: list) -> float:
    """Fraction of obligations actually settled. Not a probability."""
    if not obligations:
        return 0.0
    settled = sum(1 for o in obligations if o.status != UNESTABLISHED)
    return round(settled / len(obligations), 2)


def investigate_reuse_before_write(
    repo: str,
    intent: str,
    *,
    proposed_signature: Optional[str] = None,
    language: Optional[str] = None,
    scope: Optional[str] = None,
    include_tests: bool = False,
    strong_match: float = _STRONG_MATCH,
    adapt_floor: float = _ADAPT_FLOOR,
    max_candidates: int = _MAX_CANDIDATES,
    storage_path: Optional[str] = None,
) -> dict:
    """Investigate whether *intent* is already implemented in *repo*.

    Args:
        repo: Repository identifier or path.
        intent: What the caller is about to write, in their own words
            ("a modal dialog component", "parse an ISO 8601 timestamp").
        proposed_signature: The signature about to be written, when known.
            Enables the structural channel, which catches a same-shaped
            function under an unrelated name.
        language: Restrict to one indexed language.
        scope: Glob restricting the search to a subtree.
        include_tests: Consider symbols in test files. Off by default: a
            fixture that shares your intent is not a reuse candidate.
        strong_match: At or above this match strength, an existing symbol
            refutes the claim. Seeded, not calibrated.
        adapt_floor: Between this and ``strong_match``, a symbol is reported as
            related work rather than a blocker.
        max_candidates: Cap on candidates whose liveness is established.
        storage_path: Custom index storage path.

    Returns:
        A dict carrying ``verdict``, the ``claim`` under test, every obligation
        with its status and evidence, the candidates with their liveness, the
        per-channel disclosure, and a ``recommended_next_action``.
    """
    start = time.perf_counter()

    from ..storage import IndexStore  # noqa: PLC0415
    from ..tools._utils import index_status_to_tool_error, resolve_repo  # noqa: PLC0415

    if not (0.0 <= adapt_floor <= strong_match <= 1.0):
        return {"error": "require 0.0 <= adapt_floor <= strong_match <= 1.0"}
    if max_candidates < 1:
        return {"error": "max_candidates must be >= 1"}

    claim = f"Nothing in {repo} already implements {intent!r}, so writing it new is justified."

    try:
        owner, name_part = resolve_repo(repo, storage_path)
    except ValueError as e:
        return {"error": str(e)}

    store = IndexStore(base_path=storage_path)
    index = store.load_index(owner, name_part)
    if not index:
        return index_status_to_tool_error(store.inspect_index(owner, name_part))

    # Sampled here, before a single channel runs. See `_index_was_rewritten`.
    index_changed_at_start = _index_was_rewritten(index)

    obligations: list = []
    channels = _Channels()
    found: dict = {}
    calls = 0

    # ── Obligation 1: the intent yields something searchable ────────────
    # Fatal rather than merely unresolved. Every later obligation searches for
    # these terms; with none, the channels would all return nothing and a naive
    # verdict would read that silence as proof of absence — the most expensive
    # possible failure for a tool whose output licenses writing new code.
    terms = _intent_terms(intent)
    if not terms:
        return {
            "verdict": NOT_ESTABLISHED,
            "claim": claim,
            "confidence": 0.0,
            "obligations": [],
            "candidates": [],
            "unresolved_obligations": ["intent_is_searchable"],
            "channels": channels.to_dict(),
            "recommended_next_action": (
                "The intent reduced to no content words, so nothing was "
                "searched. Restate it with the nouns and verbs of the thing "
                "itself ('parse ISO 8601 timestamp', not 'the thing I need')."
            ),
            "_meta": {
                "timing_ms": round((time.perf_counter() - start) * 1000, 1),
                "charter": "read_only",
            },
        }

    obligations.append(
        Obligation(
            name="intent_is_searchable",
            question="Does the intent reduce to usable query terms?",
            status=SATISFIED,
            evidence=[f"Query terms: {', '.join(terms)}"],
            detail={"terms": terms},
            channel=LEXICAL,
            calls=0,
        )
    )

    from ..retrieval.confidence import BM25_CEILING, COSINE_CEILING  # noqa: PLC0415
    from ..tools.search_symbols import search_symbols  # noqa: PLC0415

    # ⚠⚠ `debug=True` is not a diagnostic here, it is the only way to read the
    # score. `search_symbols` emits `score` on a result row ONLY under debug;
    # without it every row arrives scoreless, `_squash` returns 0.0 for all of
    # them, and `strong`/`partial` are empty by construction -- so
    # `strong_match` and `adapt_floor` would be parameters that grade nothing
    # and every verdict would ride on obligation status alone. It also disables
    # the result cache (`_cacheable = not debug`), which is correct rather than
    # incidental: of the three result-cache consumers only `search_symbols`
    # revalidates, and an absence claim replayed from a cached row is the exact
    # shape #377 item 3 exists to refuse.
    #
    # Cost, measured on this repository's own index (~25k symbols): a debug
    # sweep is ~25 ms, against ~0.5 ms for a cache HIT and ~1.7 s for the cold
    # miss the cache exists to avoid. So the price is the cache, not the
    # `_bm25_breakdown` the debug path also computes -- and 25 ms is the honest
    # uncached cost of one sweep in an investigation the caller runs once
    # before writing a function. The breakdown is dropped; only `score` is read.
    search_kw: dict[str, Any] = {
        "storage_path": storage_path,
        "detail_level": "standard",
        "debug": True,
    }
    if language:
        search_kw["language"] = language
    if scope:
        search_kw["file_pattern"] = scope

    # ── Obligation 2: no symbol already carries this name ───────────────
    # The cheapest channel and the one that catches the most embarrassing
    # duplication: a writer about to add `formatIsoDate` to a repo that has
    # `format_iso_date`. Fuzzy is on because the near-miss spelling is the
    # whole point of the probe.
    name_ob = Obligation(
        name="no_name_twin",
        question="Does an indexed symbol already carry a name for this intent?",
        channel=LEXICAL,
    )
    forms = _identifier_forms(terms)
    try:
        hits: list = []
        for form in forms:
            res = search_symbols(
                repo, form, max_results=5, fuzzy=True, **search_kw
            )
            calls += 1
            if "error" in res:
                continue
            rows = [
                r for r in (res.get("results") or [])
                if (r.get("name") or "").lower().replace("_", "") == form.lower().replace("_", "")
                # A fixture that shares the intent is not a name twin either.
                # Filtering here and not only in `_collect` keeps the
                # obligation's verdict and the candidate list reading the same
                # corpus; otherwise a test-file twin refutes the claim while
                # being excluded from every candidate that could explain it.
                and (include_tests or not _is_test_path(r.get("file", "") or ""))
            ]
            hits.extend(rows)
            _collect(
                rows,
                channel=LEXICAL,
                ceiling=BM25_CEILING,
                why=f"name matches the intent spelled {form!r}",
                include_tests=include_tests,
                into=found,
            )
        if hits:
            name_ob.status = REFUTED
            name_ob.evidence = [
                f"{h.get('name')} in {h.get('file')}:{h.get('line')}" for h in hits[:5]
            ]
            name_ob.detail = {
                "forms_probed": forms,
                "refuting_symbol_ids": [
                    h.get("id") for h in hits if h.get("id")
                ],
            }
        else:
            name_ob.status = SATISFIED
            name_ob.evidence.append(
                f"No symbol is named {', '.join(forms)} ({len(forms)} spellings probed)"
            )
        channels.lexical = "ok"
    except Exception as e:  # pragma: no cover - defensive
        name_ob.status = UNESTABLISHED
        name_ob.evidence.append(f"Name probe raised: {e}")
        channels.lexical = "error"
    obligations.append(name_ob)

    # ── Obligation 3: no lexical match on names, signatures, summaries ──
    lex_ob = Obligation(
        name="no_lexical_match",
        question="Does any symbol's name, signature, summary or docstring describe this intent?",
        channel=LEXICAL,
    )
    try:
        res = search_symbols(repo, " ".join(terms), max_results=15, **search_kw)
        calls += 1
        if "error" in res:
            lex_ob.status = UNESTABLISHED
            lex_ob.evidence.append(f"search_symbols failed: {res['error']}")
            channels.lexical = "error"
        else:
            rows = res.get("results") or []
            _collect(
                rows,
                channel=LEXICAL,
                ceiling=BM25_CEILING,
                why="text of the symbol matches the intent",
                include_tests=include_tests,
                into=found,
            )
            lex_ob.status = SATISFIED
            lex_ob.evidence.append(
                f"{len(rows)} symbol(s) scored against the intent; "
                f"{sum(1 for c in found.values() if c.strength >= strong_match)} "
                f"at or above the strong-match threshold"
            )
            lex_ob.detail = {"scanned_symbols": len(getattr(index, "symbols", []) or [])}
            channels.lexical = "ok"
    except Exception as e:  # pragma: no cover - defensive
        lex_ob.status = UNESTABLISHED
        lex_ob.evidence.append(f"Lexical sweep raised: {e}")
        channels.lexical = "error"
    obligations.append(lex_ob)

    # ── Obligation 4: no semantic match (the synonym channel) ───────────
    # THE OBLIGATION THIS MODULE EXISTS FOR. Nothing above can connect an
    # intent of "modal" to an existing `Dialog`; they share no token. When this
    # channel is unavailable the sweep is not merely weaker, it is blind to the
    # single most common form of accidental duplication — so this obligation
    # goes UNESTABLISHED rather than SATISFIED, and the verdict degrades to
    # `lexical_only` instead of licensing a write.
    sem_ob = Obligation(
        name="no_semantic_match",
        question="Does any symbol mean the same thing under a different vocabulary?",
        channel=SEMANTIC,
    )
    sem_state, sem_note = _semantic_state(store, owner, name_part)
    if sem_state != "used":
        sem_ob.status = UNESTABLISHED
        sem_ob.evidence.append(sem_note)
        sem_ob.detail = {"semantic_state": sem_state}
        channels.semantic = sem_state
        channels.notes["semantic"] = sem_note
    else:
        try:
            res = search_symbols(
                repo,
                intent,
                max_results=15,
                semantic=True,
                semantic_only=True,
                **search_kw,
            )
            calls += 1
            if "error" in res:
                sem_ob.status = UNESTABLISHED
                sem_ob.evidence.append(f"semantic search failed: {res['error']}")
                channels.semantic = "error"
            else:
                rows = res.get("results") or []
                _collect(
                    rows,
                    channel=SEMANTIC,
                    ceiling=COSINE_CEILING,
                    why="means the same thing under different vocabulary",
                    include_tests=include_tests,
                    into=found,
                )
                sem_ob.status = SATISFIED
                sem_ob.evidence.append(
                    f"{len(rows)} symbol(s) compared by embedding against the intent"
                )
                channels.semantic = "ok"
        except Exception as e:  # pragma: no cover - defensive
            sem_ob.status = UNESTABLISHED
            sem_ob.evidence.append(f"Semantic sweep raised: {e}")
            channels.semantic = "error"
    obligations.append(sem_ob)

    # ── Obligation 5: no structural twin (only when a signature is given) ──
    # Catches the same function under a name nobody would search for. Runs only
    # when the caller supplied the signature they are about to write, because
    # without it there is no shape to compare and a fabricated one would be
    # comparing our guess to their code.
    struct_ob = Obligation(
        name="no_structural_twin",
        question="Does any symbol have a near-identical signature shape?",
        channel=STRUCTURAL,
    )
    if not proposed_signature:
        struct_ob.status = UNESTABLISHED
        struct_ob.evidence.append(
            "No proposed_signature was supplied, so signature shape could not "
            "be compared. This channel is optional: pass the signature you are "
            "about to write to enable it."
        )
        channels.structural = "not_requested"
        # An optional channel nobody asked for must not block a verdict. It is
        # recorded as unestablished for the audit trail and excluded from the
        # obligation set the verdict reads, which is the only place the
        # distinction between "could not" and "was not asked to" can be made.
        struct_ob.detail = {"optional": True}
    else:
        try:
            from ..tools.find_similar_symbols import _jaccard, _signature_tokens  # noqa: PLC0415

            want = _signature_tokens({"signature": proposed_signature})
            twins = []
            for sym in getattr(index, "symbols", []) or []:
                if sym.get("kind") not in ("function", "method", "class"):
                    continue
                path = sym.get("file", "") or ""
                if not include_tests and _is_test_path(path):
                    continue
                if language and sym.get("language") != language:
                    continue
                sim = _jaccard(want, _signature_tokens(sym))
                if sim >= strong_match:
                    twins.append((sim, sym))
            twins.sort(key=lambda p: p[0], reverse=True)
            for sim, sym in twins[:max_candidates]:
                sid = sym.get("id") or ""
                if sid and (sid not in found or found[sid].strength < sim):
                    found[sid] = Candidate(
                        symbol_id=sid,
                        name=sym.get("name", "") or "",
                        kind=sym.get("kind", "") or "",
                        file=sym.get("file", "") or "",
                        line=int(sym.get("line", 0) or 0),
                        channel=STRUCTURAL,
                        strength=sim,
                        signature=(sym.get("signature") or "").strip(),
                        summary=(sym.get("summary") or "").strip(),
                        why="signature shape is near-identical to the one proposed",
                    )
            struct_ob.status = REFUTED if twins else SATISFIED
            if twins:
                struct_ob.detail["refuting_symbol_ids"] = [
                    sym.get("id") for _, sym in twins[:max_candidates]
                    if sym.get("id")
                ]
            struct_ob.evidence.append(
                f"{len(twins)} symbol(s) share the proposed signature shape"
                if twins
                else "No symbol shares the proposed signature shape"
            )
            channels.structural = "ok"
        except Exception as e:  # pragma: no cover - defensive
            struct_ob.status = UNESTABLISHED
            struct_ob.evidence.append(f"Structural comparison raised: {e}")
            channels.structural = "error"
    obligations.append(struct_ob)

    # ── Establish liveness before offering anything ─────────────────────
    # A dead helper is not a reuse candidate. Pointing a writer at one does not
    # prevent duplication, it doubles the dead code — and it is the failure a
    # keyword-matching reuse checker cannot even detect, because the match
    # looks identical either way.
    ranked = sorted(found.values(), key=lambda c: c.strength, reverse=True)
    ranked = ranked[:max_candidates]
    calls += _establish_liveness(repo, ranked, storage_path)

    strong = [c for c in ranked if c.strength >= strong_match and c.live is not False]
    partial = [
        c for c in ranked
        if adapt_floor <= c.strength < strong_match and c.live is not False
    ]
    dead_matches = [c for c in ranked if c.live is False]

    # A strong match we could not prove dead still refutes (`live is not
    # False` above). A strong match we proved dead does not — it is reported
    # separately, because "this exists and nothing uses it" is a different
    # instruction to a writer than either "reuse it" or "nothing is there".
    # A refutation is only a reuse instruction if something backing it is
    # alive. `found` and `ranked` hold the SAME Candidate objects, so the
    # liveness pass above is visible here; a refuter that never made the
    # `max_candidates` cut keeps ``live is None`` and counts as unknown, which
    # blocks rather than permits (the tri-state rule, applied to the verdict
    # instead of to one candidate).
    refuting_ids = {
        sid
        for o in obligations
        if o.status == REFUTED
        for sid in (o.detail.get("refuting_symbol_ids") or [])
    }
    dead_only_refutation = bool(refuting_ids) and not any(
        found[sid].live is not False for sid in refuting_ids if sid in found
    )

    blockers = _absence_blockers(index, index_changed=index_changed_at_start)

    # The optional structural channel is excluded from the verdict when nobody
    # asked for it (see above); everything else is read.
    verdict_obligations = [
        o for o in obligations if not o.detail.get("optional")
    ]
    verdict = _verdict(
        verdict_obligations,
        strong=strong,
        partial=partial,
        absence_blockers=blockers,
        dead_only_refutation=dead_only_refutation,
    )

    unresolved = [o for o in verdict_obligations if o.status == UNESTABLISHED]

    if verdict == REUSE_AVAILABLE:
        top = strong[0] if strong else ranked[0]
        action = (
            f"Do not write this. {top.name} in {top.file}:{top.line} already "
            f"does it ({top.why}"
            + (f", {top.reference_count} reference(s)" if top.reference_count else "")
            + "). Read it with get_symbol_source before deciding to extend it."
        )
    elif verdict == ADAPT_CANDIDATE and partial:
        top = partial[0]
        action = (
            f"Related work exists: {top.name} in {top.file}:{top.line} "
            f"({top.why}). Read it before writing — extending it is usually "
            "cheaper than a parallel implementation."
        )
    elif verdict == ADAPT_CANDIDATE and dead_matches:
        # Reached only via a dead-only refutation, where `partial` is empty by
        # construction: the symbols that refuted the claim are the ones we
        # proved unreferenced. `dead_matches` is non-empty here by the same
        # construction -- only a candidate that reached `ranked` gets a liveness
        # pass, so a refuter established dead is necessarily in it -- and the
        # guard is here so a change to either half degrades to a vaguer
        # sentence rather than to an IndexError on the verdict path.
        top = dead_matches[0]
        action = (
            f"{top.name} in {top.file}:{top.line} already implements this and "
            "nothing references it. Writing a second copy would leave the "
            "repository with two, one of them dead. Read it and decide whether "
            "to revive it or delete it before writing."
        )
    elif verdict == WRITE_JUSTIFIED:
        action = (
            "Every channel was searched, including semantic, and nothing "
            "matches. Writing this new is justified."
        )
    elif verdict == LEXICAL_ONLY:
        action = (
            "Nothing matches lexically, but the synonym channel was "
            "unavailable, so an existing implementation under different "
            "vocabulary has NOT been ruled out. "
            + channels.notes.get("semantic", "")
        ).strip()
    elif blockers:
        action = (
            "Absence cannot be established from this index: "
            + blockers[0]
            + ". Re-index, then re-run before treating the intent as unimplemented."
        )
    elif verdict == ADAPT_CANDIDATE:  # pragma: no cover - see the guard above
        action = (
            "Something already implements this and could not be offered for "
            "reuse. Read `candidates` and `dead_matches` before writing."
        )
    else:
        first = unresolved[0] if unresolved else None
        action = (
            f"Unresolved: {first.question} "
            + (first.evidence[0] if first and first.evidence else "no evidence gathered")
        ) if first else "No obligation was settled; treat absence as unproven."

    result: dict[str, Any] = {
        "verdict": verdict,
        "claim": claim,
        "confidence": _confidence(verdict_obligations),
        "obligations": [o.to_dict() for o in obligations],
        "satisfied_obligations": [o.name for o in obligations if o.status == SATISFIED],
        "refuted_obligations": [o.name for o in obligations if o.status == REFUTED],
        "unresolved_obligations": [o.name for o in unresolved],
        "candidates": [c.to_dict() for c in ranked],
        "reuse_candidates": [c.symbol_id for c in strong],
        "adapt_candidates": [c.symbol_id for c in partial],
        "channels": channels.to_dict(),
        "recommended_next_action": action,
        "_meta": {
            "timing_ms": round((time.perf_counter() - start) * 1000, 1),
            "obligations_total": len(obligations),
            "index_calls": calls,
            "query_terms": terms,
            "rewrite_probe": (
                "sampled before the scan; a rebuild starting mid-scan is not "
                "visible, because the semantic channel moves the same mtime"
            ),
            "charter": "read_only",
        },
    }
    if blockers:
        result["absence_unprovable"] = blockers
    if dead_matches:
        # Surfaced at the top level, not buried in `candidates`, because it
        # changes what the writer should do: the intent IS implemented here and
        # nothing uses it, which is a revive-or-delete decision rather than a
        # reuse one.
        result["dead_matches"] = [c.to_dict() for c in dead_matches]
    return result
