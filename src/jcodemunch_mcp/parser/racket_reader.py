"""A Racket reader in Python: bytes in, a tree of ``RacketNode`` out.

Why this exists
---------------
Racket source is not defined by a grammar; it is defined by a READER, and a
``#lang`` line can replace that reader. tree-sitter-racket implements one
reader (the default S-expression one) as a grammar, which fails in two ways
the walker in ``extractor.py`` could not repair from the outside:

* ``#lang at-exp`` text bodies (``@cmd[datum]{text}``) are prose to Racket
  and tokens to the grammar. A ``"`` inside one swallowed every definition
  after it in the file; a ``;`` made the enclosing form an error.
* On any error, tree-sitter RECOVERS by re-parenting, and the recovered tree
  put internal definitions at module level -- a fabricated, importable
  binding, which is the one thing an index must never claim.

This module reads the default reader syntax (the "Reader" chapter of the
Racket reference) and, when asked, the at-exp extension exactly as
``scribble/reader`` implements it: ``@`` as a non-terminating dispatch
character, ``{...}`` bodies with balanced braces, ``|<punct>{ ... }<punct>|``
alternate delimiters, ``@|expr ...|`` escapes, ``@"string"`` escapes, and
``@;`` comments in both spellings. Errors do not recover; they RESYNCHRONISE
(see ``_read_program``) so a stray paren at line 40 does not cost the
definitions at line 200, and the broken span is an ``ERROR`` node the walker
already skips.

Node shape
----------
The tree mirrors tree-sitter-racket's -- the same ``type`` names, comments as
named children wherever they occur, ``dot`` nodes inside lists, quote forms
as wrapper nodes, ``#(...)`` as ``vector`` around ``list`` -- so the symbol
walker consumes it unchanged. An at-form ``@cmd[d]{t}`` is a ``list`` node
(that is what ``read`` produces: ``(cmd d "t")``) spanning from the ``@`` to
the closing brace, flagged ``at_form``; a command-only form (``@foo``,
``@(expr)``, ``@|x|``) is just the command node, with its own span. Both are
what ``read-syntax`` reports.

What is NOT modelled, and why it does not matter here
-----------------------------------------------------
The at-exp reader splits a text body into per-line strings, ``"\\n"``
strings and synthesised indentation strings. The walker never looks inside a
body, so text runs are emitted as ``string`` nodes on a coarser split. The
reader-fidelity harness compares text bodies by SPAN for that reason.

Positions are byte offsets; ``start_point``/``end_point`` are (row, byte
column) as tree-sitter reports them.
"""
from __future__ import annotations

import re
from bisect import bisect_right
from typing import Optional

from . import parse_budget

__all__ = ["RacketNode", "RacketTree", "RacketReadError", "read_racket", "READER_GENERATION"]

#: Bump when the reader changes what UNCHANGED `.rkt` bytes yield. Stamped on
#: every local index that holds Racket files (`CodeIndex.racket_reader_generation`)
#: and compared at the next index, so the change reaches exactly those indexes
#: with one full re-parse -- the Racket-local shape of `PARSER_GENERATION`,
#: which would re-parse every language for everybody. 1 is the first reader;
#: an index with no stamp was parsed by tree-sitter.
READER_GENERATION = 1


class RacketReadError(Exception):
    """A read error at byte ``pos``. Raised inside the reader, RECORDED (never
    propagated) by ``read_racket``: see ``RacketTree.errors``."""

    def __init__(self, pos: int, message: str) -> None:
        super().__init__(f"{message} at byte {pos}")
        self.pos = pos
        self.message = message


