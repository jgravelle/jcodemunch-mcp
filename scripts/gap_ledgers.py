"""The arrival rule for tracked-gap ledgers, and the manifest that backs it (#758).

A gap ledger is a module-level `_*GAPS` literal in a test file (`_KNOWN_GAPS`,
`_CONFIRMED_GAPS`, `_GAPS`). Each entry removes a form from a check, so each
must cite an issue that EXISTS and is OPEN. `tests/test_gap_ledgers_cite_open_issues.py`
asks `check()`; this script's `--refresh` rewrites the offline manifest the
check reads, so the suite never touches the network.

    uv run python scripts/gap_ledgers.py            # print what check() reports
    uv run python scripts/gap_ledgers.py --refresh  # rewrite the manifest via gh

⚠ Ledgers are read with `ast.literal_eval`, never by importing a test module:
an import runs module code and fixtures, and a computed ledger cannot be
checked at all, so it is REPORTED rather than skipped.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "tests" / "fixtures" / "gap_ledger_issues.json"
REPO = "jgravelle/jcodemunch-mcp"

_LEDGER_NAME = re.compile(r"^_[A-Z_]*GAPS$")
_ISSUE = re.compile(r"#(\d+)\b")
_NOT_A_LITERAL = object()


def find_ledgers(tests_dir: Path):
    """Yield `(path, name, line, value)` for every module-level ledger.

    `value` is the literal, or `_NOT_A_LITERAL` when the assignment is computed.
    """
    for path in sorted(tests_dir.rglob("test_*.py")):
        if "fixtures" in path.relative_to(tests_dir).parts:
            continue
        try:
            # Another file's `"\d"` docstring is not this scan's business.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for node in tree.body:
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets, value = [node.target], node.value
            else:
                continue
            for target in targets:
                if isinstance(target, ast.Name) and _LEDGER_NAME.match(target.id):
                    try:
                        literal = ast.literal_eval(value)
                    except ValueError:
                        literal = _NOT_A_LITERAL
                    yield path, target.id, node.lineno, literal


def entries(value, path=()):
    """Yield `(path, reason_text)` for every entry of a ledger literal.

    An entry is a string, or a tuple whose strings are read together (a
    `(form, reason)` pair); dicts and lists are walked.
    """
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _entry_or_walk(item, path + (key,))
    elif isinstance(value, (list, set, frozenset)):
        for item in value:
            yield from _entry_or_walk(item, path)


def _entry_or_walk(item, path):
    if isinstance(item, str):
        yield path, item
    elif isinstance(item, tuple):
        yield path, " ".join(part for part in item if isinstance(part, str))
    else:
        yield from entries(item, path)


def cited(text: str) -> set[int]:
    return {int(n) for n in _ISSUE.findall(text)}


def cited_issues(tests_dir: Path) -> set[int]:
    return {
        number
        for _path, _name, _line, value in find_ledgers(tests_dir)
        if value is not _NOT_A_LITERAL
        for _key, text in entries(value)
        for number in cited(text)
    }


def check(tests_dir: Path, manifest: dict[int, str]) -> list[str]:
    """Every entry that does not cite an issue the manifest records as OPEN."""
    problems = []
    for path, name, line, value in find_ledgers(tests_dir):
        where = f"{path.name}:{line} {name}"
        if value is _NOT_A_LITERAL:
            problems.append(
                f"{where} is not a literal, so its entries cannot be checked"
            )
            continue
        for key, text in entries(value):
            label = f"{where}{list(key)!r}"
            numbers = cited(text)
            if not numbers:
                problems.append(f"{label} names no issue: {text!r}")
                continue
            if any(manifest.get(n) == "OPEN" for n in numbers):
                continue
            reasons = [
                f"#{n} is not in the manifest (run scripts/gap_ledgers.py --refresh)"
                if n not in manifest
                else f"#{n} is {manifest[n]}"
                for n in sorted(numbers)
            ]
            problems.append(f"{label} cites no OPEN issue: {'; '.join(reasons)}")
    return problems


def _state(number: int) -> str:
    env = dict(os.environ, GITHUB_TOKEN="")
    out = subprocess.run(
        [
            "gh",
            "issue",
            "view",
            str(number),
            "--repo",
            REPO,
            "--json",
            "state",
            "-q",
            ".state",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=False,
    )
    return (
        out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else "MISSING"
    )


def refresh(tests_dir: Path = ROOT / "tests", manifest_path: Path = MANIFEST) -> dict:
    issues = {str(n): _state(n) for n in sorted(cited_issues(tests_dir))}
    data = {
        "note": "Written by scripts/gap_ledgers.py --refresh; read by tests/test_gap_ledgers_cite_open_issues.py (#758).",
        "repo": REPO,
        "issues": issues,
    }
    manifest_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return data


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--refresh", action="store_true", help="rewrite the manifest from gh"
    )
    args = parser.parse_args(argv)
    if args.refresh:
        print(json.dumps(refresh()["issues"]))
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    problems = check(ROOT / "tests", {int(k): v for k, v in data["issues"].items()})
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
