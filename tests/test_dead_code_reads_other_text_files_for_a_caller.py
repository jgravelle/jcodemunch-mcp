"""`get_dead_code_v2` reads other text-read files for a caller, import edge or not (LEDGER L-157).

For a function of a language that records no call reference, the tool read
text in two places: the function's own file, and each file the import graph
says imports that file. Several of those languages give the import graph no
edge between the two files that matter: a Clojure `:require`, an Elixir
`alias`, an Erlang remote call, a Bash `source`, and two C files that include
one header (the edge runs to the header, never between the `.c` files). And
call-graph-only mode has no import graph at all. So a function called only
from another file read `no_callers`: live code reported dead.

Now a function in a text-read file that is still without a caller is asked
of every other text-read file of a language that holds functions. A line
inside a symbol of the SAME name there is a declaration or another
definition, never a call: a C prototype in a header must not make every
declared function live, and two files that each define `init` must not call
each other.

Every fixture goes through `index_folder` and the tool (Standing lesson
09-15: a hand-built index could carry import edges the real parser never
writes).
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.storage import IndexStore
from jcodemunch_mcp.tools import get_dead_code_v2 as v2
from jcodemunch_mcp.tools._call_graph import FileWords
from jcodemunch_mcp.tools.index_folder import index_folder

PYTHON_PAIR = {
    "util.py": "def b():\n    return 1\n",
    "py.py": "from util import b\n\ndef a():\n    return b()\n",
}
# Call-graph-only mode: Python that records a call and imports nothing.
NO_IMPORTS = {"py.py": "def b():\n    return 1\n\n\ndef a():\n    return b()\n"}

# language -> (files, the file that defines, the name called from the other file, the name nothing calls)
PAIRS = {
    "clojure": (
        {"a.clj": "(ns a)\n(defn used-thing [x]\n  x)\n\n(defn unused-thing [x]\n  x)\n",
         "b.clj": "(ns b\n  (:require [a :as a]))\n(defn run []\n  (a/used-thing 1))\n"},
        "a.clj", "used-thing", "unused-thing"),
    "elixir": (
        {"a.ex": "defmodule A do\n  def used_thing(x) do\n    x\n  end\n\n  def unused_thing(x) do\n    x\n  end\nend\n",
         "b.ex": "defmodule B do\n  alias A\n\n  def run do\n    A.used_thing(1)\n  end\nend\n"},
        "a.ex", "used_thing", "unused_thing"),
    "erlang": (
        {"a.erl": "-module(a).\nused_thing(A) -> A.\nunused_thing(A) -> A.\n",
         "b.erl": "-module(b).\nrun() -> a:used_thing(1).\n"},
        "a.erl", "used_thing", "unused_thing"),
    "bash": (
        {"a.sh": "used_thing() {\n  echo 1\n}\n\nunused_thing() {\n  echo 2\n}\n",
         "b.sh": "source ./a.sh\nrun() {\n  used_thing\n}\n"},
        "a.sh", "used_thing", "unused_thing"),
    # The ordinary C layout: both files include the header, no edge joins them,
    # and the header DECLARES both functions.
    "c": (
        {"a.h": "int used_thing(int x);\nint unused_thing(int x);\n",
         "a.c": "#include \"a.h\"\nint used_thing(int x) {\n  return x;\n}\n\nint unused_thing(int x) {\n  return x;\n}\n",
         "b.c": "#include \"a.h\"\nint run(void) {\n  return used_thing(1);\n}\n"},
        "a.c", "used_thing", "unused_thing"),
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


def _run(repo, store):
    out = v2.get_dead_code_v2(repo=repo, min_confidence=0.0, max_results=0, storage_path=store)
    assert "error" not in out, out
    return out


def _without_callers(out):
    return {(r["file"], r["name"]) for r in out["dead_symbols"] if "no_callers" in r["signals"]}


@pytest.mark.parametrize("language", sorted(PAIRS))
def test_the_fixture_is_the_reported_shape(tmp_path, language):
    """Guard: no call reference for these files, and no import edge from the caller to the definer."""
    files, definer, used, unused = PAIRS[language]
    _, _, index = _index(tmp_path, {**PYTHON_PAIR, **files})
    assert not [key for key in index.get_callers_by_name() if key[0] in files]
    rev = v2._build_reverse_adjacency(index.imports, frozenset(index.source_files), getattr(index, "alias_map", None),
                                      getattr(index, "psr4_map", None))
    assert not [f for f in rev.get(definer, ()) if f in files], "an import edge reaches the definer now; pick another fixture"


@pytest.mark.parametrize("language", sorted(PAIRS))
def test_a_function_called_only_from_another_file_is_not_reported(tmp_path, language):
    files, definer, used, unused = PAIRS[language]
    repo, store, _ = _index(tmp_path, {**PYTHON_PAIR, **files})
    out = _run(repo, store)
    assert out.get("_meta", {}).get("mode") != "call_graph_only"
    dead = _without_callers(out)
    assert (definer, used) not in dead, f"{language}: `{used}` is called from the other file"
    assert (definer, unused) in dead, f"{language}: nothing calls `{unused}`"


@pytest.mark.parametrize("language", sorted(PAIRS))
def test_the_same_in_call_graph_only_mode(tmp_path, language):
    files, definer, used, unused = PAIRS[language]
    repo, store, index = _index(tmp_path, {**NO_IMPORTS, **files})
    out = _run(repo, store)
    if index.imports:
        # C's includes are import edges; the multi-signal path is the one it takes.
        assert language == "c"
        return
    assert out["_meta"]["mode"] == "call_graph_only", out["_meta"]
    dead = {(r["file"], r["name"]) for r in out["dead_symbols"]}
    assert (definer, used) not in dead, f"{language}: `{used}` is called from the other file"
    assert (definer, unused) in dead


@pytest.mark.parametrize("language", sorted(PAIRS))
def test_the_same_on_an_index_with_no_call_reference(tmp_path, language):
    """The text fallback: no Python beside it, so nothing in the index records a call."""
    files, definer, used, unused = PAIRS[language]
    repo, store, index = _index(tmp_path, files)
    assert not index.get_callers_by_name()
    out = _run(repo, store)
    dead = {(r["file"], r["name"]) for r in out["dead_symbols"] if "no_callers" in r.get("signals", [])}
    assert (definer, used) not in dead, f"{language}: `{used}` is called from the other file"
    assert (definer, unused) in dead


# ---------------------------------------------------------------------------
# what must not move
# ---------------------------------------------------------------------------


def test_a_c_prototype_in_a_header_is_not_a_call(tmp_path):
    """`unused_thing` is declared in a.h and defined in a.c; a declaration is not a caller."""
    files, definer, used, unused = PAIRS["c"]
    repo, store, index = _index(tmp_path, {**PYTHON_PAIR, **files})
    assert [s for s in index.symbols if s["file"] == "a.h" and s["name"] == unused], "guard: the prototype is a symbol"
    assert ("a.c", unused) in _without_callers(_run(repo, store))


def test_two_files_that_each_define_a_name_do_not_call_each_other(tmp_path):
    files = {
        "a.ex": "defmodule A do\n  def init(x) do\n    x\n  end\nend\n",
        "b.ex": "defmodule B do\n  def init(x) do\n    x\n  end\nend\n",
    }
    repo, store, _ = _index(tmp_path, {**PYTHON_PAIR, **files})
    dead = _without_callers(_run(repo, store))
    assert ("a.ex", "init") in dead and ("b.ex", "init") in dead, dead


def test_a_call_beside_another_definition_of_the_name_still_counts(tmp_path):
    """b.ex defines its own `init` AND calls A.init outside it: the call is outside the same-named span."""
    files = {
        "a.ex": "defmodule A do\n  def init(x) do\n    x\n  end\nend\n",
        "b.ex": "defmodule B do\n  def init(x) do\n    x\n  end\n\n  def run do\n    A.init(1)\n  end\nend\n",
    }
    repo, store, _ = _index(tmp_path, {**PYTHON_PAIR, **files})
    dead = _without_callers(_run(repo, store))
    assert ("a.ex", "init") not in dead, dead


def test_a_data_file_that_names_a_function_is_not_a_caller(tmp_path):
    """JSON holds symbols and records no call, but no JSON symbol is a function: it is not read."""
    files = {
        "a.clj": "(ns a)\n(defn used-thing [x]\n  x)\n",
        "config.json": '{"handler": "used-thing", "name": "x"}\n',
    }
    repo, store, index = _index(tmp_path, {**PYTHON_PAIR, **files})
    assert "config.json" in v2._files_read_as_text(index), "guard: the data file must be eligible"
    out = _run(repo, store)
    assert ("a.clj", "used-thing") in _without_callers(out)
    assert out["callers_read_as_text"] == {"files": 1, "languages": ["clojure"]}, out["callers_read_as_text"]


def test_a_function_of_a_language_that_records_calls_is_not_asked_of_other_files(tmp_path):
    """Python records calls: a mention in an unrelated Python or Clojure file is not a caller."""
    files = {
        "util.py": "def b():\n    return 1\n\n\ndef named_only():\n    return 2\n",
        "py.py": "from util import b\n\n\ndef a():\n    return b()\n",
        "other.py": "def c():\n    # named_only\n    return 3\n",
        "a.clj": "(ns a)\n(defn run []\n  ;; named_only\n  1)\n",
    }
    repo, store, _ = _index(tmp_path, files)
    assert ("util.py", "named_only") in _without_callers(_run(repo, store))


def test_the_disclosure_counts_the_other_files_read(tmp_path):
    files, definer, used, unused = PAIRS["clojure"]
    repo, store, _ = _index(tmp_path, {**PYTHON_PAIR, **files})
    out = _run(repo, store)
    assert out["callers_read_as_text"] == {"files": 2, "languages": ["clojure"]}, out["callers_read_as_text"]


# ---------------------------------------------------------------------------
# the work: other files are read once, and a plain name is a dictionary read
# ---------------------------------------------------------------------------


def _many(files_n, functions_n):
    """files_n Clojure files of functions_n uncalled plain-named functions each, beside Python."""
    files = dict(PYTHON_PAIR)
    for f in range(files_n):
        body = "(ns m%d)\n" % f + "".join("(defn fn_%d_%d [x]\n  x)\n\n" % (f, i) for i in range(functions_n))
        files["m%d.clj" % f] = body
    return files


def test_each_file_is_built_once_however_many_functions_ask(tmp_path, monkeypatch):
    built = []
    real_init = FileWords.__init__

    def counting(self, content):
        built.append(len(content))
        real_init(self, content)

    monkeypatch.setattr(FileWords, "__init__", counting)
    repo, store, _ = _index(tmp_path, _many(6, 40))
    built.clear()
    out = _run(repo, store)
    assert out["callers_read_as_text"]["files"] == 6
    # Six files, and the module-level sweep's residue index for a file it is asked about.
    assert len(built) <= 12, len(built)


def test_asking_other_files_about_a_plain_name_does_not_grow_with_the_files(tmp_path, monkeypatch):
    """A dead plain-named function costs a dictionary read, not one question per other file."""
    asked = []
    real_names = FileWords.names

    def counting(self, name):
        asked.append(name)
        return real_names(self, name)

    counts = {}
    for files_n in (4, 16):
        root = tmp_path / str(files_n)
        root.mkdir()
        repo, store, _ = _index(root, _many(files_n, 10))
        monkeypatch.setattr(FileWords, "names", counting)
        asked.clear()
        _run(repo, store)
        counts[files_n] = len(asked)
        monkeypatch.setattr(FileWords, "names", real_names)
    # Four times the files and four times the functions: at most four times the questions (plus slack), never sixteen.
    assert counts[16] <= 5 * max(counts[4], 1) + 40, counts


# ---------------------------------------------------------------------------
# which line of a same-named symbol is the declaration
# ---------------------------------------------------------------------------


def test_a_facade_of_the_same_name_is_a_caller(tmp_path):
    """`facade.clj` defines `start` and its body calls `impl/start` on the next line."""
    files = {
        "impl.clj": "(ns impl)\n(defn start [x]\n  x)\n",
        "facade.clj": "(ns facade)\n(defn start [x]\n  (impl/start x))\n",
    }
    repo, store, _ = _index(tmp_path, {**PYTHON_PAIR, **files})
    dead = _without_callers(_run(repo, store))
    assert ("impl.clj", "start") not in dead, dead


def test_a_prototype_whose_name_is_on_its_second_line_is_not_a_call(tmp_path):
    """GNU style: the return type on one line, the name on the next. Still a declaration."""
    files = {
        "a.h": "int\nunused_thing(int x);\n",
        "a.c": "#include \"a.h\"\nint\nunused_thing(int x)\n{\n  return x;\n}\n",
        "b.c": "#include \"a.h\"\nint run(void) {\n  return 1;\n}\n",
    }
    repo, store, index = _index(tmp_path, {**PYTHON_PAIR, **files})
    proto = [s for s in index.symbols if s["file"] == "a.h" and s["name"] == "unused_thing"]
    assert proto and proto[0]["line"] == 1 and proto[0].get("end_line", 1) >= 2, proto
    assert ("a.c", "unused_thing") in _without_callers(_run(repo, store))


def test_a_compound_name_is_not_asked_of_every_file_that_holds_its_words(tmp_path, monkeypatch):
    """Every word of `get-item-F-I` is in every file; its pairs are not. Questions must not grow with the files."""

    def repo_of(files_n):
        files = dict(PYTHON_PAIR)
        for f in range(files_n):
            body = "(ns m%d)\n" % f + "".join("(defn get-item-%d-%d [x]\n  x)\n\n" % (f, i) for i in range(10))
            body += ";; get item " + " ".join(str(k) for k in range(max(files_n, 10))) + "\n"
            files["m%d.clj" % f] = body
        return files

    asked = []
    real_names, real_lines = FileWords.names, FileWords.lines_naming
    counts = {}
    for files_n in (4, 16):
        root = tmp_path / str(files_n)
        root.mkdir()
        repo, store, _ = _index(root, repo_of(files_n))
        monkeypatch.setattr(FileWords, "names", lambda self, name: (asked.append(name), real_names(self, name))[1])
        monkeypatch.setattr(FileWords, "lines_naming", lambda self, name: (asked.append(name), real_lines(self, name))[1])
        asked.clear()
        out = _run(repo, store)
        counts[files_n] = len(asked)
        monkeypatch.setattr(FileWords, "names", real_names)
        monkeypatch.setattr(FileWords, "lines_naming", real_lines)
        assert len(_without_callers(out)) >= files_n * 10
    # Four times the functions: about four times the questions. Asking every file would be sixteen times.
    assert counts[16] <= 5 * counts[4] + 40, counts


# ---------------------------------------------------------------------------
# review of L-157: the same uncalled names defined in every file
# ---------------------------------------------------------------------------

CALLBACKS = ("init", "handle", "rules", "up", "down", "terminate", "authorize")


def _callbacks_everywhere(files_n):
    """Every file defines the same seven functions and nothing calls them by name."""
    files = dict(PYTHON_PAIR)
    for f in range(files_n):
        files["m%d.clj" % f] = "(ns m%d)\n" % f + "".join("(defn %s [x]\n  x)\n\n" % n for n in CALLBACKS)
    return files


def test_a_name_defined_in_every_file_is_worked_out_once_not_once_per_function(tmp_path, monkeypatch):
    """Seven callbacks in N files are 7N functions; asking each file per function is 7N times N questions."""
    asked = []
    real_names, real_lines = FileWords.names, FileWords.lines_naming
    counts = {}
    for files_n in (4, 16):
        root = tmp_path / str(files_n)
        root.mkdir()
        repo, store, _ = _index(root, _callbacks_everywhere(files_n))
        monkeypatch.setattr(FileWords, "names", lambda self, name: (asked.append(name), real_names(self, name))[1])
        monkeypatch.setattr(FileWords, "lines_naming", lambda self, name: (asked.append(name), real_lines(self, name))[1])
        asked.clear()
        out = _run(repo, store)
        counts[files_n] = len(asked)
        monkeypatch.setattr(FileWords, "names", real_names)
        monkeypatch.setattr(FileWords, "lines_naming", real_lines)
        dead = _without_callers(out)
        assert all(("m%d.clj" % f, n) in dead for f in range(files_n) for n in CALLBACKS), "nothing calls them"
    # Four times the files and the functions: about four times the questions, never sixteen.
    assert counts[16] <= 5 * counts[4] + 40, counts


def test_a_shared_name_that_one_file_calls_is_live_everywhere_but_the_answer_skips_the_asker(tmp_path):
    """Two files define `init`; a third calls it. Both definitions are live; a lone definer is not its own caller."""
    files = {
        "a.clj": "(ns a)\n(defn init [x]\n  x)\n",
        "b.clj": "(ns b)\n(defn init [x]\n  x)\n",
        "c.clj": "(ns c)\n(defn run []\n  (a/init 1))\n",
    }
    repo, store, _ = _index(tmp_path, {**PYTHON_PAIR, **files})
    dead = _without_callers(_run(repo, store))
    assert ("a.clj", "init") not in dead and ("b.clj", "init") not in dead, dead
    lone = tmp_path / "lone"
    lone.mkdir()
    repo, store, _ = _index(lone, {**PYTHON_PAIR, "a.clj": "(ns a)\n(defn init [x]\n  (init x))\n", "b.clj": "(ns b)\n(defn other [] 1)\n"})
    assert ("a.clj", "init") in _without_callers(_run(repo, store))


def test_a_recursive_function_called_from_a_later_file_is_live(tmp_path):
    """The first file that names `walk` beyond its declaration is its own; the caller is the second."""
    files = {
        "a.clj": "(ns a)\n(defn walk [x]\n  (walk x))\n",
        "z.clj": "(ns z)\n(defn run []\n  (a/walk 1))\n",
    }
    repo, store, _ = _index(tmp_path, {**PYTHON_PAIR, **files})
    assert ("a.clj", "walk") not in _without_callers(_run(repo, store))
