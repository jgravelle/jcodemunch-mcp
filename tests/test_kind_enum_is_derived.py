"""The published `kind` enum and the runtime gate are one list (#571).

Found by @devtomnl. `field` has been emitted by the Python parser since the
dataclass-fields change and was in neither gate — 399 of them in this repo's own
index, and `search_symbols(kind="field")` was refused at both.

⚠⚠ **The divergence was invisible because each side looked internally
consistent.** The wire enum was a hand-written literal and `VALID_KINDS` a
frozenset, and nothing compared them; a reader of either one sees seven kinds
and no reason to doubt it. That is the second-copy mechanism this project keeps
paying for, so the fix is derivation and this file is the ratchet under it.

⚠ Ordering is load-bearing and is tested here for a reason that is not
aesthetics: the enum sits in the CACHED PREFIX, and `frozenset` iteration order
over strings varies with per-process hash randomisation. Publishing the set
directly would serve a different schema on every server start for the same
build.
"""

from __future__ import annotations

import ast
import json
import pathlib

import pytest

from jcodemunch_mcp.parser.symbols import KIND_ORDER, VALID_KINDS

SERVER = pathlib.Path(__file__).resolve().parents[1] / "src" / "jcodemunch_mcp" / "server.py"


def test_field_is_a_valid_kind():
    """The reported bug, stated as the outcome rather than the mechanism."""
    assert "field" in VALID_KINDS


def test_valid_kinds_is_exactly_the_published_order():
    assert VALID_KINDS == frozenset(KIND_ORDER)
    assert len(KIND_ORDER) == len(set(KIND_ORDER)), f"duplicate in {KIND_ORDER}"


def test_order_is_a_sequence_not_a_set():
    """A set cannot be published: str hashing is randomised per process.

    Asserting the TYPE is the only way to catch a future edit that "simplifies"
    `KIND_ORDER` back into a set literal — the served schema would then differ
    run to run and nothing else here would notice.
    """
    assert isinstance(KIND_ORDER, tuple)


def test_published_enum_is_derived_not_a_literal():
    """⚠ Reads the AST, not the served dict.

    A shipped literal that happens to equal `KIND_ORDER` today passes any
    value-equality check and drifts the moment a kind is added — which is
    precisely the history being fixed. This asserts the SHAPE of the source.
    """
    tree = ast.parse(SERVER.read_text(encoding="utf-8"))
    literal_enums: list[int] = []
    derived = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if not (isinstance(key, ast.Constant) and key.value == "enum"):
                continue
            names = {n.id for n in ast.walk(value) if isinstance(n, ast.Name)}
            if "KIND_ORDER" in names:
                derived += 1
                continue
            if isinstance(value, (ast.List, ast.Tuple)):
                items = [
                    e.value for e in value.elts
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)
                ]
                if "function" in items and "method" in items:
                    literal_enums.append(getattr(value, "lineno", -1))
    assert derived >= 1, "no `enum` in server.py derives from KIND_ORDER"
    assert not literal_enums, (
        "a hardcoded symbol-kind enum is back in server.py at line(s) "
        f"{literal_enums} — derive it from KIND_ORDER (#571)"
    )


def test_the_literal_predicate_fires_on_the_known_bad_shape():
    """Non-vacuity: the check above must FAIL against the pre-fix source."""
    bad = ast.parse(
        'x = {"kind": {"type": "string",\n'
        '  "enum": ["function", "class", "method", "constant", "type",\n'
        '           "template", "import"]}}\n'
    )
    found = []
    for node in ast.walk(bad):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if isinstance(key, ast.Constant) and key.value == "enum":
                names = {n.id for n in ast.walk(value) if isinstance(n, ast.Name)}
                if "KIND_ORDER" in names:
                    continue
                if isinstance(value, (ast.List, ast.Tuple)):
                    items = [
                        e.value for e in value.elts
                        if isinstance(e, ast.Constant) and isinstance(e.value, str)
                    ]
                    if "function" in items and "method" in items:
                        found.append(value.lineno)
    assert found, "the predicate cannot see the defect it is written against"


@pytest.mark.asyncio
async def test_the_wire_schema_serves_every_valid_kind():
    """⚠ Verify at the user's entry point.

    `search_symbols(kind=...)` called directly in Python never touched either
    gate, so the defect was invisible from a unit test of the tool. Both gates
    live at the dispatcher.
    """
    from jcodemunch_mcp.server import list_tools

    tools = await list_tools()
    by_name = {t.name: t for t in tools}
    if "search_symbols" not in by_name:
        pytest.skip("search_symbols not on this surface")
    schema = by_name["search_symbols"].inputSchema
    kind = schema.get("properties", {}).get("kind", {})
    if "enum" not in kind:
        pytest.skip("kind enum demoted on this surface (compact schemas)")
    assert kind["enum"] == list(KIND_ORDER)
    assert "field" in kind["enum"]
    # Serialisable and stable: this is what goes on the wire.
    assert json.loads(json.dumps(kind["enum"])) == list(KIND_ORDER)


@pytest.mark.asyncio
async def test_the_runtime_gate_accepts_field():
    """The second gate. Fixing the schema alone leaves this one refusing."""
    from jcodemunch_mcp.server import VALID_KINDS as dispatcher_kinds

    assert "field" in dispatcher_kinds


def test_every_spec_kind_is_a_valid_kind():
    """⚠⚠ A kind a LanguageSpec can emit must be a kind the wire can carry (#732).

    #571's fix derived the schema enum from `KIND_ORDER` so the enum and the
    tuple could not drift. That closed the copy and left the other direction
    open: nothing checked that a kind a SPEC maps a node type to is in the
    tuple at all. `PHP_SPEC` mapped `property_declaration` to `property` from
    before #571, and the entry never fired -- a declared-but-dead kind, which
    both gates would have rejected the moment anything emitted one. Kotlin
    became the first live emitter in #732 and the whole suite stayed green,
    because 10,741 tests contained nothing that asked this question.

    The failure it prevents is precise: `search_symbols(kind="<new>")` is
    REFUSED by `server.py`'s `kind_filter not in VALID_KINDS` check, and the
    published schema enum omits the value, so the kind is emitted into the
    index and unreachable through the one filter meant to find it.

    ⚠ `KIND_ORDER` is APPEND-ONLY (its own comment says why: each position is
    bytes a client has already cached). So the fix for a failure here is to
    append the kind, never to reword a spec to dodge it.
    """
    from jcodemunch_mcp.parser.languages import LANGUAGE_REGISTRY
    from jcodemunch_mcp.parser.symbols import VALID_KINDS

    offenders = {}
    for language, spec in sorted(LANGUAGE_REGISTRY.items()):
        for node_type, kind in (getattr(spec, "symbol_node_types", None) or {}).items():
            if kind not in VALID_KINDS:
                offenders.setdefault(kind, []).append(f"{language}:{node_type}")

    assert not offenders, (
        f"these spec kinds are not in KIND_ORDER, so a symbol carrying one is "
        f"unreachable by `search_symbols(kind=...)` and absent from the wire "
        f"enum: {offenders}. Append to KIND_ORDER (never reorder)."
    )
