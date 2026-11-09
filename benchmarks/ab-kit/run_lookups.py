"""Self-run A/B kit, tier 2: the same lookups asked of a Claude Code Explore agent with Read/Grep/Glob (arm G)
and with jCodeMunch added (arm J), on your own repo. Standard library only; needs `claude` and `jcodemunch-mcp` on PATH.

  python run_lookups.py --repo PATH --lookups lookups.json --cap 15 [--seeds 3] [--explore-model haiku] [--out DIR]
  python report.py --results DIR --lookups lookups.json

What it does:
  * copies the repo (without .git) twice into DIR, one copy per arm, and indexes the J copy locally;
  * writes a project .claude/agents/Explore.md override in each copy (model from --explore-model; tools differ only
    by the jCodeMunch tools and one sentence naming them);
  * runs one headless Claude Code session per lookup x arm x seed: the parent is told to use the Explore agent;
  * stops at --cap dollars, retries a miswired session once, and records everything under DIR. Nothing is sent
    anywhere except your own model calls.

How the cap counts: before a session starts, its whole budget (--session-cap, or what is left under --cap) is
charged in DIR/spend.jsonl. When the session ends with a cost reported by Claude Code, that cost replaces the charge.
A session that crashes, is stopped, or reports no cost stays charged at its full budget. Claude Code enforces each
session's budget itself and can go over it by up to one model turn, so the run's total can pass --cap by that much.

BILLED: every session is a real Claude Code session billed to the account your `claude` CLI uses (not to jCodeMunch),
and it sends your code to the Anthropic API. --cap is required, and the run asks you to type yes before the first
session (--yes skips the question). Sessions use whatever credentials your `claude` CLI already uses. This script never prints or stores a key.
It reads ANTHROPIC_API_KEY, if set, only to check its own output files after each session and delete any file that
contains it. jCodeMunch savings sharing is off and its telemetry URL points at a dead local address.
"""
import argparse, hashlib, json, os, pathlib, queue, random, re, shutil, subprocess, sys, threading, time, uuid

JCM_TOOLS = ["resolve_repo", "search_symbols", "get_file_outline", "get_symbol_source", "get_ranked_context", "search_text"]
OFF = {"JCODEMUNCH_SHARE_SAVINGS": "0", "JCODEMUNCH_TELEMETRY_URL": "http://127.0.0.1:9/disabled"}
# A fresh store gets the 6-tool `counter` surface, which publishes none of JCM_TOOLS: arm J would then have only
# Read/Grep/Glob, the same as arm G. The full surface publishes every tool the Explore override names.
J_ENV = {**OFF, "JCODEMUNCH_TOOL_SURFACE": "full"}
SKIP = shutil.ignore_patterns(".git", "node_modules", ".venv", "venv", "__pycache__")


def listed_tools(cmd, env, timeout=120.0, grace=10.0):
    """Tool names an MCP stdio server publishes: initialize, then tools/list (following nextCursor).

    Raises RuntimeError when the server exits, answers with an error, or stays silent past `timeout`.
    """
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
                            text=True, encoding="utf-8", errors="replace")
    lines = queue.Queue()

    def pump():
        for line in proc.stdout: lines.put(line)
        lines.put(None)  # EOF: the server exited

    threading.Thread(target=pump, daemon=True).start()
    deadline = time.monotonic() + timeout

    def send(msg):
        proc.stdin.write(json.dumps(msg) + "\n"); proc.stdin.flush()

    def answer(mid):
        while True:
            left = deadline - time.monotonic()  # one deadline for the whole handshake, however chatty the server is
            if left <= 0: raise RuntimeError(f"no answer to request {mid} within {timeout:.0f} s")
            try: line = lines.get(timeout=left)
            except queue.Empty: raise RuntimeError(f"no answer to request {mid} within {timeout:.0f} s")
            if line is None: raise RuntimeError(f"the server exited before answering request {mid}")
            try: msg = json.loads(line)
            except ValueError: continue
            if msg.get("id") == mid:
                if "error" in msg: raise RuntimeError(f"request {mid} failed: {msg['error']}")
                return msg.get("result") or {}

    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "ab-kit", "version": "1"}}})
        answer(1)
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        names, cursor, mid, seen = [], None, 2, set()
        while True:
            send({"jsonrpc": "2.0", "id": mid, "method": "tools/list", "params": {"cursor": cursor} if cursor else {}})
            res = answer(mid); names += [t.get("name") for t in res.get("tools") or []]
            cursor = res.get("nextCursor"); mid += 1
            if not cursor: return names
            if cursor in seen or len(seen) >= MAX_TOOL_PAGES:
                raise RuntimeError(f"tools/list did not finish paging (cursor {cursor!r} after {len(seen) + 1} pages)")
            seen.add(cursor)
    finally:
        try: proc.stdin.close()
        except OSError: pass
        try: proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            proc.kill()
            try: proc.wait(timeout=grace)
            except subprocess.TimeoutExpired: pass
        try: proc.stdout.close()
        except OSError: pass


