"""The `config` reporter must show every indexing limit it honours (#375).

v1.108.193 gave the per-file cap a config key (`max_file_size`) and an env var
(`JCODEMUNCH_MAX_FILE_SIZE`), and the resolver honoured both. The `config`
command did not print it. Its two siblings were listed, so the omission read as
"this limit does not exist" rather than "this limit is not shown", and the only
way to read the effective value was to import `security` and call the resolver
by hand — which is what @dkiaulakis did, concluding from a bare REPL (no
`load_config()`) that the env route was dead. It was not; it was invisible.

The guard below is deliberately written against the SET of limits rather than
against `max_file_size` alone: the defect class is "a limit gains a route and
the reporter is not updated", and pinning one key would not catch the next one.
"""

from __future__ import annotations

import json as _json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

SRC = str(Path(__file__).resolve().parents[1] / "src")

# Every limit the indexer resolves from config. Adding a resolver here without
# adding a `row(...)` to the reporter is the bug this file exists to catch.
INDEXING_LIMITS = ("max_file_size", "max_folder_files", "max_index_files")

BIG_BODY = "// x\n" + ("a" * 700_000)
TOUCHED_BODY = "// touched\n" + ("a" * 700_000)


def _run_config(env_extra: dict[str, str] | None = None) -> str:
    env = {**os.environ, "PYTHONPATH": SRC}
    env.pop("JCODEMUNCH_MAX_FILE_SIZE", None)
    if env_extra:
        env.update(env_extra)
    # Isolate the config FILE too, not just the env var: without this the
    # subprocess reads the developer's real ~/.code-index/config.jsonc, and
    # both max_file_size assertions below fail on any box that sets the key.
    # TemporaryDirectory rather than mkdtemp (used elsewhere in this file)
    # because the config reporter WRITES a config.jsonc into the directory it
    # is pointed at, so an uncleaned dir per call accumulates.
    # ⚠⚠ Isolate the PROJECT config as well, by running somewhere that has no
    # `.jcodemunch.jsonc`. `config` loads one from the CWD, and the CWD under
    # pytest is the repo root -- so the moment this repo gained a project
    # config of its own (#566: raising `max_file_size` so `server.py` enters
    # the index), `max_file_size` reported `768000 [project]` and both
    # assertions below failed.
    #
    # ⚠ The global-config isolation above was added for the SAME reason one
    # source over, and its comment reasons about "any box that sets the key" --
    # it just did not ask which config files exist. **A user whose own repo
    # carries a `.jcodemunch.jsonc` would have hit this identically**, which is
    # the #437 shape: a test that passes on two machines and fails on a third.
    with tempfile.TemporaryDirectory(prefix="jcm-config-isolation-") as cfg_dir:
        env["CODE_INDEX_PATH"] = cfg_dir
        proc = subprocess.run(
            [sys.executable, "-m", "jcodemunch_mcp", "config"],
            capture_output=True,
            text=True,
            env=env,
            cwd=cfg_dir,
            timeout=120,
        )
    return proc.stdout


@pytest.mark.parametrize("key", INDEXING_LIMITS)
def test_config_reports_every_indexing_limit(key):
    """Each limit the walk honours is visible in `config`."""
    out = _run_config()
    assert re.search(rf"^\s*{re.escape(key)}\s", out, re.M), (
        f"`jcodemunch-mcp config` does not report {key!r}. A limit the indexer "
        "honours but the reporter hides cannot be diagnosed without calling the "
        "resolver by hand; that is the #375 failure mode."
    )


def test_config_reports_max_file_size_default():
    out = _run_config()
    m = re.search(r"^\s*max_file_size\s+(\S+)\s+\((default)\)", out, re.M)
    assert m, f"max_file_size default row missing from:\n{out}"
    assert m.group(1) == "512000", (
        "v1.108.193 shipped the escape hatch with the DEFAULT deliberately "
        f"unchanged at 512000; reporter shows {m.group(1)}."
    )


