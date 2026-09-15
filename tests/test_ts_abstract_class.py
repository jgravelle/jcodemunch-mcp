"""tree-sitter-typescript gives `abstract class X` its own node type.

`abstract_class_declaration` is distinct from `class_declaration`, so a spec
that maps only the latter sees no abstract class at all -- and, because
`container_node_types` is the same list, the methods declared inside one are
still extracted but attributed to nobody. The symbol id becomes
`<file>::<name>#method` instead of `<file>::Owner.<name>#method`, which is the
"a bare name is not an identity" failure the Rust fidelity harness already
pays for one language over.

An abstract base class is where a hierarchy declares its API, so this is not a
corner of the grammar: zod v3's whole public instance surface hangs off
`export abstract class ZodType`, and all 7 abstract classes in that tree were
absent from the index (#698).

Properties, not spellings: each test states an outcome a caller can observe.
"""

import pytest

from jcodemunch_mcp.parser.extractor import parse_file
from jcodemunch_mcp.parser.languages import LANGUAGE_REGISTRY

# The TypeScript family: both grammars emit `abstract_class_declaration`, and
# a fix applied to one spec reaches only half the product.
_TS_FAMILY = ("typescript", "tsx")

_FILENAME = {"typescript": "a.ts", "tsx": "a.tsx"}

# Both spellings ship in real code and they are different nodes to the grammar
# only in their `export` wrapper, which is exactly why fixing one is no
# evidence about the other.
_DECLARATIONS = ("export abstract class", "abstract class")


def _source(declaration: str) -> str:
    return f"""
{declaration} Base {{
  abstract validate(x: unknown): boolean;

  describe(): string {{
    return "base";
  }}
}}

class Concrete extends Base {{
  validate(x: unknown): boolean {{
    return true;
  }}
}}
"""


@pytest.mark.parametrize("language", _TS_FAMILY)
@pytest.mark.parametrize("declaration", _DECLARATIONS)
def test_abstract_class_is_extracted_as_a_class(language, declaration):
    """A caller asking where `Base` is defined gets nothing without this."""
    symbols = parse_file(_source(declaration), _FILENAME[language], language)
    classes = {s.name for s in symbols if s.kind == "class"}
    assert "Base" in classes, f"abstract class missing; got {sorted(classes)}"


@pytest.mark.parametrize("language", _TS_FAMILY)
@pytest.mark.parametrize("declaration", _DECLARATIONS)
def test_methods_of_an_abstract_class_keep_their_owner(language, declaration):
    """`describe` declared in `Base` must not be filed under a bare name.

    Two abstract classes in one file each declaring `create` would otherwise
    produce ids differing only by path.
    """
    symbols = parse_file(_source(declaration), _FILENAME[language], language)
    describe = [s for s in symbols if s.name == "describe" and s.kind == "method"]
    assert describe, "method inside the abstract class was not extracted"
    assert all("Base.describe" in s.id for s in describe), (
        f"method lost its owner: {[s.id for s in describe]}"
    )


@pytest.mark.parametrize("language", _TS_FAMILY)
@pytest.mark.parametrize("declaration", _DECLARATIONS)
def test_concrete_class_in_the_same_file_is_unchanged(language, declaration):
    """Non-vacuity: the ordinary path must still work, or the assertions above
    would pass against an extractor that had simply stopped distinguishing
    containers at all."""
    symbols = parse_file(_source(declaration), _FILENAME[language], language)
    assert "Concrete" in {s.name for s in symbols if s.kind == "class"}
    validate = [s for s in symbols if s.name == "validate" and s.kind == "method"]
    owners = {s.id.split("::", 1)[1].split(".", 1)[0] for s in validate}
    assert owners == {"Base", "Concrete"}, f"owners were {sorted(owners)}"


@pytest.mark.parametrize("language", _TS_FAMILY)
@pytest.mark.parametrize("declaration", _DECLARATIONS)
def test_abstract_member_is_extracted_as_a_method(language, declaration):
    """`abstract validate(): boolean;` is `abstract_method_signature`, a second
    node type in the same family.

    It is the half of the base class that declares the contract, so a spec that
    reaches the class but not its abstract members indexes the hierarchy's API
    as whatever the subclasses happened to implement.
    """
    symbols = parse_file(_source(declaration), _FILENAME[language], language)
    validate = [s for s in symbols if s.name == "validate" and s.kind == "method"]
    ids = {s.id for s in validate}
    assert any("Base.validate" in i for i in ids), (
        f"abstract member missing from {sorted(ids)}"
    )


@pytest.mark.parametrize("language", _TS_FAMILY)
def test_spec_maps_the_abstract_node_wherever_it_maps_the_plain_one(language):
    """The ratchet: a TS-family spec may not know about `class_declaration`
    and not know about `abstract_class_declaration`.

    Written over the property rather than the two specs that exist today, so a
    third TypeScript-family spec added later inherits the rule instead of
    reintroducing the defect.
    """
    spec = LANGUAGE_REGISTRY[language]
    assert spec.symbol_node_types.get("class_declaration") == "class"
    assert spec.symbol_node_types.get("abstract_class_declaration") == "class", (
        "abstract_class_declaration is not a symbol node type"
    )
    assert "abstract_class_declaration" in spec.name_fields, (
        "abstract_class_declaration has no name field, so it extracts unnamed"
    )
    assert "abstract_class_declaration" in spec.container_node_types, (
        "abstract_class_declaration is not a container, so its methods orphan"
    )
    assert spec.symbol_node_types.get("abstract_method_signature") == "method", (
        "an abstract member is not method_definition and needs its own entry"
    )
    assert "abstract_method_signature" in spec.name_fields
