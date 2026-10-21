"""The Racket `#lang` gate: a reader is decided before anything is read.

A `#lang` line names a READER, and a reader can make a `.rkt` file's surface
syntax anything at all -- Markdown (`punct`), prose (`scribble/manual`),
at-exp text over Racket (`conscript`). Before this gate the walker parsed
every `.rkt` as `racket/base`; now the tier selects the mode of
`racket_reader.py` (S-expressions, or S-expressions with `@` as the command
character) or refuses to read a document at all.

Measured on 207 `#lang conscript` files against Racket's own reader: 39% of
definitions found, ~100 FABRICATED (an internal `define` promoted to module
level by error recovery), because `;` `"` `#` `|` are prose inside an at-exp
text body and comment / string / reader-prefix tokens to the grammar. With the
gate: 0 missing, 0 wrong spans. On 94 `#lang punct` files: 0 symbols, where a
Markdown document ABOUT Racket would otherwise index its code samples.

The tests are split by tier. Most are ABSENCE tests, as in
``test_racket_language.py``: the risk is what gets emitted, not what is missed.
"""

import pytest

from jcodemunch_mcp.parser.extractor import (
    _parse_racket_symbols,
    _racket_lang_of,
    _racket_tier,
)
from jcodemunch_mcp.parser.racket_reader import read_racket


@pytest.fixture(autouse=True)
def _no_project_config(monkeypatch):
    """Answer the gate's built-in lists, not the developer's config file."""
    monkeypatch.setattr(
        "jcodemunch_mcp.config.get",
        lambda key, default=None, repo=None: default,
    )


@pytest.fixture
def langs(monkeypatch):
    """Install `racket_langs` without touching any real config file."""
    def _install(mapping):
        monkeypatch.setattr(
            "jcodemunch_mcp.config.get",
            lambda key, default=None, repo=None: (
                mapping if key == "racket_langs" else default
            ),
        )
    return _install


def _names(src: str, repo=None) -> set:
    return {s.name for s in _parse_racket_symbols(src.encode("utf-8"), "g.rkt", repo=repo)}


# ── reading the #lang line ────────────────────────────────────────────────

@pytest.mark.parametrize("head,expected", [
    (b"#lang racket/base\n", ("racket/base", None)),
    (b"#lang at-exp racket/base\n", ("at-exp", "racket/base")),
    (b"#lang punct opcraftco\n", ("punct", "opcraftco")),
    (b";; a comment first\n;; and another\n#lang racket\n", ("racket", None)),
    (b"#! /usr/bin/env racket\n#lang racket\n", ("racket", None)),
    (b"\n\n  #lang typed/racket\n", ("typed/racket", None)),
    (b"(module m racket/base (define x 1))\n", (None, None)),
    (b"", (None, None)),
], ids=["plain", "at-exp", "punct-with-arg", "after-comments", "after-shebang",
        "after-blank", "module-form", "empty"])
def test_lang_line_is_read_from_the_head_of_the_file(head, expected):
    assert _racket_lang_of(head) == expected


def test_lang_line_is_not_read_from_inside_the_body():
    """A `#lang` mentioned in a comment BELOW the first form is not the lang."""
    assert _racket_lang_of(b"(define x 1)\n;; #lang punct\n") == (None, None)


# ── the three tiers ───────────────────────────────────────────────────────

@pytest.mark.parametrize("lang,tier", [
    ("racket", "sexp"), ("racket/base", "sexp"), ("racket/gui", "sexp"),
    ("typed/racket", "sexp"), ("typed/racket/base", "sexp"), ("s-exp", "sexp"),
    ("info", "sexp"), ("scheme/base", "sexp"), ("plai", "sexp"), ("br", "sexp"),
    ("br/quicklang", "sexp"), ("web-server/insta", "sexp"), ("eopl", "sexp"),
    ("rosette", "sexp"), ("rosette/safe", "sexp"),
    ("at-exp racket/base", "at-exp"), ("at-exp racket", "at-exp"),
    ("debug racket/base", "sexp"), ("errortrace racket", "sexp"),
    ("punct", "text"), ("punct opcraftco", "text"), ("scribble/manual", "text"),
    ("scribble/base", "text"), ("pollen", "text"), ("pollen/mode racket/base", "at-exp"),
    ("markdown", "text"), ("brag", "text"), ("datalog", "text"),
    ("rhombus", "text"), ("at-exp scribble/base", "text"),
])
def test_built_in_tiers(lang, tier):
    assert _racket_tier(f"#lang {lang}\n(define x 1)\n".encode())[0] == tier


