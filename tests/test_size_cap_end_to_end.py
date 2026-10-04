"""The size cap, tested where the USER stands.

This file exists because the cap took THREE releases to fix and every one of them
passed its own tests:

- v1.108.193 gave the cap a config key and proved the RESOLVER returns the right
  number.
- v1.108.194 made every walk resolve on entry and proved the WALK CALLS the
  resolver.
- v1.108.197 made the resolver read project config and proved the RESOLVER TAKES
  A REPO.

Each fix was real. After each one, a person who set `max_file_size` and ran an
index still could not see their file. Nobody had asked the user's question.

So these tests ask only that question, at the altitude a user occupies:

    set the cap by <a documented route>, index a folder holding an oversize
    source file, and assert the symbol inside it is RETRIEVABLE.

No resolver is inspected, no call site is grepped, no argument is asserted. The
matrix is (every documented route) x (every discovery entry point), because the
defect in .194 was a route that worked on one walk and not another, and the
defect in .197 was a route that worked from one config file and not another. A
single passing cell has never been evidence for this feature.

Deliberately NOT parametrized over the internal `max_size=` argument: no user
has one. Adding it would grow the matrix while lowering its altitude, which is
the mistake this file is named after.
"""
from __future__ import annotations

import pytest

from jcodemunch_mcp import config as config_module
from jcodemunch_mcp.security import DEFAULT_MAX_FILE_SIZE
from jcodemunch_mcp.tools.index_folder import index_folder

# Just over the 500 KB default. Deliberately only just: the parse is real work
# and this file runs its matrix seven times.
OVERSIZE = DEFAULT_MAX_FILE_SIZE + 5_000
GENEROUS_CAP = OVERSIZE + 10_000


def _make_project(tmp_path):
    """A folder with one oversize source file and one ordinary one.

    The small companion is what makes the default-cap case meaningful. With one
    file and the default cap the walk finds nothing at all, and "no source files
    found" is indistinguishable from a walk that broke for an unrelated reason.
    With two, the cap has to DISCRIMINATE: the small file is indexed and the
    large one is not, which is the actual guarantee.
    """
    project = tmp_path / "project"
    project.mkdir()
    # The padding OPENS the file. tree-sitter's Python grammar is quadratic in
    # a run of comment lines that follows any statement (LEDGER L-113): with the
    # function first this file took about 11 s to parse
    # (`evidence/l113_shapes.txt`), and this module is about the cap, not that.
    padding = "# " + ("x" * 78) + "\n"
    (project / "big_module.py").write_text(
        padding * (OVERSIZE // len(padding) + 1)
        + "\ndef marker_symbol():\n    return 1\n",
        encoding="utf-8",
    )
    (project / "small_helper.py").write_text(
        "def companion_symbol():\n    return 2\n", encoding="utf-8"
    )
    assert (project / "big_module.py").stat().st_size > DEFAULT_MAX_FILE_SIZE
    assert (project / "small_helper.py").stat().st_size < DEFAULT_MAX_FILE_SIZE
    return project


def _index(project, tmp_path, paths=None):
    return index_folder(
        str(project),
        use_ai_summaries=False,
        storage_path=str(tmp_path / "store"),
        incremental=False,
        context_providers=False,
        paths=paths,
    )


def _is_retrievable(repo, symbol_name, tmp_path):
    """Can a user find this symbol by searching for it?

    `search_symbols` matches on the name rather than a substring of it (a query
    of "symbol" finds neither of these), and returns its rows under `results`.
    Both of those are easy to get wrong from memory, so this helper exists once.
    """
    from jcodemunch_mcp.tools.search_symbols import search_symbols

    found = search_symbols(
        repo,
        symbol_name,
        storage_path=str(tmp_path / "store"),
        max_results=50,
    )
    return symbol_name in [r.get("name") for r in (found.get("results") or [])]


ROUTES = ["env_var", "global_config", "project_config"]


@pytest.fixture
def route(request, monkeypatch, tmp_path):
    """Set the cap the way the docs say to, then undo it."""
    which = request.param

    # ⚠ Pin storage BEFORE any load_config() below (#437). A bare load_config()
    # resolves to CODE_INDEX_PATH or ~/.code-index/config.jsonc, so it reads the
    # DEVELOPER's real config straight past conftest's _reset_global_config, and
    # auto-creates the file when it is missing. Pinned for every route, not just
    # env_var, so no future branch can reopen it.
    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path / "storage"))

    project = _make_project(tmp_path)

    if which == "env_var":
        # Documented in the env table. Read at load_config() time, so a bare
        # os.environ poke without a reload proves nothing. That is exactly what
        # made @dkiaulakis conclude the env route was dead when it was not.
        monkeypatch.setenv("JCODEMUNCH_MAX_FILE_SIZE", str(GENEROUS_CAP))
        config_module.load_config()
    elif which == "global_config":
        monkeypatch.setitem(
            config_module._GLOBAL_CONFIG, "max_file_size", GENEROUS_CAP
        )
    elif which == "project_config":
        # The route the config template points at, and the one still broken
        # after BOTH .193 and .194 shipped claiming to have fixed the cap.
        (project / ".jcodemunch.jsonc").write_text(
            '{"max_file_size": %d}' % GENEROUS_CAP, encoding="utf-8"
        )
    else:  # pragma: no cover
        raise AssertionError(f"unknown route {which}")

    yield project

    config_module.invalidate_project_config_cache(str(project))
    if which == "env_var":
        monkeypatch.delenv("JCODEMUNCH_MAX_FILE_SIZE", raising=False)
        config_module.load_config()


