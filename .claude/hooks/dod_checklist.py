"""The Definition-of-Done checklist, produced from evidence (DESIGN D6, section 1.1).

purpose:  every workflow ends in a checklist the AGENT did not fill in; each of
          STANDARD.md's twelve DoD items is met / unmet / n.a. with the evidence
          path, and pre_pr.py refuses a PR with an `unmet` row
invokes:  git diff against --base-ref, scripts/dod_changelog.py,
          scripts/surface_diff.py, the evidence files under
          .claude/state/evidence/, harness/thresholds.json (diff only)
produces: .claude/state/evidence/checklist.md (also printed)
refuses:  nothing; it reports

Usage: python .claude/hooks/dod_checklist.py [--base-ref origin/main] [--labels a,b] [--contributor]
       python .claude/hooks/dod_checklist.py --stamp red|green   (right after each run, #671)
The DoD text itself is read from docs/standard/STANDARD.md at run time; the
item numbers are the only thing this file knows.
"""

from __future__ import annotations

import argparse
import ast
import collections
import difflib
import hashlib
import json
import pathlib
import re
import subprocess
import sys

from _common import (
    EVIDENCE,
    REPO,
    UNREADABLE_PREFIX,
    content_tree,
    git,
    paths_for,
    tree_diff_paths,
    tree_id,
)

RATE_KEY_RE = re.compile(
    r'^\+.*["\'](\w+_(?:pct|rate|share)|confidence)["\']\s*:', re.M
)
BACKGROUND_RE = re.compile(
    r"^\+.*(?:threading\.Thread|socket\.socket|httpx\.|asyncio\.create_task|schedule)",
    re.M,
)


#: An env-var NAME as a whole string literal (a mention inside help text is not a
#: read; a trailing underscore is a prefix constant, not a name).
ENV_NAME_RE = re.compile(r"(?:JCODEMUNCH|MUNCH)_[A-Z0-9_]*[A-Z0-9]\Z")
#: Any env-var name, for the first argument of an env READ (`OPENAI_API_BASE`).
ENV_READ_ARG_RE = re.compile(r"[A-Z][A-Z0-9_]*[A-Z0-9]\Z")
#: Where DoD 6 puts the row: the two tables, plus the invariant rows the
#: 2026-08-31 split left in CLAUDE.md (table rows only, never its prose).
ROW6_DOCS = ("CONFIGURATION.md", "CLI-AND-ENV.md", "CLAUDE.md")
#: A subcommand's row is in a CLI table; a CONFIGURATION.md key row (`port`,
#: `host`, `transport`) must not document a subcommand of the same word.
ROW6_SUBCOMMAND_DOCS = ("CLI-AND-ENV.md", "CLAUDE.md")
ROW6_KINDS = ("subcommand", "option", "env")
#: A subcommand or option is COUNTED (one `--dry-run` per subcommand that
#: takes it); an env var is a name however many modules read it.
ROW6_COUNTED = ("subcommand", "option")
#: One `git grep` pattern for "a base file that could declare a name".
ROW6_CANDIDATE = r"add_parser|add_argument|environ|getenv|MUNCH_"
_BOM = chr(0xFEFF)


def _is_environ(n: ast.AST) -> bool:
    return (isinstance(n, ast.Attribute) and n.attr == "environ") or (
        isinstance(n, ast.Name) and n.id == "environ"
    )


def _env_read_arg(node: ast.AST) -> str | None:
    """The literal name an env READ asks for: `os.environ.get("X")`,
    `os.getenv("X")`, `os.environ["X"]`, `"X" in os.environ`, with or without
    the `os.` prefix. A store or a delete is not a read."""
    arg = None
    if isinstance(node, ast.Call) and node.args:
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr == "get" and _is_environ(f.value):
            arg = node.args[0]
        elif (isinstance(f, ast.Name) and f.id == "getenv") or (
            isinstance(f, ast.Attribute)
            and f.attr == "getenv"
            and isinstance(f.value, ast.Name)
            and f.value.id == "os"
        ):
            arg = node.args[0]
    elif isinstance(node, ast.Subscript) and _is_environ(node.value):
        if isinstance(node.ctx, ast.Load):
            arg = node.slice
    elif isinstance(node, ast.Compare) and len(node.ops) == 1:
        if isinstance(node.ops[0], (ast.In, ast.NotIn)) and _is_environ(
            node.comparators[0]
        ):
            arg = node.left
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value if ENV_READ_ARG_RE.match(arg.value) else None
    return None


def _is_suppress(value: ast.AST) -> bool:
    return (isinstance(value, ast.Attribute) and value.attr == "SUPPRESS") or (
        isinstance(value, ast.Name) and value.id == "SUPPRESS"
    )


def cli_env_counts(source: str) -> dict[str, collections.Counter]:
    """How many times one Python source declares each CLI subcommand, long
    option and env-var name.

    Read from the AST, so a wrapped call, a comment, a docstring or a help
    string cannot add or hide a name. An env var is a `JCODEMUNCH_*`/`MUNCH_*`
    literal, or the literal first argument of an env read whatever its prefix.
    An option argparse itself hides (`help=SUPPRESS`) is not a documented
    surface and is left out. Raises SyntaxError on a source that does not parse.
    """
    counts: dict[str, collections.Counter] = {
        k: collections.Counter() for k in ROW6_KINDS
    }
    env: set[str] = set()
    for node in ast.walk(ast.parse(source.lstrip(_BOM))):
        read = _env_read_arg(node)
        if read:
            env.add(read)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if ENV_NAME_RE.match(node.value):
                env.add(node.value)
            continue
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        literals = [
            a.value
            for a in node.args
            if isinstance(a, ast.Constant) and isinstance(a.value, str)
        ]
        if node.func.attr == "add_parser":
            counts["subcommand"].update(literals[:1])
            for k in node.keywords:
                if k.arg == "aliases" and isinstance(k.value, (ast.List, ast.Tuple)):
                    counts["subcommand"].update(
                        e.value
                        for e in k.value.elts
                        if isinstance(e, ast.Constant) and isinstance(e.value, str)
                    )
        elif node.func.attr == "add_argument":
            if not any(
                k.arg == "help" and _is_suppress(k.value) for k in node.keywords
            ):
                counts["option"].update(v for v in literals if v.startswith("--"))
    counts["env"].update(env)
    return counts


