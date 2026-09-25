# Findings ledger: defects a session finds on the way

**Rule (jjg, 2026-09-25).** When a session finds a defect or an improvement
while fixing something else (a reviewer probe, a split finding, a follow-up it
noticed), it adds a row here. It does not open a GitHub issue. A GitHub issue
is opened only for:

- a defect a user reported, or
- a user-visible wrong answer that jjg picks for the tracker.

**Why.** Measured on 2026-09-25 with
`gh issue list --state all --search "created:>=2026-09-11"` and the matching
`closed:` query:
- 121 issues were opened. jjg's account filed 109 of them, and sessions wrote
  those. Users filed 5.
- 99 issues were closed.
- Between 11:19Z and 15:10Z on 2026-09-25, one session filed 9 issues (#869,
  #871, #872, #874 to #879). 3 issues closed in that window (#719, #725,
  #726).

Policy 1 split each review finding into its own issue, so the fix loop kept
creating its own backlog.

**What this is not.**
- It is not a place to hide a defect. A row stays until the defect is fixed
  or jjg rules it won't be.
- It does not replace policy 1. Split a REPORTER's issue one finding per
  issue, as before.
- It does not replace the process logs. `docs/*/FINDINGS.md` records defects
  in the harness, the workflows, the inbound layer and the competitive tier.
  This ledger records defects in the PRODUCT (and in tests that guard it).

**How to add a row.**
1. Use the next `L-` number.
2. Say what is wrong in one sentence, and give the smallest reproduction you
   have.
3. Name the file and symbol.
4. Give the kind: `defect`, `enhancement` or `test`.
5. Give the severity:
   - `high`: wrong answer or destructive advice.
   - `medium`: misleading, but it discloses itself.
   - `low`: cosmetic or latent.
6. Name the PR or issue that found it.
7. Mention the row in that PR's body.

**How to close a row.** Set Status to `FIXED <date> (<PR>)`, or to `WONTFIX`
with jjg's reason. Keep the row. To promote a row to an issue, set Status to
`FILED #N`. Promote only when jjg picks it.

| ID | Found | Finding | Where | Kind | Severity | Status |
|---|---|---|---|---|---|---|
| L-01 | 2026-09-25, #715 | `assemble_task_context`'s tectonic stage keeps only anchor, file count, cohesion, directory and nexus flag for each plate. It drops `coupled_to`, the pairwise plate coupling that `get_tectonic_map` has already computed. A task capsule can't say which clusters the task's plates depend on. | `src/jcodemunch_mcp/tools/assemble_task_context.py` `_stage_tectonic` | enhancement | low | OPEN |
| L-02 | 2026-09-25, #715 | A test that runs `git` with `text=True` and no `encoding` decodes the output with the locale codepage (cp1252 on Windows). cp1252 leaves five bytes undefined (0x81, 0x8D, 0x8F, 0x90, 0x9D), so any UTF-8 character with one of them in its encoding fails to decode. The failure raises in the reader thread and `stdout` comes back `None`. #715 fixed `test_retirement_ledger._git`: a `”` (U+201D, which contains 0x9D) crashed it. `grep -nE '"git"' tests/*.py \| grep text=True \| grep -v encoding` finds 13 more single-line call sites. Most read ASCII output from fixture repos, so the risk is latent. A call that reads this repo's own diffs or file content is the exposed shape. | `tests/*.py` (`subprocess.run(["git", ...], text=True)`) | test | low | OPEN |
| L-03 | 2026-09-25, #759 | Enum members are not indexed. #759 ruled (jjg, 2026-09-25) that an enum member is a `constant` owned by its enum in every language, and shipped PHP. A 2026-09-25 probe of `parse_file` found 16 languages that publish only the enum: TypeScript, Java, C#, Rust, Kotlin, Swift, Dart, Zig, PowerShell, C, C++, Scala, F#, Solidity, GraphQL and Protobuf. Python (`E.A`) and PHP (`E.A`) index them. Reproduction: `enum E { A, B }` in each grammar's spelling yields `[('E', 'type')]`. The Dart, Zig and PowerShell pins fail when their variants arrive. | `src/jcodemunch_mcp/parser/languages.py`, each `*_SPEC` | defect | medium | OPEN |
