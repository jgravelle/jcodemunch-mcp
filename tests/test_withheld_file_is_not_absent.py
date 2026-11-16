"""A file the index withheld is not answered as a file that does not exist (#1396, @dkiaulakis).

`index_folder` refuses a file over `max_file_size` during discovery and keeps
only a COUNT of such files (`coverage.withheld.too_large`). The file tools then
had nothing to tell a withheld path from a path that never existed, and gave
both the same answer: no symbols, verdict `absent`, and the note "treat this as
strong evidence the target is not present". An agent told to stop looking for
a file that is real, current and wanted goes looking for an older copy instead.

Now the coverage record names the withheld files (capped, with a total and a
`truncated` flag), and the file tools answer a withheld path with what was
withheld and why, in the response body as well as the verdict, because
`meta_fields: []` (the default) strips `_meta`. A listed file is confirmed on
disk before it is answered for, so a deleted one reads absent. A path the record
does not list is never looked up on disk (a lookup would confirm that `.env` or
a gitignored file exists, with its size): when the index withheld files it does
not name, the answer is that absence is not proven, and nothing more.
"""

import json
import subprocess

import pytest

from jcodemunch_mcp.storage import IndexStore
from jcodemunch_mcp.tools.get_file_content import get_file_content
from jcodemunch_mcp.tools.get_file_outline import get_file_outline
from jcodemunch_mcp.tools.index_folder import index_folder

NEWLINE = chr(10)
LIMIT = 2000


def _big(n_funcs: int = 200) -> str:
    return "".join(f"function f{i}() {{ return {i}; }}" + NEWLINE for i in range(n_funcs))


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    (root / "sub").mkdir(parents=True)
    (root / "big.js").write_text(_big(), encoding="utf-8")
    (root / "sub" / "big2.js").write_text(_big(150), encoding="utf-8")
    (root / "sub" / "ok.js").write_text("function ok() { return 2; }" + NEWLINE, encoding="utf-8")
    (root / "small.js").write_text("function s() { return 1; }" + NEWLINE, encoding="utf-8")
    # A git root, so a walk of `sub` merges into this index instead of making
    # a second one (which would leave the record untouched and the subdir
    # tests below vacuous).
    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(git + ["add", "."], cwd=root, check=True)
    subprocess.run(git + ["commit", "-qm", "init"], cwd=root, check=True)
    store = tmp_path / "store"
    store.mkdir()
    out = index_folder(path=str(root), use_ai_summaries=False, storage_path=str(store), max_size=LIMIT)
    assert out.get("success"), out
    return {"root": root, "store": str(store), "repo_id": out["repo"]}


def _outline(repo, path):
    return get_file_outline(repo["repo_id"], path, storage_path=repo["store"])


def _content(repo, path):
    return get_file_content(repo["repo_id"], path, storage_path=repo["store"])


def _verdict(resp):
    return (resp.get("_meta") or {}).get("verdict") or {}


def test_the_withheld_file_and_a_missing_path_get_different_answers(repo):
    """The reported shape: byte-identical outlines once the path-derived fields are set aside."""
    assert (repo["root"] / "big.js").stat().st_size > LIMIT

    def shape(resp):
        r = {k: v for k, v in resp.items() if k not in ("file", "_meta")}
        v = dict(_verdict(resp))
        v.pop("did_you_mean", None)
        return json.dumps({"body": r, "state": v.get("state"), "note": v.get("note")}, sort_keys=True, default=str)

    assert shape(_outline(repo, "big.js")) != shape(_outline(repo, "does-not-exist.js"))


def test_the_outline_names_what_was_withheld_in_the_body(repo):
    out = _outline(repo, "big.js")
    w = out.get("withheld")
    assert w, out
    assert w["reason"] == "too_large"
    assert _verdict(out).get("state") == "degraded"
    assert w["size"] == (repo["root"] / "big.js").stat().st_size
    assert w["limit"] == LIMIT
    assert "max_file_size" in w.get("remedy", "")


def test_the_verdict_does_not_claim_absence_for_a_withheld_file(repo):
    v = _verdict(_outline(repo, "big.js"))
    assert v.get("state") != "absent", v
    assert "strong evidence" not in v.get("note", "")


