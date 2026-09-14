"""get_tectonic_map's temporal (git co-churn) signal produces edges (#667).

⚠⚠ The signal carries 30% of the documented weight and had contributed ZERO
edges on every repository since the tool shipped: it ran
`git log --format=COMMIT_SEP`, git resolves a bare `--format=` value as a
pretty-format NAME, answered `fatal: invalid --pretty format`, and the
non-zero branch returned `{}` without a word. Every existing assertion was
satisfied by `{}` (`"structural" in signals_used`; encoder tests round-tripped
a hand-written `"temporal"` a producer never emitted), so these assert the
NON-EMPTY direction against a real repository.

What each test pins, and why (for docs/harness/ARCHAEOLOGY.md):

- two files committed together yield a temporal edge for that pair, from a
  real `git log` (the #667 defect);
- an index rooted BELOW the git top level still matches: `git log --name-only`
  prints top-level paths, the index holds root-relative ones, and without
  `--relative` every name misses and the signal is `{}` again by a second
  route;
- get_tectonic_map reports `"temporal"` in `signals_used` end to end;
- a git failure is LOGGED at WARNING with git's stderr (a silent `{}` is
  indistinguishable from a repository with no co-changes);
- a shallow clone that truncates the window WITHHOLDS the signal and says so
  in the answer (`signals_withheld`), because each signal is normalised
  against its own maximum and a truncated history would rescale it, not
  weaken it (Practice 6's shallow-clone family; the reason is not put in
  `_meta`, which a default install strips);
- no git invocation under src/ passes a bare `--format=`/`--pretty=` literal
  that git would read as a format NAME (the property, not the one call site).
"""

from __future__ import annotations

import logging
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from jcodemunch_mcp.tools import _git_history as gh
from jcodemunch_mcp.tools import get_tectonic_map as gtm
from jcodemunch_mcp.tools.index_folder import index_folder

REPO = Path(__file__).resolve().parents[1]


def _git(args, cwd, when=None):
    env = None
    if when is not None:
        import os
        stamp = when.strftime("%Y-%m-%dT%H:%M:%S+00:00")
        env = {**os.environ, "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false"] + args,
        cwd=str(cwd), capture_output=True, text=True, stdin=subprocess.DEVNULL, env=env,
    )


def _co_churn_repo(root: Path, sub: str = "", commits: int = 3, span_days: int = 10) -> Path:
    """A git repo whose `a.py` and `b.py` (under `sub`) change together in every
    commit, and `c.py` changes alone. Returns the directory holding the files."""
    root.mkdir(parents=True, exist_ok=True)
    _git(["init", "-q", "-b", "main"], root)
    pkg = root / sub if sub else root
    pkg.mkdir(parents=True, exist_ok=True)
    # c.py is committed ALONE, always, so the only co-changing pair is (a, b)
    (pkg / "c.py").write_text("def leaf():\n    return 1\n", encoding="utf-8")
    _git(["add", "-A"], root)
    _git(["commit", "-q", "-m", "init c"], root, when=datetime.now(timezone.utc) - timedelta(days=span_days + 2))
    (pkg / "b.py").write_text("from c import leaf\n\ndef mid():\n    return leaf()\n", encoding="utf-8")
    (pkg / "a.py").write_text("from b import mid\n\ndef top():\n    return mid()\n", encoding="utf-8")
    _git(["add", "-A"], root)
    _git(["commit", "-q", "-m", "init a b"], root, when=datetime.now(timezone.utc) - timedelta(days=span_days + 1))
    for i in range(commits):
        when = datetime.now(timezone.utc) - timedelta(days=span_days - span_days * i / max(commits, 1))
        with open(pkg / "a.py", "a", encoding="utf-8") as f:
            f.write(f"# a{i}\n")
        with open(pkg / "b.py", "a", encoding="utf-8") as f:
            f.write(f"# b{i}\n")
        _git(["add", "-A"], root)
        _git(["commit", "-q", "-m", f"together {i}"], root, when=when)
    with open(pkg / "c.py", "a", encoding="utf-8") as f:
        f.write("# alone\n")
    _git(["add", "-A"], root)
    _git(["commit", "-q", "-m", "alone"], root, when=datetime.now(timezone.utc) - timedelta(days=1))
    return pkg


@pytest.fixture(autouse=True)
def _clear_history_memo():
    gh._CACHE.clear()
    yield
    gh._CACHE.clear()


def test_files_committed_together_are_temporally_coupled(tmp_path):
    pkg = _co_churn_repo(tmp_path / "r")
    edges = gtm._temporal_edges(str(pkg), frozenset({"a.py", "b.py", "c.py"}), days=90)
    assert edges, "the temporal signal returned {} for a repo with co-changes (#667)"
    assert edges.get(("a.py", "b.py")) == 1.0, edges
    assert ("a.py", "c.py") not in edges and ("b.py", "c.py") not in edges


