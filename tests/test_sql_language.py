"""Tests for SQL language support in jcodemunch-mcp."""
import pytest
from jcodemunch_mcp.parser import parse_file


SQL_DDL = """\
-- Orders table stores all customer orders
CREATE TABLE orders (
    id          INT PRIMARY KEY,
    customer_id INT NOT NULL,
    status      VARCHAR(20),
    total       DECIMAL(10,2)
);

-- Active orders view
CREATE VIEW active_orders AS
    SELECT * FROM orders WHERE status = 'active';

-- Get order total function
CREATE FUNCTION get_order_total(p_id INT)
RETURNS DECIMAL
BEGIN
    DECLARE v_total DECIMAL;
    SELECT total INTO v_total FROM orders WHERE id = p_id;
    RETURN v_total;
END;

CREATE INDEX idx_orders_status ON orders(status);

CREATE SCHEMA analytics;
"""

SQL_WITH_CTE = """\
WITH ranked AS (
    SELECT *, ROW_NUMBER() OVER (ORDER BY id) AS rn FROM orders
)
SELECT * FROM ranked;
"""

SQL_DBT_JINJA = """\
-- dbt model: staging orders
SELECT
    id,
    {{ dbt_utils.star(from=ref('raw_orders')) }},
    status
FROM {{ ref('raw_orders') }}
WHERE {% if is_incremental() %}
    updated_at > (SELECT MAX(updated_at) FROM {{ this }})
{% endif %}
    status != 'deleted'
"""


def test_sql_parser_available():
    """Smoke test: grammar must load without raising."""
    from tree_sitter_language_pack import get_parser
    parser = get_parser("sql")
    assert parser is not None


def test_sql_symbols_extracted():
    symbols = parse_file(SQL_DDL, "test.sql", "sql")
    assert len(symbols) >= 4


def test_sql_table_kind():
    symbols = parse_file(SQL_DDL, "test.sql", "sql")
    table = next((s for s in symbols if s.name == "orders"), None)
    assert table is not None
    assert table.kind == "table"


def test_sql_view_extracted():
    symbols = parse_file(SQL_DDL, "test.sql", "sql")
    view = next((s for s in symbols if s.name == "active_orders"), None)
    assert view is not None
    assert view.kind == "view"


def test_sql_function_extracted():
    symbols = parse_file(SQL_DDL, "test.sql", "sql")
    func = next((s for s in symbols if s.name == "get_order_total"), None)
    assert func is not None
    assert func.kind == "function"


def test_sql_index_extracted():
    symbols = parse_file(SQL_DDL, "test.sql", "sql")
    idx = next((s for s in symbols if s.name == "idx_orders_status"), None)
    assert idx is not None
    assert idx.kind == "index"


def test_sql_schema_extracted():
    symbols = parse_file(SQL_DDL, "test.sql", "sql")
    schema = next((s for s in symbols if s.name == "analytics"), None)
    assert schema is not None
    assert schema.kind == "type"


def test_sql_cte_extracted():
    symbols = parse_file(SQL_WITH_CTE, "cte.sql", "sql")
    cte = next((s for s in symbols if s.name == "ranked"), None)
    assert cte is not None
    assert cte.kind == "function"


def test_sql_function_signature():
    symbols = parse_file(SQL_DDL, "test.sql", "sql")
    func = next((s for s in symbols if s.name == "get_order_total"), None)
    assert func is not None
    assert "CREATE FUNCTION" in func.signature
    assert "RETURNS" in func.signature


def test_sql_table_signature():
    symbols = parse_file(SQL_DDL, "test.sql", "sql")
    table = next((s for s in symbols if s.name == "orders"), None)
    assert table is not None
    assert "CREATE TABLE orders" in table.signature


def test_sql_table_docstring():
    symbols = parse_file(SQL_DDL, "test.sql", "sql")
    table = next((s for s in symbols if s.name == "orders"), None)
    assert table is not None
    assert "Orders table" in table.docstring


def test_sql_partial_parse_tolerant():
    """tree-sitter should degrade gracefully on Jinja-templated SQL (dbt models)."""
    symbols = parse_file(SQL_DBT_JINJA, "staging_orders.sql", "sql")
    # Must not raise; may return 0 symbols (Jinja is stripped, SELECT remains)
    assert isinstance(symbols, list)


def test_sql_jinja_preprocessor():
    """Jinja expressions should be replaced before parsing."""
    from jcodemunch_mcp.parser.sql_preprocessor import strip_jinja, is_jinja_sql

    jinja_sql = b"SELECT * FROM {{ ref('orders') }} WHERE {% if true %}1=1{% endif %}"
    assert is_jinja_sql(jinja_sql)
    cleaned = strip_jinja(jinja_sql)
    assert b"{{" not in cleaned
    assert b"{%" not in cleaned
    assert b"__jinja__" in cleaned


def test_sql_jinja_dbt_model_parses():
    """A dbt model with Jinja should parse without errors after preprocessing."""
    dbt_model = """\
-- dbt model with ref and config
{{ config(materialized='table') }}

CREATE TABLE {{ ref('stg_orders') }} AS
SELECT
    id,
    {{ dbt_utils.star(from=ref('raw_orders')) }}
FROM {{ ref('raw_orders') }}
"""
    symbols = parse_file(dbt_model, "models/orders.sql", "sql")
    assert isinstance(symbols, list)
    # After Jinja stripping, CREATE TABLE __jinja__ should still extract
    # The name will be __jinja__ which is fine — the file is still indexed


def test_sql_extension_registered():
    """Ensure .sql is mapped in LANGUAGE_EXTENSIONS."""
    from jcodemunch_mcp.parser.languages import LANGUAGE_EXTENSIONS
    assert ".sql" in LANGUAGE_EXTENSIONS
    assert LANGUAGE_EXTENSIONS[".sql"] == "sql"


def test_sql_language_in_registry():
    """Ensure sql is in LANGUAGE_REGISTRY."""
    from jcodemunch_mcp.parser.languages import LANGUAGE_REGISTRY
    assert "sql" in LANGUAGE_REGISTRY


# ── dbt directive extraction tests ──────────────────────────────────────

DBT_MACRO = """\
{#
/**
 * Deduplicate rows in a final model.
 * Keeps the first row per partition.
 */
#}
{% macro macro_dedupe_final_model(source_cte, partition_by, order_by) %}
    select
        * exclude rnk
    from (
        select
            *,
            row_number() over (
                partition by {{partition_by}} order by {{order_by}}
            ) as rnk
        from {{source_cte}}
        qualify rnk = 1
    )
{% endmacro %}
"""

DBT_TEST = """\
{% test datashare_safe_row_count_test(model, source_name, source_table_name) %}

{%- set source_relation = source(source_name, source_table_name) -%}

select count(*) from {{ model }}

{% endtest %}
"""

DBT_SNAPSHOT = """\
{% snapshot snap_zoominfo_contacts %}
{{
    config(unique_key = "CONTACT_ZOOMINFO_ID",
        strategy = "check",
        check_cols = "all")
}}
select * from {{ source('zoominfo', 'contacts') }}
{% endsnapshot %}
"""

DBT_MATERIALIZATION = """\
{% materialization cortex_agent, adapter='snowflake' %}
    {%- set target_relation = this -%}
    {{ return({'relations': [target_relation]}) }}
{% endmaterialization %}
"""

DBT_MULTI_MACRO = """\
-- Helper macro
{% macro helper_one(x) %}
    select {{x}}
{% endmacro %}

{# Second helper #}
{% macro helper_two(a, b) %}
    select {{a}}, {{b}}
{% endmacro %}
"""