def test_no_lang_line_is_the_default_reader_and_therefore_sexp():
    """A `(module ...)` file is read by the default reader by construction."""
    assert _racket_tier(b"(module m racket/base (define x 1))\n") == ("sexp", "")


def test_an_unlisted_lang_is_text():
    """⚠ The asymmetry rule. A lang the gate does not know could be anything;
    a missed definition is recoverable by reading the file, a fabricated one is
    not. So unknown means no symbols, not a guess."""
    tier, written = _racket_tier(b"#lang conscript\n(defstep (s) 1)\n")
    assert tier == "text"
    assert written == "conscript"


def test_text_tier_emits_nothing_even_for_a_define_in_prose():
    """A Markdown document about Racket carries `(define ...)` in code samples.
    Before the gate, every one of those indexed as a module-level binding."""
    src = ("#lang punct\n\n# Defining things\n\nUse define:\n\n"
           "```racket\n(define (greet name) name)\n(struct posn (x y))\n```\n")
    assert _names(src) == set()


def test_text_tier_is_a_gate_not_a_parse_failure(caplog):
    """The file is announced as skipped at INFO, naming the lang, so a corpus
    of an unknown lang shows up in the log rather than as a silent zero."""
    import logging
    with caplog.at_level(logging.INFO, logger="jcodemunch_mcp.parser.extractor"):
        _names("#lang mylang\n(define x 1)\n")
    assert any("mylang" in r.getMessage() and "racket_langs" in r.getMessage()
               for r in caplog.records)


# ── project-declared langs ────────────────────────────────────────────────

def test_config_promotes_a_projects_own_lang(langs):
    langs({"conscript": "at-exp"})
    src = "#lang conscript\n(define (helper x) x)\n"
    assert "helper" in _names(src, repo="/proj")


def test_config_key_covers_its_sub_langs(langs):
    """`conscript` covers `conscript/with-require` and `conscript/local`,
    the way the built-in lists match `racket/*`."""
    langs({"conscript": "at-exp"})
    assert _racket_tier(b"#lang conscript/with-require\n", repo="/proj")[0] == "at-exp"
    assert _racket_tier(b"#lang conscript/local\n", repo="/proj")[0] == "at-exp"


def test_config_wins_over_the_built_in_lists(langs):
    """A project may demote a lang too -- `text` for a `racket/base` file set
    that is really generated data, or `sexp` for a lang we list as text."""
    langs({"racket/base": "text", "markdown": "sexp"})
    assert _racket_tier(b"#lang racket/base\n", repo="/proj")[0] == "text"
    assert _racket_tier(b"#lang markdown\n", repo="/proj")[0] == "sexp"


def test_no_repo_means_no_declarations(langs):
    langs({"conscript": "at-exp"})
    assert _racket_tier(b"#lang conscript\n", repo=None)[0] == "text"


@pytest.mark.parametrize("bad", [
    {"conscript": "prose"},           # not a tier
    {"conscript": ["at-exp"]},        # list, unhashable
    {"conscript": None},
    {3: "at-exp"},                    # non-string key
], ids=["bad-tier", "list", "null", "int-key"])
def test_malformed_entries_cost_one_entry_not_the_file(langs, bad):
    langs({**bad, "other": "sexp"})
    assert _racket_tier(b"#lang other\n", repo="/proj")[0] == "sexp"
    assert _racket_tier(b"#lang conscript\n", repo="/proj")[0] == "text"


# ── at-exp: text bodies are read as text, by the reader ───────────────────