def test_an_index_rooted_below_the_git_top_level_still_matches(tmp_path):
    pkg = _co_churn_repo(tmp_path / "mono", sub="services/api")
    edges = gtm._temporal_edges(str(pkg), frozenset({"a.py", "b.py", "c.py"}), days=90)
    assert edges.get(("a.py", "b.py")) == 1.0, edges


def test_get_tectonic_map_reports_the_temporal_signal(tmp_path):
    pkg = _co_churn_repo(tmp_path / "r")
    store = tmp_path / "store"
    store.mkdir()
    res = index_folder(str(pkg), use_ai_summaries=False, storage_path=str(store))
    assert res["success"] is True
    out = gtm.get_tectonic_map(res["repo"], storage_path=str(store))
    assert "error" not in out, out
    assert "temporal" in out["signals_used"], out["signals_used"]
    assert "signals_withheld" not in out


def test_a_git_failure_is_logged_with_its_stderr(tmp_path, monkeypatch, caplog):
    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 128, "", "fatal: invalid --pretty format: X\n")

    monkeypatch.setattr(gtm.subprocess, "run", fake_run)
    with caplog.at_level(logging.WARNING, logger=gtm.logger.name):
        assert gtm._temporal_edges(str(tmp_path), frozenset({"a.py"}), days=90) == {}
    assert any(
        r.levelno >= logging.WARNING and "invalid --pretty format" in r.getMessage() for r in caplog.records
    ), [r.getMessage() for r in caplog.records]


def test_a_shallow_clone_that_truncates_the_window_withholds_the_signal(tmp_path):
    src = _co_churn_repo(tmp_path / "src", commits=4, span_days=300)
    dst = tmp_path / "shallow"
    subprocess.run(["git", "clone", "-q", "--depth=2", f"file://{src.as_posix()}", str(dst)],
                   capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if _git(["rev-parse", "--is-shallow-repository"], dst).stdout.strip() != "true":
        pytest.skip("git did not produce a shallow clone here")
    assert gh.history_coverage(str(dst), 90)["complete"] is False
    store = tmp_path / "store"
    store.mkdir()
    res = index_folder(str(dst), use_ai_summaries=False, storage_path=str(store))
    assert res["success"] is True
    out = gtm.get_tectonic_map(res["repo"], storage_path=str(store), days=90)
    assert "error" not in out, out
    assert "temporal" not in out["signals_used"]
    assert out["signals_withheld"]["temporal"]["reason"] == "shallow_truncates_window", out.get("signals_withheld")


_KNOWN_PRETTY_NAMES = {"oneline", "short", "medium", "full", "fuller", "reference", "email", "raw", "mboxrd"}


def test_no_git_call_passes_a_bare_format_literal():
    """git reads `--format=<x>`/`--pretty=<x>` as a format NAME unless <x> holds a
    `%` placeholder or a `format:`/`tformat:` prefix; any other literal fails."""
    rx = re.compile(r"""["']--(?:format|pretty)=([^"']*)["']""")
    offenders = []
    for path in sorted((REPO / "src").rglob("*.py")):
        for m in rx.finditer(path.read_text(encoding="utf-8")):
            value = m.group(1)
            if value == "" or "%" in value or "{" in value or value.startswith(("format:", "tformat:")):
                continue  # `{` is an f-string that interpolates a placeholder-bearing string
            if value in _KNOWN_PRETTY_NAMES:
                continue
            offenders.append(f"{path.relative_to(REPO)}: --format={value}")
    assert offenders == [], offenders


def test_the_compact_encoding_keeps_signals_withheld():
    """The reason a signal was withheld must survive MUNCH compaction; an
    undeclared key would be dropped there and the answer would read as a
    two-signal repository with nothing wrong."""
    from jcodemunch_mcp.encoding.schemas import get_tectonic_map as enc

    withheld = {"temporal": {"complete": False, "shallow": True, "reason": "shallow_truncates_window", "window_days": 90}}
    response = {
        "repo": "o/r", "plate_count": 0, "file_count": 2, "plates": [], "isolated_files": ["a.py", "b.py"],
        "signals_used": ["structural"], "signals_withheld": withheld, "drifter_summary": [],
        "_meta": {"timing_ms": 1.0, "methodology": "tectonic_label_propagation"},
    }
    payload, _ = enc.encode("get_tectonic_map", response)
    assert enc.decode(payload)["signals_withheld"] == withheld
