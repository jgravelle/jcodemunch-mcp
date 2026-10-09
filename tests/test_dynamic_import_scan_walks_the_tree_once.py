"""LEDGER L-147: the dynamic-import scan costs the same however many tables a file holds.

`_python_dynamic_imports` (#876) asked two questions per name by walking the
file's whole syntax tree each time: is this module-level literal table only
ever read, and is this loop variable bound only by its loops. A file with forty
literal tables was walked more than eighty times, for an answer about one
`import_module` call. On this repository's own `src/` that was a quarter of a
cold index, and the nightly's cold-index Floor caught it (#1005).

The cost is counted, never timed: every node `ast.walk` visits goes through
`ast.iter_child_nodes`, so the number of calls is the number of node visits.
Divided by the size of the tree, it says how many times the file was walked.
"""

import ast

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


def _walks_per_node(content: str, monkeypatch) -> float:
    """Node visits the scan makes, per node of the file's tree."""
    size = sum(1 for _ in ast.walk(ast.parse(content)))
    real = ast.iter_child_nodes
    visits = {"n": 0}

    def counting(node):
        visits["n"] += 1
        return real(node)

    monkeypatch.setattr(ast, "iter_child_nodes", counting)
    try:
        imports_mod._python_dynamic_imports(content, set())
    finally:
        monkeypatch.setattr(ast, "iter_child_nodes", real)
    assert visits["n"] > 0
    return visits["n"] / size


@pytest.mark.parametrize("build", [_tables_file, _loops_file], ids=["literal_tables", "loop_variables"])
def test_the_scan_walks_a_file_no_more_times_when_it_holds_more_names(build, monkeypatch):
    few = _walks_per_node(build(4), monkeypatch)
    many = _walks_per_node(build(64), monkeypatch)
    # Sixteen times the names; the same number of passes, give or take one.
    assert many <= few + 1, (few, many)


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
