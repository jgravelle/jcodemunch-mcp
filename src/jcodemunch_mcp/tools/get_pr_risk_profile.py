"""get_pr_risk_profile — unified risk assessment for a branch or PR.

Fuses six orthogonal signals into a single scored report:

  1. **Changed symbols** — what actually moved between two SHAs
  2. **Blast radius** — aggregate downstream dependents across all changes
  3. **Complexity** — cyclomatic/nesting scores for every touched symbol
  4. **Churn** — historical volatility of touched files
  5. **Test gaps** — changed symbols with no test-file reachability
  6. **Runtime traffic** *(Phase 7, optional)* — log of runtime hit count
     for the symbols this PR touches; lets the score tell the difference
     between editing a function called 1M times/day and editing one
     that has run zero times this quarter. Zero-cost when no traces have
     been ingested — falls back to the five-signal mix.

Each signal contributes to a composite **risk_score** (0.0–1.0) with an
overall **risk_level** (low / medium / high / critical).

When traces *have* been ingested, the response also carries
``runtime_dark_code_introduced`` — True when the PR adds symbols whose
file has no runtime evidence at all (likely-unreachable additions, or a
trace blind-spot that needs widening before the change ships).

Designed for CI integration (exit code gating) and the ``/review`` workflow.
Requires a locally indexed repo (``index_folder``).
"""

from __future__ import annotations

import logging
import math
import sqlite3
import time
from typing import Optional

from ..storage import IndexStore
from ..storage.generation import connect_readonly
from ..parser.imports import resolve_specifier
from ._utils import resolve_repo

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Risk scoring weights (tuned for production signal balance)
# ---------------------------------------------------------------------------
# Two weight regimes — chosen at request time based on whether the
# index has any runtime evidence:
#
#   * Static-only (the historical default): five signals summing to 1.0.
#   * Runtime-aware (Phase 7): six signals summing to 1.0; static weights
#     are scaled by 0.85 to make room for a 0.15 runtime-traffic weight.
#
# The static-only regime is preserved bit-for-bit so the score is
# backwards-compatible against pre-Phase-7 baselines on repos that don't
# ingest runtime data.
_W_BLAST = 0.30     # blast radius breadth
_W_COMPLEXITY = 0.25  # max cyclomatic among changed symbols
_W_CHURN = 0.15     # historical volatility
_W_TEST_GAP = 0.20  # untested changed symbols
_W_VOLUME = 0.10    # sheer volume of changes
_W_RUNTIME = 0.15   # log(runtime hits) for changed symbols (Phase 7)

# Normalisation constant for runtime hit counts — log1p(1M) ≈ 13.8 maps
# to a runtime score of 1.0. Anything below 1M traffic on a single
# symbol gets a proportional fraction.
_RUNTIME_HITS_NORM = math.log1p(1_000_000)

