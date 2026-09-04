---
name: changelog-format
description: "The shape of a CHANGELOG.md entry and heading in this repo. Load before writing an [Unreleased] line or cutting a release block."
---
# CHANGELOG format

Authority: the top of `CHANGELOG.md` (the newest block is the template),
`scripts/dod_changelog.py` (DoD 3, the gate), CLAUDE.md "Output Rules"
(the CHANGELOG voice is the one deliberate carve-out: entries ARGUE).

- Heading: `## [X.Y.Z] - YYYY-MM-DD - <thesis sentence>`; the thesis is
  the one lesson of the release. `## [Unreleased]` sits above it and is
  never empty at release time.
- An entry says what was wrong, why (the mechanism), and what the fix
  makes impossible, not what files changed. Credit the reporter by
  login and cite `#N`.
- A number in an entry is pasted from an evidence file of the same run or
  read from `harness/thresholds.json`; a typed number is a finding for
  the reviewer.
- A tool added or removed is named in the entry (`scripts/surface_diff.py`
  checks the CHANGELOG diff for the name).
- A retired test's entry names the lesson and the replacement assertion
  (mirrors `harness/retired.json`, DoD 11).
- The `no-changelog` label exempts a PR and is printed in the check
  summary; docs-only, CI-only and test-only changes trigger nothing.
