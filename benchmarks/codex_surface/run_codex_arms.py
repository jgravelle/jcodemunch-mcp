"""Measure jcodemunch's net token effect on Codex CLI across four arms.

Motivated by https://www.reddit.com/r/codex/comments/1vjfepe/ which measured
jCodeMunch at +28.45% on Codex and -3.34% on OpenCode. The hypothesis this
harness tests: the tool-schema payload is a FIXED per-request cost while
retrieval savings are PROPORTIONAL to baseline size, so on an already-lean
baseline the fixed cost wins. See README.md.

The fixed-cost term is already measured and needs no API credits
(`measure_surface`-style handshake, reproduced by --surface-only here):
90 tools / 24,007 tokens at default `full`, 6 tools / 1,030 at `counter`.
What this harness adds is the savings term, which needs live runs.

Arms:
  A  baseline    no MCP server at all
  B  full        jcm at default tool_surface, NO AGENTS.md routing policy
  C  full+policy jcm at default tool_surface, WITH AGENTS.md
  D  counter     jcm at tool_surface=counter, WITH AGENTS.md

B vs C isolates whether the policy file is what makes the tools get used
instead of used-in-addition-to native search. C vs D isolates the schema tax.

Usage:
    python run_codex_arms.py --preflight
    python run_codex_arms.py --surface-only
    python run_codex_arms.py --repo /path/to/pinned/clone --model gpt-5.1-codex

Nothing is written to the operator's real ~/.codex. Auth and config are
isolated via CODEX_HOME; jcm storage is isolated via CODE_INDEX_PATH.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent

# Resolve once. On Windows `codex` is a .cmd wrapper and subprocess does no
# PATHEXT lookup without shell=True, so passing the bare name raises WinError 2.
CODEX_BIN = shutil.which("codex") or "codex"

# Deliberately OUTSIDE the repository tree. Codex populates its home with
# downloaded plugin caches and skill scripts, i.e. third-party Python that every
# repo-wide guard test then scans. Pointing it at benchmarks/ made
# test_no_text_mode_subprocess_without_encoding fail on four files nobody here
# wrote and gitignore does not help, because those guards walk the filesystem,
# not the index. Keeping it out of the tree also means a live auth token is
# never one `git add -f` away.
DEFAULT_CODEX_HOME = Path(
    os.environ.get("CODE_INDEX_PATH", str(Path.home() / ".code-index"))
) / "codex_bench_home"

# ---------------------------------------------------------------------------
# Arms
# ---------------------------------------------------------------------------

ARMS = [
    {"id": "A", "name": "baseline",    "mcp": False, "agents_md": False, "config": {}},
    {"id": "B", "name": "full",        "mcp": True,  "agents_md": False,
     "config": {"tool_surface": "full", "tool_profile": "full"}},
    {"id": "C", "name": "full+policy", "mcp": True,  "agents_md": True,
     "config": {"tool_surface": "full", "tool_profile": "full"}},
    {"id": "D", "name": "counter",     "mcp": True,  "agents_md": True,
     "config": {"tool_surface": "counter"}},
]

AGENTS_MD = """# Repository policy

Prefer the jcodemunch MCP tools over shell search for code navigation.

- Locating a symbol: `search_symbols`, not `grep`.
- Reading a symbol: `get_symbol_source`, not `cat` or `sed`.
- File shape before opening: `get_file_outline`.
- Who calls or imports something: `find_references`, `find_importers`.
- Broad exploration: `get_ranked_context` answers in one call.

