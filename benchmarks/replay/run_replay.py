"""Replay benchmark harness — run a fixture against the live indexer
and compute ranking-quality metrics.

Usage:

    PYTHONPATH=src python benchmarks/replay/run_replay.py \\
        --fixture benchmarks/replay/fixtures/self_v1_75_0.json

    # With a baseline gate (fails on >2% regression in any metric)
    PYTHONPATH=src python benchmarks/replay/run_replay.py \\
        --fixture benchmarks/replay/fixtures/self_v1_75_0.json \\
        --baseline 1.75.0 --gate $(python -m harness threshold replay.max_relative_drop)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from jcodemunch_mcp import __version__ as _PKG_VERSION  # noqa: E402
from jcodemunch_mcp.tools.search_symbols import search_symbols  # noqa: E402

from metrics import aggregate, mrr_at_k, ndcg_at_k, recall_at_k  # noqa: E402


def _resolve_version() -> str:
    """Prefer the installed package version; fall back to pyproject.toml.

    The latter matters when the harness runs against a freshly-bumped
    version that hasn't been pip-reinstalled yet.
    """
    if _PKG_VERSION and _PKG_VERSION != "unknown":
        return _PKG_VERSION
    pyproject = REPO_ROOT / "pyproject.toml"
    if pyproject.exists():
        for line in pyproject.read_text().splitlines():
            if line.strip().startswith("version"):
                # version = "1.75.0"
                bits = line.split("=", 1)
                if len(bits) == 2:
                    return bits[1].strip().strip('"').strip("'")
    return "unknown"


__version__ = _resolve_version()


def _run_query(repo: str, query: str, k: int, storage_path: str | None) -> list[str]:
    out = search_symbols(
        repo=repo,
        query=query,
        max_results=k,
        storage_path=storage_path,
    )
    if "error" in out:
        return []
    return [r.get("id", r.get("symbol_id", "")) for r in out.get("results", [])]


def run_fixture(
    fixture_path: Path,
    *,
    k: int = 10,
    storage_path: str | None = None,
    repo_override: str | None = None,
) -> dict:
    fixture = json.loads(fixture_path.read_text())
    # The fixture's repo id is a hash of the absolute index path, which differs
    # across machines (e.g. a CI runner). The queries and expected symbol ids are
    # repo-relative and portable, so a caller can override just the container id.
    repo = repo_override or fixture["repo"]
    queries = fixture.get("queries", [])
    per_query: list[dict] = []
    for entry in queries:
        q = entry["query"]
        relevant = entry.get("expected_top_k", [])
        predicted = _run_query(repo, q, k, storage_path)
        per_query.append({
            "query": q,
            "relevant_count": len(relevant),
            "predicted_count": len(predicted),
            "ndcg": ndcg_at_k(predicted, relevant, k),
            "mrr": mrr_at_k(predicted, relevant, k),
            "recall": recall_at_k(predicted, relevant, k),
        })
    overall = aggregate(per_query)
    return {
        "version": __version__,
        "fixture": fixture.get("name", fixture_path.stem),
        "fixture_path": str(fixture_path.relative_to(REPO_ROOT)),
        "k": k,
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "per_query": per_query,
        "overall": overall,
    }


def _baseline_path(version: str, fixture_name: str) -> Path:
    return REPO_ROOT / "benchmarks" / "replay" / "results" / f"{fixture_name}-v{version}.json"


def _check_gate(result: dict, baseline_path: Path, gate_pct: float) -> tuple[bool, list[str]]:
    """Return (passed, reasons). Fails if any aggregate metric dropped by
    more than ``gate_pct`` (relative)."""
    if not baseline_path.exists():
        return True, [f"Baseline {baseline_path.name} not found — first run, gate skipped."]
    baseline = json.loads(baseline_path.read_text())
    base_overall = baseline.get("overall", {})
    cur_overall = result.get("overall", {})
    failures: list[str] = []
    for metric, base_val in base_overall.items():
        cur_val = cur_overall.get(metric, 0.0)
        if base_val <= 0:
            continue
        drop = (base_val - cur_val) / base_val
        if drop > gate_pct:
            failures.append(
                f"{metric}: {cur_val:.4f} vs baseline {base_val:.4f} "
                f"(-{drop*100:.1f}%, threshold -{gate_pct*100:.1f}%)"
            )
    return (not failures), failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", required=True, help="Path to fixture JSON")
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--storage-path", default=None)
    parser.add_argument(
        "--repo",
        default=None,
        help="Override the fixture's repo id (its id is a hash of the absolute "
             "index path, so it differs per machine; queries stay portable).",
    )
    parser.add_argument(
        "--baseline",
        default=None,
        help="Compare against benchmarks/replay/results/{fixture}-v{X}.json; "
             "exit non-zero on regression > --gate.",
    )
    parser.add_argument(
        "--baseline-file",
        default=None,
        help="Compare against an explicit committed baseline JSON (version-neutral, "
             "for a stable CI gate); takes precedence over --baseline.",
    )
    parser.add_argument(
        "--gate",
        type=float,
        default=None,
        help="max relative drop of any aggregate metric before failing; default is "
             "harness/thresholds.json replay.max_relative_drop (the only copy)",
    )
    parser.add_argument(
        "--write-result",
        action="store_true",
        help="Persist the result JSON to benchmarks/replay/results/.",
    )
    args = parser.parse_args()
    if args.gate is None:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from harness import thresholds as _thresholds
        args.gate = float(_thresholds.floor("replay.max_relative_drop"))

    fixture_path = Path(args.fixture).resolve()
    result = run_fixture(
        fixture_path, k=args.k, storage_path=args.storage_path, repo_override=args.repo,
    )

    print(json.dumps(result["overall"], indent=2))

    if args.write_result:
        out_dir = REPO_ROOT / "benchmarks" / "replay" / "results"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{result['fixture']}-v{result['version']}.json"
        out_path.write_text(json.dumps(result, indent=2) + "\n")
        print(f"\nwrote {out_path.relative_to(REPO_ROOT)}")

    if args.baseline_file or args.baseline:
        bp = (
            Path(args.baseline_file).resolve()
            if args.baseline_file
            else _baseline_path(args.baseline, result["fixture"])
        )
        passed, reasons = _check_gate(result, bp, args.gate)
        for r in reasons:
            print(("PASS: " if passed else "FAIL: ") + r)
        if not passed:
            return 1
    return 0


if __name__ == "__main__":
    # Windows: a redirected stdout is cp1252; write UTF-8 (L-145, L-320).
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
