"""The README's 15 benchmark lookups: tokens_saved against Claude's own count.

For each of express, fastapi and gin (``benchmarks/tasks.json`` names the
pinned commits; ``<corpora dir>/<name>`` holds a checkout of each), this
indexes into a fresh store with the code on PYTHONPATH, runs the README
workflow (``search_symbols`` top 5, then ``get_symbol_source`` on the top 3)
for each of the five queries, and compares the summed ``_meta.tokens_saved``
with Claude's count_tokens (free) of exactly what the tracker claims: every
file the calls would otherwise have read, counted whole, minus every response
as served, with ``_meta`` (``claude_served``) and without it, as a default
install receives it (``claude_served_no_meta``). It also times the index and
each call. ``record_savings`` is a no-op, so no real ledger moves. The API key
is read at runtime (environment, then the Windows user environment) and never
printed.

Usage: lookups.py <corpora dir> <store dir> <out.json> [--no-claude]
"""

import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request

os.environ["JCODEMUNCH_SHARE_SAVINGS"] = "0"
CORPORA, STORE, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
CLAUDE = "--no-claude" not in sys.argv
os.environ["CODE_INDEX_PATH"] = STORE

MODEL = "claude-opus-5-5"
URL = "https://api.anthropic.com/v1/messages/count_tokens"
CHUNK_BYTES = 300_000
REQUESTS = 0


def _key():
    k = os.environ.get("ANTHROPIC_API_KEY")
    if k:
        return k
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as h:
            return winreg.QueryValueEx(h, "ANTHROPIC_API_KEY")[0] or None
    except OSError:
        return None


KEY = _key() if CLAUDE else None
if CLAUDE and not KEY:
    print("ANTHROPIC_API_KEY: not set; nothing counted")
    sys.exit(2)


