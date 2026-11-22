"""A C or C++ caller is found by the text search wherever v1 can see it (LEDGER L-339).

v1 records no C/C++ call references, so `get_call_hierarchy` finds callers by
text: it reads the files that import the definition's file, and the files that
import a header the definition's own file includes. Four layouts returned no
caller at all, with nothing in the response saying the list could be short:

- the caller sits in the definition's own file;
- the caller includes an umbrella header that includes the declaring one;
- the definition's file does not include its own header;
- the caller writes its own prototype and includes no header.

The fix stays a text match (`text_matched`), follows header-to-header includes
only up to a disclosed hop cap, and takes the last two layouts only when the
name has exactly one non-static definition. A C/C++ callers answer now says it
came from a text match, at the top level, where a default install keeps it.
The full include-chain rule is v2 (V2-BACKLOG row 6).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jcodemunch_mcp.tools import _call_graph
from jcodemunch_mcp.tools.find_dead_code import find_dead_code
from jcodemunch_mcp.tools.get_call_hierarchy import get_call_hierarchy
from jcodemunch_mcp.tools.get_dead_code_v2 import get_dead_code_v2
from jcodemunch_mcp.tools.index_folder import index_folder


def _index(tmp_path, files: dict[str, str]):
    src = tmp_path / "src"
    for rel, text in files.items():
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    store = tmp_path / "store"
    r = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert r["success"], r
    return r["repo"], str(store)


def _hierarchy(repo, store, symbol_id):
    h = get_call_hierarchy(repo=repo, symbol_id=symbol_id, direction="callers", storage_path=store)
    assert "error" not in h, h
    return h


def _callers(repo, store, symbol_id) -> dict[str, str]:
    return {c["id"]: c["resolution"] for c in _hierarchy(repo, store, symbol_id)["callers"]}


# label -> (files, the definition asked about, the caller it must show)
REACHED = {
    "a caller in the same file": (
        {"a.c": "int twice(int x) { return 2 * x; }\nint use(void) { return twice(3); }\n"},
        "a.c::twice#function",
        "a.c::use#function",
    ),
    "a static function's caller in its own file": (
        {"a.c": "static int twice(int x) { return 2 * x; }\nint use(void) { return twice(3); }\n"},
        "a.c::twice#function",
        "a.c::use#function",
    ),
    "an umbrella header": (
        {
            "util.h": "int helper(int x);\n",
            "all.h": '#include "util.h"\n',
            "util.c": '#include "util.h"\nint helper(int x) { return x; }\n',
            "main.c": '#include "all.h"\nint run(void) { return helper(1); }\n',
        },
        "util.c::helper#function",
        "main.c::run#function",
    ),
    "a definition that skips its header": (
        {
            "util.h": "int helper(int x);\n",
            "impl.c": "int helper(int x) { return x; }\n",
            "main.c": '#include "util.h"\nint run(void) { return helper(1); }\n',
        },
        "impl.c::helper#function",
        "main.c::run#function",
    ),
    "a prototype in the caller": (
        {
            "impl.c": "int helper(int x) { return x; }\n",
            "main.c": "extern int helper(int x);\nint run(void) { return helper(1); }\n",
        },
        "impl.c::helper#function",
        "main.c::run#function",
    ),
    "a template in a header behind an umbrella": (
        {
            "t.hpp": "template <typename T> T twice(T x) { return x + x; }\n",
            "all.hpp": '#include "t.hpp"\n',
            "main.cpp": '#include "all.hpp"\nint run() { return twice<int>(2); }\n',
        },
        "t.hpp::twice#function",
        "main.cpp::run#function",
    ),
    # Controls: v1 found these before the fix and must still.
    "a header of the same stem": (
        {
            "util.h": "int helper(int x);\n",
            "util.c": '#include "util.h"\nint helper(int x) { return x; }\n',
            "main.c": '#include "util.h"\nint run(void) { return helper(1); }\n',
        },
        "util.c::helper#function",
        "main.c::run#function",
    ),
    "a header of another stem": (
        {
            "server.h": "int f(int x);\n",
            "db.c": '#include "server.h"\nint f(int x) { return x; }\n',
            "net.c": '#include "server.h"\nint g(void) { return f(1); }\n',
        },
        "db.c::f#function",
        "net.c::g#function",
    ),
}


@pytest.mark.parametrize("label", sorted(REACHED))
def test_a_definition_shows_the_caller_v1_can_see(tmp_path, label):
    files, definition, caller = REACHED[label]
    repo, store = _index(tmp_path, files)
    assert _callers(repo, store, definition).get(caller) == "text_matched", label


def test_two_definitions_and_no_including_one_take_nothing(tmp_path):
    """Neither definition includes the declaring header, so nothing says which one is called."""
    repo, store = _index(tmp_path, {
        "api.h": "int start(void);\n",
        "a/impl.c": "int start(void) { return 1; }\n",
        "b/impl.c": "int start(void) { return 2; }\n",
        "main.c": '#include "api.h"\nint run(void) { return start(); }\n',
    })
    assert _callers(repo, store, "a/impl.c::start#function") == {}
    assert _callers(repo, store, "b/impl.c::start#function") == {}


def test_two_definitions_and_a_prototype_take_nothing(tmp_path):
    repo, store = _index(tmp_path, {
        "a/impl.c": "int start(void) { return 1; }\n",
        "b/impl.c": "int start(void) { return 2; }\n",
        "main.c": "extern int start(void);\nint run(void) { return start(); }\n",
    })
    assert _callers(repo, store, "a/impl.c::start#function") == {}
    assert _callers(repo, store, "b/impl.c::start#function") == {}


def test_the_definition_that_includes_the_header_takes_the_callers(tmp_path):
    repo, store = _index(tmp_path, {
        "api.h": "int start(void);\n",
        "a/impl.c": '#include "../api.h"\nint start(void) { return 1; }\n',
        "b/impl.c": "int start(void) { return 2; }\n",
        "main.c": '#include "api.h"\nint run(void) { return start(); }\n',
    })
    assert "main.c::run#function" in _callers(repo, store, "a/impl.c::start#function")
    assert _callers(repo, store, "b/impl.c::start#function") == {}


def test_a_file_that_sees_no_declaration_is_not_a_caller(tmp_path):
    repo, store = _index(tmp_path, {
        "util.h": "int helper(int x);\n",
        "util.c": '#include "util.h"\nint helper(int x) { return x; }\n',
        "stray.c": "int run(void) { return helper(1); }\n",
    })
    assert _callers(repo, store, "util.c::helper#function") == {}


def test_a_static_function_takes_no_caller_from_another_file(tmp_path):
    repo, store = _index(tmp_path, {
        "x.h": "int twice(int x);\n",
        "a.c": '#include "x.h"\nstatic int twice(int x) { return 2 * x; }\nint use(void) { return twice(3); }\n',
        "b.c": '#include "x.h"\nint other(void) { return twice(4); }\n',
    })
    assert _callers(repo, store, "a.c::twice#function") == {"a.c::use#function": "text_matched"}


def test_a_method_of_another_class_with_the_same_name_takes_nothing(tmp_path):
    repo, store = _index(tmp_path, {
        "w.hpp": "class Widget {\n public:\n  void draw();\n};\n",
        "g.hpp": "class Gadget {\n public:\n  void draw();\n};\n",
        "w.cpp": '#include "w.hpp"\nvoid Widget::draw() {}\n',
        "g.cpp": '#include "g.hpp"\nvoid Gadget::draw() {}\n',
        "main.cpp": '#include "w.hpp"\nint run(Widget &w) { w.draw(); return 0; }\n',
    })
    assert "main.cpp::run#function" in _callers(repo, store, "w.cpp::Widget.draw#method")
    assert _callers(repo, store, "g.cpp::Gadget.draw#method") == {}


def _chain(n: int) -> dict[str, str]:
    """`main.c` reaches `util.h` through `n` umbrella headers."""
    files = {
        "util.h": "int helper(int x);\n",
        "util.c": '#include "util.h"\nint helper(int x) { return x; }\n',
        "u0.h": '#include "util.h"\n',
    }
    for i in range(1, n):
        files[f"u{i}.h"] = f'#include "u{i - 1}.h"\n'
    files["main.c"] = f'#include "u{n - 1}.h"\nint run(void) {{ return helper(1); }}\n'
    return files


def test_the_umbrella_walk_stops_at_the_hop_cap_and_says_so(tmp_path):
    cap = _call_graph.C_UMBRELLA_HOPS
    repo, store = _index(tmp_path / "in", _chain(cap))
    inside = _hierarchy(repo, store, "util.c::helper#function")
    assert "main.c::run#function" in {c["id"] for c in inside["callers"]}
    assert inside["callers_text_only"]["umbrella_hop_cap"] == cap
    assert inside["callers_text_only"]["umbrella_hop_cap_reached"] is False

    repo, store = _index(tmp_path / "out", _chain(cap + 1))
    beyond = _hierarchy(repo, store, "util.c::helper#function")
    assert beyond["callers"] == []
    assert beyond["callers_text_only"]["umbrella_hop_cap_reached"] is True


def test_an_empty_c_answer_says_it_came_from_a_text_match(tmp_path):
    """L-59's lesson: an empty list must not read as a confirmed zero. Top
    level, because `_meta` is stripped on a default install."""
    repo, store = _index(tmp_path, {
        "util.c": "int helper(int x) { return x; }\n",
        "stray.c": "int run(void) { return helper(1); }\n",
    })
    h = _hierarchy(repo, store, "util.c::helper#function")
    assert h["callers"] == []
    note = h["callers_text_only"]
    assert note["basis"] == "text_matched"
    assert note["may_miss_callers"] is True
    assert "text" in note["detail"]


def test_a_non_c_symbol_carries_no_text_only_note(tmp_path):
    repo, store = _index(tmp_path, {
        "a.py": "def twice(x):\n    return 2 * x\n\n\ndef use():\n    return twice(3)\n",
    })
    assert "callers_text_only" not in _hierarchy(repo, store, "a.py::twice#function")


DEAD_CODE_LAYOUTS = {k: REACHED[k][0] for k in REACHED} | {
    "a stray caller": {
        "util.c": "int helper(int x) { return x; }\n",
        "stray.c": "int run(void) { return helper(1); }\n",
    },
}


@pytest.mark.parametrize("label", sorted(DEAD_CODE_LAYOUTS))
def test_dead_code_never_consults_the_caller_reach(tmp_path, label, monkeypatch):
    """The dead-code tools do not read the caller search this change widens.

    The reach rule is replaced by one that raises, so a dead-code tool that
    ever consults it (through any import) fails here. `find_direct_callers`
    looks `c_text_reach` up through module globals, so the patch reaches it.
    """
    repo, store = _index(tmp_path, DEAD_CODE_LAYOUTS[label])

    def answers():
        v2 = get_dead_code_v2(repo=repo, storage_path=store)
        v1 = find_dead_code(repo=repo, storage_path=store, min_confidence=0)
        return (
            sorted(str(s.get("symbol_id") or s.get("id")) for s in v2.get("dead_symbols", [])),
            sorted(f["file"] for f in v1.get("dead_files", [])),
        )

    before = answers()
    assert any(before), (label, before)

    def refuse(*_a, **_k):
        raise AssertionError("a dead-code tool consulted the C caller reach")

    monkeypatch.setattr(_call_graph, "c_text_reach", refuse)
    assert answers() == before


def test_the_raising_reach_is_seen_by_the_caller_search(tmp_path, monkeypatch):
    """Non-vacuity for the test above: the same patch does reach the search."""
    files, definition, _caller = REACHED["an umbrella header"]
    repo, store = _index(tmp_path, files)

    def refuse(*_a, **_k):
        raise AssertionError("consulted")

    monkeypatch.setattr(_call_graph, "c_text_reach", refuse)
    with pytest.raises(AssertionError, match="consulted"):
        get_call_hierarchy(repo=repo, symbol_id=definition, direction="callers", storage_path=store)


# C++: a bare name does not identify a function (review of L-339). The two
# one-definition rules are C only, so none of these take another f's caller.
CPP_SAME_BARE_NAME = {
    "another namespace": (
        {
            "a.hpp": "namespace a { void f(); }\n",
            "b.cpp": "namespace b { void f() {} }\n",
            "main.cpp": '#include "a.hpp"\nint run() { a::f(); return 0; }\n',
        },
        "b.cpp::b.f#function",
    ),
    "an anonymous namespace": (
        {
            "a.hpp": "void f();\n",
            "b.cpp": "namespace { void f() {} }\nvoid f();\n",
            "main.cpp": '#include "a.hpp"\nint run() { f(); return 0; }\n',
        },
        None,
    ),
    "an overload": (
        {
            "a.hpp": "void f(int x);\n",
            "b.cpp": "void f(double x) {}\n",
            "main.cpp": '#include "a.hpp"\nint run() { f(1); return 0; }\n',
        },
        None,
    ),
}


@pytest.mark.parametrize("label", sorted(CPP_SAME_BARE_NAME))
def test_a_cpp_function_of_the_same_bare_name_takes_no_caller(tmp_path, label):
    files, definition = CPP_SAME_BARE_NAME[label]
    repo, store = _index(tmp_path, files)
    if definition is None:
        from jcodemunch_mcp.storage import IndexStore

        owner, name = repo.split("/", 1)
        idx = IndexStore(base_path=store).load_index(owner, name)
        defs = [
            s["id"] for s in idx.symbols
            if s["file"] == "b.cpp" and s["name"] == "f"
            and not (s.get("signature") or "").rstrip().endswith(";")
        ]
        assert defs, idx.symbols
        definition = defs[0]
    assert "main.cpp::run#function" not in _callers(repo, store, definition), label


def test_the_compact_form_keeps_the_text_only_block(tmp_path):
    """The `adaptive` default picks the compact encoding whenever it is
    smaller, so the block must survive it."""
    from jcodemunch_mcp.encoding.schemas import get_call_hierarchy as sch

    repo, store = _index(tmp_path, {
        "util.c": "int helper(int x) { return x; }\n",
        "stray.c": "int run(void) { return helper(1); }\n",
    })
    h = _hierarchy(repo, store, "util.c::helper#function")
    payload, _enc = sch.encode("get_call_hierarchy", h)
    assert sch.decode(payload)["callers_text_only"] == h["callers_text_only"]


def test_a_header_past_the_cap_that_nothing_includes_does_not_raise_the_flag(tmp_path):
    cap = _call_graph.C_UMBRELLA_HOPS
    files = _chain(cap)
    files[f"u{cap}.h"] = f'#include "u{cap - 1}.h"\n'
    repo, store = _index(tmp_path, files)
    h = _hierarchy(repo, store, "util.c::helper#function")
    assert "main.c::run#function" in {c["id"] for c in h["callers"]}
    assert h["callers_text_only"]["umbrella_hop_cap_reached"] is False


def test_a_static_answer_says_only_its_own_file_is_read(tmp_path):
    repo, store = _index(tmp_path, REACHED["a static function's caller in its own file"][0])
    detail = _hierarchy(repo, store, "a.c::twice#function")["callers_text_only"]["detail"]
    assert "static" in detail and "own file" in detail


def test_the_name_table_slot_never_pairs_one_map_with_another_maps_table(monkeypatch):
    """Review of L-339: tool calls run on worker threads, and a slot written
    in two steps let a slow build for map A land under map B's key. The
    interleaving is pinned: A's build blocks until B has been served."""
    import threading

    a = {"a.c": [{"id": "a.c::f#function", "name": "f", "kind": "function", "signature": "int f(void)"}]}
    b = {"b.c": [{"id": "b.c::g#function", "name": "g", "kind": "function", "signature": "int g(void)"}]}
    real = _call_graph._CNameTable
    a_building = threading.Event()
    b_served = threading.Event()

    def slow(symbols_by_file):
        if symbols_by_file is a:
            a_building.set()
            assert b_served.wait(10)
        return real(symbols_by_file)

    monkeypatch.setattr(_call_graph, "_CNameTable", slow)
    monkeypatch.setattr(_call_graph, "_NAME_TABLE", (None, None))
    got = {}
    worker = threading.Thread(target=lambda: got.setdefault("a", _call_graph._name_table(a)))
    worker.start()
    assert a_building.wait(10)
    got["b"] = _call_graph._name_table(b)
    b_served.set()
    worker.join(10)
    assert not worker.is_alive()
    assert set(got["a"].definitions) == {"f"}
    assert set(got["b"].definitions) == {"g"}
    # B's next node: whatever the slot holds, it never hands B A's table.
    assert set(_call_graph._name_table(b).definitions) == {"g"}
    assert set(_call_graph._name_table(a).definitions) == {"f"}


def test_no_dead_code_module_imports_the_caller_search():
    """A bound import would bypass the raising patch above, so the absence of
    the import is asserted over the syntax tree, not by patching."""
    import ast
    import jcodemunch_mcp.tools.find_dead_code as fdc
    import jcodemunch_mcp.tools.get_dead_code_v2 as v2

    searched = {"c_text_reach", "find_direct_callers", "bfs_callers"}
    for mod in (fdc, v2):
        tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
        names = {
            a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names
        } | {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        assert not names & searched, (mod.__name__, names & searched)
