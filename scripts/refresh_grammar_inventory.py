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
    "Written by scripts/refresh_grammar_inventory.py (#724). Do NOT hand-edit",
    "to silence a failure. Each entry is a node type the compiled grammar emits and",
    "the LanguageSpec does not name, so the form is never extracted.",
    "A change here is a DECISION: either the form is a symbol (declare it in",
    "languages.py) or it is correctly ignored (update this file in the same commit).",
    "Regenerate: uv run python scripts/refresh_grammar_inventory.py",
]


def main() -> int:
    sys.path.insert(0, str(ROOT / "tests"))
    from test_grammar_spelled_forms import _CONFIRMED_GAPS, _current_inventory

    inventory = _current_inventory()
    payload = {
        "_README": _README,
        "confirmed_gaps": {k: v[0] for k, v in sorted(_CONFIRMED_GAPS.items())},
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
