"""The arrival rule for tracked-gap ledgers, and the manifest that backs it (#758).

A gap ledger is a module-level container in `tests/` whose entries each take a
KNOWN DEFECT out of a check until it is fixed (`_KNOWN_GAPS`, `_GHOSTS`,
`KNOWN_UNENCODED`, `ALLOWED_UNTIL_FIXED`, ...). Each entry makes its check ask
less, so each must cite an issue that EXISTS and is OPEN.
`tests/test_gap_ledgers_cite_open_issues.py` asks `check()`; `--refresh`
rewrites the offline manifest the check reads, so the suite never touches the
network.

    uv run python scripts/gap_ledgers.py            # print what check() reports
    uv run python scripts/gap_ledgers.py --refresh  # rewrite the manifest via gh

⚠⚠ **What makes a container a ledger is what an entry DOES, not what it is
called**, and the first version of this file forgot that: it found ledgers by
the spelling `_*GAPS` and review planted `_KNOWN_GHOSTS` (same shape, same
stated convention, in a file the issue named) and `ALLOWED_UNTIL_FIXED`
straight past it. So discovery is a PARTITION: every module-level container
whose name carries exemption vocabulary (`_VOCABULARY`) must be classified,
either as a ledger in `LEDGERS` or as a deliberate policy in `NOT_LEDGERS` with
its reason. An unclassified one fails, and so does a registry row naming
something that no longer exists. ⚠ The vocabulary is still a spelling, and
broad on purpose; the partition turns a miss into a decision someone makes.

⚠ Ledgers are read with `ast.literal_eval`, never by importing a test module:
an import runs module code and fixtures. A computed ledger, a ledger mutated
after its literal, and a file that does not parse are REPORTED, never skipped.
⚠ Only MODULE-level containers are seen; a ledger in a class or function body
is not, and none exists today.
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

#: Names that sound like an exemption. Broad on purpose: a name that escapes it
#: is the spelling trap again, and a false hit costs one `NOT_LEDGERS` line.
_VOCABULARY = re.compile(
    r"KNOWN|GAP|GHOST|PENDING|ALLOW|UNTIL|UNWIRED|UNENCODED|EXEMPT|EXCUS|EXCEPTION"
    r"|IGNORE|SKIP|WAIV|TOLERAT|GRANDFATHER|UNFIXED|BROKEN|DEFER|XFAIL|TODO"
)

#: Containers whose entries each park a KNOWN DEFECT until it is fixed. Each
#: has a guard that fails when its gap closes; this file adds the arrival half.
LEDGERS: frozenset[tuple[str, str]] = frozenset(
    {
        ("test_absence_wiring_guard.py", "KNOWN_UNWIRED_WRAPPERS"),
        ("test_constant_extraction_guard.py", "EXEMPT"),
        ("test_declared_forms_extract.py", "_KNOWN_GAPS"),
        ("test_file_io_encoding_guard.py", "KNOWN_UNENCODED"),
        ("test_grammar_spelled_forms.py", "_CONFIRMED_GAPS"),
        ("test_grammar_spelled_forms.py", "_INLINE_GHOSTS_FOUND"),
        ("test_grammar_spelled_forms.py", "_KNOWN_GHOSTS"),
        ("test_grammar_spelled_forms.py", "_PENDING_CHANNELS"),
        ("test_language_spec_maps_agree.py", "_KNOWN_GAPS"),
        ("test_member_kind_audit.py", "_GAPS"),
        ("test_nuxt_srcdir.py", "_JS_VARIANT_EXEMPT"),
        ("test_one_declaration_binds_every_name.py", "_GAPS"),
        ("test_rust_fidelity.py", "_KNOWN_GAPS"),
        ("test_subprocess_encoding_guard.py", "KNOWN_UNENCODED"),
        ("test_workflow_commit_identity.py", "ALLOWED_UNTIL_FIXED"),
    }
)

#: Containers the vocabulary catches that are a PERMANENT decision, not a
#: parked defect, each with the reason. An entry here is never a way to make
#: `check()` green: a ledger moved here stops asking for an issue.
NOT_LEDGERS: dict[tuple[str, str], str] = {
    (
        "test_config_isolation_guard.py",
        "EXEMPT",
    ): "deliberate: modules that exercise the real config path, each named with its reason",
    (
        "test_config_isolation_guard.py",
        "TWIN_EXEMPT",
    ): "a decision about importing the src twin, not a defect",
    (
        "test_dispatch_schema_parity.py",
        "_KNOWN_FORGIVING_ALIASES",
    ): "documented input aliases, deliberately absent from the schema",
    (
        "test_docs_config_parity.py",
        "DOC_KEYS_SKIPPED",
    ): "config keys documented as prose, not a single literal",
    (
        "test_grammar_spelled_forms.py",
        "_HELPER_LITERAL_EXCEPTIONS",
    ): "measured non-gaps: helper literals the parse function does not hold",
    (
        "test_rust_fidelity.py",
        "_KNOWN_UNEMITTED",
    ): "kinds we never index at all (module, macro), a policy",
    (
        "test_schema_baseline_transcription.py",
        "_EXEMPT",
    ): "the file names the historical values in its own docstring",
    ("test_sdist_exclusions.py", "ALLOWED_ROOT_FILES"): "the sdist allowlist itself",
    (
        "test_subprocess_encoding_guard.py",
        "EXEMPT_TREES",
    ): "a scope decision: tests/ drives git over ASCII fixtures",
    (
        "test_tectonic_temporal_signal.py",
        "_KNOWN_PRETTY_NAMES",
    ): "data: git's pretty-format names",
    (
        "test_turn_budget.py",
        "_AUTO_COMPACTED_EXEMPT",
    ): "files where the retired flag may appear as history",
    (
        "test_v1_108_176.py",
        "UNKNOWN",
    ): "fixture data: a tri-state UNKNOWN coverage block",
}

_ISSUE = re.compile(r"#(\d+)\b")
_EMPTY_CALLS = frozenset({"set", "frozenset", "dict", "list", "tuple"})
_MUTATORS = frozenset(
    {"update", "add", "append", "extend", "setdefault", "insert", "__setitem__"}
)
_NOT_A_LITERAL = object()


def _parse(path: Path):
    # Another file's `"\d"` docstring is not this scan's business.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _container_value(node):
    """The literal a container assignment holds, `_NOT_A_LITERAL`, or None
    when the value is not a container at all."""
    if isinstance(node, (ast.Dict, ast.Set, ast.List, ast.Tuple)):
        try:
            return ast.literal_eval(node)
        except ValueError:
            return _NOT_A_LITERAL
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _EMPTY_CALLS
    ):
        if not node.args and not node.keywords:
            return {
                "set": set(),
                "frozenset": frozenset(),
                "dict": {},
                "list": [],
                "tuple": (),
            }[node.func.id]
        return _NOT_A_LITERAL
    return None


def scan(tests_dir: Path):
    """`(candidates, problems)`: every module-level container whose name carries
    exemption vocabulary, as `{(file, name): (line, value, mutations)}`, and
    every file that could not be read."""
    candidates: dict = {}
    problems: list[str] = []
    for path in sorted(tests_dir.rglob("*.py")):
        rel = path.relative_to(tests_dir)
        if "fixtures" in rel.parts:
            continue
        try:
            tree = _parse(path)
        except SyntaxError as error:
            problems.append(
                f"{rel.as_posix()} does not parse, so its ledgers cannot be checked: {error.msg}"
            )
            continue
        found = {}
        for node in tree.body:
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets, value = [node.target], node.value
            else:
                continue
            for target in targets:
                if isinstance(target, ast.Name) and _VOCABULARY.search(
                    target.id.upper()
                ):
                    literal = _container_value(value)
                    if literal is not None:
                        found[target.id] = (node.lineno, literal, [])
        for node in tree.body:
            name = _mutated_name(node)
            if name in found:
                found[name][2].append(node.lineno)
        for name, record in found.items():
            candidates[(rel.as_posix(), name)] = record
    return candidates, problems


def _mutated_name(node):
    """The module-level name a statement mutates in place, if any."""
    if isinstance(node, ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name):
                return target.value.id
    if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
        return node.target.id
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
        func = node.value.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr in _MUTATORS
            and isinstance(func.value, ast.Name)
        ):
            return func.value.id
    return None


def entries(value, path=()):
    """Yield `(path, reason_text)` for every entry of a ledger literal.

    An entry is a string, or a tuple whose strings are read together (a
    `(form, reason)` pair); dicts, lists and sets are walked.
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