The repository is already indexed. Call `resolve_repo` to get its id.
"""


# ---------------------------------------------------------------------------
# Token accounting
# ---------------------------------------------------------------------------

USAGE_KEYS = {
    "input_tokens", "output_tokens", "cached_input_tokens",
    "reasoning_output_tokens", "total_tokens",
}


def find_usage(obj, out):
    """Recursively collect any dict that looks like a token-usage record.

    CONFIRMED against CLI 0.147.0 on 2026-08-10. `turn.completed` carries:
        usage: {input_tokens, cached_input_tokens, cache_write_input_tokens,
                output_tokens, reasoning_output_tokens}
    Note there is NO `total_tokens`, so sum_usage's fallback is the live path,
    and `cached_input_tokens` is a SUBSET of `input_tokens`, not an addend.
    """
    if isinstance(obj, dict):
        if USAGE_KEYS & obj.keys():
            out.append(obj)
        for v in obj.values():
            find_usage(v, out)
    elif isinstance(obj, list):
        for v in obj:
            find_usage(v, out)


def sum_usage(events):
    """Total tokens for ONE step (one `codex exec` invocation).

    Takes the LAST usage record within the step. Each step is its own process
    emitting its own `turn.completed`, so usage is PER-INVOCATION, not
    cumulative across the session. The across-step headline is therefore
    `total_summed`; `total_last_step` is kept only as a cross-check.

    Confirmed 2026-08-10 against CLI 0.147.0. Before that this file assumed
    cumulative reporting and defaulted to the last step, which would have
    under-reported every multi-step arm by roughly the whole run.
    """
    records = []
    for ev in events:
        find_usage(ev, records)
    if not records:
        return {"input": 0, "output": 0, "cached": 0, "total": 0, "records_seen": 0}
    last = records[-1]
    inp = int(last.get("input_tokens", 0) or 0)
    out = int(last.get("output_tokens", 0) or 0)
    cached = int(last.get("cached_input_tokens", 0) or 0)
    total = int(last.get("total_tokens", 0) or 0) or (inp + out)
    return {"input": inp, "output": out, "cached": cached,
            "total": total, "records_seen": len(records)}


# ---------------------------------------------------------------------------
# Codex invocation
# ---------------------------------------------------------------------------

def codex_cmd(arm, workdir, model, codex_home, storage, thread_id=None, prompt=""):
    cmd = [CODEX_BIN, "exec", "--json", "--skip-git-repo-check",
           "-s", "read-only", "-C", str(workdir)]
    if model:
        cmd += ["-m", model]

    if arm["mcp"]:
        server = f'{sys.executable}'
        cmd += [
            "-c", f'mcp_servers.jcodemunch.command={json.dumps(server)}',
            "-c", 'mcp_servers.jcodemunch.args=["-m","jcodemunch_mcp"]',
            "-c", f'mcp_servers.jcodemunch.env={{"CODE_INDEX_PATH"={json.dumps(str(storage))},'
                  f'"PYTHONPATH"={json.dumps(str(REPO_ROOT / "src"))}}}',
        ]

    # `codex exec resume` takes [SESSION_ID] [PROMPT] POSITIONALLY, so the
    # subcommand and its id must come AFTER every flag. Putting flags after the
    # id makes the parser read `-s` as a positional; the process then exits on a
    # usage error having emitted no JSON at all.
    if thread_id:
        cmd += ["resume", thread_id]
    cmd.append(prompt)
    return cmd


def run_step(arm, workdir, model, codex_home, storage, prompt, thread_id, raw_fh, timeout):
    env = dict(os.environ)
    env["CODEX_HOME"] = str(codex_home)
    cmd = codex_cmd(arm, workdir, model, codex_home, storage, thread_id, prompt)

    proc = subprocess.run(cmd, env=env, capture_output=True, timeout=timeout)
    events, new_thread, failed = [], thread_id, None

    for line in proc.stdout.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        events.append(ev)
        raw_fh.write(json.dumps(ev) + "\n")
        if ev.get("type") == "thread.started":
            new_thread = ev.get("thread_id")
        if ev.get("type") == "turn.failed":
            failed = ev.get("error", {}).get("message", "unknown")

    # A step that produced no `turn.completed` did NOT run, and must never be
    # recorded as 0 tokens. The first version of this function only believed a
    # `turn.failed` EVENT, so a process that died on a usage error before
    # emitting any JSON booked a silent zero and the summary table printed
    # percentages computed from one step out of six. Absence of a completion is
    # the failure signal; the exit code alone is not enough, because a usage
    # error and a clean finish can both be non-zero on this CLI.
    if failed is None and not any(e.get("type") == "turn.completed" for e in events):
        tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        detail = tail[-1][:200] if tail else f"exit {proc.returncode}, no output"
        failed = f"no turn.completed ({detail})"

    return events, new_thread, failed


# ---------------------------------------------------------------------------
# Surface measurement (no credits needed)
# ---------------------------------------------------------------------------

def measure_surface(config):
    """Real MCP stdio handshake. Returns (n_tools, bytes, tokens)."""
    import tiktoken
    enc = tiktoken.get_encoding("o200k_base")

    storage = tempfile.mkdtemp(prefix="jcmsurf_")
    try:
        (Path(storage) / "config.jsonc").write_text(json.dumps(config), encoding="utf-8")
        env = dict(os.environ)
        env.update({"CODE_INDEX_PATH": storage,
                    "PYTHONPATH": str(REPO_ROOT / "src"),
                    "PYTHONIOENCODING": "utf-8"})
        proc = subprocess.Popen(
            [sys.executable, "-m", "jcodemunch_mcp"], cwd=str(REPO_ROOT), env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

        def send(o):
            proc.stdin.write((json.dumps(o) + "\n").encode())
            proc.stdin.flush()

        send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "codex", "version": "0.98.0"}}})
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})

        tools, deadline = [], time.time() + 120
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            try:
                msg = json.loads(line.decode("utf-8", "replace"))
            except Exception:
                continue
            if msg.get("id") == 2 and "result" in msg:
                tools = msg["result"].get("tools", [])
                break
        proc.kill()
        proc.wait(timeout=10)

        payload = json.dumps(tools, separators=(",", ":"))
        return len(tools), len(payload), len(enc.encode(payload))
    finally:
        shutil.rmtree(storage, ignore_errors=True)


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------

def preflight(model, codex_home):
    """Fail loudly and specifically. Every check here cost real debugging once."""
    ok = True

    def check(label, passed, detail=""):
        nonlocal ok
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}{'  ' + detail if detail else ''}")
        ok = ok and passed

    print("Preflight:")
    check("codex CLI on PATH", shutil.which("codex") is not None)

    try:
        import tiktoken  # noqa: F401
        check("tiktoken importable", True)
    except ImportError:
        check("tiktoken importable", False, "pip install tiktoken")

    check("jcodemunch src present", (REPO_ROOT / "src" / "jcodemunch_mcp").is_dir())

    auth = Path(codex_home) / "auth.json"
    check("isolated CODEX_HOME has auth", auth.exists(),
          "" if auth.exists() else
          f"run: CODEX_HOME={codex_home} codex login   (device flow, needed for *-codex "
          "models: they 404 on /v1/responses with an API key)")

    # Model reachability. A 1-token probe is the cheapest real answer, and it is
    # the check that catches an unfunded account, which no static check can.
    if auth.exists():
        env = dict(os.environ)
        env["CODEX_HOME"] = str(codex_home)
        try:
            probe = [CODEX_BIN, "exec", "--json", "--skip-git-repo-check",
                     "-s", "read-only"]
            if model:
                probe += ["-m", model]
            probe.append("Reply with exactly: ok")
            p = subprocess.run(probe, env=env, capture_output=True, timeout=180)
            out = p.stdout.decode("utf-8", "replace")
            if "no credits remaining" in out:
                check(f"model {model or '<cli default>'} usable", False, "account has NO API CREDITS")
            elif "Model not found" in out:
                check(f"model {model or '<cli default>'} usable", False,
                      "not reachable on this auth; *-codex models need ChatGPT login")
            elif '"turn.completed"' in out:
                check(f"model {model or '<cli default>'} usable", True)
                usage = []
                for line in out.splitlines():
                    if line.strip().startswith("{"):
                        try:
                            find_usage(json.loads(line), usage)
                        except Exception:
                            pass
                check("token usage present in JSONL", bool(usage),
                      f"keys: {sorted(usage[-1].keys())}" if usage
                      else "PARSER NEEDS UPDATING, see find_usage() docstring")
            else:
                check(f"model {model or '<cli default>'} usable", False, "no turn.completed; see stdout")
        except subprocess.TimeoutExpired:
            check(f"model {model or '<cli default>'} usable", False, "probe timed out")

    print()
    return ok


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", help="path to the pinned clone under test")
    ap.add_argument("--model", default=None,
                    help="omit to use the CLI default, which is the only "
                         "combination verified working on a ChatGPT account")
    ap.add_argument("--tasks", default=str(HERE / "tasks.json"))
    ap.add_argument("--out", default=str(HERE / "results"))
    ap.add_argument("--codex-home", default=str(DEFAULT_CODEX_HOME),
                    help="isolated CODEX_HOME; the operator's real ~/.codex is never touched")
    ap.add_argument("--timeout", type=int, default=900, help="per-step seconds")
    ap.add_argument("--arms", default="A,B,C,D")
    ap.add_argument("--repeats", type=int, default=3,
                    help="independent sessions per arm; the spread across them "
                         "is what says whether an arm difference is real")
    ap.add_argument("--preflight", action="store_true", help="run checks and exit")
    ap.add_argument("--surface-only", action="store_true",
                    help="measure tool-schema payload per arm; needs no API credits")
    args = ap.parse_args()

    codex_home = Path(args.codex_home)
    codex_home.mkdir(parents=True, exist_ok=True)

    if args.surface_only:
        print(f"{'arm':<16} {'tools':>6} {'bytes':>10} {'tokens':>9}")
        print("-" * 46)
        base = None
        for arm in ARMS:
            if not arm["mcp"]:
                print(f"{arm['name']:<16} {0:>6} {0:>10} {0:>9}")
                continue
            n, b, t = measure_surface(arm["config"])
            base = base if base is not None else t
            print(f"{arm['name']:<16} {n:>6} {b:>10,} {t:>9,}")
        print("\nFixed cost per request. Savings term needs live runs.")
        return 0

    if args.preflight:
        return 0 if preflight(args.model, codex_home) else 1

    if not args.repo:
        ap.error("--repo is required (use --surface-only or --preflight without it)")

    if not preflight(args.model, codex_home):
        print("Preflight failed. Fix the above before spending anything.", file=sys.stderr)
        return 1

    tasks = json.loads(Path(args.tasks).read_text(encoding="utf-8"))
    steps = tasks["steps"]
    if tasks.get("target_repo", {}).get("commit") == "PIN_ME":
        print("WARNING: tasks.json has an unpinned target commit. Fine for a smoke "
              "run, not for a published number.", file=sys.stderr)

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    selected = [a for a in ARMS if a["id"] in args.arms.split(",")]
    results = []

    for arm in selected:
        print(f"\n=== arm {arm['id']}: {arm['name']} ===")
        workdir = Path(tempfile.mkdtemp(prefix=f"codexarm_{arm['id']}_"))
        storage = Path(tempfile.mkdtemp(prefix=f"codexstore_{arm['id']}_"))
        try:
            # Fresh copy per arm so AGENTS.md presence cannot leak between arms.
            shutil.copytree(args.repo, workdir / "repo",
                            ignore=shutil.ignore_patterns(".git"))
            target = workdir / "repo"

            if arm["agents_md"]:
                (target / "AGENTS.md").write_text(AGENTS_MD, encoding="utf-8")

            if arm["mcp"]:
                (storage / "config.jsonc").write_text(
                    json.dumps(arm["config"]), encoding="utf-8")
                n, b, t = measure_surface(arm["config"])
                print(f"  tool surface: {n} tools, {t:,} tokens/request")

                # Pre-index OUTSIDE the measured flow. Indexing is a one-time
                # setup cost amortised across every later session, so charging
                # it to a six-step flow overstates our cost against a baseline
                # arm that has no setup step at all. Excluded deliberately, and
                # said so in README; a run that measured it would be measuring
                # onboarding, not retrieval.
                print("  pre-indexing ...", end="", flush=True)
                ienv = dict(os.environ)
                ienv.update({"CODE_INDEX_PATH": str(storage),
                             "PYTHONPATH": str(REPO_ROOT / "src")})
                t0 = time.time()
                ip = subprocess.run(
                    [sys.executable, "-m", "jcodemunch_mcp", "index", str(target)],
                    env=ienv, capture_output=True, timeout=1800)
                if ip.returncode != 0:
                    print(" FAILED")
                    print(ip.stderr.decode("utf-8", "replace")[-800:], file=sys.stderr)
                    raise RuntimeError(f"pre-index failed for arm {arm['id']}")
                print(f" {time.time() - t0:.0f}s")
            else:
                n = b = t = 0

            # Each repeat is an INDEPENDENT session (fresh thread_id), so the
            # spread across repeats is the run-to-run variance of the whole
            # flow. Without it a single number per arm is unfalsifiable: the
            # first run of this harness showed 81k-140k across arms for
            # near-identical work, which is wider than most effects we care
            # about. The index is built once per arm and reused, because it does
            # not change between repeats.
            raw_path = outdir / f"arm_{arm['id']}_raw.jsonl"
            repeats = []
            with open(raw_path, "w", encoding="utf-8") as raw_fh:
                for rep in range(1, args.repeats + 1):
                    per_step, thread_id = [], None
                    print(f"  repeat {rep}/{args.repeats}")
                    for i, prompt in enumerate(steps, 1):
                        print(f"    step {i}/{len(steps)} ...", end="", flush=True)
                        t0 = time.time()
                        events, thread_id, failed = run_step(
                            arm, target, args.model, codex_home, storage,
                            prompt, thread_id, raw_fh, args.timeout)
                        u = sum_usage(events)
                        u.update(step=i, repeat=rep, failed=failed,
                                 seconds=round(time.time() - t0, 1))
                        per_step.append(u)
                        print(f" {u['total']:,} tok, {u['seconds']}s"
                              + (f"  FAILED: {failed}" if failed else ""))
                        if failed:
                            break
                    rep_total = sum(s["total"] for s in per_step)
                    repeats.append({
                        "repeat": rep, "steps": per_step, "total": rep_total,
                        "cached": sum(s["cached"] for s in per_step),
                        "input": sum(s["input"] for s in per_step),
                        "output": sum(s["output"] for s in per_step),
                        "failed": any(s["failed"] for s in per_step),
                    })
                    print(f"    repeat total: {rep_total:,}")

            ok = [r for r in repeats if not r["failed"]]
            totals = sorted(r["total"] for r in ok)
            median = totals[len(totals) // 2] if totals else 0
            results.append({
                "arm": arm["id"], "name": arm["name"],
                "schema_tools": n, "schema_tokens": t,
                "repeats": repeats,
                "repeats_ok": len(ok),
                "totals": totals,
                "median": median,
                "min": totals[0] if totals else 0,
                "max": totals[-1] if totals else 0,
                "cached_median": (sorted(r["cached"] for r in ok)[len(ok) // 2]
                                  if ok else 0),
                "any_failure": any(r["failed"] for r in repeats),
            })
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
            shutil.rmtree(storage, ignore_errors=True)

    (outdir / "results.json").write_text(json.dumps({
        "model": args.model,
        "target_repo": tasks.get("target_repo"),
        "arms": results,
        "repeats": args.repeats,
        "note": "Usage is PER-INVOCATION (confirmed CLI 0.147.0), so a repeat's "
                "total is the sum over its steps. Headline is the MEDIAN across "
                "repeats. Compare the arm spread against the within-arm spread "
                "before believing any delta.",
    }, indent=2), encoding="utf-8")

    print(f"\n{'arm':<16} {'schema tok':>11} {'median':>10} "
          f"{'min':>10} {'max':>10} {'cached':>9} {'vs base':>9}")
    print("-" * 82)
    baseline = next((r["median"] for r in results if r["arm"] == "A"), 0)
    for r in results:
        delta = (f"{100 * (r['median'] - baseline) / baseline:+.1f}%"
                 if baseline else "n/a")
        flag = "  INCOMPLETE" if r["any_failure"] else ""
        print(f"{r['name']:<16} {r['schema_tokens']:>11,} {r['median']:>10,} "
              f"{r['min']:>10,} {r['max']:>10,} {r['cached_median']:>9,} "
              f"{delta:>9}{flag}")

    # The honesty gate. If arms differ by less than a single arm varies with
    # itself, the run has measured noise and must not be quoted as an effect.
    base = next((r for r in results if r["arm"] == "A"), None)
    if base and base["repeats_ok"] > 1 and baseline:
        within = base["max"] - base["min"]
        spans = [abs(r["median"] - baseline) for r in results if r["arm"] != "A"]
        print(f"\nbaseline within-arm spread: {within:,} tokens "
              f"({100 * within / baseline:.1f}% of median)")
        if spans and max(spans) < within:
            print("WARNING: EVERY arm difference is smaller than the baseline's own "
                  "run-to-run spread. This run distinguishes nothing. More "
                  "repeats or a tighter task flow, not a conclusion.")
    print(f"\nRaw events and results.json in {outdir}")
    return 0


if __name__ == "__main__":
    # Windows: a redirected stdout is cp1252; write UTF-8 (L-145, L-320).
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
