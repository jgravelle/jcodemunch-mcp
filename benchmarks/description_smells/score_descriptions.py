"""Score jMunch MCP tool descriptions against the rubric from arXiv:2602.14878.

Rubric source: SAILResearch/mcp-tool-description-augmentation,
mcpuniverse/scripts/description_evaluation_prompt.py (DESCRIPTION_QUALITY_PROMPT).

The paper operationalises the rubric through an FM judge. This script is a
deterministic re-implementation of the mechanical parts of that rubric so the
scores are reproducible and auditable. Components that need judgement (purpose
wording quality) use cue-based proxies; those are flagged in the output so they
can be spot-checked by hand.

Two scoring frames, because the paper's scanner payload is name + server +
description text ONLY (no inputSchema):
  - frame "paper": parameter_explanation judged on description prose alone.
    Rubric 1/5 is literally "Parameters not explained or only in schema".
  - frame "schema": parameter_explanation credited when the JSON Schema carries
    a per-parameter description (which the client does send to the model).
"""

import json
import re
import sys
from pathlib import Path

SP = Path(__file__).parent

WHEN_TO_USE = re.compile(
    r"\b(use (this|it|when|before|after|for|to|instead)|call (this|it|before|once)|"
    r"start here|good first call|designed for|designed to be|run this|pairs? with|"
    r"use case|opening move|first tool called|use [a-z_]{4,} (as|when|for|to)|"
    r"for cheap|for navigation|workflow)\b",
    re.I,
)
WHEN_NOT_TO_USE = re.compile(
    r"(\buse [a-z_]+ instead\b|\bfor [^.]{3,60}, use [a-z_]+\b|\bprefer [a-z_(]+\b|"
    r"\bdoes not\b|\bdoes NOT\b|\bdistinct from\b|\brather than\b|\bnot supported\b|"
    r"\binstead of\b|\bdo not re-run\b|\bnever\b|\bskip\b)",
    re.I,
)
LIMITATION = re.compile(
    r"(\brequires?\b|\bonly\b|\bdoes not\b|\bnot supported\b|\bheuristic\b|\bcapped?\b|"
    r"\breturns? (an )?empty\b|\bread-only\b|\bnever (writes|mutates|edits)\b|"
    r"\bdegrades\b|\bestimate\b|\bnot exhaustive\b|\bfails? closed\b|\bunless\b|"
    r"\bmust be\b|\bcannot\b|\bno .{0,20}(access|i/o)\b|\breserved\b|\bopt-in\b|"
    r"\bwhen no [a-z ]{3,30}(been )?(ingested|run|configured|indexed)\b|"
    r"\bempty [a-z_ ]{0,20}(list|patterns)\b|\bexcludes?\b|\brefuses?\b|"
    r"\bpre-?1\.\d|\bre-?index\b|\bnot yet\b|\bpartial\b|\bmay (be|not)\b|"
    r"\bNOT\b|\bno (content|embeddings?|index|dataset|repo|registry|traces?) [a-z]{2,12}\b|"
    r"\boptional(ly)? \[?[a-z]|\bwhen the optional\b|\bskipped\b|\bfalls? back\b)",
    re.I,
)
RETURN_SHAPE = re.compile(r"\b(returns?|reports?|surfaces?|emits?|yields?)\b", re.I)
EXAMPLE_CUE = re.compile(r"(e\.g\.|for example|such as|'[^']{2,40}'|\"[^\"]{2,40}\")")


def sentences(text):
    parts = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]
    return [s for s in parts if len(s.split()) >= 3]