_RISK_THRESHOLDS = {
    "low": 0.25,
    "medium": 0.50,
    "high": 0.75,
    # anything above 0.75 = critical
}


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _load_runtime_signal_for_changed(
    db_path,
    changed_symbol_ids: list[str],
    changed_files: list[str],
) -> tuple[dict[str, int], frozenset[str], bool]:
    """One-shot read of runtime evidence for the symbols this PR touches.

    Returns:
        (per_symbol_hits, files_with_traces, runtime_present)
          - ``per_symbol_hits[sid]`` = total runtime hit count for that
            symbol across all sources (runtime_calls + runtime_stack_events).
          - ``files_with_traces`` = files with at least one runtime hit on
            any symbol (used by the dark-code detector).
          - ``runtime_present`` = whether any runtime row exists at all;
            False short-circuits the entire Phase 7 augmentation so the
            score is identical to the pre-Phase-7 mix.

    Read-only / immutable connection so the LRU cache isn't evicted by
    an mtime bump (matches the Phase 2 confidence-probe pattern).
    """
    per_sym: dict[str, int] = {}
    file_set: set[str] = set()
    if not db_path.exists():
        return per_sym, frozenset(), False
    try:
        conn = connect_readonly(db_path, isolation_level="")
    except sqlite3.OperationalError:
        return per_sym, frozenset(), False
    conn.row_factory = sqlite3.Row
    try:
        try:
            present = conn.execute(
                "SELECT 1 FROM runtime_calls LIMIT 1"
            ).fetchone() is not None
        except sqlite3.OperationalError:
            return per_sym, frozenset(), False
        if not present:
            return per_sym, frozenset(), False

        # Per-symbol hit counts. Using IN with an executemany-ish list is
        # awkward in sqlite3; a single LEFT JOIN against a temp table is
        # cleaner — but for the small N (changed-symbol count usually
        # under a few hundred) a single SELECT with VALUES is fine.
        if changed_symbol_ids:
            placeholders = ",".join("?" * len(changed_symbol_ids))
            rc_rows = conn.execute(
                f"SELECT symbol_id, SUM(count) AS n FROM runtime_calls "
                f"WHERE symbol_id IN ({placeholders}) GROUP BY symbol_id",
                tuple(changed_symbol_ids),
            ).fetchall()
            for r in rc_rows:
                per_sym[r["symbol_id"]] = int(r["n"] or 0)
            try:
                rs_rows = conn.execute(
                    f"SELECT symbol_id, SUM(count) AS n FROM runtime_stack_events "
                    f"WHERE symbol_id IN ({placeholders}) GROUP BY symbol_id",
                    tuple(changed_symbol_ids),
                ).fetchall()
                for r in rs_rows:
                    per_sym[r["symbol_id"]] = per_sym.get(r["symbol_id"], 0) + int(r["n"] or 0)
            except sqlite3.OperationalError:
                # Pre-v16 DB without runtime_stack_events — runtime_calls only is fine.
                pass

        # Files with at least one runtime hit on any symbol — used by the
        # "is this PR touching a trace blind-spot?" check.
        for r in conn.execute(
            "SELECT DISTINCT s.file FROM symbols s JOIN runtime_calls rc ON rc.symbol_id = s.id"
        ):
            f = r[0] or ""
            if f:
                file_set.add(f)
    finally:
        conn.close()
    return per_sym, frozenset(file_set), True


def _runtime_traffic_score(per_sym_hits: dict[str, int]) -> float:
    """Map per-symbol hit counts to a 0..1 risk component.

    Uses log1p so a 10x hit-count delta becomes a single-unit score
    delta — matches the standard "log of traffic" intuition. Average
    over the changed symbols (not max) so a PR touching a hot function
    *plus* twenty cold ones isn't scored the same as one touching only
    the hot function — both should rank below "every symbol is hot".
    """
    if not per_sym_hits:
        return 0.0
    contributions = [math.log1p(c) / _RUNTIME_HITS_NORM for c in per_sym_hits.values()]
    if not contributions:
        return 0.0
    return _clamp01(sum(contributions) / len(contributions))


def _build_reverse_adjacency(
    imports: dict, source_files: frozenset, alias_map: Optional[dict] = None,
    psr4_map: Optional[dict] = None,
) -> dict[str, list[str]]:
    """Return {file: [files_that_import_it]} from raw import data."""
    rev: dict[str, list[str]] = {}
    for src_file, file_imports in imports.items():
        for imp in file_imports:
            target = resolve_specifier(imp["specifier"], src_file, source_files, alias_map, psr4_map)
            if target and target != src_file:
                rev.setdefault(target, []).append(src_file)
    return {k: list(dict.fromkeys(v)) for k, v in rev.items()}


def _is_test_file(path: str) -> bool:
    """Quick heuristic for test files."""
    p = path.lower().replace("\\", "/")
    return (
        "/test" in p or "/tests/" in p or "/__tests__/" in p
        or p.startswith("test") or p.startswith("tests/")
        or "_test." in p or ".test." in p or ".spec." in p
        or "_spec." in p or "test_" in p.split("/")[-1]
    )


