"""A function assigned to a member at module scope is a symbol (LEDGER L-263).

CommonJS code defines most of its API by assignment: express's
`res.send = function send(body) {...}`, `exports.compileTrust = function ...`,
and the prototype pattern `Foo.prototype.bar = function () {...}`. None of
these produced a symbol, so a search for `send` in express lib/ found only the
npm import of the `send` package.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.parser import parse_file


def _symbols(src: str, filename: str = "lib/response.js", language: str = "javascript"):
    return {(s.kind, s.qualified_name): s for s in parse_file(src, filename, language)}


@pytest.mark.parametrize(
    "src, kind, qualified",
    [
        (
            "var res = {};\nres.send = function send(body) { return body; };\n",
            "method",
            "res.send",
        ),
        (
            "var res = {};\nres.json = function (obj) { return obj; };\n",
            "method",
            "res.json",
        ),
        ("var app = {};\napp.listen = (port) => port;\n", "method", "app.listen"),
        (
            "exports.compileTrust = function (v) { return v; };\n",
            "function",
            "compileTrust",
        ),
        (
            "module.exports.normalizeType = function normalizeType(t) { return t; };\n",
            "function",
            "normalizeType",
        ),
        (
            "function Foo() {}\nFoo.prototype.bar = function () { return 1; };\n",
            "method",
            "Foo.bar",
        ),
        (
            "function Foo() {}\nFoo.baz = function* () { yield 1; };\n",
            "method",
            "Foo.baz",
        ),
        ("var a = {b: {}};\na.b.c = () => 3;\n", "method", "a.b.c"),
    ],
)
def test_a_member_assigned_function_is_a_symbol(src, kind, qualified):
    syms = _symbols(src)
    assert (kind, qualified) in syms, sorted(syms)
    sym = syms[(kind, qualified)]
    assert sym.name == qualified.rsplit(".", 1)[-1]
    assert sym.line == src.count("\n")  # the assignment is the last line


def test_the_span_is_the_whole_assignment_statement():
    src = "var res = {};\n/** Send a body. */\nres.send = function send(body) {\n  return body;\n};\nvar x = 1;\n"
    sym = _symbols(src)[("method", "res.send")]
    assert (sym.line, sym.end_line) == (3, 5)
    assert "Send a body" in sym.docstring
    assert sym.signature.startswith("res.send = function send(body)")


def test_an_umd_wrapper_body_counts_as_module_scope():
    src = "(function () {\n  var api = {};\n  api.run = function () { return 1; };\n})();\n"
    assert ("method", "api.run") in _symbols(src)


def test_typescript_assignments_are_symbols_too():
    src = "const res: any = {};\nres.send = (body: string) => body;\n"
    assert ("method", "res.send") in _symbols(src, "lib/r.ts", "typescript")


@pytest.mark.parametrize(
    "src",
    [
        "var res = {};\nres.count = 1;\n",  # a value, not a function
        "var res = {};\nres['send'] = function () {};\n",  # a computed key
        "var o = {};\no.handler = makeHandler();\n",  # a call result
        "function outer() {\n  var o = {};\n  o.inner = function () {};\n}\n",  # a local inside a function
        "module.exports = function () {};\n",  # the default export is not a member
    ],
)
def test_no_symbol_without_a_named_function_assignment_at_module_scope(src):
    syms = _symbols(src)
    assert not [
        k
        for k in syms
        if k[0] in ("function", "method") and "." in k[1] and k[1] != "outer"
    ], sorted(syms)
    assert ("method", "o.inner") not in syms


@pytest.mark.parametrize(
    "src",
    [
        # A callback is not module scope, though no symbol encloses it.
        "app.get('/', function (req, res) {\n  res.locals.fmt = function () {};\n});\n",
        # Nor is the body of a function bound by `const`.
        "const make = function () {\n  var o = {};\n  o.run = () => 1;\n  return o;\n};\n",
    ],
)
def test_an_assignment_inside_a_non_iife_function_is_not_a_symbol(src):
    syms = _symbols(src)
    assert not [k for k in syms if k[1] in ("res.locals.fmt", "o.run")], sorted(syms)


def test_an_existing_declaration_keeps_its_id():
    """`function send() {}` plus `res.send = send` must not move the function's id."""
    src = "function send(b) { return b; }\nvar res = {};\nres.send = send;\n"
    syms = _symbols(src)
    assert ("function", "send") in syms
    assert syms[("function", "send")].id.endswith("::send#function")
