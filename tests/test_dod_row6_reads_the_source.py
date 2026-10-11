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
import collections
import importlib.util
import subprocess
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


BASE = """
import argparse, os

def build():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers()
    index_parser = sub.add_parser("index", help="index a target")
    index_parser.add_argument("target")
    index_parser.add_argument("--no-ai-summaries", action="store_true")
    return ap

LEVEL = os.environ.get("JCODEMUNCH_LOG_LEVEL")
"""

DOCS = "| `index [target]` | `--no-ai-summaries` skips summaries. `JCODEMUNCH_LOG_LEVEL` sets the level. |"


def _added(dod, base: dict, head: dict) -> set[str]:
    added, unparsed = dod.row6_added(base, head)
    assert unparsed == []
    return {name for names in added.values() for name in names}


# -- what counts as an addition ----------------------------------------------


def test_an_option_added_in_server_py_is_found(dod):
    head = BASE.replace(
        "    return ap",
        "    index_parser.add_argument(\n"
        '        "--identity-mode",\n'
        '        choices=["config", "local", "git"],\n'
        '        default="config",\n'
        "    )\n"
        "    return ap",
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
    head = {
        "src/jcodemunch_mcp/cli/new.py": 'def f(p):\n    p.add_argument("--fresh")\n'
    }
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
    head_other = (
        'def wire(p):\n    p.add_argument("--no-ai-summaries", action="store_true")\n'
    )
    assert (
        _added(dod, {SERVER: BASE, other: ""}, {SERVER: head_server, other: head_other})
        == set()
    )


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
        ("refresh", "see `refresh [path]` in the middle of a sentence", False),
        ("server", "| `server.py` | the dispatcher |", False),
        ("status", "| `status()` | reports progress |", False),
        ("sync", "run `uv sync --locked` first", False),
        ("log", "| `log/2` | erlang arity |", False),
    ],
)
def test_a_name_is_documented_only_as_itself(dod, name, docs, documented):
    kind = (
        "env"
        if name.isupper()
        else ("option" if name.startswith("--") else "subcommand")
    )
    assert dod.row6_undocumented({kind: {name}}, docs) == ([] if documented else [name])


# -- the verdict -------------------------------------------------------------


def _head_with_everything() -> str:
    return (
        BASE.replace(
            "    return ap",
            '    index_parser.add_argument("--identity-mode", default="config")\n'
            '    sub.add_parser("refresh")\n'
            "    return ap",
        )
        + 'KNOB = os.environ.get("JCODEMUNCH_NEW_KNOB")\n'
    )


def test_nothing_added_is_not_applicable(dod):
    verdict, evidence = dod.row6_verdict(
        {SERVER: BASE}, {SERVER: BASE + "\nX = 1\n"}, DOCS
    )
    assert verdict is None
    assert "no CLI subcommand or option gained a declaration" in evidence


def test_an_undocumented_addition_is_unmet_and_named(dod):
    verdict, evidence = dod.row6_verdict(
        {SERVER: BASE}, {SERVER: _head_with_everything()}, DOCS
    )
    assert verdict == "unmet"
    for name in ("--identity-mode", "refresh", "JCODEMUNCH_NEW_KNOB"):
        assert name in evidence


def test_a_partly_documented_addition_names_only_what_is_missing(dod):
    docs = "\n".join([DOCS + " `--identity-mode`", "| `refresh [path]` | re-parse |"])
    verdict, evidence = dod.row6_verdict(
        {SERVER: BASE}, {SERVER: _head_with_everything()}, docs
    )
    assert verdict == "unmet"
    missing = evidence.split("no row names:")[1]
    assert "JCODEMUNCH_NEW_KNOB" in missing
    assert "--identity-mode" not in missing and "refresh" not in missing


