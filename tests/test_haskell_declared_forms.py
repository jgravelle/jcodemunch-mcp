"""#722: Haskell extracted no symbols at all.

`HASKELL_SPEC` declared five node types and a name field for none of them, so
every declaration resolved to no name and was dropped; and `type_synon` is not
a node type the grammar emits (it spells it `type_synomym`, its own typo). The
grammar was installed and parsed every fixture here without error, so a green
"haskell is supported" row said nothing about it.

Each test below is one decision made while fixing it, outcome first.
"""

import pytest

from jcodemunch_mcp.parser.extractor import parse_file


@pytest.fixture(autouse=True)
def _all_languages_enabled(monkeypatch):
    """Answer the parser, not the developer's config file (#411)."""
    import jcodemunch_mcp.config as config

    monkeypatch.setattr(config, "is_language_enabled", lambda *a, **k: True)


def _rows(source):
    symbols = parse_file(source, "M.hs", "haskell")
    by_id = {s.id: s for s in symbols}
    return [
        (s.kind, s.name, by_id[s.parent].name if s.parent else None) for s in symbols
    ]


REPORTED = (
    "module M where\n"
    "data Color = Red | Green\n"
    "newtype Wrap = Wrap Int\n"
    "type Syn = Int\n"
    "class Show a where\n"
    "  showIt :: a -> String\n"
    "adder :: Int -> Int\n"
    "adder x = x + 1\n"
)


def test_the_reported_file_yields_every_declaration():
    assert _rows(REPORTED) == [
        ("type", "Color", None),
        ("type", "Wrap", None),
        ("type", "Syn", None),
        ("class", "Show", None),
        ("method", "showIt", "Show"),
        ("function", "adder", None),
    ]


def test_a_top_level_signature_does_not_double_count_its_function():
    """`adder :: ...` and `adder x = ...` are one function, not two symbols."""
    names = [name for _, name, _ in _rows(REPORTED)]
    assert names.count("adder") == 1


def test_a_multi_clause_function_is_one_symbol_spanning_its_clauses():
    """Pattern-matched clauses are one function. Indexed separately they
    collected `~1`/`~2` ordinals, the #763 shape."""
    source = "module M where\nfact :: Int -> Int\nfact 0 = 1\nfact n = n * fact (n - 1)\nother = 2\n"
    symbols = parse_file(source, "M.hs", "haskell")
    assert [(s.name, "~" in s.id) for s in symbols] == [("fact", False), ("other", False)]
    fact = symbols[0]
    # The signature is part of the function: `get_symbol_source` should show it.
    assert (fact.line, fact.end_line) == (2, 4)
    assert fact.signature == "fact :: Int -> Int"


def test_a_binding_with_no_arguments_is_a_function():
    """`main = do ...` is the grammar's `bind`, not its `function`, and it is
    the entry point of every Haskell program."""
    source = 'module Main where\nmain :: IO ()\nmain = putStrLn "x"\n'
    assert _rows(source) == [("function", "main", None)]


def test_a_class_method_with_a_default_is_one_method():
    source = "module M where\nclass Shape a where\n  area :: a -> Int\n  area _ = 0\n"
    assert _rows(source) == [("class", "Shape", None), ("method", "area", "Shape")]


def test_instance_methods_are_owned_by_the_instance_not_the_module():
    """Left at module level they were two more functions called `area`."""
    source = (
        "module M where\n"
        "data A = A\n"
        "data B = B\n"
        "class Shape a where\n"
        "  area :: a -> Int\n"
        "instance Shape A where\n"
        "  area _ = 1\n"
        "instance Shape B where\n"
        "  area _ = 2\n"
    )
    rows = _rows(source)
    assert ("method", "area", "Shape A") in rows
    assert ("method", "area", "Shape B") in rows
    assert all("~" not in s.id for s in parse_file(source, "M.hs", "haskell"))


def test_a_where_binding_is_a_local_and_is_not_indexed():
    source = "module M where\nouter x = helper x\n  where helper y = y + 1\n        k = 2\n"
    assert _rows(source) == [("function", "outer", None)]


def test_a_haddock_comment_is_the_docstring_including_on_the_first_declaration():
    """The comment above the module's first declaration sits outside the
    `declarations` node, so a sibling walk alone finds it for every
    declaration except the first."""
    source = (
        "module M where\n"
        "-- | A stack.\n"
        "data Stack = Empty\n"
        "-- | Push a value.\n"
        "push :: Int -> Stack\n"
        "push _ = Empty\n"
    )
    docs = {s.name: s.docstring for s in parse_file(source, "M.hs", "haskell")}
    assert docs == {"Stack": "A stack.", "push": "Push a value."}


def test_a_signature_written_over_several_lines_is_kept_whole():
    """Review found the first draft kept a signature's first LINE, so the
    standard long-signature layout published `signature='f'`."""
    source = "module M where\nf\n  :: Int\n  -> Int\nf x = x\n"
    (f,) = parse_file(source, "M.hs", "haskell")
    assert (f.signature, f.line, f.end_line) == ("f :: Int -> Int", 2, 5)


