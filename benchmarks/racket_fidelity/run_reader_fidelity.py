#!/usr/bin/env python3
"""Measure the Python Racket reader against Racket's own reader.

`run_fidelity.py` asks whether the SYMBOLS we extract are the ones Racket
binds. This asks the question underneath it: is the TREE the walker is given
the tree Racket reads? A wrong tree can produce a right symbol by luck, and
the expander harness cannot tell luck from correctness -- it only sees names.

Ground truth is `reader_oracle.rkt`: `read-syntax` with `read-accept-reader`,
so each file is read by the reader its own `#lang` line selects. Every syntax
object becomes a (type, byte-start, byte-end) triple and the two sides are
compared as multisets. The buckets, and their bars:

  ONLY_RACKET       a node Racket read that we did not, or read with a
                    different span                                  -> must be 0
  ONLY_OURS         a node we read that Racket did not              -> must be 0
  OUR_ONLY_ERROR    Racket read the file; we reported an error      -> must be 0
  RACKET_ONLY_ERROR Racket rejected the file; we read it. Lenient
                    in the harmless direction (`#\\12`), listed     -> reported
  BOTH_ERROR        both rejected it                                -> reported

What is deliberately NOT compared, stated so nobody reads a green run as more
than it is:

  * strings INSIDE an at-exp form. The at-exp reader splits text bodies into
    per-line strings, merges `@"str"` escapes into their neighbours and
    synthesises indentation strings; the Python reader emits text runs on a
    coarser split. The walker never reads inside a body, so bodies are
    compared by the SPAN of the form that holds them.
  * comments, `.`, `#lang` lines, and the contents of `#hash(...)` and
    `#s(...)` literals -- Racket's `read-syntax` does not wrap hash keys or
    prefab keys as syntax, so there is no position to compare.
  * the datum after a `#reader <module>` form. It is read by THAT module's
    reader, which this reader does not have -- `scribble/comment-reader`
    turns the text of `;` comments into at-exp forms, for instance. Counted
    in `reader_extension_forms`; the default reader's guess at the datum's
    extent is what the walker sees.

    python benchmarks/racket_fidelity/run_reader_fidelity.py
    python benchmarks/racket_fidelity/run_reader_fidelity.py --corpus my.json --racket-langs conscript=at-exp
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from jcodemunch_mcp.parser import extractor as _extractor  # noqa: E402
from jcodemunch_mcp.parser.extractor import _racket_command_char, _racket_tier  # noqa: E402
from jcodemunch_mcp.parser.racket_reader import read_racket  # noqa: E402

HERE = Path(__file__).resolve().parent
ORACLE = HERE / "reader_oracle.rkt"

HARD_FAIL = ("only_racket", "only_ours", "our_only_error")

#: Node types on OUR side that Racket's reader has no counterpart for.
_OURS_ONLY_TYPES = frozenset({
    "program", "comment", "block_comment", "sexp_comment", "dot",
    "extension", "lang_name", "graph", "decimal", "ERROR",
})
#: Wrappers whose inner `list` is our tree-sitter-shaped artefact.
_WRAPPED = frozenset({"vector", "hash", "structure"})
#: Literals whose CONTENTS are unpositioned on Racket's side.
_OPAQUE = frozenset({"hash", "structure"})


def racket_version() -> str:
    try:
        out = subprocess.run(["racket", "--version"], capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=60)
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unavailable"


def _racket_dir(expr: str) -> Path | None:
    try:
        out = subprocess.run(
            ["racket", "-e", f"(require setup/dirs) (display (path->string ({expr})))"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        p = Path(out.stdout.strip())
        return p if p.is_dir() else None
    except Exception:
        return None


def run_oracle(paths: list[Path], timeout: int) -> dict[str, dict]:
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


def _drop_strings_in_at_forms(nodes: list[tuple[str, int, int]],
                              at_spans: list[tuple[int, int]]) -> tuple[list, int]:
    if not at_spans:
        return nodes, 0
    kept, dropped = [], 0
    for t, s, e in nodes:
        if t == "string" and any(a <= s and e <= b for a, b in at_spans):
            dropped += 1
        else:
            kept.append((t, s, e))
    return kept, dropped


def oracle_nodes(rec: dict) -> tuple[list[tuple[str, int, int]], list[tuple[int, int]]]:
    nodes, at_spans = [], []
    for type_, pos, span, at in rec["nodes"]:
        if pos is None or span is None:
            continue
        s, e = pos - 1, pos - 1 + span
        nodes.append((type_, s, e))
        if at:
            at_spans.append((s, e))
    return nodes, at_spans


def our_nodes(root) -> tuple[list[tuple[str, int, int]], list[tuple[int, int]], list[tuple[int, int]]]:
    nodes, at_spans, reader_spans = [], [], []
    stack = [(root, False)]
    while stack:
        n, artefact = stack.pop()
        t = n.type
        if t in _OURS_ONLY_TYPES:
            if t == "extension" and n.text.startswith(b"#reader"):
                reader_spans.append((n.start_byte, n.end_byte))
            elif t == "program":
                stack.extend((c, False) for c in n.children)
            continue
        if artefact:
            # The `list` a tree-sitter-shaped `vector`/`hash`/`structure`
            # wraps: not a node on Racket's side, but its children are.
            stack.extend((c, False) for c in n.children)
            continue
        if t == "here_string":
            t = "string"
        nodes.append((t, n.start_byte, n.end_byte))
        if n.at_form:
            at_spans.append((n.start_byte, n.end_byte))
        if t in _OPAQUE:
            continue
        stack.extend((c, t in _WRAPPED and c.type == "list") for c in n.children)
    return nodes, at_spans, reader_spans


def compare(path: Path, rec: dict, at_exp: bool, command_char: bytes = b"@") -> dict:
    src = path.read_bytes()
    tree = read_racket(src, at_exp=at_exp, command_char=command_char)
    out = {"file": str(path), "at_exp": at_exp, "only_racket": [], "only_ours": [],
           "nodes_compared": 0, "at_forms": 0, "strings_in_at_forms_dropped": 0,
           "reader_extension_forms": 0}
    if "error" in rec:
        out["racket_error"] = rec["error"][:200]
        out["our_error"] = str(tree.errors[0]) if tree.errors else None
        return out
    if tree.errors:
        out["our_error"] = str(tree.errors[0])
        return out
    theirs, their_at = oracle_nodes(rec)
    ours, our_at, reader_spans = our_nodes(tree.root_node)
    theirs, d1 = _drop_strings_in_at_forms(theirs, their_at)
    ours, d2 = _drop_strings_in_at_forms(ours, our_at)
    if reader_spans:
        def outside(nodes):
            return [n for n in nodes if not any(a <= n[1] and n[2] <= b for a, b in reader_spans)]
        theirs, ours = outside(theirs), outside(ours)
    a, b = collections.Counter(theirs), collections.Counter(ours)
    out["nodes_compared"] = sum(b.values())
    out["at_forms"] = len(their_at)
    out["strings_in_at_forms_dropped"] = d1
    out["reader_extension_forms"] = len(reader_spans)

    def show(items):
        return [[t, s, e, src[s:e][:40].decode("utf-8", "replace")] for t, s, e in sorted(items)]

    out["only_racket"] = show((a - b).elements())
    out["only_ours"] = show((b - a).elements())
    return out


def _lang_of(path: Path) -> str | None:
    try:
        with path.open("rb") as fh:
            head = fh.read(4096)
    except OSError:
        return None
    lang, _ = _extractor._racket_lang_of(head)
    return lang


def resolve_targets(corpus: dict) -> list[Path]:
    paths: list[Path] = []
    for t in corpus.get("targets", []):
        kind = t.get("kind")
        if kind in ("collects", "pkgs"):
            base = _racket_dir("find-collects-dir" if kind == "collects" else "find-pkgs-dir")
            if base is None:
                print(f"  ! {kind} dir unavailable; skipping", t.get("id"), file=sys.stderr)
                continue
            root = base / t["path"] if t.get("path") else base
            found = sorted(root.rglob(t["glob"]) if t.get("recursive") else root.glob(t.get("glob", "*.rkt")))
        elif kind == "path":
            root = Path(os.path.expanduser(t["path"]))
            found = sorted(root.rglob(t.get("glob", "*.rkt")))
        else:
            continue
        found = [p for p in found if p.is_file() and "compiled" not in p.parts]
        if t.get("lang"):
            want = t["lang"]
            found = [p for p in found if (_lang_of(p) or "") == want]
        paths.extend(found)
    seen: set[Path] = set()
    return [p for p in paths if not (p in seen or seen.add(p))]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default=str(HERE / "reader_corpus.json"))
    ap.add_argument("--out", default=str(HERE / "reader_results.json"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--batch", type=int, default=60)
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--racket-langs", default="",
                    help="comma-separated lang=tier promotions, as `racket_langs` config would declare")
    args = ap.parse_args()

    promoted = {}
    for item in filter(None, args.racket_langs.split(",")):
        lang, tier = item.split("=", 1)
        promoted[lang] = tier
    if promoted:
        _extractor._racket_configured_langs = lambda repo: promoted   # type: ignore[assignment]

    corpus = json.loads(Path(args.corpus).read_text(encoding="utf-8"))
    paths = resolve_targets(corpus)
    if args.limit:
        paths = paths[:args.limit]
    if not paths:
        print("No files resolved from corpus.", file=sys.stderr)
        return 2
    print(f"Racket: {racket_version()}   files: {len(paths)}")

    per_file: list[dict] = []
    skipped_text: list[str] = []
    for i in range(0, len(paths), args.batch):
        chunk = []
        for p in paths[i:i + args.batch]:
            tier, written = _racket_tier(p.read_bytes(), "/racket-fidelity")
            if tier == "text":
                skipped_text.append(str(p))
            else:
                chunk.append((p, tier == "at-exp", _racket_command_char(written, "/racket-fidelity")))
        try:
            oracle = run_oracle([p for p, _, _ in chunk], args.timeout)
        except subprocess.TimeoutExpired:
            per_file.extend({"file": str(p), "racket_error": "oracle timeout", "only_racket": [],
                             "only_ours": [], "nodes_compared": 0, "at_forms": 0} for p, _, _ in chunk)
            continue
        for p, at_exp, cc in chunk:
            rec = oracle.get(str(p)) or {"error": "no oracle output"}
            per_file.append(compare(p, rec, at_exp, cc))
        print(f"  {min(i + args.batch, len(paths))}/{len(paths)}", file=sys.stderr)

    racket_errors = [f for f in per_file if f.get("racket_error")]
    summary = {
        "racket_version": racket_version(),
        "files": len(per_file),
        "files_at_exp": sum(1 for f in per_file if f["at_exp"]),
        "files_skipped_text_tier": len(skipped_text),
        "nodes_compared": sum(f["nodes_compared"] for f in per_file),
        "at_forms": sum(f["at_forms"] for f in per_file),
        "strings_in_at_forms_dropped": sum(f.get("strings_in_at_forms_dropped", 0) for f in per_file),
        "reader_extension_forms": sum(f.get("reader_extension_forms", 0) for f in per_file),
        "only_racket": sum(len(f["only_racket"]) for f in per_file),
        "only_ours": sum(len(f["only_ours"]) for f in per_file),
        "our_only_error": sum(1 for f in per_file if f.get("our_error") and not f.get("racket_error")),
        "racket_only_error": sum(1 for f in racket_errors if not f.get("our_error")),
        "both_error": sum(1 for f in racket_errors if f.get("our_error")),
    }
    results = {
        "summary": summary,
        "hard_fail_buckets": list(HARD_FAIL),
        "not_compared": ["strings inside at-exp forms (compared by the form's span)",
                         "comments, `.`, `#lang`, contents of #hash and #s literals",
                         "the datum after `#reader <module>` (read by that module's reader)"],
        "racket_only_errors": [{"file": f["file"], "error": f["racket_error"]}
                               for f in racket_errors if not f.get("our_error")][:100],
        "our_only_errors": [{"file": f["file"], "error": f["our_error"]}
                            for f in per_file if f.get("our_error") and not f.get("racket_error")],
        "per_file": [f for f in per_file if f["only_racket"] or f["only_ours"] or f.get("our_error")
                     or f.get("racket_error")],
        "files_skipped_text_tier": skipped_text,
    }
    Path(args.out).write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print()
    for k, v in summary.items():
        print(f"  {k:30} {v}")
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
