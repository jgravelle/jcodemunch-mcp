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
def test_dead_code_reads_nothing_this_changes(tmp_path, label, monkeypatch):
    """The dead-code tools never ask the caller search: their answers are
    the same with the search replaced by one that returns nothing."""
    repo, store = _index(tmp_path, DEAD_CODE_LAYOUTS[label])

    def answers():
        v2 = get_dead_code_v2(repo=repo, storage_path=store)
        v1 = find_dead_code(repo=repo, storage_path=store, min_confidence=0)
        return (
            sorted(str(s.get("symbol_id") or s.get("id")) for s in v2.get("dead_symbols", [])),
            sorted(f["file"] for f in v1.get("dead_files", [])),
            sorted(str(s.get("symbol_id") or s.get("id")) for s in v1.get("dead_symbols", [])),
        )

    before = answers()
    # Non-vacuity: the layout must report SOMETHING, or equal answers prove nothing.
    assert any(before), (label, before)
    monkeypatch.setattr(_call_graph, "find_direct_callers", lambda *a, **k: [])
    assert answers() == before
