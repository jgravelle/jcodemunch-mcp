"""Every tracked-gap entry cites an issue that exists and is OPEN (#758).

A gap ledger (`_KNOWN_GAPS`, `_CONFIRMED_GAPS`, `_GAPS`) removes a form from a
check. That is the right mechanism for a known, tracked defect, and it makes
the ledger the one place in its file where adding a line makes a check ask
LESS. Each ledger already fails when its gap CLOSES; nothing checked how an
entry ARRIVES, so a free-text reason, or an invented issue number, silenced a
regression with every gate green.

Rulings:
- Every entry names at least one issue (`#NNN`) that the committed manifest
  `tests/fixtures/gap_ledger_issues.json` records as OPEN.
- The manifest is OFFLINE: the suite never reaches the network, and a test
  needing a token is a test that gets skipped. `scripts/gap_ledgers.py
  --refresh` rewrites it with `gh`, so a closed issue whose entry is still
  present shows up as a failure here.
- ⚠⚠ Ledgers are FOUND, never listed: `find_ledgers` scans `tests/` for a
  module-level `_*GAPS` literal, so a seventh ledger inherits the rule on
  arrival. The issue named three; the scan found six.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location("gap_ledgers", _ROOT / "scripts" / "gap_ledgers.py")
gap_ledgers = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gap_ledgers)

_MANIFEST = _ROOT / "tests" / "fixtures" / "gap_ledger_issues.json"


def _manifest() -> dict[int, str]:
    data = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    return {int(k): v for k, v in data["issues"].items()}


def test_every_gap_ledger_in_the_suite_is_found():
    """The scan's floor: the six ledgers that exist today. A scan that went
    blind would make the rule below vacuous, so its reach is asserted."""
    found = {(p.name, name) for p, name, _line, _value in gap_ledgers.find_ledgers(_ROOT / "tests")}
    assert {
        ("test_language_spec_maps_agree.py", "_KNOWN_GAPS"),
        ("test_grammar_spelled_forms.py", "_CONFIRMED_GAPS"),
        ("test_declared_forms_extract.py", "_KNOWN_GAPS"),
        ("test_member_kind_audit.py", "_GAPS"),
        ("test_one_declaration_binds_every_name.py", "_GAPS"),
        ("test_rust_fidelity.py", "_KNOWN_GAPS"),
    } <= found, found


def test_every_gap_entry_cites_an_open_issue():
    problems = gap_ledgers.check(_ROOT / "tests", _manifest())
    assert problems == [], "\n".join(problems)


def test_the_manifest_records_exactly_the_cited_issues():
    """A manifest row nobody cites is stale, and a cited issue with no row
    is one the refresh never saw. Either way: `scripts/gap_ledgers.py --refresh`."""
    assert set(_manifest()) == gap_ledgers.cited_issues(_ROOT / "tests")


def test_the_rule_rejects_planted_excuses(tmp_path):
    """Non-vacuity: the rule over a planted ledger reports every bad entry,
    and the one good one passes."""
    (tmp_path / "test_planted.py").write_text(
        "_KNOWN_GAPS = {\n"
        "    'a': 'no issue named at all',\n"
        "    'b': '#999999: an issue nobody opened',\n"
        "    'c': '#100: an issue that is closed',\n"
        "    'd': '#200: open and tracked',\n"
        "}\n"
        "_CONFIRMED_GAPS = {'x': [('form', 'free text only')]}\n"
        "_GAPS = {('k', 'r'): '#200'}\n"
        "_OTHER = {'not': 'a ledger'}\n",
        encoding="utf-8",
    )
    problems = gap_ledgers.check(tmp_path, {100: "CLOSED", 200: "OPEN"})
    joined = "\n".join(problems)
    assert len(problems) == 4, joined
    assert "'a'" in joined and "names no issue" in joined
    assert "#999999" in joined and "not in the manifest" in joined
    assert "#100" in joined and "CLOSED" in joined
    assert "'x'" in joined
    assert "_OTHER" not in joined and "'d'" not in joined


def test_a_ledger_that_is_not_a_literal_is_reported(tmp_path):
    """The rule reads ledgers with `ast.literal_eval`, never by importing a test
    module; a computed ledger cannot be read, so it cannot pass silently."""
    (tmp_path / "test_computed.py").write_text("_KNOWN_GAPS = dict(a='x')\n", encoding="utf-8")
    problems = gap_ledgers.check(tmp_path, {})
    assert len(problems) == 1 and "not a literal" in problems[0], problems