def test_a_documented_addition_is_met(dod):
    docs = "\n".join(
        [
            DOCS + " `--identity-mode`, `JCODEMUNCH_NEW_KNOB`",
            "| `refresh [path]` | re-parse |",
        ]
    )
    verdict, evidence = dod.row6_verdict(
        {SERVER: BASE}, {SERVER: _head_with_everything()}, docs
    )
    assert verdict == "met"
    assert "--identity-mode" in evidence


def test_a_head_file_that_cannot_be_parsed_is_unmet_never_not_applicable(dod):
    verdict, evidence = dod.row6_verdict(
        {SERVER: BASE}, {SERVER: "def broken(:\n"}, DOCS
    )
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
                isinstance(a, ast.Constant) and a.value == "--identity-mode"
                for a in call.args
            )
        ):
            self.dropped += 1
            return None
        return node


def test_the_1018_shape_from_the_real_server_py(dod):
    head = (ROOT / SERVER).read_text(encoding="utf-8")
    dropper = _DropIdentityMode()
    base = ast.unparse(dropper.visit(ast.parse(head)))
    assert dropper.dropped >= 1, (
        "the reported option is no longer where the report found it"
    )

    added, unparsed = dod.row6_added({SERVER: base}, {SERVER: head})
    assert unparsed == []
    assert added["option"] == {"--identity-mode"}
    assert added["subcommand"] == set() and added["env"] == set()

    docs = dod.row6_docs(ROOT)
    assert dod.row6_verdict({SERVER: base}, {SERVER: head}, docs)[0] == "met"
    without = {
        d: t.replace("--identity-mode", "--identity-m0de") for d, t in docs.items()
    }
    assert dod.row6_verdict({SERVER: base}, {SERVER: head}, without)[0] == "unmet"


# -- spellings review round one found ----------------------------------------


@pytest.mark.parametrize(
    "source, expected",
    [
        ('import os\nA = os.environ.get("OPENAI_NEW_KNOB")\n', {"OPENAI_NEW_KNOB"}),
        (
            'import os\nA = os.getenv("CODE_INDEX_NEW_KNOB", "x")\n',
            {"CODE_INDEX_NEW_KNOB"},
        ),
        ('import os\nA = os.environ["WATCHFILES_NEW_KNOB"]\n', {"WATCHFILES_NEW_KNOB"}),
        (
            'from os import environ, getenv\nA = environ.get("A_KNOB")\nB = getenv("B_KNOB")\n',
            {"A_KNOB", "B_KNOB"},
        ),
        ('PREFIX = "JCODEMUNCH_HOOK_"\n', set()),
        ('d = {}\nA = d.get("NOT_AN_ENV_READ")\n', set()),
        ('import os\nA = os.environ.get("lower_case")\n', set()),
    ],
    ids=[
        "environ-get",
        "getenv",
        "subscript",
        "bare-imports",
        "prefix-constant",
        "dict-get",
        "lower-case",
    ],
)
def test_an_env_read_is_named_whatever_its_prefix(dod, source, expected):
    assert dod.cli_env_names(source)["env"] == expected


def test_suppress_imported_by_name_is_still_hidden(dod):
    source = (
        "from argparse import SUPPRESS\n"
        'def f(p):\n    p.add_argument("--internal-probe", help=SUPPRESS)\n'
    )
    assert dod.cli_env_names(source)["option"] == set()


def test_a_subcommand_alias_is_a_subcommand(dod):
    source = 'def f(sub):\n    sub.add_parser("list-repos", aliases=["ls", "lr"])\n'
    assert dod.cli_env_names(source)["subcommand"] == {"list-repos", "ls", "lr"}


def test_a_source_with_a_bom_parses(dod):
    source = chr(0xFEFF) + 'def f(p):\n    p.add_argument("--fresh")\n'
    assert dod.cli_env_names(source)["option"] == {"--fresh"}


def _counts(dod, **kinds):
    out = {k: collections.Counter() for k in dod.ROW6_KINDS}
    for kind, names in kinds.items():
        out[kind].update(names)
    return out


