"""search_text: brace alternation in file_pattern, and the files a cut page left off.

On a large repository, an agent asked for every caller of a module. Its first
broad search_text came back cut at max_results (20 lines, all from the first
three files), so it could not see which other files matched and narrowed by
guesswork. When it then sent a brace pattern such as
``components/{update,websocket_api,hassio}/**``, file_pattern matched nothing
(fnmatch has no braces) and it ran one search per directory.

Two changes, both in search_text only:

- ``file_pattern`` expands brace alternation, bounded (no nesting, at most
  ``MAX_BRACE_ALTERNATIVES`` patterns). The pattern as written is still tried
  too, so a path with literal braces keeps matching.
- A truncated search keeps counting through its scope and names the matching
  files it did not show: ``more_files: [{file, matches}]``, capped at
  ``MORE_FILES_CAP`` with ``more_files_total``, ``more_files_truncated`` and
  ``more_files_complete`` beside it.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.encoding.schemas import search_text as st_schema
from jcodemunch_mcp.tools import search_text as st_mod
from jcodemunch_mcp.tools.index_folder import index_folder
from jcodemunch_mcp.tools.search_text import search_text


def _index(tmp_path, files: dict[str, str]):
    src = tmp_path / "src"
    for rel, text in files.items():
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    store = tmp_path / "store"
    res = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert res["success"] is True, res
    return res["repo"], str(store)


def _files(res) -> set[str]:
    return {g["file"] for g in res["results"]}


@pytest.fixture
def dirs(tmp_path):
    files = {f"pkg/{d}/mod.py": f"def f():\n    return 'needle in {d}'\n" for d in ("a", "b", "c")}
    return _index(tmp_path, files)


# --------------------------------------------------------------------------- braces


def test_a_brace_pattern_matches_the_union_of_its_alternatives(dirs):
    rid, store = dirs
    res = search_text(rid, "needle", file_pattern="pkg/{a,b}/**", storage_path=store)
    assert _files(res) == {"pkg/a/mod.py", "pkg/b/mod.py"}, res
    assert "note" not in res


def test_two_brace_groups_expand_together(dirs):
    rid, store = dirs
    res = search_text(rid, "needle", file_pattern="{pkg,other}/{a,c}/*.py", storage_path=store)
    assert _files(res) == {"pkg/a/mod.py", "pkg/c/mod.py"}, res


def test_a_brace_pattern_also_matches_at_depth_like_any_pattern(dirs):
    rid, store = dirs
    res = search_text(rid, "needle", file_pattern="{a,c}/mod.py", storage_path=store)
    assert _files(res) == {"pkg/a/mod.py", "pkg/c/mod.py"}, res


def test_nested_braces_are_matched_as_written(dirs):
    rid, store = dirs
    res = search_text(rid, "needle", file_pattern="pkg/{a,{b,c}}/**", storage_path=store)
    assert res["result_count"] == 0
    assert "matched no indexed file" in res["note"]
    assert "matched as written" in res["note"], res


def test_too_many_alternatives_are_matched_as_written(dirs):
    rid, store = dirs
    assert st_mod.MAX_BRACE_ALTERNATIVES < 64
    pattern = "pkg/{a,x}{1,2}{3,4}{5,6}{7,8}{9,0}/**"  # 64 alternatives
    res = search_text(rid, "needle", file_pattern=pattern, storage_path=store)
    assert res["result_count"] == 0
    assert "matched as written" in res["note"], res


def test_expansion_is_bounded_and_keeps_the_pattern_as_written():
    expand = st_mod._file_patterns
    assert expand("a/{b,c}/*.py") == ["a/{b,c}/*.py", "a/b/*.py", "a/c/*.py"]
    assert expand("{{cookiecutter.name}}/*.py") == ["{{cookiecutter.name}}/*.py"]
    assert expand("plain/*.py") == ["plain/*.py"]
    assert expand("a/{b,{c,d}}") == ["a/{b,{c,d}}"]
    assert len(expand("{a,b}" * 6)) == 1


def test_a_literal_brace_directory_still_matches(tmp_path):
    rid, store = _index(tmp_path, {"{{cookiecutter.name}}/app.py": "x = 'needle'\n"})
    res = search_text(rid, "needle", file_pattern="{{cookiecutter.name}}/*.py", storage_path=store)
    assert res["result_count"] == 1 and "note" not in res, res


# --------------------------------------------------------------------------- the files a cut page left off


def test_a_cut_page_names_the_files_it_left_off(tmp_path):
    files = {f"m{i}.py": f"def f():\n    return 'needle {i}'\n" for i in range(8)}
    rid, store = _index(tmp_path, files)
    res = search_text(rid, "needle", max_results=3, storage_path=store)
    assert res["truncated"] is True
    shown = _files(res)
    assert len(shown) == 3
    more = {m["file"]: m["matches"] for m in res["more_files"]}
    assert set(more) == set(files) - shown, res
    assert all(n == 1 for n in more.values())
    assert res["more_files_total"] == 5
    assert res["more_files_truncated"] is False
    assert res["more_files_complete"] is True


def test_a_file_cut_partway_counts_its_unshown_matches(tmp_path):
    rid, store = _index(tmp_path, {"big.py": "".join(f"n{i} = 'needle'\n" for i in range(5))})
    res = search_text(rid, "needle", max_results=2, storage_path=store)
    assert res["result_count"] == 2
    assert res["more_files"] == [{"file": "big.py", "matches": 3}], res


def test_shown_plus_unshown_matches_add_up_to_every_match(tmp_path):
    files = {f"m{i}.py": "".join(f"v{j} = 'needle'\n" for j in range(i + 1)) for i in range(6)}
    rid, store = _index(tmp_path, files)
    full = search_text(rid, "needle", max_results=100, storage_path=store)
    cut = search_text(rid, "needle", max_results=4, storage_path=store)
    assert full["truncated"] is False
    assert cut["result_count"] + sum(m["matches"] for m in cut["more_files"]) == full["result_count"]


def test_the_list_is_capped_and_says_so(tmp_path):
    n = st_mod.MORE_FILES_CAP + 6
    files = {f"m{i:02d}.py": "x = 'needle'\n" for i in range(n + 1)}
    rid, store = _index(tmp_path, files)
    res = search_text(rid, "needle", max_results=1, storage_path=store)
    assert len(res["more_files"]) == st_mod.MORE_FILES_CAP
    assert res["more_files_total"] == n
    assert res["more_files_truncated"] is True


def test_the_list_puts_the_files_with_most_matches_first(tmp_path):
    files = {"a.py": "x = 'needle'\n", "b.py": "x = 'needle'\n", "c.py": "".join("y = 'needle'\n" for _ in range(4))}
    rid, store = _index(tmp_path, files)
    res = search_text(rid, "needle", max_results=1, storage_path=store)
    counts = [m["matches"] for m in res["more_files"]]
    assert counts == sorted(counts, reverse=True), res


def test_a_page_that_was_not_cut_carries_no_list(dirs):
    rid, store = dirs
    res = search_text(rid, "needle", storage_path=store)
    assert res["truncated"] is False
    assert not any(k.startswith("more_files") for k in res), res


def test_the_count_stops_at_the_time_budget_and_says_it_is_incomplete(tmp_path):
    files = {f"m{i}.py": "x = 'needle'\n" for i in range(4)}
    rid, store = _index(tmp_path, files)
    import re
    from jcodemunch_mcp.storage import IndexStore
    owner, name = rid.split("/", 1)
    st = IndexStore(base_path=store)
    content_dir = st._content_dir(owner, name)
    counts, complete, _unreadable = st_mod._count_matches(
        st, content_dir, ["m0.py", "m1.py"], re.compile("needle"), None, deadline=0.0,
    )
    assert complete is False and counts == {}


def test_the_compact_encoding_keeps_the_list(tmp_path):
    files = {f"m{i}.py": "x = 'needle'\n" for i in range(5)}
    rid, store = _index(tmp_path, files)
    res = search_text(rid, "needle", max_results=2, storage_path=store)
    payload, _encoding = st_schema.encode("search_text", res)
    decoded = st_schema.decode(payload)
    assert decoded["more_files"] == res["more_files"]
    assert decoded["more_files_total"] == res["more_files_total"]
    assert decoded["more_files_truncated"] is res["more_files_truncated"]
    assert decoded["more_files_complete"] is res["more_files_complete"]
    assert st_schema.ENCODING_ID == "st4" and "st3" in st_schema.LEGACY_ENCODING_IDS