class RacketNode:
    """One node. The attribute surface is the subset of tree-sitter's
    ``Node`` the Racket walker uses, with the same semantics."""

    __slots__ = ("type", "start_byte", "end_byte", "children", "parent",
                 "at_form", "_tree", "_index")

    is_named = True

    def __init__(self, type_: str, start: int, end: int, children=None) -> None:
        self.type = type_
        self.start_byte = start
        self.end_byte = end
        self.children: list[RacketNode] = children if children is not None else []
        self.parent: Optional[RacketNode] = None
        self.at_form = False
        self._tree: Optional[RacketTree] = None
        self._index = 0

    # -- tree-sitter surface ------------------------------------------------
    @property
    def text(self) -> bytes:
        return self._tree.source[self.start_byte:self.end_byte]

    @property
    def start_point(self) -> tuple[int, int]:
        return self._tree.point(self.start_byte)

    @property
    def end_point(self) -> tuple[int, int]:
        return self._tree.point(self.end_byte)

    @property
    def named_children(self) -> list["RacketNode"]:
        return self.children

    @property
    def child_count(self) -> int:
        return len(self.children)

    @property
    def prev_named_sibling(self) -> Optional["RacketNode"]:
        if self.parent is None or self._index == 0:
            return None
        return self.parent.children[self._index - 1]

    @property
    def next_named_sibling(self) -> Optional["RacketNode"]:
        if self.parent is None or self._index + 1 >= len(self.parent.children):
            return None
        return self.parent.children[self._index + 1]

    @property
    def has_error(self) -> bool:
        """On the root: did the read record ANY error -- including a
        mismatched closer, which keeps its form and leaves no ``ERROR`` node.
        Elsewhere: is there an ``ERROR`` node in this subtree."""
        if self.parent is None and self._tree is not None:
            return bool(self._tree.errors)
        if self.type == "ERROR":
            return True
        return any(c.has_error for c in self.children)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{self.type} [{self.start_byte}:{self.end_byte}]>"


class RacketTree:
    """The result of one read. ``errors`` is every ``RacketReadError`` the
    reader hit, in source order; ``root_node.has_error`` is its emptiness."""

    __slots__ = ("source", "root_node", "errors", "_line_starts")

    def __init__(self, source: bytes, root: RacketNode, errors: list[RacketReadError]) -> None:
        self.source = source
        self.root_node = root
        self.errors = errors
        self._line_starts = [0] + [m.end() for m in re.finditer(b"\n", source)]
        self._adopt(root)

    def _adopt(self, node: RacketNode) -> None:
        stack = [node]
        while stack:
            parse_budget.checkpoint()
            n = stack.pop()
            n._tree = self
            for i, c in enumerate(n.children):
                parse_budget.checkpoint()
                c.parent = n
                c._index = i
                stack.append(c)

    def point(self, byte: int) -> tuple[int, int]:
        row = bisect_right(self._line_starts, byte) - 1
        return row, byte - self._line_starts[row]


# ---------------------------------------------------------------------------
# Lexical tables. Bytes throughout: positions must be byte offsets, and a
# UTF-8 continuation byte is never a delimiter, so byte-level scanning is
# exact for everything ASCII and correct-by-construction for the rest.

# Whitespace per `char-whitespace?`, plus the BOM, in UTF-8.
_WS_ALT = (rb"[\t\n\v\f\r ]|\xc2[\x85\xa0]|\xe1\x9a\x80|\xe2\x80[\x80-\x8a\xa8\xa9]"
           rb"|\xe2\x81\x9f|\xe3\x80\x80|\xef\xbb\xbf")
_WS = re.compile(rb"(?:" + _WS_ALT + rb")+")

# A delimited sequence: anything but whitespace and ( ) [ ] { } " , ' ` ;
# with `\x` and `|...|` verbatim. `#`, `@` and `.` are ordinary constituents
# past the first character; the first character is dispatched by the caller.
_SYM_BODY = (rb"[^\t\n\v\f\r ()\[\]{}\"',`;|\\\xc2\xe1\xe2\xe3\xef]"
             rb"|\\[\s\S]|\|[^|]*\|"
             rb"|\xc2(?![\x85\xa0])|\xe1(?!\x9a\x80)"
             rb"|\xe2(?!\x80[\x80-\x8a\xa8\xa9]|\x81\x9f)|\xe3(?!\x80\x80)|\xef(?!\xbb\xbf)")
_SYM = re.compile(rb"(?:" + _SYM_BODY + rb")+")
# The at-exp COMMAND readtable makes `|` terminating (so `@foo|{` ends the
# command at `foo`); `\` still escapes.
_SYM_CMD = re.compile(rb"(?:" + _SYM_BODY.replace(rb"|\|[^|]*\|", rb"") + rb")+")