def test_dbt_macro_extracted():
    """dbt {% macro %} should be extracted as a function symbol."""
    symbols = parse_file(DBT_MACRO, "macros/dedupe.sql", "sql")
    macro = next((s for s in symbols if s.name == "macro_dedupe_final_model"), None)
    assert macro is not None
    assert macro.kind == "function"


def test_dbt_macro_signature():
    symbols = parse_file(DBT_MACRO, "macros/dedupe.sql", "sql")
    macro = next((s for s in symbols if s.name == "macro_dedupe_final_model"), None)
    assert macro is not None
    assert "macro_dedupe_final_model" in macro.signature
    assert "source_cte" in macro.signature


def test_dbt_macro_docstring():
    symbols = parse_file(DBT_MACRO, "macros/dedupe.sql", "sql")
    macro = next((s for s in symbols if s.name == "macro_dedupe_final_model"), None)
    assert macro is not None
    assert "Deduplicate" in macro.docstring


def test_dbt_macro_line_range():
    symbols = parse_file(DBT_MACRO, "macros/dedupe.sql", "sql")
    macro = next((s for s in symbols if s.name == "macro_dedupe_final_model"), None)
    assert macro is not None
    assert macro.line == 7   # {% macro ... %} (line 7 in the test string)
    assert macro.end_line > macro.line  # spans multiple lines


def test_dbt_test_extracted():
    """dbt {% test %} should be extracted as a function symbol."""
    symbols = parse_file(DBT_TEST, "tests/row_count.sql", "sql")
    test = next((s for s in symbols if s.name == "datashare_safe_row_count_test"), None)
    assert test is not None
    assert test.kind == "function"


def test_dbt_snapshot_extracted():
    """dbt {% snapshot %} should be extracted as a type symbol."""
    symbols = parse_file(DBT_SNAPSHOT, "snapshots/contacts.sql", "sql")
    snap = next((s for s in symbols if s.name == "snap_zoominfo_contacts"), None)
    assert snap is not None
    assert snap.kind == "type"


def test_dbt_materialization_extracted():
    """dbt {% materialization %} should be extracted as a function symbol."""
    symbols = parse_file(DBT_MATERIALIZATION, "macros/cortex_agent.sql", "sql")
    mat = next((s for s in symbols if s.name == "cortex_agent"), None)
    assert mat is not None
    assert mat.kind == "function"


def test_dbt_multiple_macros():
    """Multiple macros in one file should all be extracted."""
    symbols = parse_file(DBT_MULTI_MACRO, "macros/helpers.sql", "sql")
    names = [s.name for s in symbols]
    assert "helper_one" in names
    assert "helper_two" in names


def test_dbt_macro_with_whitespace_variants():
    """Jinja blocks with whitespace trimming ({%- -%}) should still be extracted."""
    src = "{%- macro trimmed_macro(x, y) -%}\n  select {{x}}\n{%- endmacro -%}\n"
    symbols = parse_file(src, "macros/trim.sql", "sql")
    macro = next((s for s in symbols if s.name == "trimmed_macro"), None)
    assert macro is not None
    assert macro.kind == "function"


def test_dbt_snapshot_no_params():
    """Snapshots have no params — should still extract cleanly."""
    symbols = parse_file(DBT_SNAPSHOT, "snapshots/contacts.sql", "sql")
    snap = next((s for s in symbols if s.name == "snap_zoominfo_contacts"), None)
    assert snap is not None
    assert "snapshot" in snap.signature


def test_plain_sql_unaffected():
    """Plain SQL (no Jinja) should work exactly as before."""
    symbols = parse_file(SQL_DDL, "test.sql", "sql")
    assert len(symbols) >= 4
    names = [s.name for s in symbols]
    assert "orders" in names
    assert "active_orders" in names


TSQL_PROCEDURE = """\
-- =============================================
-- Author:\texample
-- Description:\tRecalculate Totals
-- =============================================
CREATE PROCEDURE [dbo].[usp_RecalcTotals]
\t@CustomerId int
AS
BEGIN
\tSET NOCOUNT ON;
\tSELECT 1;
END
"""

SYBASE_PROCEDURE = """\
CREATE PROCEDURE dbo.usp_SetOrderTotal(
  @OrderRef numeric (19,0),
  @Amount money
)
as
begin
  update orders set total = @Amount where order_ref = @OrderRef
end
return
go
sp_procxmode 'dbo.usp_SetOrderTotal', 'Unchained'
go
"""

TSQL_TRIGGER = """\
CREATE TRIGGER [dbo].[trg_OrdersInsert]
    on dbo.Orders
       instead of insert
as
begin
insert into Orders(OrderRef) select OrderRef from inserted
end;
"""

TSQL_BRACKET_VIEW = "CREATE VIEW [dbo].[vw_ActiveCustomers] AS SELECT 1 AS x\n"


def test_tsql_procedure_extracted():
    symbols = parse_file(TSQL_PROCEDURE, "usp_RecalcTotals.sql", "sql")
    proc = next((s for s in symbols if s.name == "dbo.usp_RecalcTotals"), None)
    assert proc is not None
    assert proc.kind == "procedure"
    assert proc.line == 5
    assert proc.signature.startswith("CREATE PROCEDURE")
    assert "@CustomerId int" in proc.signature


def test_tsql_procedure_docstring_from_banner():
    symbols = parse_file(TSQL_PROCEDURE, "proc.sql", "sql")
    proc = symbols[0]
    assert "Author:" in proc.docstring
    assert "=====" not in proc.docstring


def test_sybase_procedure_unquoted():
    symbols = parse_file(SYBASE_PROCEDURE, "dbo.usp_SetOrderTotal.sql", "sql")
    proc = next((s for s in symbols if s.name == "dbo.usp_SetOrderTotal"), None)
    assert proc is not None
    assert proc.kind == "procedure"
    assert "@OrderRef numeric (19,0)" in proc.signature


def test_procedure_body_stops_at_batch_separator():
    symbols = parse_file(SYBASE_PROCEDURE, "dbo.usp_SetOrderTotal.sql", "sql")
    proc = next(s for s in symbols if s.name == "dbo.usp_SetOrderTotal")
    body = SYBASE_PROCEDURE.encode()[proc.byte_offset:proc.byte_offset + proc.byte_length]
    assert b"sp_procxmode" not in body
    assert b"end" in body


def test_abbreviated_proc_keyword():
    src = "CREATE PROC dbo.Get_Next_Value @seed int AS BEGIN SELECT 1 END\n"
    symbols = parse_file(src, "Get_Next_Value.sql", "sql")
    proc = next((s for s in symbols if s.name == "dbo.Get_Next_Value"), None)
    assert proc is not None
    assert proc.kind == "procedure"


def test_create_or_alter_procedure():
    src = "CREATE OR ALTER PROCEDURE dbo.spThing AS BEGIN SELECT 1 END\n"
    symbols = parse_file(src, "spThing.sql", "sql")
    assert any(s.name == "dbo.spThing" for s in symbols)


def test_unqualified_procedure_name():
    src = "create procedure RecalculateDiscount\nas\nselect 1\n"
    symbols = parse_file(src, "RecalculateDiscount.sql", "sql")
    assert any(s.name == "RecalculateDiscount" for s in symbols)


def test_tsql_trigger_extracted_with_parent_table():
    symbols = parse_file(TSQL_TRIGGER, "trg_OrdersInsert.sql", "sql")
    trig = next((s for s in symbols if s.name == "dbo.trg_OrdersInsert"), None)
    assert trig is not None
    assert trig.kind == "trigger"
    assert trig.parent is None
    assert trig.signature.startswith("CREATE TRIGGER")
    assert "dbo.Orders" in trig.signature


