"""Deletion safety as a set of proof obligations rather than a similarity score.

The claim under test is always the same shape:

    "<symbol> in <file> can be removed without breaking anything."

The investigation refuses to treat that claim as established until every
obligation below is either satisfied or refuted. An obligation nobody could
settle is reported as unresolved, and an unresolved obligation can never
produce a ``safe`` verdict. That rule is the entire honesty guarantee, and it
is enforced in exactly one place: ``_verdict``.

Why this exists
---------------
``check_delete_safe`` already fuses importers, references, dead-code confidence
and runtime evidence into a verdict tier. It is good, and it is not this. It
answers "how risky does this look?" by scoring signals. An investigation answers
"which specific things must be true, and which of them did we actually
establish?" -- and names the gap when it cannot.

The distinction is not academic. It is the exact failure the 2026-03-18
dead-code A/B measured: file-level liveness was established, export-level
liveness was never asked, and the agent reported the export alive with no
awareness that it had answered a different question. Scoring more signals would
not have helped. Raising the missing obligation would have.

Ordering
--------
Obligations run cheapest-first and short-circuit on refutation: once any
obligation is refuted the claim is dead, and spending more budget to refute it
harder is waste. Cost here means round trips against the index, not tokens; all
of it is local.

Charter
-------
Read-only. This plans and reads. It never edits, executes, or deletes.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

SATISFIED = "satisfied"      # we established this obligation holds
REFUTED = "refuted"          # we established it does NOT hold: a real blocker
UNESTABLISHED = "unestablished"  # we could not find out; NOT the same as satisfied

# Evidence channels. An obligation is STATIC when source alone can settle it,
# and DYNAMIC when it needs evidence of what actually ran. The split exists so
# the verdict can distinguish "source is clean, execution unknown" from "we
# barely established anything" -- two states that otherwise both land in
# not_established and read identically.
STATIC = "static"
DYNAMIC = "dynamic"

# Verdicts
SAFE = "safe"                      # every obligation settled in favour of removal
STATIC_CLEAR = "static_clear"      # source says removable; runtime unknown
UNSAFE = "unsafe"                  # at least one real blocker
NOT_ESTABLISHED = "not_established"  # a source-answerable question went unanswered


@dataclass
class Obligation:
    """One thing that must be true before deletion is justified.

    ``status`` is deliberately tri-state. Collapsing UNESTABLISHED into
    SATISFIED is how a tool reports "I found no references" as "there are no
    references", which is the failure mode this whole module exists to prevent.
    """

    name: str
    question: str
    status: str = UNESTABLISHED
    evidence: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)
    calls: int = 0
    channel: str = STATIC

    def to_dict(self) -> dict:
        d = {
            "obligation": self.name,
            "question": self.question,
            "status": self.status,
            "channel": self.channel,
            "evidence": self.evidence,
        }
        if self.detail:
            d["detail"] = self.detail
        return d


def _split_importers_by_liveness(
    repo: str, files: list[str], storage_path: Optional[str]
) -> tuple[list[str], list[str]]:
    """Partition importer files into (unreachable, reachable).

    One level of recursion only, deliberately. A file with zero importers of its
    own is unreachable; deciding that its importers are in turn unreachable is
    the same question one level up, and unbounded walking would turn a bounded
    investigation into a graph traversal with no budget. One level covers the
    case the 2026-03-18 A/B actually hit (a dead loader importing a helper) and
    reports the rest honestly rather than guessing.

    An importer we cannot classify counts as REACHABLE, so uncertainty blocks
    deletion rather than permitting it.
    """
    dead: list[str] = []
    live: list[str] = []
    try:
        from ..tools.find_importers import find_importers  # noqa: PLC0415
    except Exception:  # pragma: no cover - defensive
        return [], list(files)

    for f in files:
        try:
            res = find_importers(repo, f, storage_path=storage_path)
            if "error" in res:
                live.append(f)
                continue
            # (LEDGER L-73) A file a dynamic import can load has no static
            # importer by construction; that is not evidence it is dead.
            if res.get("dynamic_import_boundary"):
                live.append(f)
            elif int(res.get("importer_count", 0) or 0) == 0:
                dead.append(f)
            else:
                live.append(f)
        except Exception:  # pragma: no cover - defensive
            live.append(f)
    return dead, live


def _verdict(obligations: list[Obligation]) -> str:
    """The one place a conclusion is allowed to be drawn.

    Refuted beats everything: a single real use makes the claim false.
    Otherwise every obligation must be satisfied to reach ``safe``.

    ``static_clear`` is the honest middle. Most repositories have never ingested
    a runtime trace, so ``no_runtime_evidence`` is permanently unresolvable
    there, and a strict rule would make ``safe`` unreachable for nearly every
    real user. That is not more honest, it is less informative: it renders the
    verdict constant and pushes the reader back into the obligation list to
    learn anything. ``static_clear`` says precisely what was established --
    every source-answerable question passed -- without claiming the one thing
    source cannot show.

    The distinction it protects: ``static_clear`` means the dynamic channel is
    open. ``not_established`` means something source COULD have answered went
    unanswered, which is a different and more suspicious situation.
    """
    if any(o.status == REFUTED for o in obligations):
        return UNSAFE
    if all(o.status == SATISFIED for o in obligations):
        return SAFE
    static_settled = all(
        o.status == SATISFIED for o in obligations if o.channel == STATIC
    )
    if static_settled:
        return STATIC_CLEAR
    return NOT_ESTABLISHED


def _confidence(obligations: list[Obligation]) -> float:
    """Fraction of obligations actually settled. Not a probability.

    Reported so a caller can distinguish "one small gap" from "we established
    almost nothing", which a bare verdict string hides.
    """
    if not obligations:
        return 0.0
    settled = sum(1 for o in obligations if o.status != UNESTABLISHED)
    return round(settled / len(obligations), 2)


def investigate_deletion_safety(
    repo: str,
    symbol: str,
    *,
    include_runtime: bool = True,
    include_text_sweep: bool = True,
    storage_path: Optional[str] = None,
) -> dict:
    """Investigate whether *symbol* can be removed from *repo*.

    Args:
        repo: Repository identifier or path.
        symbol: Symbol id or bare name.
        include_runtime: Consult ingested runtime traces when present.
        include_text_sweep: Sweep raw file contents for the name. Catches uses
            the import graph cannot see (templates, config, string dispatch).
        storage_path: Custom index storage path.

    Returns:
        A dict carrying ``verdict``, the ``claim`` under test, every obligation
        with its status and evidence, the unresolved ones called out separately,
        and a ``recommended_next_action`` naming the cheapest way to close the
        largest remaining gap.
    """
    start = time.perf_counter()

    from ..retrieval.verdict import symbol_not_found  # noqa: PLC0415
    from ..storage import IndexStore  # noqa: PLC0415
    from ..tools._utils import resolve_repo  # noqa: PLC0415
    from ..tools.check_delete_safe import (  # noqa: PLC0415
        _detect_entry_point,
        _resolve_target,
        _runtime_data_present,
        _runtime_hits,
    )

    try:
        owner, name_part = resolve_repo(repo, storage_path)
    except ValueError as e:
        return {"error": str(e)}

    store = IndexStore(base_path=storage_path)
    index = store.load_index(owner, name_part)
    if not index:
        from ..tools._utils import (  # noqa: PLC0415
            index_status_to_tool_error,
        )

        return index_status_to_tool_error(store.inspect_index(owner, name_part))

    obligations: list[Obligation] = []

    # ── Obligation 1: the target resolves to exactly one symbol ──────────
    # Everything downstream is about a specific symbol. If we cannot name it,
    # no later evidence means anything, so this is fatal rather than merely
    # unresolved.
    target = _resolve_target(index, symbol)
    if target is None:
        return {
            "verdict": NOT_ESTABLISHED,
            "claim": f"{symbol} can be removed from {repo}.",
            **symbol_not_found(symbol, index.symbols),
            "obligations": [],
            "unresolved_obligations": ["target_resolves"],
            "recommended_next_action": (
                "Confirm the symbol name or id with search_symbols; the "
                "investigation cannot start without a resolved target."
            ),
        }

    target_id = target["id"]
    target_name = target.get("name", "") or ""
    target_file = target.get("file", "") or ""
    target_kind = target.get("kind", "") or ""

    obligations.append(
        Obligation(
            name="target_resolves",
            question="Does the target resolve to exactly one indexed symbol?",
            status=SATISFIED,
            evidence=[f"Resolved to {target_id} ({target_kind}) in {target_file}"],
            detail={"symbol_id": target_id, "kind": target_kind, "file": target_file},
            calls=1,
        )
    )

    # ── Obligation 2: nothing imports this name ─────────────────────────
    # THE GAP-2 OBLIGATION. A file having importers says nothing about whether
    # THIS export is among the names they import. check_references answers the
    # export-level question directly and has always been able to; the 2026-03-18
    # A/B failed because nobody asked it.
    ref_ob = Obligation(
        name="export_not_imported",
        question=f"Does any file import the name {target_name!r} specifically?",
    )
    try:
        from ..tools.check_references import check_references  # noqa: PLC0415

        refs = check_references(repo, identifier=target_name, storage_path=storage_path)
        ref_ob.calls = 1
        if "error" in refs:
            ref_ob.status = UNESTABLISHED
            ref_ob.evidence.append(f"check_references failed: {refs['error']}")
        else:
            import_refs = refs.get("import_references") or []
            # ⚠ Read the match check_references made; never compare names
            # again here (LEDGER L-90). Its comparison folds Unicode forms
            # and escapes, so a raw `target_name in names` dropped an import
            # it had found and this obligation read SATISFIED over a live
            # importer. `match_type` already separates a named import from a
            # specifier-stem one, which is all this filter is for.
            named_importers = [
                r["file"]
                for r in import_refs
                if any(m.get("match_type") == "named" for m in r.get("matches", []))
            ]
            if named_importers:
                # A refutation is only real if the thing that would break is
                # itself reachable. Breaking dead code is not breaking
                # anything. This is Gap 3 restated at the obligation level:
                # find_importers already exposes has_importers, and the naive
                # obligation ignores it, which would report a transitively dead
                # symbol as unsafe forever.
                dead_importers, live_importers = _split_importers_by_liveness(
                    repo, named_importers, storage_path
                )
                ref_ob.calls += len(named_importers)
                if live_importers:
                    ref_ob.status = REFUTED
                    ref_ob.evidence = [
                        f"Imported by name in {f}" for f in live_importers[:5]
                    ]
                    ref_ob.detail = {
                        "named_importers": named_importers,
                        "live_importers": live_importers,
                        "dead_importers": dead_importers,
                    }
                else:
                    ref_ob.status = SATISFIED
                    ref_ob.evidence = [
                        f"Imported only by {f}, which is itself unreachable"
                        for f in dead_importers[:5]
                    ]
                    ref_ob.detail = {
                        "deletion_cluster": dead_importers,
                        "note": (
                            "Removable only together with these importers; "
                            "deleting this symbol alone would break them."
                        ),
                    }
            else:
                ref_ob.status = SATISFIED
                ref_ob.evidence.append(
                    f"No file imports the name {target_name!r} "
                    f"({refs.get('import_count', 0)} import references scanned)"
                )
    except Exception as e:  # pragma: no cover - defensive
        ref_ob.status = UNESTABLISHED
        ref_ob.evidence.append(f"Reference check raised: {e}")
    obligations.append(ref_ob)

    # ── Obligation 3: the name is not used in source text elsewhere ─────
    # The import graph cannot see template interpolation, string dispatch,
    # reflective lookup, or config that names a symbol. A raw text sweep is a
    # weaker signal but it fails in the safe direction: a hit blocks deletion,
    # a miss only leaves the obligation satisfied for what text can show.
    text_ob = Obligation(
        name="no_textual_use",
        question=f"Does {target_name!r} appear in any file other than its own?",
    )
    if not include_text_sweep:
        text_ob.status = UNESTABLISHED
        text_ob.evidence.append("Text sweep disabled by caller")
    else:
        try:
            from ..tools.search_text import search_text  # noqa: PLC0415

            hits = search_text(repo, target_name, storage_path=storage_path)
            text_ob.calls = 1
            results = hits.get("results") or []
            other_files = [
                r.get("file")
                for r in results
                if r.get("file") and r.get("file") != target_file
            ]
            if other_files:
                # Same liveness qualifier as the import obligation: a mention
                # inside an unreachable file is not a use. Without this the two
                # obligations disagree about the same dead cluster, and the
                # weaker signal silently wins the verdict.
                dead_m, live_m = _split_importers_by_liveness(
                    repo, [f for f in other_files if f], storage_path
                )
                text_ob.calls += len(other_files)
                if live_m:
                    text_ob.status = REFUTED
                    text_ob.evidence = [f"Name appears in {f}" for f in live_m[:5]]
                    text_ob.detail = {"files": live_m, "in_unreachable_files": dead_m}
                else:
                    text_ob.status = SATISFIED
                    text_ob.evidence = [
                        f"Only textual occurrences are in unreachable files: "
                        f"{', '.join(dead_m[:3])}"
                    ]
                    text_ob.detail = {"in_unreachable_files": dead_m}
            else:
                text_ob.status = SATISFIED
                text_ob.evidence.append(
                    f"{target_name!r} appears only in its own file ({target_file})"
                )
        except Exception as e:  # pragma: no cover - defensive
            text_ob.status = UNESTABLISHED
            text_ob.evidence.append(f"Text sweep raised: {e}")
    obligations.append(text_ob)

    # ── Obligation 4: the symbol is not an entry point ──────────────────
    entry_ob = Obligation(
        name="not_entry_point",
        question="Is the symbol reachable as a framework or runtime entry point?",
        calls=1,
    )
    entry_signal = _detect_entry_point(target)
    if entry_signal:
        entry_ob.status = REFUTED
        entry_ob.evidence.append(f"Entry-point indicator: {entry_signal}")
        entry_ob.detail = {"indicator": entry_signal}
    else:
        entry_ob.status = SATISFIED
        entry_ob.evidence.append("No route/command/task/signal indicator on the symbol")
    obligations.append(entry_ob)

    # ── Obligation 4b: no dynamic import can load the file (LEDGER L-70) ─
    # A package- or prefix-scoped dynamic import names its module at runtime,
    # so obligations 2 and 3 pass by construction for every file it can load.
    # Source CAN answer this (read the loader), so it is a static obligation
    # left open, never `static_clear`. Only present when a site reaches the
    # file; an opaque site is disclosed elsewhere and blocks nothing (#876).
    from ..tools._dynamic_boundary import DynamicBoundary  # noqa: PLC0415

    reaching = DynamicBoundary(index.imports).reaching(target_file)
    if reaching:
        obligations.append(
            Obligation(
                name="no_dynamic_loader",
                question="Can a dynamic import load the file that defines it?",
                status=UNESTABLISHED,
                evidence=[
                    f"{reaching[0]} imports a module by a computed name that can "
                    f"reach {target_file}; read it for the names it can produce"
                ],
                detail={"loaders": reaching[:10], "loaders_total": len(reaching)},
                calls=1,
            )
        )

    # ── Obligation 5: production never ran it ───────────────────────────
    # UNESTABLISHED when no traces exist. This is the obligation most likely to
    # be honestly unresolvable, and reporting it as satisfied would be the
    # single most dangerous lie this module could tell: "no runtime evidence"
    # and "no traces ingested" look identical in a bare count.
    runtime_ob = Obligation(
        name="no_runtime_evidence",
        question="Do ingested runtime traces show this symbol executing?",
        channel=DYNAMIC,
    )
    if not include_runtime:
        runtime_ob.status = UNESTABLISHED
        runtime_ob.evidence.append("Runtime check disabled by caller")
    else:
        try:
            if not _runtime_data_present(store, owner, name_part):
                runtime_ob.status = UNESTABLISHED
                runtime_ob.evidence.append(
                    "No runtime traces ingested for this repo, so execution "
                    "could not be ruled out. Import traces with import-trace."
                )
            else:
                runtime_ob.calls = 1
                hits = _runtime_hits(store, owner, name_part, target_id)
                if hits is None:
                    runtime_ob.status = UNESTABLISHED
                    runtime_ob.evidence.append("Runtime lookup returned no answer")
                elif hits > 0:
                    runtime_ob.status = REFUTED
                    runtime_ob.evidence.append(f"Observed executing {hits} times")
                    runtime_ob.detail = {"hits": hits}
                else:
                    runtime_ob.status = SATISFIED
                    runtime_ob.evidence.append(
                        "Traces are present for this repo and record zero hits"
                    )
        except Exception as e:  # pragma: no cover - defensive
            runtime_ob.status = UNESTABLISHED
            runtime_ob.evidence.append(f"Runtime check raised: {e}")
    obligations.append(runtime_ob)

    verdict = _verdict(obligations)
    unresolved = [o for o in obligations if o.status == UNESTABLISHED]
    blockers = [o for o in obligations if o.status == REFUTED]

    if verdict == UNSAFE:
        action = (
            f"Do not delete. {blockers[0].question} was refuted: "
            f"{blockers[0].evidence[0] if blockers[0].evidence else 'see evidence'}"
        )
    elif verdict == SAFE:
        action = (
            "Every obligation was settled in favour of removal, including "
            "runtime evidence. Review the evidence list before acting."
        )
    elif verdict == STATIC_CLEAR:
        action = (
            "Every source-answerable obligation passed; nothing in the code "
            "references this. Execution was not ruled out"
            + (
                f" ({unresolved[0].evidence[0]})"
                if unresolved and unresolved[0].evidence
                else ""
            )
            + ". Ingest traces with import-trace to close the last obligation."
        )
    else:
        first = unresolved[0]
        action = f"Unresolved: {first.question} " + (
            first.evidence[0] if first.evidence else "no evidence gathered"
        )

    # Surface a transitively dead cluster at the top level. Buried in an
    # obligation's detail it is findable; here it is actionable, and it changes
    # what the caller has to delete.
    cluster: list[str] = []
    for o in obligations:
        for f in o.detail.get("deletion_cluster", []) or []:
            if f not in cluster:
                cluster.append(f)

    result = {
        "verdict": verdict,
        "claim": f"{target_name} in {target_file} can be removed without breaking anything.",
        "confidence": _confidence(obligations),
        "symbol_id": target_id,
        "obligations": [o.to_dict() for o in obligations],
        "satisfied_obligations": [o.name for o in obligations if o.status == SATISFIED],
        "refuted_obligations": [o.name for o in blockers],
        "unresolved_obligations": [o.name for o in unresolved],
        "recommended_next_action": action,
        "_meta": {
            "timing_ms": round((time.perf_counter() - start) * 1000, 1),
            "obligations_total": len(obligations),
            "index_calls": sum(o.calls for o in obligations),
            "charter": "read_only",
        },
    }
    if cluster:
        result["deletion_cluster"] = cluster
        result["recommended_next_action"] = (
            "Removable only as a group. These importers are themselves "
            f"unreachable and must go with it: {', '.join(cluster)}"
        )
    return result
