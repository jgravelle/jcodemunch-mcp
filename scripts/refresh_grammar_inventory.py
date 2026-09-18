"""Rewrite the unnamed-declaration inventory from the compiled grammars (#724).

`tests/test_grammar_spelled_forms.py` fails when the set of declaration forms
a grammar spells and no `LanguageSpec` names has changed. That failure is a
DECISION to make, not a file to regenerate reflexively:

* the form is a symbol we should extract -> declare it in
  `src/jcodemunch_mcp/parser/languages.py`, and the inventory shrinks on its
  own;
* the form is correctly ignored (`parameter_declaration`,
  `local_variable_declaration`, `catch_declaration`) -> run this script in the
  same commit that changed the grammar or the spec.

⚠ Running this to make a red test green, without reading the diff it writes,
is the one use this script must not have. The diff is the review.

⚠⚠ This is deliberately a SCRIPT and not a `--update` flag on the test. A flag
sits one keystroke from the failing run and gets used reflexively; a separate
command has to be typed on purpose, and shows up in the diff as its own edit.
"""

import json
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parent.parent
BASELINE = ROOT / "tests" / "fixtures" / "grammar_declaration_inventory.json"

_README = [
    "Written by scripts/refresh_grammar_inventory.py (#724). Do NOT hand-edit to",
    "silence a failure.",
    "",
    "Each entry is a node type the compiled grammar EMITS and the language's",
    "recognised set does not contain -- either its LanguageSpec.symbol_node_types",
    "(source `spec`) or the node-type literals its _parse_<lang>_symbols function",
    "matches on (source `inline`). See `sources` below for which applies.",
    "",
    "The recognised set reads ALL FOUR extraction channels since #757 --",
    "symbol_node_types, constant_patterns, field_patterns and variable_patterns",
    "-- so a form extracted through any of them is NOT listed here. That fixed",
    "the direction in which this artifact was worst: #735 indexed every Java",
    "field through field_patterns and java/field_declaration stayed listed; and",
    "on #743's branch, moving php/property_declaration into the same channel",
    "GREW the count, in the change that fixed it. Every form the widening",
    "suppresses a row for carries a sample in",
    "tests/test_inventory_reads_every_channel.py, proved by deletion, so a form",
    "that stops extracting comes back here.",
    "",
    "An entry is still NOT a proof that the form is unextractable, because two",
    "routes remain outside those channels: a container_node_types entry (rust/",
    "impl_item is one, and deliberately emits nothing itself) and a node type",
    "matched as a literal inside the extractor (cpp/namespace_definition). So the",
    "entry means: no channel names this form, and whether the extractor reaches",
    "it another way is a question, not a given. Six rows",
    "(python/with_item, bash/case_item, swift/capture_list_item,",
    "swift/tuple_type_item, rust/attribute_item, rust/inner_attribute_item) are not",
    "declarations at all and are left in rather than special-cased.",
    "",
    "`confirmed_gaps` are the rows verified through parse_file to yield no symbol.",
    "",
    "A change here is a DECISION: either the form is a symbol (declare it in",
    "languages.py) or it is correctly ignored (regenerate in the same commit).",
    "Regenerate: uv run python scripts/refresh_grammar_inventory.py",
]


def main() -> int:
    sys.path.insert(0, str(ROOT / "tests"))
    from test_grammar_spelled_forms import (
        _CONFIRMED_GAPS,
        _current_inventory,
        _inventory_sources,
    )

    inventory = _current_inventory()
    sources = _inventory_sources()
    payload = {
        "_README": _README,
        "confirmed_gaps": {
            k: [nt for nt, _why in v] for k, v in sorted(_CONFIRMED_GAPS.items())
        },
        "sources": {k: sources[k] for k in sorted(inventory)},
        "inventory": {k: inventory[k] for k in sorted(inventory)},
    }

    before = BASELINE.read_text(encoding="utf-8") if BASELINE.exists() else ""
    after = json.dumps(payload, indent=2) + "\n"
    BASELINE.write_text(after, encoding="utf-8")

    entries = sum(len(v) for v in payload["inventory"].values())
    print(
        f"{BASELINE.relative_to(ROOT)}: "
        f"{len(payload['inventory'])} languages, {entries} unnamed forms"
    )
    print(
        "unchanged" if before == after else "CHANGED -- read the diff before committing"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
