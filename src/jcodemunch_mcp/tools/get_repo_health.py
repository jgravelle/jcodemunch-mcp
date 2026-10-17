"""get_repo_health — one-call triage snapshot of a repository.

Aggregates results from individual tools to produce a structured health summary:
  - Symbol and file counts
  - Dead-code estimate (% of functions/methods with high dead-code confidence)
  - Average cyclomatic complexity
  - Top 5 hotspots (complexity × churn)
  - Dependency cycle count
  - Unstable module count (instability > 0.7 in the import graph)

Designed to be the *first* tool called in a new session.  One call gives a complete
triage picture with no follow-up needed.  All heavy lifting is delegated to individual
tools — no logic is duplicated here.
"""

from __future__ import annotations

import re
import time
from typing import Optional

from ..storage import IndexStore
from ._utils import resolve_repo
from .get_dead_code_v2 import get_dead_code_v2
from .get_dependency_cycles import get_dependency_cycles
from .get_hotspots import get_hotspots
from .get_dependency_graph import _build_adjacency
from ._entry_points import entry_point_spec


def _avg_complexity(index) -> float:
    """Mean cyclomatic complexity across all functions/methods with data."""
    values = [
        s.get("cyclomatic") or 0
        for s in index.symbols
        if s.get("kind") in ("function", "method") and (s.get("cyclomatic") or 0) > 0
    ]
    return round(sum(values) / len(values), 2) if values else 0.0


# Directory names that hold non-production code: tests, benchmarks, scripts,
# examples. Files in these directories have Ca=0 by construction (pytest
# collects tests, benchmarks/scripts run from the shell, examples are
# illustrative) so they trivially meet the instability > 0.7 threshold and
# would otherwise dominate the coupling axis for any well-tested project.
# Exclusion applies to both the iteration AND the denominator — see
# _count_unstable_modules below.
_NON_PRODUCTION_DIR_NAMES = frozenset({
    "tests", "test", "benchmarks", "examples", "scripts",
})

# Filename suffixes for ecosystems that co-locate tests with source rather
# than placing them under a tests/ directory:
#   Go:        foo_test.go
#   Jest:      foo.test.{js,jsx,ts,tsx}
#   Jasmine/Karma/Angular/NestJS:  foo.spec.{js,jsx,ts,tsx}
#   RSpec:     foo_spec.rb
#   JUnit:     FooTest.java
# Inline conventions like Rust's #[cfg(test)] mod tests cannot be detected
# by path alone and are out of scope — would need an AST-aware approach.
_NON_PRODUCTION_FILENAME_RE = re.compile(
    r"(?:_test\.go|\.(?:test|spec)\.[jt]sx?|_spec\.rb|Test\.java)$",
    re.IGNORECASE,
)


def _is_production_path(path: str) -> bool:
    """True when the path is neither under a non-production directory nor
    a test-suffix file. See module-level constants for the exact rules."""
    norm = path.replace("\\", "/")
    if any(p in _NON_PRODUCTION_DIR_NAMES for p in norm.split("/")):
        return False
    if _NON_PRODUCTION_FILENAME_RE.search(norm):
        return False
    return True


def _count_unstable_modules(index) -> tuple[int, int, int, Optional[str]]:
    """Return ``(unstable_count, production_total, entry_points_excluded, profile)``.

    Counts files with instability > 0.7 (Ce-dominated) among production
    code only — tests, benchmarks, scripts, and examples are excluded
    from both the numerator AND the denominator. Including them would
    structurally penalize any project with a real test suite.

    Inbound references *from* test files still count toward production
    files' Ca (so well-tested code looks more stable, which is correct).

    ⚠⚠ **Framework entry points are excluded on exactly the same grounds,
    and it took an outside report to notice the rule already existed** (#561,
    @lilubot). The comment above ``_NON_PRODUCTION_DIR_NAMES`` says tests have
    ``Ca=0`` *by construction* and so "trivially meet the instability > 0.7
    threshold". A Next.js ``app/api/**/route.ts`` is the identical case: the
    framework invokes it over HTTP, no module imports it, ``Ca`` is 0 and
    ``I`` is 1.0 with no code-health content whatsoever. Measured on the
    reporting repo: **203 of 366 unstable files were route handlers, 126 of
    them with zero importers.**

    ⚠⚠ **Excluded from the denominator too, and that is the load-bearing
    half.** Dropping them from the numerator alone would have shrunk a count
    without shrinking what it is a fraction of — a silent, self-flattering
    adjustment, the same sign error that took a tree from 84.0 B to 88.8 B
    against a truth of 77.3 C in v1.108.305. Removing them from both leaves
    the *rate* unbiased with respect to route handlers, which is what tests
    already get.

    ⚠ An entry point with a genuinely alarming ``Ce`` is therefore not
    reported by this axis at all. That is a real loss, so the count is
    disclosed rather than dropped quietly — see ``coupling_entry_points_excluded``
    in the response.

    ⚠⚠ **Only the DETECTED framework profile excludes; ``find_dead_code``'s
    ``_ENTRY_POINT_FILENAMES`` deliberately does not.** A repo-root ``main.py``
    is arguably the same case, and widening to that filename list would move
    the published coupling score of every Python repository we have ever
    graded — including the observatory's own baselines — on a heuristic rather
    than a detection. The profile is read off ``package.json`` / ``next.config``
    and names the convention the framework itself imposes. Widening is a
    separate decision with a separate before/after measurement, not a tidy-up.
    """
    if not index.imports:
        return 0, 0, 0, None
    source_files = frozenset(index.source_files)
    alias_map = getattr(index, "alias_map", None)
    fwd = _build_adjacency(index.imports, source_files, alias_map, getattr(index, "psr4_map", None))

    # Build reverse (importers per file). The graph still includes test
    # imports — they credit production Ca and that's the correct shape.
    rev: dict[str, list] = {}
    for src, targets in fwd.items():
        for tgt in targets:
            rev.setdefault(tgt, []).append(src)

    spec = entry_point_spec(index)
    candidates = [f for f in index.source_files if _is_production_path(f)]
    production_files = [f for f in candidates if not spec.matches(f)]
    excluded = len(candidates) - len(production_files)

    unstable = 0
    for f in production_files:
        ca = len(rev.get(f, []))
        ce = len(fwd.get(f, []))
        total = ca + ce
        if total > 0 and (ce / total) > 0.7:
            unstable += 1
    return unstable, len(production_files), excluded, spec.profile_name


