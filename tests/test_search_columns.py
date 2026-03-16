"""Tests for search_columns tool."""

import pytest
from unittest.mock import patch, MagicMock
from jcodemunch_mcp.tools.search_columns import search_columns, _collect_all_columns


def _make_index(context_metadata, symbols=None):
    """Create a mock CodeIndex with the given context_metadata."""
    index = MagicMock()
    index.context_metadata = context_metadata
    index.symbols = symbols or []
    return index


def _patch_search(index):
    """Patch resolve_repo and IndexStore to return the given index."""
    return [
        patch(
            "jcodemunch_mcp.tools.search_columns.resolve_repo",
            return_value=("local", "test"),
        ),
        patch(
            "jcodemunch_mcp.tools.search_columns.IndexStore",
            return_value=MagicMock(load_index=MagicMock(return_value=index)),
        ),
    ]


def test_search_columns_exact_match():
    """Exact column name match returns score=30."""
    index = _make_index({
        "dbt_columns": {
            "orders": {"order_id": "Primary key", "status": "Order status"},
        },
    })
    patches = _patch_search(index)
    with patches[0], patches[1]:
        result = search_columns(repo="test", query="order_id")

    assert result["result_count"] >= 1
    top = result["results"][0]
    assert top["column"] == "order_id"
    # Exact name match (30), no description bonus ("order_id" not in "primary key")
    assert top["score"] == 30


def test_search_columns_partial_match():
    """Query substring in column name scores 15."""
    index = _make_index({
        "dbt_columns": {
            "orders": {"order_id": "Primary key", "customer_id": "FK to customers"},
        },
    })
    patches = _patch_search(index)
    with patches[0], patches[1]:
        result = search_columns(repo="test", query="order")

    matches = [r for r in result["results"] if r["column"] == "order_id"]
    assert len(matches) == 1
    # "order" in "order_id" -> 15, "order" not in "primary key" -> 0
    assert matches[0]["score"] == 15


def test_search_columns_model_pattern_filter():
    """model_pattern='fact_*' filters to only matching models."""
    index = _make_index({
        "dbt_columns": {
            "fact_orders": {"amount": "Order total"},
            "dim_customers": {"name": "Customer name"},
            "fact_payments": {"method": "Payment method"},
        },
    })
    patches = _patch_search(index)
    with patches[0], patches[1]:
        # Use a broad query that would match columns in all models
        result = search_columns(repo="test", query="name method amount", model_pattern="fact_*")

    models_in_results = {r["model"] for r in result["results"]}
    assert "dim_customers" not in models_in_results
    # At least one fact_ model should appear
    assert models_in_results <= {"fact_orders", "fact_payments"}


def test_search_columns_multi_provider():
    """Multiple providers include 'source' field in results."""
    index = _make_index({
        "dbt_columns": {
            "orders": {"order_id": "Primary key"},
        },
        "sqlmesh_columns": {
            "payments": {"payment_id": "Primary key"},
        },
    })
    patches = _patch_search(index)
    with patches[0], patches[1]:
        result = search_columns(repo="test", query="id")

    assert result["result_count"] >= 2
    assert len(result["sources"]) == 2
    # Every result should have a "source" field
    for r in result["results"]:
        assert "source" in r
        assert r["source"] in ("dbt", "sqlmesh")


def test_search_columns_no_metadata():
    """Repo with no context_metadata returns helpful error."""
    index = _make_index({})
    patches = _patch_search(index)
    with patches[0], patches[1]:
        result = search_columns(repo="test", query="anything")

    assert "error" in result
    assert "column metadata" in result["error"].lower()


def test_search_columns_max_results_cap():
    """Result count respects max_results."""
    # Create enough columns to exceed the cap
    columns = {f"col_{i}": f"description {i}" for i in range(50)}
    index = _make_index({
        "dbt_columns": {"big_model": columns},
    })
    patches = _patch_search(index)
    with patches[0], patches[1]:
        result = search_columns(repo="test", query="description", max_results=5)

    assert result["result_count"] <= 5
    assert len(result["results"]) <= 5


def test_collect_all_columns_multiple_keys():
    """_collect_all_columns finds all *_columns keys."""
    meta = {
        "dbt_columns": {"m1": {"c1": "d1"}},
        "sqlmesh_columns": {"m2": {"c2": "d2"}},
        "unrelated_key": "ignored",
    }
    sources = _collect_all_columns(meta)
    assert set(sources.keys()) == {"dbt", "sqlmesh"}
    assert "m1" in sources["dbt"]
    assert "m2" in sources["sqlmesh"]
