"""LEDGER L-147: the dynamic-import scan costs the same however many tables a file holds.

`_python_dynamic_imports` (#876) asked two questions per name by walking the
file's whole syntax tree each time: is this module-level literal table only
ever read, and is this loop variable bound only by its loops. It then walked
the tree again for each of its passes, and each function once per dynamic
import in it. The nightly's cold-index Floor caught the cost (#1005).

Two properties, and the second is the one a fixed number of extra walks cannot
pass: the cost does not grow with the names in the file, and the file is
walked a bounded number of times. The scan walks it once; a subtree a
question is asked about (a function whose parameter feeds an import, a
comprehension) is walked once more. `WHOLE_FILE_ONLY` shapes ask no such
question, so one walk put back anywhere fails them.

The cost is counted, never timed, and counted twice. Walks: every node
`ast.walk` visits goes through `ast.iter_child_nodes`, and every node an
`ast.NodeVisitor` visits goes through `ast.iter_fields`. The first calls the
second on Python 3.10 to 3.14, so the larger count is the number of node
visits; divided by the size of the tree, it says how many times the file was
walked. Work: the fix reads a LIST of the nodes, and a pass over that list
per name is the same defect with no tree walk in it, so the third property is
that the function calls the scan makes, per node, do not grow with the names
(`sys.setprofile`; a second review rebuilt the whole-file table per name and
every walk test stayed green).
"""

import ast
import sys

import pytest

from jcodemunch_mcp.parser import imports as imports_mod

NL = chr(10)


def _tables_file(count: int) -> str:
    lines = ["import importlib"]
    lines += ['T%d = {"a": "pkg.mod%d", "b": "pkg.other%d"}' % (k, k, k) for k in range(count)]
    lines += ["def load(key):", "    return importlib.import_module(T0[key])"]
    return NL.join(lines) + NL


def _loops_file(count: int) -> str:
    lines = ["import importlib"]
    for k in range(count):
        lines += ['for m%d in ("pkg.a%d", "pkg.b%d"):' % (k, k, k), "    importlib.import_module(m%d)" % k]
    return NL.join(lines) + NL


def _calls_in_one_function_file(count: int) -> str:
    lines = ["import importlib", "def _load(name):"]
    lines += ["    importlib.import_module(name)"] * count
    return NL.join(lines + ['_load("pkg.a")']) + NL


def _comprehensions_file(count: int) -> str:
    lines = ["import importlib"]
    lines += ['x%d = [importlib.import_module(m) for m in ("pkg.c%d",)]' % (k, k) for k in range(count)]
    return NL.join(lines) + NL


def _one_call_functions_file(count: int) -> str:
    lines = ["import importlib"]
    for k in range(count):
        lines += ["def _f%d(name):" % k, "    return importlib.import_module(name)"]
    return NL.join(lines) + NL


def _nested_functions_file(count: int) -> str:
    depth = min(count, 16)
    lines = ["import importlib"]
    lines += ["    " * k + "def _f%d(name%d):" % (k, k) for k in range(depth)]
    return NL.join(lines + ["    " * depth + "importlib.import_module(name0)"]) + NL


def _plain_functions_file(count: int) -> str:
    """Functions that hold no dynamic import: none of them is walked."""
    lines = ["import importlib", 'importlib.import_module("pkg.a")']
    for k in range(count):
        lines += ["def _g%d(name):" % k, "    return name"]
    return NL.join(lines) + NL


BUILDERS = {
    "literal_tables": _tables_file,
    "loop_variables": _loops_file,
    "calls_in_one_function": _calls_in_one_function_file,
    "comprehensions": _comprehensions_file,
    "one_call_functions": _one_call_functions_file,
    "nested_functions": _nested_functions_file,
    "plain_functions": _plain_functions_file,
}
# Shapes with no feeding parameter and no comprehension: one walk, and no
# room for a second. The others get one more, of the subtrees asked about.
WHOLE_FILE_ONLY = {"literal_tables": 1.5, "loop_variables": 1.5, "plain_functions": 1.5}
WITH_SUBTREES = 2.5


def _walks_per_node(content: str, monkeypatch) -> float:
    """Node visits the scan makes, per node of the file's tree."""
    size = sum(1 for _ in ast.walk(ast.parse(content)))
    real_children, real_fields = ast.iter_child_nodes, ast.iter_fields
    visits = {"children": 0, "fields": 0}

    def counting_children(node):
        visits["children"] += 1
        return real_children(node)

    def counting_fields(node):
        visits["fields"] += 1
        return real_fields(node)

    monkeypatch.setattr(ast, "iter_child_nodes", counting_children)
    monkeypatch.setattr(ast, "iter_fields", counting_fields)
    try:
        imports_mod._python_dynamic_imports(content, set())
    finally:
        monkeypatch.setattr(ast, "iter_child_nodes", real_children)
        monkeypatch.setattr(ast, "iter_fields", real_fields)
    assert visits["children"] > 0
    return max(visits.values()) / size


@pytest.mark.parametrize("shape", sorted(BUILDERS))
def test_the_scan_walks_a_file_no_more_times_when_it_holds_more_names(shape, monkeypatch):
    few = _walks_per_node(BUILDERS[shape](4), monkeypatch)
    many = _walks_per_node(BUILDERS[shape](64), monkeypatch)
    # Sixteen times the names; the same number of passes, give or take one.
    assert many <= few + 1, (few, many)


