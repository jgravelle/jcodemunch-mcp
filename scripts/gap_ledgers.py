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
    r"|NOT_|NON_|INTENTIONAL"
)

#: Containers whose entries each park a KNOWN DEFECT until it is fixed. Each
#: has a guard that fails when its gap closes; this file adds the arrival half.
LEDGERS: frozenset[tuple[str, str]] = frozenset(
    {
        ("test_absence_wiring_guard.py", "KNOWN_UNWIRED_WRAPPERS"),
        # Parks a defect: "empty is the intended end state" and a stale-entry
        # ratchet backs it. Filed as a decision in round 1; review moved it.
        ("test_config_isolation_guard.py", "TWIN_EXEMPT"),
        ("test_constant_extraction_guard.py", "EXEMPT"),
        ("test_declared_forms_extract.py", "_KNOWN_GAPS"),
        ("test_file_io_encoding_guard.py", "KNOWN_UNENCODED"),
        ("test_grammar_spelled_forms.py", "_CONFIRMED_GAPS"),
        ("test_grammar_spelled_forms.py", "_INLINE_GHOSTS_FOUND"),
        ("test_grammar_spelled_forms.py", "_KNOWN_GHOSTS"),
        ("test_grammar_spelled_forms.py", "_PENDING_CHANNELS"),
        # Fields read by nothing, each citing #725; wiring one in makes it a channel.
        ("test_grammar_spelled_forms.py", "_UNREAD_NON_CHANNEL_FIELDS"),
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
        "test_member_kind_audit.py",
        "_NOT_SAMPLED",
    ): "languages with no member declarations to audit, each with the reason",
    (
        "test_grammar_spelled_forms.py",
        "_NON_CHANNEL_SPEC_FIELDS",
    ): "the roster union of the classified buckets, computed",
    (
        "test_dispatch_schema_parity.py",
        "_NON_SCHEMA_KEYS",
    ): "cross-cutting dispatch keys, not per-tool schema properties",
    (
        "test_configuration_md_defaults.py",
        "_NON_LITERAL_DEFAULTS",
    ): "defaults too large for a table cell, each proved by its own check",
    (
        "test_v1_108_199.py",
        "_NOT_SERVER_PATH",
    ): "modules that own their process and a terminal stdin",
    (
        "test_v1_108_199.py",
        "_INTENTIONAL_PIPE",
    ): "sites that pass a terminating stdin on purpose, each with the reason",
    (
        "test_watcher_knob_parity.py",
        "_NOT_A_KNOB",
    ): "a parameter-name collision, not a knob",
    (
        "test_mcp_instructions.py",
        "_NOT_TOOL_NAMES",
    ): "snake_case prose words that are not tool names",
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
        except (ValueError, TypeError):
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
                        found[target.id] = (node.lineno, literal, [], node)
        # Anywhere in the module, not only at top level: a mutation under an
        # `if`, a `try`, a loop or a function body adds an entry just the same
        # (review round 2 planted seven that the top-level scan missed).
        defining = {record[3] for record in found.values()}
        for node in ast.walk(tree):
            if node in defining:
                continue
            for name in _mutated_names(node):
                if name in found:
                    found[name][2].append(node.lineno)
        for name, (line, literal, mutations, _node) in found.items():
            candidates[(rel.as_posix(), name)] = (line, literal, sorted(set(mutations)))
    return candidates, problems


def _root_name(node):
    """`X` for `X`, `X[k]`, `X[k][j]`, `X.attr` and any chain of them."""
    while isinstance(node, (ast.Subscript, ast.Attribute)):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _targets(node):
    if isinstance(node, (ast.Tuple, ast.List)):
        for element in node.elts:
            yield from _targets(element)
    else:
        yield node


def _mutated_names(node):
    """Every name a statement rebinds or mutates in place: an assignment to it
    or through it (`X = ...`, `X[k] = ...`, `X[k][j] = ...`, `X |= ...`) or a
    mutator call on it (`X.update(...)`, `X[k].add(...)`)."""
    if isinstance(node, ast.Assign):
        targets = [t for target in node.targets for t in _targets(target)]
    elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
        targets = list(_targets(node.target))
    elif (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _MUTATORS
    ):
        targets = [node.func.value]
    else:
        return []
    return [name for name in (_root_name(t) for t in targets) if name]


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
    elif isinstance(item, (dict, list, set, frozenset)) and item:
        yield from entries(item, path)
    else:
        # `None`, a number, an EMPTY container: an entry with no reason at
        # all, never an entry that walks into nothing (review round 2).
        yield path, ""


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
    for key in sorted(set(ledgers) & set(not_ledgers)):
        problems.append(
            f"{key[0]} {key[1]} is registered as both a ledger and a decision"
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
                problems.append(
                    f"{label} names no issue: {text!r} (a ledger of bare names cannot carry "
                    f"one: reshape it to {{name: reason}} first)"
                )
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
            "state,url",
            "-q",
            '.url + " " + .state',
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=False,
    )
    # `gh issue view` also resolves a PULL REQUEST number; a PR is not an issue.
    url, _, state = out.stdout.strip().partition(" ")
    return state if out.returncode == 0 and state and "/issues/" in url else None


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
