"""`get_dead_code_v2` reads a file as text when its language records no call (LEDGER L-155).

The tool chose its caller evidence by one repository-wide test: does the index
hold ANY call reference? With none it read whole files as text. With any it
read call references plus module-level residue only. Several parsers record no
call reference (Clojure, Elixir, Erlang, and PHP as measured here), so in a
repository where another language does (Python, Ruby), a function of theirs
called only from INSIDE another function had no evidence on either path and
read `no_callers`: live code reported dead, in a tool whose output feeds
delete decisions. #409's flip, one level down.

The rule now is per language as the index holds it: a file whose language has
no call reference anywhere in the index is read as text, whatever the rest of
the repository records.

Every fixture goes through `index_folder` and the tool. A hand-built index
could carry call references the real parser never writes (Standing lesson
09-15).
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.storage import IndexStore
from jcodemunch_mcp.tools import get_dead_code_v2 as v2
from jcodemunch_mcp.tools.index_folder import index_folder

# One file per language: `used` is called only from inside `run`; `unused` is
# called by nothing; `run` is called by nothing.
SOURCES = {
    "clojure": ("a.clj", "(ns a)\n(defn used-thing [x]\n  x)\n\n(defn unused-thing [x]\n  x)\n\n(defn run []\n  (used-thing 1))\n",
                "used-thing", "unused-thing"),
    "elixir": ("a.ex", "defmodule A do\n  def used_thing(x) do\n    x\n  end\n\n  def unused_thing(x) do\n    x\n  end\n\n"
                       "  def run do\n    used_thing(1)\n  end\nend\n",
               "used_thing", "unused_thing"),
    "erlang": ("a.erl", "-module(a).\nused_thing(A) -> A.\nunused_thing(A) -> A.\nrun() -> used_thing(1).\n",
               "used_thing", "unused_thing"),
    "php": ("a.php", "<?php\nnamespace App;\nfunction used_thing() { return 1; }\nfunction unused_thing() { return 1; }\n"
                     "function run() { return used_thing(); }\n",
            "used_thing", "unused_thing"),
    # The four above are the ledger row's. The defect was wider: the parser's
    # table of call nodes holds thirteen languages, and every other language
    # records none by construction. Two of those, and Dart, which the table
    # names and which recorded none here.
    "c": ("a.c", "int used_thing(int x) {\n  return x;\n}\n\nint unused_thing(int x) {\n  return x;\n}\n\n"
                 "int run(void) {\n  return used_thing(1);\n}\n",
          "used_thing", "unused_thing"),
    "bash": ("a.sh", "used_thing() {\n  echo 1\n}\n\nunused_thing() {\n  echo 2\n}\n\nrun() {\n  used_thing\n}\n",
             "used_thing", "unused_thing"),
    "dart": ("a.dart", "int usedThing(int x) {\n  return x;\n}\n\nint unusedThing(int x) {\n  return x;\n}\n\n"
                       "int run() {\n  return usedThing(1);\n}\n",
             "usedThing", "unusedThing"),
}

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


def _signals(repo, store):
    out = v2.get_dead_code_v2(repo=repo, min_confidence=0.0, max_results=0, storage_path=store)
    assert "error" not in out, out
    assert out.get("_meta", {}).get("mode") != "call_graph_only", out.get("_meta")
    return {(r["file"], r["name"]): r["signals"] for r in out["dead_symbols"]}


@pytest.mark.parametrize("language", sorted(SOURCES))
def test_the_fixture_is_the_reported_shape(tmp_path, language):
    """Guard: this language records no call here, and Python beside it does."""
    fname, body, used, unused = SOURCES[language]
    _, _, index = _index(tmp_path, {**PYTHON_PAIR, fname: body})
    callers = index.get_callers_by_name()
    assert ("py.py", "b") in callers
    assert not [key for key in callers if key[0] == fname], "this parser records calls now; pick another fixture"
    names = {s["name"] for s in index.symbols if s["file"] == fname}
    assert {used, unused, "run"} <= names, names


@pytest.mark.parametrize("language", sorted(SOURCES))
def test_a_function_called_inside_another_is_not_reported_without_callers(tmp_path, language):
    fname, body, used, unused = SOURCES[language]
    repo, store, _ = _index(tmp_path, {**PYTHON_PAIR, fname: body})
    rows = _signals(repo, store)
    assert "no_callers" not in rows.get((fname, used), []), f"{language}: `{used}` is called in `run`"


@pytest.mark.parametrize("language", sorted(SOURCES))
def test_a_function_nothing_names_is_still_reported(tmp_path, language):
    fname, body, used, unused = SOURCES[language]
    repo, store, _ = _index(tmp_path, {**PYTHON_PAIR, fname: body})
    rows = _signals(repo, store)
    assert "no_callers" in rows[(fname, unused)], rows
    assert "no_callers" in rows[(fname, "run")], rows


@pytest.mark.parametrize("language", sorted(SOURCES))
def test_the_answer_does_not_depend_on_the_python_beside_it(tmp_path, language):
    """The flip itself: the same file, with and without a call reference elsewhere."""
    fname, body, used, unused = SOURCES[language]
    alone = tmp_path / "alone"
    alone.mkdir()
    mixed = tmp_path / "mixed"
    mixed.mkdir()
    # Without the Python pair the index holds no call reference at all; a
    # second file of the same language keeps an import-free repo out of
    # call-graph-only mode where it can, and where it cannot the test skips
    # nothing: it compares only the `no_callers` signal.
    repo_m, store_m, _ = _index(mixed, {**PYTHON_PAIR, fname: body})
    repo_a, store_a, index_a = _index(alone, {"util.py": "X = 1\n", "py.py": "import util\n", fname: body})
    assert not index_a.get_callers_by_name()
    out_a = v2.get_dead_code_v2(repo=repo_a, min_confidence=0.0, max_results=0, storage_path=store_a)
    rows_a = {(r["file"], r["name"]): r["signals"] for r in out_a["dead_symbols"]}
    rows_m = _signals(repo_m, store_m)
    for name in (used, unused, "run"):
        assert ("no_callers" in rows_a.get((fname, name), [])) == ("no_callers" in rows_m.get((fname, name), [])), (
            f"{language}: `{name}` reads differently once Python records a call beside it"
        )


def test_a_recursive_function_is_not_its_own_caller(tmp_path):
    body = "(ns a)\n(defn loop-thing [x]\n  (loop-thing x))\n\n(defn other [] 1)\n"
    repo, store, _ = _index(tmp_path, {**PYTHON_PAIR, "a.clj": body})
    rows = _signals(repo, store)
    assert "no_callers" in rows[("a.clj", "loop-thing")], rows


def test_a_mention_in_a_comment_counts_in_a_text_file_and_the_response_says_text_was_read(tmp_path):
    """Text evidence is weaker than a call reference; the response names the files it was used for."""
    body = "(ns a)\n(defn used-thing [x]\n  x)\n\n(defn run []\n  ;; used-thing is not called here\n  1)\n"
    repo, store, _ = _index(tmp_path, {**PYTHON_PAIR, "a.clj": body})
    out = v2.get_dead_code_v2(repo=repo, min_confidence=0.0, max_results=0, storage_path=store)
    rows = {(r["file"], r["name"]): r["signals"] for r in out["dead_symbols"]}
    assert "no_callers" not in rows.get(("a.clj", "used-thing"), [])
    assert out["callers_read_as_text"] == {"files": 1, "languages": ["clojure"]}, out.get("callers_read_as_text")


# ---------------------------------------------------------------------------
# what must not move
# ---------------------------------------------------------------------------


def test_a_language_that_records_calls_is_not_read_as_text(tmp_path):
    """Python records calls, so a mention inside another function's body is not a caller."""
    files = {
        "util.py": "def b():\n    return 1\n\n\ndef named_only():\n    return 2\n",
        "py.py": "from util import b\n\n\ndef a():\n    # named_only is mentioned, never called\n    return b()\n",
        "a.clj": SOURCES["clojure"][1],
    }
    repo, store, _ = _index(tmp_path, files)
    rows = _signals(repo, store)
    assert "no_callers" in rows[("util.py", "named_only")], rows