def test_config_attributes_an_env_override_to_env():
    """The value AND its provenance: 'where did this come from' is the question."""
    out = _run_config({"JCODEMUNCH_MAX_FILE_SIZE": "2000000"})
    assert re.search(r"^\s*max_file_size\s+2000000\s+\[env\]", out, re.M), (
        "An env override must report both the resolved value and `[env]` as its "
        "source, or the reporter cannot settle a 'my setting is ignored' "
        f"report. Got:\n{out}"
    )


# ── forced_paths parity between the full walk and the incremental fast path ──
#
# The full walk exempts package.json entry points from the size cap (#25). The
# watcher/incremental fast path passed `forced_paths=set()`, so the SAME file was
# indexed by one path and dropped as `too_large` by the other: it appeared after
# a full index and vanished on the next incremental touch. Surfaced indirectly by
# @dkiaulakis, who hit the cap on `tools/eidos`, added a package.json naming the
# oversized file, and verified the predicate — against the full-walk config only.


def _oversize_tree(base: Path):
    d = base / "tree"
    d.mkdir()
    big = d / "huge.js"
    big.write_text(BIG_BODY, encoding="utf-8")
    (d / "package.json").write_text(
        _json.dumps({"name": "p", "bin": {"p": "huge.js"}}), encoding="utf-8"
    )
    return d, big


def test_package_json_exemption_survives_an_incremental_reindex(tmp_path):
    """Drive the REAL fast path: full index, then an incremental touch.

    An earlier draft of this test compared two calls built from the same
    forced_paths value, which is true by construction and proves nothing. This
    one indexes for real and asserts the oversize entry point is not dropped
    once the incremental path has handled it.
    """
    from jcodemunch_mcp.tools.index_folder import index_folder

    d, big = _oversize_tree(tmp_path)
    store_dir = str(tmp_path / "store")

    full = index_folder(str(d), use_ai_summaries=False, storage_path=store_dir)
    assert full.get("success"), full
    assert "huge.js" in _json.dumps(full.get("files", [])), (
        "full walk dropped the manifest-exempt file; fixture or #25 is broken"
    )

    big.write_text(TOUCHED_BODY, encoding="utf-8")
    inc = index_folder(
        str(d),
        use_ai_summaries=False,
        storage_path=store_dir,
        incremental=True,
        changed_paths=[("modified", str(big))],
    )
    assert inc.get("success"), inc

    # `changed` is the discriminator, NOT discovery_skip_counts: the incremental
    # result carries no skip counts, so asserting on them passed with the bug
    # present and proved nothing. With the exemption reaching this path the file
    # is re-indexed (changed == 1); without it the file is filtered out before
    # it is ever compared, and the call reports changed == 0.
    if inc.get("changed") != 1:
        # Self-diagnosing failure. This assertion failed twice on windows-latest
        # while passing on every ubuntu job and on a local Windows box, and two
        # rounds of reasoning from the result dict alone produced one real bug
        # (the normcase comparison, v1.108.195) that turned out NOT to be this
        # one. A failure message that does not say WHY the filter rejected the
        # file cannot be diagnosed remotely, so compute the reason here.
        import os

        from jcodemunch_mcp.tools.index_folder import (
            _build_index_filters,
            _scan_package_json_forced_paths,
            _should_index_file,
            get_max_file_size,
        )

        forced = _scan_package_json_forced_paths(d.resolve())
        cfg = _build_index_filters(
            root=d.resolve(), follow_symlinks=False,
            max_size=get_max_file_size(), extra_spec=None,
            forced_paths=forced, skip_dirs_regex=None,
            check_binary=True, check_filename=True,
        )
        ok, reason = _should_index_file(big, cfg)[:2]
        raise AssertionError(
            "the incremental path dropped a manifest-exempt oversize file.\n"
            f"  result          = {inc}\n"
            f"  predicate ok    = {ok}\n"
            f"  reject reason   = {reason!r}\n"
            f"  forced_paths    = {sorted(forced)}\n"
            f"  file (raw)      = {str(big)!r}\n"
            f"  file (resolved) = {str(big.resolve())!r}\n"
            f"  file (normcase) = {os.path.normcase(str(big.resolve()))!r}\n"
            f"  size            = {big.stat().st_size} "
            f"cap = {get_max_file_size()}"
        )


