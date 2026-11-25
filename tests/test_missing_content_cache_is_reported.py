"""A body the index cannot produce must be reported, never returned as `""`.

Resolving a symbol and producing its source are two different successes. The row
lives in the `.db`; the bytes live in a separate content directory. When that
directory is absent -- pruned, copied without its sibling, or never shipped, as
with a starter pack, which carries `.db` files only -- `get_symbol_source`
returned the row with `source: ""` under `_meta.verdict.state == "ok"` and
`_freshness: "fresh"`. Measured against the published `nodejs` pack on
2026-07-30: bodies for 0 of 50 symbols, every one of them reported confidently.

The `source: ""` half was fixed in v1.108.204 (`source_status`). The
`_freshness: "fresh"` half named above stood until v1.108.209, which gave the
per-file classifier an `unknown` bucket — see `test_v1_108_209.py`.

These tests build that exact store shape: a real index with no content
directory beside it.
"""
from __future__ import annotations

import pytest

from jcodemunch_mcp.retrieval.verdict import build_symbol_verdict
from jcodemunch_mcp.storage.sqlite_store import SQLiteIndexStore
from jcodemunch_mcp.tools.get_symbol import get_symbol_source

SOURCE = "def hello(name):\n    return f'hi {name}'\n"


def _index_tree(tmp_path, folder_name):
    """Index a small tree and report the identity jcm chose for it.

    `index_folder` derives owner/name itself, so the test asks the store which
    repo appeared rather than asserting one.
    """
    from jcodemunch_mcp.tools.index_folder import index_folder

    src = tmp_path / folder_name
    src.mkdir()
    (src / "greet.py").write_text(SOURCE, encoding="utf-8")

    index_folder(str(src), storage_path=str(tmp_path), identity_mode="local")

    dbs = list(tmp_path.glob("*.db"))
    assert len(dbs) == 1, f"expected exactly one index, got {dbs}"
    owner, _, name = dbs[0].stem.partition("-")
    return owner, name


@pytest.fixture
def store_without_content(tmp_path):
    """An index whose rows are intact and whose content cache is gone."""
    owner, name = _index_tree(tmp_path, "widget")
    store = SQLiteIndexStore(base_path=str(tmp_path))

    content_dir = store._content_dir(owner, name)
    if content_dir.exists():
        import shutil

        shutil.rmtree(content_dir)
    assert not content_dir.exists()

    index = store.load_index(owner, name)
    assert index is not None and index.symbols, "fixture must produce real rows"
    return tmp_path, index, f"{owner}/{name}"


def _first_function_id(index) -> str:
    for sym in index.symbols:
        sid = sym["id"] if isinstance(sym, dict) else sym.id
        kind = sym["kind"] if isinstance(sym, dict) else sym.kind
        if kind in ("function", "method", "class"):
            return sid
    raise AssertionError("fixture produced no function/class symbol")


def test_single_symbol_reports_the_missing_body(store_without_content):
    base, index, repo = store_without_content
    sid = _first_function_id(index)

    result = get_symbol_source(repo=repo, symbol_id=sid, storage_path=str(base))

    assert result["source"] == ""
    assert result["source_status"] == "content_cache_missing"
    # The remedy is the one-file repair, not a whole-repo re-index.
    assert "index_file" in result["source_unavailable_reason"]

    verdict = result["_meta"]["verdict"]
    assert verdict["state"] == "degraded"
    assert verdict["channels"]["content_cache"] == "missing"
    assert verdict["unavailable_source_count"] == 1
    # The note must say the emptiness is unavailability, not an empty symbol.
    assert "NOT because the symbol is empty" in verdict["note"]


def test_the_row_itself_is_still_returned(store_without_content):
    """Refusing the body is not refusing the answer.

    Signature, line range and docstring come from the `.db` and are accurate;
    degrading them too would throw away retrieval that genuinely worked.
    """
    base, index, repo = store_without_content
    sid = _first_function_id(index)

    result = get_symbol_source(repo=repo, symbol_id=sid, storage_path=str(base))

    assert result["id"] == sid
    assert result["signature"]
    assert result["line"] >= 1
    assert result["end_line"] >= result["line"]


def test_batch_mode_counts_and_names_them(store_without_content):
    base, index, repo = store_without_content
    sid = _first_function_id(index)

    result = get_symbol_source(repo=repo, symbol_ids=[sid], storage_path=str(base))

    assert result["symbols"][0]["source_status"] == "content_cache_missing"
    assert result["_meta"]["unavailable_source_ids"] == [sid]
    assert result["_meta"]["verdict"]["state"] == "degraded"


def test_healthy_store_is_untouched(tmp_path):
    """The common path must be byte-for-byte what it was.

    Without this, a bug that reported every body as missing would still pass
    every assertion above.
    """
    owner, name = _index_tree(tmp_path, "widget")
    store = SQLiteIndexStore(base_path=str(tmp_path))
    index = store.load_index(owner, name)
    sid = _first_function_id(index)

    result = get_symbol_source(
        repo=f"{owner}/{name}", symbol_id=sid, storage_path=str(tmp_path)
    )

    assert result["source"].strip(), "a healthy store must still return the body"
    assert "source_status" not in result
    assert "source_unavailable_reason" not in result
    verdict = result["_meta"]["verdict"]
    assert verdict["state"] == "ok"
    assert "content_cache" not in verdict["channels"]
    assert "unavailable_source_count" not in verdict


def test_context_bundle_labels_it_too(store_without_content):
    """`get_symbol_source`'s own hint sends callers here.

    Fixing one tool and leaving the alternative it recommends silently empty
    would just move the defect one call to the right.
    """
    from jcodemunch_mcp.tools.get_context_bundle import get_context_bundle

    base, index, repo = store_without_content
    sid = _first_function_id(index)

    result = get_context_bundle(repo=repo, symbol_id=sid, storage_path=str(base))

    assert result["source"] == ""
    assert result["source_status"] == "content_cache_missing"


def test_context_bundle_healthy_store_has_no_label(tmp_path):
    from jcodemunch_mcp.tools.get_context_bundle import get_context_bundle

    owner, name = _index_tree(tmp_path, "widget")
    store = SQLiteIndexStore(base_path=str(tmp_path))
    sid = _first_function_id(store.load_index(owner, name))

    result = get_context_bundle(
        repo=f"{owner}/{name}", symbol_id=sid, storage_path=str(tmp_path)
    )

    assert result["source"].strip()
    assert "source_status" not in result


# ── the verdict builder in isolation ──────────────────────────────────────

def test_verdict_ok_when_every_body_was_produced():
    v = build_symbol_verdict(found_count=3, unavailable_source_count=0)
    assert v["state"] == "ok"
    assert "content_cache" not in v["channels"]


def test_verdict_degrades_on_any_unavailable_body():
    v = build_symbol_verdict(found_count=3, unavailable_source_count=1)
    assert v["state"] == "degraded"
    assert v["unavailable_source_count"] == 1
    assert "1 symbol resolved" in v["note"]


def test_verdict_note_pluralises():
    v = build_symbol_verdict(found_count=3, unavailable_source_count=2)
    assert "2 symbols resolved" in v["note"]


def test_absence_still_wins_over_an_unavailable_body():
    """A symbol that is not there is `absent`, not `degraded`.

    The two states answer different questions and the order matters: reporting
    a missing symbol as a content-cache problem would send a caller to re-index
    when the id is simply wrong.
    """
    v = build_symbol_verdict(
        found_count=0, requested_id="nope#function", unavailable_source_count=0
    )
    assert v["state"] == "absent"
