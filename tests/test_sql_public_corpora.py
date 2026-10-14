"""Parse vendored third-party SQL and check the object counts.

Why a third-party corpus at all, when tests/test_sql_batch_and_constraints.py
already covers each construct: a hand-written fixture only ever contains what
its author already knew to write down. Every defect this corpus surfaced was
one nobody had thought to write a fixture for -- guarded DDL (`add_index.sql`,
0 of 10 indexes), anonymous foreign keys (Chinook SQLite), `CREATE OR REPLACE`
(sakila PostgreSQL, 2 triggers of 15), Oracle's `/` batch terminator, the
`(file, name, kind)` id collision (sakila PostgreSQL names THIRTEEN triggers
`last_updated`), a bracket-quoted `[Name]` column costing its whole table, and
`CHECK (x BETWEEN a AND b)` costing AdventureWorks its last three.

⚠ The fixtures are VENDORED, under `fixtures/sql_corpora/`, and these tests
never touch the network. A test that reaches out fails for reasons unrelated to
the change under review and cannot run in an air-gapped checkout at all. See
`fixtures/sql_corpora/SOURCES.md` for upstream projects and licences;
`fixtures/public_sql_corpora.json` records, per file, the upstream repository,
ref, path and the sha256 of the artifact each was taken from.

⚠ Chinook is the load-bearing entry: ONE schema emitted in five dialects, so a
dialect-specific quoting regression shows up as rows that should agree
DISAGREEING, which needs no absolute number to be obviously wrong.
"""

from __future__ import annotations

import collections
import json
from pathlib import Path

import pytest

from jcodemunch_mcp.parser.extractor import _parse_sql_symbols

FIXTURES = Path(__file__).parent / "fixtures"
MANIFEST_PATH = FIXTURES / "public_sql_corpora.json"
CORPORA = FIXTURES / "sql_corpora"


def _manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _cases():
    for source in _manifest()["sources"]:
        for entry in source["files"]:
            yield pytest.param(
                source, entry, id=f"{source['id']}/{entry['path']}"
            )


@pytest.mark.parametrize("source,entry", list(_cases()))
def test_vendored_sql_yields_the_measured_objects(source, entry):
    path = CORPORA / entry["vendored_as"]
    assert path.is_file(), f"vendored fixture missing: {path}"
    counts = collections.Counter(
        s.kind for s in _parse_sql_symbols(path.read_bytes(), entry["path"])
    )
    assert dict(sorted(counts.items())) == entry["expected"]


def test_chinook_dialects_agree_on_the_same_schema():
    """One schema, five dialects, three quoting styles.  This is the assertion
    the corpus exists for.

    ⚠ Asserted on tables AND constraints, not indexes: upstream's Oracle DDL
    genuinely declares no CREATE INDEX, so that one column is legitimately
    different and pinning it would assert a fact about Oracle rather than about
    this parser.
    """
    chinook = next(s for s in _manifest()["sources"] if s["id"] == "chinook")
    tables, constraints = {}, {}
    for entry in chinook["files"]:
        counts = collections.Counter(
            s.kind
            for s in _parse_sql_symbols(
                (CORPORA / entry["vendored_as"]).read_bytes(), entry["path"]
            )
        )
        tables[entry["path"]] = counts["table"]
        constraints[entry["path"]] = counts["constraint"]

    assert len(set(tables.values())) == 1, (
        f"dialects disagree on table count, so one quoting style is being "
        f"mis-parsed: {tables}"
    )
    assert len(set(constraints.values())) == 1, (
        f"dialects disagree on constraint count: {constraints}"
    )


def test_every_vendored_file_is_covered_by_the_manifest():
    """A fixture nobody asserts on is dead weight in someone else's repo, and
    an entry with no file is a test that silently never ran."""
    declared = {
        entry["vendored_as"]
        for source in _manifest()["sources"]
        for entry in source["files"]
    }
    on_disk = {
        p.relative_to(CORPORA).as_posix()
        for p in CORPORA.rglob("*.sql")
    }
    assert declared == on_disk


def test_manifest_expectations_are_not_empty():
    """A pin of `{}` would pass against a parser that found nothing at all."""
    for source in _manifest()["sources"]:
        for entry in source["files"]:
            assert entry["expected"], f"{source['id']}/{entry['path']} pins no objects"


def test_provenance_is_recorded_for_every_vendored_file():
    """Vendored third-party code without provenance is unattributable, and the
    licences here (MIT / BSD-2-Clause) require the attribution to be kept."""
    sources = (CORPORA / "SOURCES.md").read_text(encoding="utf-8")
    for source in _manifest()["sources"]:
        assert source["repo"] in sources, f"{source['repo']} missing from SOURCES.md"
        for entry in source["files"]:
            assert len(entry["upstream_sha256"]) == 64
            assert entry["vendored_as"].startswith(source["id"] + "/")
