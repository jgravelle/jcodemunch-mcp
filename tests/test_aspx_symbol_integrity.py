"""Every non-synthetic aspx symbol has a resolvable owner, a true byte slice and a hash."""
from pathlib import Path

import pytest

from jcodemunch_mcp.parser.extractor import _parse_aspx_symbols

FIXTURES = Path(__file__).parent / "fixtures" / "dotnet"
_EXTS = (".aspx", ".ascx", ".master", ".asax")
_FILES = sorted(p for p in FIXTURES.glob("webforms_*") if p.suffix in _EXTS)

INLINE = (
    '<%@ Page Language="C#" Inherits="A.B" CodeBehind="Default.aspx.cs" %>\n'
    '<%@ Register TagPrefix="uc" Src="x.ascx" %>\n'
    '<asp:Label ID="Lbl" Text="éé" runat="server" />\n'
    '<asp:Button ID="Btn" Text="ü" runat="server" />\n'
    '<script runat="server">\n'
    '  void Greet() { var s = "é"; }\n'
    '  class Helper { void Assist() { } }\n'
    '</script>\n'
    '<asp:Literal ID="After" runat="server" />\n'
)

# Invalid UTF-8 before each control (offsets must stay byte-exact) and a
# `</script >` close tag that a bare `</script>` pattern would run past.
INVALID = (
    b'<%@ Page Language="C#" %>\n'
    b'<p>\xff\xfe\xc3 broken</p>\n'
    b'<asp:Label ID="First" runat="server" />\n'
    b'\xe9\xe9\n'
    b'<asp:Button ID="Second" runat="server" />\n'
    b'<script runat="server">\n'
    b'  void Hello() { }\n'
    b'</script >\n'
    b'<asp:Literal ID="Third" runat="server" />\n'
)


def _cases():
    out = [(p.name, p.read_bytes()) for p in _FILES]
    out.append(("Inline.aspx", INLINE.encode("utf-8")))
    out.append(("Invalid.aspx", INVALID))
    return out


def _is_synthetic(sym) -> bool:
    return sym.kind == "class" and sym.parent is None and sym.byte_offset == 0 or sym.id.endswith(".codebehind#constant")


@pytest.mark.parametrize("filename,src", _cases(), ids=[c[0] for c in _cases()])
def test_aspx_symbol_integrity(filename, src):
    syms = _parse_aspx_symbols(src, filename)
    assert syms
    ids = [s.id for s in syms]
    assert len(ids) == len(set(ids)), "symbol ids must be unique"
    idset = set(ids)
    for s in syms:
        if s.parent is not None:
            assert s.parent in idset, f"{s.id}: dangling parent {s.parent}"
        if _is_synthetic(s):
            continue
        text = src[s.byte_offset:s.byte_offset + s.byte_length].decode("utf-8", errors="strict")
        assert s.name in text, f"{s.id}: slice {text!r} lacks name {s.name!r}"
        assert s.content_hash, f"{s.id}: empty content_hash"
        assert s.parent is not None, f"{s.id}: no owner"


def test_inline_sample_shape():
    syms = {s.name: s for s in _parse_aspx_symbols(INLINE.encode("utf-8"), "Inline.aspx")}
    page = syms["Inline"]
    assert syms["Lbl"].parent == page.id
    assert syms["uc"].parent == page.id
    assert syms["Greet"].parent == page.id
    assert syms["Helper"].parent == page.id
    assert syms["Assist"].parent == syms["Helper"].id
    assert not any("ServerScript" in s.qualified_name or "ServerScript" in s.id for s in syms.values())


def test_invalid_utf8_sample_shape():
    syms = {s.name: s for s in _parse_aspx_symbols(INVALID, "Invalid.aspx")}
    # `Third` survives only if the script closed at `</script >`.
    assert {"First", "Second", "Third", "Hello"} <= set(syms)
    assert syms["Third"].line == 9
    assert INVALID[syms["Third"].byte_offset:].startswith(b'<asp:Literal ID="Third"')
