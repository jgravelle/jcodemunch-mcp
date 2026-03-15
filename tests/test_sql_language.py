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


def test_sql_table_as_type():
    symbols = parse_file(SQL_DDL, "test.sql", "sql")
    table = next((s for s in symbols if s.name == "orders"), None)
    assert table is not None
    assert table.kind == "type"


def test_sql_view_extracted():
    symbols = parse_file(SQL_DDL, "test.sql", "sql")
    view = next((s for s in symbols if s.name == "active_orders"), None)
    assert view is not None
    assert view.kind == "type"


def test_sql_function_extracted():
    symbols = parse_file(SQL_DDL, "test.sql", "sql")
    func = next((s for s in symbols if s.name == "get_order_total"), None)
    assert func is not None
    assert func.kind == "function"


def test_sql_index_extracted():
    symbols = parse_file(SQL_DDL, "test.sql", "sql")
    idx = next((s for s in symbols if s.name == "idx_orders_status"), None)
    assert idx is not None
    assert idx.kind == "type"


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