HAZARDS = {
    "semicolon": "Thanks; you are done",
    "dquote": 'He said "hi',
    "hash": "Item #1 of 3",
    "bar": "a | b",
    "paren": "1) first, 2) second",
    "all-four": 'Item #1; "quoted" | done',
}


@pytest.mark.parametrize("text", list(HAZARDS.values()), ids=list(HAZARDS))
def test_atexp_text_body_hazards_no_longer_swallow_later_definitions(text):
    """⚠ Each of these is prose to Racket's at-exp reader and a token to the
    DEFAULT reader. `"` alone took every later definition in the file with it;
    the others made the enclosing form an error. The tier is what switches
    the reader into at-exp mode; without it the same bytes do not read."""
    src = ("#lang at-exp racket/base\n"
           "(define (step)\n"
           f"  @html{{{text}}})\n"
           "(define (after) 1)\n")
    assert read_racket(src.encode()).errors, "non-vacuity: the default reader must fail on the raw text"
    assert _names(src) == {"step", "after"}


def test_atexp_nested_bodies_and_commands():
    src = ("#lang at-exp racket/base\n"
           "(define (step)\n"
           "  @html{@h1{Hi @|name|!} Do you; \"consent\"? @button[accepted]{Yes} @button[rejected]{No}})\n"
           "(define (after) 1)\n")
    assert _names(src) == {"step", "after"}


def test_offsets_and_lines_come_from_the_original_bytes():
    """The symbol's byte range and line number must name the source file,
    not any intermediate form of it -- the byte-blanking pre-pass this
    replaced kept offsets by construction; the reader keeps them because
    there is no intermediate form."""
    src = '#lang at-exp racket/base\n(define (f) @p{a "b" ; c})\n(define g 2)\n'
    syms = {s.name: s for s in _parse_racket_symbols(src.encode(), "f.rkt")}
    f, g = syms["f"], syms["g"]
    assert src.encode()[f.byte_offset:f.byte_offset + f.byte_length] == b'(define (f) @p{a "b" ; c})'
    assert (f.line, g.line) == (2, 3)


def test_symbols_from_an_atexp_file_hash_the_original_bytes():
    """Blanking is for the GRAMMAR. The symbol's content_hash and byte range
    must describe the file on disk, or an unchanged file re-hashes differently
    from its stored row and incremental indexing re-parses it forever."""
    src = b'#lang at-exp racket/base\n(define (f) @p{a "b"})\n'
    syms = _parse_racket_symbols(src, "g.rkt")
    (f,) = syms
    span = src[f.byte_offset:f.byte_offset + f.byte_length]
    assert span == b'(define (f) @p{a "b"})'
    from jcodemunch_mcp.parser.symbols import compute_content_hash
    assert f.content_hash == compute_content_hash(span)


@pytest.mark.parametrize("code", [
    '(define s "a { b")',          # brace inside a string
    "(define c #\\{)",              # brace character literal
    "(define d 1) ; open { here",   # brace inside a line comment
    "#| a { block |# (define e 1)", # brace inside a block comment
], ids=["string", "char", "line-comment", "block-comment"])
def test_a_brace_in_code_mode_does_not_open_a_body(code):
    """⚠ Each of these would otherwise blank to the next `}` or to the end of
    the file, taking real definitions with it."""
    src = f"#lang at-exp racket/base\n{code}\n(define (after) 1)\n"
    assert "after" in _names(src)


def test_a_brace_body_in_a_plain_racket_file_is_a_list():
    """`{}` are parentheses in the default reader -- `(let {[x 1]} x)` is
    legal Racket -- so the sexp tier reads them as lists, never as text."""
    src = "#lang racket/base\n(define (f) (let {[x (helper 1)]} x))\n"
    (f,) = _parse_racket_symbols(src.encode(), "g.rkt")
    assert "helper" in f.call_references


# ── the command character ─────────────────────────────────────────────────

