"""The Grep answer's fixed cost must not grow with the parts of a repo it ignores.

On a Home Assistant index (23,186 files) the hook ran out of its 4 s budget
before it searched, so a large Grep in `homeassistant/` ran unanswered: the
large repos the answer is for were the ones it could not reach. The time was
not the search. Two costs scaled with the whole repo:

- the freshness check stat-ed every indexed file, though the answer covers only
  the files inside the Grep's scope; a changed file outside that scope cannot
  change the answer;
- loading the index walked the tree for tsconfig/jsconfig files to build
  TypeScript import aliases, on an index that holds no such file.
  The walk now reads the index's own file list first and runs only when that
  list names a tsconfig or jsconfig file, or when the list may be incomplete.
"""

from __future__ import annotations

import os
import time

import pytest

from jcodemunch_mcp.cli.hooks import grep_answer
from jcodemunch_mcp.parser import imports as imports_module


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path / "store"))
    monkeypatch.setenv("JCODEMUNCH_TRUSTED_FOLDERS", str(tmp_path))
    proj = tmp_path / "proj"
    (proj / "inside").mkdir(parents=True)
    (proj / "outside").mkdir()
    (proj / "inside" / "a.py").write_text("def a():\n    return 'beacon'\n", encoding="utf-8")
    (proj / "outside" / "b.py").write_text("def b():\n    return 'beacon'\n", encoding="utf-8")
    from jcodemunch_mcp.tools.index_folder import index_folder
    from jcodemunch_mcp.tools.resolve_repo import resolve_repo

    assert index_folder(str(proj), use_ai_summaries=False)["success"]
    return resolve_repo(str(proj))["repo"], proj


def _touch_later(path) -> None:
    later = time.time() + 120
    os.utime(path, (later, later))


def test_a_change_outside_the_scope_does_not_refuse_the_answer(repo):
    name, proj = repo
    _touch_later(proj / "outside" / "b.py")
    assert grep_answer._index_matches_disk(name, "inside/*") is True


def test_a_change_inside_the_scope_still_refuses_it(repo):
    name, proj = repo
    _touch_later(proj / "inside" / "a.py")
    assert grep_answer._index_matches_disk(name, "inside/*") is False


def test_with_no_scope_every_file_is_checked(repo):
    name, proj = repo
    _touch_later(proj / "outside" / "b.py")
    assert grep_answer._index_matches_disk(name) is False


def _clear_alias_cache(root) -> None:
    with imports_module._ALIAS_MAP_LOCK:
        imports_module._alias_map_cache.pop(str(root), None)


