"""A non-ASCII name stays `name_not_searchable` until the search normalises (LEDGER L-83).

`_name_reachability.name_can_appear_at_a_call_site` accepts only ASCII
identifiers, so `check_delete_safe` refuses every non-ASCII name as
`name_not_searchable`. L-83 filed that as a defect: `def café()` is written
`café()` at a call site. The first fix (`str.isidentifier()`) was reviewed
twice and each round found a used function it certified `safe_to_delete` at
confidence 1.0:

- a definition not in NFKC form (`def ﬁle()`, a decomposed `café`,
  a mathematical-bold letter), which Python calls under its NFKC spelling;
- the mirror: an NFKC definition (`def café()`) called under a spelling that is
  not (decomposed, fullwidth), which runs because Python normalises both sides.

The reference search compares raw spellings, so for a non-ASCII name no test of
the DEFINITION's spelling can say whether a call site writes the same bytes. The
refusal is the correct answer until the search compares normalised text; that
is LEDGER L-84, which also covers the ASCII case the predicate cannot see
(`def file()` called as `ﬁle()`).

These tests pin the refusal for every shape both reviews measured, so a
predicate-only fix fails here the way the first one did.
"""
from __future__ import annotations

import subprocess
import sys

import pytest

from jcodemunch_mcp.tools._name_reachability import name_can_appear_at_a_call_site as reachable
from jcodemunch_mcp.tools.check_delete_safe import check_delete_safe
from jcodemunch_mcp.tools.index_folder import index_folder

_ABSENCE = ("safe_to_delete", "internal_only", "test_coverage_only")

# (declared spelling, call spelling). Each pair names ONE Python function.
_SPELLING_PAIRS = [
    pytest.param("ﬁle", "file", id="ligature-definition"),
    pytest.param("café", "café", id="decomposed-definition"),
    pytest.param("\U0001d41foo", "foo", id="math-bold-definition"),
    pytest.param("café", "café", id="decomposed-call"),
    pytest.param("café", "ｃafé", id="fullwidth-call"),
    pytest.param("Ωmega", "Ωmeg\U0001d41a", id="math-bold-call"),
]


@pytest.mark.parametrize("declared,called", _SPELLING_PAIRS)
def test_a_used_non_ascii_name_is_never_certified_deletable(tmp_path, declared, called):
    src = f"def {declared}():\n    return 1\n\ndef go():\n    return {called}()\n\nprint(go())\n"
    (tmp_path / "m.py").write_text(src, encoding="utf-8")
    ran = subprocess.run([sys.executable, str(tmp_path / "m.py")], capture_output=True, text=True, encoding="utf-8")
    assert ran.stdout.strip() == "1", "the pair must name one function, or the fixture proves nothing"

    storage = str(tmp_path / "idx")
    repo = index_folder(path=str(tmp_path), use_ai_summaries=False, storage_path=storage)["repo"]
    got = check_delete_safe(repo, declared, storage_path=storage)
    assert got["verdict"] not in _ABSENCE, (declared, called, got["verdict"], got["confidence"])


@pytest.mark.parametrize("name", ["café", "Ωmega", "変数", "ﬁle", "Mod.café"])
def test_the_predicate_refuses_a_non_ascii_name(name):
    assert reachable(name, "python") is False


@pytest.mark.parametrize("name", ["plain", "Mod.plain", "_x2", "file"])
def test_control_an_ascii_identifier_is_reachable(name):
    assert reachable(name, "python") is True
