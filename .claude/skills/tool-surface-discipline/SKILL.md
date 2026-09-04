---
name: tool-surface-discipline
description: "The small-front-door principle and what counts as a tool-surface change. Load in /feature step 3, in review, and before editing any tool name, argument, description or tier."
---
# Tool-surface discipline

Authority: `docs/standard/STANDARD.md` criterion 4; CLAUDE.md
"Tool-description quality", "Codex tool-surface benchmark", "Tier-switch
pricing"; `tests/test_schema_budget.py`,
`tests/test_counter_surface_stability.py`, `tests/test_description_smells.py`;
`scripts/surface_diff.py`.

The principle in three sentences: the published `counter` surface is
byte-pinned and a reworded description is a full-rate cache write for every
user; `core_compact` has a hard token ceiling the tree sits within a few
tokens of; a new tool is paid for on every request by every user whether or
not they call it.

A surface change is ANY of: a tool added, removed or renamed; an argument
added, removed or retyped; a description reworded; a tier or profile
membership moved; a Counter front-door change; an always-visible control
added. `scripts/surface_diff.py` sees names only (`docs/workflows/FINDINGS.md`
W-1); descriptions are diffed from `_build_tools_list()` dumps.

Every surface change carries: README tool reference, CLAUDE.md Key Files
(invariant) or KEY-FILES.md (description), CHANGELOG naming the tool, and
`benchmarks/schema_baseline.json` regenerated with the token delta stated
(DoD 4). The `schema.core_compact_ceiling` verdict decides whether a
description may grow; the answer is usually to trim.
