"""A base list written after a colon is read as bases (#1284, @skibadubskiybadubs).

`_parse_bases` read `extends`, `implements` and Python's `class A(B)`, and
nothing else. C# (`class Circle : IShape`), Kotlin, Swift, C++, Rust supertraits
and Ruby's `class A < B` all came back with no bases, so `get_class_hierarchy`
returned no ancestors and `find_implementations` found no implementers.

Most signatures below are ones the extractors store, measured on main
(including the C# attribute line and a `where` clause), and the Python
block-colon cases come from real indexes. They cover the spellings measured
so far, not every spelling a grammar allows. Scala and Dart's `extends B with
T` is the same defect in another spelling: "B with T" came back as one base.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.tools.get_class_hierarchy import _parse_bases


@pytest.mark.parametrize(
    "signature, expected",
    [
        ("public class Circle : IShape", ["IShape"]),
        (
            "[Transaction(TransactionMode.Manual)]\npublic class Cmd : Base<int>, IExternalCommand where T : new()",
            ["Base", "IExternalCommand"],
        ),
        ("public struct S : IA, IB<T>", ["IA", "IB"]),
        ("public interface I : J", ["J"]),
        ("public record R(int X) : Q;", ["Q"]),
        (
            "public sealed partial class P<T> : Dictionary<string, List<T>>, IDisposable",
            ["Dictionary", "IDisposable"],
        ),
        ("open class A : B()", ["B"]),
        ("object O : B", ["B"]),
        ("class C(val x: Int) : A(), B", ["A", "B"]),
        ("data class D(val m: Map<String, Int>) : Base(m.size)", ["Base"]),
        ("protocol P: Q", ["Q"]),
        ("class A: B, Q", ["B", "Q"]),
        ("struct S: Q", ["Q"]),
        ("class A : public B, private virtual C<int>", ["B", "C"]),
        ("struct S : B", ["B"]),
        ("trait T: Base + Clone", ["Base", "Clone"]),
        ("class A < B\nend", ["B"]),
        ("class M < Foo::Bar\nend", ["Foo::Bar"]),
        ("class A extends B with T", ["B", "T"]),
        ("class A extends B with M implements I", ["B", "M", "I"]),
        # Round 2 of review: stored shapes the first fix missed.
        ("public record struct RS(int X) : I;", ["I"]),
        ("enum class E(val v: Int) : I", ["I"]),
        ("public class W\n    : Base, IFoo", ["Base", "IFoo"]),
        ("public class M\n    : Base,\n      IFoo", ["Base", "IFoo"]),
        ("class P private constructor(x: Int) : B(x)", ["B"]),
        ("class Q @Inject constructor(x: Int) : B(x)", ["B"]),
        ("class A final : public B", ["B"]),
        ("struct N::S : C", ["C"]),
        ("class G : public ::ns::B", ["ns::B"]),
        ("enum Raw: String, Codable", ["Codable"]),
        ("class A extends B<int> with M", ["B", "M"]),
        ("class A extends B[T] with M", ["B", "M"]),
        # Review round 3: C++ attributes before the name, a raw type Swift allows,
        # generic implements/extends lists, and an arrow inside type arguments.
        ("struct alignas(16) V : B", ["B"]),
        ("class __declspec(dllexport) D : public B", ["B"]),
        ("class [[nodiscard]] R : public B", ["B"]),
        ("class A implements I<T>, J", ["I", "J"]),
        ("interface X extends B<T>, C<U>", ["B", "C"]),
        ("class Z extends B<(x: number) => void>", ["B"]),
        ("class K : Base<(Int) -> Unit>, I", ["Base", "I"]),
    ],
)
def test_every_stored_spelling_yields_its_bases(signature, expected):
    assert _parse_bases(signature) == expected


@pytest.mark.parametrize(
    "signature, expected",
    [
        # A comment inside the list neither adds a base nor hides the next one.
        ("class A : B, // why, C\n  D", ["B", "D"]),
        ("class A: B /* note, C */", ["B"]),
        ("public class W\n    : Base, // first\n      IFoo", ["Base", "IFoo"]),
    ],
)
def test_a_comment_in_a_base_list_is_not_a_base(signature, expected):
    assert _parse_bases(signature) == expected


@pytest.mark.parametrize(
    "signature",
    [
        # Measured from real indexes (review round 3): Python stores `class X:`
        # with the comment lines under it. Comment words are never bases.
        "class GeoIP2:\r\n    # Try MODE_MMAP_EXT, MODE_MMAP, MODE_FILE in that order.",
        "class BaseManager:\r\n    # To retain order, track",
        "class _Mgr:\n    # see AUTH_ENABLED=false",
        "class Foo:  # noqa: B903, for Python2 compatibility",
        "class BaseManager:\n    # Try Foo, Bar in that order.",
    ],
)
def test_a_python_block_colon_is_not_a_base_list(signature):
    assert _parse_bases(signature) == []
    assert _parse_bases(signature, "python") == []


def test_python_never_takes_the_colon_branch():
    """A Python docstring line could still look like a list; the language rules it out."""
    sig = 'class Doc:\n    """Base, Other: notes"""'
    assert _parse_bases(sig, "python") == []


def test_a_list_after_a_trailing_colon_is_read_when_the_language_is_known():
    """C# allows `class W :` with the bases on the next line. Without a
    language the trailing colon is taken as Python's block colon."""
    sig = "public class W :\n    Base, IFoo"
    assert _parse_bases(sig, "csharp") == ["Base", "IFoo"]
    assert _parse_bases(sig) == []


