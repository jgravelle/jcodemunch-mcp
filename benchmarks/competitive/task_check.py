"""The task answerability check (docs/competitive/DESIGN.md s4.3, s10; the
brief's Phase 3 item 3): may a task be scored head-to-head at all?

purpose:  refuse a malformed task before scoring, and keep a task only one
          side can answer out of every head-to-head table (symmetric: a
          jcm-only task is excluded the same way as a Serena-only one)
invokes:  nothing outside the process: the task records, the corpora's
          shared file sets, and each adapter's declared categories
produces: `check()`: problems that refuse the run; `split()`: the scored
          tasks and the capability-only list the result file carries;
          `tools_not_called()`: after a run, the (tool, category) pairs
          whose `cited` set is empty on EVERY scored task of a P category,
          the shape of an adapter that was silently not called (s10)
refuses:  a task whose category no null adapter answers (a malformed
          record: null_grep answers every category by construction); a
          task whose expected file is absent from its corpus at its SHA; a
          query that names a jCodeMunch tool, a symbol id or a `_meta` field
pinned:   nothing of its own: it reads the task files and corpora.json's
          set as pinned there
fairness: symmetric by construction; nothing here reads a competitor's text
"""

from __future__ import annotations

from adapter import Corpus, Task

TOOL_WORDS = ("search_symbols", "get_symbol_source", "symbol_id", "_meta")


def check(tasks: list[Task], corpora: dict[str, Corpus], adapters: list) -> list[str]:
    problems = []
    null_cats: set[str] = set()
    for a in adapters:
        if a.interface == "null":
            null_cats |= set(a.categories)
    for t in tasks:
        if t.corpus not in corpora:
            problems.append(f"{t.id}: corpus {t.corpus!r} not in this run")
            continue
        if null_cats and t.category not in null_cats:
            problems.append(f"{t.id}: category {t.category} is not answerable by the null alternative")
        files = set(corpora[t.corpus].files)
        for f, _ in t.expected:
            if f not in files:
                problems.append(f"{t.id}: expected file {f!r} is not in the corpus")
        low = t.query.lower()
        for word in TOOL_WORDS:
            if word in low:
                problems.append(f"{t.id}: query names a jcodemunch tool or field ({word})")
    return problems


def split(tasks: list[Task], adapters: list) -> tuple[list[Task], list[dict]]:
    """(scored, capability_only). A task is capability-only when it says so
    or when fewer than two non-null adapters declare its category (with at
    least two non-null adapters present, so a jcm-only run scores everything)."""
    non_null = [x for x in adapters if x.interface != "null"]
    scored, cap = [], []
    for t in tasks:
        able = [x.name for x in non_null if t.category in x.categories]
        if t.capability_only or (len(able) < 2 and len(non_null) >= 2):
            cap.append({"task": t.id, "category": t.category, "answerable_by": able})
        else:
            scored.append(t)
    return scored, cap


def tools_not_called(runs: list[dict], adapters: list) -> list[dict]:
    """After the runs: a non-null tool that cited NOTHING on every scored task
    of a P category, in every run, on a corpus. Reported, never a refusal: it
    is the first hypothesis `findings.py` assigns to such a row (s7.1)."""
    out = []
    for a in adapters:
        if a.interface == "null":
            continue
        by_cat: dict[tuple[str, str], list[int]] = {}
        for run in runs:
            for cid, rec in run.get(a.name, {}).items():
                for t in rec.get("tasks", []):
                    if t["category"].startswith("P") and not t.get("error"):
                        by_cat.setdefault((cid, t["category"]), []).append(int(t.get("cited", 0)))
        for (cid, cat), cites in sorted(by_cat.items()):
            if cites and not any(cites):
                out.append({"tool": a.name, "corpus": cid, "category": cat, "tasks": len(cites), "hypothesis": "tool_not_called"})
    return out