def test_file_content_says_withheld_not_not_found(repo):
    out = _content(repo, "big.js")
    assert out.get("withheld", {}).get("reason") == "too_large", out
    assert "max_file_size" in out.get("error", "")
    assert _verdict(out).get("state") != "absent"


def test_a_missing_path_is_still_absent(repo):
    out = _outline(repo, "does-not-exist.js")
    assert "withheld" not in out
    assert _verdict(out).get("state") == "absent"


def test_a_path_outside_the_root_is_not_looked_up_on_disk(repo):
    (repo["root"].parent / "outside.js").write_text("x", encoding="utf-8")
    out = _outline(repo, "../outside.js")
    assert "withheld" not in out
    assert _verdict(out).get("state") == "absent"


def test_a_withheld_file_in_a_subdirectory_is_named_with_its_stored_path(repo):
    w = _outline(repo, "sub/big2.js").get("withheld")
    assert w and w["reason"] == "too_large"


def test_a_file_that_shrinks_under_the_limit_is_indexed_and_leaves_the_record(repo):
    (repo["root"] / "big.js").write_text("function now_small() { return 1; }" + NEWLINE, encoding="utf-8")
    out = index_folder(path=str(repo["root"]), use_ai_summaries=False, storage_path=repo["store"], max_size=LIMIT)
    assert out.get("success"), out
    o = _outline(repo, "big.js")
    assert "withheld" not in o
    assert [s["name"] for s in o["symbols"]] == ["now_small"]
    assert _outline(repo, "sub/big2.js").get("withheld"), "the other withheld file left the record"


def test_a_subdir_walk_keeps_the_record_for_files_outside_it(repo):
    out = index_folder(path=str(repo["root"] / "sub"), use_ai_summaries=False, storage_path=repo["store"], max_size=LIMIT)
    assert out.get("success"), out
    assert out["repo"] == repo["repo_id"], "the subdir walk made a second index"
    assert _outline(repo, "big.js").get("withheld"), "a subdir walk erased a withheld file outside it"
    assert _outline(repo, "sub/big2.js").get("withheld")


def test_a_capped_record_says_so_and_unlisted_paths_are_not_proven_absent(tmp_path, monkeypatch):
    from jcodemunch_mcp.retrieval import verdict as verdict_mod

    monkeypatch.setattr(verdict_mod, "WITHHELD_FILES_MAX", 1)
    root = tmp_path / "r"
    root.mkdir()
    for name in ("a.js", "b.js", "c.js"):
        (root / name).write_text(_big(), encoding="utf-8")
    (root / "ok.js").write_text("function ok() {}" + NEWLINE, encoding="utf-8")
    store = tmp_path / "s"
    store.mkdir()
    out = index_folder(path=str(root), use_ai_summaries=False, storage_path=str(store), max_size=LIMIT)
    assert out.get("success"), out
    owner, name = out["repo"].split("/", 1)
    cov = IndexStore(base_path=str(store)).load_index(owner, name).coverage
    assert cov["withheld_files_total"] == 3
    assert cov["withheld_files_truncated"] is True
    assert len(cov["withheld_files"]) == 1
    listed = next(iter(cov["withheld_files"]))
    for f in ("a.js", "b.js", "c.js"):
        resp = get_file_outline(out["repo"], f, storage_path=str(store))
        w = resp.get("withheld") or {}
        assert w.get("reason") == ("too_large" if f == listed else "withheld_unnamed"), (f, resp)
        assert _verdict(resp).get("state") == "degraded"
    # Unnamed means unnamed: a path that never existed gets the same hedge,
    # because the index cannot rule it out, and nothing is looked up on disk.
    ghost = get_file_outline(out["repo"], "ghost.js", storage_path=str(store))
    assert (ghost.get("withheld") or {}).get("reason") == "withheld_unnamed"
    assert "size" not in ghost["withheld"]
    # A typo keeps its near-miss suggestion under the hedge.
    typo = get_file_outline(out["repo"], "okk.js", storage_path=str(store))
    assert "ok.js" in (_verdict(typo).get("did_you_mean") or []), typo
    # No withheld file can be outside the root, so those paths stay absent.
    for f in ("../outside.js", "/etc/passwd", "C:/Windows/win.ini", "sub/../../x.js"):
        resp = get_file_outline(out["repo"], f, storage_path=str(store))
        assert "withheld" not in resp, (f, resp)
        assert _verdict(resp).get("state") == "absent", f


