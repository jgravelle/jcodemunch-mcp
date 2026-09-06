"""The corpus fairness check (docs/competitive/DESIGN.md s3.3; the brief's
Phase 3 item 3): is the SET of corpora one a comparison can be fair on?

purpose:  fail a run before scoring when the corpus set is the shape symbol
          search flatters (one language, one domain, small modular repos)
invokes:  nothing but the filesystem: the shared file set of each corpus
          (adapter.Corpus.files), its line counts, its extension, and the
          `domain` its corpora.json entry declares
produces: a list of problems (empty = pass) and a verdict dict the result
          header records, criterion by criterion, so a reader sees WHICH
          rule a set met and by how much
refuses:  a policy file without every threshold (no default lives here:
          DESIGN says each threshold is written once and read from the file)
fairness: judged over the set, never one corpus; the three pinned by
          benchmarks/tasks.json fail (a)-(d) alone and stay (DESIGN s3.4);
          the additional set is what makes the set pass
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
POLICY = HERE / "corpus_policy.json"
KEYS = ("min_languages", "language_min_share", "big_file_min_lines", "big_corpus_min_files", "max_language_share", "same_domain_is_failure")

# Code-file extensions and the language they count for; anything else is
# "other" and outside the share denominators (a README is not a language).
LANGUAGES = {
    "py": "python", "pyi": "python",
    "js": "javascript", "mjs": "javascript", "cjs": "javascript", "jsx": "javascript",
    "ts": "typescript", "tsx": "typescript", "mts": "typescript", "cts": "typescript",
    "go": "go", "java": "java", "kt": "kotlin", "kts": "kotlin", "rs": "rust", "rb": "ruby", "php": "php",
    "cs": "csharp", "c": "c", "h": "c", "cpp": "cpp", "cc": "cpp", "hpp": "cpp", "swift": "swift", "scala": "scala",
    "dart": "dart", "ex": "elixir", "exs": "elixir", "erl": "erlang", "sql": "sql", "sh": "shell",
}


def load_policy(path: Path = POLICY) -> dict:
    doc = json.loads(path.read_text(encoding="utf-8"))
    missing = [k for k in KEYS if k not in doc]
    if missing:
        raise SystemExit(f"refused: corpus_policy.json lacks {missing}")
    return doc


def _language(rel: str) -> str | None:
    name = rel.rsplit("/", 1)[-1]
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return LANGUAGES.get(ext)


def describe(cid: str, root: Path, files: tuple[str, ...] | list[str], domain: str) -> dict:
    """What the check reads about one corpus: code files by language, the
    longest file in lines, the file count, the declared domain."""
    langs: Counter = Counter()
    longest = ("", 0)
    for rel in files:
        lang = _language(rel)
        if not lang:
            continue  # (b) is about a code file (the lodash shape); prose and data are not counted or read
        langs[lang] += 1
        try:
            with open(root / rel, "rb") as fh:
                n = fh.read().count(b"\n")
        except OSError:
            continue
        if n > longest[1]:
            longest = (rel, n)
    return {"id": cid, "files": len(files), "languages": dict(langs), "longest_file": longest[0], "longest_lines": longest[1], "domain": domain}


def check(descriptions: list[dict], policy: dict) -> tuple[list[str], dict]:
    """(problems, verdict). Each criterion is judged over the set and its
    measurement is recorded whether it passed or not."""
    total: Counter = Counter()
    for d in descriptions:
        total.update(d["languages"])
    n_code = sum(total.values()) or 1
    shares = {lang: c / n_code for lang, c in total.items()}
    counted = sorted(lang for lang, s in shares.items() if s >= policy["language_min_share"])
    biggest_file = max(descriptions, key=lambda d: d["longest_lines"], default=None)
    biggest_corpus = max(descriptions, key=lambda d: d["files"], default=None)
    domains = Counter(d["domain"] for d in descriptions)
    top_lang = max(shares.items(), key=lambda kv: kv[1], default=(None, 0.0))
    problems = []
    if len(counted) < policy["min_languages"]:
        problems.append(f"(a) {len(counted)} language(s) at or above {policy['language_min_share']:.0%} of the set's code files ({', '.join(counted) or 'none'}); the policy needs {policy['min_languages']}")
    if biggest_file is None or biggest_file["longest_lines"] < policy["big_file_min_lines"]:
        problems.append(f"(b) no corpus has a file of {policy['big_file_min_lines']:,} lines; the longest is {biggest_file['longest_file'] if biggest_file else 'none'} at {biggest_file['longest_lines'] if biggest_file else 0:,}")
    if biggest_corpus is None or biggest_corpus["files"] <= policy["big_corpus_min_files"]:
        problems.append(f"(c) no corpus has more than {policy['big_corpus_min_files']:,} files; the largest is {biggest_corpus['id'] if biggest_corpus else 'none'} at {biggest_corpus['files'] if biggest_corpus else 0:,}")
    if policy["same_domain_is_failure"] and len(domains) == 1 and descriptions:
        problems.append(f"(d) every corpus is the same domain by its own description: {next(iter(domains))!r}")
    if top_lang[1] > policy["max_language_share"]:
        problems.append(f"(e) {top_lang[0]} is {top_lang[1]:.0%} of the set's code files; the policy caps a language at {policy['max_language_share']:.0%}")
    verdict = {
        "policy": {k: policy[k] for k in KEYS},
        "languages_counted": counted, "language_shares": {k: round(v, 4) for k, v in sorted(shares.items())},
        "longest_file": {"corpus": biggest_file["id"], "file": biggest_file["longest_file"], "lines": biggest_file["longest_lines"]} if biggest_file else None,
        "largest_corpus": {"corpus": biggest_corpus["id"], "files": biggest_corpus["files"]} if biggest_corpus else None,
        "domains": dict(domains), "problems": problems, "ok": not problems,
    }
    return problems, verdict


def main(argv: list[str] | None = None) -> int:
    """`python corpus_check.py 'ID=PATH|DOMAIN' ...` judges local checkouts (a `|`, because a
    Windows path carries a colon);
    the shared file set is `git ls-files`, like run.py's."""
    import subprocess

    argv = sys.argv[1:] if argv is None else argv
    descs = []
    for spec in argv:
        cid, _, rest = spec.partition("=")
        path, _, domain = rest.partition("|")
        root = Path(path).resolve()
        files = [f for f in subprocess.check_output(["git", "ls-files"], cwd=root, text=True, encoding="utf-8").split("\n") if f and (root / f).is_file()]
        descs.append(describe(cid, root, files, domain or "unknown"))
    problems, verdict = check(descs, load_policy())
    print(json.dumps(verdict, indent=1))
    if problems:
        print("corpus check FAILED:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 1
    print("corpus check ok", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