def test_trigger_on_same_line_bracketed_table():
    src = "CREATE TRIGGER tiOrderLines ON [dbo].[OrderLines] FOR INSERT AS SELECT 1\n"
    symbols = parse_file(src, "tiOrderLines.sql", "sql")
    trig = next(s for s in symbols if s.name == "tiOrderLines")
    assert trig.parent is None
    assert "[dbo].[OrderLines]" in trig.signature


def test_trigger_on_a_table_defined_in_the_same_file_is_parented_to_its_id():
    src = (
        "CREATE TABLE dbo.Orders (OrderRef INT);\n"
        "GO\n"
        "CREATE TRIGGER trg_Y ON [dbo].[Orders] FOR INSERT AS SELECT 1\n"
        "GO\n"
    )
    symbols = parse_file(src, "same.sql", "sql")
    table = next(s for s in symbols if s.kind == "table")
    trig = next(s for s in symbols if s.kind == "trigger")
    assert trig.parent == table.id
    assert table.id in {s.id for s in symbols}


def test_trigger_on_a_table_defined_elsewhere_has_no_parent_but_names_the_table():
    src = "CREATE TRIGGER trg_Y ON dbo.Orders FOR INSERT AS SELECT 1\n"
    trig = next(s for s in parse_file(src, "elsewhere.sql", "sql") if s.kind == "trigger")
    assert trig.parent is None
    assert "ON dbo.Orders" in trig.signature


def test_trigger_does_not_adopt_a_temp_table_of_the_same_name():
    src = (
        "CREATE PROCEDURE dbo.usp_X AS\n"
        "CREATE TABLE #Orders (a INT)\n"
        "GO\n"
        "CREATE TRIGGER trg_Y ON Orders FOR INSERT AS SELECT 1\n"
    )
    trig = next(s for s in parse_file(src, "temp.sql", "sql") if s.kind == "trigger")
    assert trig.parent is None


def test_nested_symbols_and_triggers_nest_in_the_file_outline():
    from jcodemunch_mcp.parser.hierarchy import build_symbol_tree
    src = (
        "CREATE TABLE dbo.Orders (OrderRef INT);\n"
        "GO\n"
        "CREATE TRIGGER trg_Y ON dbo.Orders FOR INSERT AS SELECT 1\n"
        "GO\n"
        "CREATE PROCEDURE dbo.usp_X AS\n"
        "WITH cte_Recent AS (SELECT 1 AS a)\n"
        "SELECT * FROM cte_Recent\n"
        "GO\n"
    )
    symbols = parse_file(src, "outline.sql", "sql")
    roots = build_symbol_tree(symbols)
    by_name = {r.symbol.name: r for r in roots}
    assert set(by_name) == {"dbo.Orders", "dbo.usp_X"}
    assert [c.symbol.name for c in by_name["dbo.Orders"].children] == ["trg_Y"]
    assert [c.symbol.name for c in by_name["dbo.usp_X"].children] == ["cte_Recent"]


def test_a_routine_name_repeated_in_one_file_is_kept_and_numbered():
    src = (
        "CREATE PROCEDURE dbo.usp_Dup AS SELECT 1\n"
        "GO\n"
        "CREATE PROCEDURE dbo.usp_Dup AS SELECT 2\n"
        "GO\n"
    )
    procs = [s for s in parse_file(src, "dup.sql", "sql") if s.kind == "procedure"]
    assert len(procs) == 2
    assert len({s.id for s in procs}) == 2
    assert [s.line for s in procs] == [1, 3]


def test_nested_symbols_follow_a_renumbered_routine():
    src = (
        "CREATE PROCEDURE dbo.usp_Dup AS SELECT 1\n"
        "GO\n"
        "CREATE PROCEDURE dbo.usp_Dup AS\n"
        "WITH cte_Recent AS (SELECT 1 AS a)\n"
        "SELECT * FROM cte_Recent\n"
        "GO\n"
    )
    symbols = parse_file(src, "dup2.sql", "sql")
    procs = sorted((s for s in symbols if s.kind == "procedure"), key=lambda s: s.line)
    cte = next(s for s in symbols if s.name == "cte_Recent")
    assert [p.line for p in procs] == [1, 3]
    assert cte.parent == procs[1].id
    assert cte.parent != procs[0].id


def test_commented_out_routine_is_not_a_symbol():
    src = (
        "-- CREATE PROCEDURE dbo.NotReal AS SELECT 1\n"
        "/* CREATE TRIGGER dbo.AlsoNotReal ON dbo.T AS SELECT 1 */\n"
        "EXEC('CREATE PROCEDURE dbo.StringNotReal AS SELECT 1')\n"
        "CREATE PROCEDURE dbo.Real AS SELECT 1\n"
    )
    names = [s.name for s in parse_file(src, "mixed.sql", "sql")]
    assert "dbo.Real" in names
    assert "dbo.NotReal" not in names
    assert "dbo.AlsoNotReal" not in names
    assert "dbo.StringNotReal" not in names


def test_multiple_routines_in_one_file():
    src = (
        "CREATE PROCEDURE dbo.First AS SELECT 1\n"
        "GO\n"
        "CREATE PROCEDURE dbo.Second AS SELECT 2\n"
        "GO\n"
    )
    symbols = parse_file(src, "batch.sql", "sql")
    names = [s.name for s in symbols]
    assert "dbo.First" in names
    assert "dbo.Second" in names
    first = next(s for s in symbols if s.name == "dbo.First")
    assert b"Second" not in src.encode()[first.byte_offset:first.byte_offset + first.byte_length]


def test_bracket_quoted_view_name_unwrapped():
    symbols = parse_file(TSQL_BRACKET_VIEW, "vw_ActiveCustomers.sql", "sql")
    view = next((s for s in symbols if s.name == "dbo.vw_ActiveCustomers"), None)
    assert view is not None
    assert "].[" not in view.name
    assert "].[" not in view.signature


def test_bracket_quoted_table_and_index_names_unwrapped():
    src = (
        "CREATE TABLE [dbo].[Widget_Master] (WidgetID INT);\n"
        "CREATE INDEX [CIX_Shipments] ON [dbo].[Shipments] (OrderRef);\n"
    )
    symbols = parse_file(src, "brackets.sql", "sql")
    names = [s.name for s in symbols]
    assert "dbo.Widget_Master" in names
    assert "CIX_Shipments" in names
    assert not any("[" in n or "]" in n for n in names)
    idx = next(s for s in symbols if s.name == "CIX_Shipments")
    assert idx.signature == "CREATE INDEX CIX_Shipments ON dbo.Shipments"


def test_bracket_and_unquoted_names_agree():
    bracketed = parse_file(
        "CREATE VIEW [dbo].[Thing] AS SELECT 1 AS x\n", "a.sql", "sql")
    plain = parse_file(
        "CREATE VIEW dbo.Thing AS SELECT 1 AS x\n", "b.sql", "sql")
    assert bracketed[0].name == plain[0].name == "dbo.Thing"


def test_double_and_single_quoted_identifiers_unwrapped():
    from jcodemunch_mcp.parser.extractor import _clean_sql_name
    assert _clean_sql_name('"dbo"."Name"') == "dbo.Name"
    assert _clean_sql_name("'dbo'.'Name'") == "dbo.Name"
    assert _clean_sql_name("`dbo`.`Name`") == "dbo.Name"
    assert _clean_sql_name("dbo].[Name") == "dbo.Name"
    assert _clean_sql_name("[dbo].[Name]") == "dbo.Name"
    assert _clean_sql_name("plain_name") == "plain_name"