@pytest.mark.parametrize("route", ROUTES, indirect=True)
@pytest.mark.parametrize(
    "paths",
    [None, ["big_module.py", "small_helper.py"]],
    ids=["full-walk", "explicit-paths"],
)
def test_a_raised_cap_indexes_and_retrieves_the_oversize_file(
    route, tmp_path, paths
):
    """The user's question, end to end: I raised the cap, can I find my code?

    Retrievability is the claim, not `file_count`. A count of 2 would still pass
    if the file were counted and its symbols dropped, and a user who cannot find
    `marker_symbol` does not care which of those happened.
    """
    result = _index(route, tmp_path, paths=paths)

    assert result["success"] is True, result
    assert result["file_count"] == 2, result
    skips = result.get("discovery_skip_counts") or {}
    assert not skips.get("too_large"), (
        "the file was refused as too_large even though the cap was raised by a "
        f"documented route; skip_counts={skips}"
    )

    assert _is_retrievable(result["repo"], "marker_symbol", tmp_path), (
        "the oversize file was counted as indexed but its symbol is not "
        "retrievable, so a user searching for it still finds nothing"
    )


def test_the_default_refuses_only_the_oversize_file(tmp_path):
    """Non-vacuity, and the guarantee itself.

    Without this, every assertion above could pass because the cap stopped being
    enforced at all, which is a worse bug than the one this file is named after.
    The default is NOT raised: 500 KB protects the common case from a parse that
    costs more than the file is worth.
    """
    project = _make_project(tmp_path)
    result = _index(project, tmp_path)

    assert result["success"] is True, result
    assert result["file_count"] == 1, (
        "with the default cap the oversize file should be refused and the small "
        f"one indexed; got file_count={result.get('file_count')}"
    )

    assert _is_retrievable(result["repo"], "companion_symbol", tmp_path), (
        "the small file was refused too, so this proves nothing about the cap"
    )
    assert not _is_retrievable(result["repo"], "marker_symbol", tmp_path), (
        "an oversize file was indexed with NO cap raised; the 500 KB default "
        "is not being enforced"
    )


def test_the_oversize_fixture_opens_with_its_padding(tmp_path):
    """Every comment line of the oversize fixture comes before its first statement.

    A comment run after any statement parses in quadratic time (LEDGER L-113):
    with the function first, this file took about 11 s to parse and six cases of
    this module took 14 to 16 s each, which spent the full tier's wall-clock
    margin (harness F-41). Only a run that opens the file is free, so that is
    the shape pinned here, for indented comments as well as column-0 ones.
    """
    text = (_make_project(tmp_path) / "big_module.py").read_text(encoding="utf-8")
    lines = text.splitlines()
    comments = [i for i, line in enumerate(lines) if line.lstrip().startswith("#")]
    code = [i for i, line in enumerate(lines) if line.strip() and not line.lstrip().startswith("#")]
    assert comments and code, "the fixture needs its padding and its function"
    assert max(comments) < min(code), (
        f"a comment on line {max(comments) + 1} follows code that starts on line {min(code) + 1}"
    )
