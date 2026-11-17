"""Measure find_implementations channel accuracy against the authored gold corpus.

Phase C of the self-attesting retrieval contract. The corpus
(``benchmarks/goldset/corpus/``) is authored: every implementation relation
and every false-positive trap is labeled in ``gold.json`` with a rationale,
so ground truth is exact by construction. The harness snapshots the corpus to
a temp dir before indexing (never index a subtree of the host repo — the
git-identity resolver would fold it into the host index), runs
``find_implementations`` per gold target, joins each returned implementation
to its label, and reports per-channel precision/recall.

Determinism: same corpus bytes → same index → same channel output → same
numbers. ``tests/test_channel_accuracy.py`` re-runs this measurement in CI and
fails if the committed artifact (``benchmarks/provenance/channel_accuracy.json``)
diverges — the artifact cannot drift from the reproducible measurement.

Regenerate after a deliberate corpus or channel change:

    PYTHONPATH=src python benchmarks/goldset/measure.py
"""

import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
CORPUS_DIR = _HERE / "corpus"
GOLD_PATH = _HERE / "gold.json"
ARTIFACT_PATH = _HERE.parent / "provenance" / "channel_accuracy.json"

# find_implementations `source` field → channel name in the provenance registry
_SOURCE_TO_CHANNEL = {
    "class_hierarchy": "ast",
    "name_match": "duck",
    "decorator_match": "decorator",
    "lsp_dispatch": "lsp",
    "scip": "scip",
}


_CORPUS_SUFFIXES = (".py", ".ts", ".go")


def corpus_sha256(corpus_dir: Path = CORPUS_DIR, gold_path: Path = GOLD_PATH) -> str:
    """Content hash over every corpus file + the gold manifest, path-ordered."""
    h = hashlib.sha256()
    files = sorted(
        p for p in corpus_dir.rglob("*")
        if p.is_file() and p.suffix in _CORPUS_SUFFIXES
    )
    for p in files:
        h.update(p.relative_to(corpus_dir).as_posix().encode())
        h.update(p.read_bytes().replace(b"\r\n", b"\n"))
    h.update(gold_path.read_bytes().replace(b"\r\n", b"\n"))
    return h.hexdigest()


def measure(corpus_dir: Path = CORPUS_DIR, gold_path: Path = GOLD_PATH) -> dict:
    """Run the measurement; returns the channel-accuracy artifact dict."""
    from jcodemunch_mcp.tools.find_implementations import find_implementations
    from jcodemunch_mcp.tools.index_folder import index_folder

    gold = json.loads(gold_path.read_text(encoding="utf-8"))

    with tempfile.TemporaryDirectory() as td:
        snap = Path(td) / "goldset-corpus"
        shutil.copytree(corpus_dir, snap)
        storage = str(Path(td) / "index")
        indexed = index_folder(str(snap), use_ai_summaries=False, storage_path=storage)
        repo = indexed["repo"]

        stats = {
            ch: {"tp": 0, "fp": 0, "fn": 0}
            for ch in ("ast", "duck", "decorator")
        }
        per_lang: dict[str, dict] = {}
        unlabeled: list[str] = []

        for tgt in gold["targets"]:
            lang = tgt.get("language", gold.get("language", ""))
            lang_stats = per_lang.setdefault(
                lang,
                {ch: {"tp": 0, "fp": 0, "fn": 0} for ch in ("ast", "duck", "decorator")},
            )
            labels = {
                (lab["file"], lab["name"]): lab for lab in tgt["labels"]
            }
            found: set[tuple[str, str]] = set()
            result = find_implementations(
                repo, tgt["target"],
                max_results=200, token_budget=200000, storage_path=storage,
            )
            if "error" in result:
                raise RuntimeError(f"{tgt['target']}: {result['error']}")
            for impl in result.get("implementations", []):
                channel = _SOURCE_TO_CHANNEL.get(impl.get("source", ""))
                if channel not in stats:
                    continue
                key = (impl.get("file", ""), impl.get("name", ""))
                lab = labels.get(key)
                if lab is None:
                    unlabeled.append(f"{tgt['target']} -> {key} via {channel}")
                    continue
                found.add(key)
                bucket = "tp" if lab["true_impl"] else "fp"
                stats[channel][bucket] += 1
                lang_stats[channel][bucket] += 1
            # Recall: gold TRUE labels this target expected a channel to surface
            for key, lab in labels.items():
                if lab["true_impl"] and key not in found and lab["channel"] in stats:
                    stats[lab["channel"]]["fn"] += 1
                    lang_stats[lab["channel"]]["fn"] += 1

        if unlabeled:
            raise RuntimeError(
                "corpus incomplete — channel output without a gold label "
                f"(label every surfaced pair): {unlabeled}"
            )

    def _rollup(s: dict) -> dict:
        surfaced = s["tp"] + s["fp"]
        truth = s["tp"] + s["fn"]
        return {
            "precision": round(s["tp"] / surfaced, 3) if surfaced else None,
            "recall": round(s["tp"] / truth, 3) if truth else None,
            "tp": s["tp"],
            "fp": s["fp"],
            "fn": s["fn"],
            "n_surfaced": surfaced,
        }

    channels = {ch: _rollup(s) for ch, s in stats.items()}
    per_language = {
        lang: {
            ch: _rollup(s) for ch, s in lang_ch.items()
            if s["tp"] + s["fp"] + s["fn"] > 0
        }
        for lang, lang_ch in per_lang.items()
    }

    return {
        "comment": (
            "find_implementations channel accuracy on the authored gold corpus. "
            "Reproducible: tests/test_channel_accuracy.py re-runs this measurement "
            "in CI and fails on divergence. Scope is the authored corpus, NOT "
            "in-the-wild base rates — n is small and disclosed."
        ),
        "corpus_version": gold["corpus_version"],
        "languages": gold.get("languages") or [gold.get("language", "")],
        "scope": gold["scope"],
        "corpus_sha256": corpus_sha256(corpus_dir, gold_path),
        "generator": "benchmarks/goldset/measure.py",
        "channels": channels,
        "per_language": per_language,
    }


def main() -> int:
    artifact = measure()
    ARTIFACT_PATH.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT_PATH.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {ARTIFACT_PATH}")
    for ch, m in artifact["channels"].items():
        print(f"  {ch:<10} precision={m['precision']} recall={m['recall']} "
              f"(tp={m['tp']} fp={m['fp']} fn={m['fn']})")
    return 0


if __name__ == "__main__":
    # Windows: a redirected stdout is cp1252; write UTF-8 (L-145, L-320).
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
