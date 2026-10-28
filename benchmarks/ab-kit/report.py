"""Grade and summarise a self-run A/B kit run. Standard library only.
  python report.py --results <dir> --lookups <lookups.json>
Reads <dir>/runs/*/run.json, grades each answer against the answer key, and prints a markdown report:
per-lookup tokens and correctness, a paired Hodges-Lehmann ratio with an exact Wilcoxon signed-rank test on tokens,
correct counts per arm with an exact McNemar test, and the quality guard.
"""
import argparse, collections, hashlib, itertools, json, math, pathlib, re, statistics

def norm(s): return re.sub(r"[^a-z0-9_]", "", (s or "").lower())

def grade(answer, entry):
    """Correct if the answer names at least one accepted symbol and that symbol's file (or one of its alt_files)."""
    text = (answer or "").replace("\\", "/"); low = text.lower(); toks = {norm(t) for t in re.split(r"[\s`:(),\[\]]+", text)}
    for s in entry["symbols"]:
        names = {norm(s["name"]), norm(s["name"].split(".")[-1])}
        if s.get("owner"): names.add(norm(f"{s['owner']}.{s['name']}"))
        files = [s["file"]] + list(s.get("alt_files") or [])
        if (names & toks or any(n and n in norm(low) for n in names if len(n) >= 6)) and any(f.lower() in low for f in files):
            return True
    return False

def wilcoxon_exact(diffs):
    """Two-sided exact signed-rank p-value (zeros dropped, average ranks for ties, DP over doubled ranks)."""
    d = [x for x in diffs if x != 0]; n = len(d)
    if n == 0: return 1.0
    order = sorted(range(n), key=lambda i: abs(d[i])); ranks = [0.0] * n; i = 0
    while i < n:
        j = i
        while j + 1 < n and abs(d[order[j + 1]]) == abs(d[order[i]]): j += 1
        for k in range(i, j + 1): ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    r2 = [int(round(2 * r)) for r in ranks]; total = sum(r2)
    dist = collections.Counter({0: 1})
    for r in r2:
        new = collections.Counter()
        for s, c in dist.items(): new[s] += c; new[s + r] += c
        dist = new
    w = sum(r for r, x in zip(r2, d) if x > 0); mean = total / 2; dev = abs(w - mean)
    return min(1.0, sum(c for s, c in dist.items() if abs(s - mean) >= dev - 1e-9) / 2 ** n)

def hodges_lehmann(xs):
    walsh = [(a + b) / 2 for a, b in itertools.combinations_with_replacement(xs, 2)]
    return statistics.median(walsh) if walsh else float("nan")

def mcnemar_exact(b, c):
    n = b + c
    if n == 0: return 1.0
    k = min(b, c); return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)

def spend_text(manifest):
    """The run's spend, or a plain statement that it is not known. Never a zero for a value nobody recorded."""
    usd = manifest.get("spent_usd")
    if not isinstance(usd, (int, float)): return "spend not recorded (see spend.jsonl in the results folder)"
    return f"spend ${usd:.2f}" + ("" if manifest.get("finished_utc") else " so far (the run did not finish)")

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--results", required=True); ap.add_argument("--lookups", required=True)
    ap.add_argument("--arms", default="G,J"); ap.add_argument("--guard", type=int, default=None,
                    help="no saving is claimed if the jCodeMunch arm has more than this many fewer correct answers (default: 10%% of pairs, at least 1)")
    a = ap.parse_args(); res = pathlib.Path(a.results); base, test = a.arms.split(",")
    raw = open(a.lookups, "rb").read(); key = {l["id"]: l for l in json.loads(raw)["lookups"]}
    manifest = json.loads((res / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("lookups_sha256") != hashlib.sha256(raw).hexdigest(): raise SystemExit("answer key changed since the run started; refusing to grade")
    runs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(res.glob("runs/*/run.json"))]
    dropped = collections.Counter(r["arm"] for r in runs if r.get("wiring_problems"))
    runs = [r for r in runs if not r.get("wiring_problems")]
    by = collections.defaultdict(dict)
    for r in runs:
        r["correct"] = grade(r.get("answer"), key[r["lookup"]]); by[(r["lookup"], r["seed"])][r["arm"]] = r
    pairs = {k: v for k, v in by.items() if base in v and test in v}
    tok = lambda r: r["explore_tokens"]["total"]
    print(f"# A/B kit report\n\nRepo commit `{manifest.get('repo_commit')}`, jCodeMunch {manifest.get('jcm_version')}, Explore model {manifest.get('explore_model')}, "
          f"{len(pairs)} paired lookup-seeds, {spend_text(manifest)}.\n")
    print(f"Sessions left out as miswired (retries included): {base} {dropped[base]}, {test} {dropped[test]}.\n")
    print(f"| Lookup | {base} tokens (median) | {test} tokens (median) | {test}/{base} | {base} correct | {test} correct | {test} jCodeMunch calls |\n|---|---|---|---|---|---|---|")
    logr = []
    for lk in key:
        ps = [v for (l, _), v in pairs.items() if l == lk]
        if not ps: continue
        mb = statistics.median(tok(p[base]) for p in ps); mt = statistics.median(tok(p[test]) for p in ps)
        if mb > 0 and mt > 0: logr.append(math.log(mt / mb))
        print(f"| {lk} | {mb:,.0f} | {mt:,.0f} | {mt / mb if mb else float('nan'):.2f} | {sum(p[base]['correct'] for p in ps)}/{len(ps)} | "
              f"{sum(p[test]['correct'] for p in ps)}/{len(ps)} | {sum(p[test]['jcm_calls'] for p in ps)} |")
    cb = sum(p[base]["correct"] for p in pairs.values()); ct = sum(p[test]["correct"] for p in pairs.values())
    b = sum(p[base]["correct"] and not p[test]["correct"] for p in pairs.values()); c = sum(p[test]["correct"] and not p[base]["correct"] for p in pairs.values())
    guard = a.guard if a.guard is not None else max(1, len(pairs) // 10); used = sum(p[test]["jcm_calls"] > 0 for p in pairs.values())
    hl = math.exp(hodges_lehmann(logr)) if logr else float("nan")
    print(f"\n**Tokens per lookup, {test}/{base}:** Hodges-Lehmann ratio {hl:.3f}, exact Wilcoxon p = {wilcoxon_exact(logr):.4f} over {len(logr)} lookups.")
    print(f"**Correct:** {base} {cb}/{len(pairs)}, {test} {ct}/{len(pairs)}; exact McNemar p = {mcnemar_exact(b, c):.4f}.")
    print(f"**jCodeMunch was called** in {used} of {len(pairs)} {test} sessions.")
    if cb - ct > guard: print(f"\n**Quality guard tripped:** {test} has {cb - ct} fewer correct answers (limit {guard}). No token saving is claimed from this run.")
    if len(logr) < 10: print("\n**Fewer than 10 lookups:** treat the ratio as descriptive only.")
    if used == 0: print(f"\n**The agent never called jCodeMunch** in arm {test}, so this run cannot show a difference from it. That is a real result for this setup.")
    print("\nUnit: the Explore agent's tokens per lookup, as Claude Code reports them. The parent session's tokens are not counted, "
          "so this is not the whole session's bill.")

if __name__ == "__main__": main()
