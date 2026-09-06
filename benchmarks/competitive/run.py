"""The competitive tier runner (docs/competitive/DESIGN.md s5, s6).

purpose:  run every adapter on every corpus over every task three times and
          write the result file and the human summary; nothing here types a
          number, quotes a README, or moves a Floor
invokes:  adapters by name from adapter.REGISTRY; git for the commit; a
          scratch directory per run so every run is cold
produces: results/<UTC date>-<commit>.json (schema jcm-competitive-result/v1)
          and results/latest.md; optionally results/history.jsonl (--record)
refuses:  the `docker` sandbox when no Linux daemon answers (a competitor
          never runs outside it, DESIGN D2; pass --sandbox none for the
          nulls and jcodemunch alone); a task file that fails the
          answerability rule (a task whose
          category no null adapter declares, or whose expected file is not
          in the corpus); an adapter that fails adapter.validate; recording
          history from a dirty tree
pinned:   the working tree (jcodemunch) and each adapter's Pin
fairness: DESIGN s1: same file set (the corpus's tracked text files, what
          an agent with no tool could open; NOT our discovery's admitted
          set, CF-5), same tasks, same tokenizer. The null rows are on
          every table.

Usage:
  python benchmarks/competitive/run.py [--corpus ID=PATH ...] [--tasks FILE]
      [--adapters a,b,c] [--runs 3] [--out-dir DIR] [--record] [--sandbox docker|none]
  With no --corpus, the self corpus (this tree's src/) is copied to scratch.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))

from adapter import SCHEMA, Corpus, Task, corpus_digest, read_file, validate  # noqa: E402
from score import DIFF_AXES, RATIO_AXES, compare, f1  # noqa: E402
import corpora as corpora_mod  # noqa: E402
import corpus_check  # noqa: E402
import sandbox  # noqa: E402
import task_check  # noqa: E402

DEFAULT_ADAPTERS = ("null_readall", "null_grep", "jcodemunch")
CATEGORY_F1 = {"P1": "f1_P1", "P2": "f1_P2", "P4": "f1_P4", "P5": "f1_P5"}


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=REPO, text=True, encoding="utf-8", errors="replace").strip()


def load_adapter(name: str, sandbox_mode: str = "docker"):
    from adapter import REGISTRY

    spec = REGISTRY[name]
    mod, fn = spec.split(":")
    factory = getattr(importlib.import_module(mod), fn)
    try:
        return validate(factory(sandbox_mode))
    except TypeError:
        return validate(factory())  # the nulls take no mode


def load_tasks(path: Path) -> list[Task]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    tasks = []
    for r in raw["tasks"]:
        tasks.append(Task(
            id=r["id"], corpus=r["corpus"], category=r["category"], query=r["query"],
            expected=tuple((f, int(ln)) for f, ln in r.get("expected", [])),
            tolerance_lines=int(r.get("tolerance_lines", 0)), source=r.get("source", ""),
            capability_only=bool(r.get("capability_only", False)),
        ))
    return tasks


def discover_files(corpus_path: Path, scratch: Path) -> tuple[str, ...]:
    """What jcodemunch's own discovery admits for a corpus; reported, never the
    shared file set (see build_corpus). Runs in a subprocess so the discovery
    is cold and leaves nothing in this process."""
    code = (
        "import json,sys\n"
        "from jcodemunch_mcp.tools.index_folder import index_folder\n"
        "from jcodemunch_mcp.storage import IndexStore\n"
        f"r=index_folder(path={str(corpus_path)!r}, use_ai_summaries=False, storage_path={str(scratch / 'discover')!r})\n"
        "assert r.get('success'), r\n"
        f"idx=IndexStore(base_path={str(scratch / 'discover')!r}).load_index(*r['repo'].split('/',1))\n"
        "print('FILES '+json.dumps(sorted(idx.source_files)))\n"
    )
    env = dict(os.environ, CODE_INDEX_PATH=str(scratch / "discover"), PYTHONPATH=str(REPO / "src"),
               JCODEMUNCH_TRUSTED_FOLDERS=str(corpus_path), JCODEMUNCH_LIVE_JOURNAL="0")
    proc = subprocess.run([sys.executable, "-c", code], env=env, text=True, capture_output=True, encoding="utf-8", errors="replace", timeout=1200)
    line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("FILES ")), None)
    if proc.returncode != 0 or line is None:
        raise SystemExit(f"discovery failed for {corpus_path}:\n{proc.stdout[-1500:]}\n{proc.stderr[-1500:]}")
    return tuple(json.loads(line[6:]))


def copy_source_tree(src: Path, dst: Path) -> None:
    """The self corpus is this tree's `src/` WITHOUT its bytecode: `__pycache__`
    directories and `*.pyc`/`*.pyo` files are what an interpreter left behind
    on the host, not source, and a plain copytree carried 816 of them into a
    git repository every tool was told to index (FINDINGS CF-39: "Git repo:
    .git with 1,093 files" for 277 sources). Ignored by name AND by suffix, so
    a stray compiled file outside the cache dir is excluded too."""
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))


def _git_init(root: Path) -> None:
    """A corpus is a git repository: the pinned ones are checkouts, and a tool
    that keys its index by git root (cymbal) answers nothing otherwise (CF-10)."""
    if (root / ".git").exists():
        return
    for args in (["init", "-q"], ["add", "-A"], ["-c", "user.email=compete@local", "-c", "user.name=compete", "commit", "-q", "-m", "corpus"]):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def build_corpus(cid: str, path: Path, scratch: Path) -> Corpus:
    """The shared file set is the corpus's own tracked files (`git ls-files`),
    text files only: what an agent with no tool could open. It is NOT what
    jcodemunch's discovery admits (the first draft used that, and our own
    size cap withheld `server.py` from every row, including the truth for a
    task cymbal answered correctly: review round 1 of PR 2a, CF-5 rewritten).
    Each tool indexes what it wants and reports `files_indexed`."""
    if not (path / ".git").exists():
        raise SystemExit(f"refused: corpus {cid} at {path} is not a git repository (CF-10); the pinned corpora are checkouts")
    listed = subprocess.check_output(["git", "ls-files", "-z"], cwd=path).decode("utf-8").split("\0")
    files = []
    for rel in listed:
        p = path / rel
        if not rel or not p.is_file():
            continue
        with open(p, "rb") as fh:
            if b"\0" in fh.read(8192):
                continue  # binary: no agent reads it, no tool indexes it
        files.append(rel)
    files = tuple(sorted(files))
    return Corpus(id=cid, path=path, sha256=corpus_digest(path, files), files=files)


def check_tasks(tasks: list[Task], corpora: dict[str, Corpus], adapters: list) -> list[str]:
    """DESIGN s4.3: lives in task_check.py (item 3); kept as the name the tests call."""
    return task_check.check(tasks, corpora, adapters)


def run_once(adapters: list, corpora: dict[str, Corpus], tasks: list[Task], scratch: Path) -> dict:
    """One run: every adapter, every corpus, every task. Returns per-adapter,
    per-corpus axis values plus per-task detail."""
    out: dict = {}
    corpus_lines = {cid: sum(read_file(c, rel).count("\n") + 1 for rel in c.files) for cid, c in corpora.items()}
    for a in adapters:
        out[a.name] = {}
        for cid, corpus in corpora.items():
            ts = [t for t in tasks if t.corpus == cid and t.category in a.categories]
            sc = scratch / a.name / cid.replace("/", "_").replace("@", "_")
            sc.mkdir(parents=True, exist_ok=True)
            if hasattr(a, "prepare"):
                a.prepare(corpus, sc, ts)
            rep = a.index(corpus, sc)
            per_task = []
            if not rep.ok:
                # DESIGN s1.3/s9.2: a tool whose index step failed or timed out is a
                # `not_runnable` row with its reason, never partial means over the
                # tasks that happened to finish (review round 1, finding 2).
                out[a.name][cid] = {"axes": {ax: None for ax in ("index_cold_seconds", "tokens_per_task", "calls_per_task", "latency_call_ms", "tools_list_tokens", *CATEGORY_F1.values())} | {"index_ok": False, "files_indexed": rep.files_indexed},
                                    "tasks": [], "index_error": rep.stderr_tail[:500],
                                    "not_runnable": ("timeout" if getattr(a, "timed_out", lambda *_: False)(corpus, sc) else "index failed: " + (rep.stderr_tail[:200] or "no output"))}
                continue
            for t in ts:
                ans = a.answer(corpus, t, sc)
                per_task.append({
                    "task": t.id, "category": t.category, "tokens": ans.tokens, "calls": ans.calls,
                    "latency_ms": [round(x, 2) for x in ans.latency_ms], "cited": len(ans.cited),
                    "f1": f1(ans.cited, t.expected, t.tolerance_lines, ans.cites_all, corpus_lines[cid]) if t.expected else None,
                    "error": ans.error,
                })
            scored = [p for p in per_task if not p["error"]]
            axes: dict = {
                "index_cold_seconds": rep.seconds,
                "index_ok": rep.ok,
                "files_indexed": rep.files_indexed,
                "tokens_per_task": (statistics.mean(p["tokens"] for p in scored) if scored else None),
                "calls_per_task": (statistics.mean(p["calls"] for p in scored) if scored else None),
                # median over EVERY call of every task: what an agent waits per call.
                # The operations differ by tool (a symbol fetch vs a whole-file read),
                # so the ratio is a wait ratio, not a like-for-like operation (DESIGN s5.1).
                "latency_call_ms": _warm_median([x for p in scored for x in p["latency_ms"]]),
                "tools_list_tokens": a.tools_list_tokens(),
            }
            for cat, axis in CATEGORY_F1.items():
                vals = [p["f1"] for p in scored if p["category"] == cat and p["f1"] is not None]
                axes[axis] = statistics.mean(vals) if vals else None
            out[a.name][cid] = {"axes": axes, "tasks": per_task, "index_error": rep.stderr_tail[:500]}
    return out


def _scorer_sha256() -> str:
    """The code that scored this file (CF-9): run.py, score.py, adapter.py, every adapter."""
    h = hashlib.sha256()
    for p in sorted([HERE / "run.py", HERE / "score.py", HERE / "adapter.py", *(HERE / "adapters").glob("*.py"), *(x for x in (HERE / "sandbox").iterdir() if x.is_file())]):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()


def _warm_median(xs: list[float]):
    return round(statistics.median(xs), 2) if xs else None


def aggregate(runs: list[dict], adapters: list, corpora: dict[str, Corpus], jcm_name: str = "jcodemunch") -> list[dict]:
    rows = []
    axes = list(RATIO_AXES) + list(DIFF_AXES)
    for a in adapters:
        for cid in corpora:
            for axis in axes:
                tool_vals = [r[a.name][cid]["axes"].get(axis) for r in runs]
                jcm_vals = [r[jcm_name][cid]["axes"].get(axis) for r in runs] if jcm_name in [x.name for x in adapters] else []
                row = compare(axis, tool_vals, jcm_vals)
                row.update({"tool": a.name, "corpus": cid})
                rows.append(row)
    return rows


def render_md(result: dict) -> str:
    h = result["header"]
    lines = [f"# Competitive tier — {h['date']} at {h['jcm_commit']} ({h['jcm_version']})", ""]
    lines.append("A competitor's README figure is not on this page. Every number below was produced by this run on this corpus with this tokenizer (cl100k_base); `measured` is the median of the runs, `spread` is max minus min, `band` is max(5% of our median, 3x the larger spread); a delta is called meaningful only when both rows are inside the band and the gap exceeds it. ⚠ Runs in this file: " + str(h["runs"]) + (" (fewer than three: no bands, DESIGN s5)" if h["runs"] < 3 else "") + ".")
    lines.append("")
    lines.append("Corpora: " + "; ".join(f"`{c['id']}` {c['files']} files, sha256 `{c['sha256'][:12]}`" for c in h["corpora"]))
    lines.append("")
    lines.append(f"Sandbox: `{h.get('sandbox')}`" + (" (nulls and jcodemunch on the host; no competitor row can appear in a `none` run)" if h.get("sandbox") == "none" else " (every row in the D2 container: --network none, read-only rootfs, no capabilities, uid 65534, 8g, 512 pids)") + f"; tree dirty: {h.get('tree_dirty')}; scorer sha256 `{str(h.get('scorer_sha256'))[:12]}`")
    lines.append("")
    lines.append("Pins: " + "; ".join(f"`{p['name']}` {p['registry']}:{p['package']}@{p['version']} (ran as {p['ran_as']}" + (f", image `{p['image_digest'][7:19]}`" if p.get('image_digest') else "") + ")" for p in h["pins"]))
    lines.append("")
    tools = [p["name"] for p in h["pins"]]
    corpora = [c["id"] for c in h["corpora"]]
    by = {(r["axis"], r["tool"], r["corpus"]): r for r in result["rows"]}
    for axis in list(RATIO_AXES) + list(DIFF_AXES):
        if all(by[(axis, t, c)]["measured"] is None for t in tools for c in corpora):
            continue
        unit = "ratio vs jcm" if axis in RATIO_AXES else "difference vs jcm"
        lines.append(f"## {axis} ({unit})")
        if axis == "latency_call_ms":
            lines.append("")
            lines.append("Median wall time of ONE call, over every call of every task. The operations differ by tool (a symbol fetch, a whole-file read), so this is what an agent waits per call, not a like-for-like operation.")
        lines.append("")
        lines.append("| tool | " + " | ".join(corpora) + " |")
        lines.append("|---|" + "---|" * len(corpora))
        for t in tools:
            cells = []
            for c in corpora:
                r = by[(axis, t, c)]
                if r["measured"] is None:
                    cells.append("NOT COMPARABLE")
                    continue
                cell = f"{r['measured']:.4g}"
                if r["delta"] is not None and t != "jcodemunch":
                    cell += f" (delta {r['delta']:.3g}"
                    if r["band"] is not None:
                        cell += f", band {r['band']:.3g}"
                    cell += ", MEANINGFUL" if r["meaningful"] else ""
                    cell += ")"
                if r["spread"] is not None:
                    cell += f" spread {r['spread']:.3g}"
                if r["note"]:
                    cell += f" [{r['note']}]"
                cells.append(cell)
            lines.append(f"| {t} | " + " | ".join(cells) + " |")
        lines.append("")
    if result.get("not_runnable"):
        lines.append("## Not runnable")
        lines.append("")
        for nr in result["not_runnable"]:
            lines.append(f"- `{nr['tool']}`: {nr['reason']}")
        lines.append("")
    if result.get("capability_only"):
        lines.append("## Capability differences (excluded from head-to-head)")
        lines.append("")
        for t in result["capability_only"]:
            lines.append(f"- `{t['task']}` ({t['category']}): answerable by {', '.join(t['answerable_by']) or 'no non-null tool'}")
        lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--corpus", action="append", default=[], help="ID=PATH|DOMAIN; repeatable; added to the set")
    ap.add_argument("--set", default=str(HERE / "corpora.json"), help="the pinned corpus set (corpora.json); fetched into the cache by SHA; 'none' = the self corpus plus --corpus extras only: the corpus check is RECORDED, not enforced, and --record refuses on a failing one (a smoke run is never a recorded one)")
    ap.add_argument("--only", default="", help="comma-separated corpus ids from the set to run (the check still judges the whole set)")
    ap.add_argument("--tasks", default=str(HERE / "tasks"), help="a task file, or a directory of them (every *.json)")
    ap.add_argument("--adapters", default=",".join(DEFAULT_ADAPTERS))
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--out-dir", default=str(HERE / "results"))
    ap.add_argument("--record", action="store_true", help="append to history.jsonl (clean tree only)")
    ap.add_argument("--keep-scratch", action="store_true")
    ap.add_argument("--sandbox", choices=("docker", "none"), default="docker",
                    help="docker: every row in the D2 container (default); none: nulls and jcodemunch on the host, labelled")
    a = ap.parse_args(argv)

    if a.record and _git("status", "--porcelain"):
        print("refused: --record on a dirty tree", file=sys.stderr)
        return 2

    if a.sandbox == "docker" and not sandbox.docker_available():
        print("refused: --sandbox docker but no Linux Docker daemon answers; --sandbox none runs the nulls and jcodemunch on the host, labelled", file=sys.stderr)
        return 4
    scratch = Path(tempfile.mkdtemp(prefix="jcm-compete-"))
    try:
        adapters = [load_adapter(n.strip(), a.sandbox) for n in a.adapters.split(",") if n.strip()]
        commit = _git("rev-parse", "--short", "HEAD")
        corpora: dict[str, Corpus] = {}
        domains: dict[str, str] = {}
        src = scratch / "self" / "src"
        copy_source_tree(REPO / "src", src)
        _git_init(src.parent)
        corpora[f"self@{commit}"] = build_corpus(f"self@{commit}", src.parent, scratch / "discover-self")
        domains[f"self@{commit}"] = "code intelligence server"
        set_entries = [] if a.set == "none" else corpora_mod.load(Path(a.set))
        only = {x.strip() for x in a.only.split(",") if x.strip()}
        checked: list[dict] = [corpus_check.describe(f"self@{commit}", src.parent, corpora[f"self@{commit}"].files, domains[f"self@{commit}"])]
        for entry in set_entries:
            path = corpora_mod.fetch(entry, log=lambda m: print(m, file=sys.stderr))
            c = build_corpus(entry["id"], path, scratch / ("discover-" + entry["id"].replace("/", "_").replace("@", "_")))
            checked.append(corpus_check.describe(entry["id"], path, c.files, entry.get("domain", "unknown")))
            if not only or entry["id"] in only:
                corpora[entry["id"]] = c
                domains[entry["id"]] = entry.get("domain", "unknown")
        for spec in a.corpus:
            cid, _, rest = spec.partition("=")
            p, _, dom = rest.partition("|")
            corpora[cid] = build_corpus(cid, Path(p).resolve(), scratch / ("discover-" + cid.replace("/", "_").replace("@", "_")))
            domains[cid] = dom or "unknown"
            checked.append(corpus_check.describe(cid, Path(p).resolve(), corpora[cid].files, domains[cid]))
        corpus_problems, corpus_verdict = corpus_check.check(checked, corpus_check.load_policy())
        corpus_verdict["enforced"] = a.set != "none"
        if corpus_problems and corpus_verdict["enforced"]:
            print("refused: corpus check failed (DESIGN s3.3)\n  " + "\n  ".join(corpus_problems), file=sys.stderr)
            return 5
        if corpus_problems and a.record:
            print("refused: --record with --set none and a failing corpus check (DESIGN s3.3): a smoke run is not a recorded one", file=sys.stderr)
            return 5
        if corpus_problems:
            print("corpus check FAILED (recorded, not enforced under --set none):\n  " + "\n  ".join(corpus_problems), file=sys.stderr)

        task_paths = sorted(Path(a.tasks).glob("*.json")) if Path(a.tasks).is_dir() else [Path(a.tasks)]
        tasks = [t for tp in task_paths for t in load_tasks(tp)]
        tasks = [Task(**{**t.__dict__, "corpus": t.corpus.replace("self@HEAD", f"self@{commit}")}) for t in tasks]
        tasks = [t for t in tasks if t.corpus in corpora]  # a task for a set member left out by --only is not a problem
        problems = check_tasks(tasks, corpora, adapters)
        if problems:
            print("refused: task check failed\n  " + "\n  ".join(problems), file=sys.stderr)
            return 3

        scored_tasks, capability_only = task_check.split(tasks, adapters)

        runs = []
        for i in range(a.runs):
            runs.append(run_once(adapters, corpora, scored_tasks, scratch / f"run{i}"))
            print(f"run {i + 1}/{a.runs} done", file=sys.stderr, flush=True)
            # A checkpoint per run: a process killed mid-run (CF-49) keeps every run it finished.
            # Not a result file (no rows, no band); `--record` never reads it.
            ck = Path(a.out_dir) / f"checkpoint-{commit}.json"
            ck.parent.mkdir(parents=True, exist_ok=True)
            ck.write_text(json.dumps({"schema": "jcm-competitive-checkpoint/v1", "commit": commit, "runs_done": len(runs), "runs": runs}), encoding="utf-8")
        ck.unlink(missing_ok=True)  # every run finished: the result file below is the record

        version = "unknown"
        try:
            import re

            version = re.search(r'^version\s*=\s*"([^"]+)"', (REPO / "pyproject.toml").read_text(encoding="utf-8"), re.M).group(1)
        except Exception:
            pass
        header = {
            "schema": SCHEMA, "date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "jcm_commit": commit,
            "jcm_version": version, "runs": a.runs,
            "runner": {"os": platform.platform(), "python": platform.python_version(), "cpus": os.cpu_count(),
                       "ci": bool(os.environ.get("GITHUB_ACTIONS"))},
            "corpora": [{"id": c.id, "files": len(c.files), "sha256": c.sha256} for c in corpora.values()],
            "tasks_sha256": hashlib.sha256(b"".join(tp.read_bytes() for tp in task_paths)).hexdigest(),
            "task_files": [tp.name for tp in task_paths],
            "corpus_check": corpus_verdict,
            "sandbox": a.sandbox,
            "tree_dirty": bool(_git("status", "--porcelain")),
            "scorer_sha256": _scorer_sha256(),
            "pins": [{"name": x.name, **x.pin.__dict__, "ran_as": x.version(), "interface": x.interface,
                      "image_digest": (x.image().digest if hasattr(x, "image") and getattr(x, "_image", None) is not None else None)} for x in adapters],
        }
        not_runnable = sorted({(a.name, cid, r[a.name][cid].get("not_runnable")) for r in runs for a in adapters for cid in corpora if r[a.name][cid].get("not_runnable")})
        result = {"header": header, "rows": aggregate(runs, adapters, corpora), "runs": runs,
                  "capability_only": capability_only,
                  "tools_not_called": task_check.tools_not_called(runs, adapters),
                  "not_runnable": [{"tool": t, "corpus": c, "reason": why} for t, c, why in not_runnable]}
        out_dir = Path(a.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d", time.gmtime())
        rf = out_dir / f"{stamp}-{commit}.json"
        rf.write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
        (out_dir / "latest.md").write_text(render_md(result), encoding="utf-8")
        if a.record:
            line = {"date": header["date"], "jcm_commit": commit, "jcm_version": version,
                    "pins": {p["name"]: p["version"] for p in header["pins"]},
                    "medians": {f"{r['axis']}/{r['tool']}/{r['corpus']}": r["measured"] for r in result["rows"] if r["measured"] is not None}}
            with open(out_dir / "history.jsonl", "a", encoding="utf-8") as fh:
                fh.write(json.dumps(line) + "\n")
        print(f"wrote {rf} and {out_dir / 'latest.md'}")
        return 0
    finally:
        if not a.keep_scratch:
            shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
