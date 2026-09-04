---
name: observatory
description: "Context for the jcodemunch-observatory weekly scorecard — what it tracks, the Monday 06:00 UTC cron, why scores must be pulled live rather than transcribed, the NestJS grade story, and the workflow's version dispatch input. Load before quoting, interpreting, or editing observatory scores or config."
---

# Observatory tracking

`jcodemunch-mcp`, `jdocmunch-mcp`, and `jdatamunch-mcp` are tracked
weekly at https://jgravelle.github.io/jcodemunch-observatory/
alongside Express, FastAPI, Gin, Pydantic, Django, Flask, NestJS,
and Cobra (11 repos total). Cron: Mondays 06:00 UTC. Config edits
auto-trigger a rebuild via the workflow's push-on-paths filter.

⚠⚠ **MONDAY 2026-08-31 IS THE FIRST HONEST BUILD, AND IT WILL LOOK LIKE A
REGRESSION.** Until v1.108.303 the pipeline cloned with `--depth=1`, so
`churn_surface` read churn 1 for every file in every repo and the axis ranked
nothing but complexity — **every published score was flattered, ours included.**
Expect a step change across the roster with no upstream cause: jcm goes **81.3
(B) -> 75.6 (C)**, and gin's grade holds at 91.8 (A) while its `churn_surface`
raw moves 55.45 -> 39.42 because a different symbol becomes top hotspot.
**Do NOT chase this as a new defect and do NOT attribute it to any release.**
jjg decided on 2026-08-27 to let the cron publish it rather than trigger a
rebuild early. Delete this note once that build is up.

**Scores are NOT transcribed here.** Pull them from the live `index.json` before
quoting them anywhere ([[feedback_verify_observatory_output]]). A transcription is
always stale: the cron is Mondays 06:00 UTC, so it lags any same-week release.

As of the 2026-07-20 build, suite ranking holds: jdatamunch > jdocmunch > jcodemunch — smaller,
more focused tools score higher; jcm carries more surface area.
All three sit at or above the median, and all three keep their grade.
Every move is <=0.4 and no grade changed, so this is drift, not signal:
the generator itself advanced 1.108.114 -> 1.108.146 between crons, and
the third-party repos moved on their own upstream commits. **Do not
attribute these deltas to any specific release without checking the
scored SHA** — the two 0.1s and jdoc's -0.2 sit well inside the range
the generator bump alone can produce.

**NestJS's persistent D is a correctness story, not a regression.**
The barrel-aware import graph (jcm [PR #284](https://github.com/jgravelle/jcodemunch-mcp/pull/284):
`export * from <spec>` capture + dotted-basename resolution like
`./foo.service`) exposed ~18 real dependency cycles in NestJS's
inter-package barrel chains that its previously-broken graph hid
(`find_importers` against `injectable.decorator.ts` went 0 → 791).
The corrected graph keeps NestJS at the bottom because the cycles
are real.

Workflow hardening: `.github/workflows/observatory.yml` now accepts
a `version` workflow_dispatch input (`gh workflow run observatory.yml
-f version=1.93.0`) to dodge PyPI CDN propagation lag, and the
workdir-cache key prefix tracks `INDEX_VERSION` so schema bumps
invalidate prior cached SQLite indexes automatically.