@pytest.mark.parametrize(
    "signature",
    [
        "class Standalone",
        "public class Plain",
        "class C(val x: Int)",  # a Kotlin parameter's `: Int` is not a base
        "public record R(int X);",
        "struct X;",
        "trait Base",
        "class B\nend",
        "def f(x: int) -> int",
        "class Box<T : Comparable<T>>",  # a Kotlin bound inside <...> is not a base
        # An enum's `:` names its underlying or raw type, not a base.
        "public enum E : byte",
        "enum class E : uint8_t",
        "enum E : unsigned int",
        "enum Raw: Int",
        "enum E: CGFloat",
    ],
)
def test_no_base_list_means_no_bases(signature):
    assert _parse_bases(signature) == [], signature


@pytest.mark.parametrize(
    "signature, expected",
    [
        ("class Dog(Animal)", ["Animal"]),
        ("class A(B)", ["B"]),
        # Python's own `:` is read by the paren branch first, never as a list.
        ("class Foo(Bar): pass", ["Bar"]),
        ("class A<T extends X> extends B<T> implements C", ["B", "C"]),
        ("class Dog extends Animal implements IMovable", ["Animal", "IMovable"]),
        ("class Dog implements IAnimal, IMovable", ["IAnimal", "IMovable"]),
    ],
)
def test_the_spellings_that_worked_are_unchanged(signature, expected):
    assert _parse_bases(signature) == expected


def _index(tmp_path, files: dict[str, str]):
    from jcodemunch_mcp.tools.index_folder import index_folder

    src = tmp_path / "src"
    src.mkdir()
    for name, text in files.items():
        (src / name).write_text(text, encoding="utf-8")
    store = tmp_path / "store"
    r = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert r["success"], r
    return r["repo"], str(store)


CSHARP = (
    "public interface IShape {}\n"
    "public class Circle : IShape {}\n"
    "public class Cmd : IExternalCommand {}\n"
)


def test_the_reported_csharp_hierarchy(tmp_path):
    from jcodemunch_mcp.tools.get_class_hierarchy import get_class_hierarchy

    repo, store = _index(tmp_path, {"shapes.cs": CSHARP})
    circle = get_class_hierarchy(repo=repo, class_name="Circle", storage_path=store)
    assert [a["name"] for a in circle["ancestors"]] == ["IShape"], circle
    cmd = get_class_hierarchy(repo=repo, class_name="Cmd", storage_path=store)
    assert [(a["name"], a["file"]) for a in cmd["ancestors"]] == [
        ("IExternalCommand", "(external)")
    ], cmd


def test_the_reported_csharp_implementations(tmp_path):
    from jcodemunch_mcp.tools.find_implementations import find_implementations

    repo, store = _index(tmp_path, {"shapes.cs": CSHARP})
    r = find_implementations(repo=repo, symbol="IShape", storage_path=store)
    assert "Circle" in [i["name"] for i in r["implementations"]], r


PYTHON_COMMENT_NAMES_A_CLASS = (
    "class Bar:\n    pass\n\n\nclass Foo:\n    # unlike Bar, this one\n    x = 1\n"
)


def test_a_python_comment_naming_a_class_is_not_a_subclass(tmp_path):
    """The stored signature of Foo is `class Foo:` plus its comment line, which
    names Bar. Neither tool may report Foo as Bar's subclass."""
    from jcodemunch_mcp.tools.find_implementations import find_implementations
    from jcodemunch_mcp.tools.get_class_hierarchy import get_class_hierarchy

    repo, store = _index(tmp_path, {"m.py": PYTHON_COMMENT_NAMES_A_CLASS})
    foo = get_class_hierarchy(repo=repo, class_name="Foo", storage_path=store)
    assert foo["ancestors"] == [], foo
    r = find_implementations(repo=repo, symbol="Bar", storage_path=store)
    assert "Foo" not in [i["name"] for i in r["implementations"]], r
