"""Self-run A/B kit, tier 1: retrieval only, on your own repo. No model calls, no cost, no network.

  python run_retrieval.py --repo PATH --lookups lookups.json [--out DIR]

Indexes the repo locally (into DIR/store, so your usual index is untouched), then runs the token benchmark
(benchmarks/harness/run_benchmark.py --tasks) with your lookups' queries. Each lookup may carry a short "query"
(search terms); without one, its "question" is used. The harness compares jCodeMunch's search plus 3 symbol sources
against grep plus the top 3 matching files read whole, in cl100k tokens.
Needs this repo's checkout (or sdist) and a Python where jcodemunch_mcp is importable (`pip install jcodemunch-mcp`).
"""
import argparse, json, os, pathlib, shutil, subprocess, sys, time

HERE = pathlib.Path(__file__).resolve().parent; HARNESS = HERE.parent / "harness" / "run_benchmark.py"
OFF = {"JCODEMUNCH_SHARE_SAVINGS": "0", "JCODEMUNCH_TELEMETRY_URL": "http://127.0.0.1:9/disabled"}

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True); ap.add_argument("--lookups", required=True); ap.add_argument("--out", default=None)
    a = ap.parse_args(); repo = pathlib.Path(a.repo).resolve()
    out = pathlib.Path(a.out).resolve() if a.out else pathlib.Path.home() / "jcodemunch-ab-results" / ("retrieval-" + time.strftime("%Y%m%d-%H%M%S"))
    if out == repo or repo in out.parents: raise SystemExit("--out must be outside the repo")
    if not HARNESS.exists(): raise SystemExit(f"harness not found at {HARNESS}; run this from a clone or the sdist")
    lookups = json.load(open(a.lookups, encoding="utf-8"))["lookups"]
    queries = [(l.get("query") or l["question"]).strip() for l in lookups]
    if not queries: raise SystemExit("no lookups")
    out.mkdir(parents=True, exist_ok=True); store = out / "store"; store.mkdir(exist_ok=True)
    (out / "tasks.json").write_text(json.dumps(queries, indent=1), encoding="utf-8")
    env = {**os.environ, **OFF, "CODE_INDEX_PATH": str(store)}
    jcm = shutil.which("jcodemunch-mcp")
    idx = [jcm, "index", str(repo), "--no-ai-summaries"] if jcm else [sys.executable, "-m", "jcodemunch_mcp", "index", str(repo), "--no-ai-summaries"]
    p = subprocess.run(idx, env=env, capture_output=True, text=True)
    (out / "index.log").write_text(p.stdout + p.stderr, encoding="utf-8")
    if p.returncode: raise SystemExit("indexing failed; see " + str(out / "index.log"))
    p = subprocess.run([sys.executable, str(HARNESS), "--tasks", str(out / "tasks.json"), "--json", str(out / "retrieval.json"),
                        "--out", str(out / "retrieval.md"), str(repo)], env=env, capture_output=True, text=True, cwd=HARNESS.parent.parent.parent)
    (out / "harness.log").write_text(p.stdout + p.stderr, encoding="utf-8")
    md = (out / "retrieval.md").read_text(encoding="utf-8") if (out / "retrieval.md").exists() else ""
    # the harness reports a per-repo failure inside the report and still exits 0, so check the report itself
    if p.returncode or not md or "ERROR" in md or "Not indexed" in md:
        raise SystemExit("the harness did not produce a clean report; see " + str(out / "harness.log") + " and retrieval.md")
    print(f"Done: {len(queries)} lookups. Report: {out / 'retrieval.md'}\n"
          "Unit: tokens of code returned per lookup (retrieval only, cl100k). Not a session or bill measure.")

if __name__ == "__main__": main()