def cli_env_names(source: str) -> dict[str, set[str]]:
    """The names `cli_env_counts` finds, without their counts."""
    return {kind: set(c) for kind, c in cli_env_counts(source).items()}


def row6_tally(sources) -> tuple[dict[str, collections.Counter], list[str]]:
    """The summed counts of `sources` (a path -> text mapping), plus the paths
    that could not be parsed."""
    total: dict[str, collections.Counter] = {
        k: collections.Counter() for k in ROW6_KINDS
    }
    unparsed: list[str] = []
    for path, text in sorted(sources.items()):
        try:
            found = cli_env_counts(text)
        except (SyntaxError, ValueError):
            unparsed.append(path)
            continue
        for kind in ROW6_KINDS:
            total[kind].update(found[kind])
    return total, unparsed


def row6_grew(
    base: dict[str, collections.Counter], head: dict[str, collections.Counter]
) -> dict[str, set[str]]:
    """Names the head has MORE of than the base. A subcommand or an option is
    counted, so a second subcommand taking `--dry-run` is an addition and a
    renamed file or variable is not; an env var is a name, so a second read of
    one another module already reads is not."""
    out: dict[str, set[str]] = {}
    for kind in ROW6_KINDS:
        if kind in ROW6_COUNTED:
            out[kind] = {n for n, c in head[kind].items() if c > base[kind].get(n, 0)}
        else:
            out[kind] = set(head[kind]) - set(base[kind])
    return out


def row6_added(
    base_sources: dict[str, str], head_sources: dict[str, str]
) -> tuple[dict[str, set[str]], list[str]]:
    """What the head sources declare beyond the base sources, by kind, plus
    the head paths that could not be parsed. A base file that is absent or
    unparseable contributes nothing: everything in its head counts."""
    base, _ = row6_tally(base_sources)
    head, unparsed = row6_tally(head_sources)
    return row6_grew(base, head), unparsed


def _first_cells(text: str) -> str:
    return "\n".join(m.group(1) for m in re.finditer(r"^\|([^|\n]*)\|", text, re.M))


def _row6_documented(kind: str, name: str, docs: dict[str, str]) -> bool:
    esc = re.escape(name)
    everything = "\n".join(docs.values())
    if kind == "option":
        return bool(re.search(rf"(?<![\w-]){esc}(?![\w-])", everything))
    if kind == "env":
        # Always written in a code span in these docs; bare prose (`CI`) is not a row.
        return bool(re.search(rf"`{esc}(?![A-Za-z0-9_])", everything))
    # A subcommand is an ordinary word (`index`, `server`, `check`), so prose,
    # a module name (`server.py`), a call (`status()`) or another tool's verb
    # (`uv sync`) must not document it. It counts when it opens a code span in
    # the FIRST cell of a CLI table row (after at most one parent word; a cell
    # may hold two: `org-report` / `org-rollup`), or follows the program name.
    tail = rf"{esc}(?![\w.(/:=-])"
    cells = "\n".join(
        _first_cells(text) for d, text in docs.items() if d in ROW6_SUBCOMMAND_DOCS
    )
    return bool(
        re.search(rf"`(?:[\w-]+ )?{tail}", cells)
        or re.search(rf"jcodemunch-mcp\s+(?:[\w-]+\s+)?{tail}", everything)
    )


def row6_undocumented(added: dict[str, set[str]], docs) -> list[str]:
    """The added names no doc names, as themselves (a longer flag or variable
    that merely contains the name does not document it). `docs` is the mapping
    `row6_docs` returns; a bare string is read as `CLI-AND-ENV.md`."""
    if isinstance(docs, str):
        docs = {"CLI-AND-ENV.md": docs}
    return [
        name
        for kind in ROW6_KINDS
        for name in sorted(added.get(kind, ()))
        if not _row6_documented(kind, name, docs)
    ]


def row6_docs(repo: pathlib.Path) -> dict[str, str]:
    """The text DoD 6 accepts a row in, by file. `CLAUDE.md` contributes its
    table rows only: its prose and shell examples name `uv sync`,
    `git log --since`, `server.py` and would document almost any word."""
    docs: dict[str, str] = {}
    for d in ROW6_DOCS:
        p = repo / d
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        if d == "CLAUDE.md":
            text = "\n".join(ln for ln in text.splitlines() if ln.startswith("|"))
        docs[d] = text
    return docs


def row6_verdict(
    base_sources: dict[str, str],
    head_sources: dict[str, str],
    docs,
    tree=None,
) -> tuple[str | None, str]:
    """DoD 6 for the CLI and env half: `(None, why)` when nothing was added,
    else `met`/`unmet` with the names. A head file that cannot be parsed is
    `unmet`: what it adds could not be established.

    The changed files alone cannot tell an addition from a move (a renamed
    file has no base under its new path). `tree`, a callable returning the
    whole of `src/` as `(base_counts, head_counts)`, settles it, and is asked
    only when the changed files show a candidate (it walks both trees)."""
    added, unparsed = row6_added(base_sources, head_sources)
    if unparsed:
        return "unmet", "could not parse " + ", ".join(
            unparsed
        ) + "; what it adds is not established"
    if tree is not None and any(added.values()):
        whole = row6_grew(*tree())
        added = {kind: added[kind] & whole[kind] for kind in ROW6_KINDS}
    names = [n for kind in ROW6_KINDS for n in sorted(added[kind])]
    if not names:
        return None, (
            "no CLI subcommand or option gained a declaration and no env var is newly read"
            " under src/ (AST, base vs head)"
        )
    missing = row6_undocumented(added, docs)
    listed = ", ".join(names[:12]) + (
        f" (+{len(names) - 12} more)" if len(names) > 12 else ""
    )
    if missing:
        return "unmet", (
            f"added: {listed}; no row names: {', '.join(missing[:12])}"
            f" (looked in {', '.join(ROW6_DOCS)})"
        )
    return "met", f"added: {listed}; each is named in {' / '.join(ROW6_DOCS)}"