def test_the_whole_tree_separates_a_move_from_an_addition(dod):
    """A renamed file has no base under its new path, so its names look new.
    The whole of `src/` at base and head says which ones are."""
    head = {
        "src/jcodemunch_mcp/cli/receipt_cli.py": (
            "import os\n"
            'def f(p):\n    p.add_argument("--by-day")\n    p.add_argument("--brand-new")\n'
            'A = os.environ.get("JCODEMUNCH_OLD_KNOB")\n'
        )
    }
    base_tree = _counts(dod, option=["--by-day"], env=["JCODEMUNCH_OLD_KNOB"])
    head_tree = _counts(
        dod, option=["--by-day", "--brand-new"], env=["JCODEMUNCH_OLD_KNOB"]
    )
    asked = []

    def tree():
        asked.append(1)
        return base_tree, head_tree

    verdict, evidence = dod.row6_verdict({}, head, "", tree)
    assert verdict == "unmet" and "--brand-new" in evidence
    assert "--by-day" not in evidence and "JCODEMUNCH_OLD_KNOB" not in evidence
    assert asked == [1]

    asked.clear()
    assert (
        dod.row6_verdict({"a.py": "x = 1\n"}, {"a.py": "x = 2\n"}, "", tree)[0] is None
    )
    assert asked == [], (
        "the trees are walked only when the changed files show a candidate"
    )


def test_a_second_subcommand_taking_an_existing_option_is_an_addition(dod):
    """The #1018 shape one subcommand over. A set of names cannot see it: the
    name is already in the tree. A count can."""
    base = _counts(dod, option=["--dry-run", "--identity-mode"])
    head = _counts(
        dod, option=["--dry-run", "--dry-run", "--identity-mode", "--identity-mode"]
    )
    assert dod.row6_grew(base, head)["option"] == {"--dry-run", "--identity-mode"}

    changed_head = {
        SERVER: BASE.replace(
            "    return ap",
            '    w = sub.add_parser("watch")\n    w.add_argument("--no-ai-summaries")\n    return ap',
        )
    }
    whole_base = _counts(dod, subcommand=["index"], option=["--no-ai-summaries"])
    whole_head = _counts(
        dod, subcommand=["index", "watch"], option=["--no-ai-summaries"] * 2
    )
    verdict, evidence = dod.row6_verdict(
        {SERVER: BASE}, changed_head, "", lambda: (whole_base, whole_head)
    )
    assert verdict == "unmet"
    assert "--no-ai-summaries" in evidence and "watch" in evidence


def test_a_subcommand_name_declared_a_second_time_is_an_addition(dod):
    """`list` under a second parent parser: the name is in the tree already."""
    base = _counts(dod, subcommand=["list", "index"])
    head = _counts(dod, subcommand=["list", "list", "index"])
    assert dod.row6_grew(base, head)["subcommand"] == {"list"}


def test_a_second_read_of_an_env_var_is_not_an_addition(dod):
    base = _counts(dod, env=["JCODEMUNCH_OLD_KNOB"])
    head = _counts(dod, env=["JCODEMUNCH_OLD_KNOB"])
    head_sources = {
        "src/jcodemunch_mcp/x.py": 'import os\nA = os.environ.get("JCODEMUNCH_OLD_KNOB")\n'
    }
    assert dod.row6_verdict({}, head_sources, "", lambda: (base, head))[0] is None


