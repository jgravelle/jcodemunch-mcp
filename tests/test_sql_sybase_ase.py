"""Sybase ASE schema objects, against a full comprehensive schema dump.

⚠ **This fixture is VENDORED, not fetched, and that is deliberate.** Every other
corpus in this suite is pinned by sha256 and downloaded on demand, because it is
someone else's upstream artifact. This one is different: no permissively-licensed
Sybase ASE corpus could be found anywhere public, and Sybase is half the dialect
surface this parser was built for. The fixture was supplied for the purpose, so
it lives in the tree and needs no network.

What makes Sybase its own problem, rather than "T-SQL with a different logo":

- Physical-storage clauses (``LOCK DATAPAGES`` / ``DATAROWS`` / ``ALLPAGES``)
  trail the closing paren of a CREATE TABLE, where nothing else does.
- A RULE and a DEFAULT are FREE-STANDING schema objects bound afterwards with
  ``sp_bindrule`` / ``sp_bindefault`` -- there is no inline spelling.
- User-defined datatypes are declared by ``EXEC sp_addtype 'name', ...``. There
  is no CREATE TYPE, so a UDD used as a column type across the whole schema had
  no definition site anything could find.
- ``SET QUOTED_IDENTIFIER`` defaults to OFF, so ``"..."`` is a string.
- Batches are separated by ``GO`` and semicolons are absent entirely.

Measured on this fixture: **3 symbols before this work, 28 after** -- which is
every object the script declares.
"""

from __future__ import annotations

import collections
from pathlib import Path

import pytest

from jcodemunch_mcp.parser.extractor import _parse_sql_symbols

FIXTURE = Path(__file__).parent / "fixtures" / "sybase_ase_schema.sql"


@pytest.fixture(scope="module")
def symbols():
    return _parse_sql_symbols(FIXTURE.read_bytes(), "sybase_ase_schema.sql")


@pytest.fixture(scope="module")
def by_name(symbols):
    return {s.name: s for s in symbols}


def test_every_declared_object_is_recovered(symbols):
    """3 before, 28 after.  The script declares 28 schema objects and this is
    the whole list -- CREATE DATABASE is not one (it is a container, not an
    object), and sp_bindrule/sp_bindefault are bindings, not declarations."""
    counts = collections.Counter(s.kind for s in symbols)
    assert dict(sorted(counts.items())) == {
        "constraint": 14,  # 9 inline + 3 FK + 1 standalone RULE + 1 DEFAULT
        "function": 1,
        "index": 3,
        "procedure": 1,
        "table": 4,
        "trigger": 1,
        "type": 3,        # sp_addtype UDDs
        "view": 1,
    }
    assert sum(counts.values()) == 28


def test_lock_clause_does_not_hide_the_table(by_name):
    """LOCK DATAPAGES / DATAROWS / ALLPAGES trail the closing paren.  All three
    spellings appear in this schema, one per table."""
    for table in ("dbo.Customers", "dbo.Products", "dbo.Orders", "dbo.OrderDetails"):
        assert by_name[table].kind == "table"


def test_user_defined_datatypes_are_found_via_sp_addtype(by_name):
    """There is no CREATE TYPE in Sybase.  `u_currency` is used as a column type
    in three tables and as a parameter type in two routines, so "where is it
    defined" is a question that previously had no answer at all."""
    for udd in ("u_member_code", "u_status_flag", "u_currency"):
        assert by_name[udd].kind == "type", f"{udd} not recovered as a type"


def test_standalone_rule_and_default_are_constraints(by_name):
    """A Sybase RULE is a reusable CHECK and a DEFAULT is a reusable DEFAULT.
    Naming them anything but `constraint` would need a kind whose only two
    members are these."""
    assert by_name["dbo.rl_postal_code"].kind == "constraint"
    assert by_name["dbo.df_zero_currency"].kind == "constraint"


@pytest.mark.parametrize(
    "constraint,table",
    [
        ("chk_customer_status", "dbo.Customers"),
        ("pk_customers", "dbo.Customers"),
        ("uq_product_code", "dbo.Products"),
        ("chk_product_pricing", "dbo.Products"),
        ("pk_products", "dbo.Products"),
        ("chk_order_status", "dbo.Orders"),
        ("pk_orders", "dbo.Orders"),
        ("chk_quantity_positive", "dbo.OrderDetails"),
        ("pk_orderdetails", "dbo.OrderDetails"),
        ("fk_orders_customers", "dbo.Orders"),
        ("fk_details_orders", "dbo.OrderDetails"),
        ("fk_details_products", "dbo.OrderDetails"),
    ],
)
def test_every_constraint_names_its_table(by_name, constraint, table):
    """⚠ The three Products constraints are the interesting ones.  A Sybase
    MATERIALIZED computed column trips the grammar mid-column-list, so the
    table's byte range ENDS EARLY and its own inline constraints fall outside
    it.  They are recovered by falling back to the nearest table opened in the
    SAME BATCH -- same batch means same statement, which is what makes that
    safe rather than a guess."""
    assert by_name[constraint].parent == by_name[table].id


def test_the_products_table_range_really_does_end_early(by_name):
    """Pins the premise of the test above.  If the grammar ever learns
    MATERIALIZED, this fails and the fallback stops being load-bearing --
    which is worth being told about rather than silently carrying dead code."""
    products = by_name["dbo.Products"]
    pk = by_name["pk_products"]
    assert pk.byte_offset > products.byte_offset + products.byte_length, (
        "pk_products now falls INSIDE dbo.Products' range; the same-batch "
        "fallback is no longer what parents it"
    )


def test_scalar_function_with_a_begin_end_body_is_recovered(by_name):
    """The grammar cannot parse a T-SQL/Sybase function body, so this comes
    from the routine source scan."""
    fn = by_name["dbo.fn_CalculateTax"]
    assert fn.kind == "function"
    assert "RETURNS" in fn.signature.upper()


def test_trigger_carries_the_table_it_fires_on(by_name):
    trg = by_name["dbo.trg_OrderDetails_InsertValidation"]
    assert trg.kind == "trigger"
    assert trg.parent == by_name["dbo.OrderDetails"].id


def test_procedure_with_output_params_is_recovered(by_name):
    proc = by_name["dbo.sp_GetCustomerMetrics"]
    assert proc.kind == "procedure"
    assert proc.end_line > proc.line, "procedure body collapsed to one line"


def test_double_quoted_strings_produce_no_phantom_objects(symbols):
    """This schema carries `RAISERROR 99999 "..."` and `PRINT "..."` with NO
    `SET QUOTED_IDENTIFIER` anywhere -- Sybase's default is OFF, so those are
    strings.  Nothing inside them may become a symbol."""
    names = {s.name for s in symbols}
    assert not any("Transaction Aborted" in n for n in names)
    assert not any("Database internal" in n for n in names)


def test_no_semicolon_appears_anywhere_in_the_fixture():
    """The premise of the batch-separator coverage: this schema is GO-separated
    with no statement terminators at all, which is the shape that recovered 1
    object of 3 before GO was handled."""
    body = FIXTURE.read_text(encoding="utf-8")
    statement_terminators = [
        line for line in body.splitlines() if line.rstrip().endswith(";")
    ]
    assert statement_terminators == [], statement_terminators
    assert sum(
        1 for line in body.splitlines() if line.strip().upper() == "GO"
    ) >= 20
