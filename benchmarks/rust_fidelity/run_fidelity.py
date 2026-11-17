#!/usr/bin/env python3
"""Measure how faithfully jCodeMunch's Rust extractor describes real Rust code.

The question is NOT "what percentage did we get". It is: **when jCodeMunch's
index differs from what Rust itself knows, is the difference an honest gap or a
false statement?** An LLM handed an incomplete index reads the file; an LLM
handed a wrong one repeats the error.

So the buckets are asymmetric, and so are their bars:

  EXTRA        a name we assert that ``syn`` does not know  -> must be 0
  WRONG_SPAN   the definition is not inside the byte range
               ``get_symbol_source`` would hand back        -> must be 0
  MISSING      a name a human wrote that we did not find    -> reported, broken
               out by the oracle's kind so a gap has a NAME
               rather than being an unexplained shortfall

⚠⚠ THE CEILING, and it is lower than Racket's. ``syn`` PARSES; it does not
EXPAND. Racket's oracle expands, so it sees macro-introduced names and
``syntax-original?`` separates them from human-typed ones. Nothing here can. An
item produced by a ``macro_rules!`` invocation is invisible to the oracle AND to
jCodeMunch, so it is unscored in both directions. **Do not read a green run as
evidence about macro-generated code.**

    python benchmarks/rust_fidelity/run_fidelity.py --target ripgrep \
        --checkout /path/to/ripgrep
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from jcodemunch_mcp.parser.extractor import parse_file  # noqa: E402

HERE = Path(__file__).resolve().parent
ORACLE_DIR = HERE / "oracle"
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

#: Buckets whose only acceptable value is zero. A non-zero entry means the index
#: would state something untrue, which is the failure this exists to catch.
# ⚠ `undercount` and `qual_mismatch` gate at 0 alongside the original two.
# They were added 2026-08-27 after a name-keyed SET comparison scored a 37.9%
# collision rate on ripgrep as a perfect run -- 1,331 of 3,514 symbols sharing
# a bare name with a sibling in the same file, none of it visible to any bucket
# that existed. A measurement that cannot fail on a defect is not a gate.
HARD_FAIL = ("extra", "wrong_span", "undercount", "qual_mismatch")

#: Oracle kinds jCodeMunch deliberately does not emit. Each carries a REASON,
#: never a blanket category -- an exemption covering a class hides the next
#: member of it.
KNOWN_UNEMITTED = {
    "module": (
        "`mod foo;` / `mod foo { .. }` declares the module graph, not a callable "
        "or a type. The file tree already answers where a module lives."
    ),
    "macro": (
        "`macro_rules! name` defines a macro. We do not expand macros, so we can "
        "describe the definition site but nothing it produces; indexing the name "
        "alone implies a reach we do not have."
    ),
}


def oracle_binary() -> Path | None:
    for candidate in ("rust-fidelity-oracle.exe", "rust-fidelity-oracle"):
        p = ORACLE_DIR / "target" / "release" / candidate
        if p.exists():
            return p
    return None


def build_oracle() -> Path:
    """Always rebuild, then return the binary.

    ⚠⚠ This used to short-circuit on an existing binary, and that silently
    reused a STALE oracle after a failed rebuild -- the run reported the same
    numbers as before the source change, which reads as "the change had no
    effect" rather than "the change did not compile". A measurement tool that
    falls back to its previous self is worse than one that refuses.
    """
    proc = subprocess.run(
        ["cargo", "build", "--release"], cwd=ORACLE_DIR, capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        raise SystemExit(f"oracle build failed:\n{proc.stderr[-2000:]}")
    binary = oracle_binary()
    if binary is None:
        raise SystemExit("oracle built but no binary found")
    return binary


def run_oracle(binary: Path, root: Path) -> dict:
    # ⚠ encoding= is not optional. Without it Windows decodes the oracle's
    # UTF-8 JSON as cp1252 and subprocess's reader thread dies on the first
    # non-ASCII byte -- a crash in a MEASUREMENT tool, which would read as a
    # corpus problem. tests/test_subprocess_encoding_guard.py caught this.
    proc = subprocess.run(
        [str(binary), str(root)], capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        raise SystemExit(f"oracle run failed:\n{proc.stderr[-2000:]}")
    return json.loads(proc.stdout)


def checkout_sha(root: Path) -> str | None:
    proc = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    )
    return proc.stdout.strip() if proc.returncode == 0 else None


def _end_line(sym) -> int:
    end = getattr(sym, "end_line", None)
    return max(end or sym.line, sym.line)


def classify(root: Path, oracle_doc: dict) -> dict:
    """Compare jCodeMunch's symbols against the parser's answer, per file."""
    by_file: dict[str, list[dict]] = collections.defaultdict(list)
    for d in oracle_doc["defs"]:
        by_file[d["file"]].append(d)

    per_file = []
    for rel in sorted(by_file):
        path = root / rel
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        syms = parse_file(
            content, str(path), "rust", source_bytes=content.encode("utf-8")
        )
        jcm_by_name: dict[str, list] = collections.defaultdict(list)
        for s in syms:
            jcm_by_name[s.name].append(s)

        oracle_defs = by_file[rel]
        known = {d["name"] for d in oracle_defs}
        extra = sorted(n for n in jcm_by_name if n not in known)

        # ⚠ The property is not exact line equality -- the oracle points at the
        # identifier, we point at the enclosing item. It is that the definition
        # falls INSIDE the bytes get_symbol_source would return.
        wrong_span = []
        oracle_lines: dict[str, list[int]] = collections.defaultdict(list)
        for d in oracle_defs:
            if d["kind"] not in KNOWN_UNEMITTED:
                oracle_lines[d["name"]].append(d["line"])
        for name, lines in oracle_lines.items():
            candidates = jcm_by_name.get(name, [])
            if not candidates:
                continue
            # A name may be defined more than once in a file, so the question is
            # whether ANY span covers ANY oracle line.
            if any(
                s.line <= ln <= _end_line(s) for s in candidates for ln in lines
            ):
                continue
            wrong_span.append(
                {
                    "name": name,
                    "oracle_lines": sorted(lines),
                    "jcm_spans": [[s.line, _end_line(s)] for s in candidates],
                }
            )

        missing: dict[str, list[str]] = collections.defaultdict(list)
        for d in oracle_defs:
            if d["name"] not in jcm_by_name:
                missing[d["kind"]].append(d["name"])

        # ⚠⚠ Everything above keys on a bare name in a SET, and a set cannot
        # count. Proven 2026-08-27 by deleting the second symbol of every
        # duplicated name in the fixtures: `extra` and `missing` did not move.
        # `crates/core/flags/defs.rs` defines `is_switch` 108 times, so a run
        # that extracted ONE of them scored identically to a perfect one.
        # These two buckets are the part that can count.
        oracle_quals: collections.Counter = collections.Counter(
            d["qual"] for d in oracle_defs if d["kind"] not in KNOWN_UNEMITTED
        )
        jcm_quals: collections.Counter = collections.Counter(
            s.qualified_name for s in syms
        )
        undercount = [
            {"qual": q, "oracle": n, "jcm": jcm_quals.get(q, 0)}
            for q, n in sorted(oracle_quals.items())
            if jcm_quals.get(q, 0) < n
        ]
        # A name we DO find, filed under an owner the parser disagrees with.
        # Reported apart from `undercount` because the causes differ: this is
        # a wrong answer, that one is a short answer.
        oracle_names = collections.defaultdict(set)
        for d in oracle_defs:
            if d["kind"] not in KNOWN_UNEMITTED:
                oracle_names[d["name"]].add(d["qual"])
        qual_mismatch = []
        for name, want in sorted(oracle_names.items()):
            got = {s.qualified_name for s in jcm_by_name.get(name, [])}
            if got and got != want:
                qual_mismatch.append(
                    {"name": name, "oracle": sorted(want), "jcm": sorted(got)}
                )

        per_file.append(
            {
                "file": rel,
                "jcm_symbols": len(syms),
                "oracle_defs": len(oracle_defs),
                "extra": extra,
                "wrong_span": wrong_span,
                "undercount": undercount,
                "qual_mismatch": qual_mismatch,
                "missing": {k: sorted(v) for k, v in sorted(missing.items())},
            }
        )
    return {"per_file": per_file}


def summarize(result: dict, oracle_doc: dict) -> dict:
    pf = result["per_file"]
    missing_by_kind: collections.Counter = collections.Counter()
    for f in pf:
        for kind, names in f["missing"].items():
            missing_by_kind[kind] += len(names)
    total_oracle = sum(f["oracle_defs"] for f in pf)
    total_missing = sum(missing_by_kind.values())
    unexplained = {k: v for k, v in missing_by_kind.items() if k not in KNOWN_UNEMITTED}
    return {
        "files_parsed": oracle_doc["files_parsed"],
        "files_failed": oracle_doc["files_failed"],
        "oracle_defs": total_oracle,
        "jcm_symbols": sum(f["jcm_symbols"] for f in pf),
        "extra": sum(len(f["extra"]) for f in pf),
        "wrong_span": sum(len(f["wrong_span"]) for f in pf),
        "undercount": sum(len(f["undercount"]) for f in pf),
        "qual_mismatch": sum(len(f["qual_mismatch"]) for f in pf),
        "missing": total_missing,
        "missing_by_kind": dict(missing_by_kind.most_common()),
        "missing_unexplained": dict(sorted(unexplained.items())),
        "coverage_pct": (
            round(100.0 * (total_oracle - total_missing) / total_oracle, 1)
            if total_oracle
            else 0.0
        ),
        "clean_files": sum(
            1 for f in pf if not f["extra"] and not f["wrong_span"] and not f["missing"]
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default=str(HERE / "corpus.json"))
    ap.add_argument(
        "--checkout", required=True, help="directory holding the pinned checkout"
    )
    ap.add_argument("--target", required=True, help="target id from corpus.json")
    ap.add_argument("--out", default=str(HERE / "results.json"))
    ap.add_argument(
        "--write",
        action="store_true",
        help="publish results.json (refuses on a drifted checkout)",
    )
    args = ap.parse_args()

    corpus = json.loads(Path(args.corpus).read_text(encoding="utf-8"))
    target = next((t for t in corpus["targets"] if t["id"] == args.target), None)
    if target is None:
        raise SystemExit(f"no target {args.target!r} in corpus")

    pinned = target["sha"]
    # ⚠ Validated, not trusted. The first draft of corpus.json was written via a
    # shell heredoc and one digit arrived as U+096B DEVANAGARI DIGIT FIVE --
    # visually identical, and it would have pinned nothing.
    if not _SHA_RE.match(pinned):
        raise SystemExit(
            f"corpus sha for {args.target!r} is not 40 lowercase hex: {pinned!r}"
        )

    root = Path(args.checkout).resolve()
    actual = checkout_sha(root)
    drifted = actual != pinned
    if drifted and args.write:
        raise SystemExit(
            f"refusing to publish: checkout is at {actual}, corpus pins {pinned}. "
            "A number measured against a different tree is a number about a "
            "different corpus."
        )

    binary = build_oracle()
    oracle_doc = run_oracle(binary, root)
    result = classify(root, oracle_doc)
    summary = summarize(result, oracle_doc)
    summary["target"] = args.target
    summary["sha"] = actual
    summary["sha_matches_corpus"] = not drifted

    doc = {"summary": summary, "per_file": result["per_file"]}
    print(json.dumps(summary, indent=2))

    failed = [b for b in HARD_FAIL if summary[b]]
    if args.write:
        Path(args.out).write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        print(f"\nwrote {args.out}")
    if failed:
        print(f"\nGATE FAILED: {', '.join(failed)} must be 0", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    # Windows: a redirected stdout is cp1252; write UTF-8 (L-145, L-320).
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