def test_clean_sql_name_keeps_dots_inside_quotes():
    from jcodemunch_mcp.parser.extractor import _clean_sql_name
    assert _clean_sql_name("[my.table]") == "my.table"
    assert _clean_sql_name("[dbo].[my.table]") == "dbo.my.table"


def test_single_quoted_routine_name_survives_string_masking():
    src = "CREATE PROCEDURE 'dbo'.'spOldSybase' as select 1\n"
    names = [s.name for s in parse_file(src, "old.sql", "sql")]
    assert "dbo.spOldSybase" in names


def test_routine_byte_offsets_are_byte_domain():
    src = "-- héllo wörld ünicode\nCREATE PROCEDURE dbo.spX AS SELECT 1\n"
    proc = next(s for s in parse_file(src, "u.sql", "sql") if s.name == "dbo.spX")
    raw = src.encode("utf-8")
    assert raw[proc.byte_offset:proc.byte_offset + len("CREATE")] == b"CREATE"
    assert proc.line == 2


TSQL_PROC_WITH_TEMP = """\
CREATE PROCEDURE [dbo].[usp_ResetCounters]
@ItemId int
AS
CREATE TABLE #Temp (amount int)
DECLARE @Log table (ID int)
SELECT 1
GO
"""

TSQL_PROC_WITH_CTE = """\
CREATE PROCEDURE dbo.usp_RecentReport
AS
WITH cte_Recent AS (
    SELECT OrderRef FROM Orders
)
SELECT * FROM cte_Recent
GO
"""


def test_sql_object_kinds_are_specific():
    src = (
        "CREATE TABLE dbo.Orders (Status BIGINT);\n"
        "CREATE VIEW dbo.vw_ActiveCustomers AS SELECT 1 AS x;\n"
        "CREATE INDEX IX_Orders_Status ON dbo.Orders (Status);\n"
    )
    by_name = {s.name: s.kind for s in parse_file(src, "objects.sql", "sql")}
    assert by_name["dbo.Orders"] == "table"
    assert by_name["dbo.vw_ActiveCustomers"] == "view"
    assert by_name["IX_Orders_Status"] == "index"


def test_procedure_and_trigger_kinds_are_specific():
    src = (
        "CREATE PROCEDURE dbo.spThing AS SELECT 1\n"
        "GO\n"
        "CREATE TRIGGER dbo.tiThing ON dbo.Orders FOR INSERT AS SELECT 1\n"
        "GO\n"
    )
    by_name = {s.name: s.kind for s in parse_file(src, "routines.sql", "sql")}
    assert by_name["dbo.spThing"] == "procedure"
    assert by_name["dbo.tiThing"] == "trigger"


def test_sql_function_kind_unchanged():
    symbols = parse_file(
        "CREATE FUNCTION dbo.ParseCount(@s VARCHAR(20)) RETURNS INT AS BEGIN RETURN 1 END\n",
        "ParseCount.sql", "sql",
    )
    assert symbols[0].kind == "function"


def test_every_emitted_sql_kind_is_a_valid_kind():
    from jcodemunch_mcp.parser.symbols import VALID_KINDS
    src = (
        "CREATE TABLE dbo.T (a INT);\n"
        "CREATE VIEW dbo.V AS SELECT 1 AS x;\n"
        "CREATE INDEX IX ON dbo.T (a);\n"
        "CREATE SCHEMA analytics;\n"
        "CREATE PROCEDURE dbo.P AS SELECT 1\n"
        "GO\n"
        "CREATE TRIGGER dbo.G ON dbo.T FOR INSERT AS SELECT 1\n"
        "GO\n"
    )
    for sym in parse_file(src, "all.sql", "sql"):
        assert sym.kind in VALID_KINDS, f"{sym.name} has unknown kind {sym.kind!r}"


def test_search_symbols_kind_enum_is_derived_from_valid_kinds():
    from jcodemunch_mcp.parser.symbols import VALID_KINDS
    from jcodemunch_mcp.server import _build_tools_list

    tool = next(t for t in _build_tools_list() if t.name == "search_symbols")
    enum = tool.inputSchema["properties"]["kind"]["enum"]
    assert sorted(enum) == sorted(VALID_KINDS)


def test_kind_families_cover_the_database_kinds():
    from jcodemunch_mcp.parser.symbols import is_callable_kind, is_type_like_kind
    assert is_callable_kind("procedure")
    assert is_callable_kind("trigger")
    assert is_callable_kind("function")
    assert not is_callable_kind("table")
    assert is_type_like_kind("table")
    assert is_type_like_kind("view")
    assert not is_type_like_kind("procedure")


def test_temp_table_inside_procedure_is_not_a_symbol():
    symbols = parse_file(TSQL_PROC_WITH_TEMP, "usp_ResetCounters.sql", "sql")
    names = [s.name for s in symbols]
    assert "dbo.usp_ResetCounters" in names
    assert "amount" not in names
    assert not any(s.kind == "table" for s in symbols)


def test_temp_marker_is_read_from_source_not_from_the_name():
    from jcodemunch_mcp.parser.extractor import _SQL_TEMP_MARKER_RE
    assert _SQL_TEMP_MARKER_RE.match(b"CREATE TABLE #Temp (amount INT)")
    assert _SQL_TEMP_MARKER_RE.match(b"create table @tvar (id INT)")
    assert _SQL_TEMP_MARKER_RE.match(b"CREATE TEMPORARY TABLE #t (a INT)")
    assert not _SQL_TEMP_MARKER_RE.match(b"CREATE TABLE dbo.Real (a INT)")


def test_cte_inside_procedure_is_kept_and_reparented():
    symbols = parse_file(TSQL_PROC_WITH_CTE, "usp_RecentReport.sql", "sql")
    cte = next((s for s in symbols if s.name == "cte_Recent"), None)
    assert cte is not None
    proc = next(s for s in symbols if s.name == "dbo.usp_RecentReport")
    assert cte.parent == proc.id


def test_top_level_table_keeps_no_parent():
    symbols = parse_file("CREATE TABLE dbo.Widgets (WidgetId BIGINT);\n", "Widgets.sql", "sql")
    assert symbols[0].parent is None


def test_real_table_inside_routine_is_reparented_not_dropped():
    src = (
        "CREATE PROCEDURE dbo.Deploy\n"
        "AS\n"
        "CREATE TABLE dbo.NewThing (id INT)\n"
        "GO\n"
    )
    symbols = parse_file(src, "Deploy.sql", "sql")
    tbl = next((s for s in symbols if s.kind == "table"), None)
    assert tbl is not None
    proc = next(s for s in symbols if s.name == "dbo.Deploy")
    assert tbl.parent == proc.id


def test_parser_generation_advanced_for_the_sql_changes():
    """The stored SQL rows changed on unchanged content, so an index stamped
    with the previous generation must be re-parsed."""
    from jcodemunch_mcp.storage.index_store import PARSER_GENERATION
    assert PARSER_GENERATION >= 22


