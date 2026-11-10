"""Compare a trusted official encoder with length-sorted local inference."""
import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True, help="Trusted official local_encoder.py")
    parser.add_argument("--database", type=Path, required=True, help="Checkpointed code-index copy")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--texts", type=int, default=512)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--run-sizes", type=int, nargs="+", default=[8, 16, 32])
    args = parser.parse_args()
    if min(args.batch_size, args.texts, args.repeats, *args.run_sizes) <= 0:
        parser.error("batch size, text count, repeats and run sizes must be positive")
    if Path(str(args.database) + "-wal").exists():
        parser.error("use a checkpointed database copy without a WAL")
    baseline_hash = hashlib.sha256(args.baseline.read_bytes()).hexdigest()
    if baseline_hash != "1d745e21b8c7fc610a3f890a4902d3cf22b4d538923dd5cd92910f63c64c884a":
        parser.error("baseline must be the unchanged official 1.108.429–1.108.431 encoder")

    import numpy as np

    source_root = Path(__file__).resolve().parents[1] / "src"
    sys.path.insert(0, str(source_root))
    from jcodemunch_mcp.embeddings import local_encoder as candidate
    assert Path(candidate.__file__).resolve() == source_root / "jcodemunch_mcp/embeddings/local_encoder.py"
    if not candidate.is_model_available():
        parser.error("download the model first and set JCODEMUNCH_LOCAL_EMBED_MODEL")
    os.environ["JCODEMUNCH_LOCAL_EMBED_THREADS"] = "2"
    spec = importlib.util.spec_from_file_location("official_encoder", args.baseline)
    baseline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(baseline)
    with sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro&immutable=1", uri=True) as db:
        texts = [" ".join(value for value in row if value) for row in db.execute(
            "SELECT name, signature, summary FROM symbols WHERE rowid % 97 = 0 LIMIT ?", (args.texts,))]
    if not texts:
        parser.error("the sample contains no texts")

    results, reference = [], None
    original_size = candidate._INFER_SUB_BATCH
    try:
        arms = [None, *args.run_sizes]
        for repeat in range(args.repeats):
            for size in arms if repeat % 2 == 0 else list(reversed(arms)):
                module = baseline if size is None else candidate
                if size is not None:
                    candidate._INFER_SUB_BATCH = size
                module.reset_session()
                module.encode_batch(texts[:4])
                start = time.perf_counter()
                vectors = np.array([vector for i in range(0, len(texts), args.batch_size)
                                    for vector in module.encode_batch(texts[i:i + args.batch_size])])
                elapsed = time.perf_counter() - start
                if reference is None:
                    reference = vectors
                difference = float(np.abs(vectors - reference).max())
                assert vectors.shape == (len(texts), 384) and difference < 1e-5
                row = {"repeat": repeat, "run_size": size, "seconds": elapsed,
                       "texts_per_second": len(texts) / elapsed, "max_abs_difference": difference}
                results.append(row)
                print(json.dumps(row), flush=True)
                module.reset_session()
    finally:
        candidate._INFER_SUB_BATCH = original_size
        candidate.reset_session()
        baseline.reset_session()
    report = {"texts": len(texts), "caller_batch": args.batch_size, "threads": 2,
              "baseline_sha256": baseline_hash,
              "candidate_sha256": hashlib.sha256(Path(candidate.__file__).read_bytes()).hexdigest(),
              "numpy": np.__version__, "onnxruntime": importlib.metadata.version("onnxruntime"),
              "scope": "Encoder throughput; session creation/warm-up excluded; OS caches warm. All measurements retained.",
              "results": results}
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