def score_tool(tool, frame):
    desc = (tool.get("description") or "").strip()
    schema = tool.get("inputSchema") or {}
    props = schema.get("properties") or {}
    sents = sentences(desc)
    n_sent = len(sents)
    words = len(desc.split())

    # 6. Length and Completeness
    if n_sent >= 4:
        length = 5
    elif n_sent == 3:
        length = 4
    elif n_sent == 2:
        length = 3
    elif n_sent == 1 and words >= 8:
        length = 2
    else:
        length = 1

    # 1. Purpose
    # Judged on the whole description, not the opening sentence: several tools
    # open with a terse noun-phrase label and carry the behaviour immediately after.
    has_return = bool(RETURN_SHAPE.search(desc))
    if has_return and n_sent >= 2 and words >= 25:
        purpose = 5
    elif (n_sent >= 2 and words >= 15) or (has_return and words >= 12):
        purpose = 4
    elif words >= 8:
        purpose = 3
    elif words >= 5:
        purpose = 2
    else:
        purpose = 1

    # 2. Usage Guideline
    pos = bool(WHEN_TO_USE.search(desc))
    neg = bool(WHEN_NOT_TO_USE.search(desc))
    if pos and neg:
        guideline = 5
    elif pos:
        guideline = 4
    elif neg:
        guideline = 3
    elif n_sent >= 3:
        guideline = 3
    else:
        guideline = 2

    # 3. Limitation
    lim_hits = len(set(m.group(0).lower() for m in LIMITATION.finditer(desc)))
    if lim_hits >= 3:
        limitation = 5
    elif lim_hits == 2:
        limitation = 4
    elif lim_hits == 1:
        limitation = 3
    else:
        limitation = 1

    # 4. Parameter Explanation
    if not props:
        param = 5  # no parameters to explain
        param_note = "no-params"
    else:
        named = sum(1 for p in props if re.search(rf"\b{re.escape(p)}\b", desc))
        ratio_desc = named / len(props)
        schema_described = sum(
            1 for v in props.values()
            if isinstance(v, dict) and (v.get("description") or "").strip()
        )
        ratio_schema = schema_described / len(props)
        ratio = ratio_desc if frame == "paper" else max(ratio_desc, ratio_schema)
        if ratio >= 0.9:
            param = 5
        elif ratio >= 0.6:
            param = 4
        elif ratio >= 0.3:
            param = 3
        elif ratio > 0:
            param = 2
        else:
            param = 1
        param_note = f"prose={named}/{len(props)} schema={schema_described}/{len(props)}"

    # 5. Examples vs. Description Balance
    ex_hits = len(EXAMPLE_CUE.findall(desc))
    prose_chars = len(desc)
    if ex_hits == 0:
        examples = 5
    elif ex_hits * 60 < prose_chars * 0.5:
        examples = 5
    else:
        examples = 4

    scores = {
        "purpose": purpose,
        "usage_guideline": guideline,
        "limitation": limitation,
        "parameter_explanation": param,
        "examples_balance": examples,
        "length_completeness": length,
    }
    total = sum(scores.values())
    overall = round(((total - 6) / 24) * 100, 1)
    smells = [k for k, v in scores.items() if v < 3]
    return {
        "name": tool["name"],
        **scores,
        "overall": overall,
        "label": "Bad" if smells else "Good",
        "smells": smells,
        "n_sent": n_sent,
        "chars": len(desc),
        "param_note": param_note,
    }


SMELL_NAMES = {
    "purpose": "Unclear Purpose",
    "usage_guideline": "Missing Usage Guidance",
    "limitation": "Unstated Limitation",
    "parameter_explanation": "Opaque Parameters",
    "length_completeness": "Underspecified or Incomplete",
    "examples_balance": "Exemplar Issues",
}


def run(server_files, frame):
    all_rows = []
    print(f"\n{'='*72}\nFRAME: {frame}\n{'='*72}")
    for label, path in server_files:
        tools = json.loads(Path(path).read_text(encoding="utf-8"))
        rows = [score_tool(t, frame) for t in tools]
        for r in rows:
            r["server"] = label
        all_rows.extend(rows)
        smelly = [r for r in rows if r["smells"]]
        print(f"\n{label}: {len(rows)} tools | smell-free {len(rows)-len(smelly)} "
              f"({100*(len(rows)-len(smelly))/len(rows):.1f}%) | "
              f"median overall {sorted(r['overall'] for r in rows)[len(rows)//2]:.1f}")
        for comp, sname in SMELL_NAMES.items():
            n = sum(1 for r in rows if r[comp] < 3)
            med = sorted(r[comp] for r in rows)[len(rows)//2]
            print(f"   {sname:<30} median {med}/5   smelly {n:>3}/{len(rows)} ({100*n/len(rows):5.1f}%)")

    n = len(all_rows)
    smelly = [r for r in all_rows if r["smells"]]
    print(f"\nSUITE TOTAL: {n} tools | at least one smell: {len(smelly)} "
          f"({100*len(smelly)/n:.1f}%)  [paper baseline: 97.1%]")
    for comp, sname in SMELL_NAMES.items():
        c = sum(1 for r in all_rows if r[comp] < 3)
        print(f"   {sname:<30} {c:>3}/{n} ({100*c/n:5.1f}%)")
    return all_rows


if __name__ == "__main__":
    files = [
        ("jcodemunch", SP / "jcm_tools_full.json"),
        ("jdocmunch", SP / "jdoc_tools.json"),
        ("jdatamunch", SP / "jdata_tools.json"),
    ]
    paper = run(files, "paper")
    schema = run(files, "schema")
    json.dump({"paper": paper, "schema": schema},
              open(SP / "scores.json", "w", encoding="utf-8"), indent=1)
