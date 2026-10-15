"""Two Oracle shapes #1080's routine scan missed and #1081's batch parsing fixed (LEDGER L-191, L-192).

Found probing #1080's trial merge on 2026-10-15: a `CREATE FUNCTION ... RETURN
<type> AS` with a declaration section produced no symbol, and the `CREATE TABLE`
after a trigger whose body assigns `:NEW.x` was missing from the index. #1081
parses a script one batch (`/`) at a time and recovers a `CREATE FUNCTION` the
grammar cannot parse, which fixed both before either row was worked. These pin
the shapes, so a change to either mechanism cannot lose them unnoticed.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.parser.extractor import parse_file


def _symbols(source: str, name: str) -> list[tuple[str, str, int, int]]:
    return [(s.kind, s.name, s.line, s.end_line) for s in parse_file(source, name, "sql")]


@pytest.mark.parametrize(
    "header",
    [
        "CREATE OR REPLACE FUNCTION f_case (x NUMBER) RETURN NUMBER AS",
        "CREATE OR REPLACE FUNCTION f_case (x NUMBER) RETURN NUMBER IS",
        "CREATE FUNCTION f_case RETURN VARCHAR2 IS",
    ],
)
def test_an_oracle_function_with_a_return_clause_is_a_symbol(header):
    source = (
        f"{header}\n"
        "  r NUMBER DEFAULT 0;\n"
        "BEGIN\n"
        "  RETURN r;\n"
        "END;\n"
        "/\n"
        "CREATE VIEW v_after AS SELECT 1 AS one FROM dual;\n"
    )
    assert _symbols(source, "f.sql") == [("function", "f_case", 1, 5), ("view", "v_after", 7, 7)]


@pytest.mark.parametrize("declare", ["DECLARE\n  v NUMBER;\n", ""])
def test_the_table_after_a_trigger_assigning_new_is_indexed(declare):
    source = (
        "CREATE OR REPLACE TRIGGER trg_o\n"
        "BEFORE INSERT ON t FOR EACH ROW\n"
        f"{declare}"
        "BEGIN\n"
        "  :NEW.id := 1;\n"
        "END;\n"
        "/\n"
        "CREATE TABLE after_trg (id NUMBER);\n"
    )
    symbols = _symbols(source, "trg.sql")
    body_end = 7 if declare else 5
    assert symbols == [("trigger", "trg_o", 1, body_end), ("table", "after_trg", body_end + 2, body_end + 2)]
