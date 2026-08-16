# Tool-description smell audit

Scores every description this server emits from `tools/list` against the rubric in
**arXiv:2602.14878** — Hasan, Li, Rajbahadur, Adams & Hassan (Queen's University),
*"Model Context Protocol (MCP) Tool Descriptions Are Smelly! Towards Improving AI
Agent Efficiency with Augmented MCP Tool Descriptions"*, v1, 16 Feb 2026.

`rubric_upstream.py` is the authors' scoring prompt, copied unmodified from their
replication package (`SAILResearch/mcp-tool-description-augmentation`,
`mcpuniverse/scripts/description_evaluation_prompt.py`) so the rubric text we score
against is auditable and does not drift with our paraphrase of it.

Six components, each 1-5; a component scoring below 3 is a named smell:

| Component | Smell below 3 |
|---|---|
| Purpose | Unclear Purpose |
| Usage Guideline | Missing Usage Guidance |
| Limitation | Unstated Limitation |
| Parameter Explanation | Opaque Parameters |
| Examples vs. Description Balance | Exemplar Issues |
| Length and Completeness | Underspecified or Incomplete |

## Two frames, and why the second one exists

The authors' scanner payload is name + server + description text. It never sees
`inputSchema`, and the rubric's bottom tier for parameters reads "Parameters not
explained **or only in schema**".

- **paper frame** — parameters judged on description prose alone. Comparable to
  their published 97.1%.
- **schema frame** — parameters credited when the JSON Schema carries a
  per-parameter description, which is what the client actually sends the model.

Report both. The gap between them is a property of the instrument, not of the server.

## Deviation from the paper's method

They score with a three-model FM jury. This script is a deterministic
re-implementation of the rubric's mechanical parts, so results are reproducible in
CI and do not cost an inference call. **The numbers are therefore not
interchangeable with the paper's.** It is calibrated to over-report rather than
under-report: on a hand-audit of 27 tools, every disagreement was a limitation
clause the regex missed, never a smell it invented.

## Results, 2026-08-16

194 tools across the three servers, before and after the limitation-clause pass:

| Smell | before (paper) | after (paper) | after (schema) |
|---|---|---|---|
| Unclear Purpose | 1.5% | **0.0%** | 0.0% |
| Missing Usage Guidance | 14.9% | **5.7%** | 5.7% |
| Unstated Limitation | 51.0% | **21.6%** | 21.6% |
| Opaque Parameters | 47.9% | 43.8% | **1.5%** |
| Underspecified | 4.1% | **0.0%** | 0.0% |
| Exemplar Issues | 0.0% | 0.0% | 0.0% |
| ≥1 smell | 73.2% | **57.7%** | **26.8%** |

Ecosystem baseline from the paper, for the ≥1 smell row: 97.1% (n=856).

The residual Unstated Limitation count is mostly tools whose description does state
a boundary in phrasing the cue set does not match. Re-verify by hand before treating
a number in that column as work to do.

## Running it

```
python benchmarks/description_smells/score_descriptions.py
```

It reads `*_tools.json` dumps next to it. Regenerate a dump with:

```python
from jcodemunch_mcp import server as S
tools = await S.list_tools()
```

`tests/test_description_smells.py` enforces the components that are fully clear:
zero Unclear Purpose and zero Underspecified descriptions on the live jcodemunch
surface. Adding a tool with a one-line description fails that test.

## The cost side

Descriptions are ~34% of tool-surface tokens. The limitation pass grew
`core_compact` by roughly a hundred tokens over the figure in
`benchmarks/schema_baseline.json`, and was then trimmed back under the §10 hard
ceiling rather than re-baselined past it. Read the live number from
`tests/test_schema_budget.py`, which recomputes it, and the frozen one from the
baseline file; neither belongs in this README. Clauses on core-tier tools are terse
for that reason; standard- and full-tier clauses carry the fuller wording.

The paper's own warning applies to augmenting *all six* components at once: +5.85pp
median task success, but +67.46% execution steps and regressions in 16.67% of
domain-model pairs. This pass moved one component, which is the compact variant its
ablation found preserves reliability at lower overhead.