def test_each_doc_documents_what_dod_six_lets_it(dod, tmp_path):
    (tmp_path / "CLI-AND-ENV.md").write_text(
        "| Subcommand | Purpose |\n"
        "| `index [target]` | x |\n"
        "| `org-report` / `org-rollup` | two in one cell |\n"
        "| **`bold-cmd`** | bolded |\n"
        "| `config set <key>` | a parent word |\n"
        "| `serve` | mentions `not-first-cell` in its purpose and `JCODEMUNCH_IN_SPAN` |\n"
        "Bare prose says CI and NO_COLOR matter.\n",
        encoding="utf-8",
    )
    (tmp_path / "CONFIGURATION.md").write_text(
        "| `port` | int | a config KEY row, not a subcommand |\n`--config-flag` and `OPENAI_IN_CONFIG`\n",
        encoding="utf-8",
    )
    (tmp_path / "CLAUDE.md").write_text(
        "Run `jcodemunch-mcp prose-cmd .` and `--prose-flag` in a sentence.\n"
        "| `refresh [path]` | `--max-seconds` bounded |\n",
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text(
        "`--readme-only` and `jcodemunch-mcp readme-cmd`\n", encoding="utf-8"
    )
    docs = dod.row6_docs(tmp_path)
    assert sorted(docs) == ["CLAUDE.md", "CLI-AND-ENV.md", "CONFIGURATION.md"]

    documented = {
        "subcommand": {
            "index",
            "org-report",
            "org-rollup",
            "bold-cmd",
            "set",
            "config",
            "serve",
            "refresh",
        },
        "option": {"--max-seconds", "--config-flag"},
        "env": {"JCODEMUNCH_IN_SPAN", "OPENAI_IN_CONFIG"},
    }
    assert dod.row6_undocumented(documented, docs) == []

    not_documented = {
        "subcommand": {
            "port",
            "not-first-cell",
            "prose-cmd",
            "readme-cmd",
            "Subcommand",
        },
        "option": {"--prose-flag", "--readme-only"},
        "env": {"CI", "NO_COLOR"},
    }
    assert dod.row6_undocumented(not_documented, docs) == sorted(
        not_documented["subcommand"]
    ) + sorted(not_documented["option"]) + sorted(not_documented["env"])


# -- the entry point: a real git repository the test owns --------------------


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.invalid",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout


OTHER = "src/jcodemunch_mcp/cli/receipt.py"
OTHER_SRC = 'import os\n\ndef wire(p):\n    p.add_argument("--by-day")\n\nROOT = os.environ.get("JCODEMUNCH_OLD_KNOB")\n'


@pytest.fixture
def repo(tmp_path):
    """`main` holds server.py and cli/receipt.py and a docs table; the test
    works on branch `work`."""
    r = tmp_path / "repo"
    (r / "src" / "jcodemunch_mcp" / "cli").mkdir(parents=True)
    (r / SERVER).write_text(BASE, encoding="utf-8")
    (r / OTHER).write_text(OTHER_SRC, encoding="utf-8")
    (r / "CLI-AND-ENV.md").write_text(DOCS + "\n", encoding="utf-8")
    _git(r, "init", "-q", "-b", "main")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "base")
    _git(r, "checkout", "-q", "-b", "work")
    return r


def _changed(repo: Path) -> list[str]:
    """The path list `main` builds: committed against the base, plus unstaged."""
    return sorted(
        set(_git(repo, "diff", "--name-only", "main...HEAD").split())
        | set(_git(repo, "diff", "--name-only").split())
    )


NO_PARITY = lambda: (None, "config.py and cli/ untouched")  # noqa: E731


def _add_option(repo: Path, flag: str = "--identity-mode") -> None:
    p = repo / SERVER
    p.write_text(
        p.read_text(encoding="utf-8").replace(
            "    return ap", f'    index_parser.add_argument("{flag}")\n    return ap'
        ),
        encoding="utf-8",
    )


def test_a_committed_option_in_server_py_is_unmet_then_met(dod, repo):
    _add_option(repo)
    _git(repo, "commit", "-q", "-am", "add option")

    verdict, evidence = dod.row6_row(_changed(repo), "main", repo, NO_PARITY)
    assert verdict == "unmet" and "--identity-mode" in evidence

    with (repo / "CLI-AND-ENV.md").open("a", encoding="utf-8") as fh:
        fh.write("| `index [target]` | `--identity-mode` picks the key |\n")
    assert dod.row6_row(_changed(repo), "main", repo, NO_PARITY)[0] == "met"