_BAR_SYMBOL = re.compile(rb"\|[^|]*\|")
_STRING = re.compile(rb'"(?:[^"\\]|\\[\s\S])*"')
_LINE_REST = re.compile(rb"(?:[^\n\r\xc2\xe2]|\xc2(?!\x85)|\xe2(?!\x80[\xa8\xa9]))*")
_SHEBANG = re.compile(rb"#![ /](?:[^\n\\]|\\[\s\S])*")
_LANG = re.compile(rb"#lang ([A-Za-z0-9+_/-]+)")
_LANG_ALIAS = re.compile(rb"#!([A-Za-z0-9+_-][A-Za-z0-9+_/-]*)")
#: `#lang` names whose reader consumes ONE more module path (`#lang at-exp
#: racket/base`, `#lang s-exp "x.rkt"`). Read as part of the `extension`
#: node; otherwise `racket/base` would be a symbol at module level.
LANG_TAKES_ARGUMENT = frozenset({
    "at-exp", "pollen/mode", "s-exp", "reader", "debug", "errortrace", "profile",
})
_HERE = re.compile(rb"#<<([^\n]*)\n")
_CHAR = re.compile(rb"#\\(?:u[0-9a-fA-F]{1,4}|U[0-9a-fA-F]{1,8}|[0-7]{3}|[A-Za-z]+"
                   rb"|[\x00-\x7f]|[\xc0-\xf7][\x80-\xbf]*)")
_VECTOR = re.compile(rb"#(?:[fF][lx])?[0-9]*(?=[(\[{])")
_HASH = re.compile(rb"#hash(?:eqv|eq|alw)?(?=[(\[{])")
_GRAPH = re.compile(rb"#([0-9]+)([=#])")
_BOOL = re.compile(rb"#(?:true|false|[tTfF])(?=" + _WS_ALT + rb"|[()\[\]{}\"',`;]|$)")
_NUM_PREFIX = re.compile(rb"(?:#[eEiI](?:#[xXoObBdD])?|#[xXoObBdD](?:#[eEiI])?)")
_ALT_OPEN = re.compile(rb"\|([^a-zA-Z0-9 \t\r\n\f@\\\x7f-\xff{]*)\{")
_AT_PREFIX = re.compile(rb"#?(?:'|`|,@?)")
_TEXT_EOL = re.compile(rb"[ \t]*\r?\n[ \t]*")

_QUOTE_TYPES = {
    b"'": "quote", b"`": "quasiquote", b",": "unquote", b",@": "unquote_splicing",
    b"#'": "syntax", b"#`": "quasisyntax", b"#,": "unsyntax", b"#,@": "unsyntax_splicing",
}
_OPENERS = {0x28: 0x29, 0x5B: 0x5D, 0x7B: 0x7D}   # ( [ {
_CLOSERS = frozenset(_OPENERS.values())
_DELIM_BYTES = frozenset(b"()[]{}\"',`;\t\n\v\f\r ")
_MIRROR = bytes.maketrans(b"([{<)]}>", b")]}>([{<")

_COMMENT_TYPES = frozenset({"comment", "block_comment", "sexp_comment"})


def _number_re(radix: int) -> re.Pattern:
    """The <number> grammar of the reference, one radix, case-insensitive.

    `t` is admitted as an exponent mark so extflonums (`1.0t0`) classify as
    numbers; the grammar forbids `t` inside a complex, which this does not
    check -- a token Racket rejects is still a token, and the span is what
    matters here.
    """
    digit = {2: rb"[01]", 8: rb"[0-7]", 10: rb"[0-9]", 16: rb"[0-9a-f]"}[radix]
    mark = rb"[slt]" if radix == 16 else rb"[sldeft]"
    digits_hash = digit + rb"+#*"
    simple = (rb"(?:" + digits_hash + rb"\.?#*|" + digit + rb"*\." + digits_hash
              + rb"|" + digits_hash + rb"/" + digits_hash + rb")")
    normal = rb"(?:" + simple + rb"(?:" + mark + rb"[+-]?" + digit + rb"+)?)"
    special = rb"(?:inf\.[0ft]|nan\.[0ft])"
    real = rb"(?:[+-]?" + normal + rb"|[+-]" + special + rb")"
    ureal = rb"(?:" + normal + rb"|" + special + rb")"
    cplx = rb"(?:" + real + rb"?[+-]" + ureal + rb"?i|" + real + rb"@" + real + rb")"
    return re.compile(rb"(?:" + real + rb"|" + cplx + rb")", re.IGNORECASE)


_NUMBER = {r: _number_re(r) for r in (2, 8, 10, 16)}
_NUM_START = frozenset(b"0123456789+-.")