def test_a_lang_may_declare_its_own_command_character(langs):
    """`make-at-readtable` takes `#:command-char`; Pollen uses `◊`. With it
    declared, `◊` dispatches and `@` is an ordinary character again."""
    langs({"mylang": {"tier": "at-exp", "command_char": "◊"}})
    src = ("#lang mylang\n"
           "(define (step)\n"
           "  ◊html{Thanks; \"you\" are @done ◊|helper|})\n"
           "(define (after) 1)\n"
           "(define @plain 2)\n")
    names = _names(src, repo="/proj")
    assert names == {"step", "after", "@plain"}


def test_pollen_mode_is_at_exp_with_the_lozenge():
    """`#lang pollen/mode racket/base` is `make-at-readtable #:command-char
    #\◊` over its argument (pollen/mode.rkt, hardcoded). It was text --
    `pollen` is a document lang and the prefix rule caught `pollen/mode` --
    so 30 files of Racket code in one site yielded nothing."""
    from jcodemunch_mcp.parser.extractor import _racket_command_char
    src = ("#lang pollen/mode racket/base\n"
           "(define (step)\n"
           "  ◊div{He said \"hi\"; @not-a-form})\n"
           "(define (after) 1)\n")
    assert _racket_tier(src.encode())[0] == "at-exp"
    assert _racket_command_char("pollen/mode racket/base", None) == "◊".encode()
    assert _names(src) == {"step", "after"}
    assert _racket_tier(b"#lang pollen\n")[0] == "text", "a Pollen DOCUMENT is still a document"


def test_the_object_form_and_the_string_form_declare_the_same_tier(langs):
    langs({"a": "at-exp", "b": {"tier": "at-exp"}, "c": {"tier": "text"}})
    assert _racket_tier(b"#lang a\n", repo="/proj")[0] == "at-exp"
    assert _racket_tier(b"#lang b\n", repo="/proj")[0] == "at-exp"
    assert _racket_tier(b"#lang c\n", repo="/proj")[0] == "text"
    from jcodemunch_mcp.parser.extractor import _racket_command_char
    assert _racket_command_char("a", "/proj") == b"@"
    assert _racket_command_char("b/sub", "/proj") == b"@"
    assert _racket_command_char("at-exp b", "/proj") == b"@", "Racket's own at-exp is always `@`"
    assert _racket_command_char("debug b", "/proj") == b"@"


@pytest.mark.parametrize("bad", [
    {"tier": "at-exp", "command_char": "◊◊"},   # two characters
    {"tier": "at-exp", "command_char": " "},    # whitespace
    {"tier": "at-exp", "command_char": 1},      # not a string
    {"command_char": "◊"},                      # no tier
    {"tier": "prose"},                          # unknown tier
], ids=["two-chars", "space", "int", "no-tier", "bad-tier"])
def test_a_malformed_object_entry_costs_that_entry_only(langs, bad):
    langs({"mylang": bad, "other": "sexp"})
    assert _racket_tier(b"#lang mylang\n", repo="/proj")[0] == "text"
    assert _racket_tier(b"#lang other\n", repo="/proj")[0] == "sexp"


def test_the_command_character_is_part_of_the_config_digest(langs):
    """A changed command character changes what unchanged bytes yield, so it
    must move the stamp that forces the one re-parse."""
    from jcodemunch_mcp import config as _config
    langs({"mylang": {"tier": "at-exp", "command_char": "◊"}})
    a = _config.racket_config_digest("/proj")
    langs({"mylang": {"tier": "at-exp", "command_char": "@"}})
    b = _config.racket_config_digest("/proj")
    assert a != b != ""


def test_atexp_over_a_text_lang_is_still_text():
    assert _racket_tier(b"#lang at-exp scribble/base\n")[0] == "text"


# ── read errors: the broken form is skipped, the rest of the file is not ──

