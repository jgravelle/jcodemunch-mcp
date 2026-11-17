"""Cache-stability P0: measure how much served context is re-shuffled repeats.

Provider prompt caches bill cached reads at ~0.1x but key on byte-identical
prefixes. This harness quantifies the opportunity: across realistic drill-down
query sequences against one index generation, how much of what
`get_ranked_context` serves is the *same symbols again, in a different order*?

Metrics per consecutive query pair, aggregated per sequence and overall:
- set_jaccard        — overlap of served symbol-id sets (is there repeat at all?)
- repeat_byte_share  — bytes of repeated symbols / total bytes served (the
                       ceiling on what byte-stable emission could make
                       cache-eligible)
- reshuffled_share   — repeated bytes whose *relative order* changed between
                       responses / total bytes served (the waste this PRD
                       targets: repeats a prefix cache can never match)
- prefix_ratio       — common byte prefix of the two serialized responses /
                       min length (what a prefix cache actually sees today)

Kill threshold (PRD §8): proceed past P1 only if reshuffled_share >= 0.30.

Run: PYTHONPATH=src python benchmarks/cache_stability/measure.py
Writes results.json beside this file. Indexes a snapshot of jcm's own src/
into a temp store (snapshot-to-tmp: never index a subtree of the host repo in
place — the git-identity contains-path trap).
"""

import json
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
REPO_ROOT = HERE.parent.parent

# Realistic drill-down sequences: an agent narrowing from task framing to
# specific mechanics. Overlap between steps is the point.
SEQUENCES = {
    "budget_drilldown": [
        "session token budget tracking",
        "budget status response tokens",
        "record response tokens session budget",
        "turn budget percent used",
    ],
    "search_ranking": [
        "symbol search ranking",
        "bm25 score symbols query",
        "search symbols ranking confidence",
        "ranked context token budget packing",
    ],
    "watcher_reindex": [
        "file watcher reindex on change",
        "watcher fast path incremental index",
        "reindex state stale index",
        "watch status per repo",
    ],
    "secret_redaction": [
        "secret file detection",
        "redact response secrets patterns",
        "credential file classifier groups",
        "secret redaction chokepoint trace ingest",
    ],
}


def _item_id(item):
    return item.get("symbol_id") or item.get("id") or ""


def _item_bytes(item):
    return len(json.dumps(item, sort_keys=True).encode("utf-8"))


def _common_prefix_len(a: bytes, b: bytes) -> int:
    n = min(len(a), len(b))
    for i in range(n):
        if a[i] != b[i]:
            return i
    return n


def _pair_metrics(prev, cur):
    prev_ids = [_item_id(i) for i in prev["context_items"]]
    cur_items = {(_item_id(i)): i for i in cur["context_items"]}
    cur_ids = list(cur_items)

    prev_set, cur_set = set(prev_ids), set(cur_ids)
    union = prev_set | cur_set
    repeated = prev_set & cur_set

    total_bytes = sum(_item_bytes(i) for i in cur["context_items"]) or 1
    repeat_bytes = sum(_item_bytes(cur_items[s]) for s in repeated)

    # A repeated symbol is "in order" only if the repeated subsequence appears
    # in the same relative order in both responses.
    prev_rank = {s: n for n, s in enumerate(prev_ids)}
    repeated_in_cur_order = [s for s in cur_ids if s in repeated]
    in_order = all(
        prev_rank[a] < prev_rank[b]
        for a, b in zip(repeated_in_cur_order, repeated_in_cur_order[1:])
    )
    reshuffled_bytes = 0 if in_order else repeat_bytes

    blob_prev = json.dumps(prev["context_items"], sort_keys=True).encode("utf-8")
    blob_cur = json.dumps(cur["context_items"], sort_keys=True).encode("utf-8")

    return {
        "set_jaccard": round(len(repeated) / len(union), 3) if union else 0.0,
        "repeat_byte_share": round(repeat_bytes / total_bytes, 3),
        "reshuffled_share": round(reshuffled_bytes / total_bytes, 3),
        "prefix_ratio": round(
            _common_prefix_len(blob_prev, blob_cur) / (min(len(blob_prev), len(blob_cur)) or 1), 3
        ),
    }


def main() -> int:
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from jcodemunch_mcp.tools.get_ranked_context import get_ranked_context
    from jcodemunch_mcp.tools.index_folder import index_folder

    tmp = Path(tempfile.mkdtemp(prefix="jcm_cache_stab_"))
    try:
        snap = tmp / "snapshot" / "jcm_src"
        shutil.copytree(REPO_ROOT / "src" / "jcodemunch_mcp", snap)
        store = tmp / "store"
        res = index_folder(str(snap), storage_path=str(store), use_ai_summaries=False)
        assert res.get("success"), res

        per_sequence = {}
        all_pairs = []
        for seq_name, queries in SEQUENCES.items():
            responses = []
            for q in queries:
                out = get_ranked_context("jcm_src", q, storage_path=str(store))
                assert "error" not in out, out
                responses.append(out)
            pairs = [_pair_metrics(a, b) for a, b in zip(responses, responses[1:])]
            all_pairs.extend(pairs)
            per_sequence[seq_name] = {
                k: round(sum(p[k] for p in pairs) / len(pairs), 3) for k in pairs[0]
            }

        aggregate = {
            k: round(sum(p[k] for p in all_pairs) / len(all_pairs), 3) for k in all_pairs[0]
        }
        results = {
            "generator": "benchmarks/cache_stability/measure.py",
            "corpus": "self (src/jcodemunch_mcp snapshot)",
            "tool": "get_ranked_context (default params)",
            "sequences": per_sequence,
            "aggregate": aggregate,
            "pair_count": len(all_pairs),
            "kill_threshold_reshuffled_share": 0.30,
            "verdict_p3": "proceed" if aggregate["reshuffled_share"] >= 0.30 else "hold",
        }
        out_path = HERE / "results.json"
        out_path.write_text(json.dumps(results, indent=2))
        print(json.dumps(results["aggregate"], indent=2))
        print(f"verdict_p3={results['verdict_p3']}  ({out_path})")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    # Windows: a redirected stdout is cp1252; write UTF-8 (L-145, L-320).
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