@pytest.mark.parametrize("shape", sorted(BUILDERS))
@pytest.mark.parametrize("count", [4, 64])
def test_the_scan_walks_a_file_a_bounded_number_of_times(shape, count, monkeypatch):
    """A walk per PASS is a fixed number of extra walks, which the test above
    cannot see: with every per-pass walk put back it passed (review of L-147)."""
    walks = _walks_per_node(BUILDERS[shape](count), monkeypatch)
    assert walks <= WHOLE_FILE_ONLY.get(shape, WITH_SUBTREES), (shape, count, walks)


def _calls_per_node(content: str) -> float:
    """Function calls the scan makes, Python and C, per node of the file's tree."""
    size = sum(1 for _ in ast.walk(ast.parse(content)))
    calls = {"n": 0}

    def profiler(frame, event, arg):
        if event in ("call", "c_call"):
            calls["n"] += 1

    previous = sys.getprofile()
    sys.setprofile(profiler)
    try:
        imports_mod._python_dynamic_imports(content, set())
    finally:
        sys.setprofile(previous)
    assert calls["n"] > 0
    return calls["n"] / size


@pytest.mark.parametrize("shape", sorted(BUILDERS))
def test_the_scan_does_no_more_work_per_node_when_the_file_holds_more_names(shape):
    """A pass over the node list per name walks no tree, so only this sees it."""
    few = _calls_per_node(BUILDERS[shape](4))
    many = _calls_per_node(BUILDERS[shape](64))
    assert many <= few * 1.5, (few, many)


def test_the_work_counter_sees_a_pass_over_the_nodes_per_name(monkeypatch):
    """Non-vacuity: one `isinstance` per node per table, and the measure doubles."""
    content = _tables_file(64)
    baseline = _calls_per_node(content)
    nodes = list(ast.walk(ast.parse(content)))
    real_scan = imports_mod._python_dynamic_imports

    def per_name(text, seen):
        for _name in range(64):
            for node in nodes:
                isinstance(node, ast.Name)
        return real_scan(text, seen)

    monkeypatch.setattr(imports_mod, "_python_dynamic_imports", per_name)
    assert _calls_per_node(content) > baseline * 1.5


@pytest.mark.parametrize("route", ["ast_walk", "node_visitor"])
def test_the_counter_sees_one_extra_walk_of_the_file(route, monkeypatch):
    """Non-vacuity for the bound: one walk put back, by either route, breaks it."""
    content = _tables_file(64)
    baseline = _walks_per_node(content, monkeypatch)
    tree = ast.parse(content)
    real_scan = imports_mod._python_dynamic_imports

    def one_walk_more(text, seen):
        if route == "ast_walk":
            for _node in ast.walk(tree):
                pass
        else:
            ast.NodeVisitor().visit(tree)
        return real_scan(text, seen)

    monkeypatch.setattr(imports_mod, "_python_dynamic_imports", one_walk_more)
    walks = _walks_per_node(content, monkeypatch)
    assert walks >= baseline + 0.9 and walks > WHOLE_FILE_ONLY["literal_tables"], (baseline, walks)


def test_the_counter_sees_a_scan_that_walks_once_per_name(monkeypatch):
    """Non-vacuity: the measure grows when a walk per name is put back."""
    content = _tables_file(64)
    baseline = _walks_per_node(content, monkeypatch)
    tree = ast.parse(content)
    names = [s.targets[0].id for s in tree.body if isinstance(s, ast.Assign)]
    real_scan = imports_mod._python_dynamic_imports

    def per_name(text, seen):
        for _name in names:
            for _node in ast.walk(tree):
                pass
        return real_scan(text, seen)

    monkeypatch.setattr(imports_mod, "_python_dynamic_imports", per_name)
    assert _walks_per_node(content, monkeypatch) > baseline + 32


def test_every_table_and_loop_still_resolves_to_its_edges():
    """The answers, unchanged: one table read by the call, every loop's values."""
    tables = imports_mod._python_dynamic_imports(_tables_file(8), set())
    assert sorted(e["specifier"] for e in tables) == ["pkg.mod0", "pkg.other0"]
    loops = imports_mod._python_dynamic_imports(_loops_file(8), set())
    assert sorted(e["specifier"] for e in loops) == sorted(
        "pkg.%s%d" % (letter, k) for k in range(8) for letter in "ab"
    )


def test_a_table_that_is_written_to_is_still_a_site_not_a_table():
    """The read-only rule the per-name walk answered, on each kind of write."""
    for write in ('T0["c"] = name', "T0.update(extra)", "U = T0", "register(T0)", "T0 = {}"):
        content = _tables_file(3) + write + NL
        edges = imports_mod._python_dynamic_imports(content, set())
        assert [e["specifier"] for e in edges] == [imports_mod.DYNAMIC_IMPORT_UNRESOLVED], (write, edges)


def test_a_loop_variable_also_bound_another_way_is_not_bounded_by_its_loop():
    content = _loops_file(2) + "m0 = name" + NL
    edges = imports_mod._python_dynamic_imports(content, set())
    specs = sorted(e["specifier"] for e in edges)
    assert "pkg.a0" not in specs and "pkg.a1" in specs and imports_mod.DYNAMIC_IMPORT_UNRESOLVED in specs, specs
