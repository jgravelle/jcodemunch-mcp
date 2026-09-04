---
name: pr-description
description: "The PR title and body template, and the rules for any comment drafted for a reporter or contributor. Load when writing a PR body, a release PR, or a triage response."
---
# PR description and comment drafts

Authority: CLAUDE.md "Output Rules" and "Sentence shapes that read as
machine-written"; "Issue + release policy" 1, 2, 3a, 3b; the `spokesperson`
agent for anything outward-bound.

## PR title
`<type>: <what changed, one line>` with `(#N)` when it closes an issue;
`release: vX.Y.Z - <thesis>` for a release PR.

## PR body, in this order, headings as written
1. **What was wrong** (or missing): two to four sentences, the reporter
   credited by login.
2. **Why**: the mechanism, and whether it is a regression of a Standing
   lesson or an ARCHAEOLOGY row (name it).
3. **What is now impossible** (or possible): the property, not the
   instance; the other spellings checked.
4. **Evidence**: paste verbatim, never retype: `bench_table.md`,
   `surface.md`, `surface_descriptions.md`, the review verdict line.
5. **Definition of Done**: `checklist.md` verbatim.
6. `Closes #N` when applicable.

No number appears in 1-3 that is not in 4 or 5.

## Comment drafts (triage, close comments)
- One issue, one verdict; propose the split, do not merge findings.
- A timebox states the deadline AND the default in the same sentence,
  never longer than 24 hours, and never advertises an extension clause.
- Name the version the fix ships in; 43% of closes did not.
- Never answer a concrete pain with aggregate statistics.
- The draft goes through `spokesperson`; posting is the next layer's.