@pytest.mark.parametrize("label, source", [
    ("bird tracks", "A stack module.\n\n> module M where\n> data Stack = Empty\n\nProse.\n\n> push :: Int -> Stack\n> push _ = Empty\n"),
    ("code blocks", "Intro.\n\\begin{code}\nmodule M where\ndata Stack = Empty\n\\end{code}\nProse.\n\\begin{code}\npush :: Int -> Stack\npush _ = Empty\n\\end{code}\n"),
])
def test_literate_haskell_is_read_and_spans_index_the_original_file(label, source):
    """`.lhs` is on the supported row and yielded nothing in either style. The
    prose is blanked in place, so a span must slice the ORIGINAL bytes."""
    symbols = parse_file(source, "M.lhs", "haskell")
    assert [(s.kind, s.name) for s in symbols] == [("type", "Stack"), ("function", "push")], label
    push = symbols[1]
    raw = source.encode()[push.byte_offset:push.byte_offset + push.byte_length].decode()
    assert "push :: Int -> Stack" in raw and "push _ = Empty" in raw
    assert source.splitlines()[push.line - 1].endswith("push :: Int -> Stack")


def test_a_several_line_signature_in_a_bird_track_file_carries_no_track():
    """The two round-one fixes composed into a defect neither test could see:
    text was sliced from the ORIGINAL bytes, so every continuation line of a
    signature published its `>`. Text reads the unlit view; spans do not."""
    source = (
        "> module M where\n"
        "> -- | Adds.\n"
        "> f :: Int\n>   -> Int\n> f x = x\n"
        "> class Shape a where\n>   area :: a\n>     -> Double\n"
    )
    found = {s.qualified_name: s for s in parse_file(source, "M.lhs", "haskell")}
    assert found["f"].signature == "f :: Int -> Int"
    assert found["f"].docstring == "Adds."
    assert found["Shape.area"].signature == "area :: a -> Double"
    f = found["f"]
    raw = source.encode()[f.byte_offset:f.byte_offset + f.byte_length].decode()
    assert raw == "f :: Int\n>   -> Int\n> f x = x"


def test_a_code_block_marker_may_carry_options():
    source = "Prose.\n\\begin{code}[hide]\nmodule M where\nf = 1\n\\end{code}\n"
    assert [s.name for s in parse_file(source, "M.lhs", "haskell")] == ["f"]


def test_the_code_environment_is_named_code_and_nothing_longer():
    """Options or whitespace may follow the brace; another environment may not
    open a block. ⚠ The `codeblock` row is a PIN, not a regression: review
    reported it as broken under the prefix match, and it was not (the prefix
    included the closing brace). The `{code}x` row is the one the prefix match
    got wrong."""
    source = (
        "\\begin{codeblock}\nprose = 1\n\\end{codeblock}\n"
        "\\begin{code}x\nalso_prose = 1\n\\end{code}x\n"
        "\\begin{code}\nreal = 2\n\\end{code}\n"
    )
    assert [s.name for s in parse_file(source, "M.lhs", "haskell")] == ["real"]


def test_a_block_haddock_comment_loses_its_delimiters():
    source = "module M where\n{- | Block doc\n   second line -}\nf = 1\n"
    (f,) = parse_file(source, "M.hs", "haskell")
    assert f.docstring == "Block doc\nsecond line"


def test_an_instance_head_written_over_several_lines_is_kept_whole():
    source = "module M where\ndata C = C\nclass S a where\n  m :: a\ninstance S\n    C where\n  m = C\n"
    heads = {s.name: s.signature for s in parse_file(source, "M.hs", "haskell") if s.kind == "class"}
    assert heads == {"S": "class S a where", "S C": "instance S C where"}


def test_a_bird_track_is_prose_in_an_ordinary_hs_file():
    """The unlit pass is keyed on `.lhs`: in `.hs` a leading `>` is not code."""
    assert parse_file("> f = 1\n", "M.hs", "haskell") == []


def test_an_operator_is_indexed_in_prefix_form_only():
    """Pinned because the first CHANGELOG draft said no operator is indexed.
    The infix form has no `name` field in this grammar."""
    source = "module M where\n(<+>) a b = a\nx |> f = f x\n"
    assert _rows(source) == [("function", "(<+>)", None)]


def test_the_type_arrow_is_not_a_function():
    """The grammar names the `->` of a type `function` as well. It carries no
    name, and this pins that it never becomes a symbol."""
    source = "module M where\ntype F = Int -> Int\n"
    assert _rows(source) == [("type", "F", None)]


def test_every_spec_node_type_is_one_the_grammar_emits():
    """`type_synon` sat in the spec for the life of the language and matched
    nothing. Walk a fixture holding every form and require each spec key."""
    from tree_sitter_language_pack import get_parser

    from jcodemunch_mcp.parser.languages import LANGUAGE_REGISTRY

    source = (
        "module M where\ndata D = D\nnewtype N = N Int\ntype S = Int\n"
        "class C a where\n  m :: a\ninstance C D where\n  m = D\nf x = x\nb = 1\n"
    )
    seen = set()

    def walk(node):
        seen.add(node.type)
        for child in node.children:
            walk(child)

    walk(get_parser("haskell").parse(source.encode()).root_node)
    spec = LANGUAGE_REGISTRY["haskell"]
    assert set(spec.symbol_node_types) <= seen
    assert set(spec.symbol_node_types) <= set(spec.name_fields)
