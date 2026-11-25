"""Measure how often get_dead_code_v2 calls provably-executed code dead.

Coverage is a one-sided oracle: a symbol the test suite executed is alive, with
no heuristic in the loop. This intersects that set with the tool's output and
reports the definite false positives.

⚠⚠ There is deliberately NO recall here. Zero coverage does not mean dead, so
the other half of the confusion matrix is not observable from this data and is
not invented. See README.md.

Usage:
    python benchmarks/deadcode_eval/run_eval.py \
        --coverage benchmarks/deadcode_eval/coverage.json \
        --repo . --compare 0.90 0.95 1.0
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _norm(path: str) -> str:
    """Repo-relative, forward-slashed, lowercased for case-insensitive FS."""
    return path.replace("\\", "/").lstrip("./").lower()


def load_executed_lines(coverage_json: Path, source_root: Path) -> dict[str, set[int]]:
    """{repo_relative_file: {executed line numbers}} from a coverage JSON report."""
    data = json.loads(coverage_json.read_text(encoding="utf-8"))
    files = data.get("files", {})
    if not files:
        raise SystemExit(f"{coverage_json} has no 'files' section - wrong report format?")
    root = source_root.resolve()
    out: dict[str, set[int]] = {}
    for raw_path, rec in files.items():
        p = Path(raw_path)
        try:
            rel = p.resolve().relative_to(root)
        except (ValueError, OSError):
            rel = p
        executed = rec.get("executed_lines") or []
        if executed:
            out[_norm(str(rel))] = set(executed)
    return out


def proven_alive_symbols(index, executed: dict[str, set[int]]) -> set[str]:
    """Symbol ids with at least one executed line inside their span.

    ⚠ The def line alone is not enough: it executes at import time for every
    function in an imported module, whether or not the body ever runs. Requiring
    a hit strictly INSIDE the body is what makes this an oracle for "this code
    ran" rather than for "this module was imported".
    """
    alive: set[str] = set()
    for sym in index.symbols:
        if sym.get("kind") not in ("function", "method"):
            continue
        sid, f = sym.get("id"), sym.get("file", "")
        if not sid or not f:
            continue
        lines = executed.get(_norm(f))
        if not lines:
            continue
        start = sym.get("line") or 0
        end = sym.get("end_line") or start
        if start <= 0 or end <= start:
            continue
        # Strictly inside the signature line.
        if any(ln in lines for ln in range(start + 1, end + 1)):
            alive.add(sid)
    return alive


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--coverage", required=True, type=Path)
    ap.add_argument("--repo", default=".")
    ap.add_argument("--min-confidence", type=float, default=0.5)
    ap.add_argument("--compare", nargs="*", type=float, default=[0.90])
    ap.add_argument("--storage-path", default=None)
    ap.add_argument("--json-out", type=Path, default=None)
    args = ap.parse_args()

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    from jcodemunch_mcp.storage import IndexStore
    from jcodemunch_mcp.tools._utils import resolve_repo as _resolve_repo
    from jcodemunch_mcp.tools.get_dead_code_v2 import get_dead_code_v2

    owner, name = _resolve_repo(args.repo, args.storage_path)
    store = IndexStore(base_path=args.storage_path)
    index = store.load_index(owner, name)
    if index is None:
        raise SystemExit(f"no index for {args.repo!r}; run index_folder first")

    source_root = Path(getattr(index, "source_root", "") or os.getcwd())
    executed = load_executed_lines(args.coverage, source_root)
    alive = proven_alive_symbols(index, executed)
    if not alive:
        raise SystemExit(
            "oracle is empty: no symbol had an executed line inside its body. "
            "Check that the coverage report covers the same tree as the index."
        )

    print(f"repo:          {owner}/{name}")
    print(f"source_root:   {source_root}")
    print(f"oracle:        {len(alive):,} symbols proven alive by execution "
          f"({len(executed):,} files with coverage)")
    print(f"min_confidence {args.min_confidence}")
    print()
    hdr = (f"{'cutoff':>7} {'flagged':>9} {'false_pos':>10} {'fp_rate':>9} "
           f"{'alive_caught':>13}")
    print(hdr)
    print("-" * len(hdr))

    rows = []
    for cutoff in args.compare:
        r = get_dead_code_v2(
            repo=args.repo, min_confidence=args.min_confidence, max_results=0,
            storage_path=args.storage_path, degeneracy_cutoff=cutoff,
        )
        if "error" in r:
            print(f"{cutoff:7.2f}  ERROR: {r['error'][:60]}")
            continue
        flagged = {d["id"] for d in r.get("dead_symbols", [])}
        fp = flagged & alive
        fp_rate = len(fp) / len(flagged) if flagged else 0.0
        caught = len(fp) / len(alive)
        rows.append({
            "cutoff": cutoff, "flagged": len(flagged), "false_positives": len(fp),
            "fp_rate": round(fp_rate, 4), "alive_caught": round(caught, 4),
            "proven_alive": len(alive),
            "examples": sorted(fp)[:10],
        })
        note = "  <- vacuous: returns nothing" if not flagged else ""
        print(f"{cutoff:7.2f} {len(flagged):9,} {len(fp):10,} {fp_rate:8.1%} "
              f"{caught:12.1%}{note}")

    print()
    print("WARNING: fp_rate is a LOWER BOUND - a flagged symbol that is alive but "
          "untested is invisible here and counts as correct.")
    print("WARNING: no recall is reported. Zero coverage is not evidence of deadness.")

    if rows:
        worst = max(rows, key=lambda x: x["fp_rate"])
        if worst["examples"]:
            print(f"\nsample false positives at cutoff {worst['cutoff']}:")
            for e in worst["examples"][:5]:
                print(f"  {e}")

    if args.json_out:
        args.json_out.write_text(json.dumps(rows, indent=1), encoding="utf-8")
        print(f"\nwrote {args.json_out}")
    return 0


if __name__ == "__main__":
    # Windows: a redirected stdout is cp1252; write UTF-8 (L-145, L-320).
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