def test_an_internal_define_inside_a_broken_form_is_not_a_module_binding(caplog):
    """⚠ Regression for the fabrication class. Under tree-sitter an
    unterminated string inside a form re-parented the form's INTERNAL define
    under a root ERROR node (`list -> ERROR -> program`, measured on a real
    `(define abc@ (unit ... (define (compute-payment) ...)))`) and walking it
    reported `inner` as an importable module-level function. The reader
    marks the whole broken form ERROR, so `inner` is inside it, not beside
    it -- and the definition AFTER the broken form is still found."""
    import logging
    src = ('#lang racket\n(define outer\n  (thing\n    (define (inner) 1)\n'
           '    "))\n(define (after) 2)\n')
    root = read_racket(src.encode()).root_node
    assert [c.type for c in root.children] == ["extension", "ERROR", "list"], \
        "non-vacuity: the broken form must be an ERROR node at top level"
    assert not any(c.type == "list" and c.text.startswith(b"(define (inner)") for c in root.children)

    with caplog.at_level(logging.WARNING, logger="jcodemunch_mcp.parser.extractor"):
        names = _names(src)
    assert "inner" not in names and "outer" not in names
    assert names == {"after"}
    assert any("g.rkt" in r.getMessage() and "not indexed" in r.getMessage()
               and "line 5" in r.getMessage()
               for r in caplog.records), "the partial read must be announced, with its line"


def test_a_clean_file_logs_no_parse_warning(caplog):
    import logging
    with caplog.at_level(logging.WARNING, logger="jcodemunch_mcp.parser.extractor"):
        assert _names("#lang racket\n(define (a) 1)\n") == {"a"}
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_definitions_after_a_stray_close_paren_are_indexed(caplog):
    """⚠ This test used to pin the OPPOSITE: under tree-sitter a stray `)`
    put every later top-level form under ERROR and they were lost, "pinned so
    it is a decision rather than an accident". It was the defect written down
    as intended behaviour (Practice 9). The reader resynchronises at the next
    column-0 form, so only the `)` itself is an ERROR and `b` is found; the
    WARNING still names the file and line."""
    import logging
    with caplog.at_level(logging.WARNING, logger="jcodemunch_mcp.parser.extractor"):
        names = _names("#lang racket\n(define (a) 1))\n(define (b) 2)\n")
    assert names == {"a", "b"}
    assert any("line 2" in r.getMessage() for r in caplog.records)


def test_a_missing_close_paren_costs_its_form_not_the_file():
    """Racket rejects the whole file. Under tree-sitter the open form
    swallowed everything after it. Now `a` is lost (its extent is unknown)
    and `b`, `c` are found."""
    names = _names("#lang racket\n(define (a) 1\n(define (b) 2)\n(define (c) 3)\n")
    assert names == {"b", "c"}


def test_an_extra_close_paren_does_not_leak_internal_defines():
    """The form closes early and its remaining internal definitions arrive at
    top level indented -- the shape tree-sitter's recovery turned into
    fabricated module-level bindings. They fold into the ERROR instead."""
    src = ("#lang racket\n(define (a)\n  (let ()\n    (define (inner) 1)))\n"
           "    (define (leaked) 2))\n(define (b) 3)\n")
    assert _names(src) == {"a", "b"}


def test_lang_after_a_block_comment_is_still_seen_by_the_gate():
    """`#lang` may follow comment forms, and `#| |#` is one. openssl/mzssl.rkt
    opens with a 900-byte block comment; its lang was invisible, so the file
    read as `#lang`-less. Harmless there (the default reader either way);
    NOT harmless for a document lang behind a license block, which would
    have been read as S-expressions -- the fabrication the gate exists for."""
    from jcodemunch_mcp.parser.extractor import _racket_lang_of
    assert _racket_lang_of(b"#| license |#\n\n#lang scribble/manual\n") == ("scribble/manual", None)
    assert _racket_lang_of(b";; c\n#| a |# #| b |#\n#lang at-exp racket\n") == ("at-exp", "racket")
    # Non-vacuity: a block comment that never closes is not a license header.
    assert _racket_lang_of(b"#| open\n#lang racket\n") == (None, None)
    assert _racket_tier(b"#| license |#\n#lang scribble/manual\n(define (x) 1)\n", None)[0] == "text"
