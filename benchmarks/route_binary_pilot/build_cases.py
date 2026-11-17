"""Build the grounded pilot corpus. Run AFTER `predicate.py` was registered.

Each case is bound to a real repository at the SHA published in
`benchmarks/tasks.json`, so the pilot is reproducible by anyone who checks the
same commits out.

    Class S -- target is a real indexed SYMBOL.        gold = search_symbols
    Class T -- target is a real string LITERAL that
               is not any symbol's name.               gold = search_text

⚠⚠ **Gold is fixed by which kind of object the case was built from, and the
predicate never sees the target.** That is the only thing keeping this from
being a tautology: the predicate must recover the class from the task text plus
the repository's symbol vocabulary.

⚠⚠ **THE CONFOUND IS REAL AND IS MEASURED RATHER THAN ENGINEERED AWAY.**
Class-S tasks come from docstrings, which are written in the same vocabulary as
the symbol names around them. Class-T tasks come from user-facing message
strings, which are prose. So the predicate can look good for a reason that is an
artifact of where the text came from rather than a real inference. The runner
reports the separation with and without each target's OWN name parts in the
vocabulary, which is what tells those two apart.

Seeded and deterministic: same checkouts in, same cases out.
"""
from __future__ import annotations

import sys
import argparse
import json
import random
import re
from pathlib import Path

from jcodemunch_mcp.storage import IndexStore

HERE = Path(__file__).resolve().parent
CASES = HERE / "cases.json"
SEED = 4021

REPOS = [
    ("expressjs", "express", "1faf228935aa0a13111f92c28ee795be64ce3f0f", ("*.js",)),
    ("fastapi", "fastapi", "a64dfbbd21a445288ff583d58e1f646fe6baf3af", ("*.py",)),
    ("gin-gonic", "gin", "75ccf94d605a05fe24817fc2f166f6f2959d5cea", ("*.go",)),
]

PER_CLASS_PER_REPO = 10

# ⚠ Test files are excluded from literal extraction. Left in, class T fills up
# with `it('should ...')` case descriptions, which are a real kind of string but
# a skewed one -- they are not what a person is looking for when they reach for
# text search, and they would have made the corpus a test-title detector.
# Corrected after INSPECTING THE CASES and before running the predicate once;
# no prediction was computed against the discarded version.
_TEST_PATH = re.compile(r"(^|/)(tests?|spec|__tests__|testdata|e2e)(/|$)|_test\.|\.test\.|\.spec\.")

# A message-like literal: long enough to be a sentence fragment, short enough to
# be a real message, containing a space, and mostly words. Format placeholders
# and paths are excluded -- they are not what a person searches for in prose.
_LITERAL = re.compile(r"""["'`]([A-Za-z][A-Za-z0-9 ,.'\-:()]{19,79})["'`]""")
_BAD_LITERAL = re.compile(r"(%[sdvq]|\{\}|\$\{|https?://|\.\w{2,4}$|^\s*$)")
_SENTENCE = re.compile(r"[.!?]\s")


def _first_sentence(text: str) -> str:
    text = " ".join((text or "").split())
    parts = _SENTENCE.split(text, maxsplit=1)
    return parts[0].strip() if parts else text


def _strip_name(text: str, name: str) -> str:
    """Remove the target's name VERBATIM, leaving its component words alone.

    ⚠ Deliberately not a component-level scrub. "Not verbatim" is the registered
    condition; stripping every component word would also delete the ordinary
    English the sentence is made of, and would bias the corpus against H3 by
    construction rather than testing it.
    """
    if not name:
        return text
    return re.sub(re.escape(name), " ", text, flags=re.IGNORECASE)


def _symbol_cases(index, rng, want):
    out, seen = [], set()
    pool = [
        s for s in index.symbols
        if s.get("name") and len(s["name"]) >= 4
        and (s.get("summary") or s.get("docstring"))
        and s.get("kind") in {"function", "method", "class"}
    ]
    rng.shuffle(pool)
    for sym in pool:
        if len(out) >= want:
            break
        body = _first_sentence(sym.get("summary") or sym.get("docstring") or "")
        task = " ".join(_strip_name(body, sym["name"]).split())
        if not (40 <= len(task) <= 160):
            continue
        if task.lower() in seen:
            continue
        seen.add(task.lower())
        out.append({
            "gold": "search_symbols",
            "task": task,
            "target": sym["name"],
            "target_kind": "symbol",
            "file": sym.get("file"),
        })
    return out


def _literal_cases(root: Path, globs, symbol_names, rng, want):
    lowered = {n.lower() for n in symbol_names}
    found, seen = [], set()
    files = []
    for pattern in globs:
        for p in root.rglob(pattern):
            if ".git" in p.parts:
                continue
            if _TEST_PATH.search(str(p.relative_to(root)).replace("\\", "/")):
                continue
            files.append(p)
    rng.shuffle(files)
    for path in files:
        if len(found) >= want * 6:
            break
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for match in _LITERAL.finditer(text):
            literal = match.group(1).strip()
            if _BAD_LITERAL.search(literal) or " " not in literal:
                continue
            if literal.lower() in lowered:
                continue
            key = literal.lower()
            if key in seen:
                continue
            seen.add(key)
            found.append({
                "gold": "search_text",
                # ⚠ One frame for every literal. A frame chosen per-literal to
                # read well is an authoring decision inside the corpus, and this
                # class already carries enough of one.
                "task": f"where does the message {literal} come from",
                "target": literal,
                "target_kind": "literal",
                "file": str(path.relative_to(root)).replace("\\", "/"),
            })
    rng.shuffle(found)
    return found[:want]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--checkouts", type=Path, required=True,
                    help="directory holding express/ fastapi/ gin/ at the pinned SHAs")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    rng = random.Random(SEED)
    store = IndexStore()
    cases, meta = [], []
    for owner, name, sha, globs in REPOS:
        index = store.load_index(owner, name)
        if index is None:
            raise SystemExit(f"{owner}/{name} is not indexed; index it first")
        root = args.checkouts / name
        if not root.is_dir():
            raise SystemExit(f"missing checkout {root}")
        names = [s["name"] for s in index.symbols if s.get("name")]
        s_cases = _symbol_cases(index, rng, PER_CLASS_PER_REPO)
        t_cases = _literal_cases(root, globs, names, rng, PER_CLASS_PER_REPO)
        for c in s_cases + t_cases:
            c["repo"] = f"{owner}/{name}"
            c["commit"] = sha
        cases.extend(s_cases + t_cases)
        meta.append({
            "repo": f"{owner}/{name}", "commit": sha,
            "indexed_symbols": len(index.symbols),
            "class_S": len(s_cases), "class_T": len(t_cases),
        })

    payload = {
        "seed": SEED,
        "protocol": "benchmarks/route_binary_pilot/PROTOCOL.md",
        "predicate_registered_before_cases": "see git log: predicate.py precedes this file",
        "repos": meta,
        "cases": cases,
    }
    print(json.dumps({"total": len(cases),
                      "by_gold": {g: sum(1 for c in cases if c["gold"] == g)
                                  for g in ("search_symbols", "search_text")},
                      "repos": meta}, indent=2))
    if args.write:
        CASES.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
        print(f"\nwrote {CASES}")
    return 0


if __name__ == "__main__":
    # Windows: a redirected stdout is cp1252; write UTF-8 (L-145, L-320).
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