MAX_TOOL_PAGES = 50


def check_j_tools(jcm, store, scratch, lister=None):
    """Stop the run before any billing unless the J server, as the sessions launch it, publishes every JCM_TOOLS name."""
    st = scratch / "handshake-store"
    try:
        shutil.rmtree(st, ignore_errors=True); shutil.copytree(store, st)
        try: names = (lister or listed_tools)([jcm], {**os.environ, **J_ENV, "CODE_INDEX_PATH": str(st)})
        except (OSError, RuntimeError) as exc: raise SystemExit(f"not started: could not list the jCodeMunch tools arm J would get ({exc})")
    except OSError as exc:
        raise SystemExit(f"not started: could not copy the index for the tool check ({exc}); remove {st} and run again")
    finally:
        shutil.rmtree(st, ignore_errors=True)
    missing = [t for t in JCM_TOOLS if t not in names]
    if missing:
        raise SystemExit(f"not started: the jCodeMunch server arm J would use publishes {len(names)} tools and not {', '.join(missing)}. "
                         "The Explore agent in arm J would have no jCodeMunch tools, so both arms would be the same.")
    return names


def zero_call_warning(recs):
    """A loud line when no usable arm J session called jCodeMunch; None otherwise."""
    j = [r for r in recs if r.get("arm") == "J" and not r.get("wiring_problems")]
    if j and not any(r.get("jcm_calls") for r in j):
        return (f"WARNING: arm J made 0 jCodeMunch calls in all {len(j)} of its usable sessions. Check that the Explore agent "
                "in arm J can see the jCodeMunch tools before reading this run as a result.")
    return None

def override(arm, model):
    tools = "Read, Grep, Glob" + ("".join(f", mcp__jcodemunch__{t}" for t in JCM_TOOLS) if arm == "J" else "")
    extra = ("\nThis repository is indexed by jCodeMunch: its search_symbols, get_file_outline, get_symbol_source, "
             "get_ranked_context and search_text tools are available to you.\n") if arm == "J" else ""
    return ("---\nname: Explore\ndescription: Fast read-only search agent for locating code in this repository. "
            f"Use it for any search or lookup.\ntools: {tools}\nmodel: {model}\n---\n\n"
            "You are a read-only code search agent. Find the code the request asks about and report the file paths and "
            "symbol names (functions, methods, classes, structs) with line numbers. Do not modify files. Be concise.\n" + extra)

def tree_hash(ws):
    h = hashlib.sha256()
    for p in sorted(ws.rglob("*")):
        if p.is_file() and ".claude" not in p.relative_to(ws).parts: h.update(str(p.relative_to(ws)).encode()); h.update(p.read_bytes())
    return h.hexdigest()

LEDGER = "spend.jsonl"
SHELL_META = re.compile(r'[&|<>^%"]')

def charge(out, sid, usd, **extra):
    """Append a row to the spend ledger. It sits outside runs/, so the key check never deletes it."""
    with open(out / LEDGER, "a", encoding="utf-8") as fh: fh.write(json.dumps({"session": sid, "usd": usd, **extra}) + "\n")

