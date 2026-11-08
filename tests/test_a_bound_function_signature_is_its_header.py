"""A function bound by a declaration, an assignment or a class field has its header as its signature.

`const f = function (a) {...}`, `const g = (a) => {...}`,
`res.send = function send(body) {...}` and the class field
`handler = (e) => {...}` stored the whole statement as their signature,
body included, because the statement node has no `body` field. A plain
`function f(a)` stores only its header. Every search row for such a
function carried all of its lines, and words in the body matched searches.
Found through L-263's token benchmark, whose new member-assigned symbols
inherited it (LEDGER L-291).
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.parser import parse_file

_LANGS = [("javascript", "a.js"), ("typescript", "a.ts"), ("tsx", "a.tsx")]

_SRC = (
    "const send = function send(body) {\n  var secretword = body;\n  return secretword;\n};\n"
    "const arrow = (x) => {\n  return x * 2;\n};\n"
    "export const ex = async (a) => { return a; };\n"
    "var res = {};\n"
    "res.json = function (obj) {\n  return obj;\n};\n"
    "function plain(y) {\n  return y;\n}\n"
)


def _sigs(language, filename):
    return {s.qualified_name: s.signature for s in parse_file(_SRC, filename, language)}


@pytest.mark.parametrize("language, filename", _LANGS)
def test_a_bound_function_signature_stops_at_its_body(language, filename):
    sigs = _sigs(language, filename)
    assert sigs["send"] == "const send = function send(body)"
    assert sigs["arrow"] == "const arrow = (x) =>"
    assert sigs["ex"] == "export const ex = async (a) =>"
    assert sigs["res.json"] == "res.json = function (obj)"


@pytest.mark.parametrize("language, filename", _LANGS)
def test_no_signature_carries_a_body_line(language, filename):
    for name, sig in _sigs(language, filename).items():
        assert "return" not in sig and "secretword" not in sig, (name, sig)


_CLASS_SRC = (
    "class C {\n"
    "  handler = (e) => {\n    var secretword = e;\n    return secretword;\n  };\n"
    "  static s = function () { return 1; };\n"
    "  count = 0;\n"
    "  m(x) { return x; }\n"
    "}\n"
)


@pytest.mark.parametrize("language, filename", _LANGS)
def test_a_class_field_function_signature_stops_at_its_body(language, filename):
    """The third channel that binds a function (review round 1)."""
    sigs = {s.qualified_name: s.signature for s in parse_file(_CLASS_SRC, filename, language)}
    assert sigs["C.handler"] == "handler = (e) =>"
    assert sigs["C.s"] == "static s = function ()"
    assert sigs["C.count"] == "count = 0"
    assert sigs["C.m"] == "m(x)"


@pytest.mark.parametrize("language, filename", _LANGS)
def test_a_function_field_that_shadows_a_method_gets_its_header_too(language, filename):
    """It is published as a field (#781) but still binds a function (review round 2)."""
    src = "class C {\n  m = () => {\n    return 2;\n  };\n  m() { return 1; }\n}\n"
    fields = [s for s in parse_file(src, filename, language) if s.qualified_name == "C.m" and s.kind == "field"]
    assert [s.signature for s in fields] == ["m = () =>"]


@pytest.mark.parametrize("language, filename", _LANGS)
def test_a_plain_declaration_is_unchanged(language, filename):
    assert _sigs(language, filename)["plain"] == "function plain(y)"


@pytest.mark.parametrize("language, filename", _LANGS)
def test_the_span_still_covers_the_whole_function(language, filename):
    syms = {s.qualified_name: s for s in parse_file(_SRC, filename, language)}
    send = syms["send"]
    text = _SRC.encode()[send.byte_offset:send.byte_offset + send.byte_length].decode()
    assert text.startswith("const send") and "return secretword;" in text and text.rstrip().endswith("};")
