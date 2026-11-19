"""A trailing comment after an `extends` or `implements` list never hides a base (LEDGER L-261).

`_EXTENDS_RE` and `_IMPLEMENTS_RE` ran on the signature with its comments in it,
so `class A extends B // c` returned no base at all: the list's end anchor (`$`
or a body brace) never matched past the comment. #1284 removed comments for the
`:` and Ruby `<` branches only; this pins the `extends`/`implements` branches.
"""

import pytest

from jcodemunch_mcp.tools.get_class_hierarchy import _parse_bases


@pytest.mark.parametrize(
    ("signature", "language", "expected"),
    [
        # The three spellings the ledger row names.
        ("class A extends B // c", "php", ["B"]),
        ("class A extends B # c", "php", ["B"]),
        ("class A extends B /* c */", "typescript", ["B"]),
        # The same shapes in the other extends languages.
        ("class A extends B // c", "typescript", ["B"]),
        ("class A extends B // c", "javascript", ["B"]),
        ("public class A extends B // c", "java", ["B"]),
        ("class A extends B with M // c", "dart", ["B", "M"]),
        ("class A extends B with M // c", "scala", ["B", "M"]),
        # A comment between the two lists, and after the second.
        ("class A extends B /* c */ implements C", "java", ["B", "C"]),
        ("class A extends B // c\n    implements C, D", "java", ["B", "C", "D"]),
        ("class A extends B implements C // c", "php", ["B", "C"]),
        ("class A implements C, D // c", "php", ["C", "D"]),
        ("class A implements C /* c */ {", "typescript", ["C"]),
        # A comment naming a word must not become a base.
        ("class A extends B // extends Fake", "typescript", ["B"]),
    ],
)
def test_a_trailing_comment_keeps_the_base(signature, language, expected):
    assert _parse_bases(signature, language) == expected


@pytest.mark.parametrize(
    ("signature", "expected"),
    [
        # A PHP 8 attribute starts with `#[` and is not a comment.
        ("#[Attr] class A extends B", ["B"]),
        ("#[Attr(\"x\")] class A extends B # c", ["B"]),
        ("#[Attr]\nclass A extends B implements C", ["B", "C"]),
    ],
)
def test_a_php_attribute_is_not_a_comment(signature, expected):
    assert _parse_bases(signature, "php") == expected


@pytest.mark.parametrize(
    ("signature", "language", "expected"),
    [
        # The same defect in the parenthesised branch: a comment inside a
        # multi-line Python base list dropped every base after it.
        ("class A(\n    B,  # base one\n    C,\n):", "python", ["B", "C"]),
        ("class A(B,  # (note)\n    C):", "python", ["B", "C"]),
        ("class A(  # bases\n    B):", None, ["B"]),
        ("class A(B, C):  # comment", "python", ["B", "C"]),
    ],
)
def test_a_comment_inside_a_python_base_list_keeps_the_bases(signature, language, expected):
    assert _parse_bases(signature, language) == expected


@pytest.mark.parametrize(
    ("signature", "language", "expected"),
    [
        # A `//` or `#` inside a single-quoted string is not a comment
        # (review of the first L-261 fix: each kept its base on origin/main).
        ("@Get('//x') class C extends B", "typescript", ["B"]),
        ("@Component({selector: 'a', template: '<a href=//x>'}) class D extends B", "typescript", ["B"]),
        ("#[Route('http://x')] class A extends B", "php", ["B"]),
        ("#[Attr('#')] class A extends B", "php", ["B"]),
        ("class A(B, tag='#')", "python", ["B"]),
        ("class A(B, Literal['#'])", "python", ["B"]),
        ("class A(B, tag='x#y', C)", "python", ["B", "C"]),
        ("class A(B, tag='#'):  # c", None, ["B"]),
        ("@Ann('//x') public class A extends B // c", "java", ["B"]),
    ],
)
def test_a_comment_marker_inside_a_single_quoted_string_is_kept(signature, language, expected):
    assert _parse_bases(signature, language) == expected


@pytest.mark.parametrize(
    "signature",
    [
        "class A extends B /* not implements X */ {",
        "class A extends B // c\n    implements X {",
    ],
)
def test_the_call_graph_reads_the_base_past_a_comment(signature):
    """`constructors_as_their_classes` cut `implements` off the raw text, so a
    comment naming the word ended the base list (review of the L-261 fix)."""
    from jcodemunch_mcp.tools._call_graph import constructors_as_their_classes

    symbols = [
        {"id": "m.ts::B#class", "kind": "class", "name": "B", "file": "m.ts",
         "language": "typescript", "signature": "class B {"},
        {"id": "m.ts::B.constructor#method", "kind": "method", "name": "constructor",
         "file": "m.ts", "language": "typescript", "parent": "m.ts::B#class"},
        {"id": "m.ts::A#class", "kind": "class", "name": "A", "file": "m.ts",
         "language": "typescript", "signature": signature},
    ]
    rows = constructors_as_their_classes(symbols, {})
    # B's constructor runs when A is created: one row for B, one for A.
    assert {row["name"] for row in rows if row["id"] == "m.ts::B.constructor#method"} == {"A", "B"}


@pytest.mark.parametrize(
    ("signature", "expected"),
    [
        # A C++14 digit separator is not an opening quote: paired with an
        # apostrophe in a later comment it kept the comment as a "string",
        # which invented a base or lost one (review round 2 of L-261).
        ("class A : public B<1'000> // don't, public Evil", ["B"]),
        ("class E : public B<1'0>, /* it's */ public C", ["B", "C"]),
        ("class A : public B<1'000> // , public X 'y", ["B"]),
        ("class A : public B<0xFF'FF>, /* it's */ public C", ["B", "C"]),
        ("class A : public B<0b1010'1010>, /* it's */ public C", ["B", "C"]),
        ("class A : public B<1'000'000.5>, /* it's */ public C", ["B", "C"]),
        # A character literal is still a literal, with or without an encoding
        # prefix (review round 3: `u8'x'` paired its closing quote with a comment).
        ("class A : public B<'/'>, public C // c", ["B", "C"]),
        ("class A : public B<u8'x'> // don't, public Evil", ["B"]),
        ("class A : public B<u8'x'>, /* it's */ public C", ["B", "C"]),
        ("class A : public B<L'x'>, /* it's */ public C", ["B", "C"]),
        ("class A : public B<u8'/'>, public C // c", ["B", "C"]),
    ],
)
def test_a_cpp_digit_separator_opens_no_string(signature, expected):
    assert _parse_bases(signature, "cpp") == expected


def test_a_spaced_php_hash_is_a_comment_not_an_attribute():
    # `#[` must be contiguous; `# [Attr]` comments out the rest of the line.
    assert _parse_bases("# [Attr] class A extends B", "php") == []


@pytest.mark.parametrize("language", ["astro", "razor", "al", "sql", "erlang", "fortran"])
def test_a_single_quote_language_keeps_its_strings(language):
    assert _parse_bases("@Get('//x') class A extends B", language) == ["B"]


def test_a_signature_without_a_comment_is_unchanged():
    assert _parse_bases("class A extends B implements C, D {", "java") == ["B", "C", "D"]
    assert _parse_bases("class A extends B<T> with M {", "dart") == ["B", "M"]