def test_a_repository_whose_languages_all_record_calls_reports_no_text_reading(tmp_path):
    repo, store, _ = _index(tmp_path, {**PYTHON_PAIR, "more.py": "import util\n\n\ndef c():\n    return util.b()\n"})
    out = v2.get_dead_code_v2(repo=repo, min_confidence=0.0, max_results=0, storage_path=store)
    assert "callers_read_as_text" not in out


def test_ruby_records_its_calls_and_is_not_read_as_text(tmp_path):
    body = "def used_thing(x)\n  x\nend\n\ndef named_only\n  2\nend\n\ndef run\n  # named_only\n  used_thing(1)\nend\n"
    repo, store, index = _index(tmp_path, {**PYTHON_PAIR, "a.rb": body})
    assert [key for key in index.get_callers_by_name() if key[0] == "a.rb"]
    out = v2.get_dead_code_v2(repo=repo, min_confidence=0.0, max_results=0, storage_path=store)
    rows = {(r["file"], r["name"]): r["signals"] for r in out["dead_symbols"]}
    assert "no_callers" not in rows.get(("a.rb", "used_thing"), [])
    assert "no_callers" in rows[("a.rb", "named_only")], rows
    assert "callers_read_as_text" not in out


def test_the_rule_is_asked_of_the_index_not_of_a_list_of_languages(tmp_path):
    """A language is read as text when THIS index holds no call reference for it."""
    _, _, index = _index(tmp_path, {**PYTHON_PAIR, "a.clj": SOURCES["clojure"][1], "a.rb": "def x\n  1\nend\n"})
    text = v2._files_read_as_text(index)
    # Ruby can record calls, and this Ruby file holds none: its language has
    # no call reference in this index, so it is read as text too. That is the
    # safe direction (text only ever finds a caller).
    assert text == {"a.clj", "a.rb"}, text