def _classify_token(tok: bytes, radix: int = 10) -> str:
    if tok == b".":
        return "dot"
    if (radix != 10 or tok[0] in _NUM_START) and _NUMBER[radix].fullmatch(tok):
        return "number"
    return "symbol"


# ---------------------------------------------------------------------------


class _Reader:
    """One read of one buffer. Every ``_read_*`` takes a byte offset and
    returns ``(node, offset_after)``; errors raise ``RacketReadError`` and are
    caught only at the top level."""

    def __init__(self, source: bytes, at_exp: bool, command_char: bytes) -> None:
        self.src = source
        self.n = len(source)
        self.at_exp = at_exp
        self.cmd = command_char
        self.errors: list[RacketReadError] = []

    # -- utilities ----------------------------------------------------------
    def _skip_ws(self, i: int) -> int:
        m = _WS.match(self.src, i)
        return m.end() if m else i

    def _err(self, pos: int, message: str) -> RacketReadError:
        return RacketReadError(pos, message)

    # -- top level ------------------------------------------------------------
    def read_program(self) -> RacketNode:
        """Top-level forms with RESYNCHRONISATION on error.

        A datum that raises leaves an ``ERROR`` node from its start to the
        next line that begins with an opener (``(`` ``[`` ``{`` ``#`` ``;``,
        and the command character in at-exp mode). Idiomatic Racket starts
        every top-level form in column 0, so that is the next point at which
        the reader is back in a known state. Two consequences the walker
        relies on:

        * a form left open by a missing ``)`` costs that form only, never the
          rest of the file (Racket itself rejects the whole file);
        * an EXTRA ``)`` closes its form early and would leave the form's
          remaining internal definitions to be read as top-level forms. Those
          arrive INDENTED, so on an unexpected closer every top-level form
          read since the last column-0 form is folded into the ``ERROR`` --
          which is exactly the re-parenting fabrication this reader replaces
          tree-sitter to avoid.
        """
        src = self.src
        children: list[RacketNode] = []
        i = self._skip_ws(0)
        while i < self.n:
            parse_budget.checkpoint()
            start = i
            try:
                node, i = self._read_datum(i, top=True)
            except RacketReadError as e:
                self.errors.append(e)
                err_start = start
                if src[start] in _CLOSERS:
                    # Fold indented top-level forms back into the error.
                    while children and children[-1].type != "ERROR" \
                            and self._column(children[-1].start_byte) > 0:
                        parse_budget.checkpoint()
                        err_start = children.pop().start_byte
                resume = self._resync(max(start + 1, err_start + 1))
                children.append(RacketNode("ERROR", err_start, resume))
                i = resume
                continue
            children.append(node)
            i = self._skip_ws(i)
        return RacketNode("program", 0, self.n, children)

    def _column(self, pos: int) -> int:
        nl = self.src.rfind(b"\n", 0, pos)
        return pos - (nl + 1)

    def _resync(self, frm: int) -> int:
        src = self.src
        starters = b"([{#;" + (self.cmd[:1] if self.at_exp else b"")
        i = src.find(b"\n", frm)
        while i != -1 and i + 1 < self.n:
            parse_budget.checkpoint()
            if src[i + 1] in starters:
                return i + 1
            i = src.find(b"\n", i + 1)
        return self.n

    # -- one datum ------------------------------------------------------------
    def _read_datum(self, i: int, top: bool = False, cmd_mode: bool = False) -> tuple[RacketNode, int]:
        """Read one datum at ``i`` (no leading whitespace). Comments are data
        here -- they come back as ``comment`` / ``block_comment`` /
        ``sexp_comment`` nodes, as tree-sitter reports them."""
        src = self.src
        if i >= self.n:
            raise self._err(i, "unexpected end of file")
        b = src[i]
        if self.at_exp and src.startswith(self.cmd, i):
            nodes, j = self._read_at(i, in_text=False)
            if len(nodes) != 1:
                # `@;` in code: a comment. Racket returns a special comment
                # that its callers drop; this tree keeps every comment.
                if not nodes:
                    return RacketNode("comment", i, j), j
                raise self._err(i, "an escape in code must be exactly one expression")
            return nodes[0], j
        if b in _OPENERS:
            return self._read_list(i, cmd_mode)
        if b in _CLOSERS:
            raise self._err(i, f"unexpected `{chr(b)}`")
        if b == 0x22:  # "
            m = _STRING.match(src, i)
            if not m:
                raise self._err(i, "unterminated string")
            return RacketNode("string", i, m.end()), m.end()
        if b == 0x3B:  # ;
            m = _LINE_REST.match(src, i + 1)
            return RacketNode("comment", i, m.end()), m.end()
        if b in (0x27, 0x60, 0x2C):  # ' ` ,
            pfx = src[i:i + 2] if src.startswith(b",@", i) else src[i:i + 1]
            return self._read_wrapped(_QUOTE_TYPES[pfx], i, i + len(pfx), cmd_mode)
        if b == 0x23:  # #
            return self._read_hash(i, cmd_mode)
        if cmd_mode and b == 0x7C:
            # The command readtable reads a leading `|...|` as one symbol.
            m = _BAR_SYMBOL.match(src, i)
            if not m:
                raise self._err(i, "unterminated `|`")
            return RacketNode("symbol", i, m.end()), m.end()
        m = (_SYM_CMD if cmd_mode else _SYM).match(src, i)
        if not m:
            what = "unterminated `|`" if b == 0x7C else "unexpected character"
            raise self._err(i, what)
        kind = _classify_token(m.group(0))
        if kind == "dot" and top:
            raise self._err(i, "illegal use of `.`")
        return RacketNode(kind, i, m.end()), m.end()

    def _read_wrapped(self, type_: str, start: int, i: int,
                      cmd_mode: bool = False) -> tuple[RacketNode, int]:
        """A prefix that takes the next datum: quotes, `#&`, `#;`. Comments
        between the prefix and the datum ride along as children."""
        kids: list[RacketNode] = []
        while True:
            parse_budget.checkpoint()
            i = self._skip_ws(i)
            if i >= self.n:
                raise self._err(start, "expected a datum after the prefix")
            node, i = self._read_datum(i, cmd_mode=cmd_mode)
            kids.append(node)
            if node.type not in _COMMENT_TYPES:
                break
        return RacketNode(type_, start, i, kids), i

    def _read_list(self, i: int, cmd_mode: bool = False) -> tuple[RacketNode, int]:
        src = self.src
        start = i
        closer = _OPENERS[src[i]]
        kids: list[RacketNode] = []
        i += 1
        while True:
            parse_budget.checkpoint()
            i = self._skip_ws(i)
            if i >= self.n:
                raise self._err(start, "missing closing paren")
            b = src[i]
            if b in _CLOSERS:
                if b != closer:
                    # The paren COUNT is right and only the shape is wrong;
                    # closing here keeps every definition in the form.
                    self.errors.append(self._err(i, f"expected `{chr(closer)}`, found `{chr(b)}`"))
                return RacketNode("list", start, i + 1, kids), i + 1
            node, i = self._read_datum(i, cmd_mode=cmd_mode)
            kids.append(node)

    def _read_hash(self, i: int, cmd_mode: bool) -> tuple[RacketNode, int]:
        src = self.src
        j = i + 1
        if j >= self.n:
            raise self._err(i, "bad syntax `#`")
        b = src[j]
        if b == 0x7C:  # #| ... |#
            depth, k = 1, j + 1
            while depth:
                parse_budget.checkpoint()
                o = src.find(b"#|", k)
                c = src.find(b"|#", k)
                if c == -1:
                    raise self._err(i, "unterminated block comment")
                if o != -1 and o < c:
                    depth, k = depth + 1, o + 2
                else:
                    depth, k = depth - 1, c + 2
            return RacketNode("block_comment", i, k), k
        if b == 0x3B:  # #;
            return self._read_wrapped("sexp_comment", i, j + 1)
        if b == 0x27 or b == 0x60 or b == 0x2C:  # #' #` #, #,@
            pfx = src[i:i + 3] if src.startswith(b"#,@", i) else src[i:i + 2]
            return self._read_wrapped(_QUOTE_TYPES[pfx], i, i + len(pfx), cmd_mode)
        if b == 0x5C:  # #\
            m = _CHAR.match(src, i)
            if not m:
                raise self._err(i, "bad character constant")
            return RacketNode("character", i, m.end()), m.end()
        if b == 0x22:  # #"
            m = _STRING.match(src, j)
            if not m:
                raise self._err(i, "unterminated byte string")
            return RacketNode("byte_string", i, m.end()), m.end()
        if b == 0x3A:  # #:
            m = _SYM.match(src, j + 1)
            end = m.end() if m else j + 1
            return RacketNode("keyword", i, end), end
        if b == 0x25:  # #%
            m = _SYM.match(src, j + 1)
            end = m.end() if m else j + 1
            return RacketNode("symbol", i, end), end
        if b == 0x26:  # #&
            return self._read_wrapped("box", i, j + 1)
        if b == 0x21:  # #!
            m = _SHEBANG.match(src, i)
            if m:
                return RacketNode("comment", i, m.end()), m.end()
            m = _LANG_ALIAS.match(src, i)
            if m:
                name = RacketNode("lang_name", m.start(1), m.end(1))
                return RacketNode("extension", i, m.end(), [name]), m.end()
            raise self._err(i, "bad syntax `#!`")
        if b in (0x72, 0x70) and src[j:j + 2] in (b"rx", b"px"):  # #rx #px
            k = j + 2
            if src[k:k + 1] == b"#":
                k += 1
            m = _STRING.match(src, k)
            if not m:
                raise self._err(i, "bad regexp literal")
            return RacketNode("regex", i, m.end()), m.end()
        if b == 0x3C and src.startswith(b"#<<", i):  # here string
            m = _HERE.match(src, i)
            if not m or not m.group(1):
                raise self._err(i, "bad here-string terminator")
            term = b"\n" + m.group(1)
            k = m.end() - 1                          # the newline ending the header line
            while True:
                parse_budget.checkpoint()
                k = src.find(term, k)
                if k == -1:
                    raise self._err(i, "unterminated here string")
                end = k + len(term)
                if end == self.n:
                    return RacketNode("here_string", i, end), end
                if src[end] == 0x0A:
                    # Racket's span for a here string runs THROUGH the newline
                    # that ends the terminator line.
                    return RacketNode("here_string", i, end + 1), end + 1
                k = end
        if b in (0x65, 0x45, 0x69, 0x49, 0x78, 0x58, 0x6F, 0x4F, 0x62, 0x42, 0x64, 0x44):
            m = _NUM_PREFIX.match(src, i)
            if m:
                radix = 10
                for c in m.group(0).lower():
                    parse_budget.checkpoint()
                    radix = {0x78: 16, 0x6F: 8, 0x62: 2}.get(c, radix)
                body = _SYM.match(src, m.end())
                end = body.end() if body else m.end()
                if body and _classify_token(body.group(0), radix) == "number":
                    return RacketNode("number", i, end), end
                raise self._err(i, "bad number")
        m = _VECTOR.match(src, i)
        if m:
            inner, end = self._read_list(m.end())
            return RacketNode("vector", i, end, [inner]), end
        m = _HASH.match(src, i)
        if m:
            inner, end = self._read_list(m.end())
            return RacketNode("hash", i, end, [inner]), end
        if b == 0x73 and src[j + 1:j + 2] in (b"(", b"[", b"{"):  # #s(
            inner, end = self._read_list(j + 1)
            return RacketNode("structure", i, end, [inner]), end
        m = _BOOL.match(src, i)
        if m:
            return RacketNode("boolean", i, m.end()), m.end()
        m = _GRAPH.match(src, i)
        if m:
            tag = RacketNode("decimal", m.start(1), m.end(1))
            if m.group(2) == b"#":
                return RacketNode("graph", i, m.end(), [tag]), m.end()
            node, end = self._read_wrapped("graph", i, m.end())
            node.children.insert(0, tag)
            return node, end
        if src.startswith(b"#lang", i):
            m = _LANG.match(src, i)
            if not m:
                raise self._err(i, "bad `#lang` line")
            name = RacketNode("lang_name", m.start(1), m.end(1))
            end = m.end()
            kids = [name]
            if m.group(1).decode("ascii") in LANG_TAKES_ARGUMENT:
                k = self._skip_ws(end)
                if k < self.n:
                    arg, end = self._read_datum(k)
                    arg.type = "lang_name"
                    kids.append(arg)
            return RacketNode("extension", i, end, kids), end
        if src.startswith(b"#reader", i):
            # `#reader <module-path> <datum-read-by-it>`: the second datum is
            # read by a reader this one does not have, so the default reader
            # is the best available guess at its extent.
            path, k = self._read_wrapped("extension", i, j + 6)
            body, end = self._read_wrapped("extension", i, k)
            for c in path.children:
                parse_budget.checkpoint()
                if c.type not in _COMMENT_TYPES:
                    c.type = "lang_name"      # the module path is not a datum of the file
            return RacketNode("extension", i, end, path.children + body.children), end
        if src[j:j + 2].lower() in (b"ci", b"cs"):
            return self._read_datum(self._skip_ws(j + 2), cmd_mode=cmd_mode)
        raise self._err(i, "bad syntax `#%s`" % src[j:j + 1].decode("latin-1"))

    # -- at-exp ---------------------------------------------------------------
    def _read_at(self, i: int, in_text: bool) -> tuple[list[RacketNode], int]:
        """Dispatch on the command character at ``i``, in the order
        ``scribble/reader`` uses. Returns the nodes to splice into the
        enclosing context: one for an at-form or a command, several for a
        text-mode ``@|a b|`` escape, none for a comment or ``@||``."""
        src = self.src
        start = i
        i += len(self.cmd)
        if i >= self.n:
            raise self._err(start, "missing command")
        if _WS.match(src, i):
            raise self._err(start, "unexpected whitespace after the command character")
        if src[i] == 0x3B:  # @; comment
            body = self._read_body(i + 1)
            if body is not None:
                kids, end = body
                return [], end   # `@;{...}`: a block comment (dropped, like Racket)
            m = _LINE_REST.match(src, i + 1)
            end = m.end()
            # ..."and all following spaces (or tabs)", i.e. the newline too.
            m2 = _TEXT_EOL.match(src, end)
            return [], (m2.end() if m2 else end)
        if in_text:
            if src[i] == 0x22:   # @"string": merged into the text by Racket
                m = _STRING.match(src, i)
                if not m:
                    raise self._err(i, "unterminated string")
                return [RacketNode("string", i, m.end())], m.end()
            if src[i] == 0x7C:   # @|expr ...|
                return self._read_escape(i)
        # Punctuation prefixes wrap the WHOLE form.
        prefixes: list[str] = []
        while True:
            parse_budget.checkpoint()
            m = _AT_PREFIX.match(src, i)
            if not m:
                break
            prefixes.append(_QUOTE_TYPES[m.group(0)])
            i = m.end()
            if _WS.match(src, i):
                raise self._err(start, "unexpected whitespace after the command character")
        cmd: Optional[RacketNode] = None
        datums: Optional[list[RacketNode]] = None
        lines: Optional[list[RacketNode]] = None
        body = self._read_body(i)
        if body is not None:
            lines, i = body
        elif src[i] == 0x5B:  # [
            datums, i = self._read_datums(i)
            body = self._read_body(i)
            if body is not None:
                lines, i = body
        elif src[i] == 0x7C:  # |expr|
            nodes, i = self._read_escape(i)
            if len(nodes) != 1:
                raise self._err(i, "a |...| form in code must have exactly one expression")
            cmd = nodes[0]
            result: Optional[RacketNode] = cmd
        else:
            cmd, i = self._read_command(i)
            if i < self.n and src[i] == 0x5B:
                datums, i = self._read_datums(i)
            body = self._read_body(i)
            if body is not None:
                lines, i = body
        if datums is not None or lines is not None:
            kids = ([cmd] if cmd is not None else []) + (datums or []) + (lines or [])
            result = RacketNode("list", start, i, kids)
            result.at_form = True
        else:
            result = cmd
        for type_ in reversed(prefixes):
            parse_budget.checkpoint()
            result = RacketNode(type_, start, i, [result])
        return [result], i

    def _read_command(self, i: int) -> tuple[RacketNode, int]:
        while True:
            parse_budget.checkpoint()
            node, i = self._read_datum(i, cmd_mode=True)
            if node.type in _COMMENT_TYPES:
                raise self._err(node.start_byte, "expecting a command expression, got a comment")
            return node, i

    def _read_datums(self, i: int) -> tuple[list[RacketNode], int]:
        """``[datum ...]`` -- ordinary reads, with the command character live."""
        start = i
        kids: list[RacketNode] = []
        i += 1
        while True:
            parse_budget.checkpoint()
            i = self._skip_ws(i)
            if i >= self.n:
                raise self._err(start, "expected a `]`")
            if self.src[i] == 0x5D:
                return kids, i + 1
            node, i = self._read_datum(i)
            kids.append(node)

    def _read_escape(self, i: int) -> tuple[list[RacketNode], int]:
        """``|expr ...|``: any number of expressions, none is allowed."""
        start = i
        kids: list[RacketNode] = []
        i += 1
        while True:
            parse_budget.checkpoint()
            i = self._skip_ws(i)
            if i >= self.n:
                raise self._err(start, "expected a closing `|`")
            if self.src[i] == 0x7C:
                return kids, i + 1
            # The COMMAND readtable is in force inside `|...|`: `|` terminates
            # a symbol, so `@|user|span` is the escape `user` followed by text.
            node, i = self._read_datum(i, cmd_mode=True)
            if node.type not in _COMMENT_TYPES:
                kids.append(node)

    def _read_body(self, i: int) -> Optional[tuple[list[RacketNode], int]]:
        """``{text}`` or ``|<punct>{text}<punct>|`` at ``i``, else None."""
        src = self.src
        if i >= self.n:
            return None
        if src[i] == 0x7B:
            return self._read_text(i + 1, b"{", b"}", None)
        m = _ALT_OPEN.match(src, i)
        if m:
            opener = m.group(0)
            closer = opener[::-1].translate(_MIRROR)
            return self._read_text(m.end(), opener, closer, b"|" + m.group(1))
        return None

    def _read_text(self, i: int, opener: bytes, closer: bytes,
                   cmd_prefix: Optional[bytes]) -> tuple[list[RacketNode], int]:
        """A text body. Only three things have meaning: the opener/closer
        pair (nesting), the command character (an escape), and EOF (an
        error). With alternate delimiters the escape needs the prefix, and
        bare braces and ``@`` are text."""
        src = self.src
        at = (cmd_prefix or b"") + self.cmd
        special = re.compile(b"|".join(re.escape(s) for s in (opener, closer, at)))
        kids: list[RacketNode] = []
        text_start: Optional[int] = None
        depth = 0
        start = i

        def flush(upto: int) -> None:
            nonlocal text_start
            if text_start is not None and upto > text_start:
                kids.append(RacketNode("string", text_start, upto))
            text_start = None

        while True:
            parse_budget.checkpoint()
            m = special.search(src, i)
            if not m:
                raise self._err(start - len(opener), f"missing closing `{closer.decode('latin-1')}`")
            if m.start() > i and text_start is None:
                text_start = i
            i = m.start()
            tok = m.group(0)
            if tok == closer and depth == 0:
                flush(i)
                return kids, i + len(closer)
            if tok == opener:
                depth += 1
                if text_start is None:
                    text_start = i
                i += len(opener)
            elif tok == closer:
                depth -= 1
                if text_start is None:
                    text_start = i
                i += len(closer)
            else:
                flush(i)
                nodes, i = self._read_at(i + len(at) - len(self.cmd), in_text=True)
                kids.extend(nodes)

    def read_inside(self) -> RacketNode:
        """Text at top level (the ``scribble/base`` shape): escapes until EOF,
        braces are text. Not routed to by anything yet; see the module doc."""
        src = self.src
        kids: list[RacketNode] = []
        i = 0
        text_start: Optional[int] = None
        while i < self.n:
            parse_budget.checkpoint()
            j = src.find(self.cmd, i)
            if j == -1:
                j = self.n
            if j > i:
                kids.append(RacketNode("string", i, j))
            if j >= self.n:
                break
            try:
                nodes, i = self._read_at(j, in_text=True)
            except RacketReadError as e:
                self.errors.append(e)
                resume = self._resync(j + 1)
                kids.append(RacketNode("ERROR", j, resume))
                i = resume
                continue
            kids.extend(nodes)
        del text_start
        return RacketNode("program", 0, self.n, kids)


def read_racket(source: bytes, *, at_exp: bool = False, command_char: bytes = b"@",
                inside: bool = False) -> RacketTree:
    """Read a whole buffer.

    ``at_exp`` installs the command character as a dispatch character, as
    ``#lang at-exp`` does; it is NEVER inferred from the text, because in the
    default reader ``@`` is an ordinary symbol constituent and
    ``(define @foo 1)`` binds ``@foo``. ``inside`` reads text at top level
    (the Scribble document shape). Never raises: errors are recorded on the
    tree and the affected span is an ``ERROR`` node.
    """
    reader = _Reader(source, at_exp or inside, command_char)
    root = reader.read_inside() if inside else reader.read_program()
    return RacketTree(source, root, reader.errors)