def test_an_uncommitted_and_a_staged_addition_are_both_graded(dod, repo):
    _add_option(repo, "--unstaged")
    assert dod.row6_row(_changed(repo), "main", repo, NO_PARITY)[0] == "unmet"

    _git(repo, "add", "-A")
    assert _changed(repo) == [], (
        "a staged-only change is absent from the path list main builds"
    )
    verdict, evidence = dod.row6_row(_changed(repo), "main", repo, NO_PARITY)
    assert verdict == "unmet" and "--unstaged" in evidence


def test_the_base_is_the_merge_base_not_the_base_refs_tip(dod, repo):
    """An option that reaches `main` AFTER the branch point was not added by
    the branch, and one the branch adds is still seen."""
    _add_option(repo, "--from-branch")
    _git(repo, "commit", "-q", "-am", "branch adds")
    _git(repo, "checkout", "-q", "main")
    (repo / OTHER).write_text(
        OTHER_SRC + 'X = os.environ.get("JCODEMUNCH_MAIN_ONLY")\n', encoding="utf-8"
    )
    _git(repo, "commit", "-q", "-am", "main moves")
    _git(repo, "checkout", "-q", "work")

    verdict, evidence = dod.row6_row(_changed(repo), "main", repo, NO_PARITY)
    assert verdict == "unmet"
    assert "--from-branch" in evidence and "JCODEMUNCH_MAIN_ONLY" not in evidence