_ROUTINE_SPELLINGS = [
    ("tsql-alter-procedure",
     "ALTER PROCEDURE dbo.usp_X @a int AS BEGIN SELECT 1 END\n", "dbo.usp_X", "procedure"),
    ("tsql-alter-proc",
     "ALTER PROC dbo.usp_X AS SELECT 1\n", "dbo.usp_X", "procedure"),
    ("tsql-alter-procedure-bracketed-numbered",
     "ALTER PROCEDURE [dbo].[usp_X];1 @a int AS SELECT 1\n", "dbo.usp_X", "procedure"),
    ("tsql-alter-trigger",
     "ALTER TRIGGER dbo.trg_Y ON dbo.Orders AFTER INSERT AS SELECT 1\n", "dbo.trg_Y", "trigger"),
    ("tsql-create-or-alter-trigger",
     "CREATE OR ALTER TRIGGER dbo.trg_Y ON dbo.Orders AFTER INSERT AS SELECT 1\n",
     "dbo.trg_Y", "trigger"),
    ("pg-create-or-replace-procedure",
     "CREATE OR REPLACE PROCEDURE usp_X(a int) LANGUAGE sql AS $$ SELECT 1 $$;\n",
     "usp_X", "procedure"),
    ("pg-create-or-replace-trigger",
     "CREATE OR REPLACE TRIGGER trg_Y BEFORE INSERT ON dbo.Orders "
     "FOR EACH ROW EXECUTE FUNCTION f();\n", "trg_Y", "trigger"),
    ("pg-create-constraint-trigger",
     "CREATE CONSTRAINT TRIGGER trg_Y AFTER INSERT ON dbo.Orders "
     "FOR EACH ROW EXECUTE FUNCTION f();\n", "trg_Y", "trigger"),
    ("oracle-editionable-procedure",
     "CREATE OR REPLACE EDITIONABLE PROCEDURE usp_X IS BEGIN NULL; END;\n",
     "usp_X", "procedure"),
    ("mysql-definer-backticks-procedure",
     "CREATE DEFINER=`user`@`host` PROCEDURE usp_X() BEGIN SELECT 1; END\n",
     "usp_X", "procedure"),
    ("mysql-definer-quoted-trigger",
     "CREATE DEFINER='user'@'%' TRIGGER trg_Y BEFORE INSERT ON tbl "
     "FOR EACH ROW SET NEW.a = 1;\n", "trg_Y", "trigger"),
    ("mysql-definer-double-quoted-procedure",
     'CREATE DEFINER = "user"@"host" PROCEDURE usp_X() BEGIN SELECT 1; END\n',
     "usp_X", "procedure"),
    ("mysql-definer-bare-procedure",
     "CREATE DEFINER=user@localhost PROCEDURE usp_X() BEGIN SELECT 1; END\n",
     "usp_X", "procedure"),
    ("mysql-definer-current-user",
     "CREATE DEFINER=CURRENT_USER PROCEDURE usp_X() BEGIN SELECT 1; END\n",
     "usp_X", "procedure"),
    ("mysql-definer-current-user-call",
     "CREATE DEFINER=CURRENT_USER() TRIGGER trg_Y AFTER DELETE ON tbl "
     "FOR EACH ROW SET @a = 1;\n", "trg_Y", "trigger"),
    ("mysql-or-replace-definer",
     "CREATE OR REPLACE DEFINER=`u`@`h` PROCEDURE usp_X() BEGIN SELECT 1; END\n",
     "usp_X", "procedure"),
    ("mysql-if-not-exists",
     "CREATE PROCEDURE IF NOT EXISTS usp_X() BEGIN SELECT 1; END\n", "usp_X", "procedure"),
    ("lowercase",
     "alter procedure usp_X as select 1\n", "usp_X", "procedure"),
]


@pytest.mark.parametrize(
    "src,name,kind",
    [pytest.param(s, n, k, id=i) for i, s, n, k in _ROUTINE_SPELLINGS],
)
def test_every_spelling_that_defines_a_routine_yields_one_symbol(src, name, kind):
    symbols = parse_file(src, "spelling.sql", "sql")
    routines = [s for s in symbols if s.kind in ("procedure", "trigger")]
    assert [(s.name, s.kind) for s in routines] == [(name, kind)]
    assert routines[0].signature.split()[0].upper() in ("CREATE", "ALTER")
    assert routines[0].line == 1
    assert not [s for s in symbols if s.kind == "function"]


def test_an_alter_and_a_create_of_the_same_name_are_two_numbered_symbols():
    src = (
        "CREATE PROCEDURE dbo.usp_X AS SELECT 1\n"
        "GO\n"
        "ALTER PROCEDURE dbo.usp_X AS SELECT 2\n"
        "GO\n"
    )
    procs = [s for s in parse_file(src, "both.sql", "sql") if s.kind == "procedure"]
    assert len(procs) == 2
    assert len({s.id for s in procs}) == 2
    assert [s.line for s in procs] == [1, 3]
    assert [s.signature.split()[0].upper() for s in procs] == ["CREATE", "ALTER"]


@pytest.mark.parametrize(
    "src",
    [
        pytest.param("ALTER TABLE dbo.Orders ADD c int;\n", id="alter-table"),
        pytest.param("ALTER TABLE dbo.Orders DISABLE TRIGGER trg_Y;\n", id="alter-table-disable-trigger"),
        pytest.param("ALTER VIEW dbo.vw_X AS SELECT 1 AS a;\n", id="alter-view"),
        pytest.param("DROP PROCEDURE dbo.usp_X;\n", id="drop-procedure"),
        pytest.param("DROP PROC IF EXISTS dbo.usp_X;\n", id="drop-proc-if-exists"),
        pytest.param("DROP TRIGGER trg_Y;\n", id="drop-trigger"),
        pytest.param("EXEC dbo.usp_X;\n", id="exec"),
        pytest.param("EXECUTE dbo.usp_X @a = 1;\n", id="execute"),
        pytest.param("GRANT EXECUTE ON dbo.usp_X TO app_role;\n", id="grant-execute"),
        pytest.param("DISABLE TRIGGER trg_Y ON dbo.Orders;\n", id="disable-trigger"),
        pytest.param("ENABLE TRIGGER trg_Y ON dbo.Orders;\n", id="enable-trigger"),
        pytest.param("ALTER PROCEDURE dbo.usp_X COMPILE;\nSELECT 1 AS a;\n", id="alter-procedure-compile"),
        pytest.param("ALTER PROCEDURE usp_X RENAME TO usp_W;\n", id="alter-procedure-rename"),
        pytest.param("ALTER PROCEDURE usp_X OWNER TO app_role;\n", id="alter-procedure-owner"),
        pytest.param("ALTER TRIGGER trg_Y ON dbo.Orders DISABLE;\nSELECT 1 AS a;\n", id="alter-trigger-disable"),
        pytest.param("ALTER TRIGGER trg_Y ENABLE;\n", id="alter-trigger-enable"),
    ],
)
def test_statements_that_define_no_routine_create_no_routine(src):
    symbols = parse_file(src, "negative.sql", "sql")
    assert [s for s in symbols if s.kind in ("procedure", "trigger")] == []


def test_a_maintenance_alter_does_not_swallow_the_routine_after_it():
    src = (
        "ALTER PROCEDURE usp_Old COMPILE;\n"
        "GO\n"
        "ALTER PROCEDURE dbo.usp_X AS SELECT 1\n"
        "GO\n"
    )
    procs = [s for s in parse_file(src, "mixed_alter.sql", "sql") if s.kind == "procedure"]
    assert [(s.name, s.line) for s in procs] == [("dbo.usp_X", 3)]


def test_alter_in_comments_and_strings_declares_nothing():
    src = (
        "-- ALTER PROCEDURE dbo.NotReal AS SELECT 1\n"
        "/* ALTER TRIGGER dbo.AlsoNotReal ON dbo.T AFTER INSERT AS SELECT 1 */\n"
        "EXEC('ALTER PROCEDURE dbo.StringNotReal AS SELECT 1')\n"
        "SELECT 'CREATE OR REPLACE PROCEDURE dbo.QuotedNotReal AS' AS x\n"
        "ALTER PROCEDURE dbo.Real AS SELECT 1\n"
    )
    names = [s.name for s in parse_file(src, "mixed_alter2.sql", "sql") if s.kind == "procedure"]
    assert names == ["dbo.Real"]