def get_repo_health(
    repo: str,
    days: int = 90,
    storage_path: Optional[str] = None,
) -> dict:
    """Return a one-call triage snapshot of the repository.

    Aggregates: symbol counts, dead code %, avg complexity, top hotspots,
    dependency cycle count, and unstable module count.

    Args:
        repo:         Repository identifier (owner/repo or bare name).
        days:         Churn look-back window for hotspot calculation (default 90).
        storage_path: Optional index storage path override.

    Returns:
        ``{repo, summary, top_hotspots, cycle_count, cycles_sample,
           unstable_modules, dead_code_pct, avg_complexity,
           total_symbols, total_files, _meta}``
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

    total_files = len(index.source_files)
    total_symbols = len(index.symbols)
    fn_method_count = sum(
        1 for s in index.symbols if s.get("kind") in ("function", "method")
    )

    # Dead code estimate (min_confidence=0.67 = 2 of 3 signals)
    dead_result = get_dead_code_v2(
        repo=f"{owner}/{name}", min_confidence=0.67, storage_path=storage_path
    )
    # ⚠⚠ The count is v2's UNCAPPED `total_matches`, not `len(dead_symbols)`.
    # v2 caps the list at `max_results` (default 100) for token cost and
    # reports the true total beside it; dividing the capped list by every
    # function/method understated `dead_code_pct` on any repo with more
    # than 100 candidates. Denominator stays every function/method symbol —
    # the same population v2's own `total_analysed` counts.
    dead_count = dead_result.get("_meta", {}).get(
        "total_matches", len(dead_result.get("dead_symbols", []))
    )
    dead_code_pct = (
        round(dead_count / fn_method_count * 100, 1) if fn_method_count > 0 else 0.0
    )
    # ⚠⚠ COULD-NOT-MEASURE is not ZERO (#562, @lilubot). When too few of
    # v2's signals discriminate it returns `dead_symbols: []` **and says so in
    # `signal_warning`** -- an honest refusal. Reading only the list turns that
    # refusal into `dead_code_pct: 0.0` and a dead_code axis of 100, i.e. the
    # strongest possible claim built from an explicit admission that nothing
    # was established. Same defect as v1.108.305's `churn_surface` on a shallow
    # clone, and the mechanism that fixed it is right here.
    #
    # ⚠ `dead_code_pct` is still REPORTED, because a caller asking for the
    # number should get whatever was computed. It is the composite and the
    # GRADE that are withheld -- withholding both the number and the grade
    # would hide that a refusal happened.
    dead_code_measurable = not dead_result.get("signal_warning")

    # Avg complexity
    avg_complexity = _avg_complexity(index)

    # Top hotspots
    hotspot_result = get_hotspots(
        repo=f"{owner}/{name}", top_n=5, days=days, storage_path=storage_path
    )
    top_hotspots = hotspot_result.get("hotspots", [])

    # Dependency cycles
    cycles_result = get_dependency_cycles(
        repo=f"{owner}/{name}", storage_path=storage_path
    )
    cycles = cycles_result.get("cycles", [])
    cycle_count = len(cycles)
    cycles_sample = cycles[:3]  # Show first 3 examples

    # Unstable modules — `coupling_total` excludes tests/benchmarks/scripts
    # and is the correct denominator for the coupling axis. `total_files`
    # in the response stays as the unfiltered count.
    (
        unstable_count,
        coupling_total,
        coupling_entry_points_excluded,
        coupling_profile,
    ) = _count_unstable_modules(index)

    # Build a human-readable summary line
    health_issues: list[str] = []
    if cycle_count > 0:
        health_issues.append(f"{cycle_count} dependency cycle(s)")
    if dead_code_pct >= 10:
        health_issues.append(f"{dead_code_pct}% likely-dead functions")
    if avg_complexity >= 10:
        health_issues.append(f"avg complexity {avg_complexity} (high)")
    elif avg_complexity >= 5:
        health_issues.append(f"avg complexity {avg_complexity} (medium)")
    if unstable_count > 0:
        health_issues.append(f"{unstable_count} unstable module(s)")

    if not health_issues:
        summary = "Healthy — no major issues detected."
    else:
        summary = "Issues found: " + "; ".join(health_issues) + "."

    # Six-axis radar (see health_radar.py). Test-gap and churn-surface inputs
    # are best-effort — failures degrade gracefully and the relevant axis
    # is omitted from the radar's composite.
    untested_pct: Optional[float] = None
    try:
        from .get_untested_symbols import get_untested_symbols
        untested = get_untested_symbols(
            repo=f"{owner}/{name}",
            min_confidence=0.5,
            max_results=1,  # we only need the count
            storage_path=storage_path,
        )
        if "error" not in untested:
            reached_pct = float(untested.get("reached_pct", 0))
            untested_pct = max(0.0, 100.0 - reached_pct)
    except Exception:
        pass

    top_hotspot_score: Optional[float] = None
    if top_hotspots:
        try:
            top_hotspot_score = float(top_hotspots[0].get("hotspot_score", 0))
        except (TypeError, ValueError):
            top_hotspot_score = None

    # ⚠⚠ A truncated history makes `churn_surface` UNMEASURABLE, not zero.
    # `git log --since=N days` on a shallow clone returns a small count and exit
    # 0, so the axis ranks nothing but complexity and the composite comes out
    # FLATTERING -- measured at one identical commit: 81.3 (B) shallow versus
    # 75.6 (C) full, with churn_surface the only axis that moved.
    #
    # ⚠ Passing None omits the axis, which is the convention this radar already
    # uses for `test_gap` and `runtime_coverage`. The composite then rests on
    # what was actually measured and `omitted_axes` says so. Publishing the
    # number instead would be reporting a measurement we could not make.
    unmeasurable_axes: list[str] = []
    if not dead_code_measurable:
        unmeasurable_axes.append("dead_code")
    if hotspot_result.get("churn_measurable") is False:
        top_hotspot_score = None
        unmeasurable_axes.append("churn_surface")

    # Phase 7: optional 7th radar axis — runtime_coverage. Sourced from
    # get_runtime_coverage so the same coverage_pct that a caller would
    # see via the dedicated tool is what feeds the radar. Failures
    # (missing index column, no traces, pre-v14 DB) just leave the axis
    # omitted — the composite then aggregates over the existing 6.
    runtime_coverage_pct: Optional[float] = None
    try:
        from .get_runtime_coverage import get_runtime_coverage
        rc = get_runtime_coverage(
            repo=f"{owner}/{name}",
            storage_path=storage_path,
        )
        if "error" not in rc:
            sources = rc.get("sources") or []
            if sources:
                runtime_coverage_pct = float(rc.get("coverage_pct", 0))
    except Exception:
        pass

    from .health_radar import compute_radar
    radar = compute_radar(
        avg_complexity=avg_complexity,
        dead_code_pct=dead_code_pct,
        cycle_count=cycle_count,
        unstable_modules=unstable_count,
        total_files=coupling_total,  # production-code denominator
        untested_pct=untested_pct,
        top_hotspot_score=top_hotspot_score,
        unmeasurable_axes=unmeasurable_axes or None,
        runtime_coverage_pct=runtime_coverage_pct,
    )

    elapsed = (time.perf_counter() - t0) * 1000
    return {
        "repo": f"{owner}/{name}",
        "summary": summary,
        "total_files": total_files,
        "total_symbols": total_symbols,
        "fn_method_count": fn_method_count,
        "avg_complexity": avg_complexity,
        "dead_code_pct": dead_code_pct,
        # ⚠ False means the tool refused, NOT that the repo is clean. The
        # reason it refused is `dead_code_signal_warning`.
        "dead_code_measurable": dead_code_measurable,
        "dead_code_signal_warning": dead_result.get("signal_warning"),
        "dead_count": dead_count,
        "cycle_count": cycle_count,
        "cycles_sample": cycles_sample,
        "unstable_modules": unstable_count,
        # ⚠ Disclosed, not dropped (#561). These files left BOTH sides of the
        # coupling ratio because their Ca is 0 by construction, so the axis
        # says nothing about them either way -- and a reader who cannot see
        # the count cannot tell a clean repo from one where the excluded set
        # was most of it.
        "coupling_entry_points_excluded": coupling_entry_points_excluded,
        "coupling_framework_profile": coupling_profile,
        "top_hotspots": top_hotspots,
        "radar": radar,
        "_meta": {
            "timing_ms": round(elapsed, 1),
            "days": days,
            "methodology": "aggregate",
            "confidence_level": "medium",
        },
    }