def _git_rc(repo: pathlib.Path, *args: str) -> tuple[int, str]:
    r = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return r.returncode, r.stdout or ""


def row6_base_counts(
    repo: pathlib.Path, merge_base: str
) -> dict[str, collections.Counter]:
    """What all of `src/` declared at the merge base, whichever file held it."""
    sources: dict[str, str] = {}
    _, listing = _git_rc(
        repo, "grep", "-l", "-E", ROW6_CANDIDATE, merge_base, "--", "src"
    )
    for entry in listing.splitlines():
        path = entry.split(":", 1)[-1]
        if not path.endswith(".py"):
            continue
        rc, text = _git_rc(repo, "show", f"{merge_base}:{path}")
        if rc == 0:
            sources[path] = text
    return row6_tally(sources)[0]


def row6_head_counts(repo: pathlib.Path) -> dict[str, collections.Counter]:
    """What all of `src/` declares in the working tree."""
    sources = {
        p.relative_to(repo).as_posix(): p.read_text(
            encoding="utf-8-sig", errors="replace"
        )
        for p in sorted((repo / "src").rglob("*.py"))
    }
    return row6_tally(sources)[0]


def row6_sources(
    changed: list[str], merge_base: str, repo: pathlib.Path
) -> tuple[dict[str, str], dict[str, str]]:
    """Base and head text of every changed Python file under src/: the base
    from the merge base (the side `git diff base...HEAD` compares against),
    the head from the working tree, which is what is about to be committed."""
    base_sources: dict[str, str] = {}
    head_sources: dict[str, str] = {}
    for path in sorted({c.replace("\\", "/") for c in changed}):
        if not (path.startswith("src/") and path.endswith(".py")):
            continue
        rc, text = _git_rc(repo, "show", f"{merge_base}:{path}")
        if rc == 0:
            base_sources[path] = text
        on_disk = repo / path
        if on_disk.exists():
            head_sources[path] = on_disk.read_text(
                encoding="utf-8-sig", errors="replace"
            )
    return base_sources, head_sources


PARITY_PATHS = ("src/jcodemunch_mcp/config.py", "src/jcodemunch_mcp/cli/")
PARITY_TESTS = (
    "tests/test_cli_env_split.py",
    "tests/test_config_docs_reverse_parity.py",
)


def row6_parity(changed: list[str], run=None) -> tuple[str | None, str]:
    """DoD 6's config-key half: the two parity tests, run when `config.py` or
    `cli/` moved; `(None, why)` otherwise. `run` is `sh` unless a test passes one."""
    if not any(c.startswith(PARITY_PATHS) for c in changed):
        return None, "config.py and cli/ untouched"
    rc, out = (run or sh)(
        "uv", "run", "pytest", *PARITY_TESTS, "-q", "-p", "no:cacheprovider"
    )
    return (
        "met" if rc == 0 else "unmet",
        "tests/test_cli_env_split.py + test_config_docs_reverse_parity.py: "
        + (out.strip().splitlines() or ["?"])[-1][:120],
    )


def row6_row(
    changed: list[str], base: str, repo: pathlib.Path, parity=None
) -> tuple[str, str]:
    """DoD 6's verdict and evidence. Two halves, and either `unmet` is `unmet`:
    what the change ADDED (W-53: a touched-path test read `n.a.` for an option
    added in server.py, where nearly every subcommand lives), and the
    config-key parity tests (`row6_parity`, or the callable a test passes).

    Staged and untracked files are graded too (W-31's rule for row 3). A base
    that cannot be resolved is `unmet`: an empty diff against a missing ref is
    not "nothing was added"."""
    rc, merge_base = _git_rc(repo, "merge-base", base, "HEAD")
    merge_base = merge_base.strip()
    if rc != 0 or not merge_base:
        added = (
            "unmet",
            f"could not resolve the merge base of {base} and HEAD; what was added is not established",
        )
    else:
        _, staged = _git_rc(repo, "diff", "--name-only", "--cached")
        _, untracked = _git_rc(
            repo, "ls-files", "--others", "--exclude-standard", "--", "src"
        )
        paths = list(changed) + staged.split() + untracked.split()
        base_sources, head_sources = row6_sources(paths, merge_base, repo)
        added = row6_verdict(
            base_sources,
            head_sources,
            row6_docs(repo),
            lambda: (row6_base_counts(repo, merge_base), row6_head_counts(repo)),
        )
    parity_verdict, parity_evidence = parity() if parity else row6_parity(changed)
    halves = [v for v in (added[0], parity_verdict) if v is not None]
    verdict = "n.a." if not halves else ("unmet" if "unmet" in halves else "met")
    return verdict, f"{added[1]}; {parity_evidence}"