def _nested_tsconfig(root) -> None:
    cfg = root / "apps" / "web" / "tsconfig.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('{"compilerOptions": {"paths": {"@web/*": ["src/*"]}}}', encoding="utf-8")


def test_no_tsconfig_in_the_file_list_skips_the_walk(tmp_path, monkeypatch):
    walked = []
    real_iterdir = type(tmp_path).iterdir

    def spy(self):
        walked.append(self)
        return real_iterdir(self)

    _clear_alias_cache(tmp_path)
    monkeypatch.setattr(type(tmp_path), "iterdir", spy)
    assert imports_module._load_tsconfig_aliases(str(tmp_path), known_files=["a.py", "pkg/b.py"]) == {}
    assert walked == []


def test_a_listed_nested_tsconfig_is_still_read(tmp_path):
    _nested_tsconfig(tmp_path)
    _clear_alias_cache(tmp_path)
    got = imports_module._load_tsconfig_aliases(str(tmp_path), known_files=["apps/web/tsconfig.json"])
    assert got == {"@web/*": ["apps/web/src/*"]}


def test_without_a_file_list_the_walk_runs_as_before(tmp_path):
    _nested_tsconfig(tmp_path)
    _clear_alias_cache(tmp_path)
    got = imports_module._load_tsconfig_aliases(str(tmp_path))
    assert got == {"@web/*": ["apps/web/src/*"]}


def _code_index(root, files, **extra):
    from jcodemunch_mcp.storage.index_store import CodeIndex

    return CodeIndex(
        repo="local/x", owner="local", name="x", indexed_at="", source_files=files,
        languages={"python": 1}, symbols=[], source_root=str(root), **extra,
    )


def test_an_index_passes_its_own_file_list(tmp_path, monkeypatch):
    seen = {}

    def spy(source_root, known_files=None):
        seen["known"] = known_files
        return {}

    monkeypatch.setattr(imports_module, "_load_tsconfig_aliases", spy)
    _code_index(tmp_path, ["a.py"], coverage={"walk": "full"})
    assert seen["known"] == ["a.py"]


def test_an_index_with_no_full_walk_does_not_vouch_for_its_file_list(tmp_path, monkeypatch):
    """A `paths=` first index, or one from before coverage was recorded, may omit a tsconfig."""
    seen = {}

    def spy(source_root, known_files=None):
        seen["known"] = known_files
        return {}

    monkeypatch.setattr(imports_module, "_load_tsconfig_aliases", spy)
    _code_index(tmp_path, ["a.py"])
    assert seen["known"] is None


def test_a_capped_index_does_not_vouch_for_its_file_list(tmp_path, monkeypatch):
    """Past the file cap the list is partial, so a tsconfig may exist that it omits."""
    seen = {}

    def spy(source_root, known_files=None):
        seen["known"] = known_files
        return {}

    monkeypatch.setattr(imports_module, "_load_tsconfig_aliases", spy)
    _code_index(tmp_path, ["a.py"], coverage={"walk": "full"}, file_cap_status={"truncated": True})
    assert seen["known"] is None


def test_a_skipped_walk_is_not_cached(tmp_path):
    _clear_alias_cache(tmp_path)
    imports_module._load_tsconfig_aliases(str(tmp_path), known_files=["a.py"])
    assert str(tmp_path) not in imports_module._alias_map_cache


def test_a_tsconfig_added_between_two_indexes_in_one_process_is_read(tmp_path, monkeypatch):
    """The first index holds no tsconfig; the re-index after one is added must read it."""
    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path / "store"))
    monkeypatch.setenv("JCODEMUNCH_TRUSTED_FOLDERS", str(tmp_path))
    proj = tmp_path / "proj"
    (proj / "src").mkdir(parents=True)
    (proj / "src" / "a.ts").write_text("export const a = 1;\n", encoding="utf-8")
    (proj / "src" / "b.ts").write_text(
        "import { a } from '@/a';\nexport const b = a;\n", encoding="utf-8"
    )
    from jcodemunch_mcp.storage import IndexStore
    from jcodemunch_mcp.tools.index_folder import index_folder
    from jcodemunch_mcp.tools.resolve_repo import resolve_repo

    assert index_folder(str(proj), use_ai_summaries=False)["success"]
    owner, _, name = resolve_repo(str(proj))["repo"].partition("/")
    # No load in between: the defect was a walk-skipped `{}` the first run left
    # in the alias cache, which the re-index then read.
    (proj / "src" / "tsconfig.json").write_text(
        '{"compilerOptions": {"paths": {"@/*": ["*"]}}}', encoding="utf-8"
    )
    assert index_folder(str(proj), use_ai_summaries=False)["success"]
    index = IndexStore().load_index(owner, name)
    assert "src/tsconfig.json" in index.source_files
    assert index.alias_map == {"@/*": ["src/*"]}


def test_a_missing_file_reads_as_no_mtime(tmp_path):
    from jcodemunch_mcp.retrieval.freshness import FreshnessProbe

    (tmp_path / "here.py").write_text("x = 1\n", encoding="utf-8")
    probe = FreshnessProbe(source_root=str(tmp_path), indexed_at="", index_sha=None, file_mtimes={})
    assert probe._file_mtime("gone.py") is None
    assert probe._file_mtime("here.py") == pytest.approx((tmp_path / "here.py").stat().st_mtime)