def test_an_index_without_the_record_still_does_not_call_a_real_file_absent(repo):
    """An index built before the record existed: the disk says the file is there."""
    owner, name = repo["repo_id"].split("/", 1)
    st = IndexStore(base_path=repo["store"])
    cov = dict(st._sqlite.get_coverage(owner, name) or {})
    for k in ("withheld_files", "withheld_files_total", "withheld_files_truncated", "max_file_size"):
        cov.pop(k, None)
    st._sqlite.set_coverage(owner, name, cov)
    out = _outline(repo, "big.js")
    w = out.get("withheld") or {}
    assert w.get("reason") == "withheld_unnamed", out
    assert w.get("withheld_unnamed") == 2
    assert "size" not in w
    assert _verdict(out).get("state") == "degraded"


def test_a_deleted_withheld_file_reads_absent(repo):
    (repo["root"] / "big.js").unlink()
    out = _outline(repo, "big.js")
    assert "withheld" not in out, out
    assert _verdict(out).get("state") == "absent"


def test_excluded_files_on_disk_are_never_confirmed(tmp_path):
    """Secret, skip-dir and extension exclusions stay `absent`: no existence or size leaks."""
    root = tmp_path / "x"
    (root / "node_modules" / "pkg").mkdir(parents=True)
    (root / ".env").write_text("TOKEN=" + "a" * 5000 + NEWLINE, encoding="utf-8")
    (root / "credentials.json").write_text("{}" + " " * 5000, encoding="utf-8")
    (root / "node_modules" / "pkg" / "i.js").write_text(_big(), encoding="utf-8")
    (root / "app.js").write_text("function app() {}" + NEWLINE, encoding="utf-8")
    store = tmp_path / "xs"
    store.mkdir()
    out = index_folder(path=str(root), use_ai_summaries=False, storage_path=str(store), max_size=LIMIT)
    assert out.get("success"), out
    for f in (".env", "credentials.json", "node_modules/pkg/i.js"):
        resp = get_file_outline(out["repo"], f, storage_path=str(store))
        assert "withheld" not in resp, (f, resp)
        assert _verdict(resp).get("state") == "absent", f
        content = get_file_content(out["repo"], f, storage_path=str(store))
        assert content.get("error") == f"File not found: {f}", content


@pytest.mark.parametrize("path", ["../outside.js", "/etc/passwd", "C:/Windows/win.ini", "//server/share/x.js"])
def test_paths_that_leave_the_root_stay_absent(repo, path):
    out = _outline(repo, path)
    assert "withheld" not in out
    assert _verdict(out).get("state") == "absent"


def test_a_paths_run_leaves_the_record_as_it_was(repo):
    (repo["root"] / "small.js").write_text("function s2() { return 2; }" + NEWLINE, encoding="utf-8")
    out = index_folder(
        path=str(repo["root"]), use_ai_summaries=False, storage_path=repo["store"],
        max_size=LIMIT, paths=["small.js"],
    )
    assert out.get("success"), out
    assert _outline(repo, "big.js").get("withheld", {}).get("reason") == "too_large"
    assert _outline(repo, "sub/big2.js").get("withheld", {}).get("reason") == "too_large"


def test_each_entry_keeps_the_limit_it_was_withheld_under(repo):
    out = index_folder(path=str(repo["root"] / "sub"), use_ai_summaries=False, storage_path=repo["store"], max_size=LIMIT + 1000)
    assert out.get("success"), out
    assert out["repo"] == repo["repo_id"], "the subdir walk made a second index"
    assert _outline(repo, "big.js")["withheld"]["limit"] == LIMIT
    assert _outline(repo, "sub/big2.js")["withheld"]["limit"] == LIMIT + 1000