_TRIGGER_TARGETS = [
    pytest.param(
        "CREATE TRIGGER trg_Y ON dbo.Orders FOR INSERT AS SELECT 1\n", id="tsql-on-after-name"),
    pytest.param(
        "ALTER TRIGGER trg_Y ON dbo.Orders AFTER INSERT AS SELECT 1\n", id="tsql-alter"),
    pytest.param(
        "CREATE TRIGGER trg_Y BEFORE INSERT ON dbo.Orders FOR EACH ROW EXECUTE FUNCTION f();\n",
        id="pg-before-insert"),
    pytest.param(
        "CREATE OR REPLACE TRIGGER trg_Y AFTER INSERT OR UPDATE OF a, b OR DELETE ON dbo.Orders "
        "FOR EACH ROW EXECUTE FUNCTION f();\n", id="pg-or-replace-multi-event-update-of"),
    pytest.param(
        "CREATE TRIGGER trg_Y INSTEAD OF INSERT ON dbo.Orders FOR EACH ROW EXECUTE FUNCTION f();\n",
        id="pg-instead-of"),
    pytest.param(
        "CREATE DEFINER=`u`@`h` TRIGGER trg_Y BEFORE UPDATE ON dbo.Orders FOR EACH ROW SET @a = 1;\n",
        id="mysql-definer"),
    pytest.param(
        "CREATE TRIGGER trg_Y AFTER DELETE ON dbo.Orders FOR EACH ROW SET @a = 1;\n",
        id="mysql-after-delete"),
]


@pytest.mark.parametrize("trigger_sql", _TRIGGER_TARGETS)
def test_trigger_parent_is_the_same_file_table_for_every_timing_spelling(trigger_sql):
    src = "CREATE TABLE dbo.Orders (a INT, b INT);\nGO\n" + trigger_sql
    symbols = parse_file(src, "same_file.sql", "sql")
    table = next(s for s in symbols if s.kind == "table")
    trig = next(s for s in symbols if s.kind == "trigger")
    assert trig.parent == table.id
    assert "dbo.Orders" in trig.signature


@pytest.mark.parametrize("trigger_sql", _TRIGGER_TARGETS)
def test_trigger_on_a_table_defined_elsewhere_keeps_the_name_and_no_parent(trigger_sql):
    trig = next(s for s in parse_file(trigger_sql, "elsewhere2.sql", "sql") if s.kind == "trigger")
    assert trig.parent is None
    assert "dbo.Orders" in trig.signature


def test_create_or_replace_trigger_execute_function_makes_no_function_symbol():
    src = (
        "CREATE OR REPLACE TRIGGER trg_Y BEFORE INSERT ON dbo.Orders\n"
        "FOR EACH ROW EXECUTE FUNCTION audit_fn();\n"
    )
    symbols = parse_file(src, "pg_trigger.sql", "sql")
    assert [(s.kind, s.name) for s in symbols] == [("trigger", "trg_Y")]