def sh(*cmd: str) -> tuple[int, str]:
    r = subprocess.run(
        cmd,
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def dod_items() -> dict[int, str]:
    text = (REPO / "docs" / "standard" / "STANDARD.md").read_text(encoding="utf-8")
    sec = text.split("## Definition of Done for a change", 1)[1].split("\n## ", 1)[0]
    items = {}
    for m in re.finditer(r"^(\d+)\.\s+(.*?)(?=^\d+\.\s|\Z)", sec, re.M | re.S):
        items[int(m.group(1))] = " ".join(m.group(2).split())
    return items


def evidence(name: str) -> str | None:
    p = EVIDENCE / name
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else None


# W-38: the roots a red/green pair is REQUIRED for (this list serves row 1
# only; pre_commit.CODE_ROOTS and _common.TIER_PATHS answer other questions,
# W-43 names the three). Row 1 grades a pair that
# exists whatever the path; the roots decide only whether an absent pair is
# unmet or n.a. Three reviewers in one day graded the row by hand because
# `.claude/hooks/`, `benchmarks/` and `tests/` were not on the old list.
CODE_ROOTS = paths_for("redgreen")  # W-43: projected from _common.PATH_TABLE


def row1_verdict(
    changed: list[str], red: str | None, green: str | None
) -> tuple[str, str]:
    """Row 1: red then green. Returns (verdict, evidence)."""
    if red is None or green is None:
        if not any(c.startswith(CODE_ROOTS) for c in changed):
            return "n.a.", "no change under a code root (" + ", ".join(
                CODE_ROOTS
            ) + ") and no red/green pair"
        return (
            "unmet",
            "evidence/red.txt (touched tests at the base ref, must fail) and evidence/green.txt (at HEAD, must pass) are required",
        )
    # A red run that dies at collection says `error`, not `failed`, and
    # exits 2 (W-38 review; the remedy named EXIT=1 and EXIT=2 both).
    red_fail = "EXIT=0" not in red.splitlines()[-1:] and (
        "failed" in red.lower() or "error" in red.lower()
    )
    green_ok = "EXIT=0" in green.splitlines()[-1:] or (
        " passed" in green.lower() and "failed" not in green.lower()
    )
    return (
        "met" if red_fail and green_ok else "unmet",
        f"evidence/red.txt fails={red_fail}; evidence/green.txt passes={green_ok}",
    )


# #671: row 1 read red.txt/green.txt by path alone, so a later change on the
# same box inherited them (#669 graded `met` from #666's pair). A stamp binds a
# run to the branch, the tier-path tree it ran on (`_common.tree_id`, the
# identity pre_pr.py already holds the full-tier stamp to) and its own output.


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_stamp(
    kind: str, text: str, branch: str, tree: str, *, content: str | None = None
) -> dict:
    stamp = {"kind": kind, "branch": branch, "tree": tree, "sha256": _sha(text)}
    if content is not None:
        stamp["content_tree"] = (
            content  # #715: every tracked file, not only the code roots
        )
    return stamp


def current_branch() -> str:
    return git("rev-parse", "--abbrev-ref", "HEAD").strip()


def write_stamp(kind: str) -> int:
    """`--stamp red|green`: bind the run just written to this branch and tree."""
    text = evidence(f"{kind}.txt")
    if text is None:
        print(
            f"dod_checklist: evidence/{kind}.txt is absent; run the tests into it first",
            file=sys.stderr,
        )
        return 2
    tree = tree_id()
    if tree.startswith(UNREADABLE_PREFIX):
        # UNKNOWN is not a tree: a random id would always "differ" from green's.
        print(
            f"dod_checklist: the tree could not be read; evidence/{kind}.txt is not stamped",
            file=sys.stderr,
        )
        return 2
    stamp = make_stamp(kind, text, current_branch(), tree, content=content_tree())
    (EVIDENCE / f"{kind}.stamp.json").write_text(
        json.dumps(stamp, indent=1), encoding="utf-8"
    )
    print(
        f"stamped evidence/{kind}.txt: branch={stamp['branch']} tree={stamp['tree'][:12]}"
    )
    return 0


def _outside_code_roots_only(
    red_stamp: dict,
    green_stamp: dict,
    diff_paths,
    *,
    changed: list[str],
    content_now: str | None,
) -> tuple[list[str] | None, str]:
    """#715: equal tier trees are a genuine pair when the fix lives outside the code roots.

    A docs correction guarded by a new test has ONE tier tree for red and green
    by construction. This admits exactly that shape and nothing wider (review
    round 2 found the first draft passed a src fix whose red ran on the fixed
    code, once any unrelated doc moved before green):
    - the change touches no code root but `tests/`, so the tier tree could not
      have moved between an honest red and green;
    - green ran on the content tree that is there NOW;
    - the content trees differ, git can name what moved, nothing that moved is
      under a code root, and EVERY non-code path the branch changes moved, so
      red ran before any of them (a doc edited before red leaves red on the fix).
    Every UNKNOWN fails closed. Returns (moved paths, "") or (None, why not).
    """
    beyond_tests = [
        c for c in changed if c.startswith(CODE_ROOTS) and not c.startswith("tests/")
    ]
    if beyond_tests:
        return None, (
            f"the change touches code roots beyond tests/ ({', '.join(beyond_tests[:3])}), "
            "so red must run on a different code-root tree"
        )
    a, b = red_stamp.get("content_tree"), green_stamp.get("content_tree")
    for kind, c in (("red", a), ("green", b), ("the current", content_now)):
        if not isinstance(c, str) or not c:
            return None, f"{kind} content tree is missing; re-stamp it"
        if c.startswith(UNREADABLE_PREFIX):
            return None, f"{kind} content tree could not be read (fail closed)"
    if b != content_now:
        return (
            None,
            "evidence/green.txt ran on content that has changed since; re-run green and stamp it",
        )
    if a == b:
        return (
            None,
            "red and green ran on identical content, so red did not run on the pre-change tree",
        )
    moved = diff_paths(a, b)
    if moved is None:
        return (
            None,
            "git could not compare the red and green content trees (fail closed)",
        )
    if not moved:
        return (
            None,
            "git names no path between the red and green content trees (fail closed)",
        )
    inside = [p for p in moved if p.startswith(CODE_ROOTS)]
    if inside:
        return None, (
            f"the content trees differ under a code root the tier tree did not see: {inside[:3]}. "
            "The usual cause is a new test that was untracked at red; `git add` it before "
            "running red, then re-run red and green"
        )
    stray = [p for p in moved if p not in changed]
    if stray:
        return (
            None,
            f"{', '.join(stray[:3])} moved between red and green but is not part of this change",
        )
    unmoved = [c for c in changed if not c.startswith(CODE_ROOTS) and c not in moved]
    if unmoved:
        return None, (
            f"{', '.join(unmoved[:3])} changed on the branch but not between red and green, "
            "so red ran after part of the change"
        )
    return moved, ""


def row1_binding(
    red: str,
    green: str,
    red_stamp: dict | None,
    green_stamp: dict | None,
    *,
    branch: str,
    tree: str,
    diff_paths=tree_diff_paths,
    changed: list[str] = (),
    content_now: str | None = None,
) -> str | None:
    """None when the pair belongs to this change; otherwise why it does not."""
    if red_stamp is None or green_stamp is None:
        missing = [
            k for k, s in (("red", red_stamp), ("green", green_stamp)) if s is None
        ]
        return (
            f"evidence/{'/'.join(missing)} not stamped; run "
            "`python .claude/hooks/dod_checklist.py --stamp red|green` right after each run"
        )
    for kind, text, stamp in (("red", red, red_stamp), ("green", green, green_stamp)):
        if stamp.get("branch") != branch:
            return f"evidence/{kind}.txt was stamped on branch {stamp.get('branch')!r}, not {branch!r}"
        if stamp.get("sha256") != _sha(text):
            return f"evidence/{kind}.txt changed after it was stamped"
    for kind, t in (
        ("red", red_stamp.get("tree")),
        ("green", green_stamp.get("tree")),
        ("the current", tree),
    ):
        if not isinstance(t, str) or not t or t.startswith(UNREADABLE_PREFIX):
            return f"{kind} tree could not be read, so the pair cannot be bound (fail closed)"
    if green_stamp.get("tree") != tree:
        return (
            f"evidence/green.txt ran on tree {str(green_stamp.get('tree'))[:12]}, "
            f"the tree now is {tree[:12]}; re-run green and stamp it"
        )
    if red_stamp.get("tree") == green_stamp.get("tree"):
        moved, why = _outside_code_roots_only(
            red_stamp,
            green_stamp,
            diff_paths,
            changed=list(changed),
            content_now=content_now,
        )
        if moved is None:
            return f"evidence/red.txt ran on the same code-root tree as green.txt, and {why}"
    return None


def row1(
    changed: list[str],
    red: str | None,
    green: str | None,
    red_stamp: dict | None,
    green_stamp: dict | None,
    *,
    branch: str,
    tree: str,
    diff_paths=tree_diff_paths,
    content_now: str | None = None,
) -> tuple[str, str]:
    """Row 1 with the pair held to this change, then graded by row1_verdict."""
    if red is None or green is None:
        return row1_verdict(changed, red, green)
    why = row1_binding(
        red,
        green,
        red_stamp,
        green_stamp,
        branch=branch,
        tree=tree,
        diff_paths=diff_paths,
        changed=changed,
        content_now=content_now,
    )
    if why is None:
        verdict, ev = row1_verdict(changed, red, green)
        if red_stamp.get("tree") == green_stamp.get("tree"):
            moved, _ = _outside_code_roots_only(
                red_stamp,
                green_stamp,
                diff_paths,
                changed=changed,
                content_now=content_now,
            )
            shown = ", ".join((moved or [])[:5]) + (
                f" (+{len(moved) - 5} more)" if moved and len(moved) > 5 else ""
            )
            ev += f"; red and green share a code-root tree and differ only outside it (#715): {shown}"
        return verdict, ev
    if not any(c.startswith(CODE_ROOTS) for c in changed):
        return (
            "n.a.",
            f"no change under a code root; the red/green pair on disk is not this change's ({why})",
        )
    return "unmet", why


def _stamp_json(name: str) -> dict | None:
    text = evidence(name)
    if text is None:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def harness_pass(summary: str | None) -> bool | None:
    if summary is None:
        return None
    return "**FAIL**" not in summary and "HARNESS FAIL" not in summary


#: How similar two bodies must be for a removal + addition to read as a RENAME.
#:
#: ⚠⚠ **A DELIBERATE BIAS TOWARD REPORTING, not a margin.** The first version
#: of this comment claimed 0.75 "sits between them with margin on both sides" on
#: the strength of two samples -- `b9dfcb19`, the one retirement in
#: `harness/retired.json`, at **0.571**, and #753's rename (`880a9061`) at
#: **0.851**. A sweep of the last 800 commits touching `tests/` falsifies that:
#: **499 removal/addition pairs reach this rule, only 6 clear 0.75, and 58 sit
#: inside the 0.571-0.851 band.** The rename class straddles the line -- genuine
#: renames measured at 0.735, 0.696 and 0.689 -- so there is margin against the
#: two samples and NONE against the class.
#:
#: ⚠⚠ **Those are PRODUCTION-PATH figures and the first version's were not.**
#: `name not in moved` runs BEFORE `_is_a_rename`, so a name re-added anywhere in
#: the diff never reaches the body comparison. Scoring those 16 pairs too gave
#: 517 / 15 / 64 and told a reader the constant excludes 15 of 517 when on the
#: path it governs it excludes 6 of 499 -- the near-identical bodies of re-added
#: names are what doubled the count. Found in review. **A measurement quoted to
#: justify a rule has to be taken on the path that rule runs on.**
#:
#: ⚠⚠ It stays at 0.75 anyway, and the reason is the direction of the error.
#: A missed retirement loses the lesson permanently and silently; a reported
#: rename costs a human one look. **The cost is ledger NOISE, not blocked work**:
#: an `unmet` row has `pre_pr.py` refuse the PR, and the cheapest escape is a
#: ledger entry for a rename, which `tests/test_retirement_ledger.py` accepts
#: because the old name is genuinely gone. So the bias dilutes the ledger rather
#: than stopping anyone.
#:
#: ⚠ `test_a_rename_below_the_threshold_is_an_accepted_false_positive` pins one
#: of the measured sub-0.75 renames, so **lowering this constant to "fix" a
#: false positive fails a test that explains why it is accepted.** Raising or
#: lowering it needs a re-run of the sweep, not a nudge against one case.
_RENAME_BODY_SIMILARITY = 0.75


def ledger_uncovered(subjects: list[str], repo: pathlib.Path) -> list[str]:
    """Which retirements have no `harness/retired.json` entry of their own.

    ⚠⚠ **A check that the ledger FILE was touched is not a check that what it
    must record is recorded.** Row 11 asked only whether `harness/retired.json`
    appeared in the diff, so a PR retiring three test functions and filing ONE
    entry graded `met` -- which happened on the very PR that taught the row to
    detect retired functions at all, and was found by reading the row's own
    evidence string rather than by any test.

    ⚠ A DELETED FILE is covered by an entry for the file OR for any test inside
    it (`path` is `file` or `file::name`), because both spellings are legal in
    the ledger's own note and a file entry is the stronger claim.

    ⚠ Unreadable or malformed ledger returns every subject as uncovered: an
    unparseable ledger records nothing, and UNKNOWN blocks.
    """
    try:
        entries = json.loads(
            (repo / "harness" / "retired.json").read_text(
                encoding="utf-8", errors="replace"
            )
        )["retired"]
        recorded = {str(e.get("path", "")) for e in entries}
    except Exception:
        return list(subjects)
    return [
        s
        for s in subjects
        if s not in recorded and not any(p.startswith(s + "::") for p in recorded)
    ]


def retired_test_functions(diff: str, repo: pathlib.Path) -> list[str]:
    r"""Test FUNCTIONS removed and not redefined, keyed `file::name`.

    ⚠⚠ **A file-granular check could not see a retirement.** DoD 11 and
    `harness/retired.json`'s own note are explicit that the unit is
    `file::test_name`, and this item read `--diff-filter=D`, which lists
    deleted FILES only -- so a PR deleting two test functions from a file
    that survives was graded `n.a.`, and the item was never evaluated by the
    machine at all. Found in review of #748/#749, which deleted exactly two.

    ⚠⚠ **A NAME IS NOT AN IDENTITY, and the first version of this keyed on
    one.** It concatenated every file under `tests/` and asked whether
    `def <name>(` appeared anywhere, so retiring one of the **128 test names
    that are defined in more than one file** (`test_empty` in 8,
    `test_idempotent` in 6) was excluded as a rename and the ledger was
    never demanded -- the exact grade this detector exists to stop. That is
    `tests/test_key_files_split.py`'s own recorded bug, which collapsed
    `runtime/redact.py` with `redact.py` by basename, reproduced inside a
    guard written against a neighbouring miss. Keyed `file::name` now.

    ⚠⚠ **A rename is told from a retirement by the BODY, and a file-level
    rule was measured wrong.** The first version excluded every removal in a
    file that gained any test function, which silenced `b9dfcb19` -- the ONLY
    prior retirement in `harness/retired.json` -- because its replacement was
    added to the same file beside eleven other new tests. The ledger's schema
    makes that the normal shape, not an edge case: entry 0's `path` and
    `replacement` name one file. A rename keeps the body; a replacement
    rewrites it, so the bodies are compared against
    `_RENAME_BODY_SIMILARITY`, which is measured on both real cases in this
    repo's history rather than chosen.

    ⚠ A name added to ANOTHER file in the same diff is a move, not a
    retirement, and is excluded by name.

    ⚠ `REPO / "tests"`, never `Path("tests")`: every other filesystem and
    git read here is anchored because a hook's CWD is not the repo root.
    Unanchored, `rglob` yields nothing, every removed name reads as retired,
    and `pre_pr.py` refuses the PR on an ordinary rename.

    ⚠ A DEFINITION, not a mention. The survival scan matches
    `^\s*(async\s+)?def <name>\b` per file, the pattern
    `tests/test_retirement_ledger.py` already uses. A raw substring scan is
    answered by a test that merely NAMES the retired function -- and this
    very PR ships one, `test_the_retired_gap_tests_are_gone_from_the_short_function_file`,
    which asserts on the literal `"def test_a_macro_is_a_known_separate_gap"`.
    It was saved only by the absent `(`.
    """
    removed: dict[tuple[str, str], tuple[str, ...]] = {}
    added: dict[tuple[str, str], tuple[str, ...]] = {}
    current = ""
    side = ""
    block_name = ""
    block: list[str] = []

    def _flush() -> None:
        if block_name:
            target = removed if side == "-" else added
            target[(current, block_name)] = tuple(block)

    for line in diff.splitlines():
        # ⚠ A file header is recognised by its FULL prefix, never by a leading
        # `-`: a removed body line reading `--- separator ---` at column 0 would
        # otherwise flush the block early and lower that body's similarity
        # score, which biases a real rename toward reading as a retirement.
        # Latent (no test file here has such a line) and cheap to close.
        if line.startswith("+++ b/") or line.startswith("--- a/"):
            _flush()
            side, block_name, block = "", "", []
            # ⚠⚠ BOTH headers set the path, and reading only `+++ b/` misattributed
            # every test in a DELETED file: git emits `+++ /dev/null` there, so
            # `current` kept the previous file and the removed tests were keyed to
            # it -- measured, `tests/test_b.py`'s test reported as
            # `tests/test_a.py::test_from_the_deleted_file`. That feeds the wrong
            # file to the survival check AND to the rename comparison. A modified
            # file and a rename both overwrite this from `+++ b/` on the next line.
            current = line[len("+++ b/") :].strip()
            continue
        if line.startswith("@@"):
            _flush()
            side, block_name, block = "", "", []
            continue
        if not current or line[:1] not in ("-", "+"):
            _flush()
            side, block_name, block = "", "", []
            continue
        if line[:1] != side:
            _flush()
            side, block_name, block = line[:1], "", []
        text = line[1:]
        m = re.match(r"\s*(?:async\s+)?def (test_\w+)", text)
        if m:
            _flush()
            block_name, block = m.group(1), []
        elif block_name:
            stripped = text.strip()
            if stripped:
                block.append(stripped)
    _flush()

    if not removed:
        return []

    def _defined_in(path: str, name: str) -> bool:
        file_path = repo / path
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        return bool(re.search(rf"^\s*(?:async\s+)?def {re.escape(name)}\b", text, re.M))

    def _is_a_rename(path: str, body: tuple[str, ...]) -> bool:
        """Did some function ADDED to this file keep this one's body?

        ⚠⚠ **The body is the discriminator, and a file-level one was measured
        WRONG.** Excluding every removal in a file that gained any test silenced
        `b9dfcb19` -- the only prior retirement in `harness/retired.json` --
        because its replacement went into the SAME file beside eleven other new
        tests. The ledger's own schema makes that the normal shape: entry 0's
        `path` and `replacement` name one file. So the file-level rule missed
        1 of 1 historical retirements, and this PR was caught only because its
        replacements happened to go in a new file.

        ⚠ A rename keeps the body; a replacement rewrites it.
        `_RENAME_BODY_SIMILARITY` carries the calibration and the two
        measurements behind it.
        """
        if not body:
            return False
        for (added_path, _name), added_body in added.items():
            if added_path != path or not added_body:
                continue
            if (
                difflib.SequenceMatcher(
                    None, "\n".join(body), "\n".join(added_body)
                ).ratio()
                >= _RENAME_BODY_SIMILARITY
            ):
                return True
        return False

    moved = {name for _path, name in added}
    return sorted(
        f"{path}::{name}"
        for (path, name), body in removed.items()
        if not _defined_in(path, name)
        and name not in moved
        and not _is_a_rename(path, body)
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-ref", default="origin/main")
    ap.add_argument("--labels", default="")
    ap.add_argument("--contributor", action="store_true")
    ap.add_argument("--stamp", choices=("red", "green"), default=None)
    a = ap.parse_args()
    if a.stamp:
        return write_stamp(a.stamp)
    labels = {s.strip() for s in a.labels.split(",") if s.strip()}
    base = a.base_ref
    changed = (
        git("diff", "--name-only", f"{base}...HEAD").split()
        + git("diff", "--name-only").split()
    )
    changed = sorted(set(changed))
    # DoD 7 and 8 are about PRODUCT behaviour: scan additions under src/ only,
    # or a doc that MENTIONS httpx or a schedule trips them (first run on the
    # workflows layer itself).
    src_diff = git("diff", f"{base}...HEAD", "--", "src/") + git("diff", "--", "src/")
    touched = lambda *pre: any(c.startswith(pre) for c in changed)  # noqa: E731
    src_changed = touched("src/")
    deleted_test_files = [
        f
        for f in git(
            "diff", "--name-only", "--diff-filter=D", f"{base}...HEAD", "--", "tests/"
        ).splitlines()
        if f.strip()
    ]

    retired_functions = retired_test_functions(
        git("diff", "-U0", f"{base}...HEAD", "--", "tests/"), REPO
    )
    tests_deleted = bool(deleted_test_files) or bool(retired_functions)

    rows: list[tuple[int, str, str]] = []

    def row(n: int, verdict: str, ev: str) -> None:
        rows.append((n, verdict, ev))

    # 1 red then green
    red, green = evidence("red.txt"), evidence("green.txt")
    if red is not None and green is not None:
        row(
            1,
            *row1(  # W-38, #671
                changed,
                red,
                green,
                _stamp_json("red.stamp.json"),
                _stamp_json("green.stamp.json"),
                branch=current_branch(),
                tree=tree_id(),
                content_now=content_tree(),
            ),
        )
    else:
        row(1, *row1_verdict(changed, red, green))  # W-38

    # 2 fast tier (ruff inside), touched files, full tier with skip verdicts
    fast, full = evidence("fast.md"), evidence("full.md")
    fp, fu = harness_pass(fast), harness_pass(full)
    if fp is None or fu is None:
        row(
            2,
            "unmet",
            "evidence/fast.md and evidence/full.md (harness --summary) are required",
        )
    else:
        row(
            2,
            "met" if fp and fu else "unmet",
            f"fast tier pass={fp}; full tier pass={fu} (skip ceilings are verdict rows inside)",
        )

    # 3 changelog
    if "no-changelog" in labels:
        row(3, "n.a.", "label no-changelog")
    elif not src_changed:
        # W-38 review: widening this gate is inert, because the authority it
        # routes to (scripts/dod_changelog.py) requires an entry for src/ only;
        # a wider gate here would read `met` with no entry (#508's shape).
        row(3, "n.a.", "no change under src/")
    elif (
        git("diff", "--name-only", "--", "src/").strip()
        or git("diff", "--cached", "--name-only", "--", "src/").strip()
    ):
        # scripts/dod_changelog.py diffs base...HEAD; an UNCOMMITTED src/ edit is
        # invisible to it and the row would read met with no entry (W-31).
        row(
            3,
            "unmet",
            "src/ has uncommitted changes; commit them, then re-run (dod_changelog reads the committed diff)",
        )
    else:
        rc, out = sh(
            sys.executable,
            "scripts/dod_changelog.py",
            "--base-ref",
            base,
            "--labels",
            a.labels,
        )
        row(
            3,
            "met" if rc == 0 else "unmet",
            "scripts/dod_changelog.py: "
            + (out.strip().splitlines() or ["?"])[-1][:160],
        )

    # 4 tool surface
    has_desc = "--descriptions" in (REPO / "scripts" / "surface_diff.py").read_text(
        encoding="utf-8"
    )
    rc, out = sh(
        "uv",
        "run",
        "python",
        "scripts/surface_diff.py",
        *(["--descriptions"] if has_desc else []),
        "--base-ref",
        base,
    )
    desc_changed = "description changed:" in out
    if rc == 0 and "no surface change" in out and not desc_changed:
        row(
            4,
            "n.a.",
            "surface unchanged (names"
            + (
                " and descriptions"
                if has_desc
                else "; descriptions not diffed, FINDINGS W-1"
            )
            + " via scripts/surface_diff.py)",
        )
    else:
        docs = all(
            any(c == f for c in changed) for f in ("README.md", "CHANGELOG.md")
        ) and any(c in ("CLAUDE.md", "KEY-FILES.md") for c in changed)
        base_touched = "benchmarks/schema_baseline.json" in changed
        row(
            4,
            "met" if rc == 0 and docs and base_touched else "unmet",
            f"surface_diff rc={rc} desc_changed={desc_changed}; README+CHANGELOG+CLAUDE/KEY-FILES changed={docs}; schema_baseline changed={base_touched}",
        )

    # 5 benchmark mirrors
    if not touched("benchmarks/"):
        row(5, "n.a.", "benchmarks/ untouched")
    else:
        rc, out = sh(
            "uv",
            "run",
            "pytest",
            "tests/test_provenance.py",
            "tests/test_schema_budget.py",
            "-q",
            "-p",
            "no:cacheprovider",
        )
        row(
            5,
            "met" if rc == 0 else "unmet",
            "tests/test_provenance.py + test_schema_budget.py: "
            + (out.strip().splitlines() or ["?"])[-1][:120],
        )

    # 6 config/env/CLI rows: what the change added, and the config-key
    # parity tests when their files moved (row6_row combines the two).
    row(6, *row6_row(changed, base, REPO))

    # 7 background behaviour disclosure
    if not BACKGROUND_RE.search(src_diff):
        row(7, "n.a.", "no thread/socket/http/scheduled addition in the diff")
    else:
        readme_diff = git("diff", f"{base}...HEAD", "--", "README.md")
        row(
            7,
            "met" if "Background behavior" in readme_diff else "unmet",
            "diff adds a thread/socket/http/schedule; README 'Background behavior, fully disclosed' must change",
        )

    # 8 *_basis beside a published rate
    rates = RATE_KEY_RE.findall(src_diff)
    if not rates:
        row(8, "n.a.", "no new rate/share/confidence key in the diff")
    else:
        basis = "_basis" in src_diff or "refus" in src_diff
        row(
            8,
            "met" if basis else "unmet",
            f"new keys {sorted(set(rates))[:5]}; a *_basis sibling or a refusal path must appear in the diff",
        )

    # 9 contributor PR
    if not a.contributor:
        row(9, "n.a.", "our own PR")
    else:
        tm = evidence("trial_merge.txt")
        cla = evidence("cla.txt")
        row(
            9,
            "met"
            if tm and "EXIT=0" in tm and cla and "count=0" not in cla
            else "unmet",
            "evidence/trial_merge.txt (fast tier on the trial merge) and evidence/cla.txt (status count on the head SHA)",
        )

    # 10 fast; bench when benchmarks/, harness/ or server.py changed
    needs_bench = touched(*paths_for("bench"))  # W-43: the table's bench column
    bench = evidence("bench.md")
    bp = harness_pass(bench)
    if fp is None:
        row(10, "unmet", "evidence/fast.md required")
    elif needs_bench and bp is None:
        row(
            10,
            "unmet",
            "benchmarks/, harness/ or server.py changed: evidence/bench.md (harness bench --offline --summary) required",
        )
    else:
        row(
            10,
            "met" if fp and (bp is not False) else "unmet",
            f"fast pass={fp}; bench pass={bp if needs_bench else 'not required'}",
        )

    # 11 retired test ledger
    if not tests_deleted:
        row(11, "n.a.", "no test file deleted and no test function retired")
    else:
        rc, out = sh(
            "uv",
            "run",
            "pytest",
            "tests/test_retirement_ledger.py",
            "-q",
            "-p",
            "no:cacheprovider",
        )
        subject = ", ".join(deleted_test_files + retired_functions) or "(none)"
        uncovered = ledger_uncovered(deleted_test_files + retired_functions, REPO)
        row(
            11,
            "met"
            if rc == 0 and "harness/retired.json" in changed and not uncovered
            else "unmet",
            f"retired: {subject}; ledger test rc={rc}; harness/retired.json "
            f"changed={'harness/retired.json' in changed}; "
            f"without a ledger entry: {', '.join(uncovered) or '(none)'}",
        )

    # 12 threshold moved
    if "harness/thresholds.json" not in changed:
        row(12, "n.a.", "harness/thresholds.json untouched")
    else:
        try:
            old = json.loads(git("show", f"{base}:harness/thresholds.json"))[
                "thresholds"
            ]
            new = json.loads(
                (REPO / "harness" / "thresholds.json").read_text(encoding="utf-8")
            )["thresholds"]
        except (json.JSONDecodeError, KeyError):
            old, new = [], []
        oldmap = {e["id"]: e for e in old}
        bad = []
        for e in new:
            o = oldmap.get(e["id"])
            if o and o.get("floor") != e.get("floor"):
                hist_ok = len(e.get("history", [])) > len(o.get("history", []))
                if not (hist_ok or e.get("loosened")):
                    bad.append(e["id"])
        row(
            12,
            "met" if not bad else "unmet",
            "moved floors without history/loosened: " + (", ".join(bad) or "none"),
        )

    items = dod_items()
    out_lines = ["| DoD | item | verdict | evidence |", "|---|---|---|---|"]
    for n, verdict, ev in rows:
        out_lines.append(f"| {n} | {items.get(n, '?')[:90]} | {verdict} | {ev} |")
    unmet = [n for n, v, _ in rows if v == "unmet"]
    out_lines.append("")
    out_lines.append(
        f"Checklist: {len(rows) - len(unmet)}/{len(rows)} met or n.a.; unmet: {unmet or 'none'}. Generated by .claude/hooks/dod_checklist.py against {base}."
    )
    text = "\n".join(out_lines) + "\n"
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / "checklist.md").write_text(text, encoding="utf-8")
    print(text)
    return 1 if unmet else 0


if __name__ == "__main__":
    sys.exit(main())