# ---------------------------------------------------------------------------
# the second spelling: no import edge, so the tool takes call-graph-only mode
# ---------------------------------------------------------------------------

NO_IMPORTS = {"py.py": "def b():\n    return 1\n\n\ndef a():\n    return b()\n"}


@pytest.mark.parametrize("language", sorted(SOURCES))
def test_call_graph_only_mode_reads_the_same_files_as_text(tmp_path, language):
    fname, body, used, unused = SOURCES[language]
    repo, store, index = _index(tmp_path, {**NO_IMPORTS, fname: body})
    assert index.get_callers_by_name() and not [k for k in index.get_callers_by_name() if k[0] == fname]
    out = v2.get_dead_code_v2(repo=repo, min_confidence=0.0, max_results=0, storage_path=store)
    assert out["_meta"]["mode"] == "call_graph_only", out["_meta"]
    dead = {(r["file"], r["name"]) for r in out["dead_symbols"]}
    assert (fname, used) not in dead, f"{language}: `{used}` is called in `run`"
    assert (fname, unused) in dead and (fname, "run") in dead, dead
    assert out["callers_read_as_text"]["languages"] == [language]


def test_call_graph_only_mode_is_unmoved_where_every_language_records_calls(tmp_path):
    body = "def b():\n    return 1\n\n\ndef named_only():\n    return 2\n\n\ndef a():\n    # named_only\n    return b()\n"
    repo, store, _ = _index(tmp_path, {"py.py": body})
    out = v2.get_dead_code_v2(repo=repo, min_confidence=0.0, max_results=0, storage_path=store)
    assert out["_meta"]["mode"] == "call_graph_only"
    dead = {r["name"] for r in out["dead_symbols"]}
    assert dead == {"a", "named_only"}, dead
    assert "callers_read_as_text" not in out


