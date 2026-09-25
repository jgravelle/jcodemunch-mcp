"""Every tracked-gap entry cites an issue that exists and is OPEN (#758).

A gap ledger (`_KNOWN_GAPS`, `_KNOWN_GHOSTS`, `KNOWN_UNENCODED`,
`ALLOWED_UNTIL_FIXED`, ...) removes a known defect from a check. That is the
right mechanism for a tracked defect, and it makes the ledger the one place in
its file where adding a line makes a check ask LESS. Each ledger already fails
when its gap CLOSES; nothing checked how an entry ARRIVES, so a free-text
reason, or an invented issue number, silenced a regression with every gate
green.

Rulings:
- Every entry names at least one issue (`#NNN`) that the committed manifest
  `tests/fixtures/gap_ledger_issues.json` records as OPEN.
- The manifest is OFFLINE: the suite never reaches the network, and a test
  needing a token is a test that gets skipped. `scripts/gap_ledgers.py
  --refresh` rewrites it with `gh`. ⚠ So a cited issue CLOSED without a fix
  stays OPEN here until someone refreshes; a FIXED gap removes its own entry
  (each ledger's close guard), and the exact-set test below then names the
  stale manifest row.
- ⚠⚠ Ledgers are classified, never found by a name pattern: the first draft
  scanned for `_*GAPS` and review walked `_KNOWN_GHOSTS` and
  `ALLOWED_UNTIL_FIXED` past it. Every module-level container whose name
  sounds like an exemption must be in `LEDGERS` or `NOT_LEDGERS`
  (`scripts/gap_ledgers.py`); the issue named three ledgers and there are
  fifteen.
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


def test_every_gap_entry_cites_an_open_issue():
    problems = gap_ledgers.check(_ROOT / "tests", _manifest())
    assert problems == [], "\n".join(problems)


def test_every_gap_ledger_in_the_suite_is_found():
    """The partition's floor: the three ledgers #758 named, plus the two
    review planted past the first draft, are gated, never excused."""
    assert {
        ("test_language_spec_maps_agree.py", "_KNOWN_GAPS"),
        ("test_grammar_spelled_forms.py", "_CONFIRMED_GAPS"),
        ("test_declared_forms_extract.py", "_KNOWN_GAPS"),
        ("test_grammar_spelled_forms.py", "_KNOWN_GHOSTS"),
        ("test_workflow_commit_identity.py", "ALLOWED_UNTIL_FIXED"),
    } <= gap_ledgers.LEDGERS


def test_the_manifest_records_exactly_the_cited_issues():
    """A manifest row nobody cites is stale, and a cited issue with no row
    is one the refresh never saw. Either way: `scripts/gap_ledgers.py --refresh`."""
    assert set(_manifest()) == gap_ledgers.cited_issues(_ROOT / "tests")


_PLANTED = {
    ("test_planted.py", "_KNOWN_GAPS"),
    ("test_planted.py", "_KNOWN_GHOSTS"),
    ("test_planted.py", "ALLOWED_UNTIL_FIXED"),
    ("test_planted.py", "_CONFIRMED_GAPS"),
}


def test_the_rule_rejects_planted_excuses(tmp_path):
    """Non-vacuity: every bad entry is reported, and the good ones pass --
    including a `(form, reason)` tuple, whose reason is its SECOND element."""
    (tmp_path / "test_planted.py").write_text(
        "_KNOWN_GAPS = {\n"
        "    'a': 'no issue named at all',\n"
        "    'b': '#999999: an issue nobody opened',\n"
        "    'c': '#100: an issue that is closed',\n"
        "    'd': '#200: open and tracked',\n"
        "}\n"
        "_KNOWN_GHOSTS = {'haskell': ({'type_synon'}, 'free text')}\n"
        "ALLOWED_UNTIL_FIXED = {'wf.yml': {'someone@example.com'}}\n"
        "_CONFIRMED_GAPS = {'x': [('form', 'free text only'), ('good_form', '#200 tracked')]}\n",
        encoding="utf-8",
    )
    problems = gap_ledgers.check(tmp_path, {100: "CLOSED", 200: "OPEN"}, ledgers=_PLANTED, not_ledgers={})
    joined = "\n".join(problems)
    assert len(problems) == 6, joined
    assert "['a'] names no issue" in joined
    assert "#999999 is not in the manifest" in joined
    assert "#100 is CLOSED" in joined
    assert "_KNOWN_GHOSTS['haskell'] names no issue" in joined
    assert "ALLOWED_UNTIL_FIXED['wf.yml'] names no issue" in joined
    assert "'form free text only'" in joined
    assert "['d']" not in joined and "good_form" not in joined


def test_a_container_that_sounds_like_an_exemption_must_be_classified(tmp_path):
    """The partition: an unregistered exemption-shaped name fails, whatever it
    is called, and a registry row naming nothing that exists fails too."""
    (tmp_path / "test_new.py").write_text(
        "_PARKED_UNTIL_FIXED = {'x': 'free text'}\nKNOWN_BROKEN = set()\n_ORDINARY = {'a': 1}\n",
        encoding="utf-8",
    )
    problems = gap_ledgers.check(
        tmp_path, {}, ledgers=frozenset({("test_gone.py", "_GAPS")}), not_ledgers={}
    )
    joined = "\n".join(problems)
    assert "_PARKED_UNTIL_FIXED sounds like an exemption and is unclassified" in joined
    assert "KNOWN_BROKEN sounds like an exemption and is unclassified" in joined
    assert "_ORDINARY" not in joined
    assert "test_gone.py _GAPS is registered" in joined
    assert len(problems) == 3, joined


def test_a_ledger_that_is_not_a_literal_is_reported(tmp_path):
    """Read with `ast.literal_eval`, never by importing a test module: a
    computed ledger, one mutated after its literal, and a file that does not
    parse are each reported, never skipped."""
    (tmp_path / "test_computed.py").write_text("_KNOWN_GAPS = dict(a='x')\n", encoding="utf-8")
    (tmp_path / "test_mutated.py").write_text(
        "_KNOWN_GAPS = {}\n_KNOWN_GAPS['late'] = 'free'\n_KNOWN_GAPS.update(b='free')\n",
        encoding="utf-8",
    )
    (tmp_path / "test_broken.py").write_text("def (:\n", encoding="utf-8")
    ledgers = frozenset({("test_computed.py", "_KNOWN_GAPS"), ("test_mutated.py", "_KNOWN_GAPS")})
    problems = gap_ledgers.check(tmp_path, {}, ledgers=ledgers, not_ledgers={})
    joined = "\n".join(problems)
    assert "test_computed.py:1 _KNOWN_GAPS is not a literal" in joined
    assert "mutated at line 2" in joined and "mutated at line 3" in joined
    assert "test_broken.py does not parse" in joined
    assert len(problems) == 4, joined