def spent(out):
    """Dollars spent or possibly spent: each session's last ledger row. A session never settled stays at its budget."""
    last = {}
    p = out / LEDGER
    if p.exists():
        for line in open(p, encoding="utf-8", errors="replace"):
            try: row = json.loads(line); last[row["session"]] = float(row["usd"])
            except (ValueError, KeyError, TypeError): continue
    return sum(last.values())

def confirm(a, ask=input, tty=None):
    """True only on --yes or a typed yes. With no terminal to ask on, refuse rather than start billing."""
    if a.yes: return True
    if not (sys.stdin.isatty() if tty is None else tty):
        raise SystemExit("not started: there is no terminal to confirm on. Pass --yes to start without the question.")
    return ask("Type yes to start: ").strip().lower() == "yes"

def shell_safe(cmd):
    """cmd.exe reads these characters as its own syntax, so a path holding one would change the command line."""
    bad = [x for x in cmd if SHELL_META.search(x)]
    if bad: raise SystemExit(f"not started: {bad[0]!r} holds a character cmd.exe would interpret; use an --out path without it")

def session(out, name, ws, arm, lk, seed, a, jcm, store):
    run = out / "runs" / name
    if run.exists():  # a session stopped mid-run: keep its files and its ledger charge, start a fresh one
        n = 1
        while run.with_name(f"{name}-interrupted-{n}").exists(): n += 1
        run.rename(run.with_name(f"{name}-interrupted-{n}"))
    run.mkdir(parents=True)
    cfg = {"mcpServers": {}}
    if arm == "J":
        st = run / "store"; shutil.copytree(store, st)
        cfg["mcpServers"]["jcodemunch"] = {"command": jcm, "args": [], "env": {**J_ENV, "CODE_INDEX_PATH": str(st)}}
    (run / "mcp-config.json").write_text(json.dumps(cfg, indent=1), encoding="utf-8")
    prompt = (f"Use the Explore agent to answer this lookup in the repository in the current directory: {lk['question']}\n"
              "Do not search yourself. When the agent returns, reply with only its answer: the code it found, one item per line as `path:line symbol`.")
    cap = min(a.session_cap, a.cap - spent(out)); sid = str(uuid.uuid4())
    cmd = [shutil.which("claude") or "claude", "-p", "--model", a.parent_model, "--output-format", "stream-json", "--verbose",
           "--setting-sources", "project,local", "--strict-mcp-config", "--mcp-config", str(run / "mcp-config.json"),
           "--permission-mode", "dontAsk", "--allowedTools", "Agent Task Read Grep Glob mcp__jcodemunch__*",
           "--disallowedTools", "Bash PowerShell Edit Write MultiEdit NotebookEdit WebFetch WebSearch",
           "--max-budget-usd", f"{cap:.4f}", "--session-id", sid]
    shell = cmd[0].lower().endswith((".cmd", ".bat"))
    if shell: shell_safe(cmd)
    env = {**os.environ, **OFF, "ENABLE_TOOL_SEARCH": "false"}; env.pop("CODE_INDEX_PATH", None)
    before = tree_hash(ws)
    charge(out, sid, round(cap, 4), name=name, state="reserved")
    with open(run / "stream.jsonl", "w", encoding="utf-8") as fh:
        proc = subprocess.run(cmd, cwd=ws, env=env, input=prompt, stdout=fh, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                              errors="replace", shell=shell)
    del env
    init = result = None; parent_tools = []; sub_tools = []; explore = 0
    for line in open(run / "stream.jsonl", encoding="utf-8", errors="replace"):
        try: ev = json.loads(line)
        except ValueError: continue
        if ev.get("type") == "system" and ev.get("subtype") == "init" and init is None: init = ev
        if ev.get("type") == "result": result = ev
        if ev.get("type") == "assistant":
            calls = [c for c in ev.get("message", {}).get("content") or [] if c.get("type") == "tool_use"]
            if ev.get("parent_tool_use_id"): sub_tools += [c["name"] for c in calls]
            else:
                parent_tools += [c["name"] for c in calls]
                explore += sum(c["name"] in ("Agent", "Task") and (c.get("input") or {}).get("subagent_type") == "Explore" for c in calls)
    r = result or {}; i = init or {}; mu = r.get("modelUsage") or {}
    if isinstance(r.get("total_cost_usd"), (int, float)): charge(out, sid, r["total_cost_usd"], name=name, state="settled")
    else: charge(out, sid, round(cap, 4), name=name, state="no cost reported; charged at its budget")
    em = {k: v for k, v in mu.items() if a.explore_model.lower() in k.lower()}
    t = {f: sum(v.get(f, 0) for v in em.values()) for f in ("inputTokens", "outputTokens", "cacheReadInputTokens", "cacheCreationInputTokens")}
    t["total"] = sum(t.values())
    p = []
    if not i: p.append("no init event")
    tools = set(i.get("tools") or [])
    has = any(x.startswith("mcp__jcodemunch__") for x in tools)
    if i and arm == "G" and has: p.append("jCodeMunch tool presence does not match arm")
    # Arm J needs every tool the Explore override names: some other jCodeMunch tool (the counter front door's
    # route/menu/order) is not enough, and it is what a fresh store published before the full surface was set.
    missing = [t for t in JCM_TOOLS if f"mcp__jcodemunch__{t}" not in tools]
    if i and arm == "J" and missing: p.append("jCodeMunch tools the Explore agent names are missing: " + ", ".join(missing))
    if arm == "J" and [s.get("status") for s in (i.get("mcp_servers") or []) if s.get("name") == "jcodemunch"] != ["connected"]: p.append("jcodemunch not connected")
    if explore < 1: p.append("parent did not call the Explore agent")
    if [x for x in parent_tools if x not in ("Agent", "Task")]: p.append("parent searched itself")
    if not em: p.append(f"no {a.explore_model} usage")
    if not (r.get("result") or "").strip(): p.append("no answer")
    if tree_hash(ws) != before: p.append("workspace changed")
    rec = {"name": name, "lookup": lk["id"], "seed": seed, "arm": arm, "cost_usd": r.get("total_cost_usd"), "explore_models": sorted(em),
           "explore_tokens": t, "explore_cost_usd": sum(v.get("costUSD", 0) for v in em.values()), "jcm_calls": sum(x.startswith("mcp__jcodemunch__") for x in sub_tools),
           "sub_tools": sub_tools, "parent_tools": parent_tools, "answer": r.get("result"), "exit": proc.returncode, "wiring_problems": p,
           "cc_version": i.get("claude_code_version")}
    shutil.rmtree(run / "store", ignore_errors=True)
    key = os.environ.get("ANTHROPIC_API_KEY", "").encode()
    rec["key_found_in_outputs"] = False
    if key:
        for f in [q for q in run.rglob("*") if q.is_file() and key in q.read_bytes()]: f.unlink(); rec["key_found_in_outputs"] = True
    del key
    (run / "run.json").write_text(json.dumps(rec, indent=1), encoding="utf-8")
    print(f"{name}: cost {rec['cost_usd']} explore tokens {t['total']} jcm {rec['jcm_calls']} problems {p}", flush=True)
    return rec

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True); ap.add_argument("--lookups", required=True); ap.add_argument("--cap", type=float, required=True)
    ap.add_argument("--seeds", type=int, default=3); ap.add_argument("--explore-model", default="haiku"); ap.add_argument("--parent-model", default="opus")
    ap.add_argument("--session-cap", type=float, default=0.5); ap.add_argument("--out", default=None)
    ap.add_argument("--yes", action="store_true", help="start without asking you to type yes")
    a = ap.parse_args(); repo = pathlib.Path(a.repo).resolve()
    out = pathlib.Path(a.out).resolve() if a.out else pathlib.Path.home() / "jcodemunch-ab-results" / time.strftime("%Y%m%d-%H%M%S")
    if out == repo or repo in out.parents: raise SystemExit("--out must be outside the repo")
    raw = open(a.lookups, "rb").read(); lookups = json.loads(raw)["lookups"]
    if not all(l.get("symbols") for l in lookups): raise SystemExit("every lookup needs an answer key (symbols); fix lookups.json first")
    jcm = shutil.which("jcodemunch-mcp")
    if not jcm: raise SystemExit("jcodemunch-mcp is not on PATH")
    out.mkdir(parents=True, exist_ok=True)
    mpath = out / "manifest.json"
    if mpath.exists():
        m = json.loads(mpath.read_text(encoding="utf-8"))
        if m["lookups_sha256"] != hashlib.sha256(raw).hexdigest(): raise SystemExit("lookups.json changed since this run started")
    else:
        head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip() or None
        ver = subprocess.run([jcm, "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()
        m = {"repo_commit": head, "jcm_version": ver, "lookups_sha256": hashlib.sha256(raw).hexdigest(), "explore_model": a.explore_model,
             "parent_model": a.parent_model, "seeds": a.seeds, "cap_usd": a.cap, "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        for arm in ("G", "J"):
            ws = out / f"ws-{arm}"; shutil.copytree(repo, ws, ignore=SKIP); (ws / ".claude" / "agents").mkdir(parents=True, exist_ok=True)
            (ws / ".claude" / "agents" / "Explore.md").write_text(override(arm, a.explore_model), encoding="utf-8")
        st = out / "store"; st.mkdir()
        p = subprocess.run([jcm, "index", str(out / "ws-J"), "--no-ai-summaries"], env={**os.environ, **OFF, "CODE_INDEX_PATH": str(st)}, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if p.returncode: raise SystemExit("indexing failed:\n" + p.stderr[-2000:])
        mpath.write_text(json.dumps(m, indent=1), encoding="utf-8")
    print(f"This run starts up to {2 * a.seeds * len(lookups)} real Claude Code sessions, billed to the account your `claude` CLI uses, "
          f"and sends this repository's code to the Anthropic API. It stops at ${a.cap:.2f} (already spent here: ${spent(out):.2f}); "
          "the last session can go over by up to one model turn.", flush=True)
    check_j_tools(jcm, out / "store", out)
    if not confirm(a): raise SystemExit("not started.")
    plan = []
    for s in range(1, a.seeds + 1):
        ls = list(lookups); random.Random(1000 + s).shuffle(ls)
        for lk in ls:
            arms = ["G", "J"]; random.Random(f"{s}-{lk['id']}").shuffle(arms); plan += [(lk, s, arm) for arm in arms]
    for lk, s, arm in plan:
        name = f"{lk['id']}-{arm}-s{s}"
        if (out / "runs" / name / "run.json").exists() or (out / "runs" / (name + "-retry") / "run.json").exists(): continue
        if a.cap - spent(out) < 0.25: print("cap reached; stopping"); break
        rec = session(out, name, out / f"ws-{arm}", arm, lk, s, a, jcm, out / "store")
        if rec["wiring_problems"] and a.cap - spent(out) >= 0.25: session(out, name + "-retry", out / f"ws-{arm}", arm, lk, s, a, jcm, out / "store")
        m["spent_usd"] = spent(out); mpath.write_text(json.dumps(m, indent=1), encoding="utf-8")
    m["spent_usd"] = spent(out); m["finished_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    mpath.write_text(json.dumps(m, indent=1), encoding="utf-8")
    recs = [json.loads(f.read_text(encoding="utf-8")) for f in (out / "runs").glob("*/run.json")] if (out / "runs").exists() else []
    warn = zero_call_warning(recs)
    if warn: print(f"\n{warn}\n", flush=True)
    print(f"done: ${m['spent_usd']:.2f} spent. Report: python report.py --results \"{out}\" --lookups \"{a.lookups}\"")

if __name__ == "__main__": main()
