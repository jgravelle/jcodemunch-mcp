"""DoD item 6 is graded from what the change ADDED, not from which files it
touched (workflow FINDINGS W-53).

`dod_checklist.py` decided whether item 6 applied by asking whether
`config.py` or `cli/` changed. Fifty-one of the fifty-three `add_parser`
calls and 164 long options live in `server.py`, and an env-var name is read
in thirty files, so #1018's `--identity-mode` on `index` read `n.a.: config.py
and cli/ untouched` and the `CLI-AND-ENV.md` row was written by hand with
nothing asking for it. And where the watched paths WERE touched, the two tests
the row ran check that a documented row still resolves and that a `DEFAULTS`
key is documented; neither asks whether a NEW subcommand or option has a row.

The row now reads the changed files' source at the base and at HEAD (AST, so
a wrapped call, a comment or a help string cannot move it), takes the names
HEAD has and the base did not, and asks whether each is named in the docs.

What each test pins, for `docs/harness/ARCHAEOLOGY.md`: an option, a
subcommand and an env var added in ANY module are found; a name that only
moved between changed files, a reformatted call, a mention in a comment, a
docstring or a help string, and an env name inside a longer string are not;
an option argparse itself hides (`help=argparse.SUPPRESS`) is not asked for;
a doc that names a LONGER flag or variable does not document the shorter one;
a head file that cannot be parsed is unmet, never n.a.; and the #1018 shape,
rebuilt from the real `server.py`, is detected and is met by the real docs.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / ".claude" / "hooks"
SERVER = "src/jcodemunch_mcp/server.py"


@pytest.fixture(scope="module")
def dod():
    sys.path.insert(0, str(HOOKS))
    try:
        spec = importlib.util.spec_from_file_location(
            "_wf_dod_row6", HOOKS / "dod_checklist.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(HOOKS))


BASE = '''
import argparse, os

def build():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers()
    index_parser = sub.add_parser("index", help="index a target")
    index_parser.add_argument("target")
    index_parser.add_argument("--no-ai-summaries", action="store_true")
    return ap

LEVEL = os.environ.get("JCODEMUNCH_LOG_LEVEL")
'''

DOCS = "| `index [target]` | `--no-ai-summaries` skips summaries. `JCODEMUNCH_LOG_LEVEL` sets the level. |"


def _added(dod, base: dict, head: dict) -> set[str]:
    added, unparsed = dod.row6_added(base, head)
    assert unparsed == []
    return {name for names in added.values() for name in names}


# -- what counts as an addition ----------------------------------------------


def test_an_option_added_in_server_py_is_found(dod):
    head = BASE.replace(
        '    return ap',
        '    index_parser.add_argument(\n'
        '        "--identity-mode",\n'
        '        choices=["config", "local", "git"],\n'
        '        default="config",\n'
        '    )\n'
        '    return ap',
    )
    assert _added(dod, {SERVER: BASE}, {SERVER: head}) == {"--identity-mode"}


def test_a_subcommand_added_is_found(dod):
    head = BASE.replace(
        "    return ap", '    sub.add_parser("refresh", help="x")\n    return ap'
    )
    assert _added(dod, {SERVER: BASE}, {SERVER: head}) == {"refresh"}


def test_an_env_var_read_in_any_module_is_found(dod):
    base = {"src/jcodemunch_mcp/security.py": "import os\n"}
    head = {
        "src/jcodemunch_mcp/security.py": 'import os\nX = os.environ.get("JCODEMUNCH_NEW_KNOB", "0")\n'
    }
    assert _added(dod, base, head) == {"JCODEMUNCH_NEW_KNOB"}


def test_a_short_and_long_pair_is_named_by_its_long_form(dod):
    head = BASE.replace(
        "    return ap",
        '    index_parser.add_argument("-n", "--dry-run", action="store_true")\n    return ap',
    )
    assert _added(dod, {SERVER: BASE}, {SERVER: head}) == {"--dry-run"}


def test_a_new_file_counts_everything_in_it(dod):
    head = {"src/jcodemunch_mcp/cli/new.py": 'def f(p):\n    p.add_argument("--fresh")\n'}
    assert _added(dod, {}, head) == {"--fresh"}


# -- what does not -----------------------------------------------------------


def test_untouched_names_in_a_changed_file_are_not_additions(dod):
    head = BASE + "\nOTHER = 1\n"
    assert _added(dod, {SERVER: BASE}, {SERVER: head}) == set()


def test_a_reformatted_call_is_not_an_addition(dod):
    head = BASE.replace(
        'index_parser.add_argument("--no-ai-summaries", action="store_true")',
        'index_parser.add_argument(\n        "--no-ai-summaries",\n        action="store_true",\n    )',
    )
    assert head != BASE
    assert _added(dod, {SERVER: BASE}, {SERVER: head}) == set()


def test_a_name_moved_between_two_changed_files_is_not_an_addition(dod):
    other = "src/jcodemunch_mcp/cli/moved.py"
    line = '    index_parser.add_argument("--no-ai-summaries", action="store_true")\n'
    head_server = BASE.replace(line, "")
    assert head_server != BASE
    head_other = 'def wire(p):\n    p.add_argument("--no-ai-summaries", action="store_true")\n'
    assert _added(dod, {SERVER: BASE, other: ""}, {SERVER: head_server, other: head_other}) == set()


def test_a_mention_is_not_an_addition(dod):
    head = BASE + (
        '\n# index_parser.add_argument("--ghost")\n'
        'DOC = """sub.add_parser("phantom") and --ghost"""\n'
        'HELP = "set JCODEMUNCH_GHOST=1 to enable"\n'
    )
    assert _added(dod, {SERVER: BASE}, {SERVER: head}) == set()


def test_an_option_argparse_hides_is_not_asked_for(dod):
    head = BASE.replace(
        "    return ap",
        '    index_parser.add_argument("--internal-probe", help=argparse.SUPPRESS)\n    return ap',
    )
    assert _added(dod, {SERVER: BASE}, {SERVER: head}) == set()


def test_a_positional_argument_is_not_an_option(dod):
    head = BASE.replace(
        "    return ap", '    index_parser.add_argument("extra")\n    return ap'
    )
    assert _added(dod, {SERVER: BASE}, {SERVER: head}) == set()


# -- is it documented --------------------------------------------------------


@pytest.mark.parametrize(
    "name, docs, documented",
    [
        ("--identity-mode", "`index` takes `--identity-mode local`", True),
        ("--identity-mode", "`--identity-mode-strict` is another flag", False),
        ("--identity-mode", "x--identity-mode", False),
        ("JCODEMUNCH_NEW_KNOB", "| `JCODEMUNCH_NEW_KNOB` | 0 |", True),
        ("JCODEMUNCH_NEW_KNOB", "JCODEMUNCH_NEW_KNOB_SECONDS only", False),
        ("JCODEMUNCH_NEW_KNOB", "MY_JCODEMUNCH_NEW_KNOB", False),
        ("refresh", "| `refresh [path]` | re-parse |", True),
        ("refresh", "run `jcodemunch-mcp refresh .`", True),
        ("refresh", "| `config refresh` | sub-subcommand |", True),
        ("refresh", "we refresh the index often", False),
        ("refresh", "`refresh-all` exists", False),
    ],
)
def test_a_name_is_documented_only_as_itself(dod, name, docs, documented):
    kind = "env" if name.isupper() else ("option" if name.startswith("--") else "subcommand")
    assert dod.row6_undocumented({kind: {name}}, docs) == ([] if documented else [name])


# -- the verdict -------------------------------------------------------------


def _head_with_everything() -> str:
    return BASE.replace(
        "    return ap",
        '    index_parser.add_argument("--identity-mode", default="config")\n'
        '    sub.add_parser("refresh")\n'
        "    return ap",
    ) + 'KNOB = os.environ.get("JCODEMUNCH_NEW_KNOB")\n'


def test_nothing_added_is_not_applicable(dod):
    verdict, evidence = dod.row6_verdict({SERVER: BASE}, {SERVER: BASE + "\nX = 1\n"}, DOCS)
    assert verdict is None
    assert "no CLI subcommand, option or env var added" in evidence


def test_an_undocumented_addition_is_unmet_and_named(dod):
    verdict, evidence = dod.row6_verdict({SERVER: BASE}, {SERVER: _head_with_everything()}, DOCS)
    assert verdict == "unmet"
    for name in ("--identity-mode", "refresh", "JCODEMUNCH_NEW_KNOB"):
        assert name in evidence


def test_a_partly_documented_addition_names_only_what_is_missing(dod):
    docs = DOCS + " `--identity-mode` and `refresh [path]`."
    verdict, evidence = dod.row6_verdict({SERVER: BASE}, {SERVER: _head_with_everything()}, docs)
    assert verdict == "unmet"
    missing = evidence.split("no row names:")[1]
    assert "JCODEMUNCH_NEW_KNOB" in missing
    assert "--identity-mode" not in missing and "refresh" not in missing


def test_a_documented_addition_is_met(dod):
    docs = DOCS + " `--identity-mode`, `refresh [path]`, `JCODEMUNCH_NEW_KNOB`."
    verdict, evidence = dod.row6_verdict({SERVER: BASE}, {SERVER: _head_with_everything()}, docs)
    assert verdict == "met"
    assert "--identity-mode" in evidence


def test_a_head_file_that_cannot_be_parsed_is_unmet_never_not_applicable(dod):
    verdict, evidence = dod.row6_verdict({SERVER: BASE}, {SERVER: "def broken(:\n"}, DOCS)
    assert verdict == "unmet"
    assert SERVER in evidence


def test_a_base_file_that_cannot_be_parsed_counts_the_head_as_added(dod):
    added, unparsed = dod.row6_added({SERVER: "def broken(:\n"}, {SERVER: BASE})
    assert unparsed == []
    assert "--no-ai-summaries" in added["option"] and "index" in added["subcommand"]


# -- the reported shape, from the real file ----------------------------------


class _DropIdentityMode(ast.NodeTransformer):
    """The real `server.py` as it stood before #1018: the `--identity-mode`
    `add_argument` statement removed."""

    dropped = 0

    def visit_Expr(self, node):
        call = node.value
        if (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "add_argument"
            and any(
                isinstance(a, ast.Constant) and a.value == "--identity-mode" for a in call.args
            )
        ):
            self.dropped += 1
            return None
        return node


def test_the_1018_shape_from_the_real_server_py(dod):
    head = (ROOT / SERVER).read_text(encoding="utf-8")
    dropper = _DropIdentityMode()
    base = ast.unparse(dropper.visit(ast.parse(head)))
    assert dropper.dropped == 1, "the reported option is no longer where the report found it"

    added, unparsed = dod.row6_added({SERVER: base}, {SERVER: head})
    assert unparsed == []
    assert added["option"] == {"--identity-mode"}
    assert added["subcommand"] == set() and added["env"] == set()

    docs = dod.row6_docs_text(ROOT)
    assert dod.row6_verdict({SERVER: base}, {SERVER: head}, docs)[0] == "met"
    without = docs.replace("--identity-mode", "--identity-m0de")
    assert dod.row6_verdict({SERVER: base}, {SERVER: head}, without)[0] == "unmet"


def test_the_checklist_grades_row_six_through_the_verdict_function():
    """`main` must call the function these tests exercise; a row 6 decided
    from touched paths alone is the defect."""
    tree = ast.parse((HOOKS / "dod_checklist.py").read_text(encoding="utf-8"))
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    called = {
        n.func.id
        for n in ast.walk(main)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "row6_verdict" in called