def test_forced_path_scan_is_skipped_when_nothing_is_oversize(tmp_path):
    """Correctness must not cost a tree walk per watcher event.

    Spies on the REAL module-level scanner while driving the REAL incremental
    path, so this fails if the shipped closure stops being lazy. An earlier
    draft re-implemented the laziness inside the test and could not have caught
    a regression in the code that ships.
    """
    from jcodemunch_mcp.tools import index_folder as m

    d, big = _oversize_tree(tmp_path)
    small = d / "small.js"
    small.write_text("x = 1\n", encoding="utf-8")
    store_dir = str(tmp_path / "store")
    m.index_folder(str(d), use_ai_summaries=False, storage_path=store_dir)

    calls: list = []
    real = m._scan_package_json_forced_paths

    def spy(p):
        calls.append(p)
        return real(p)

    m._scan_package_json_forced_paths = spy
    try:
        small.write_text("x = 2\n", encoding="utf-8")
        m.index_folder(
            str(d),
            use_ai_summaries=False,
            storage_path=store_dir,
            incremental=True,
            changed_paths=[("modified", str(small))],
        )
        assert calls == [], (
            "the manifest scan (a full tree walk) ran for a change set with no "
            "oversize file; that is the #375 cost class on the hot path"
        )

        big.write_text(TOUCHED_BODY, encoding="utf-8")
        m.index_folder(
            str(d),
            use_ai_summaries=False,
            storage_path=store_dir,
            incremental=True,
            changed_paths=[("modified", str(big))],
        )
        assert calls, "the scan must run when an oversize file IS in the change set"
    finally:
        m._scan_package_json_forced_paths = real


# ── the escape hatch must reach the PRIMARY walk, not just the fast path ──
#
# v1.108.193 added `max_file_size` + JCODEMUNCH_MAX_FILE_SIZE and wired
# `get_max_file_size()` into exactly one call site: the watcher fast path.
# `index_folder` called `discover_local_files` WITHOUT `max_size=`, so the main
# walk kept the hardcoded default and a raised cap changed nothing. The reported
# problem — "the cap cannot be moved" — was still true after the release that
# claimed to fix it.


def test_raised_cap_reaches_the_primary_discovery_walk(monkeypatch, tmp_path):
    from jcodemunch_mcp import security
    from jcodemunch_mcp.tools.index_folder import discover_local_files

    d, _big = _oversize_tree(tmp_path)
    (d / "package.json").unlink()  # isolate the cap from the #25 exemption

    real_get = security._config.get

    def cap(value):
        def fake(key, default=None, *a, **k):
            return value if key == "max_file_size" else real_get(key, default, *a, **k)
        return fake

    monkeypatch.setattr(security._config, "get", cap(2_000_000))
    files, _w, skips = discover_local_files(d)
    assert len(files) == 1 and not skips.get("too_large"), (
        "a raised max_file_size did not reach discover_local_files; the escape "
        f"hatch only covers the fast path. files={len(files)} skips={skips}"
    )


def test_default_cap_still_rejects(monkeypatch, tmp_path):
    """Raising must be opt-in: the default is protection, not an accident."""
    from jcodemunch_mcp.tools.index_folder import discover_local_files

    d, _big = _oversize_tree(tmp_path)
    (d / "package.json").unlink()
    files, _w, skips = discover_local_files(d)
    assert len(files) == 0 and skips.get("too_large") == 1, (
        f"default cap stopped rejecting oversize files. files={len(files)} skips={skips}"
    )