def test_a_name_the_base_ref_dropped_after_the_branch_point_is_not_an_addition(
    dod, repo
):
    """`main` removes an option the branch still carries. Against the ref's
    TIP the branch would appear to have added it."""
    _git(repo, "checkout", "-q", "main")
    _add_option(repo, "--legacy-undocumented")
    _git(repo, "commit", "-q", "-am", "main has a legacy option")
    _git(repo, "checkout", "-q", "-b", "work2")
    p = repo / SERVER
    p.write_text(p.read_text(encoding="utf-8") + "\nTOUCHED = 1\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "branch touches the file")
    _git(repo, "checkout", "-q", "main")
    p.write_text(
        p.read_text(encoding="utf-8").replace(
            '    index_parser.add_argument("--legacy-undocumented")\n', ""
        ),
        encoding="utf-8",
    )
    _git(repo, "commit", "-q", "-am", "main drops it")
    _git(repo, "checkout", "-q", "work2")
    assert "--legacy-undocumented" in p.read_text(encoding="utf-8")

    verdict, evidence = dod.row6_row(_changed(repo), "main", repo, NO_PARITY)
    assert verdict == "n.a.", evidence


def test_an_env_var_added_outside_server_py_and_cli_is_unmet(dod, repo):
    """The module the old rule never watched."""
    sec = repo / "src" / "jcodemunch_mcp" / "security.py"
    sec.write_text(
        'import os\nCAP = os.environ.get("JCODEMUNCH_NEW_CAP")\n', encoding="utf-8"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "new module reads an env var")

    verdict, evidence = dod.row6_row(_changed(repo), "main", repo, NO_PARITY)
    assert verdict == "unmet" and "JCODEMUNCH_NEW_CAP" in evidence


def test_a_renamed_file_adds_nothing(dod, repo):
    _git(repo, "mv", OTHER, "src/jcodemunch_mcp/cli/receipt_cli.py")
    _git(repo, "commit", "-q", "-m", "rename")
    assert _changed(repo) == [
        "src/jcodemunch_mcp/cli/receipt_cli.py"
    ] or OTHER in _changed(repo)

    verdict, evidence = dod.row6_row(_changed(repo), "main", repo, NO_PARITY)
    assert verdict == "n.a.", evidence


def test_a_second_read_of_another_modules_env_var_adds_nothing(dod, repo):
    p = repo / SERVER
    p.write_text(
        p.read_text(encoding="utf-8") + 'OLD = os.environ.get("JCODEMUNCH_OLD_KNOB")\n',
        encoding="utf-8",
    )
    _git(repo, "commit", "-q", "-am", "second read")
    verdict, evidence = dod.row6_row(_changed(repo), "main", repo, NO_PARITY)
    assert verdict == "n.a.", evidence


def test_an_option_another_module_already_takes_is_still_an_addition(dod, repo):
    """`--by-day` exists in cli/receipt.py and has no row. `index` gaining it
    is a new declaration, and the row asks for it."""
    _add_option(repo, "--by-day")
    _git(repo, "commit", "-q", "-am", "index takes --by-day too")
    verdict, evidence = dod.row6_row(_changed(repo), "main", repo, NO_PARITY)
    assert verdict == "unmet" and "--by-day" in evidence


def test_a_renamed_module_that_only_reads_env_vars_adds_nothing(dod, repo):
    """No argparse call in the file: the base walk must still find it."""
    env_only = repo / "src" / "jcodemunch_mcp" / "knobs.py"
    env_only.write_text(
        'import os\nA = os.getenv("JCODEMUNCH_KNOB_A")\n', encoding="utf-8"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "env-only module")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--ff-only", "work")
    _git(repo, "checkout", "-q", "work")
    _git(repo, "mv", "src/jcodemunch_mcp/knobs.py", "src/jcodemunch_mcp/knobs2.py")
    _git(repo, "commit", "-q", "-m", "rename")
    verdict, evidence = dod.row6_row(_changed(repo), "main", repo, NO_PARITY)
    assert verdict == "n.a.", evidence


def test_an_untracked_module_is_graded(dod, repo):
    new = repo / "src" / "jcodemunch_mcp" / "zz_new.py"
    new.write_text(
        'import os\nA = os.environ.get("JCODEMUNCH_ZZ_UNTRACKED")\n', encoding="utf-8"
    )
    assert _changed(repo) == []
    verdict, evidence = dod.row6_row(_changed(repo), "main", repo, NO_PARITY)
    assert verdict == "unmet" and "JCODEMUNCH_ZZ_UNTRACKED" in evidence


def test_a_change_outside_src_or_to_a_non_python_file_is_not_applicable(dod, repo):
    (repo / "scripts").mkdir()
    (repo / "scripts" / "tool.py").write_text(
        'def f(p):\n    p.add_argument("--script-flag")\n', encoding="utf-8"
    )
    (repo / "src" / "jcodemunch_mcp" / "data.json").write_text(
        '{"JCODEMUNCH_X": 1}', encoding="utf-8"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "outside")
    assert dod.row6_row(_changed(repo), "main", repo, NO_PARITY)[0] == "n.a."


def test_a_base_that_cannot_be_resolved_is_unmet_never_not_applicable(dod, repo):
    verdict, evidence = dod.row6_row([], "origin/nope", repo, NO_PARITY)
    assert verdict == "unmet" and "origin/nope" in evidence


@pytest.mark.parametrize(
    "add_option, parity, expected",
    [
        (False, (None, "untouched"), "n.a."),
        (False, ("met", "parity ok"), "met"),
        (False, ("unmet", "parity failed"), "unmet"),
        (True, (None, "untouched"), "unmet"),
        (True, ("met", "parity ok"), "unmet"),
        (True, ("unmet", "parity failed"), "unmet"),
    ],
)
def test_either_half_unmet_makes_the_row_unmet(dod, repo, add_option, parity, expected):
    if add_option:
        _add_option(repo)
        _git(repo, "commit", "-q", "-am", "add option")
    verdict, evidence = dod.row6_row(_changed(repo), "main", repo, lambda: parity)
    assert verdict == expected
    assert parity[1] in evidence


def test_main_hands_row_six_to_the_row_function_and_nothing_else():
    """`main` writes row 6 exactly once, and its verdict is `row6_row`'s with
    the real parity half (no fourth argument)."""
    tree = ast.parse((HOOKS / "dod_checklist.py").read_text(encoding="utf-8"))
    main = next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main"
    )
    sixes = [
        n
        for n in ast.walk(main)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "row"
        and n.args
        and isinstance(n.args[0], ast.Constant)
        and n.args[0].value == 6
    ]
    assert len(sixes) == 1
    (call,) = sixes
    assert len(call.args) == 2 and isinstance(call.args[1], ast.Starred)
    inner = call.args[1].value
    assert isinstance(inner, ast.Call) and inner.func.id == "row6_row"
    assert [a.id for a in inner.args] == ["changed", "base", "REPO"]
    assert inner.keywords == []


