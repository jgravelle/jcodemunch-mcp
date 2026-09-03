"""`python -m harness [fast|full|bench|all|check ID [--stamp]|threshold ID|thresholds|corpora|warm]`

The single entry point that decides whether a change is acceptable
(docs/harness/DESIGN.md). Exit code is non-zero on any test failure, any Floor
violation, or any tier over its runtime ceiling. Every threshold verdict is
printed as one line: id, criterion, floor, observed, PASS|FAIL.

Tier membership: harness/tiers.json. Floors: harness/thresholds.json (only).
Corpus checksums: harness/corpora.json. Results: harness/results/latest.json
when --write-results is given.

Methodology rules R1-R62 (docs/harness/ARCHAEOLOGY.md section A) bind the bench
tier; the assertions that enforce them cite the rule number.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "src"))

from harness import thresholds as T  # noqa: E402
from harness import corpora as C  # noqa: E402

TIERS = json.loads((HERE / "tiers.json").read_text(encoding="utf-8"))
RESULTS_DIR = HERE / "results"
PY = sys.executable


_VERDICT_RE = re.compile(
    r"^(\S+)\s+crit (\S+)\s+floor (\S+ \S+)\s+observed (\S+)\s+(PASS|FAIL)\b"
)


class _Tee:
    """Stdout wrapper that keeps every threshold verdict line (docs/cicd/DESIGN.md section 8).

    `--summary FILE` writes them as a Markdown table (GitHub's step summary);
    `--annotate` prints a `::error title=<id>::...` line for each FAIL so the
    verdict shows in the Checks tab without opening the log. Formatting only:
    the verdict itself comes from thresholds.py.
    """

    def __init__(self, real):
        self.real = real
        self.lines: list[str] = []
        self._buf = ""

    def write(self, text):
        self.real.write(text)
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self.lines.append(line)

    def flush(self):
        self.real.flush()

    def verdicts(self) -> list[tuple[str, str, str, str, str]]:
        out = []
        for ln in self.lines:
            m = _VERDICT_RE.match(ln.strip())
            if m:
                out.append(m.groups())
        return out

    def summary_markdown(self, title: str, ok: bool) -> str:
        rows = [
            "| threshold | criterion | floor | observed | verdict |",
            "|---|---|---|---|---|",
        ]
        for tid, crit, floor, obs, verdict in self.verdicts():
            rows.append(
                f"| `{tid}` | {crit} | {floor} | {obs} | {'**FAIL**' if verdict == 'FAIL' else 'PASS'} |"
            )
        extra = [
            ln
            for ln in self.lines
            if ln.startswith(("   ", "  ")) and ("passed" in ln or "failed" in ln)
        ]
        head = f"## {title}: {'PASS' if ok else 'FAIL'}\n\n"
        body = (
            "\n".join(rows) if len(rows) > 2 else "_no threshold verdicts in this run_"
        )
        tail = (
            ("\n\n```\n" + "\n".join(x.strip() for x in extra[-3:]) + "\n```")
            if extra
            else ""
        )
        return head + body + tail + "\n"

    def annotations(self) -> list[str]:
        return [
            f"::error title={tid}::floor {floor}, observed {obs} (criterion {crit}, docs/standard/STANDARD.md)"
            for tid, crit, floor, obs, verdict in self.verdicts()
            if verdict == "FAIL"
        ]


def _env() -> dict:
    return {
        "os": platform.platform(),
        "python": platform.python_version(),
        "cpus": os.cpu_count(),
        "runner": "github" if os.environ.get("GITHUB_ACTIONS") else "local",
        "commit": _git("rev-parse", "--short", "HEAD"),
    }


def _git(*args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", *args], cwd=REPO, text=True, encoding="utf-8", errors="replace"
        ).strip()
    except Exception:
        return "unknown"


def _run(cmd: list[str], *, env: dict | None = None) -> tuple[int, str, float]:
    t0 = time.perf_counter()
    e = dict(os.environ)
    e.setdefault("PYTHONPATH", str(REPO / "src"))
    e.setdefault("PYTHONIOENCODING", "utf-8")
    if env:
        e.update(env)
    proc = subprocess.run(
        cmd,
        cwd=REPO,
        env=e,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out, time.perf_counter() - t0


TOKENIZER_ASSET = "cl100k_base"


def warm_assets() -> bool:
    """Fetch the tokenizer's BPE asset OUTSIDE the pytest session (F-14).

    tiktoken downloads `cl100k_base` on first use and caches it per box. Under
    `tests/conftest.py::_no_network` that download raises, so on a fresh runner
    every test that counts tokens (schema budget, benchmark harness, negative
    evidence) fails in setup: 4 failed + 22 errors on the first CI run of this
    tier. A warm box never shows it. Warming here keeps the tests offline
    without marking a token-count test `network`.
    """
    rc, out, secs = _run(
        [PY, "-c", f"import tiktoken; tiktoken.get_encoding({TOKENIZER_ASSET!r})"]
    )
    print(
        f"== warm tokenizer asset {TOKENIZER_ASSET}: {'ok' if rc == 0 else 'FAILED'} {secs:.1f}s"
    )
    if rc != 0:
        print(out[-1500:])
    return rc == 0


def self_index(store: Path) -> str | None:
    """Index this repo into a FRESH store and return its repo id (F-15).

    The replay fixture's repo id is a hash of the absolute index path, which
    differs per box; `run_replay.py --repo/--storage-path` exist for exactly
    this and `replay.yml` already used them. The first bench run on CI did
    not and scored mrr 0.0 against a golden 1.0: no index, not a regression.
    A fresh store per run is also what makes the step deterministic locally.
    """
    # Absolute path (a relative one logs a warning to the same stream) and a
    # sentinel line, because stdout and stderr are merged by _run.
    code = (
        "from jcodemunch_mcp.tools.index_folder import index_folder;"
        f"r = index_folder(path={str(REPO)!r}, use_ai_summaries=False, incremental=False, storage_path={str(store)!r});"
        "print('REPO_ID=' + r['repo'])"
    )
    rc, out, secs = _run([PY, "-c", code])
    m = re.search(r"^REPO_ID=(\S+)$", out, re.M)
    rid = m.group(1) if m else ""
    if rc == 0 and not rid:
        rc = 1
    print(
        f"== self index -> {store}: {'ok ' + rid if rc == 0 else 'FAILED'} {secs:.1f}s"
    )
    if rc != 0:
        print(out[-1500:])
        return None
    return rid


_SUMMARY = re.compile(
    r"(?:(\d+) passed)?(?:, )?(?:(\d+) skipped)?(?:, )?(?:(\d+) failed)?"
)


def _annotate_failure(title: str, out: str, *, max_lines: int = 8) -> None:
    """Surface a non-threshold failure (pytest, ruff, corpora) as check annotations.

    A Floor failure carries its own verdict line; a failing TEST or a lint
    error does not, and the first CI probe showed only "Process completed
    with exit code 1" in the Checks tab (docs/cicd/VERIFICATION.md). Printed
    only under GitHub Actions so local runs stay readable.
    """
    if not os.environ.get("GITHUB_ACTIONS"):
        return
    failed = [
        ln.strip() for ln in out.splitlines() if ln.startswith(("FAILED ", "ERROR "))
    ]
    lines = (
        failed[:max_lines]
        if failed
        else [ln.strip() for ln in out.strip().splitlines()[-max_lines:]]
    )
    for ln in lines:
        print(f"::error title={title}::{ln[:400]}")
    if len(failed) > max_lines:
        print(f"::error title={title}::... {len(failed) - max_lines} more; see the log")


def _pytest_summary(out: str) -> dict:
    line = ""
    for ln in out.splitlines()[::-1]:
        if re.search(r"\b(passed|failed|error)\b", ln) and (" in " in ln):
            line = ln
            break

    def grab(word: str) -> int:
        m = re.search(rf"(\d+) {word}", line)
        return int(m.group(1)) if m else 0

    return {
        "passed": grab("passed"),
        "skipped": grab("skipped"),
        "failed": grab("failed") + grab("error"),
        "line": line.strip(),
    }


def _xdist_args() -> list[str]:
    """Probe the interpreter that will RUN pytest, not this one.

    First run of the fast tier under a bare `python -m harness` took 110 s
    serial and failed its own 90 s ceiling: the conda interpreter had no
    xdist while `.venv` did. `uv run python -m harness` is the documented
    spelling; a serial fallback is announced so a ceiling failure reads as
    "wrong interpreter", not "slow tests" (FINDINGS F-12).
    """
    probe = subprocess.run([PY, "-c", "import xdist"], capture_output=True)
    if probe.returncode == 0:
        return ["-n", "auto", "--dist", "loadfile"]
    print(
        f"[harness] WARNING: pytest-xdist not importable by {PY}; running SERIAL. "
        "Use `uv run python -m harness` (the .venv has xdist).",
        file=sys.stderr,
    )
    return []


# --------------------------------------------------------------------------- measurers
# Each returns the OBSERVED value for a threshold id, or None when it can only be
# established by a test/harness the tier runs (the verdict then comes from that
# run's exit code, and `check` says so).


def _m_languages_registry() -> int:
    from jcodemunch_mcp.parser.languages import LANGUAGE_REGISTRY

    return len(LANGUAGE_REGISTRY)


def _m_languages_extensions() -> int:
    from jcodemunch_mcp.parser.languages import LANGUAGE_EXTENSIONS

    return len(LANGUAGE_EXTENSIONS)


def _m_counter_saving() -> float:
    b = json.loads(
        (REPO / "benchmarks" / "schema_baseline.json").read_text(encoding="utf-8")
    )
    return round(1 - b["counter_full"] / b["full_full"], 4)


def _m_claude_md() -> int:
    return len((REPO / "CLAUDE.md").read_text(encoding="utf-8"))


def _m_core_compact() -> int:
    """Identical method to tests/test_schema_budget.py::test_live_core_compact (cl100k, live build)."""
    import tiktoken
    from jcodemunch_mcp import config as config_module
    from jcodemunch_mcp.server import _build_tools_list

    enc = tiktoken.get_encoding("cl100k_base")
    cfg = config_module._GLOBAL_CONFIG  # type: ignore[attr-defined]
    original = {k: cfg.get(k) for k in ("tool_profile", "compact_schemas")}
    try:
        cfg["tool_profile"] = "core"
        cfg["compact_schemas"] = True
        tools = _build_tools_list()
        payload = [
            {"name": t.name, "description": t.description, "inputSchema": t.inputSchema}
            for t in tools
        ]
        return len(enc.encode(json.dumps(payload, separators=(",", ":"))))
    finally:
        for k, v in original.items():
            if v is None:
                cfg.pop(k, None)
            else:
                cfg[k] = v


def _m_route_control() -> float:
    """Same computation as tests/test_catalog_moratorium.py::_control_at_1 over the committed artifact."""
    bench = REPO / "benchmarks" / "route_recall"
    holdout = json.loads((bench / "holdout_results.json").read_text(encoding="utf-8"))
    corpus = json.loads((bench / "holdout.json").read_text(encoding="utf-8"))
    mirrors = {q["q"]: q.get("mirrors", "control") for q in corpus["queries"]}
    rows = [r for r in holdout["per_query"] if mirrors.get(r["query"]) == "control"]
    hits = sum(1 for r in rows if r["route_rank"] == 1)
    return round(100.0 * hits / len(rows), 1)


def _m_rust(bucket: str):
    def f() -> int:
        r = json.loads(
            (REPO / "benchmarks" / "rust_fidelity" / "results.json").read_text(
                encoding="utf-8"
            )
        )
        s = r.get("summary", r)
        return int(s[bucket])

    return f


def _m_racket(bucket: str):
    def f() -> int:
        r = json.loads(
            (REPO / "benchmarks" / "racket_fidelity" / "results.json").read_text(
                encoding="utf-8"
            )
        )
        s = r.get("summary", r)
        return int(s[bucket])

    return f


def _m_goldset_recall() -> float:
    r = json.loads(
        (REPO / "benchmarks" / "provenance" / "channel_accuracy.json").read_text(
            encoding="utf-8"
        )
    )
    chans = r.get("channels", r)
    vals = [
        v["recall"] for v in chans.values() if isinstance(v, dict) and "recall" in v
    ]
    return min(vals)


def _m_ci_timeout() -> int:
    text = (REPO / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8")
    m = re.search(r"^\s*timeout-minutes:\s*(\d+)", text, re.M)
    return int(m.group(1)) if m else 10**6


def _measure_types() -> int:
    """pyright error count over src/ (STANDARD N3, types half). Ratchet: may only fall."""
    rc, out, _ = _run(["uvx", "pyright@1.1.405", "src/", "--outputjson"])
    try:
        data = json.loads(out[out.index("{") : out.rindex("}") + 1])
        return int(data["summary"]["errorCount"])
    except Exception:
        print(out[-1500:])
        raise SystemExit(
            "pyright did not produce a summary; the count is UNKNOWN and UNKNOWN blocks"
        )


AUDIT_ALLOWLIST = HERE / "audit-allowlist.json"


def _measure_deps() -> int:
    """Known advisories in the RUNTIME dependency set (criterion 8), minus unexpired allowlist entries.

    pip-audit (OSV/PyPI) reports no severity, so the Floor counts every
    advisory; an entry in harness/audit-allowlist.json needs an id, a reason
    and an `expires` date, after which it counts again.
    """
    import tempfile

    req = Path(tempfile.mkdtemp(prefix="harness-audit-")) / "requirements.txt"
    rc, out, _ = _run(
        [
            "uv",
            "export",
            "--no-dev",
            "--no-hashes",
            "--format",
            "requirements-txt",
            "-o",
            str(req),
        ]
    )
    if rc != 0 or not req.exists():
        print(out[-1500:])
        raise SystemExit(
            "uv export failed; the dependency set is UNKNOWN and UNKNOWN blocks"
        )
    rc, out, _ = _run(
        [
            "uvx",
            "pip-audit@2.9.0",
            "-r",
            str(req),
            "-f",
            "json",
            "--progress-spinner",
            "off",
        ]
    )
    try:
        data = json.loads(out[out.index("{") : out.rindex("}") + 1])
    except Exception:
        print(out[-1500:])
        raise SystemExit(
            "pip-audit did not produce JSON; the advisory count is UNKNOWN and UNKNOWN blocks"
        )
    allow = {}
    if AUDIT_ALLOWLIST.exists():
        today = time.strftime("%Y-%m-%d")
        for e in json.loads(AUDIT_ALLOWLIST.read_text(encoding="utf-8")).get(
            "allow", []
        ):
            if e.get("expires", "") >= today and e.get("reason"):
                allow[e["id"]] = e
    count = 0
    for dep in data.get("dependencies", []):
        for v in dep.get("vulns", []):
            tag = f"{dep['name']}=={dep['version']} {v['id']} fix={','.join(v.get('fix_versions') or []) or 'none'}"
            if v["id"] in allow:
                print(f"   allowlisted until {allow[v['id']]['expires']}: {tag}")
            else:
                print(f"   advisory: {tag}")
                count += 1
    return count


NETWORK_MEASURERS = {"types.error_max": _measure_types, "deps.vuln_max": _measure_deps}

MEASURERS = {
    "languages.registry_min": _m_languages_registry,
    "languages.extensions_min": _m_languages_extensions,
    "counter.saving_min": _m_counter_saving,
    "claude_md.max_chars": _m_claude_md,
    "schema.core_compact_ceiling": _m_core_compact,
    "route.control_at1": _m_route_control,
    "fidelity.rust.extra": _m_rust("extra"),
    "fidelity.rust.wrong_span": _m_rust("wrong_span"),
    "fidelity.rust.undercount": _m_rust("undercount"),
    "fidelity.rust.qual_mismatch": _m_rust("qual_mismatch"),
    "fidelity.racket.extra": _m_racket("extra"),
    "fidelity.racket.wrong_span": _m_racket("wrong_span"),
    "goldset.recall_min": _m_goldset_recall,
    "ci.test_job_timeout_minutes": _m_ci_timeout,
}

# Thresholds whose verdict is carried by a test or harness exit code rather than
# a value the runner can read on its own.
DELEGATED = {
    "replay.max_relative_drop": "benchmarks/replay/run_replay.py --gate (bench tier) / .github/workflows/replay.yml",
    "schema.drift_tolerance": "tests/test_schema_budget.py (fast tier)",
    "token.grand_ratio_vs_grep": "benchmarks/harness/run_benchmark.py --floor (bench tier, network)",
    "token.per_repo_rise_max": "benchmarks/harness/run_benchmark.py --floor (bench tier, network)",
    "coverage.min": "pytest --cov-fail-under (full tier)",
    "suite.fast_seconds": "this runner, fast tier wall clock",
    "suite.full_seconds": "this runner, full tier wall clock",
    "ci.skips_ubuntu": "this runner / test.yml, pytest summary",
    "ci.skips_windows": "this runner / test.yml, pytest summary",
    "suite.fast_skips_max": "harness fast tier, pytest summary",
}


def check(
    tid: str, *, stamp: bool = False, explicit: bool = False
) -> tuple[bool | None, object]:
    e = T.get(tid)
    if tid in NETWORK_MEASURERS and not explicit:
        print(
            f"{tid:<40} crit {e['criterion']:<3} floor {e['comparator']} {e['floor']!s:<12} delegated to `python -m harness check {tid}` (network; pr-gate.yml stage 1)"
        )
        return None, None
    if tid in NETWORK_MEASURERS:
        observed = NETWORK_MEASURERS[tid]()
        ok = T.passes(tid, observed)
        print(T.verdict_line(tid, observed))
        if stamp:
            _stamp(tid, observed)
        return ok, observed
    if tid in MEASURERS:
        observed = MEASURERS[tid]()
        ok = T.passes(tid, observed)
        print(T.verdict_line(tid, observed))
        if stamp:
            _stamp(tid, observed)
        return ok, observed
    if not DELEGATED.get(tid, "").startswith("this runner"):
        print(
            f"{tid:<40} crit {e['criterion']:<3} floor {e['comparator']} {e['floor']!s:<12} delegated to {DELEGATED.get(tid, '?')}"
        )
    return None, None


def _stamp(tid: str, observed: object) -> None:
    data = json.loads(T.THRESHOLDS_PATH.read_text(encoding="utf-8"))
    for e in data["thresholds"]:
        if e["id"] == tid:
            e["measured"] = {
                "value": observed,
                "commit": _git("rev-parse", "--short", "HEAD"),
                "date": time.strftime("%Y-%m-%d"),
                "env": platform.platform(),
            }
    T.THRESHOLDS_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def offline_checks(stamp: bool = False) -> tuple[bool, list[dict]]:
    ok_all = True
    rows = []
    for tid in T.load(announce=False):
        ok, obs = check(tid, stamp=stamp)
        rows.append(
            {
                "id": tid,
                "floor": T.floor(tid),
                "observed": obs,
                "verdict": "PASS" if ok else ("FAIL" if ok is False else "DELEGATED"),
            }
        )
        if ok is False:
            ok_all = False
    return ok_all, rows


def _skips_floor_id() -> str:
    return "ci.skips_windows" if sys.platform.startswith("win") else "ci.skips_ubuntu"


# --------------------------------------------------------------------------- tiers


def tier_fast(result: dict) -> bool:
    t0 = time.perf_counter()
    ok = True
    print("== corpora checksums")
    bad = C.verify()
    if bad:
        ok = False
        for b in bad:
            print("  MISMATCH", b)
        _annotate_failure("fast tier: corpus checksum", "\n".join(bad))
    else:
        print("  all pinned corpora match harness/corpora.json")
    if not warm_assets():
        ok = False
    files = TIERS["fast"]
    print(f"== fast tier: {len(files)} files")
    rc, out, secs = _run(
        [PY, "-m", "pytest", *files, "-q", "-p", "no:cacheprovider", *_xdist_args()]
    )
    summ = _pytest_summary(out)
    print("  ", summ["line"])
    if rc != 0:
        ok = False
        print(out[-4000:])
        _annotate_failure("fast tier: pytest", out)
    # A skip ceiling here too: a rebuilt .venv without the watch extra took
    # this tier from 7 skips to 112 at exit 0 (2026-09-03, the 08-28 shape).
    print(T.verdict_line("suite.fast_skips_max", summ["skipped"]))
    if not T.passes("suite.fast_skips_max", summ["skipped"]):
        ok = False
    print("== ruff check src/")
    rc2, out2, _ = _run([PY, "-m", "ruff", "check", "src/"])
    print("  ", out2.strip().splitlines()[-1] if out2.strip() else "")
    if rc2 != 0:
        ok = False
        print(out2[-3000:])
        _annotate_failure("fast tier: ruff check src/", out2)
    print("== offline thresholds")
    ok3, rows = offline_checks()
    ok = ok and ok3
    for u in TIERS.get("unclear", []):
        print(f"REVIEW  {u['path']}: {u['question']}")
    wall = time.perf_counter() - t0
    fl = T.floor("suite.fast_seconds")
    print(T.verdict_line("suite.fast_seconds", round(wall, 2)))
    if wall > fl:
        ok = False
    result["tiers"]["fast"] = {
        "seconds": round(wall, 2),
        **{k: summ[k] for k in ("passed", "skipped", "failed")},
        "ruff_ok": rc2 == 0,
    }
    result["thresholds"] = rows
    return ok


def tier_full(result: dict) -> bool:
    t0 = time.perf_counter()
    cov = T.floor("coverage.min")
    warm_ok = warm_assets()
    print(f"== full tier: tests/ with --cov-fail-under={cov}")
    rc, out, secs = _run(
        [
            PY,
            "-m",
            "pytest",
            "tests/",
            "-q",
            "-p",
            "no:cacheprovider",
            *_xdist_args(),
            "--tb=short",
            "--cov=src",
            "--cov-report=term",
            f"--cov-fail-under={cov}",
        ]
    )
    summ = _pytest_summary(out)
    print("  ", summ["line"])
    ok = rc == 0 and warm_ok
    if rc != 0:
        print(out[-6000:])
        _annotate_failure("full tier: pytest", out)
    m = re.search(r"^TOTAL\s+\d+\s+\d+\s+(\d+)%", out, re.M)
    cov_obs = int(m.group(1)) if m else None
    if cov_obs is not None:
        print(T.verdict_line("coverage.min", cov_obs))
    sid = _skips_floor_id()
    print(T.verdict_line(sid, summ["skipped"]))
    if not T.passes(sid, summ["skipped"]):
        ok = False
    wall = time.perf_counter() - t0
    print(T.verdict_line("suite.full_seconds", round(wall, 2)))
    if wall > T.floor("suite.full_seconds"):
        ok = False
    result["tiers"]["full"] = {
        "seconds": round(wall, 2),
        **{k: summ[k] for k in ("passed", "skipped", "failed")},
        "coverage_pct": cov_obs,
    }
    return ok


def tier_bench(result: dict, *, offline: bool) -> bool:
    t0 = time.perf_counter()
    ok = True
    arts: dict = {}
    for step in TIERS["bench"]:
        if step.get("network") and offline:
            print(f"== {step['name']}: SKIPPED (--offline; needs network)")
            arts[step["name"]] = {"skipped": "offline"}
            continue
        cmd = list(step["cmd"])
        if step.get("self_index"):
            import shutil
            import tempfile

            store = Path(tempfile.mkdtemp(prefix="harness-self-index-"))
            rid = self_index(store)
            if rid is None:
                ok = False
                arts[step["name"]] = {
                    "rc": 1,
                    "seconds": 0.0,
                    "tail": "self index failed",
                }
                shutil.rmtree(store, ignore_errors=True)
                continue
            cmd += ["--repo", rid, "--storage-path", str(store)]
        print(f"== {step['name']}: {' '.join(cmd)}")
        rc, out, secs = _run([PY, *cmd])
        if step.get("self_index"):
            shutil.rmtree(store, ignore_errors=True)
        tail = "\n".join(out.strip().splitlines()[-3:])
        print(f"   rc={rc} {secs:.1f}s\n   " + tail.replace("\n", "\n   "))
        arts[step["name"]] = {"rc": rc, "seconds": round(secs, 2), "tail": tail}
        if rc != 0:
            ok = False
        if step.get("restore"):
            subprocess.run(["git", "checkout", "--", *step["restore"]], cwd=REPO)
    lat = RESULTS_DIR / "self_latency.json"
    if lat.exists():
        arts["self_latency"] = json.loads(lat.read_text(encoding="utf-8"))
    wall = time.perf_counter() - t0
    result["tiers"]["bench"] = {"seconds": round(wall, 2), "ok": ok}
    result["artifacts"] = arts
    return ok


def write_results(result: dict) -> Path:
    RESULTS_DIR.mkdir(exist_ok=True)
    p = RESULTS_DIR / "latest.json"
    p.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return p


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m harness")
    ap.add_argument(
        "command",
        nargs="?",
        default="all",
        choices=[
            "fast",
            "full",
            "bench",
            "all",
            "check",
            "threshold",
            "thresholds",
            "corpora",
            "warm",
        ],
    )
    ap.add_argument("id", nargs="?")
    ap.add_argument(
        "--stamp",
        action="store_true",
        help="check: write the observed value into thresholds.json `measured`",
    )
    ap.add_argument(
        "--offline", action="store_true", help="bench: skip steps that need the network"
    )
    ap.add_argument(
        "--write-results", action="store_true", help="write harness/results/latest.json"
    )
    ap.add_argument(
        "--pin",
        action="store_true",
        help="corpora: (re)write harness/corpora.json checksums",
    )
    ap.add_argument(
        "--summary",
        metavar="FILE",
        help="append a Markdown table of every verdict line to FILE (GitHub step summary)",
    )
    ap.add_argument(
        "--annotate",
        action="store_true",
        help="print a ::error annotation for every FAIL verdict",
    )
    a = ap.parse_args(argv)
    tee = None
    if a.summary or a.annotate:
        tee = _Tee(sys.stdout)
        sys.stdout = tee
    try:
        rc = _dispatch(a)
    finally:
        if tee is not None:
            sys.stdout = tee.real
            if a.summary:
                title = f"harness {a.command}" + (f" {a.id}" if a.id else "")
                with open(a.summary, "a", encoding="utf-8") as fh:
                    fh.write(tee.summary_markdown(title, rc == 0))
            if a.annotate:
                for line in tee.annotations():
                    print(line)
    return rc


def _dispatch(a) -> int:
    if a.command == "threshold":
        print(T.floor(a.id))
        return 0
    if a.command == "warm":
        return 0 if warm_assets() else 1
    if a.command == "thresholds":
        for tid, e in T.load(announce=False).items():
            print(
                f"{tid:<40} crit {e['criterion']:<3} {e['comparator']} {e['floor']!s:<10} set {e['set_at']['date']} @{e['set_at']['commit']}  measured {e.get('measured') and e['measured'].get('value')}"
            )
        return 0
    if a.command == "corpora":
        if a.pin:
            C.pin()
            print("pinned", C.MANIFEST)
        bad = C.verify()
        print("mismatches:", bad or "none")
        return 1 if bad else 0
    if a.command == "check":
        if a.id:
            ok, _ = check(a.id, stamp=a.stamp, explicit=True)
            return 0 if ok in (True, None) else 1
        ok, _ = offline_checks(stamp=a.stamp)
        return 0 if ok else 1

    result = {
        "schema": "jcm-harness-result/v1",
        "date": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "env": _env(),
        "tiers": {},
    }
    ok = True
    if a.command in ("fast", "all"):
        ok = tier_fast(result) and ok
    if a.command in ("full", "all"):
        ok = tier_full(result) and ok
    if a.command in ("bench", "all"):
        ok = tier_bench(result, offline=a.offline) and ok
    if a.write_results:
        print("results ->", write_results(result))
    print("HARNESS", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
