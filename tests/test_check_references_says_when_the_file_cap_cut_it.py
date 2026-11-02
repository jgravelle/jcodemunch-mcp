"""`check_references` says when `max_content_results` cut the content scan.

The content search stops after `max_content_results` files (default 20) and
`content_count` is the number of files listed. Nothing in the response said
the scan stopped, so a caller reading `content_count: 20` could not tell
twenty files from twenty of several hundred.

`content_truncated` is true only when at least one more file that mentions
the name exists beyond the cap: the scan reads one matching file past the cap
to know. It is false when the listed files are all there are, including when
exactly `max_content_results` files match. Every other field is unchanged.
"""
from __future__ import annotations

import pytest

from jcodemunch_mcp.tools.check_references import check_references
from jcodemunch_mcp.tools.index_folder import index_folder

NAME = "compute_total"


@pytest.fixture
def repo(tmp_path):
    src = tmp_path / "proj"
    src.mkdir()
    (src / "core.py").write_text(f"def {NAME}(x):\n    return x\n", encoding="utf-8")
    for i in range(3):
        (src / f"use{i}.py").write_text(f"from core import {NAME}\n\n\ndef f{i}():\n    return {NAME}({i})\n", encoding="utf-8")
    store = tmp_path / "store"
    r = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert r.get("success"), r
    return r["repo"], str(store)


@pytest.mark.parametrize("cap,listed,truncated", [(1, 1, True), (2, 2, True), (3, 3, False), (10, 3, False)])
def test_the_flag_says_whether_a_matching_file_was_left_out(repo, cap, listed, truncated):
    repo_id, store = repo
    out = check_references(repo_id, identifier=NAME, max_content_results=cap, storage_path=store)
    assert out["content_count"] == listed == len(out["content_references"])
    assert out["content_truncated"] is truncated


def test_batch_mode_carries_the_flag_per_identifier(repo):
    repo_id, store = repo
    out = check_references(repo_id, identifiers=[NAME, "nothing_named_this"], max_content_results=2, storage_path=store)
    by_name = {r["identifier"]: r for r in out["results"]}
    assert by_name[NAME]["content_truncated"] is True
    assert by_name["nothing_named_this"]["content_truncated"] is False


def test_an_import_only_check_has_no_content_flag(repo):
    repo_id, store = repo
    out = check_references(repo_id, identifier=NAME, search_content=False, storage_path=store)
    assert "content_truncated" not in out
