"""Preflight check: is it safe to delete this symbol?

Combines find_importers (with cross-repo), check_references (text + import search),
find_dead_code confidence, runtime evidence (when Phase 7 traces exist), and
entry-point heuristics into a single verdict + actionable recommendation.

Verdict tiers (most-permissive first):
  - safe_to_delete         — no importers, no refs, dead-code confidence ≥0.9, no runtime hits
  - test_coverage_only     — only test files reference it (orphan; consider removing tests too)
  - internal_only          — refs exist only within the symbol's own file
  - internal_uses_blocking — referenced by other symbols in this repo (refactor first)
  - external_uses_blocking — imported by other files in this repo
  - scip_referenced        — compiler-verified references (SCIP) the import graph + text search missed
  - cross_repo_blocking    — used by other indexed repos (highest static severity)
  - runtime_observed       — Phase 7 traces show this code runs (red flag regardless of static refs)
  - entry_point            — decorator/main pattern suggests external invocation
  - corpus_inadequate      — nothing references it, and this index cannot support
                             that as proof (stale, withheld files, or an import
                             edge that only exists at runtime). #566/#569
  - name_not_searchable    — nothing references it BY NAME, and no call site
                             would write that name: a C# operator is invoked as
                             `a + b`, an indexer as `a[0]`. Absence of the token
                             is not evidence of disuse. #714

⚠⚠ **Both `corpus_inadequate` and `name_not_searchable` replace an absence
verdict, never a blocking one.**
A found importer is positive evidence and a thin corpus cannot unfind it — the
same asymmetry `_stop_rule._HARD_BLOCKER` already encodes.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Optional

from ..storage import IndexStore, record_savings, estimate_savings, cost_avoided
from ..storage.generation import connect_readonly
from ..runtime.confidence import symbol_hit_count
from . import _name_reachability
from ._corpus_adequacy import UNPROVEN_CEILING, assess_corpus
from ._stop_rule import build_stop_rule
from ._utils import index_status_to_tool_error, resolve_repo

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Severity scoring for individual blockers (1-5, higher = more dangerous)
# ---------------------------------------------------------------------------
_SEVERITY_CROSS_REPO = 5
_SEVERITY_RUNTIME = 5
_SEVERITY_ENTRY_POINT = 5
_SEVERITY_SCIP = 5           # compiler-verified reference — proven external use
_SEVERITY_EXTERNAL_IMPORT = 4
_SEVERITY_INTERNAL_REF = 3
_SEVERITY_TEST_ONLY = 2

# Decorator patterns suggesting external invocation
_ENTRY_DECORATOR_RE = re.compile(
    r"\b(route|get|post|put|patch|delete|command|task|signal|"
    r"event|listener|handler|subscribe|on|receiver|websocket|"
    r"endpoint|api|view|mount|app|cli|main|fixture)\b",
    re.IGNORECASE,
)

_TEST_FILE_RE = re.compile(r"(^|[/\\])(test_|tests?[/\\]|_test\.|conftest\.py)", re.IGNORECASE)


def _is_test_file(file_path: str) -> bool:
    return bool(_TEST_FILE_RE.search(file_path or ""))


def _resolve_target(index, symbol: str) -> Optional[dict]:
    """Resolve a symbol id or name to one symbol dict."""
    for sym in index.symbols:
        if sym.get("id") == symbol:
            return sym
    candidates = [s for s in index.symbols if s.get("name") == symbol]
    if not candidates:
        return None
    # Prefer non-import kinds with the largest body
    candidates.sort(key=lambda s: (
        s.get("kind") == "import",
        -int(s.get("byte_length", 0) or 0),
    ))
    return candidates[0]


def _detect_entry_point(target: dict) -> Optional[str]:
    """Return the matched entry-point indicator if target looks like one."""
    decorators = target.get("decorators") or []
    if isinstance(decorators, str):
        decorators = [decorators]
    for dec in decorators:
        dec_str = str(dec) if not isinstance(dec, dict) else (dec.get("name") or "")
        if dec_str and _ENTRY_DECORATOR_RE.search(dec_str):
            return f"decorator:{dec_str}"
    # __main__ / main heuristics
    name = (target.get("name") or "").lower()
    if name in {"main", "__main__", "run", "serve", "cli", "app"}:
        return f"name:{name}"
    return None


def _runtime_hits(store: IndexStore, owner: str, name: str, symbol_id: str) -> Optional[int]:
    """Best-effort runtime hit count over the indexed trace window.

    Delegates to the one reader (#717); a local copy of this query is how
    `hit_count` outlived the schema that never had it.
    """
    try:
        db_path = store._sqlite._db_path(owner, name)
    except Exception as exc:  # noqa: BLE001
        logger.debug("_runtime_hits: db path unavailable: %s", exc, exc_info=True)
        return None
    return symbol_hit_count(db_path, symbol_id)


def _runtime_data_present(store: IndexStore, owner: str, name: str) -> bool:
    """Has *any* runtime trace been ingested for this repo?

    Distinct from :func:`_runtime_hits` — which conflates "no traces at
    all" with "this particular symbol has zero hits in traces that
    exist." This helper lets ``safe_to_delete`` verdicts caveat
    themselves honestly when the runtime channel was simply never
    populated.
    """
    try:
        db_path = store._sqlite._db_path(owner, name)
        if not db_path.exists():
            return False
        conn = connect_readonly(db_path, isolation_level="")
        try:
            row = conn.execute("SELECT 1 FROM runtime_calls LIMIT 1").fetchone()
            return row is not None
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.debug("check_delete_safe: runtime probe skipped: %s", exc, exc_info=True)
        return False


def _check_dead_code_conf(repo: str, target_id: str, storage_path: Optional[str]) -> float:
    """Look up find_dead_code's confidence score for this symbol."""
    try:
        from .find_dead_code import find_dead_code  # noqa: PLC0415
        out = find_dead_code(
            repo, granularity="symbol", min_confidence=0.0, include_tests=False,
            storage_path=storage_path,
        )
        entries = out.get("dead_symbols") or out.get("results") or []
        for e in entries:
            if e.get("symbol_id") == target_id:
                return float(e.get("confidence", 0.0))
    except Exception as exc:  # noqa: BLE001
        logger.debug("check_delete_safe: find_dead_code lookup skipped: %s", exc, exc_info=True)
    return 0.0


def check_delete_safe(
    repo: str,
    symbol: str,
    cross_repo: bool = True,
    include_runtime: bool = True,
    storage_path: Optional[str] = None,
) -> dict:
    """Composite preflight: can this symbol be deleted safely?

    Returns one verdict tier, a confidence score, a ranked list of blockers,
    and a one-line recommended action. Reuses find_importers + check_references
    + find_dead_code + runtime evidence; never mutates the codebase.

    Args:
        repo: Repository identifier.
        symbol: Symbol id or name to evaluate.
        cross_repo: Include other indexed repos in the analysis (default True).
        include_runtime: Consult runtime_calls for production evidence (default True).
        storage_path: Custom storage path.

    Returns:
        Dict with ``verdict``, ``confidence``, ``blockers`` list, ``recommended_action``,
        per-signal counts, and ``_meta``.
    """
    start = time.perf_counter()

    try:
        owner, name = resolve_repo(repo, storage_path)
    except ValueError as e:
        return {"error": str(e)}

    store = IndexStore(base_path=storage_path)
    index = store.load_index(owner, name)
    if not index:
        return index_status_to_tool_error(store.inspect_index(owner, name))

    target = _resolve_target(index, symbol)
    if target is None:
        return {"error": f"Symbol not found: {symbol}"}

    target_id = target["id"]
    target_name = target.get("name", "")
    target_file = target.get("file", "")

    blockers: list[dict] = []

    # ── Signal 1: entry-point indicator ─────────────────────────────────
    entry_signal = _detect_entry_point(target)
    if entry_signal:
        blockers.append({
            "kind": "entry_point",
            "detail": entry_signal,
            "severity": _SEVERITY_ENTRY_POINT,
        })

    # ── Signal 2: file-level importers (cross_repo when requested) ─────
    # Test-file importers are tracked separately so the verdict can correctly
    # downgrade to test_coverage_only when nothing but tests imports the file.
    external_import_count = 0
    test_import_count = 0
    cross_repo_count = 0
    try:
        from .find_importers import find_importers  # noqa: PLC0415
        importers_out = find_importers(
            repo=f"{owner}/{name}", file_path=target_file,
            cross_repo=cross_repo, storage_path=storage_path,
        )
        for entry in importers_out.get("importers", []) or []:
            if entry.get("cross_repo"):
                cross_repo_count += 1
                blockers.append({
                    "kind": "cross_repo_import",
                    "repo": entry.get("source_repo", ""),
                    "file": entry.get("file", ""),
                    "severity": _SEVERITY_CROSS_REPO,
                })
            else:
                imp_file = entry.get("file", "")
                if imp_file and imp_file != target_file:
                    if _is_test_file(imp_file):
                        test_import_count += 1
                        blockers.append({
                            "kind": "test_import",
                            "file": imp_file,
                            "severity": _SEVERITY_TEST_ONLY,
                        })
                    else:
                        external_import_count += 1
                        blockers.append({
                            "kind": "external_import",
                            "file": imp_file,
                            "severity": _SEVERITY_EXTERNAL_IMPORT,
                        })
    except Exception as exc:  # noqa: BLE001
        logger.debug("check_delete_safe: find_importers skipped: %s", exc, exc_info=True)

    # ── Signal 3: identifier text refs (catches duck-typed callers) ────
    internal_ref_count = 0
    test_ref_count = 0
    try:
        from .check_references import check_references  # noqa: PLC0415
        # Batch form (identifiers=[...]) so check_references returns its grouped
        # `results` shape — singular (identifier=...) returns a flat response with
        # no `results` key, which this loop would silently read as empty (#338).
        ref_out = check_references(
            repo=f"{owner}/{name}", identifiers=[target_name],
            search_content=True, max_content_results=20,
            storage_path=storage_path,
        )
        for entry in ref_out.get("results", []) or []:
            for ref in entry.get("content_references", []) or []:
                ref_file = ref.get("file", "")
                if not ref_file:
                    continue
                # ⚠⚠ `ref_file == target_file` used to be skipped here, and it
                # was #406's defect one layer up: it discarded the FILE when the
                # thing that must be excluded is the DEFINITION. Before
                # v1.108.226 the skip was unreachable — `check_references` never
                # returned a reference in the defining file — so nothing showed
                # it was wrong. Measured on the two-function module in
                # tests/test_v1_108_226.py: `helper`, called by `main()` one line
                # below it, came back **`safe_to_delete` with zero blockers**.
                # That is this tool's whole job, answered backwards.
                #
                # `check_references` now excludes the definition's own line span,
                # so a reference reported in `target_file` is a genuine
                # same-file use and belongs in the count.
                if _is_test_file(ref_file):
                    test_ref_count += 1
                    if test_ref_count <= 3:
                        blockers.append({
                            "kind": "test_reference",
                            "file": ref_file,
                            "line": ref.get("line", 0),
                            "severity": _SEVERITY_TEST_ONLY,
                        })
                else:
                    internal_ref_count += 1
                    if internal_ref_count <= 3:
                        blockers.append({
                            "kind": "internal_reference",
                            "file": ref_file,
                            "line": ref.get("line", 0),
                            "severity": _SEVERITY_INTERNAL_REF,
                        })
    except Exception as exc:  # noqa: BLE001
        logger.debug("check_delete_safe: check_references skipped: %s", exc, exc_info=True)

    # ── Signal 4: dead-code confidence ─────────────────────────────────
    dead_code_conf = _check_dead_code_conf(f"{owner}/{name}", target_id, storage_path)
    # ...and whether the corpus behind it can support an absence claim at all
    # (#566). Same authority `find_dead_code` uses, never a second reading.
    # ⚠ Imported at MODULE level deliberately: a function-local import resolves
    # through `_corpus_adequacy`'s globals, so patching it here would silently
    # do nothing -- the `cli/policy.py` monkeypatch trap, found by a test that
    # patched this name and watched the verdict not move.
    corpus_adequacy = assess_corpus(index)

    # ── Signal 5: runtime evidence (Phase 7) ────────────────────────────
    runtime_hits = _runtime_hits(store, owner, name, target_id) if include_runtime else None
    runtime_data_present = _runtime_data_present(store, owner, name) if include_runtime else False
    if runtime_hits and runtime_hits > 0:
        blockers.append({
            "kind": "runtime_observed",
            "hit_count": runtime_hits,
            "severity": _SEVERITY_RUNTIME,
        })

    # ── Signal 6: SCIP compiler-verified references (compile-time evidence) ──
    # Files the compiler proved reference the target but the import graph + text
    # search missed (dynamic dispatch, barrel re-exports). A non-test external
    # one is proof the symbol is used — it flips an otherwise safe_to_delete
    # verdict to blocked. Honest no-op without ingested SCIP.
    from ._scip_consume import scip_reference_files, scip_meta_block  # noqa: PLC0415
    scip_external_count = 0
    scip_test_count = 0
    scip_block: Optional[dict] = None
    _scip_files, _scip_meta, _scip_stale = scip_reference_files(store, owner, name, target_id)
    _scip_files.pop(target_file, None)  # self-references are not external uses
    for _f in sorted(_scip_files):
        if _is_test_file(_f):
            scip_test_count += 1
            blockers.append({
                "kind": "scip_test_reference", "file": _f,
                "severity": _SEVERITY_TEST_ONLY, "verification": "compiler_verified",
            })
        else:
            scip_external_count += 1
            blockers.append({
                "kind": "scip_reference", "file": _f,
                "severity": _SEVERITY_SCIP, "verification": "compiler_verified",
            })
    if _scip_files:
        scip_block = scip_meta_block(
            _scip_meta, _scip_stale,
            verified_external_refs=scip_external_count, verified_test_refs=scip_test_count,
        )

    # ── Verdict selection ──────────────────────────────────────────────
    # Order matters — most restrictive first. Tests are counted separately
    # from external imports so test-only consumption downgrades the verdict.
    total_test_signals = test_ref_count + test_import_count + scip_test_count
    total_external_signals = external_import_count
    total_internal_signals = internal_ref_count

    if runtime_hits and runtime_hits > 0:
        verdict = "runtime_observed"
    elif entry_signal:
        verdict = "entry_point"
    elif cross_repo_count > 0:
        verdict = "cross_repo_blocking"
    elif scip_external_count > 0:
        verdict = "scip_referenced"
    elif total_external_signals > 0:
        verdict = "external_uses_blocking"
    elif total_internal_signals > 0:
        verdict = "internal_uses_blocking"
    elif total_test_signals > 0:
        verdict = "test_coverage_only"
    elif dead_code_conf >= 0.9:
        verdict = "safe_to_delete"
    elif (total_internal_signals == 0 and total_external_signals == 0
          and total_test_signals == 0):
        # No refs at all, but dead-code analysis didn't reach high confidence.
        # Still surface as safe with a slightly lower confidence score.
        verdict = "safe_to_delete"
    else:
        verdict = "internal_only"

    # ── Corpus adequacy (#566/#569) ────────────────────────────────────
    # ⚠⚠ This is the DESTRUCTIVE surface of the same defect, and the branch
    # above is why it needed its own fix: the "no refs at all" fallback reaches
    # `safe_to_delete` REGARDLESS of dead_code_conf, and then floors the
    # confidence at 0.85. Capping `find_dead_code` alone left that path
    # certifying a delete over a stale index — the twelve `encoding/schemas`
    # encoders of #569 have no refs at all, so each one graded safe at 0.85.
    #
    # ⚠ Only the ABSENCE verdicts are overridden. A found importer is positive
    # evidence and an inadequate corpus cannot unfind it, which is the same
    # asymmetry `_HARD_BLOCKER` already encodes.
    # ── The name cannot reach a call site (#714) ───────────────────────
    # ⚠⚠ A SECOND cause with the same destructive shape, and it is not fixed
    # by re-indexing. A C# operator is invoked as `a + b`, an indexer as
    # `a[0]`, a conversion as `(string)a` -- the declaration's name
    # (`operator +`, `this[]`, `explicit operator string`) appears at NO call
    # site by construction, so "no references found" is not evidence about it.
    # Measured before this branch existed: on a corpus where every one of them
    # was used, the ordinary method in the same file returned
    # `internal_uses_blocking` and `operator +` returned `safe_to_delete` at
    # confidence 1.0, "No callers or refs found."
    #
    # ⚠ Same asymmetry as corpus adequacy: only ABSENCE verdicts are replaced.
    # A found reference is positive evidence, and an unsearchable name cannot
    # unfind it.
    unreachable_name = None
    if (
        # ⚠ `target`, the RESOLVED symbol -- not the `symbol` argument, which is
        # whatever the caller passed. Reading the argument would have looked
        # right: an id (`…::Vec.operator +#method`) is not an identifier either,
        # so the branch would fire for ids and silently not for plain names.
        not _name_reachability.name_can_appear_at_a_call_site(target.get("name", ""))
        and verdict in ("safe_to_delete", "internal_only", "test_coverage_only")
    ):
        verdict = "name_not_searchable"
        unreachable_name = {
            "action": "read the call sites by hand, or check runtime evidence",
            "why": (
                f"{target.get('name', '')!r} is not a name any call site writes, so a "
                f"reference search over names cannot establish that nothing uses it"
            ),
        }
        blockers.append({
            "kind": "name_not_searchable",
            "blockers": [unreachable_name["why"]],
            "severity": _SEVERITY_INTERNAL_REF,
        })

    corpus_gap = None
    if not corpus_adequacy.adequate and verdict in (
        "safe_to_delete", "internal_only", "test_coverage_only",
    ):
        verdict = "corpus_inadequate"
        corpus_gap = {
            "action": "re-index this repo",
            "why": corpus_adequacy.warning(),
        }
        blockers.append({
            "kind": "corpus_inadequate",
            "blockers": list(corpus_adequacy.blockers),
            "severity": _SEVERITY_INTERNAL_REF,
        })

    # ── Confidence ─────────────────────────────────────────────────────
    # Start at dead-code confidence (or 0.5 baseline) and decay per blocker.
    confidence = max(0.5, dead_code_conf)
    if verdict == "corpus_inadequate":
        # ⚠ The floor below is what made the old path dangerous: it raised an
        # unproven verdict to 0.85. Nothing was established here, so nothing is
        # floored.
        confidence = min(confidence, corpus_adequacy.ceiling)
    elif verdict == "name_not_searchable":
        # ⚠ NOT `corpus_adequacy.ceiling`: the corpus may be perfectly adequate
        # -- the first draft used it and published confidence 1.0 on a refusal,
        # because adequacy answers a different question. `UNPROVEN_CEILING` is
        # the number this project already uses for "an absence nothing could
        # establish", which is exactly this.
        confidence = min(confidence, UNPROVEN_CEILING)
    elif verdict == "safe_to_delete":
        confidence = max(confidence, 0.85 if dead_code_conf < 0.9 else 0.95)
    elif verdict == "runtime_observed":
        confidence = 0.05  # nearly certain unsafe
    elif verdict == "cross_repo_blocking":
        confidence = 0.10
    elif verdict == "scip_referenced":
        confidence = 0.10  # compiler-proven external use
    elif verdict == "entry_point":
        confidence = 0.20
    elif verdict == "external_uses_blocking":
        confidence = 0.25
    elif verdict == "internal_uses_blocking":
        confidence = 0.45
    elif verdict == "test_coverage_only":
        confidence = 0.65
    elif verdict == "internal_only":
        confidence = 0.55

    # ── Recommended action ─────────────────────────────────────────────
    # Honest-hint caveat: when the verdict relies on the *absence* of
    # runtime evidence but no traces have ever been ingested for this
    # repo, the runtime channel can't actually prove safety — only
    # static signals can. Surface that in the recommended_action so
    # operators don't read "safe" as "we checked production traffic."
    safe_action = "No callers, refs, or runtime hits found — deletion appears safe."
    if include_runtime and not runtime_data_present:
        safe_action = (
            "No callers or refs found. Static signals only — no runtime traces "
            "ingested for this repo, so production traffic was not consulted. "
            "Run `import-trace` against representative traffic to strengthen this verdict."
        )

    actions = {
        "safe_to_delete": safe_action,
        "name_not_searchable": (
            "No references found BY NAME, and no call site would write this "
            "name: it is invoked syntactically. Read the call sites, or check "
            "runtime evidence, before deleting."
        ),
        "corpus_inadequate": (
            "No references found, but this index cannot support that as proof. "
            + (corpus_adequacy.warning() or "")
        ),
        "test_coverage_only": "Only tests reference this symbol. Remove the tests alongside it.",
        "internal_only": "Refs exist only in the same file. Safe with local refactor.",
        "internal_uses_blocking": (
            f"{internal_ref_count} internal reference(s) found. Rename/refactor callers first."
        ),
        "external_uses_blocking": (
            f"{external_import_count} other file(s) in this repo import this. Update importers first."
        ),
        "scip_referenced": (
            f"{scip_external_count} file(s) carry a compiler-verified reference to this symbol "
            "(SCIP) that the import graph and text search didn't surface — dynamic dispatch or "
            "barrel re-exports. Update those call sites before deleting."
        ),
        "cross_repo_blocking": (
            f"{cross_repo_count} other repo(s) in the suite depend on this. Coordinate a deprecation."
        ),
        "runtime_observed": (
            f"Runtime traces show {runtime_hits} hits — this code runs in production. "
            "Investigate why static analysis missed the callers."
        ),
        "entry_point": (
            f"Entry-point indicator ({entry_signal}) — invoked externally by framework/CLI/protocol. "
            "Never delete blindly; verify routing config."
        ),
    }

    # Rank blockers by severity, truncate to top 5
    blockers.sort(key=lambda b: -b.get("severity", 0))
    blockers_out = blockers[:5]

    # Token-savings ledger (cheap response)
    raw_bytes = int(target.get("byte_length", 0) or 0) + 1000
    response_bytes = 800
    tokens_saved = estimate_savings(raw_bytes, response_bytes)
    total_saved = record_savings(tokens_saved, tool_name="check_delete_safe")

    elapsed = (time.perf_counter() - start) * 1000

    result = {
        "verdict": verdict,
        "confidence": round(confidence, 2),
        "target": {
            "symbol_id": target_id,
            "name": target_name,
            "kind": target.get("kind", ""),
            "file": target_file,
            "line": target.get("line", 0),
        },
        "blockers": blockers_out,
        "recommended_action": actions.get(verdict, "Review blockers before deletion."),
        # Executable stop rule beside the certainty language. `terminal` means
        # no further jcodemunch call moves this verdict; it does NOT mean safe.
        # See tools/_stop_rule.py for why this ships by default.
        "stop_rule": build_stop_rule(
            "check_delete_safe",
            verdict,
            cross_repo=cross_repo,
            include_runtime=include_runtime,
            runtime_data_present=runtime_data_present,
            corpus_gap=corpus_gap,
        ),
        "corpus_adequacy": corpus_adequacy.as_dict(),
        "signals": {
            "external_import_count": external_import_count,
            "test_import_count": test_import_count,
            "cross_repo_count": cross_repo_count,
            "internal_ref_count": internal_ref_count,
            "test_ref_count": test_ref_count,
            "scip_external_ref_count": scip_external_count,
            "scip_test_ref_count": scip_test_count,
            "dead_code_confidence": round(dead_code_conf, 3),
            "entry_point": entry_signal,
        },
        "_meta": {
            "timing_ms": round(elapsed, 1),
            "tokens_saved": tokens_saved,
            "total_tokens_saved": total_saved,
            **cost_avoided(tokens_saved, total_saved),
        },
    }
    if not corpus_adequacy.adequate:
        result["signal_warning"] = corpus_adequacy.warning()
    if scip_block is not None:
        result["_meta"]["scip"] = scip_block
    if runtime_hits is not None:
        result["signals"]["runtime_hits"] = runtime_hits
    if include_runtime:
        result["signals"]["runtime_data_present"] = runtime_data_present
    return result
