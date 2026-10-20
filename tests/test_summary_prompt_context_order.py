"""The summary prompt's `Context:` lines keep first-seen order on every run (#1178).

Reported by @skibadubskiybadubs: `_build_prompt` collected the ecosystem
context lines into a `set()`, so their order followed Python's string hash
seed. Two identical runs sent different prompts (5 of 20 in the report), and a
different prompt gives a different summary even at temperature 0, so a
re-index with no code change could rewrite summaries.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"

CONTEXTS = [f"provider{i}: context line {i}" for i in range(8)]

SCRIPT = """
import sys
from jcodemunch_mcp.parser.symbols import Symbol
from jcodemunch_mcp.summarizer.batch_summarize import BaseSummarizer

contexts = %r
symbols = []
for i, ctx in enumerate(contexts + contexts[:3]):
    symbols.append(Symbol(
        id=f"m.py::f{i}#function", file="m.py", name=f"f{i}", qualified_name=f"f{i}",
        kind="function", language="python", signature=f"def f{i}():", ecosystem_context=ctx,
    ))
sys.stdout.write(BaseSummarizer()._build_prompt(symbols))
""" % (CONTEXTS,)


def _prompt(seed: str) -> str:
    env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONPATH=str(SRC))
    return subprocess.run(
        [sys.executable, "-c", SCRIPT],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=True,
    ).stdout


def test_the_context_block_is_the_same_under_every_hash_seed():
    prompts = {seed: _prompt(seed) for seed in ("1", "2", "3", "4", "5", "6")}
    assert len(set(prompts.values())) == 1, "the prompt depends on the hash seed"


def test_the_context_block_is_in_first_seen_order_with_duplicates_dropped():
    prompt = _prompt("7")
    block = prompt.split("Context:\n", 1)[1]
    lines = [ln for ln in block.splitlines() if ln.startswith("provider")]
    assert lines == CONTEXTS
