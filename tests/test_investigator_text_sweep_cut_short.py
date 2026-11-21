"""A text sweep cut short is not a sweep that found nothing (LEDGER L-309).

The deletion investigator's `no_textual_use` obligation asks `search_text`
for the name and reads `results` only. `search_text` returns 20 lines by
default, so a symbol whose own file mentions its name 20 times or more fills
the page with its own file. A use in another file is never returned, and the
obligation read `SATISFIED`: "appears only in its own file". The same holds
for a scan stopped by the regex time budget and for an index that the
`max_folder_files` cap left without some files; `search_text` reports all
three, and the obligation looked at none of them.

The sweep now asks for the whole match set, and when the answer still says it
was cut short, a sweep that found no other file is `UNESTABLISHED`, never
`SATISFIED`. A use it did find still refutes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jcodemunch_mcp.investigator import REFUTED, SATISFIED, UNESTABLISHED
from jcodemunch_mcp.investigator.deletion_safety import investigate_deletion_safety
from jcodemunch_mcp.tools.index_folder import index_folder

# `aaa.py` sorts ahead of `main.py`, so a page of 20 lines is all its own.
_OWN_MENTIONS = 30


def _index(root: Path, files: dict[str, str]) -> tuple[str, str]:
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    storage = str(root / ".index")
    res = index_folder(str(root), use_ai_summaries=False, storage_path=storage)
    return res.get("repo", str(root)), storage


def _files(own_mentions: int) -> dict[str, str]:
    body = "def zap():\n    return 1\n" + "".join(
        f"# zap note {i}\n" for i in range(own_mentions)
    )
    return {
        "aaa.py": body,
        # A use the import graph cannot see: the name reached by string.
        "main.py": 'import aaa\n\nif __name__ == "__main__":\n    getattr(aaa, "zap")()\n',
    }


def _text_ob(result: dict) -> dict:
    for o in result["obligations"]:
        if o["obligation"] == "no_textual_use":
            return o
    raise AssertionError(result["obligations"])


def test_a_use_past_the_first_page_still_refutes(tmp_path):
    repo, storage = _index(tmp_path, _files(_OWN_MENTIONS))
    res = investigate_deletion_safety(
        repo, "zap", include_runtime=False, storage_path=storage
    )
    ob = _text_ob(res)
    assert ob["status"] == REFUTED, ob
    assert res["verdict"] != "safe"


def test_the_use_refutes_when_the_own_file_is_short(tmp_path):
    """Control: the fixture's use is visible to the sweep at all."""
    repo, storage = _index(tmp_path, _files(2))
    res = investigate_deletion_safety(
        repo, "zap", include_runtime=False, storage_path=storage
    )
    assert _text_ob(res)["status"] == REFUTED


@pytest.mark.parametrize(
    "signal",
    [
        {"truncated": True},
        {"timed_out": True},
        {"_meta": {"index_truncated": {"truncated": True}}},
    ],
    ids=["max_results", "timed_out", "index_cap"],
)
def test_a_cut_short_sweep_with_no_other_file_is_unestablished(
    tmp_path, monkeypatch, signal
):
    repo, storage = _index(tmp_path, {"aaa.py": "def zap():\n    return 1\n"})
    import jcodemunch_mcp.tools.search_text as st

    def cut_short(*a, **k):
        out = {
            "result_count": 1,
            "truncated": False,
            "timed_out": False,
            "results": [{"file": "aaa.py", "line": 1, "text": "def zap():"}],
            "_meta": {},
        }
        out.update(signal)
        return out

    monkeypatch.setattr(st, "search_text", cut_short)
    res = investigate_deletion_safety(
        repo, "zap", include_runtime=False, storage_path=storage
    )
    ob = _text_ob(res)
    assert ob["status"] == UNESTABLISHED, ob
    assert res["verdict"] != "safe"


def test_a_complete_sweep_of_the_own_file_only_is_satisfied(tmp_path):
    repo, storage = _index(tmp_path, {"aaa.py": "def zap():\n    return 1\n"})
    res = investigate_deletion_safety(
        repo, "zap", include_runtime=False, storage_path=storage
    )
    assert _text_ob(res)["status"] == SATISFIED


def test_the_sweep_asks_for_the_whole_match_set(tmp_path, monkeypatch):
    repo, storage = _index(tmp_path, {"aaa.py": "def zap():\n    return 1\n"})
    import jcodemunch_mcp.tools.search_text as st

    seen: list = []
    real = st.search_text

    def spy(*a, **k):
        seen.append(k.get("scan_limit"))
        return real(*a, **k)

    monkeypatch.setattr(st, "search_text", spy)
    investigate_deletion_safety(
        repo, "zap", include_runtime=False, storage_path=storage
    )
    assert seen and seen[0] == st.SCAN_LIMIT_MAX
