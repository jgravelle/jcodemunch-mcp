"""The watcher's directory census prunes exactly what discovery prunes (#1163, @yagiii1344).

Under WSL the watcher polls, and it polls every directory `_watch_directories`
returns. That census pruned only the skip-name list, while discovery prunes
seven kinds of directory before it descends. So the census stat-polled trees
discovery never indexes: a vcpkg checkout, CACHEDIR-tagged caches, virtualenv
`site-packages`. The reporter measured 96,191 polled entries across 23 repos,
77% of them in four gitignored trees, and the watcher used ~76% of a core.

Neither walk pruned gitignored DIRECTORIES: discovery descended into them and
dropped their files one at a time. Git never re-includes anything below an
ignored directory, so both walks now prune it, and the census and discovery
ask the same rule.

The two properties held here, in both directions:
- the census is never STRICTER than discovery: every directory holding an
  indexed file is watched, or an edit there is never seen;
- the census is never LOOSER: no directory discovery prunes is watched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jcodemunch_mcp.security import CACHEDIR_TAG_FILENAME, CACHEDIR_TAG_SIGNATURE
from jcodemunch_mcp.tools.index_folder import discover_local_files
from jcodemunch_mcp.watcher import _watch_directories


def _write(root: Path, rel: str, text: str = "x = 1\n") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = (tmp_path / "repo").resolve()
    _write(root, "src/app/main.py")
    _write(root, "src/app/util.py")
    # A gitignored tree, the reporter's largest (vcpkg/ and .cache/).
    _write(root, ".gitignore", "vcpkg/\n.cache/\n*.log\n")
    _write(root, "vcpkg/ports/zlib/portfile.py")
    _write(root, "vcpkg/ports/zlib/deep/a/b/c.py")
    _write(root, ".cache/pkg/x.py")
    # A file ignored by name inside a kept directory: the DIRECTORY stays.
    _write(root, "src/app/debug.log", "log\n")
    # A nested .gitignore that ignores a subdirectory of its own directory.
    _write(root, "tools/.gitignore", "out/\n")
    _write(root, "tools/build.py")
    _write(root, "tools/out/gen.py")
    # A directory that declares itself a cache.
    (root / "tagged").mkdir()
    (root / "tagged" / CACHEDIR_TAG_FILENAME).write_bytes(
        CACHEDIR_TAG_SIGNATURE + b"\n"
    )
    _write(root, "tagged/blob.py")
    # A virtualenv's installed packages.
    _write(root, "venv1/pyvenv.cfg", "home = /usr/bin\n")
    _write(root, "venv1/lib/python3.12/site-packages/pkg/mod.py")
    return root


def _census(root: Path) -> set[Path]:
    return {Path(d).resolve() for d in _watch_directories(str(root))}


def _indexed_dirs(root: Path) -> set[Path]:
    files, _warnings, _counts = discover_local_files(root)
    return {Path(f).resolve().parent for f in files}


def test_every_directory_holding_an_indexed_file_is_watched(repo):
    indexed = _indexed_dirs(repo)
    assert indexed, "the fixture must index something"
    missing = indexed - _census(repo)
    assert not missing, sorted(str(p) for p in missing)


@pytest.mark.parametrize(
    "pruned",
    [
        "vcpkg",
        "vcpkg/ports/zlib/deep",
        ".cache",
        "tools/out",
        "tagged",
        "venv1/lib/python3.12/site-packages",
    ],
)
def test_a_tree_discovery_prunes_is_not_watched(repo, pruned):
    census = _census(repo)
    target = (repo / pruned).resolve()
    assert target not in census, pruned
    assert not any(target in d.parents for d in census), pruned


def test_a_kept_directory_with_an_ignored_file_is_still_watched(repo):
    assert (repo / "src" / "app").resolve() in _census(repo)
    assert (repo / "tools").resolve() in _census(repo)


def test_discovery_indexes_nothing_below_an_ignored_directory(repo):
    files, _w, counts = discover_local_files(repo)
    rels = {Path(f).resolve().relative_to(repo).as_posix() for f in files}
    assert not any(r.startswith(("vcpkg/", ".cache/", "tools/out/")) for r in rels), (
        rels
    )
    assert {"src/app/main.py", "src/app/util.py", "tools/build.py"} <= rels
    assert counts.get("gitignored_dir", 0) >= 3, counts


@pytest.mark.parametrize(
    "rules, keep",
    [
        # pathspec's last-match rule re-includes these; so did main's walk.
        (["outdir/**", "!outdir/keep.py"], "outdir/keep.py"),
        (["outdir/", "!outdir/keep.py"], "outdir/keep.py"),
        (["*", "!*/", "!*.py", "!.gitignore"], "outdir/keep.py"),
    ],
)
def test_a_negation_below_an_ignored_directory_keeps_its_file_and_its_watch(
    tmp_path, rules, keep
):
    """Review of #1163: pruning on a directory match dropped files a `!`
    pattern below it re-includes, which the per-file verdict (and so
    `index_file` and the watcher fast path) keeps. A directory is pruned only
    when no spec reaching it holds a negation, so these trees are walked and
    the file stays indexed AND watched. `thirdp`-style names, never `vendor`,
    which the skip list prunes before this rule is reached."""
    root = (tmp_path / "neg").resolve()
    _write(root, ".gitignore", "".join(line + chr(10) for line in rules))
    _write(root, "outdir/keep.py")
    _write(root, "outdir/drop.txt", "x" + chr(10))
    _write(root, "main.py")
    files, _w, _c = discover_local_files(root)
    rels = {Path(f).resolve().relative_to(root).as_posix() for f in files}
    assert keep in rels and "main.py" in rels, rels
    assert (root / "outdir").resolve() in _census(root)
    from jcodemunch_mcp.tools.index_folder import (
        _should_index_file,
        _walk_filter_config,
    )

    cfg = _walk_filter_config(root, per_file=True)
    specs = []
    from jcodemunch_mcp.tools.index_folder import _load_dir_gitignore

    _load_dir_gitignore(str(root), [".gitignore"], specs)
    ok, _reason, _rel, _warn = _should_index_file(root / keep, cfg, specs)
    assert ok, "the per-file verdict and the walk must agree"


@pytest.mark.parametrize("pattern", ["thirdp/*", "thirdp/*/*.log"])
def test_a_pattern_that_matches_a_directory_but_not_its_files_prunes_nothing(
    tmp_path, pattern
):
    """Review of #1163, round 2: `x/*` matches `x/sub/` and not `x/sub/a.py`,
    with no negation anywhere. Pruning on the directory match dropped a file
    the per-file verdict keeps; the descendant probes keep the tree walked."""
    root = (tmp_path / "star").resolve()
    _write(root, ".gitignore", pattern + chr(10))
    _write(root, "thirdp/sub/a.py")
    _write(root, "main.py")
    files, _w, _c = discover_local_files(root)
    rels = {Path(f).resolve().relative_to(root).as_posix() for f in files}
    assert "thirdp/sub/a.py" in rels, rels
    assert (root / "thirdp" / "sub").resolve() in _census(root)
    from jcodemunch_mcp.tools.index_folder import (
        _load_dir_gitignore,
        _should_index_file,
        _walk_filter_config,
    )

    specs: list = []
    _load_dir_gitignore(str(root), [".gitignore"], specs)
    ok, _r, _rel, _w2 = _should_index_file(
        root / "thirdp" / "sub" / "a.py",
        _walk_filter_config(root, per_file=True),
        specs,
    )
    assert ok, "the per-file verdict and the walk must agree"


def test_an_ignored_directory_with_no_negation_is_pruned(tmp_path):
    root = (tmp_path / "plain").resolve()
    _write(root, ".gitignore", "thirdp/" + chr(10))
    _write(root, "thirdp/a.py")
    _write(root, "main.py")
    files, _w, counts = discover_local_files(root)
    rels = {Path(f).resolve().relative_to(root).as_posix() for f in files}
    assert rels == {"main.py"}, rels
    assert counts["gitignored_dir"] == 1, counts
    assert (root / "thirdp").resolve() not in _census(root)


def test_project_extra_ignore_patterns_prune_the_census(tmp_path):
    """`extra_ignore_patterns` in the project's .jcodemunch.jsonc reached
    discovery and not the census, so it could not be used to stop the polling."""
    root = (tmp_path / "extra").resolve()
    _write(root, "main.py")
    _write(root, "generated/big/a.py")
    _write(root, ".jcodemunch.jsonc", '{"extra_ignore_patterns": ["generated/"]}\n')
    files, _w, _c = discover_local_files(root)
    rels = {Path(f).resolve().relative_to(root).as_posix() for f in files}
    assert "main.py" in rels and not any(r.startswith("generated/") for r in rels), rels
    census = _census(root)
    assert (root / "generated").resolve() not in census
    assert (root / "generated" / "big").resolve() not in census


def test_the_new_directory_reasons_define_the_corpus_and_withhold_nothing():
    """A pruned ignored directory is the corpus being defined, like `gitignore`,
    so an absence claim over the rest stays citable (v1.108.193's contract)."""
    from jcodemunch_mcp.tools.index_folder import WITHHELD_SKIP_REASONS

    assert not {"gitignored_dir", "extra_ignore_dir"} & WITHHELD_SKIP_REASONS
