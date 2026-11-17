#!/usr/bin/env python3
"""Measure how faithfully jCodeMunch's Racket extractor describes real Racket code.

The question this answers is NOT "what percentage did we get". It is: **when
jCodeMunch's index differs from what Racket itself knows, is the difference an
honest gap or a false statement?** An LLM handed an incomplete index reads the
file instead; an LLM handed a wrong one repeats the error.

So the buckets are asymmetric, and so are their bars:

  EXTRA             a name we assert that Racket does not know    -> must be 0
  WRONG_SPAN        the definition is not inside the byte range
                    we would hand back for it                     -> must be 0
  MISSING           a name a human wrote that we did not find     -> reported
  CALLABLE_UNKNOWABLE  we say constant, the value turns out to be
                    a procedure. `(define curry (make-curry #f))`
                    is callable and no syntactic test can know it.
                    A ceiling, NOT a bar -- driving it to zero
                    needs an evaluator                            -> reported
  GENERATED_ONLY    macro-introduced; invisible by construction   -> reported
  EXPORT_ONLY       reachable under a different name than we
                    indexed (rename-out, struct-out)              -> reported

Ground truth comes from `oracle.rkt`, which expands each module with Racket's
own expander. See that file for why `syntax-original?` is the discriminator.

    python benchmarks/racket_fidelity/run_fidelity.py --corpus <corpus.json>
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from jcodemunch_mcp.parser.extractor import _parse_racket_symbols  # noqa: E402

HERE = Path(__file__).resolve().parent
ORACLE = HERE / "oracle.rkt"

#: Buckets whose only acceptable value is zero. A non-zero entry here means the
#: index would state something untrue, which is the failure this exists to catch.
HARD_FAIL = ("extra", "wrong_span")

#: jCodeMunch kinds that assert "you can call this".
CALLABLE_KINDS = frozenset({"function", "method"})

#: ⚠ There are NO named exemptions, and there used to be one. `(define-generics
#: async-channel-type ...)` binds `gen:async-channel-type`,
#: `async-channel-type?`, `async-channel-type/c` and each method, and NOT the
#: bare stem -- the walker emitted the stem anyway, and this function forgave
#: it by treating the oracle knowing `gen:<name>` as knowing `<name>`. That is
#: an `extra` hidden by the harness built to find it. The walker now emits
#: what the expander binds and the comparison is plain membership; any future
#: exemption goes here with its reason, and must be for one NAME, never a class.
def _oracle_knows(name: str, known: set) -> bool:
    return name in known


def racket_version() -> str:
    try:
        out = subprocess.run(["racket", "--version"], capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=60)
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unavailable"


def collects_dir() -> Path | None:
    try:
        out = subprocess.run(
            ["racket", "-e", "(require setup/dirs) (display (path->string (find-collects-dir)))"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        p = Path(out.stdout.strip())
        return p if p.is_dir() else None
    except Exception:
        return None


def run_oracle(paths: list[Path], timeout: int) -> dict[str, dict]:
    """One Racket process for the whole batch -- startup dominates otherwise."""
    if not paths:
        return {}
    proc = subprocess.run(
        ["racket", str(ORACLE), *[str(p) for p in paths]],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    out: dict[str, dict] = {}
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        out[rec["file"]] = rec
    return out


def classify(path: Path, oracle: dict) -> dict:
    """Compare one file's jCodeMunch symbols against the expander's answer."""
    source_bytes = path.read_bytes()
    jcm = _parse_racket_symbols(source_bytes, str(path))
    jcm_by_name: dict[str, list] = {}
    for s in jcm:
        jcm_by_name.setdefault(s.name, []).append(s)

    defs = oracle.get("definitions", [])
    exports = oracle.get("exports", [])

    src_defs = [d for d in defs if d.get("from_source")]
    gen_defs = [d for d in defs if not d.get("from_source")]
    src_names = {d["name"] for d in src_defs}
    gen_names = {d["name"] for d in gen_defs}
    export_names = {e["name"] for e in exports}
    # A name in ANY oracle bucket is a name Racket knows. Comparing instance
    # lists instead of name sets produces false alarms: `add-between` is a real
    # `(define ...)` AND acquires generated contract wrappers, so it legitimately
    # appears in both buckets.
    known = src_names | gen_names | export_names

    # ⚠⚠ Class members are OUT OF SCOPE for this comparison, and saying so is
    # the honest move rather than scoring them. `(define/public (area) 4)` binds
    # a member of a class VALUE, not a module-level name, so neither the
    # expanded module body nor `module->exports` mentions it -- the oracle has
    # nothing to say, and calling that a fabrication is a category error.
    #
    # The consequence must be stated plainly: THIS HARNESS DOES NOT MEASURE
    # class-member fidelity. That is covered by unit tests in
    # tests/test_racket_language.py instead. `methods_unscored` keeps the size
    # of the unmeasured set visible in results.json.
    method_names = {s.name for s in jcm if s.kind == "method"}
    scored = {n for n in jcm_by_name if n not in method_names}
    extra = sorted(n for n in scored if not _oracle_knows(n, known))

    # The property that matters is not exact line equality -- the oracle points
    # at the identifier, we point at the enclosing form. It is that the
    # definition falls INSIDE the bytes `get_symbol_source` would return.
    wrong_span = []
    oracle_lines: dict[str, list[int]] = {}
    for d in src_defs:
        if isinstance(d.get("line"), int):
            oracle_lines.setdefault(d["name"], []).append(d["line"])
    for name, lines in oracle_lines.items():
        candidates = [c for c in jcm_by_name.get(name, [])
                      if c.kind != "method"]
        if not candidates:
            continue
        # A name may be defined more than once in a file, so the question is
        # whether ANY of our spans covers ANY of the oracle's lines -- not
        # whether every span covers every line.
        if any(s.line <= ln <= max(s.end_line, s.line)
               for s in candidates for ln in lines):
            continue
        wrong_span.append({
            "name": name, "oracle_lines": lines,
            "jcm_spans": [[s.line, s.end_line] for s in candidates],
        })

    # Runtime evidence for the lambda-versus-value guess, the one an LLM acts on
    # when it decides whether a name can be called.
    callable_mismatch = []
    for e in exports:
        proc = e.get("procedure")
        if proc is None or e["name"] not in jcm_by_name:
            continue
        for s in jcm_by_name[e["name"]]:
            says_callable = s.kind in CALLABLE_KINDS
            if says_callable != bool(proc):
                callable_mismatch.append(
                    {"name": e["name"], "jcm_kind": s.kind, "is_procedure": bool(proc)})

    missing = sorted(src_names - set(jcm_by_name))
    export_only = sorted(export_names - set(jcm_by_name) - src_names - gen_names)

    return {
        "file": str(path),
        "jcm_symbols": len(jcm),
        "methods_unscored": len(method_names),
        "oracle_source": len(src_names),
        "oracle_generated": len(gen_names),
        "oracle_exports": len(export_names),
        "extra": extra,
        "wrong_span": wrong_span,
        "callable_unknowable": callable_mismatch,
        "missing": missing,
        "export_only": export_only,
    }


def resolve_targets(corpus: dict) -> list[Path]:
    paths: list[Path] = []
    for t in corpus.get("targets", []):
        if t.get("kind") == "collects":
            base = collects_dir()
            if base is None:
                print("  ! collects dir unavailable; skipping", t.get("id"), file=sys.stderr)
                continue
            root = base / t["path"]
            paths.extend(sorted(root.glob(t.get("glob", "*.rkt"))))
        elif t.get("kind") == "path":
            root = Path(os.path.expanduser(t["path"]))
            paths.extend(sorted(root.rglob(t.get("glob", "*.rkt"))))
    return [p for p in paths if p.is_file()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default=str(HERE / "corpus.json"))
    ap.add_argument("--out", default=str(HERE / "results.json"))
    ap.add_argument("--limit", type=int, default=0, help="cap files (0 = no cap)")
    ap.add_argument("--batch", type=int, default=40)
    ap.add_argument("--timeout", type=int, default=600)
    args = ap.parse_args()

    corpus = json.loads(Path(args.corpus).read_text(encoding="utf-8"))
    paths = resolve_targets(corpus)
    if args.limit:
        paths = paths[:args.limit]
    if not paths:
        print("No files resolved from corpus.", file=sys.stderr)
        return 2

    print(f"Racket: {racket_version()}   files: {len(paths)}")

    per_file: list[dict] = []
    oracle_errors: list[dict] = []
    for i in range(0, len(paths), args.batch):
        chunk = paths[i:i + args.batch]
        try:
            oracle = run_oracle(chunk, args.timeout)
        except subprocess.TimeoutExpired:
            oracle_errors.append({"batch": i, "error": "oracle timeout"})
            continue
        for p in chunk:
            rec = oracle.get(str(p))
            if rec is None or "error" in (rec or {}):
                # ⚠ Counted and named, never silently dropped. A file the oracle
                # could not expand is a file this benchmark says NOTHING about,
                # and a shrinking denominator would flatter every ratio.
                oracle_errors.append({
                    "file": str(p),
                    "error": (rec or {}).get("error", "no oracle output")[:200],
                })
                continue
            per_file.append(classify(p, rec))
        print(f"  {min(i + args.batch, len(paths))}/{len(paths)}", file=sys.stderr)

    def total(key: str) -> int:
        return sum(len(f[key]) for f in per_file)

    summary = {
        "racket_version": racket_version(),
        "files_measured": len(per_file),
        "files_oracle_failed": len(oracle_errors),
        "jcm_symbols": sum(f["jcm_symbols"] for f in per_file),
        "methods_unscored": sum(f["methods_unscored"] for f in per_file),
        "oracle_source_names": sum(f["oracle_source"] for f in per_file),
        "oracle_generated_names": sum(f["oracle_generated"] for f in per_file),
        "extra": total("extra"),
        "wrong_span": total("wrong_span"),
        "callable_unknowable": total("callable_unknowable"),
        "missing": total("missing"),
        "export_only": total("export_only"),
    }
    denom = summary["oracle_source_names"]
    summary["source_coverage_pct"] = (
        round(100.0 * (denom - summary["missing"]) / denom, 1) if denom else None
    )

    results = {
        "summary": summary,
        "hard_fail_buckets": list(HARD_FAIL),
        "oracle_errors": oracle_errors[:50],
        "per_file": per_file,
    }
    Path(args.out).write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")

    print()
    for k, v in summary.items():
        print(f"  {k:26} {v}")
    breaches = [k for k in HARD_FAIL if summary[k]]
    if breaches:
        print(f"\nHARD FAIL: {', '.join(breaches)} -- see {args.out}")
        return 1
    print(f"\nHard-fail buckets all zero. Wrote {args.out}")
    return 0


if __name__ == "__main__":
    # Windows: a redirected stdout is cp1252; write UTF-8 (L-145, L-320).
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
