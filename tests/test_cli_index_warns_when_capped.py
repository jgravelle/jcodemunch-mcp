"""The CLI says on stderr when the file cap left files out of an index.

`index` prints its result as JSON on stdout. When `max_folder_files` drops
files, that JSON carries `truncated: true` and a warning, but the run still
reports success and exits 0, so a person or a script reading the exit code or
skimming the output saw a good index. Benchmarking indexed 2,000 of 24,347
home-assistant files this way under the default cap. The CLI now writes one
line to stderr naming the counts and the key to raise. Stdout and the exit
code are unchanged, because a capped index is a working index.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys


def _index(folder, storage, cap):
    env = {
        **os.environ,
        "CODE_INDEX_PATH": str(storage),
        "JCODEMUNCH_USE_AI_SUMMARIES": "false",
        "JCODEMUNCH_MAX_FOLDER_FILES": str(cap),
        "JCODEMUNCH_TRUSTED_FOLDERS": str(folder.parent),
    }
    return subprocess.run(
        [sys.executable, "-m", "jcodemunch_mcp", "index", str(folder), "--no-ai-summaries"],
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
        stdin=subprocess.DEVNULL,
    )


def _make_repo(root, n):
    root.mkdir()
    for i in range(n):
        (root / f"m{i}.py").write_text(f"def f{i}():\n    return {i}\n", encoding="utf-8")
    return root


def _cap_lines(stderr: str) -> list[str]:
    return [line for line in stderr.splitlines() if "max_folder_files" in line]


def test_a_capped_index_warns_on_stderr(tmp_path):
    repo = _make_repo(tmp_path / "repo", 12)
    proc = _index(repo, tmp_path / "store", cap=5)

    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert result["success"] is True
    assert result["truncated"] is True
    assert result["files_skipped_cap"] == 7

    lines = _cap_lines(proc.stderr)
    assert len(lines) == 1, proc.stderr
    line = lines[0]
    assert "5 of 12" in line
    assert "7" in line
    assert "JCODEMUNCH_MAX_FOLDER_FILES" in line


def test_an_uncapped_index_prints_no_cap_warning(tmp_path):
    repo = _make_repo(tmp_path / "repo", 4)
    proc = _index(repo, tmp_path / "store", cap=50)

    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout).get("truncated") in (None, False)
    assert _cap_lines(proc.stderr) == []