def test_routine_header_scan_is_linear_in_whitespace_runs():
    """Compare two input sizes against each other instead of a wall-clock
    bound: 4x the whitespace should cost about 4x, a quadratic scan about 16x."""
    import time
    from jcodemunch_mcp.parser.extractor import _SQL_ROUTINE_HEADER_RE, _SQL_TRIGGER_ON_RE

    def cost(n):
        hostile = [
            b"CREATE DEFINER=" + b" " * n + b"x",
            b"CREATE DEFINER=a@" + b" " * n + b"x",
            b"CREATE DEFINER=`a`@" + b"\t" * n + b"PROCEDURE" + b" " * n + b"IF NOT" + b" " * n + b"x",
            b"CREATE OR" + b" " * n + b"x",
            b"ALTER" + b" " * n + b"x",
        ]
        trigger = b" " * n + b"BEFORE INSERT OR " * (n // 20) + b"x"
        best = float("inf")
        for _ in range(3):
            t0 = time.perf_counter()
            for src in hostile:
                _SQL_ROUTINE_HEADER_RE.search(src)
            _SQL_TRIGGER_ON_RE.match(trigger, 0)
            best = min(best, time.perf_counter() - t0)
        return best

    small, large = cost(5_000), cost(20_000)
    assert large / max(small, 1e-4) < 10, (small, large)
# ---------------------------------------------------------------------------
# A routine's span ends at its own terminator (#1080 review).
# ---------------------------------------------------------------------------

def _body(src, sym):
    raw = src.encode("utf-8")
    return raw[sym.byte_offset:sym.byte_offset + sym.byte_length].decode("utf-8")


def _by_name(symbols):
    return {s.name: s for s in symbols}


PG_PROCEDURE_THEN_DDL = """\
CREATE OR REPLACE PROCEDURE p_one() LANGUAGE plpgsql AS $$ BEGIN NULL; END; $$;
CREATE TABLE orders (id int primary key);
CREATE VIEW v_orders AS SELECT id FROM orders;
CREATE INDEX ix_orders ON orders (id);
"""


def test_objects_after_a_dollar_quoted_procedure_are_top_level():
    symbols = _by_name(parse_file(PG_PROCEDURE_THEN_DDL, "pg.sql", "sql"))
    proc = symbols["p_one"]
    assert (proc.line, proc.end_line) == (1, 1)
    assert _body(PG_PROCEDURE_THEN_DDL, proc).rstrip().endswith("$$;")
    for name in ("orders", "v_orders", "ix_orders"):
        assert symbols[name].parent is None, name


def test_a_tagged_dollar_quote_ends_at_its_own_tag_not_at_an_inner_dollar_quote():
    src = (
        "CREATE PROCEDURE p_one() LANGUAGE plpgsql AS $body$\n"
        "BEGIN\n"
        "  EXECUTE $q$ SELECT 1; $q$;\n"
        "  NULL;\n"
        "END;\n"
        "$body$;\n"
        "CREATE TABLE orders (id int);\n"
    )
    symbols = _by_name(parse_file(src, "pg_tag.sql", "sql"))
    proc = symbols["p_one"]
    assert (proc.line, proc.end_line) == (1, 6)
    assert symbols["orders"].parent is None


def test_a_dollar_quoted_procedure_with_trailing_characteristics_ends_at_the_semicolon():
    src = (
        "CREATE PROCEDURE p_one()\n"
        "AS $$ BEGIN NULL; END; $$\n"
        "LANGUAGE plpgsql;\n"
        "CREATE TABLE orders (id int);\n"
    )
    symbols = _by_name(parse_file(src, "pg_lang.sql", "sql"))
    assert (symbols["p_one"].line, symbols["p_one"].end_line) == (1, 3)
    assert symbols["orders"].parent is None


def test_a_single_quoted_procedure_body_ends_at_the_semicolon_after_it():
    src = (
        "CREATE PROCEDURE p_one() LANGUAGE sql AS 'SELECT 1; SELECT 2';\n"
        "CREATE TABLE orders (id int);\n"
    )
    symbols = _by_name(parse_file(src, "pg_str.sql", "sql"))
    assert symbols["p_one"].end_line == 1
    assert symbols["orders"].parent is None


MYSQL_PROCEDURE_THEN_DDL = """\
DELIMITER ;;
CREATE PROCEDURE p_one()
BEGIN
  SELECT 1;
  SELECT 2;
END ;;
DELIMITER ;
CREATE TABLE orders (id INT PRIMARY KEY);
CREATE VIEW v_orders AS SELECT id FROM orders;
"""


def test_objects_after_a_mysql_delimiter_procedure_are_top_level():
    symbols = _by_name(parse_file(MYSQL_PROCEDURE_THEN_DDL, "my.sql", "sql"))
    proc = symbols["p_one"]
    assert (proc.line, proc.end_line) == (2, 6)
    assert _body(MYSQL_PROCEDURE_THEN_DDL, proc).rstrip().endswith(";;")
    assert symbols["orders"].parent is None
    assert symbols["v_orders"].parent is None


def test_a_mysql_delimiter_that_is_dollars_is_not_a_dollar_quote():
    src = (
        "DELIMITER $$\n"
        "CREATE PROCEDURE p_one()\n"
        "BEGIN\n"
        "  SELECT 1;\n"
        "END$$\n"
        "DELIMITER ;\n"
        "CREATE TABLE orders (id INT);\n"
    )
    symbols = _by_name(parse_file(src, "my_dollar.sql", "sql"))
    assert (symbols["p_one"].line, symbols["p_one"].end_line) == (2, 5)
    assert symbols["orders"].parent is None


def test_a_mysql_trigger_under_a_custom_delimiter_ends_at_the_delimiter():
    src = (
        "DELIMITER //\n"
        "CREATE TRIGGER trg_one BEFORE INSERT ON orders FOR EACH ROW\n"
        "BEGIN\n"
        "  SET NEW.id = 1;\n"
        "END//\n"
        "DELIMITER ;\n"
        "CREATE TABLE orders (id INT);\n"
    )
    symbols = _by_name(parse_file(src, "my_trg.sql", "sql"))
    assert symbols["trg_one"].end_line == 5
    assert symbols["orders"].parent is None


PLAIN_BEGIN_END = """\
CREATE PROCEDURE p_one()
BEGIN
  DECLARE n INT DEFAULT 0;
  IF n = 0 THEN
    SELECT CASE WHEN n = 0 THEN 'zero' ELSE 'other' END;
  END IF;
  WHILE n < 3 DO
    SET n = n + 1;
  END WHILE;
  CASE n
    WHEN 3 THEN SELECT 3;
    ELSE SELECT 0;
  END CASE;
END;
CREATE TABLE orders (id INT PRIMARY KEY);
CREATE INDEX ix_orders ON orders (id);
"""


def test_objects_after_a_plain_begin_end_procedure_are_top_level():
    symbols = _by_name(parse_file(PLAIN_BEGIN_END, "plain.sql", "sql"))
    proc = symbols["p_one"]
    assert (proc.line, proc.end_line) == (1, 14)
    assert symbols["orders"].parent is None
    assert symbols["ix_orders"].parent is None


def test_an_oracle_style_is_begin_end_name_procedure_ends_at_its_semicolon():
    src = (
        "CREATE OR REPLACE PROCEDURE p_one IS\n"
        "BEGIN\n"
        "  NULL;\n"
        "END p_one;\n"
        "CREATE TABLE orders (id INT);\n"
    )
    symbols = _by_name(parse_file(src, "ora.sql", "sql"))
    assert symbols["p_one"].end_line == 4
    assert symbols["orders"].parent is None


def test_a_semicolon_trigger_without_a_body_ends_at_its_semicolon():
    src = (
        "CREATE TRIGGER trg_one BEFORE INSERT ON orders\n"
        "FOR EACH ROW WHEN (NEW.id IS NOT NULL) EXECUTE FUNCTION audit_fn();\n"
        "CREATE VIEW v_orders AS SELECT 1;\n"
    )
    symbols = _by_name(parse_file(src, "pg_trg.sql", "sql"))
    assert symbols["trg_one"].end_line == 2
    assert symbols["v_orders"].parent is None


def test_two_adjacent_procedures_each_end_at_their_own_terminator():
    src = (
        "CREATE PROCEDURE p_one() LANGUAGE sql AS $$ SELECT 1 $$;\n"
        "CREATE TABLE orders (id int);\n"
        "CREATE PROCEDURE p_two() LANGUAGE sql AS $$ SELECT 2 $$;\n"
        "CREATE TABLE items (id int);\n"
    )
    symbols = _by_name(parse_file(src, "two.sql", "sql"))
    assert symbols["p_one"].end_line == 1
    assert symbols["p_two"].end_line == 3
    assert symbols["orders"].parent is None
    assert symbols["items"].parent is None


# T-SQL has no reliable body terminator: its span keeps running to `GO` or the
# next routine header, so these must NOT end at an inner semicolon.
def test_a_tsql_body_without_begin_keeps_running_past_semicolons_to_go():
    src = (
        "CREATE PROCEDURE dbo.usp_One\n"
        "AS\n"
        "  UPDATE a SET x = 1;\n"
        "  UPDATE b SET y = 2;\n"
        "GO\n"
    )
    proc = _by_name(parse_file(src, "t.sql", "sql"))["dbo.usp_One"]
    assert (proc.line, proc.end_line) == (1, 4)


def test_a_tsql_begin_end_body_without_a_semicolon_runs_to_go():
    src = (
        "CREATE PROCEDURE dbo.usp_One\n"
        "AS\n"
        "BEGIN\n"
        "  SELECT 1\n"
        "END\n"
        "SELECT 2\n"
        "GO\n"
    )
    proc = _by_name(parse_file(src, "t.sql", "sql"))["dbo.usp_One"]
    assert proc.end_line == 6


def test_begin_transaction_is_not_a_block_opener():
    src = (
        "CREATE PROCEDURE p_one()\n"
        "BEGIN\n"
        "  BEGIN TRANSACTION;\n"
        "  BEGIN TRY\n"
        "    SELECT 1;\n"
        "  END TRY\n"
        "END;\n"
        "CREATE TABLE orders (id INT);\n"
    )
    symbols = _by_name(parse_file(src, "tx.sql", "sql"))
    assert symbols["p_one"].end_line == 7
    assert symbols["orders"].parent is None


def test_an_unterminated_dollar_quote_falls_back_to_the_old_span():
    src = (
        "CREATE PROCEDURE p_one() LANGUAGE sql AS $$ SELECT 1;\n"
        "CREATE TABLE orders (id int);\n"
    )
    symbols = _by_name(parse_file(src, "open.sql", "sql"))
    assert symbols["p_one"].end_line == 2
    assert symbols["orders"].parent == symbols["p_one"].id


def test_a_bracket_identifier_with_an_apostrophe_does_not_hide_later_routines():
    src = (
        "CREATE VIEW [it's] AS SELECT 1\n"
        "GO\n"
        "CREATE PROCEDURE dbo.after_apostrophe\n"
        "AS\n"
        "SELECT 1\n"
        "GO\n"
    )
    names = [s.name for s in parse_file(src, "br.sql", "sql")]
    assert "dbo.after_apostrophe" in names


def test_a_bracket_identifier_keeps_its_comment_markers_opaque():
    from jcodemunch_mcp.parser.extractor import _mask_sql_noise
    masked = _mask_sql_noise(b"[a--b] [it's] x")
    assert len(masked) == len(b"[a--b] [it's] x")
    assert b"'" not in masked
    assert masked.endswith(b" x")
    assert masked.count(b"[") == 2 and masked.count(b"]") == 2


def test_a_schema_does_not_outrank_its_tables_as_the_file_representative(tmp_path):
    from jcodemunch_mcp.parser.symbols import REPRESENTATIVE_KIND_RANK as RANK
    from jcodemunch_mcp.tools.get_repo_map import get_repo_map
    from jcodemunch_mcp.tools.index_folder import index_folder

    for kind in ("table", "view", "index"):
        assert RANK[kind] == RANK["type"], kind
    for kind in ("procedure", "trigger"):
        assert RANK[kind] < RANK["type"], kind

    (tmp_path / "schema.sql").write_text(
        "CREATE SCHEMA app;\n"
        "CREATE TABLE app.orders (id INT PRIMARY KEY, total INT, note TEXT);\n"
        "CREATE TABLE app.items (id INT PRIMARY KEY, order_id INT, sku TEXT);\n",
        encoding="utf-8",
    )
    storage = str(tmp_path / ".index")
    repo = index_folder(str(tmp_path), use_ai_summaries=False, storage_path=storage)["repo"]
    files = {f["path"]: f for f in get_repo_map(repo, max_per_file=1, storage_path=storage)["files"]}
    (shown,) = files["schema.sql"]["symbols"]
    assert shown["kind"] == "table"


def test_the_routine_scans_cost_grows_linearly_with_input_size():
    """Ratio of two input sizes, not a wall-clock bound: 4x the input should
    cost about 4x; a quadratic scan costs about 16x."""
    import time
    from jcodemunch_mcp.parser.extractor import _scan_sql_routines

    def cost(n):
        sources = [
            b"CREATE PROCEDURE p_one() LANGUAGE sql AS $a$" + b" " * n + b"x",
            b"DELIMITER ;;\nCREATE PROCEDURE p_one()\nBEGIN\n" + b"BEGIN\n" * (n // 6),
            b"CREATE PROCEDURE p_one() BEGIN " + b"CASE " * (n // 5),
            b"CREATE PROCEDURE p_one() " + b"AS " * (n // 3),
            b"CREATE PROCEDURE p_one IS " + b"PROCEDURE a; " * (n // 13),
            b"CREATE PROCEDURE p_one IS " + b"v NUMBER; " * (n // 10),
            b"CREATE PROCEDURE p_one IS " + b"PROCEDURE a IS " * (n // 15),
            b"".join(b"CREATE PROCEDURE p%d() LANGUAGE sql AS $$ x\n" % i for i in range(n // 40)),
        ]
        best = float("inf")
        for _ in range(3):
            t0 = time.perf_counter()
            for src in sources:
                _scan_sql_routines(src, "x.sql")
            best = min(best, time.perf_counter() - t0)
        return best

    small, large = cost(20_000), cost(80_000)
    assert large / max(small, 1e-4) < 10, (small, large)


# ---------------------------------------------------------------------------
# An Oracle routine with a declaration section ends at its own END; (round 2).
# ---------------------------------------------------------------------------

ORACLE_IS_DECLARATION = """\
CREATE OR REPLACE PROCEDURE p_o IS
  v NUMBER;
BEGIN
  v := 1;
END p_o;
CREATE TABLE t8 (id int);
CREATE INDEX ix8 ON t8 (id);
"""


def test_objects_after_an_oracle_procedure_with_a_declaration_section_are_top_level():
    symbols = _by_name(parse_file(ORACLE_IS_DECLARATION, "ora.sql", "sql"))
    proc = symbols["p_o"]
    assert (proc.line, proc.end_line) == (1, 5)
    assert _body(ORACLE_IS_DECLARATION, proc).rstrip().endswith("END p_o;")
    assert symbols["t8"].parent is None
    assert symbols["ix8"].parent is None


def test_an_oracle_as_declaration_with_an_initialiser_ends_at_its_end():
    src = (
        "CREATE OR REPLACE PROCEDURE p_o(a IN NUMBER) AS\n"
        "  v NUMBER := 0;\n"
        "  w CONSTANT VARCHAR2(10) := 'x;y';\n"
        "BEGIN\n"
        "  v := a;\n"
        "END;\n"
        "CREATE TABLE t8 (id int);\n"
    )
    symbols = _by_name(parse_file(src, "ora_as.sql", "sql"))
    assert (symbols["p_o"].line, symbols["p_o"].end_line) == (1, 6)
    assert symbols["t8"].parent is None


def test_an_oracle_declare_section_with_a_cursor_and_a_local_procedure():
    src = (
        "CREATE OR REPLACE PROCEDURE p_o IS\n"
        "  CURSOR c IS SELECT id FROM t8;\n"
        "  TYPE rec IS RECORD (a NUMBER, b VARCHAR2(5));\n"
        "  PRAGMA AUTONOMOUS_TRANSACTION;\n"
        "  PROCEDURE fwd(x NUMBER);\n"
        "  PROCEDURE local_p(x NUMBER) IS\n"
        "    n NUMBER;\n"
        "  BEGIN\n"
        "    n := x;\n"
        "  END local_p;\n"
        "BEGIN\n"
        "  local_p(1);\n"
        "END p_o;\n"
        "CREATE TABLE t8 (id int);\n"
        "CREATE INDEX ix8 ON t8 (id);\n"
    )
    symbols = _by_name(parse_file(src, "ora_cur.sql", "sql"))
    assert (symbols["p_o"].line, symbols["p_o"].end_line) == (1, 13)
    assert symbols["t8"].parent is None
    assert symbols["ix8"].parent is None


def test_an_oracle_local_function_with_return_is_skipped_as_a_declaration():
    src = (
        "CREATE OR REPLACE PROCEDURE p_o IS\n"
        "  FUNCTION f(x NUMBER) RETURN NUMBER IS\n"
        "  BEGIN\n"
        "    RETURN CASE WHEN x > 0 THEN 1 ELSE 0 END;\n"
        "  END f;\n"
        "BEGIN\n"
        "  NULL;\n"
        "END;\n"
        "CREATE TABLE t8 (id int);\n"
    )
    symbols = _by_name(parse_file(src, "ora_fn.sql", "sql"))
    assert (symbols["p_o"].line, symbols["p_o"].end_line) == (1, 8)
    assert symbols["t8"].parent is None


def test_an_oracle_trigger_with_a_declare_section_ends_at_its_end():
    src = (
        "CREATE OR REPLACE TRIGGER trg_o BEFORE INSERT ON t8 FOR EACH ROW\n"
        "DECLARE\n"
        "  v NUMBER;\n"
        "BEGIN\n"
        "  v := 1;\n"
        "END;\n"
        "CREATE TABLE t8 (id int);\n"
    )
    symbols = _by_name(parse_file(src, "ora_trg.sql", "sql"))
    assert symbols["trg_o"].end_line == 6


def test_an_oracle_declaration_section_with_no_begin_falls_back_to_the_old_span():
    src = (
        "CREATE OR REPLACE PROCEDURE p_o IS\n"
        "  v NUMBER;\n"
        "CREATE TABLE t8 (id int);\n"
    )
    symbols = _by_name(parse_file(src, "ora_open.sql", "sql"))
    assert symbols["p_o"].end_line == 3


def test_a_tsql_as_body_of_semicolon_statements_still_runs_to_go():
    src = (
        "CREATE PROC dbo.usp_One AS SET NOCOUNT ON; SELECT 1;\n"
        "UPDATE a SET x = 1;\n"
        "BEGIN\n"
        "  SELECT 2;\n"
        "END;\n"
        "SELECT 3;\n"
        "GO\n"
    )
    proc = _by_name(parse_file(src, "t.sql", "sql"))["dbo.usp_One"]
    assert (proc.line, proc.end_line) == (1, 6)


def test_a_tsql_as_body_shaped_like_a_declaration_still_runs_to_go():
    src = (
        "CREATE PROCEDURE dbo.usp_Two\n"
        "AS\n"
        "  EXEC dbo.usp_Other;\n"
        "  IF @x IS NULL SELECT 1;\n"
        "BEGIN\n"
        "  SELECT 2;\n"
        "END;\n"
        "SELECT 3;\n"
        "GO\n"
    )
    proc = _by_name(parse_file(src, "t.sql", "sql"))["dbo.usp_Two"]
    assert (proc.line, proc.end_line) == (1, 8)