def count(text):
    global REQUESTS
    body = json.dumps(
        {"model": MODEL, "messages": [{"role": "user", "content": text}]}
    ).encode("utf-8")
    for attempt in range(8):
        req = urllib.request.Request(
            URL,
            data=body,
            method="POST",
            headers={
                "x-api-key": KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                REQUESTS += 1
                return json.loads(r.read())["input_tokens"]
        except urllib.error.HTTPError as e:
            if e.code in (429, 529) or e.code >= 500:
                time.sleep(min(float(e.headers.get("retry-after") or 2**attempt), 60))
                continue
            raise SystemExit(f"count_tokens failed: HTTP {e.code}")
    raise SystemExit("count_tokens failed: retries exhausted")


OVERHEAD = count("x") - 1 if CLAUDE else 0


def count_body(text):
    return max(0, count(text) - OVERHEAD) if text else 0


def count_many(texts):
    total, buf, size = 0, [], 0
    for t in texts:
        b = len(t.encode("utf-8"))
        if buf and size + b > CHUNK_BYTES:
            total += count_body("\n".join(buf))
            buf, size = [], 0
        buf.append(t)
        size += b
    if buf:
        total += count_body("\n".join(buf))
    return total


from jcodemunch_mcp.storage import IndexStore  # noqa: E402
from jcodemunch_mcp.tools import get_symbol as gs_mod  # noqa: E402
from jcodemunch_mcp.tools import search_symbols as ss_mod  # noqa: E402
from jcodemunch_mcp.tools.index_folder import index_folder  # noqa: E402

for m in (ss_mod, gs_mod):
    m.record_savings = lambda tokens_saved, *a, **k: 0

TASKS = [
    t["query"]
    for t in json.load(
        open(
            os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "..", "tasks.json"
            ),
            encoding="utf-8",
        )
    )["tasks"]
]
out = {"model": MODEL, "claude": CLAUDE}
grand = {"tracker": 0, "claude_served": 0}
lat = {"search_symbols": [], "get_symbol_source": []}
for short in ("express", "fastapi", "gin"):
    t0 = time.perf_counter()
    res = index_folder(
        os.path.join(CORPORA, short), use_ai_summaries=False, storage_path=STORE
    )
    index_s = time.perf_counter() - t0
    assert res.get("success"), res
    rid = res["repo"]
    owner, name = rid.split("/", 1)
    store = IndexStore(base_path=STORE)
    index = store.load_index(owner, name)
    texts = {}
    for rel in index.source_files:
        p = os.path.join(CORPORA, short, rel)
        try:
            texts[rel] = open(p, encoding="utf-8", errors="replace").read()
        except OSError:
            texts[rel] = ""
    files = [t for t in texts.values() if t]
    fb = sum(len(t.encode("utf-8")) for t in files)
    tpb_file = None
    file_tokens = {}
    rows = []
    bases = set()
    for q in TASKS:
        served_texts = []
        stripped_texts = []
        raw_b = 0
        t = time.perf_counter()
        r1 = ss_mod.search_symbols(
            repo=rid, query=q, max_results=5, detail_level="standard"
        )
        lat["search_symbols"].append((time.perf_counter() - t) * 1000)
        served_texts.append(json.dumps(r1, separators=(",", ":"), default=str))
        stripped_texts.append(
            json.dumps(
                {k: v for k, v in r1.items() if k != "_meta"},
                separators=(",", ":"),
                default=str,
            )
        )
        tracker = r1.get("_meta", {}).get("tokens_saved", 0)
        bases.add(r1.get("_meta", {}).get("tokens_saved_basis"))
        hits = r1.get("results") or []
        raw_files = sorted({h.get("file") for h in hits})
        raw_b += sum(index.file_sizes.get(f, 0) for f in raw_files)
        for h in [h for h in hits if h.get("id")][:3]:
            t = time.perf_counter()
            r2 = gs_mod.get_symbol_source(repo=rid, symbol_id=h["id"])
            lat["get_symbol_source"].append((time.perf_counter() - t) * 1000)
            served_texts.append(json.dumps(r2, separators=(",", ":"), default=str))
            stripped_texts.append(
                json.dumps(
                    {k: v for k, v in r2.items() if k != "_meta"},
                    separators=(",", ":"),
                    default=str,
                )
            )
            tracker += r2.get("_meta", {}).get("tokens_saved", 0)
            bases.add(r2.get("_meta", {}).get("tokens_saved_basis"))
            raw_b += index.file_sizes.get(h.get("file"), 0)
            raw_files.append(h.get("file"))
        row = {"query": q, "tracker": tracker, "raw_bytes": raw_b}
        if CLAUDE:
            for f in raw_files:
                if f not in file_tokens:
                    file_tokens[f] = count_body(texts.get(f, ""))
            raw_tok = sum(file_tokens[f] for f in raw_files)
            served = sum(count_body(s) for s in served_texts)
            row["claude_raw"] = raw_tok
            row["claude_response"] = served
            row["claude_served"] = max(0, raw_tok - served)
            stripped = sum(count_body(s) for s in stripped_texts)
            row["claude_response_no_meta"] = stripped
            row["claude_served_no_meta"] = max(0, raw_tok - stripped)
            grand["claude_served"] += row["claude_served"]
        grand["tracker"] += tracker
        rows.append(row)
    rep = {
        "index_seconds": round(index_s, 3),
        "files": len(index.source_files),
        "bases": sorted(b for b in bases if b),
        "tracker": sum(r["tracker"] for r in rows),
        "lookups": rows,
    }
    if CLAUDE:
        rep["claude_raw"] = sum(r["claude_raw"] for r in rows)
        rep["claude_response"] = sum(r["claude_response"] for r in rows)
        rep["claude_served_no_meta"] = sum(r["claude_served_no_meta"] for r in rows)
        rep["tracker_over_claude_no_meta"] = round(
            rep["tracker"] / rep["claude_served_no_meta"], 3
        )
        grand["claude_served_no_meta"] = (
            grand.get("claude_served_no_meta", 0) + rep["claude_served_no_meta"]
        )
        rep["claude_served"] = sum(r["claude_served"] for r in rows)
        rep["tracker_over_claude"] = round(rep["tracker"] / rep["claude_served"], 3)
    out[short] = rep
if CLAUDE:
    grand["tracker_over_claude"] = round(grand["tracker"] / grand["claude_served"], 3)
    grand["tracker_over_claude_no_meta"] = round(
        grand["tracker"] / grand["claude_served_no_meta"], 3
    )
out["all"] = grand
out["latency_ms"] = {
    k: {
        "n": len(v),
        "median": round(statistics.median(v), 2),
        "p90": round(sorted(v)[int(0.9 * len(v))], 2),
    }
    for k, v in lat.items()
}
out["requests"] = REQUESTS
json.dump(out, open(OUT, "w"), indent=1)
for short in ("express", "fastapi", "gin"):
    print(short, {k: v for k, v in out[short].items() if k != "lookups"})
print("all", grand)
print("latency", out["latency_ms"], "requests", REQUESTS)
