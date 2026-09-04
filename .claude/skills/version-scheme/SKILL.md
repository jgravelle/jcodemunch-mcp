---
name: version-scheme
description: "How the next version number is derived and what a bump touches. Load in /release step 2 or before any version edit."
---
# Version scheme

Authority: `docs/cicd/RUNBOOK.md` section 1, `scripts/release_preflight.py`
(`read_pins`, `pins_verdict`), `tests/test_release_preflight.py`.

- Scheme: `1.108.N`; a release is `N + 1`. Minor or major moves are a
  human decision with a stated reason (`/release --minor|--major <reason>`).
- Derivation, printed every time: latest tag
  `git for-each-ref refs/tags --sort=-v:refname --format='%(refname:short) %(creatordate:iso-strict)' | head -1` must equal
  `pyproject.toml`'s `version`. Unequal means a bump is already pending
  (refuse) or a tag is missing (the pipeline tags; see the runbook).
- Pin sites are ENUMERATED BY GREP for the old version
  (`--include=*.json --include=*.toml --include=*.lock`), never listed
  here: the list grew from two to seven and a listed copy was wrong each
  time it grew. `git check-ignore` every hit; a generated file is not a
  pin site. `whatsnew.json` is regenerated (current + entry), not edited.
- `git tag <name>` is DENIED in a session (the pipeline tags); the read form above is the one to use.
- `uv.lock`: only the name-scoped `version =` line; never `uv lock` to
  bump (a newer local uv rewrites platform markers; CLAUDE.md).
- The pre-flight is the authority on whether the pins agree:
  `uv run python scripts/release_preflight.py --version X --pins-only`.