# -- the config-key half ------------------------------------------------------


def test_parity_runs_its_two_tests_only_when_their_files_moved(dod):
    calls = []

    def run(*cmd):
        calls.append(cmd)
        return 0, "2 passed in 0.1s\n"

    assert dod.row6_parity(["src/jcodemunch_mcp/server.py", "README.md"], run) == (
        None,
        "config.py and cli/ untouched",
    )
    assert calls == []

    verdict, evidence = dod.row6_parity(["src/jcodemunch_mcp/cli/receipt.py"], run)
    assert verdict == "met" and "2 passed" in evidence
    assert len(calls) == 1
    assert "tests/test_cli_env_split.py" in calls[0]
    assert "tests/test_config_docs_reverse_parity.py" in calls[0]

    assert dod.row6_parity(["src/jcodemunch_mcp/config.py"], run)[0] == "met"


def test_a_failing_parity_run_is_unmet(dod):
    verdict, evidence = dod.row6_parity(
        ["src/jcodemunch_mcp/config.py"],
        lambda *cmd: (1, "1 failed, 1 passed in 0.2s\n"),
    )
    assert verdict == "unmet" and "1 failed" in evidence


def test_the_row_uses_the_real_parity_half_when_none_is_passed(dod, repo, monkeypatch):
    seen = []

    def fake_sh(*cmd):
        seen.append(cmd)
        return 1, "1 failed in 0.1s\n"

    monkeypatch.setattr(dod, "sh", fake_sh)
    cfg = repo / "src" / "jcodemunch_mcp" / "config.py"
    cfg.write_text("DEFAULTS = {}\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "config moves")

    verdict, evidence = dod.row6_row(_changed(repo), "main", repo)
    assert seen, "the parity tests were not run"
    assert verdict == "unmet" and "1 failed" in evidence


# -- env spellings round two found --------------------------------------------


@pytest.mark.parametrize(
    "source, expected",
    [
        ('import os\nos.environ["TOKENIZERS_PARALLELISM"] = "false"\n', set()),
        ('import os\ndel os.environ["GONE_KNOB"]\n', set()),
        ('import os\nA = "CODE_INDEX_NEW" in os.environ\n', {"CODE_INDEX_NEW"}),
        ('import os\nA = "CODE_INDEX_NEW" not in os.environ\n', {"CODE_INDEX_NEW"}),
        ('def f(cfg):\n    return cfg.getenv("CFG_VAR")\n', set()),
        (
            'import os\nos.environ["JCODEMUNCH_SET_HERE"] = "1"\n',
            {"JCODEMUNCH_SET_HERE"},
        ),
    ],
    ids=[
        "store",
        "delete",
        "in",
        "not-in",
        "getenv-on-another-object",
        "a-prefixed-literal-is-always-a-name",
    ],
)
def test_a_write_is_not_a_read_and_a_membership_test_is(dod, source, expected):
    assert dod.cli_env_names(source)["env"] == expected
