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
| L-03 | 2026-09-25, #759 | Enum members are not indexed. #759 ruled (jjg, 2026-09-25) that an enum member is a `constant` owned by its enum in every language, and shipped PHP. `tests/test_enum_members_register.py` pins which languages do and don't: on 2026-09-25, 3 of the 37 enum-bearing languages index members (AL, PHP, Python). 30 index only the enum (its `ENUM_ONLY` set, including TypeScript, Java, C#, Rust, Kotlin and Swift). The register fails when a language starts indexing its members, so this row shrinks with it. | `src/jcodemunch_mcp/parser/languages.py`, each `*_SPEC` | defect | medium | OPEN |
| L-04 | 2026-09-25, #759 | With the register's samples, Fortran (`enum, bind(c)`), Julia (`@enum`), Objective-C (`NS_ENUM` and plain `enum`) and SQL (`CREATE TYPE ... AS ENUM`) produce no enum symbol at all, not even the enum. Unverified: the sample may be what fails, so check each grammar's parse before fixing. Review of #759 found two more shapes with the same result, in languages the register classes as enum-only by their file-scope sample: Verilog `typedef enum { A, B } E;` inside a `module` or `package`, and Verse's inline `E := enum{A, B}`. `NO_ENUM` in `tests/test_enum_members_register.py`. | `src/jcodemunch_mcp/parser/languages.py` (those four specs) | defect | low | OPEN |
| L-05 | 2026-09-25, #843 | A Nim `var`/`let`/`const` section binding several names, or an exported one, publishes ONE constant named after the whole name list: `var x*, y: int` yields a single symbol named `x*, y`, and neither `x` nor `y` is findable by name. `_parse_nim_symbols` takes the text of `symbol_declaration_list` and strips a trailing `*`, where #812's object fields walk each `symbol_declaration` and unwrap `exported_symbol`. | `src/jcodemunch_mcp/parser/extractor.py` `_parse_nim_symbols` (the `var_section` branch) | defect | medium | OPEN |
| L-06 | 2026-09-25, #843 | A Nim routine declared inside another routine is not indexed: `proc outer*() =` with a nested `proc inner(x: int) = ...` yields only `outer`. The routine branch of `_parse_nim_symbols` returns without walking the body, and no routine ever sets `scope`, so a nested routine has neither a symbol nor an owner. | `src/jcodemunch_mcp/parser/extractor.py` `_parse_nim_symbols` (routine branch `return`) | defect | low | OPEN |
| L-07 | 2026-09-25, #844 | A C++ out-of-class member definition loses its owner: `class A { int run(); };` then `int A::run() { ... }` yields `A.run#method` for the declaration and a bare `run#function` (no `parent`) for the body. `_extract_cpp_name` keeps only the `name` child of a `qualified_identifier`, so the scope is discarded, and the body cannot be found as `A.run`. Pascal (#844) and Objective-C index the body as a `method` of its class under the shared qualified name. | `src/jcodemunch_mcp/parser/extractor.py` `_extract_cpp_name`, `_find_cpp_name_in_subtree` | defect | medium | OPEN |
| L-08 | 2026-09-25, #844 | A Pascal forward class declaration (`TA = class;`) is indexed as a `class`, a twin of the full declaration, so the two become `TA#class~1` and `~2`. The declaration's members follow the full one by containment, but an implementation-section body (`procedure TA.M`) sits inside neither twin and #821's rule gives it no `parent`. A forward declaration has no body and is not a second class. | `src/jcodemunch_mcp/parser/extractor.py` `_parse_pascal_symbols` (`declType` branch) | defect | low | OPEN |
| L-09 | 2026-09-25, #844 | A Pascal record's kind depends on how the grammar spells it, not on what it is: `TRec = record X: Integer; end;` is `declRecord` and indexes as `type`, while the same record with a method, or generic (`TPair<K, V> = record`), is `declClass` and indexes as `class`. One Delphi concept, two kinds. | `src/jcodemunch_mcp/parser/extractor.py` `_parse_pascal_symbols` (`declType` branch) | defect | low | OPEN |
| L-10 | 2026-09-25, #844 | A Pascal implementation body whose owner name is shared by arity twins (`TList` and `TList<T>`, both named `TList`) has no `parent`: `procedure TList.Add` and `procedure TList<T>.Add` both resolve to the name `TList`, which names two ordinal twins, and #821's containment rule finds neither containing the body. The body's own `genericTpl` says which twin it means, and the reader drops it. Same mechanism as L-08. | `src/jcodemunch_mcp/parser/extractor.py` `_parse_pascal_symbols` (`defProc` owner lookup) | defect | low | OPEN |
