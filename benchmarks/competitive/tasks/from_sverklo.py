"""Task generator: sverklo-bench's lodash / requests / express ground-truth
rules, reproduced in Python over OUR pinned checkouts.

purpose:  give the competitive tier line-level ground truth on three corpora
          that a third party defined (DESIGN s4.2 asks for tasks not written
          by the adapter author; these rules were), without running a line
          of that party's code
invokes:  nothing but the filesystem: the pinned checkouts in corpora.py's
          cache, read with `git ls-files` for the file set; no network, no
          competitor, no jcodemunch
produces: tasks/lodash.json, tasks/requests.json, tasks/express.json, each
          task carrying `source` = the sverklo-bench commit, its licence and
          the rule that produced the expected set
refuses:  a hand-given P1 line that does not carry the symbol's definition
          at our SHA (the line moved: the task would grade every tool
          against a wrong answer); a corpus whose cache is missing (fetch
          first; nothing here fetches); an expected file absent from the
          checkout's tracked set
pinned:   sverklo-bench a0c3017c819452012fee69cd727913ba50fee865 (the
          generators lodash.gen.ts, requests.gen.ts, express.gen.ts; tasks
          CC-BY-4.0); corpora by SHA from corpora.json
fairness: the rules are theirs, verbatim where a regex can be verbatim:
          P1 = a hand-verified definition line; P2 = a word-grep over the
          named paths minus the definition line (a REFERENCE task is graded
          against grep, so a tool that finds fewer references than grep
          loses, and so does one that finds more); P4 = the files whose
          text imports the named file, at line 0 with a wide tolerance (a
          file-level answer, like self.json's). P5 (dead code) is dropped:
          its expected set is empty by construction and DESIGN s4.1 reports
          P5 without scoring it. Express's P1 lines are resolved by the
          definition regex at OUR SHA (theirs is 4.21.1); a symbol the
          regex cannot find at our SHA is dropped and counted in the file's
          note, never guessed.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import corpora as corpora_mod  # noqa: E402

SVERKLO = "sverklo-bench@a0c3017c (CC-BY-4.0)"
WIDE = 100000       # a file-level P4 answer, like self.json's
P2_TOLERANCE = 2    # a reference cited within two lines of grep's line counts

# The five published token queries (benchmarks/tasks.json) as category T,
# for the three corpora those numbers were published on.
T_QUERIES = ("router route handler", "middleware", "error exception", "request response", "context bind")
T_AUTHORED = {
    "lodash": ("deep clone", "debounce throttle timer", "iteratee shorthand"),
    "requests": ("session cookies", "retry adapter", "redirect handling"),
}


def _tracked(root: Path) -> list[str]:
    out = subprocess.check_output(["git", "ls-files"], cwd=root, text=True, encoding="utf-8")
    return [f for f in out.split("\n") if f and (root / f).is_file()]


def _lines(root: Path, rel: str) -> list[str]:
    return (root / rel).read_text(encoding="utf-8", errors="replace").split("\n")


def _grep(root: Path, files: list[str], pattern: re.Pattern, under: tuple[str, ...], suffix: str) -> list[tuple[str, int, str]]:
    """(file, 1-based line, text) for every tracked file under one of `under`
    (a path prefix or an exact file) with the suffix, whose line matches."""
    hits = []
    for rel in files:
        if not rel.endswith(suffix):
            continue
        if not any(u == "" or rel == u or rel.startswith(u.rstrip("/") + "/") for u in under):
            continue
        for i, text in enumerate(_lines(root, rel), 1):
            if pattern.search(text):
                hits.append((rel, i, text))
    return hits


def _files_matching(root: Path, files: list[str], pattern: re.Pattern, suffix: str, exclude_dirs: tuple[str, ...], under: str = "") -> list[str]:
    out = []
    for rel in files:
        if not rel.endswith(suffix) or (under and not rel.startswith(under)):
            continue
        parts = rel.split("/")
        if any(p in exclude_dirs for p in parts[:-1]):
            continue
        if pattern.search((root / rel).read_text(encoding="utf-8", errors="replace")):
            out.append(rel)
    return out


def _task(tid: str, corpus: str, cat: str, query: str, expected=(), tol: int = 0, source: str = "") -> dict:
    t = {"id": tid, "corpus": corpus, "category": cat, "query": query}
    if cat != "T":
        t["expected"] = [[f, n] for f, n in expected]
        t["tolerance_lines"] = tol
    t["source"] = source
    return t


def _verify_p1(root: Path, rel: str, line: int, name: str, def_re: re.Pattern) -> None:
    lines = _lines(root, rel)
    text = lines[line - 1] if 0 < line <= len(lines) else ""
    if not def_re.search(text):
        raise SystemExit(f"refused: {rel}:{line} does not define {name!r} at this SHA (line reads {text.strip()[:60]!r}); the hand-verified line moved")


# ---------------------------------------------------------------- lodash
LODASH_P1 = [("map", 9620), ("filter", 9239), ("reduce", 9745), ("debounce", 10372), ("throttle", 10965),
             ("merge", 13505), ("cloneDeep", 11155), ("get", 13194), ("set", 13741), ("chunk", 6903)]
LODASH_P4 = ["fp/_baseConvert.js", "fp/_convertBrowser.js", "fp/_mapping.js", "fp/placeholder.js", "lodash.js"]


def lodash(root: Path, corpus: str) -> tuple[list[dict], list[str]]:
    files = _tracked(root)
    tasks, notes = [], []
    for i, (name, line) in enumerate(LODASH_P1, 1):
        def_re = re.compile(rf"(function\s+{re.escape(name)}\b|var\s+{re.escape(name)}\s*=|^{re.escape(name)}:)")
        _verify_p1(root, "lodash.js", line, name, def_re)
        tasks.append(_task(f"ld-p1-{i:02d}", corpus, "P1", name, [("lodash.js", line)], 0,
                           f"{SVERKLO} lodash.gen.ts P1: hand-verified line, re-verified by the definition regex at this SHA"))
    for i, name in enumerate(LODASH_P1, 1):
        name = name[0]
        word = re.compile(rf"\b{re.escape(name)}\b")
        def_re = re.compile(rf"(function\s+{re.escape(name)}\b|var\s+{re.escape(name)}\s*=|^{re.escape(name)}:)")
        refs = [(f, n) for f, n, text in _grep(root, files, word, ("lodash.js", "fp", "lib"), ".js") if not def_re.search(text)]
        tasks.append(_task(f"ld-p2-{i:02d}", corpus, "P2", name, refs, P2_TOLERANCE,
                           f"{SVERKLO} lodash.gen.ts P2: word-grep over lodash.js, fp/, lib/ minus the definition line ({len(refs)} lines)"))
    for i, rel in enumerate(LODASH_P4, 1):
        base = re.sub(r"\.(js|ts|mjs|cjs)$", "", rel).rsplit("/", 1)[-1]
        importers = [f for f in _files_matching(root, files, re.compile(r"require.*" + re.escape(base)), ".js", ("node_modules", "test", "dist")) if f != rel]
        if not importers:
            notes.append(f"ld-p4-{i:02d} ({rel}) dropped: no importer by the rule at this SHA")
            continue
        tasks.append(_task(f"ld-p4-{i:02d}", corpus, "P4", rel, [(f, 0) for f in importers], WIDE,
                           f"{SVERKLO} lodash.gen.ts P4 importers: .js files outside node_modules/test/dist whose text matches require.*{base} ({len(importers)} files)"))
    for i, q in enumerate(T_AUTHORED["lodash"], 1):
        tasks.append(_task(f"ld-T-{i}", corpus, "T", q, source="authored:claude:2026-09-06 (a T task has no expected set; the query is a concept search over the corpus)"))
    return tasks, notes


# -------------------------------------------------------------- requests
REQUESTS_P1 = [("get", "src/requests/api.py", 62), ("options", "src/requests/api.py", 76), ("head", "src/requests/api.py", 88),
               ("post", "src/requests/api.py", 103), ("put", "src/requests/api.py", 118), ("patch", "src/requests/api.py", 133),
               ("delete", "src/requests/api.py", 148), ("request", "src/requests/api.py", 14),
               ("Session", "src/requests/sessions.py", 356), ("Response", "src/requests/models.py", 640)]
REQUESTS_P4 = ["src/requests/api.py", "src/requests/sessions.py", "src/requests/models.py", "src/requests/adapters.py", "src/requests/exceptions.py"]


def requests_(root: Path, corpus: str) -> tuple[list[dict], list[str]]:
    files = _tracked(root)
    tasks, notes = [], []
    for i, (name, rel, line) in enumerate(REQUESTS_P1, 1):
        def_re = re.compile(rf"^\s*(def|class)\s+{re.escape(name)}\b")
        _verify_p1(root, rel, line, name, def_re)
        tasks.append(_task(f"rq-p1-{i:02d}", corpus, "P1", name, [(rel, line)], 0,
                           f"{SVERKLO} requests.gen.ts P1: hand-verified line, re-verified by ^(def|class) NAME at this SHA"))
    for i, (name, _, _) in enumerate(REQUESTS_P1, 1):
        word = re.compile(rf"\b{re.escape(name)}\b")
        def_re = re.compile(rf"^\s*(def|class)\s+{re.escape(name)}\b")
        refs = [(f, n) for f, n, text in _grep(root, files, word, ("src/requests",), ".py") if not def_re.search(text)]
        tasks.append(_task(f"rq-p2-{i:02d}", corpus, "P2", name, refs, P2_TOLERANCE,
                           f"{SVERKLO} requests.gen.ts P2: word-grep over src/requests minus the def/class line ({len(refs)} lines)"))
    for i, rel in enumerate(REQUESTS_P4, 1):
        base = rel.rsplit("/", 1)[-1][:-3]
        pat = re.compile(rf"from \.\.?{re.escape(base)} import|from \.\.?[a-zA-Z_]+\.{re.escape(base)} import")
        importers = [f for f in _files_matching(root, files, pat, ".py", ("__pycache__",), under="src/requests/") if f != rel]
        if not importers:
            notes.append(f"rq-p4-{i:02d} ({rel}) dropped: no importer by the rule at this SHA")
            continue
        tasks.append(_task(f"rq-p4-{i:02d}", corpus, "P4", rel, [(f, 0) for f in importers], WIDE,
                           f"{SVERKLO} requests.gen.ts P4 importers: src/requests .py files matching `from .{base} import` or `from .pkg.{base} import` ({len(importers)} files)"))
    for i, q in enumerate(T_AUTHORED["requests"], 1):
        tasks.append(_task(f"rq-T-{i}", corpus, "T", q, source="authored:claude:2026-09-06 (a T task has no expected set; the query is a concept search over the corpus)"))
    return tasks, notes


# --------------------------------------------------------------- express
EXPRESS_P1 = ["createApplication", "Route", "Layer", "View", "query", "init", "acceptParams", "stringify", "compileETag", "merge"]
EXPRESS_P2 = ["Route", "Layer", "View", "createApplication", "compileETag", "compileQueryParser", "compileTrust", "acceptParams", "deprecate", "merge"]
EXPRESS_P4 = ["lib/express.js", "lib/application.js", "lib/router/index.js", "lib/request.js", "lib/response.js"]


def _express_def_patterns(name: str) -> list[re.Pattern]:
    e = re.escape(name)
    return [re.compile(p) for p in (
        rf"^\s*function\s+{e}\s*\(", rf"^\s*exports\.{e}\s*=", rf"^\s*module\.exports\s*=\s*function\s+{e}\b",
        rf"^\s*module\.exports\.{e}\s*=", rf"^\s*var\s+{e}\s*=\s*function", rf"^\s*{e}\s*:\s*function")]


def express(root: Path, corpus: str) -> tuple[list[dict], list[str]]:
    files = _tracked(root)
    tasks, notes = [], []
    for i, name in enumerate(EXPRESS_P1, 1):
        loc = None
        for pat in _express_def_patterns(name):
            hits = _grep(root, files, pat, ("lib", "index.js"), ".js")
            if hits:
                loc = (hits[0][0], hits[0][1])
                break
        if loc is None:
            notes.append(f"ex-p1-{i:02d} ({name}) dropped: no definition by the six patterns under lib/ or index.js at this SHA")
            continue
        tasks.append(_task(f"ex-p1-{i:02d}", corpus, "P1", name, [loc], 0,
                           f"{SVERKLO} express.gen.ts P1: first match of the definition patterns under lib/ and index.js at this SHA"))
    for i, name in enumerate(EXPRESS_P2, 1):
        word = re.compile(rf"\b{re.escape(name)}\b")
        def_re = re.compile(rf"(function\s+{re.escape(name)}\b|exports\.{re.escape(name)}\s*=|var\s+{re.escape(name)}\s*=)")
        refs = [(f, n) for f, n, text in _grep(root, files, word, ("lib", "index.js"), ".js") if not def_re.search(text)]
        if not refs:
            notes.append(f"ex-p2-{i:02d} ({name}) dropped: no reference by the rule at this SHA")
            continue
        tasks.append(_task(f"ex-p2-{i:02d}", corpus, "P2", name, refs, P2_TOLERANCE,
                           f"{SVERKLO} express.gen.ts P2: word-grep over lib/ and index.js minus the definition line ({len(refs)} lines)"))
    for i, rel in enumerate(EXPRESS_P4, 1):
        base = re.sub(r"\.(js|ts|mjs|cjs)$", "", rel).rsplit("/", 1)[-1]
        importers = [f for f in _files_matching(root, files, re.compile(r"require.*" + re.escape(base)), ".js", ("node_modules", "test")) if f != rel]
        if not importers:
            notes.append(f"ex-p4-{i:02d} ({rel}) dropped: no importer by the rule at this SHA")
            continue
        tasks.append(_task(f"ex-p4-{i:02d}", corpus, "P4", rel, [(f, 0) for f in importers], WIDE,
                           f"{SVERKLO} express.gen.ts P4 importers: .js files outside node_modules/test whose text matches require.*{base} ({len(importers)} files)"))
    for i, q in enumerate(T_QUERIES, 1):
        tasks.append(_task(f"ex-T-{i}", corpus, "T", q, source="jcm-tasks.json"))
    return tasks, notes


GENERATORS = {"lodash/lodash": lodash, "psf/requests": requests_, "expressjs/express": express}


def _check_expected(root: Path, tasks: list[dict]) -> None:
    tracked = set(_tracked(root))
    for t in tasks:
        for f, _ in t.get("expected", []):
            if f not in tracked:
                raise SystemExit(f"refused: {t['id']} expects {f!r}, not a tracked file of the checkout")


def main() -> int:
    entries = {e["repo"]: e for e in corpora_mod.load(HERE.parent / "corpora.json")}
    for repo, gen in GENERATORS.items():
        entry = entries[repo]
        root = corpora_mod.cache_dir() / entry["repo"].replace("/", "__")
        if not root.exists():
            raise SystemExit(f"refused: {repo} is not in the corpus cache; run corpora.py fetch first")
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True, encoding="utf-8").strip()
        if head != entry["sha"]:
            raise SystemExit(f"refused: {repo} cache is at {head[:7]}, corpora.json pins {entry['sha'][:7]}")
        tasks, notes = gen(root, entry["id"])
        _check_expected(root, tasks)
        short = repo.split("/")[1]
        doc = {
            "schema": "jcm-competitive-tasks/v1",
            "note": f"Generated by tasks/from_sverklo.py over {entry['id']} ({entry.get('tag', '')}); the rules are {SVERKLO}'s, the expected sets are recomputed here, never copied. Dropped: {notes or 'nothing'}.",
            "generator": "from_sverklo.py", "corpus": entry["id"], "dropped": notes, "tasks": tasks,
        }
        out = HERE / f"{short}.json"
        out.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        cats = {}
        for t in tasks:
            cats[t["category"]] = cats.get(t["category"], 0) + 1
        print(f"{out.name}: {len(tasks)} tasks {cats}; dropped {len(notes)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    os.chdir(HERE)
    sys.exit(main())