def test_size_cap_exemption_is_case_insensitive_where_the_os_is(tmp_path):
    """The #25 exemption must survive a differently-cased path.

    ⚠ Found by CI on windows-latest, not locally. `_should_index_file` compared
    the RAW resolved string against `forced_paths`, while the gitignore check a
    few lines above it normalised with os.path.normcase for exactly this reason.
    On Windows the same file resolves to strings differing by drive-letter case
    or 8.3 short form, so the exemption silently voided and the entry point was
    dropped as `too_large` — a real user-facing bug, not just a flaky test.
    """
    import os

    from jcodemunch_mcp.tools.index_folder import (
        _build_index_filters,
        _scan_package_json_forced_paths,
        _should_index_file,
    )

    d, big = _oversize_tree(tmp_path)
    forced = _scan_package_json_forced_paths(d)
    assert forced, "fixture produced no forced paths"

    # Every stored entry is normalised, so a caller cannot reintroduce a raw
    # comparison without this failing.
    assert all(e == os.path.normcase(e) for e in forced), (
        f"forced_paths holds un-normalised entries: {forced}"
    )

    cfg = _build_index_filters(
        root=d.resolve(), follow_symlinks=False, max_size=512000,
        extra_spec=None, forced_paths=forced, skip_dirs_regex=None,
        check_binary=True, check_filename=True,
    )
    ok, reason = _should_index_file(big, cfg)[:2]
    assert ok and not reason, (
        f"the manifest-exempt file was rejected as {reason!r} despite being in "
        "forced_paths; the comparison is not normalisation-safe"
    )


def test_a_differently_spelled_watcher_path_is_not_silently_dropped(tmp_path):
    r"""A change whose path spells the root differently must still resolve.

    ⚠ Found by CI on windows-latest after two wrong diagnoses. The fast path did
    `abs_path.relative_to(folder_path)` and answered ValueError with a bare
    `continue`, so a change was DROPPED SILENTLY — no warning, no skip counter.
    On the runner, tempfile handed back an 8.3 short path
    (`C:\Users\RUNNER~1\...`) while the root resolved long
    (`C:\Users\runneradmin\...`). A Windows watcher emitting either form would
    stop reindexing and say nothing about it.

    ⚠⚠ HONEST LIMIT, stated rather than hidden. The assertions below are the
    PORTABLE half of the contract. This machine cannot exercise the fallback:
    Windows pathlib already compares drive letters case-insensitively, and an
    8.3 short name cannot be constructed portably — so neutering the fallback
    does NOT fail this test locally. I checked, and it passed.
    **CI on windows-latest is the real gate for the short-name case.** A test
    that cannot fail is not evidence, so this one is kept only for the contract
    it does pin, and is explicitly NOT offered as proof of the 8.3 fix.
    """
    from pathlib import Path as _P

    from jcodemunch_mcp.tools.index_folder import _rel_to_root

    root = tmp_path / "root"
    root.mkdir()
    root = root.resolve()
    (root / "sub").mkdir()
    f = root / "sub" / "a.js"
    f.write_text("x\n", encoding="utf-8")

    assert _rel_to_root(f, root) == "sub/a.js", "exact spelling must work"

    # A genuinely unrelated path must still be refused — the fallback must not
    # turn "outside the root" into a false positive.
    (tmp_path / "elsewhere").mkdir()
    outside = tmp_path / "elsewhere" / "z.js"
    assert _rel_to_root(outside, root) is None, (
        "a path outside the root must not be accepted by the fallback"
    )

    # A sibling directory sharing a name prefix must not match by string
    # prefix — the normcase fallback compares with a separator appended for
    # exactly this reason.
    sibling = _P(str(root) + "_other") / "a.js"
    assert _rel_to_root(sibling, root) is None, (
        "prefix matching must be separator-aware, not a bare startswith"
    )