# ---------------------------------------------------------------------------
# review of L-155: the flip survived in call-graph-only mode, and the
# disclosure counted files nobody read
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("language", sorted(SOURCES))
def test_call_graph_only_mode_does_not_depend_on_the_python_beside_it(tmp_path, language):
    """Alone, beside Python that calls nothing, beside Python that calls: one answer."""
    fname, body, used, unused = SOURCES[language]
    beside = {
        "alone": {},
        "python_without_a_call": {"py.py": "def b():" + chr(10) + "    return 1" + chr(10)},
        "python_with_a_call": NO_IMPORTS,
    }
    answers = {}
    for label, extra in beside.items():
        root = tmp_path / label
        root.mkdir()
        repo, store, index = _index(root, {**extra, fname: body})
        assert bool(index.get_callers_by_name()) == (label == "python_with_a_call")
        out = v2.get_dead_code_v2(repo=repo, min_confidence=0.0, max_results=0, storage_path=store)
        assert out["_meta"]["mode"] == "call_graph_only", (label, out["_meta"])
        answers[label] = sorted(r["name"] for r in out["dead_symbols"] if r["file"] == fname)
    assert answers["alone"] == answers["python_without_a_call"] == answers["python_with_a_call"] == sorted([unused, "run"]), answers


DATA_FILES = {
    "pyproject.toml": "[project]" + chr(10) + 'name = "x"' + chr(10),
    "package.json": '{"name": "x", "version": "1.0.0"}' + chr(10),
}


def test_a_data_file_nobody_reads_is_not_disclosed_as_read(tmp_path):
    """TOML and JSON hold symbols and record no call; no function is asked of them."""
    repo, store, index = _index(tmp_path, {**PYTHON_PAIR, **DATA_FILES})
    eligible = v2._files_read_as_text(index)
    assert eligible & set(DATA_FILES), "guard: a data file must be eligible for this test to mean anything"
    out = v2.get_dead_code_v2(repo=repo, min_confidence=0.0, max_results=0, storage_path=store)
    assert "callers_read_as_text" not in out, out.get("callers_read_as_text")


def test_the_disclosure_counts_the_files_read_not_the_files_eligible(tmp_path):
    repo, store, index = _index(tmp_path, {**PYTHON_PAIR, **DATA_FILES, "a.clj": SOURCES["clojure"][1]})
    assert len(v2._files_read_as_text(index)) > 1
    out = v2.get_dead_code_v2(repo=repo, min_confidence=0.0, max_results=0, storage_path=store)
    assert out["callers_read_as_text"] == {"files": 1, "languages": ["clojure"]}, out["callers_read_as_text"]


def test_an_index_with_no_call_reference_discloses_its_text_reading_too(tmp_path):
    """The text fallback reads every function's file; the response says how many."""
    files = {"util.py": "X = 1" + chr(10), "py.py": "import util" + chr(10), "a.clj": SOURCES["clojure"][1]}
    repo, store, index = _index(tmp_path, files)
    assert not index.get_callers_by_name()
    out = v2.get_dead_code_v2(repo=repo, min_confidence=0.0, max_results=0, storage_path=store)
    assert out.get("_meta", {}).get("mode") != "call_graph_only"
    assert out["callers_read_as_text"] == {"files": 1, "languages": ["clojure"]}, out["callers_read_as_text"]


class _StubIndex:
    def __init__(self, symbols):
        self.symbols = symbols


def test_a_file_of_two_languages_is_read_as_text_only_when_neither_records_a_call():
    """The rule itself, on symbol rows: a template with a script block follows its recording half."""
    index = _StubIndex([
        {"file": "page.vue", "language": "vue", "name": "tpl"},
        {"file": "page.vue", "language": "typescript", "name": "setup"},
        {"file": "other.ts", "language": "typescript", "name": "f", "call_references": ["g"]},
        {"file": "both.x", "language": "vue", "name": "a"},
        {"file": "both.x", "language": "toml", "name": "b"},
        {"file": "", "language": "vue", "name": "no file"},
    ])
    assert v2._files_read_as_text(index) == {"both.x"}
