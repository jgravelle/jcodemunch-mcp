"""A result file says which fairness note each pin ran under (CF-62), and the
name of our own row is typed once (CF-51's follow-up).

`docs/competitive/fairness/<tool>.md` is what the reviewer diffs against an
adapter's call plan on that adapter's PR, and until now no result header
named it: an edit to a note left every earlier result file indistinguishable
from one run under the new note. The header carries `fairness_note` (the
path, repo-relative) and `fairness_sha256` (its bytes at run time) per pin,
the way `scorer_sha256` already covers the scorer. A null has no note and
records None; a variant (`jcodemunch_counter`) ran under its parent's note.

The second half: `"jcodemunch"` was typed in four places in run.py and once
each in findings.py and trend.py beside the REGISTRY key that defines it;
`adapter.JCM_NAME` is the one spelling now.
"""
from __future__ import annotations

import hashlib
import importlib
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMPETE = ROOT / "benchmarks" / "competitive"
NOTES = ROOT / "docs" / "competitive" / "fairness"


@pytest.fixture(scope="module")
def mods():
    sys.path.insert(0, str(COMPETE))
    try:
        for m in ("adapter", "run", "findings", "trend"):
            sys.modules.pop(m, None)
        return {m: importlib.import_module(m) for m in ("adapter", "run", "findings", "trend")}
    finally:
        sys.path.remove(str(COMPETE))


def test_the_digest_is_the_notes_bytes_and_absent_when_there_is_no_note(mods, tmp_path):
    run = mods["run"]
    (tmp_path / "cymbal.md").write_bytes(b"# cymbal\nargued\n")
    note, digest = run.fairness_note("cymbal", notes_dir=tmp_path)
    assert digest == hashlib.sha256(b"# cymbal\nargued\n").hexdigest()
    assert note.endswith("cymbal.md")
    assert run.fairness_note("null_grep", notes_dir=tmp_path) == (None, None)


def test_a_variant_runs_under_its_parents_note(mods, tmp_path):
    run = mods["run"]
    (tmp_path / "jcodemunch.md").write_bytes(b"ours\n")
    parent = run.fairness_note("jcodemunch", notes_dir=tmp_path)
    assert run.fairness_note("jcodemunch_counter", variant_of="jcodemunch", notes_dir=tmp_path) == parent
    assert parent[1] == hashlib.sha256(b"ours\n").hexdigest()


def test_every_competitor_in_the_registry_has_a_note_on_disk_and_the_nulls_do_not(mods):
    run, adapter = mods["run"], mods["adapter"]
    for name in adapter.REGISTRY:
        note, digest = run.fairness_note(name, variant_of="jcodemunch" if name == "jcodemunch_counter" else None)
        if name.startswith("null_"):
            assert (note, digest) == (None, None), name
        else:
            assert note == f"docs/competitive/fairness/{name.removesuffix('_counter')}.md", name
            assert re.fullmatch(r"[0-9a-f]{64}", digest), name
            assert (ROOT / note).read_bytes() == (NOTES / Path(note).name).read_bytes()


def test_the_pin_record_carries_the_note_beside_the_pin(mods):
    run, adapter = mods["run"], mods["adapter"]

    class Fake:
        name = "cymbal"
        pin = adapter.Pin(registry="github-release", package="cymbal", version="0.14.0")
        interface = "cli"

        def version(self):
            return "0.14.0"

    rec = run.pin_record(Fake())
    assert rec["name"] == "cymbal" and rec["version"] == "0.14.0" and rec["ran_as"] == "0.14.0"
    assert rec["fairness_note"] == "docs/competitive/fairness/cymbal.md"
    assert rec["fairness_sha256"] == hashlib.sha256((NOTES / "cymbal.md").read_bytes()).hexdigest()
    assert rec["variant_of"] is None and rec["image_digest"] is None


def test_the_summary_names_each_pins_note_digest(mods):
    run = mods["run"]
    header = {"date": "d", "jcm_commit": "c", "jcm_version": "v", "runs": 3, "corpora": [], "sandbox": "none",
              "tree_dirty": False, "scorer_sha256": "f" * 64,
              "pins": [{"name": "cymbal", "registry": "github-release", "package": "cymbal", "version": "0.14.0",
                        "ran_as": "0.14.0", "image_digest": None, "fairness_note": "docs/competitive/fairness/cymbal.md",
                        "fairness_sha256": "abcdef0123456789" + "0" * 48},
                       {"name": "null_grep", "registry": "none", "package": "grep", "version": "0", "ran_as": "0",
                        "image_digest": None, "fairness_note": None, "fairness_sha256": None}]}
    md = run.render_md({"header": header, "rows": [], "runs": [], "capability_only": [], "tools_not_called": [], "not_runnable": []})
    assert "fairness `abcdef012345`" in md
    assert "null_grep" in md and md.count("fairness `") == 1


def test_our_rows_name_is_typed_once(mods):
    adapter, run, findings, trend = (mods[m] for m in ("adapter", "run", "findings", "trend"))
    assert adapter.JCM_NAME == "jcodemunch" and adapter.JCM_NAME in adapter.REGISTRY
    assert findings.JCM == adapter.JCM_NAME and trend.JCM == adapter.JCM_NAME
    assert run.DEFAULT_ADAPTERS[-1] == adapter.JCM_NAME
    # An `is` check would be vacuous: CPython interns identifier-shaped literals
    # across modules, so a retyped `"jcodemunch"` IS the constant (review round 1).
    # The scan is the ratchet: the quoted literal, as a token, in any code line of
    # the three readers. On main it sat at run.py:57 (a tuple element), :223 (a
    # default), :271 (a comparison), :304 (a comparison), findings.py:50, trend.py:34.
    for name in ("run.py", "findings.py", "trend.py"):
        src = (COMPETE / name).read_text(encoding="utf-8")
        code = "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("#"))
        hits = [ln for ln in code.splitlines() if '"jcodemunch"' in ln or "'jcodemunch'" in ln]
        assert not hits, (name, hits)