def cited_issues(tests_dir: Path, ledgers=LEDGERS) -> set[int]:
    candidates, _problems = scan(tests_dir)
    return {
        number
        for key, (_line, value, _mutations) in candidates.items()
        if key in ledgers and value is not _NOT_A_LITERAL
        for _path, text in entries(value)
        for number in cited(text)
    }


def check(
    tests_dir: Path, manifest: dict[int, str], ledgers=LEDGERS, not_ledgers=None
) -> list[str]:
    """Every way the ledgers under `tests_dir` fail the arrival rule."""
    not_ledgers = NOT_LEDGERS if not_ledgers is None else not_ledgers
    candidates, problems = scan(tests_dir)
    for key in sorted(set(candidates) - set(ledgers) - set(not_ledgers)):
        problems.append(
            f"{key[0]}:{candidates[key][0]} {key[1]} sounds like an exemption and is unclassified: "
            f"add it to LEDGERS (it parks a defect) or NOT_LEDGERS (a permanent decision, with the reason)"
        )
    for key in sorted((set(ledgers) | set(not_ledgers)) - set(candidates)):
        problems.append(
            f"{key[0]} {key[1]} is registered in scripts/gap_ledgers.py and no longer exists"
        )
    for key in sorted(set(candidates) & set(ledgers)):
        line, value, mutations = candidates[key]
        where = f"{key[0]}:{line} {key[1]}"
        if value is _NOT_A_LITERAL:
            problems.append(
                f"{where} is not a literal, so its entries cannot be checked"
            )
            continue
        for at in mutations:
            problems.append(
                f"{where} is mutated at line {at}, so its literal is not the whole ledger"
            )
        for path, text in entries(value):
            label = f"{where}{list(path)!r}"
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


def _state(number: int):
    """The issue's state, or None when `gh` could not say (no auth, no network,
    no such issue). None is UNKNOWN and is never written as a state."""
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
    state = out.stdout.strip()
    return state if out.returncode == 0 and state else None


def refresh(tests_dir: Path = ROOT / "tests", manifest_path: Path = MANIFEST) -> dict:
    """Rewrite the manifest, or refuse and write nothing when any state is unknown."""
    numbers = sorted(cited_issues(tests_dir))
    states = {n: _state(n) for n in numbers}
    unknown = [n for n, state in states.items() if state is None]
    if unknown:
        raise SystemExit(
            f"gh could not report {', '.join(f'#{n}' for n in unknown)}; the manifest is unchanged. "
            f"An issue that does not exist cannot be cited."
        )
    data = {
        "note": "Written by scripts/gap_ledgers.py --refresh; read by tests/test_gap_ledgers_cite_open_issues.py (#758).",
        "repo": REPO,
        "issues": {str(n): states[n] for n in numbers},
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
