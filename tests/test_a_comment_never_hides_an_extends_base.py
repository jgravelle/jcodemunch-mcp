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


def test_a_signature_without_a_comment_is_unchanged():
    assert _parse_bases("class A extends B implements C, D {", "java") == ["B", "C", "D"]
    assert _parse_bases("class A extends B<T> with M {", "dart") == ["B", "M"]
