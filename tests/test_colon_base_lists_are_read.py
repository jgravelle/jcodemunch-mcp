"""A base list written after a colon is read as bases (#1284, @skibadubskiybadubs).

`_parse_bases` read `extends`, `implements` and Python's `class A(B)`, and
nothing else. C# (`class Circle : IShape`), Kotlin, Swift, C++, Rust supertraits
and Ruby's `class A < B` all came back with no bases, so `get_class_hierarchy`
returned no ancestors and `find_implementations` found no implementers.

The signatures below are the ones the extractors store, measured on main
(including the C# attribute line and a `where` clause), not hand-written
shapes. Scala and Dart's `extends B with T` is the same defect in another
spelling: "B with T" came back as one base.
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
    ],
)
def test_every_stored_spelling_yields_its_bases(signature, expected):
    assert _parse_bases(signature) == expected


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
