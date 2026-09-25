"""Rust extraction fidelity, gated off FROZEN oracle data.

⚠⚠ This runs with no Rust toolchain installed, and that is the whole point. The
oracle (`benchmarks/rust_fidelity/oracle/`) needs `cargo` and the network; CI
has neither reliably. `tests/fixtures/rust_oracle.json` is its output over
`tests/fixtures/rust/`, committed, so the gate travels.

The buckets are asymmetric because the failures are:

  EXTRA       a name we assert that Rust does not bind -> MUST be 0. An LLM
              handed a name that does not exist repeats the error.
  WRONG_SPAN  the definition is not inside the byte range
              `get_symbol_source` would return         -> MUST be 0.
  MISSING     a name a human wrote that we did not find -> REPORTED. Broken out
              by kind so a gap has a name; two kinds are known and deliberate.

⚠ Regenerate the frozen data with `tests/fixtures/rust/REGENERATE.md` after
touching either the fixtures or the oracle. A stale artifact that still passes
is the #553 failure mode in a new costume.
"""

from __future__ import annotations

import collections
import json
import pathlib

import pytest

from jcodemunch_mcp.parser.extractor import parse_file

_FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "rust"
_ORACLE = pathlib.Path(__file__).parent / "fixtures" / "rust_oracle.json"

#: Oracle kinds jCodeMunch deliberately does not emit, each with its reason.
#: Mirrors KNOWN_UNEMITTED in benchmarks/rust_fidelity/run_fidelity.py.
_KNOWN_UNEMITTED = {"module", "macro"}

#: Extraction gaps, by oracle kind. **Empty, and that is the assertion.**
#:
#: It held three entries when the harness was built -- `constant` (a `const` or
#: `static` inside a function body), `method` (a trait method with a signature
#: and no default body) and `union` (no symbol at all). All three are fixed, so
#: the ratchet tightened from "these gaps are known" to "there are none".
#:
#: ⚠ Adding an entry here records a TRACKED gap, and its reason must cite an
#: OPEN issue (#758, `tests/test_gap_ledgers_cite_open_issues.py`). It is NOT
#: the way to make a red test green: a gap that arrives without an issue is a
#: regression, and `_KNOWN_UNEMITTED` is not the place for it either -- that set
#: is for kinds we never index at all, and moving a gap into it converts a bug
#: into a policy.
_KNOWN_GAPS: dict[str, str] = {}



