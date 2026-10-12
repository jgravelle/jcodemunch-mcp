"""A name is matched as a token whatever characters it starts and ends with (LEDGER L-154).

The word rule was `\\b` + the name + `\\b`. A word boundary beside a punctuation
mark exists only when a WORD character is on its other side, so a name that
ends in one (Ruby and Elixir `valid?` and `save!`, Clojure `empty?`) matched in
`valid?x` and never in `valid?`, `valid?(x)` or `valid? x`; a name that starts
with one (`$x`, Clojure `-main`) matched only after a word character, which is
`run-main` and never `(-main)`. Every tool that asks "does this text name this
symbol" read live code as unnamed: `get_dead_code_v2` reported it without
callers, and the rename plan skipped its uses.

The rule is one builder now (`word_pattern` in `tools/_call_graph.py`): on a
side where the name has a word character the boundary is `\\b` as before, and
on a side where it has a mark the name must not be glued to a word character.
A name that starts and ends with a word character gets the pattern it always
had, byte for byte.

The rule had twenty-odd hand-written copies; the last test scans `src/` so a
new one fails here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from jcodemunch_mcp.storage import IndexStore
from jcodemunch_mcp.tools import _call_graph
from jcodemunch_mcp.tools import get_blast_radius as blast
from jcodemunch_mcp.tools import get_dead_code_v2 as v2
from jcodemunch_mcp.tools import plan_refactoring as plan
from jcodemunch_mcp.tools._call_graph import FileWords, _word_match, _word_starts
from jcodemunch_mcp.tools.index_folder import index_folder

SRC = Path(__file__).resolve().parents[1] / "src" / "jcodemunch_mcp"

# (name, text, is the name a token of the text)
CASES = [
    # a name that ENDS in a mark
    ("valid?", "valid?", True),
    ("valid?", "x.valid?(y)", True),
    ("valid?", "if valid? x", True),
    ("valid?", "return valid?\n", True),
    ("valid?", "A.valid?(1)", True),
    ("valid?", "invalid?(y)", False),
    ("valid?", "valid(y)", False),
    ("valid?", "valid?x", False),
    ("save!", "user.save!", True),
    ("save!", "save!(x)", True),
    ("save!", "autosave!", False),
    ("empty?", "(empty? coll)", True),
    ("empty?", "(not-empty? coll)", True),  # `-` is not a word character: as `main` is named in `run-main`
    ("name=", "self.name= 1", True),
    # a name that STARTS with a mark
    ("$x", "$x", True),
    ("$x", "echo $x;", True),
    ("$x", "f($x, $y)", True),
    ("$x", "$xy", False),
    ("$x", "a$x", False),
    ("-main", "(-main)", True),
    ("-main", "(defn -main [& args])", True),
    ("-main", "(run-main)", False),
    ("@count", "x = @count + 1", True),
    ("@count", "user@count", False),
    # marks on both sides
    ("*out*", "(binding [*out* w])", True),
    ("*out*", "x*out*y", False),
    # a name with no word character: an operator has no glue rule
    ("==", "a==b", True),
    ("==", "a == b", True),
    ("<=>", "a<=>b", True),
    ("==", "a = b", False),
    ("==", "a===b", True),  # nothing says where an operator ends
    # a name that ends in whitespace: the space is the separator
    ("be-it ", "be-it now", True),
    ("be-it ", "(be-it)", False),
    # a name of word characters at both ends: unchanged
    ("run", "run()", True),
    ("run", "rerun()", False),
    ("run", "run_all()", False),
    ("do-thing", "(do-thing 1)", True),
    ("do-thing", "(redo-thing 1)", False),
    ("add/2", "add/2", True),
    ("ns::f", "x = ns::f()", True),
]


@pytest.mark.parametrize("name, text, expected", CASES)
def test_the_word_rule(name, text, expected):
    assert _word_match(text, name) is expected


@pytest.mark.parametrize("name, text, expected", CASES)
def test_the_offsets_follow_the_same_rule(name, text, expected):
    starts = _word_starts(text, name)
    assert bool(starts) is expected
    assert all(text.startswith(name, i) for i in starts)


@pytest.mark.parametrize("name, text, expected", CASES)
def test_the_word_index_follows_the_same_rule(name, text, expected):
    padded = "first line\n" + text + "\nlast line\n"
    words = FileWords(padded)
    assert words.names(name) is expected
    assert FileWords(padded).lines_naming(name) == ([1] if expected else [])


@pytest.mark.parametrize("name", ["run", "do-thing", "add/2", "ns::f", "a.b", "x y", "caf\u00e9", "\u540d\u524d", "_x", "x1"])
def test_a_name_with_a_word_character_at_both_ends_keeps_its_pattern_byte_for_byte(name):
    assert _call_graph.word_pattern(name) == r"\b" + re.escape(name) + r"\b"


def test_an_empty_name_matches_nothing_new():
    assert _call_graph.word_pattern("") == r"\b\b"


# ---------------------------------------------------------------------------
# through the tools
# ---------------------------------------------------------------------------

PYTHON_PAIR = {
    "util.py": "def b():\n    return 1\n",
    "py.py": "from util import b\n\ndef a():\n    return b()\n",
}


def _index(tmp_path, files):
    src = tmp_path / "src"
    src.mkdir()
    store = tmp_path / "store"
    store.mkdir()
    for name, body in files.items():
        (src / name).write_text(body, encoding="utf-8")
    r = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert r["success"] is True
    owner, name = r["repo"].split("/", 1)
    return r["repo"], str(store), IndexStore(base_path=str(store)).load_index(owner, name)


def _without_callers(repo, store):
    out = v2.get_dead_code_v2(repo=repo, min_confidence=0.0, max_results=0, storage_path=store)
    assert "error" not in out, out
    return {(r["file"], r["name"]) for r in out["dead_symbols"] if "no_callers" in r["signals"]}


TOOL_CASES = {
    # Ruby records a call inside a method; a call at the top of the file is text.
    "ruby, called at module level": (
        {"a.rb": "def check_thing?\n  true\nend\n\ndef other_thing?\n  true\nend\n\ncheck_thing?\n"},
        ("a.rb", "check_thing?"), ("a.rb", "other_thing?")),
    "ruby bang method, called at module level": (
        {"a.rb": "def save_thing!\n  true\nend\n\ndef drop_thing!\n  true\nend\n\nsave_thing!\n"},
        ("a.rb", "save_thing!"), ("a.rb", "drop_thing!")),
    "elixir, called inside another function": (
        {"a.ex": "defmodule A do\n  def valid_thing?(x) do\n    x\n  end\n\n  def other_thing?(x) do\n    x\n  end\n\n"
                 "  def run do\n    valid_thing?(1)\n  end\nend\n"},
        ("a.ex", "valid_thing?"), ("a.ex", "other_thing?")),
    "elixir, called from another file": (
        {"a.ex": "defmodule A do\n  def valid_thing?(x) do\n    x\n  end\n\n  def other_thing?(x) do\n    x\n  end\nend\n",
         "b.ex": "defmodule B do\n  def run do\n    A.valid_thing?(1)\n  end\nend\n"},
        ("a.ex", "valid_thing?"), ("a.ex", "other_thing?")),
    "clojure predicate, called inside another function": (
        {"a.clj": "(ns a)\n(defn empty-thing? [x]\n  x)\n\n(defn other-thing? [x]\n  x)\n\n(defn run []\n  (empty-thing? 1))\n"},
        ("a.clj", "empty-thing?"), ("a.clj", "other-thing?")),
    "clojure bang function, called from another file": (
        {"a.clj": "(ns a)\n(defn reset-thing! [x]\n  x)\n\n(defn clear-thing! [x]\n  x)\n",
         "b.clj": "(ns b)\n(defn run []\n  (a/reset-thing! 1))\n"},
        ("a.clj", "reset-thing!"), ("a.clj", "clear-thing!")),
}


@pytest.mark.parametrize("label", sorted(TOOL_CASES))
def test_a_called_name_that_ends_in_a_mark_is_not_reported_without_callers(tmp_path, label):
    files, called, uncalled = TOOL_CASES[label]
    repo, store, index = _index(tmp_path, {**PYTHON_PAIR, **files})
    names = {(s["file"], s["name"]) for s in index.symbols}
    assert called in names and uncalled in names, "guard: the parser indexes the name with its mark"
    dead = _without_callers(repo, store)
    assert called not in dead, f"{label}: `{called[1]}` is called"
    assert uncalled in dead, f"{label}: nothing calls `{uncalled[1]}`"


def test_blast_radius_reads_the_same_rule():
    text = "x.valid?(1)\ninvalid?(2)\nvalid?\n"
    assert blast._name_in_content(text, "valid?") is True
    assert blast._name_in_content("invalid?(2)\n", "valid?") is False
    assert [s["line"] for s in blast._extract_reference_snippets(text, "valid?")] == [1, 3]


def test_a_rename_rewrites_the_uses_and_nothing_glued_to_them():
    text = "x.valid?(1) and invalid?(2)\nreturn valid?\n"
    assert plan._apply_word_replacement(text, "valid?", "ok?") == "x.ok?(1) and invalid?(2)\nreturn ok?\n"
    assert plan._apply_word_replacement("(-main) (run-main)", "-main", "-start") == "(-start) (run-main)"
    assert plan._apply_word_replacement("run() rerun()", "run", "go") == "go() rerun()"


# ---------------------------------------------------------------------------
# the rule has one builder
# ---------------------------------------------------------------------------

# A boundary written beside an escaped name, in any of the shapes the copies took.
_HAND_ROLLED = re.compile(
    r"""\\b["']\s*\+\s*re\.escape\(      # r"\b" + re.escape(
      | re\.escape\([^()]*(?:\([^()]*\)[^()]*)*\)\s*\+\s*r?["']\\b   # re.escape(...) + r"\b
      | \{re\.escape\([^{}]*\)\}\\b      # f"...{re.escape(x)}\b"
      | \\b\{re\.escape\(                # f"\b{re.escape(
    """,
    re.VERBOSE,
)


def _hand_rolled_sites(text: str) -> list[int]:
    return [i + 1 for i, line in enumerate(text.splitlines()) if _HAND_ROLLED.search(line)]


@pytest.mark.parametrize("line", [
    'return bool(re.search(r"\\b" + re.escape(name) + r"\\b", content))',
    'pattern = re.compile(r"\\b" + re.escape(sym_name) + r"\\s*\\(")',
    'if re.search(r"\\b" + re.escape(stay_sym["name"]) + r"\\b", body):',
    'if re.match(rf"@?{re.escape(target_name)}\\b", d):',
    "x = re.compile('\\\\b' + re.escape(n))",
    'interp = re.compile(r"\\{[^}]*\\b" + re.escape(symbol_name) + r"\\b[^}]*\\}")',
])
def test_the_scan_sees_each_shape_the_copies_took(line):
    assert _hand_rolled_sites(line) == [1], line


def test_no_module_writes_the_word_rule_by_hand():
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for line_no in _hand_rolled_sites(text):
            offenders.append(f"{path.relative_to(SRC).as_posix()}:{line_no}")
    assert not offenders, (
        "a word boundary written beside re.escape(name) is wrong for a name that starts or ends in a "
        "mark (L-154); build it with tools._call_graph.word_pattern / word_left / word_right: " + ", ".join(offenders)
    )