def get_pr_risk_profile(
    repo: str,
    base_ref: Optional[str] = None,
    head_ref: str = "HEAD",
    days: int = 90,
    storage_path: Optional[str] = None,
) -> dict:
    """Produce a unified risk assessment for all changes between two refs.

    Args:
        repo:         Repository identifier (owner/repo or bare name).
        base_ref:     Base SHA/ref to compare from. Defaults to the SHA stored at index time.
        head_ref:     Head SHA/ref to compare to (default "HEAD").
        days:         Churn look-back window in days (default 90).
        storage_path: Optional index storage path override.

    Returns:
        Dict with:
          - risk_score: float 0.0–1.0
          - risk_level: "low" / "medium" / "high" / "critical"
          - signal_breakdown: per-signal scores and contributing data
          - changed_symbols_count, blast_radius_files, untested_count
          - hottest_symbols: top-5 riskiest symbols with scores
          - recommendations: actionable guidance based on signals
          - _meta
    """
    t0 = time.perf_counter()

    try:
        owner, name = resolve_repo(repo, storage_path)
    except ValueError as e:
        return {"error": str(e)}

    store = IndexStore(base_path=storage_path)
    index = store.load_index(owner, name)
    if index is None:
        return {"error": f"No index found for {repo!r}. Run index_folder first."}

    if not index.source_root:
        return {
            "error": (
                "get_pr_risk_profile requires a locally indexed repo (index_folder). "
                "GitHub-indexed repos do not have a local git working tree."
            )
        }

    # -------------------------------------------------------------------
    # Step 1: Get changed symbols via the existing tool
    # -------------------------------------------------------------------
    from .get_changed_symbols import get_changed_symbols

    diff_result = get_changed_symbols(
        repo=repo,
        since_sha=base_ref,
        until_sha=head_ref,
        include_blast_radius=False,
        suppress_meta=True,
        storage_path=storage_path,
    )
    if "error" in diff_result:
        return diff_result

    all_changed = (
        diff_result.get("changed_symbols", [])
        + diff_result.get("added_symbols", [])
        + diff_result.get("removed_symbols", [])
    )
    changed_files = diff_result.get("changed_files", [])
    from_sha = diff_result.get("from_sha", "")
    to_sha = diff_result.get("to_sha", "")

    if not all_changed and not changed_files:
        elapsed = (time.perf_counter() - t0) * 1000
        return {
            "repo": f"{owner}/{name}",
            "from_sha": from_sha,
            "to_sha": to_sha,
            "risk_score": 0.0,
            "risk_level": "low",
            "changed_symbols_count": 0,
            "changed_files_count": 0,
            "signal_breakdown": {},
            "hottest_symbols": [],
            "recommendations": ["No symbol changes detected between the two refs."],
            "_meta": {"timing_ms": round((time.perf_counter() - t0) * 1000, 1)},
        }

    # -------------------------------------------------------------------
    # Step 2: Blast radius (aggregate across all changed files)
    # -------------------------------------------------------------------
    blast_files: set[str] = set()
    if index.imports is not None:
        source_files = frozenset(index.source_files)
        rev_adj = _build_reverse_adjacency(
            index.imports, source_files, index.alias_map,
            getattr(index, "psr4_map", None),
        )
        for f in changed_files:
            # Direct importers only (depth=1) for aggregate scoring
            for importer in rev_adj.get(f, []):
                blast_files.add(importer)
        # Remove the changed files themselves from blast count
        blast_files -= set(changed_files)

    total_files = len(index.source_files) if index.source_files else 1
    blast_ratio = len(blast_files) / total_files
    # Sigmoid-like scaling: small blast ratios stay small, large ones saturate
    signal_blast = _clamp01(1 - math.exp(-5 * blast_ratio))

    # -------------------------------------------------------------------
    # Step 3: Complexity signal (max cyclomatic among changed symbols)
    # -------------------------------------------------------------------
    sym_index = {s.get("id", ""): s for s in index.symbols}
    max_cyclomatic = 0
    complexities: list[dict] = []
    for cs in all_changed:
        sid = cs.get("symbol_id", "")
        idx_sym = sym_index.get(sid)
        if idx_sym:
            cyc = idx_sym.get("cyclomatic", 0) or 0
            if cyc > 0:
                complexities.append({
                    "symbol": cs.get("name", ""),
                    "file": cs.get("file", ""),
                    "cyclomatic": cyc,
                    "max_nesting": idx_sym.get("max_nesting", 0) or 0,
                })
            max_cyclomatic = max(max_cyclomatic, cyc)

    # Normalise: cyclomatic 1-5 = low, 10 = medium, 20+ = high
    signal_complexity = _clamp01(math.log1p(max_cyclomatic) / math.log1p(30))

    # -------------------------------------------------------------------
    # Step 4: Churn signal (historical volatility of touched files)
    # -------------------------------------------------------------------
    from .get_hotspots import _run_git as _hotspot_git, _get_file_churn

    cwd = index.source_root
    file_churn: dict[str, int] = {}
    git_ok = False
    rc, _, _ = _hotspot_git(["rev-parse", "--git-dir"], cwd=cwd)
    if rc == 0:
        git_ok = True
        file_churn = _get_file_churn(cwd, days)
        # Normalise paths
        file_churn = {k.replace("\\", "/"): v for k, v in file_churn.items()}

    total_churn = 0
    for f in changed_files:
        total_churn += file_churn.get(f.replace("\\", "/"), 0)

    avg_churn = total_churn / max(len(changed_files), 1)
    # Normalise: 1-3 commits/file = low, 10 = medium, 20+ = high
    signal_churn = _clamp01(math.log1p(avg_churn) / math.log1p(25))

    # -------------------------------------------------------------------
    # Step 5: Test gap signal (changed symbols without test reachability)
    # -------------------------------------------------------------------
    untested_symbols: list[dict] = []
    test_file_set = frozenset(f for f in index.source_files if _is_test_file(f))

    if index.imports is not None:
        for cs in all_changed:
            cs_file = cs.get("file", "")
            cs_name = cs.get("name", "")
            if not cs_file or not cs_name:
                continue
            # Skip test files themselves
            if _is_test_file(cs_file):
                continue
            # Check: does any test file import this file?
            test_importers = [f for f in rev_adj.get(cs_file, []) if f in test_file_set]
            if not test_importers:
                untested_symbols.append({
                    "symbol": cs_name,
                    "file": cs_file,
                    "change_type": cs.get("change_type", "unknown"),
                })

    non_test_changed = [cs for cs in all_changed if not _is_test_file(cs.get("file", ""))]
    untested_ratio = len(untested_symbols) / max(len(non_test_changed), 1)
    signal_test_gap = _clamp01(untested_ratio)

    # -------------------------------------------------------------------
    # Step 6: Volume signal (sheer scope of changes)
    # -------------------------------------------------------------------
    sym_count = len(all_changed)
    file_count = len(changed_files)
    # Normalise: 1-5 symbols = low, 20 = medium, 50+ = high
    signal_volume = _clamp01(math.log1p(sym_count) / math.log1p(60))

    # -------------------------------------------------------------------
    # Step 7 (Phase 7): Runtime traffic weight + dark-code flag.
    # Zero-cost when no traces have been ingested — falls through to
    # the historical five-signal mix.
    # -------------------------------------------------------------------
    db_path = store._sqlite._db_path(owner, name)  # type: ignore[attr-defined]
    changed_symbol_ids = [
        cs.get("symbol_id", "") for cs in all_changed if cs.get("symbol_id")
    ]
    per_sym_hits, files_with_traces, runtime_present = _load_runtime_signal_for_changed(
        db_path,
        changed_symbol_ids,
        changed_files,
    )
    signal_runtime = _runtime_traffic_score(per_sym_hits) if runtime_present else 0.0

    # Dark-code detector: any *added* symbol whose file has no runtime
    # evidence at all. Flips True only when traces exist (otherwise every
    # file looks dark and the flag is meaningless).
    runtime_dark_code_introduced = False
    dark_code_files: list[str] = []
    if runtime_present:
        added = diff_result.get("added_symbols", []) or []
        for sym in added:
            f = (sym.get("file") or "").replace("\\", "/")
            if not f:
                continue
            if f in files_with_traces:
                continue
            if _is_test_file(f):
                continue
            runtime_dark_code_introduced = True
            if f not in dark_code_files:
                dark_code_files.append(f)

    # -------------------------------------------------------------------
    # Composite score
    # -------------------------------------------------------------------
    if runtime_present:
        # Six-signal regime: rebalance the historical five so the new
        # runtime weight (0.15) slots in without inflating the total.
        scale = 1.0 - _W_RUNTIME
        risk_score = round(
            scale * (
                _W_BLAST * signal_blast
                + _W_COMPLEXITY * signal_complexity
                + _W_CHURN * signal_churn
                + _W_TEST_GAP * signal_test_gap
                + _W_VOLUME * signal_volume
            )
            + _W_RUNTIME * signal_runtime,
            4,
        )
    else:
        risk_score = round(
            _W_BLAST * signal_blast
            + _W_COMPLEXITY * signal_complexity
            + _W_CHURN * signal_churn
            + _W_TEST_GAP * signal_test_gap
            + _W_VOLUME * signal_volume,
            4,
        )

    if risk_score <= _RISK_THRESHOLDS["low"]:
        risk_level = "low"
    elif risk_score <= _RISK_THRESHOLDS["medium"]:
        risk_level = "medium"
    elif risk_score <= _RISK_THRESHOLDS["high"]:
        risk_level = "high"
    else:
        risk_level = "critical"

    # -------------------------------------------------------------------
    # Hottest symbols (mini-hotspot score per changed symbol)
    # -------------------------------------------------------------------
    hottest: list[dict] = []
    for cs in all_changed:
        cs_file = cs.get("file", "").replace("\\", "/")
        sid = cs.get("symbol_id", "")
        idx_sym = sym_index.get(sid)
        cyc = (idx_sym.get("cyclomatic", 0) or 0) if idx_sym else 0
        churn = file_churn.get(cs_file, 0)
        score = round(cyc * math.log1p(churn), 4) if cyc else 0.0
        hottest.append({
            "symbol": cs.get("name", ""),
            "file": cs.get("file", ""),
            "change_type": cs.get("change_type", "unknown"),
            "cyclomatic": cyc,
            "file_churn": churn,
            "hotspot_score": score,
        })
    hottest.sort(key=lambda x: -x["hotspot_score"])
    top_5 = hottest[:5]

    # -------------------------------------------------------------------
    # Recommendations
    # -------------------------------------------------------------------
    recommendations: list[str] = []
    if signal_test_gap > 0.5:
        recommendations.append(
            f"{len(untested_symbols)} changed symbol(s) have no test coverage. "
            "Add tests before merging to prevent regressions."
        )
    if signal_blast > 0.5:
        recommendations.append(
            f"High blast radius: {len(blast_files)} files depend on changed code. "
            "Review downstream consumers carefully."
        )
    if signal_complexity > 0.6:
        recommendations.append(
            f"High complexity (max cyclomatic: {max_cyclomatic}). "
            "Consider extracting complex logic into smaller functions."
        )
    if signal_churn > 0.5:
        recommendations.append(
            f"Volatile files (avg {avg_churn:.1f} commits/{days}d). "
            "High-churn areas are statistically more bug-prone."
        )
    if runtime_present and signal_runtime > 0.5:
        # The runtime signal is the most actionable single piece of data
        # in the response — surface it explicitly when it crosses the bar.
        max_hits = max(per_sym_hits.values()) if per_sym_hits else 0
        recommendations.append(
            f"High runtime traffic on touched code (peak {max_hits:,} hits in the window). "
            "Treat this PR as production-critical — confirm rollout has a fast rollback path."
        )
    if runtime_dark_code_introduced:
        recommendations.append(
            f"PR introduces symbols in {len(dark_code_files)} file(s) that have no runtime "
            "evidence at all. Either the new code is unreachable, or your trace coverage has "
            "a blind spot — investigate before merging. "
            f"Files: {', '.join(dark_code_files[:5])}"
            + (f" (+{len(dark_code_files) - 5} more)" if len(dark_code_files) > 5 else "")
        )
    if not recommendations:
        recommendations.append("No major risk signals detected. Routine review recommended.")

    # Effective weights — the static-only mix is unchanged; the
    # runtime-aware mix scales each static weight by 0.85 to make room
    # for the 0.15 runtime weight. Surfaced in _meta.weights so callers
    # can reproduce the score without consulting source.
    if runtime_present:
        scale = 1.0 - _W_RUNTIME
        eff_weights = {
            "blast_radius": round(_W_BLAST * scale, 4),
            "complexity": round(_W_COMPLEXITY * scale, 4),
            "churn": round(_W_CHURN * scale, 4),
            "test_gap": round(_W_TEST_GAP * scale, 4),
            "volume": round(_W_VOLUME * scale, 4),
            "runtime_traffic": _W_RUNTIME,
        }
    else:
        eff_weights = {
            "blast_radius": _W_BLAST,
            "complexity": _W_COMPLEXITY,
            "churn": _W_CHURN,
            "test_gap": _W_TEST_GAP,
            "volume": _W_VOLUME,
        }

    signal_breakdown = {
        "blast_radius": {
            "score": round(signal_blast, 4),
            "weight": eff_weights["blast_radius"],
            "affected_files": len(blast_files),
        },
        "complexity": {
            "score": round(signal_complexity, 4),
            "weight": eff_weights["complexity"],
            "max_cyclomatic": max_cyclomatic,
        },
        "churn": {
            "score": round(signal_churn, 4),
            "weight": eff_weights["churn"],
            "avg_file_churn": round(avg_churn, 1),
        },
        "test_gap": {
            "score": round(signal_test_gap, 4),
            "weight": eff_weights["test_gap"],
            "untested_symbols": len(untested_symbols),
        },
        "volume": {
            "score": round(signal_volume, 4),
            "weight": eff_weights["volume"],
            "symbols_changed": sym_count,
        },
    }
    if runtime_present:
        max_hits = max(per_sym_hits.values()) if per_sym_hits else 0
        signal_breakdown["runtime_traffic"] = {
            "score": round(signal_runtime, 4),
            "weight": _W_RUNTIME,
            "max_symbol_hits": max_hits,
            "symbols_with_runtime": len(per_sym_hits),
        }

    elapsed = (time.perf_counter() - t0) * 1000
    response: dict = {
        "repo": f"{owner}/{name}",
        "from_sha": from_sha,
        "to_sha": to_sha,
        "risk_score": risk_score,
        "risk_level": risk_level,
        "changed_symbols_count": sym_count,
        "changed_files_count": file_count,
        "blast_radius_files": len(blast_files),
        "untested_count": len(untested_symbols),
        "signal_breakdown": signal_breakdown,
        "hottest_symbols": top_5,
        "untested_symbols": untested_symbols[:10],  # cap output
        "recommendations": recommendations,
        "_meta": {
            "timing_ms": round(elapsed, 1),
            "methodology": (
                "weighted_multi_signal_fusion_v2_runtime_aware"
                if runtime_present
                else "weighted_multi_signal_fusion"
            ),
            "confidence_level": "high" if git_ok else "medium",
            "runtime_data_present": runtime_present,
            "weights": eff_weights,
            "tip": (
                "risk_score = weighted fusion of "
                + ("6" if runtime_present else "5")
                + " signals (0.0–1.0). "
                "risk_level thresholds: low ≤0.25, medium ≤0.50, high ≤0.75, critical >0.75. "
                "Use signal_breakdown to understand which factors drive the score. "
                "hottest_symbols = highest individual risk per changed symbol."
                + (
                    " runtime_dark_code_introduced=True means the PR adds code in a file "
                    "that has zero runtime evidence — review before merging."
                    if runtime_present
                    else ""
                )
            ),
        },
    }
    if runtime_present:
        response["runtime_dark_code_introduced"] = runtime_dark_code_introduced
        if dark_code_files:
            response["runtime_dark_code_files"] = dark_code_files

    # Compiler-diagnostics snapshot over the changed set -- REPORTED, NOT
    # SCORED. A seventh weight moves every published grade and needs its own
    # measurement (the _count_unstable_modules sign error is the precedent);
    # the block is the deliverable. None = no data ingested = nothing rendered.
    from ._diagnostics_consume import (  # noqa: PLC0415
        diagnostics_currency, diagnostics_snapshot, live_git_head, load_symbol_diagnostics,
    )
    diag_map = load_symbol_diagnostics(db_path, changed_symbol_ids)
    if diag_map is not None:
        snap = diagnostics_snapshot(db_path) or {}
        with_errors = []
        total_err = total_warn = 0
        for cs in all_changed:
            d = diag_map.get(cs.get("symbol_id", ""))
            if d is None:
                continue
            total_err += d["errors"]
            total_warn += d["warnings"]
            if d["errors"] > 0:
                with_errors.append({
                    "symbol_id": cs.get("symbol_id", ""),
                    "name": cs.get("name", ""),
                    "file": cs.get("file", ""),
                    "errors": d["errors"],
                    "tools": d["tools"],
                })
        response["diagnostics"] = {
            "basis": "reported_not_scored",
            "errors": total_err,
            "warnings": total_warn,
            "changed_symbols_with_errors": with_errors,
            "tools": snap.get("tools", []),
            "as_of": snap.get("as_of"),
            "current": diagnostics_currency(snap.get("as_of"), live_git_head(cwd)),
        }
    return response
