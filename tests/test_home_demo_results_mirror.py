"""The home-page demo's five-query table is read off its result file.

`benchmarks/home_demo/README.md` publishes all five demo queries, including the
ones that put no answer in the top 5. A reader quotes the table, so every cell
in it must equal the measurement it was copied from, and every query in the
measurement must have a row (the misses included). Offline.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "benchmarks" / "home_demo"


def _rows():
    text = (DEMO / "README.md").read_text(encoding="utf-8")
    rows = {}
    for line in text.splitlines():
        m = re.match(r"^\| (\d) \| (.+?) \| ([\d,]+) \| ([\d,]+) \| ([\d,]+) \| (yes|no)\b", line)
        if m:
            n = int(m.group(1))
            rows[n] = {
                "query": m.group(2),
                "grep": int(m.group(3).replace(",", "")),
                "read_all": int(m.group(4).replace(",", "")),
                "jcm": int(m.group(5).replace(",", "")),
                "answer": m.group(6) == "yes",
            }
    return rows


def _result():
    return json.loads((DEMO / "results-1.108.391.json").read_text(encoding="utf-8"))


def test_every_measured_query_has_a_row_including_the_misses():
    result = _result()
    rows = _rows()
    assert sorted(rows) == [c["n"] for c in result["candidates"]]
    assert any(not r["answer"] for r in rows.values()), "the misses are published too"


def test_every_cell_equals_the_measurement():
    rows = _rows()
    for c in _result()["candidates"]:
        row = rows[c["n"]]
        assert row["query"] == c["query"]
        assert row["grep"] == c["grep_top3_tokens"]
        assert row["read_all"] == c["read_all_tokens"]
        assert row["jcm"] == c["jcm_tokens"]
        assert row["answer"] == bool(c["answer_in_top5"])


def test_the_readme_names_the_pin_and_version_of_the_result():
    result = _result()
    text = (DEMO / "README.md").read_text(encoding="utf-8")
    assert result["pin"] in text
    assert result["jcodemunch_mcp"] in text
