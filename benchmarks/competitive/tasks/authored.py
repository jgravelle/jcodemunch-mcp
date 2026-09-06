"""Task generator for the corpora no third party wrote tasks for: NestJS,
Spring Framework, Angular. The symbols and files are chosen by hand; the
expected sets are computed by the same rules from_sverklo.py reproduces,
so nothing here is a typed line number.

purpose:  line-level ground truth on the three corpora corpus_check needs
          (the TypeScript monorepo bucket, the Java over-10k bucket and
          its TypeScript counterweight), in the shape sverklo-bench's rules
          give the other three
invokes:  nothing but the filesystem: the pinned checkouts in corpora.py's
          cache, `git ls-files` for the file set
produces: tasks/nest.json, tasks/spring.json, tasks/angular.json
refuses:  a chosen symbol whose definition the rule cannot find exactly
          once at the SHA (zero is a wrong choice, two is an ambiguous
          query); a corpus cache missing or at the wrong commit; a P2 or
          P4 whose expected set comes out empty
pinned:   corpora by SHA from corpora.json; the choices dated 2026-09-06
fairness: one author (the adapters' author) chose these; DESIGN s4.2's
          independence rule is not met and FINDINGS says so. The RULES are
          the third party's (P1 definition line; P2 word-grep minus the
          definition line, spec files excluded; P4 importers by an import
          statement naming the file, file-level), so the author chose WHAT
          is asked, never what the answer is. The symbols are public,
          documented entry points of each framework, the kind any user
          would look up first; none was chosen by running a tool on it.
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
from from_sverklo import P2_TOLERANCE, WIDE, _files_matching, _grep, _task, _tracked  # noqa: E402

AUTHORED = "authored:claude:2026-09-06"
TS_DEF = r"^export (abstract )?(class|function) {name}\b"
JAVA_DEF = r"^public (abstract |final )?class {name}\b"

SPECS = {
    "nestjs/nest": {
        "short": "nest", "suffix": ".ts", "def": TS_DEF, "exclude": ("node_modules",), "spec": re.compile(r"\.spec\.ts$"),
        "p1": ["NestFactoryStatic", "Reflector", "RouterExplorer"],
        "p2": [("ExceptionsHandler", "packages"), ("RouterExplorer", "packages")],
        "p4": [("packages/core/router/router-explorer.ts", r"from '[./]*router-explorer(\.js)?'", "packages"),
               ("packages/core/exceptions/exceptions-handler.ts", r"from '[./]*exceptions/exceptions-handler(\.js)?'|from '\./exceptions-handler(\.js)?'", "packages")],
        "t": ["guard interceptor pipe", "exception filter", "dependency injection module"],
    },
    "spring-projects/spring-framework": {
        "short": "spring", "suffix": ".java", "def": JAVA_DEF, "exclude": (), "spec": re.compile(r"/src/(test|testFixtures|jmh)/"),
        "p1": ["DefaultListableBeanFactory", "AnnotationConfigApplicationContext", "DispatcherServlet"],
        "p2": [("AnnotationConfigApplicationContext", "spring-context/src/main"), ("DispatcherServlet", "spring-webmvc/src/main")],
        "p4": [("spring-webmvc/src/main/java/org/springframework/web/servlet/DispatcherServlet.java", r"^import org\.springframework\.web\.servlet\.DispatcherServlet;", ""),
               ("spring-web/src/main/java/org/springframework/web/client/RestTemplate.java", r"^import org\.springframework\.web\.client\.RestTemplate;", "")],
        "t": ["bean definition registry", "transaction manager", "dispatcher servlet handler mapping"],
    },
    "angular/angular": {
        "short": "angular", "suffix": ".ts", "def": TS_DEF, "exclude": ("node_modules",), "spec": re.compile(r"\.spec\.ts$|/test/|/testing/"),
        "p1": ["HttpClient", "Injector", "Router"],
        "p2": [("Router", "packages/router/src"), ("HttpClient", "packages/common/http/src")],
        "p4": [("packages/router/src/router.ts", r"from '\./router'", "packages/router/src"),
               ("packages/core/src/di/injector.ts", r"from '[./]*di/injector'|from '\./injector'", "packages/core/src")],
        "t": ["change detection", "dependency injection injector", "router navigation guard"],
    },
}


def generate(root: Path, corpus: str, spec: dict) -> list[dict]:
    files = [f for f in _tracked(root) if not any(p in spec["exclude"] for p in f.split("/")[:-1])]
    short, sfx, is_spec = spec["short"], spec["suffix"], spec["spec"]
    tasks = []
    for i, name in enumerate(spec["p1"], 1):
        pat = re.compile(spec["def"].format(name=re.escape(name)), re.M)
        hits = [(f, n) for f, n, _ in _grep(root, files, pat, ("",), sfx) if not is_spec.search("/" + f)]
        if len(hits) != 1:
            raise SystemExit(f"refused: {corpus} P1 {name!r} defined {len(hits)} times by the rule: {hits[:3]}")
        tasks.append(_task(f"{short}-p1-{i:02d}", corpus, "P1", name, hits, 0,
                           f"{AUTHORED}, verified by reading {hits[0][0]}; expected = the one line matching {spec['def'].format(name=name)!r}"))
    for i, (name, under) in enumerate(spec["p2"], 1):
        word = re.compile(rf"\b{re.escape(name)}\b")
        def_re = re.compile(spec["def"].format(name=re.escape(name)))
        refs = [(f, n) for f, n, text in _grep(root, files, word, (under,), sfx) if not def_re.search(text) and not is_spec.search("/" + f)]
        if not refs:
            raise SystemExit(f"refused: {corpus} P2 {name!r} has no reference under {under}")
        tasks.append(_task(f"{short}-p2-{i:02d}", corpus, "P2", name, refs, P2_TOLERANCE,
                           f"{AUTHORED}; expected = word-grep over {under} ({sfx}, spec/test files excluded) minus the definition line ({len(refs)} lines)"))
    for i, (rel, imp, under) in enumerate(spec["p4"], 1):
        pat = re.compile(imp, re.M)
        importers = [f for f in _files_matching(root, files, pat, sfx, spec["exclude"], under) if f != rel and not is_spec.search("/" + f)]
        if not importers:
            raise SystemExit(f"refused: {corpus} P4 {rel} has no importer by {imp!r}")
        tasks.append(_task(f"{short}-p4-{i:02d}", corpus, "P4", rel, [(f, 0) for f in importers], WIDE,
                           f"{AUTHORED}; expected = {sfx} files under {under or 'the tree'} (spec/test excluded) with a line matching {imp!r} ({len(importers)} files)"))
    for i, q in enumerate(spec["t"], 1):
        tasks.append(_task(f"{short}-T-{i}", corpus, "T", q, source=f"{AUTHORED} (a T task has no expected set)"))
    return tasks


def main() -> int:
    entries = {e["repo"]: e for e in corpora_mod.load(HERE.parent / "corpora.json")}
    for repo, spec in SPECS.items():
        entry = entries[repo]
        root = corpora_mod.cache_dir() / repo.replace("/", "__")
        if not root.exists():
            raise SystemExit(f"refused: {repo} is not in the corpus cache; run corpora.py fetch first")
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True, encoding="utf-8").strip()
        if head != entry["sha"]:
            raise SystemExit(f"refused: {repo} cache is at {head[:7]}, corpora.json pins {entry['sha'][:7]}")
        tasks = generate(root, entry["id"], spec)
        doc = {
            "schema": "jcm-competitive-tasks/v1",
            "note": f"Generated by tasks/authored.py over {entry['id']} ({entry.get('tag', '')}): symbols and files chosen by one author on 2026-09-06, expected sets computed by from_sverklo.py's rules (P1 definition line, P2 word-grep minus the definition, P4 importers by import statement). DESIGN s4.2's independent-author rule is NOT met here; docs/competitive/FINDINGS.md records it.",
            "generator": "authored.py", "corpus": entry["id"], "tasks": tasks,
        }
        out = HERE / f"{spec['short']}.json"
        out.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        cats = {}
        for t in tasks:
            cats[t["category"]] = cats.get(t["category"], 0) + 1
        print(f"{out.name}: {len(tasks)} tasks {cats}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    os.chdir(HERE)
    sys.exit(main())