@pytest.fixture(scope="module")
def oracle():
    if not _ORACLE.exists():
        pytest.skip("frozen rust oracle absent")
    return json.loads(_ORACLE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def by_file(oracle):
    grouped: dict[str, list[dict]] = collections.defaultdict(list)
    for d in oracle["defs"]:
        grouped[d["file"]].append(d)
    return grouped


# ⚠⚠ Read off disk, never written out as a literal. The three names that used
# to sit in every `parametrize` below were a SECOND roster beside the frozen
# artifact, and only the artifact had a test keeping it honest -- so a fixture
# added and regenerated correctly still walked past `extra` and `wrong_span`.
# `qualification.rs` was added on 2026-08-27 and was ungated on arrival.
_FIXTURE_NAMES = sorted(p.name for p in _FIXTURES.glob("*.rs"))


def _jcm(rel: str):
    path = _FIXTURES / rel
    content = path.read_text(encoding="utf-8")
    return parse_file(content, str(path), "rust", source_bytes=content.encode("utf-8"))


def _end_line(sym) -> int:
    end = getattr(sym, "end_line", None)
    return max(end or sym.line, sym.line)


def test_frozen_oracle_is_not_empty(oracle):
    """A vacuity guard: an empty artifact would make every gate below pass."""
    assert oracle["files_parsed"] >= 3
    assert oracle["files_failed"] == 0
    assert len(oracle["defs"]) >= 30


def test_fixture_set_matches_the_frozen_oracle(by_file):
    """The artifact must describe the fixtures that are actually on disk.

    ⚠ Catches the stale-artifact case directly: add a fixture, forget to
    regenerate, and the new file is gated by nothing.
    """
    on_disk = {p.name for p in _FIXTURES.glob("*.rs")}
    described = set(by_file)
    assert on_disk == described, (
        f"fixtures on disk {sorted(on_disk)} != frozen oracle {sorted(described)}; "
        "regenerate per tests/fixtures/rust/REGENERATE.md"
    )


@pytest.mark.parametrize("rel", _FIXTURE_NAMES)
def test_no_fabricated_symbols(rel, by_file):
    """`extra` must be 0. This is the gate that matters most."""
    known = {d["name"] for d in by_file[rel]}
    emitted = {s.name for s in _jcm(rel)}
    fabricated = sorted(emitted - known)
    assert not fabricated, (
        f"{rel}: emitted {fabricated}, which Rust does not bind at this site"
    )


@pytest.mark.parametrize("rel", _FIXTURE_NAMES)
def test_no_wrong_spans(rel, by_file):
    """Every definition we DO find must fall inside the span we would return."""
    syms = _jcm(rel)
    by_name: dict[str, list] = collections.defaultdict(list)
    for s in syms:
        by_name[s.name].append(s)
    # ⚠⚠ ANY span covering ANY of that name's oracle lines, matching
    # run_fidelity.py. A name can be defined more than once in a file --
    # `render` is both a trait signature and an impl in basics.rs -- and
    # per-definition checking reports the MISSING trait method as a WRONG span
    # on the impl, which is a different bucket with a different bar.
    oracle_lines: dict[str, list[int]] = collections.defaultdict(list)
    for d in by_file[rel]:
        if d["kind"] not in _KNOWN_UNEMITTED:
            oracle_lines[d["name"]].append(d["line"])
    wrong = []
    for name, lines in oracle_lines.items():
        candidates = by_name.get(name, [])
        if not candidates:
            continue
        if any(s.line <= ln <= _end_line(s) for s in candidates for ln in lines):
            continue
        wrong.append((name, sorted(lines), [[s.line, _end_line(s)] for s in candidates]))
    assert not wrong, f"{rel}: definition outside our span: {wrong}"


@pytest.mark.parametrize("rel", _FIXTURE_NAMES)
def test_no_undercount(rel, by_file):
    """Every definition must be found as many times as Rust binds it.

    ⚠⚠ The three gates above key on bare names in a SET, and a set cannot
    count. Proven 2026-08-27 by deleting the second symbol of every duplicated
    name in this fixture set: `extra` and `missing` did not move. ripgrep's
    `crates/core/flags/defs.rs` binds `is_switch` 108 times, so a run that
    extracted ONE of them scored identically to a perfect one.

    Counted on the QUALIFIED name, because that is the only spelling under
    which `Alpha.new` and `Beta.new` are two things rather than one.
    """
    want = collections.Counter(
        d["qual"] for d in by_file[rel] if d["kind"] not in _KNOWN_UNEMITTED
    )
    got = collections.Counter(s.qualified_name for s in _jcm(rel))
    short = {q: (n, got.get(q, 0)) for q, n in want.items() if got.get(q, 0) < n}
    assert not short, f"{rel}: found fewer than Rust binds (want, got): {short}"


@pytest.mark.parametrize("rel", _FIXTURE_NAMES)
def test_qualification_matches_the_parser(rel, by_file):
    """A definition we find must be filed under the owner Rust gives it.

    ⚠ `impl Display for Foo` puts `fmt` on **Foo**, not on `Display`. Keying
    on the trait is the same collision one level up: every type's `fmt` lands
    in one bucket. This is a wrong answer where `test_no_undercount` is a
    short one, so they are separate gates.
    """
    want: dict[str, set[str]] = collections.defaultdict(set)
    for d in by_file[rel]:
        if d["kind"] not in _KNOWN_UNEMITTED:
            want[d["name"]].add(d["qual"])
    by_name: dict[str, set[str]] = collections.defaultdict(set)
    for s in _jcm(rel):
        by_name[s.name].add(s.qualified_name)
    wrong = {
        name: {"rust": sorted(quals), "jcm": sorted(by_name[name])}
        for name, quals in want.items()
        if by_name.get(name) and by_name[name] != quals
    }
    assert not wrong, f"{rel}: filed under the wrong owner: {wrong}"


def test_nested_cfg_functions_are_found(by_file):
    """The shape that broke the measurement before the harness existed.

    ⚠ Two `imp` functions under opposite `#[cfg]`s inside one body. An oracle
    that does not walk function bodies calls both fabrications; an extractor
    that does not walk them misses both. Pinned in both directions.
    """
    names = [s.name for s in _jcm("guards.rs")]
    assert names.count("imp") == 2, f"expected 2 nested `imp`, got {names.count('imp')}"
    oracle_imp = [d for d in by_file["guards.rs"] if d["name"] == "imp"]
    assert len(oracle_imp) == 2


def test_macro_invocation_defines_nothing():
    """`vec!` / `println!` are calls. Neither they nor their contents are defs."""
    names = {s.name for s in _jcm("guards.rs")}
    for forbidden in ("vec", "println", "inner_thing"):
        assert forbidden not in names, f"{forbidden!r} is not a definition here"


def test_variants_and_closures_are_not_symbols_but_a_field_now_is():
    """Enum variants and `let`-bound closures bind no top-level name. A struct
    field does, as of #786.

    ⚠⚠ **This test used to forbid `depth` and it was the OLD decision's
    witness, not a guard on the new one** (Practice 9). It was written when the
    oracle deliberately omitted fields, and inverting it is the point of #786
    rather than a casualty of it: a struct's members are members, the audit
    tracked their absence as `("rust", "mutable")`, and every other language in
    that family now answers.

    ⚠ The three names beside it are NOT inverted and that is the whole reason
    this test still exists. A variant and a closure remain absent, so the
    change is scoped to the thing that moved.
    """
    names = {s.name for s in _jcm("guards.rs")}
    assert "depth" in names, "a named struct field is a member since #786"
    # ⚠⚠ `shade` is the one that matters and the corpus had no shape for it
    # until #786: `Gamma { shade: u8 }` spells its members with the SAME nodes
    # a struct does, in the grammar and in `syn`, so it is what a channel
    # gated on the node type alone would adopt on EITHER side. Without a
    # variant carrying named fields, both halves of that exclusion were
    # ungated and a change to either would have moved no number. Found in
    # review.
    for forbidden in ("Alpha", "Beta", "Gamma", "shade", "helper"):
        assert forbidden not in names, f"{forbidden!r} must not be a symbol"


def test_ordinary_definitions_are_all_found():
    """The floor. If these regress, something broke that macros do not explain."""
    names = {s.name for s in _jcm("basics.rs")}
    for expected in (
        "MAX_DEPTH", "GREETING", "Config", "Mode", "Result2", "Error",
        "Render", "new", "clamp", "top_level", "render_twice",
        "nested_in_module", "fmt",
    ):
        assert expected in names, f"{expected!r} missing from basics.rs"


def test_known_gaps_are_still_exactly_these(by_file):
    """Reported, not gated -- but pinned, so closing one is visible.

    ⚠ Asserts the SET of gap kinds, not a count. A new kind appearing here is a
    regression that a coverage percentage would have absorbed silently.
    """
    observed: set[str] = set()
    for rel in ("basics.rs", "guards.rs", "sample.rs"):
        emitted = {s.name for s in _jcm(rel)}
        for d in by_file[rel]:
            if d["kind"] in _KNOWN_UNEMITTED:
                continue
            if d["name"] not in emitted:
                observed.add(d["kind"])
    unexpected = observed - set(_KNOWN_GAPS)
    assert not unexpected, (
        f"new extraction gap kind(s): {sorted(unexpected)}. Either fix the "
        f"extractor or add the kind to _KNOWN_GAPS with a reason. "
        f"⚠ This set is currently EMPTY -- the fixtures have no unexplained "
        f"gaps at all, so any entry appearing here is a regression."
    )
