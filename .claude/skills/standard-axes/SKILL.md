---
name: standard-axes
description: "The criterion numbers of STANDARD.md, so an acceptance criterion can be mapped to one. Load in /feature step 2."
---
# STANDARD.md axes

Authority: `docs/standard/STANDARD.md`. Read the criterion you map to;
this list is only its index.

Ranked criteria: 1 correctness of what is returned; 2 token reduction per
task; 3 index freshness and incremental cost; 4 tool-surface discipline;
5 latency; 6 install, configuration and client friction; 7 stability across
releases; 8 security and integrity of what is indexed; 9 observability and
telemetry honesty; 10 breadth of language support.

Non-functional: N1 test-suite runtime ceiling; N2 coverage floor; N3 lint
and type cleanliness; N4 deterministic benchmark output; N5 no network
during tests; N6 agent-instruction budget; N7 CI skip count.

Every acceptance criterion in a spec names one of these by number and the
Floor ids it could move (`uv run python -m harness thresholds` lists them;
cite ids, never values). A criterion that maps to none is not an
acceptance criterion; rewrite it or drop it.
