"""Preflight check: is it safe to edit this symbol?

Where :func:`check_delete_safe` answers "who breaks if this disappears,"
this answers "what's my regression risk if I modify it, and what must I
preserve." It fuses signature impact (external / cross-repo callers that
depend on the current contract), cyclomatic complexity, test-coverage
presence, and runtime traffic into a single verdict + a one-line
recommended action. Read-only — never mutates the codebase.

Verdict tiers (most-constraining first):
  - runtime_critical  — runs in production (traces show hits); edits are high-stakes
  - signature_impact  — external/cross-repo callers (incl. compiler-verified SCIP refs) depend on the signature; body edits OK, keep the contract
  - complexity_risk   — high cyclomatic complexity; edits are regression-prone
  - untested          — referenced but no test coverage; add a characterization test first
  - safe_to_edit      — low complexity, no external callers; modify freely

The canonical signal helpers (`_is_test_file`, `_resolve_target`,
`_runtime_hits`, `_runtime_data_present`) are reused from
:mod:`check_delete_safe` rather than duplicated, so the two preflight tools
stay in lockstep.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from ..storage import IndexStore, record_savings, estimate_savings, cost_avoided
from ._stop_rule import build_stop_rule
from ._utils import index_status_to_tool_error, resolve_repo
from .check_delete_safe import (
    _is_test_file,
    _resolve_target,
    _runtime_hits,
    _runtime_data_present,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Severity scoring for individual edit blockers (1-5, higher = riskier to touch)
# ---------------------------------------------------------------------------
_SEVERITY_RUNTIME = 5
_SEVERITY_CROSS_REPO = 5
_SEVERITY_SCIP = 5           # compiler-verified caller depends on the contract
_SEVERITY_EXTERNAL_IMPORT = 4
_SEVERITY_DIAGNOSTICS = 4    # the checker already says this symbol is broken
_SEVERITY_COMPLEXITY = 3
_SEVERITY_INTERNAL_REF = 3
_SEVERITY_UNTESTED = 2

# Cyclomatic complexity at/above this is "high" — matches the low/medium/high
# bands used by get_symbol_complexity (1-4 low, 5-10 medium, 11+ high).
_COMPLEXITY_HIGH = 11


def check_edit_safe(
    repo: str,
    symbol: str,
    cross_repo: bool = True,
    include_runtime: bool = True,
    storage_path: Optional[str] = None,
) -> dict:
    """Composite preflight: can this symbol be edited safely?

    Returns one verdict tier, a confidence score (higher = safer to edit
    freely), a ranked list of blockers, and a one-line recommended action.
    Reuses find_importers + check_references + stored complexity + runtime
    evidence; never mutates the codebase.

    Args:
        repo: Repository identifier.
        symbol: Symbol id or name to evaluate.
        cross_repo: Include other indexed repos in the analysis (default True).
        include_runtime: Consult runtime_calls for production evidence (default True).
        storage_path: Custom storage path.

    Returns:
        Dict with ``verdict``, ``confidence``, ``target``, ``blockers`` list,
        ``recommended_action``, per-signal counts, and ``_meta``.
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
    cyclomatic = int(target.get("cyclomatic") or 0)

    blockers: list[dict] = []

    # ── Signal 1: signature impact — file-level importers (cross_repo opt) ──
    # External/cross-repo importers depend on the current signature, so a
    # signature-changing edit breaks them. Test importers are tracked apart.
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
                    "info": "another indexed repo depends on this signature",
                })
            else:
                imp_file = entry.get("file", "")
                if imp_file and imp_file != target_file:
                    if _is_test_file(imp_file):
                        test_import_count += 1
                    else:
                        external_import_count += 1
                        blockers.append({
                            "kind": "external_import",
                            "file": imp_file,
                            "severity": _SEVERITY_EXTERNAL_IMPORT,
                            "info": "external caller depends on the current signature",
                        })
    except Exception as exc:  # noqa: BLE001
        logger.debug("check_edit_safe: find_importers skipped: %s", exc, exc_info=True)

    # ── Signal 2: identifier refs + test-coverage presence ─────────────────
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
                # Same correction as check_delete_safe (#406, v1.108.226): the
                # skip discarded the defining FILE when only the DEFINITION must
                # be excluded, and `check_references` now does that exclusion by
                # line span. A same-file caller is a real caller — for an edit
                # preflight it is the one most likely to break first.
                if _is_test_file(ref_file):
                    test_ref_count += 1
                else:
                    internal_ref_count += 1
                    if internal_ref_count <= 3:
                        blockers.append({
                            "kind": "internal_reference",
                            "file": ref_file,
                            "line": ref.get("line", 0),
                            "severity": _SEVERITY_INTERNAL_REF,
                            "info": "internal call site that may rely on current behavior",
                        })
    except Exception as exc:  # noqa: BLE001
        logger.debug("check_edit_safe: check_references skipped: %s", exc, exc_info=True)

    has_test_coverage = (test_import_count + test_ref_count) > 0
    is_referenced = (external_import_count + cross_repo_count + internal_ref_count) > 0

    # ── Signal 3: complexity (stored at index time, no re-parse) ───────────
    high_complexity = cyclomatic >= _COMPLEXITY_HIGH
    if high_complexity:
        blockers.append({
            "kind": "high_complexity",
            "cyclomatic": cyclomatic,
            "severity": _SEVERITY_COMPLEXITY,
            "info": f"cyclomatic complexity {cyclomatic} (high) — edits are regression-prone",
        })

    # ── Signal 4: untested-but-used ────────────────────────────────────────
    if is_referenced and not has_test_coverage:
        blockers.append({
            "kind": "no_test_coverage",
            "severity": _SEVERITY_UNTESTED,
            "info": "symbol is used but no referencing test files were found",
        })

    # ── Signal 5: runtime evidence (Phase 7) ───────────────────────────────
    runtime_hits = _runtime_hits(store, owner, name, target_id) if include_runtime else None
    runtime_data_present = _runtime_data_present(store, owner, name) if include_runtime else False
    if runtime_hits and runtime_hits > 0:
        blockers.append({
            "kind": "runtime_observed",
            "hit_count": runtime_hits,
            "severity": _SEVERITY_RUNTIME,
            "info": "this symbol executes in production traffic",
        })

    # ── Signal 6: SCIP compiler-verified callers (compile-time evidence) ───
    # A compiler-proven external reference is a caller that depends on the
    # current contract, even when the import graph and text search miss it
    # (dynamic dispatch, barrel re-exports). It raises the same signature_impact
    # concern. Honest no-op without ingested SCIP.
    from ._scip_consume import scip_reference_files, scip_meta_block  # noqa: PLC0415
    scip_external_count = 0
    scip_block: Optional[dict] = None
    _scip_files, _scip_meta, _scip_stale = scip_reference_files(store, owner, name, target_id)
    _scip_files.pop(target_file, None)
    for _f in sorted(_scip_files):
        if _is_test_file(_f):
            continue  # test refs don't constrain a signature edit
        scip_external_count += 1
        blockers.append({
            "kind": "scip_reference", "file": _f, "severity": _SEVERITY_SCIP,
            "verification": "compiler_verified",
            "info": "compiler-verified caller depends on the current contract",
        })
    if scip_external_count:
        scip_block = scip_meta_block(
            _scip_meta, _scip_stale, verified_external_refs=scip_external_count,
        )

    # ── Verdict selection (most-constraining first) ────────────────────────
    signature_impact = (external_import_count + cross_repo_count + scip_external_count) > 0

    if runtime_hits and runtime_hits > 0:
        verdict = "runtime_critical"
    elif signature_impact:
        verdict = "signature_impact"
    elif high_complexity:
        verdict = "complexity_risk"
    elif is_referenced and not has_test_coverage:
        verdict = "untested"
    else:
        verdict = "safe_to_edit"

    # ── Confidence (higher = safer to edit freely) ─────────────────────────
    confidence = {
        "runtime_critical": 0.15,
        "signature_impact": 0.40,
        "complexity_risk": 0.45,
        "untested": 0.55,
        "safe_to_edit": 0.90,
    }[verdict]
    if verdict == "safe_to_edit" and has_test_coverage:
        confidence = 0.95  # low complexity, no callers, and covered by tests

    # ── Recommended action ─────────────────────────────────────────────────
    callers = external_import_count + cross_repo_count + scip_external_count
    tests_note = "" if has_test_coverage else " No test coverage detected — add a characterization test first."
    actions = {
        "runtime_critical": (
            f"Runs in production ({runtime_hits} runtime hit(s)). Edit behind a flag, keep "
            f"behavior backward-compatible, and watch monitoring.{tests_note}"
        ),
        "signature_impact": (
            f"{callers} external/cross-repo caller(s) depend on this. Body edits are safe; "
            f"preserve the signature and return contract, or update callers in lockstep.{tests_note}"
        ),
        "complexity_risk": (
            f"High cyclomatic complexity ({cyclomatic}). Edits are regression-prone — change in "
            f"small steps.{tests_note}"
        ),
        "untested": (
            f"Referenced by {internal_ref_count} site(s) with no test coverage. Add a "
            "characterization test before editing to catch regressions."
        ),
        "safe_to_edit": (
            "Low complexity, no external callers — safe to edit."
            + ("" if has_test_coverage else " (No tests reference it; consider adding one.)")
        ),
    }

    # ── Signal: pre-existing compiler / linter diagnostics (snapshot) ──
    # Read from the `diagnostics` table an `import-trace --diagnostics` run
    # filled. None means NO DATA (never ingested, or a DB that predates the
    # table): nothing is rendered, and `diagnostics_data_present` says so --
    # an absent checker is not a clean checker. A real zero appears only when
    # the checker ran and found nothing on this symbol.
    from ._diagnostics_consume import (  # noqa: PLC0415
        diagnostics_currency, diagnostics_snapshot, live_git_head,
        load_symbol_diagnostics, summarise,
    )
    _db_path = store._sqlite._db_path(owner, name)  # type: ignore[attr-defined]
    diag_map = load_symbol_diagnostics(_db_path, [target_id])
    diag_block: Optional[dict] = None
    diag_note = ""
    if diag_map is not None:
        entry = diag_map.get(target_id) or {"errors": 0, "warnings": 0, "infos": 0, "tools": [], "codes": []}
        snap = diagnostics_snapshot(_db_path) or {}
        as_of = snap.get("as_of")
        diag_block = {
            "errors": entry["errors"],
            "warnings": entry["warnings"],
            "tools": entry["tools"],
            "as_of": as_of,
            "current": diagnostics_currency(as_of, live_git_head(getattr(index, "source_root", None))),
        }
        if entry["errors"] > 0:
            detail = summarise(entry)
            blockers.append({
                "kind": "pre_existing_diagnostics",
                "detail": detail,
                "severity": _SEVERITY_DIAGNOSTICS,
            })
            if diag_block["current"] is True:
                staleness = ""
            elif diag_block["current"] is False:
                staleness = " (snapshot is behind HEAD)"
            else:
                staleness = " (snapshot currency unknown)"
            diag_note = (
                f" The checker already reports {entry['errors']} error(s) here: {detail}{staleness}. "
                "Fix or acknowledge the baseline before editing, or a new error is indistinguishable from an old one."
            )

    # Rank blockers by severity, truncate to top 5
    blockers.sort(key=lambda b: -b.get("severity", 0))
    blockers_out = blockers[:5]

    # Token-savings ledger (cheap response)
    raw_bytes = int(target.get("byte_length", 0) or 0) + 1000
    response_bytes = 800
    tokens_saved = estimate_savings(raw_bytes, response_bytes)
    total_saved = record_savings(tokens_saved, tool_name="check_edit_safe")

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
        "recommended_action": actions[verdict] + diag_note,
        # Executable stop rule beside the certainty language. `terminal` means
        # no further jcodemunch call moves this verdict; it does NOT mean safe.
        # See tools/_stop_rule.py for why this ships by default.
        "stop_rule": build_stop_rule(
            "check_edit_safe",
            verdict,
            cross_repo=cross_repo,
            include_runtime=include_runtime,
            runtime_data_present=runtime_data_present,
        ),
        "signals": {
            "external_import_count": external_import_count,
            "cross_repo_count": cross_repo_count,
            "test_import_count": test_import_count,
            "internal_ref_count": internal_ref_count,
            "test_ref_count": test_ref_count,
            "scip_external_ref_count": scip_external_count,
            "cyclomatic": cyclomatic,
            "has_test_coverage": has_test_coverage,
        },
        "_meta": {
            "timing_ms": round(elapsed, 1),
            "tokens_saved": tokens_saved,
            "total_tokens_saved": total_saved,
            **cost_avoided(tokens_saved, total_saved),
        },
    }
    if scip_block is not None:
        result["_meta"]["scip"] = scip_block
    if runtime_hits is not None:
        result["signals"]["runtime_hits"] = runtime_hits
    if include_runtime:
        result["signals"]["runtime_data_present"] = runtime_data_present
    result["signals"]["diagnostics_data_present"] = diag_block is not None
    if diag_block is not None:
        result["signals"]["diagnostics"] = diag_block
    return result
