# VERIFICATION — is the competitive tier trustworthy? (2026-09-06)

Phase 4 of the competitive brief, per DESIGN §10: each Phase 4 line maps
to a script flag or a test, and the skeptical-competitor review is written
by hand per axis, each argument either changing DESIGN.md or entering
FINDINGS.md as a known limitation. Branch `competitive/phase4-verify`,
stacked on `competitive/item7-command` (#619). Box: Windows 11 10.0.26200,
Python 3.12.4, 24 logical CPUs, Docker Desktop (WSL 2). Every number below
is read from a file named beside it; nothing here is typed from memory.

The tests named below: `uv run pytest tests/test_competitive_*.py -q` on
this tree, `147 passed, 1 skipped` (the skip is the sandbox-timeout test that
needs a docker daemon, `test_competitive_checks.py:296`).

## 1. Three runs on one commit → the result file's raw triples and `spread`

The recorded run `benchmarks/competitive/results/2026-09-06-0e3a1706.json`
(self + `lodash/lodash@f299b52` + `psf/requests@0e322af`, 11 adapters,
3 runs, container): 297 rows, 226 carrying three raw values under `runs`
(the rest are `NOT COMPARABLE` rows with no value to repeat). Per axis,
how the stability rule (DESIGN §5.1: spread within 10% of the row's own
median) graded them:

| axis | stable | unstable | n/a |
|---|---|---|---|
| calls_per_task | 33 | 0 | 0 |
| f1_P1 | 29 | 0 | 4 |
| f1_P2 | 26 | 0 | 7 |
| f1_P4 | 24 | 0 | 9 |
| f1_P5 | 0 | 0 | 33 |
| index_cold_seconds | 0 | 27 | 6 |
| latency_call_ms | 12 | 21 | 0 |
| tokens_per_task | 33 | 0 | 0 |
| tools_list_tokens | 21 | 0 | 12 |

149 rows are `meaningful`; no unstable row is (`meaningful` implies both
rows stable, checked over every row of the file). Two axes are unstable
almost everywhere on this box and say so: every `index_cold_seconds` row
(CF-14, the Windows bind mount) and 21 of 33 `latency_call_ms` rows
(CF-8, sub-millisecond medians against a 10% relative rule). Neither is
read as a finding against either side until a Linux runner produces three
runs (CF-53).

Repeatability of the instrument on our own rows: `/competitive-compare`'s
dry run (`docs/competitive/evidence/compare_dryrun_item7.md`, two commits
that differ by docs and one script) has 8 jcm rows with both sides, 6 of
them differing by exactly 0; the two that moved are the two unstable axes
above, and each moved by less than the current run's own spread on that
row (the `competitive_cur` result file's `spread` field).

Tests: `test_competitive_tier.py::test_band_and_meaningful_follow_the_harness_rule`
(the band is max(5% of our median, 3x the larger spread); stability is
judged before the band so a row's instability cannot widen the band it is
then measured against);
`::test_end_to_end_writes_a_valid_result_file_with_null_rows` (three raw
values per row on a real run of the nulls and jcodemunch).

## 2. Misconfigured adapter → the fairness note and the `cited`-empty rule

Every adapter's module header names `docs/competitive/fairness/<tool>.md`,
and the note is the thing the reviewer diffs against the Dockerfile and
the adapter's call plan on each adapter PR (the adapter PRs, #614 and its
stack, each reviewed to APPROVE with the note in the diff; the
round-1 findings on those PRs were fairness-note items: a follow-up call
uncharged, a default the README does not document).

The second half catches the tool that was silently not called:
`run.py` lists every adapter whose `cited` set is empty on every P task
of a corpus under `tools_not_called`, the row is `NOT COMPARABLE` there,
and `findings.py`'s first hypothesis for such a row is `tool_not_called`,
never a gap. The recorded run has 9 such entries. It caught our own
adapter first: CF-51 (our P2 answer asks the import-graph tool, which
returns zero references and says so in its reply), and PR 3a's round-1
review, which found the first FINDINGS draft counting those rows as wins
(CF-46's correction).

Tests: `test_competitive_checks.py::test_tools_not_called_names_only_the_silent_tool`;
per adapter, `test_competitive_<tool>.py` ("the call plan follows the
fairness note with every call charged"); `test_competitive_tier.py::test_every_shipped_adapter_satisfies_the_interface`.

## 3. Fabricated README → the feed and build paths over a fixture

Nothing a competitor publishes reaches a number. The build path: a result
file carries no `claims` field (D4), asserted by
`test_competitive_tier.py::test_end_to_end_writes_a_valid_result_file_with_null_rows`
over the schema, and `latest.md`'s header line "a competitor's README
figure is not on this page" is asserted by the same test over the
rendered summary.
The feed path: `feed.py` over a fixture whose release title carries a
capability word and whose body names an axis writes a `competitive-idea`
draft quoting the TITLE only, inside a fenced `data` block under the
inbound preamble read from POLICY.md, and a `rerun.json` record; a body
word alone never makes a draft; an unreadable registry is `unknown`,
never "no release".

Tests: `test_competitive_workflows.py::test_feed_cli_over_a_fixture_writes_feed_rerun_and_drafts`,
`::test_feed_rules_title_words_body_words_and_unknown`,
`::test_preamble_is_read_from_policy`, `::test_feed_source_repo_from_registry_metadata_only`;
`test_competitive_codegraph.py::test_the_captured_tools_list_is_the_readme_allowlist_and_the_default_is_one_tool`
(a README's tool list is what the tool's own `tools/list` reports, never
the README's count).

## 4. jcm-only task → the `capability_only` exclusion

`task_check.py` flags a task `capability_only` when fewer than two non-null
adapters declare its category and excludes it from every head-to-head
table; the rule is symmetric, so a task only one competitor can answer is
excluded the same way. The recorded run lists 0 such tasks: every task
file's categories are P1/P2/P4/T, declared by more than one adapter.

Tests: `test_competitive_checks.py::test_split_is_symmetric_and_needs_two_answerers`,
`::test_check_refuses_absent_file_and_tool_words` (a query naming a
jCodeMunch tool, a symbol id or a `_meta` field is refused);
`test_competitive_tier.py::test_task_check_refuses_absent_expected_files_and_tool_naming_queries`.

## 5. Inside and outside the band → `findings.py` over synthetic result files

A `competitive-gap` draft is written only for a `meaningful` row where jcm
is behind (never ahead, never unstable, never our own row, never a CLI's
zero schema cost); `competitive-watch` only when ahead and `narrowed` on
two consecutive runs; `standard-proposal` only on two runs past the Target
read verbatim from STANDARD.md, never a Floor. The recorded run produced
88 gap drafts in draft mode and 7 of them are a null baseline ahead of us
(CF-56): the rule drafts what it sees, and the human on the ledger decides.

Tests: `test_competitive_findings.py::test_gap_draft_only_for_meaningful_rows_where_jcm_is_behind`,
`::test_hypothesis_rule_from_the_fixed_list`, `::test_watch_needs_ahead_and_narrowed_twice`,
`::test_standard_proposal_reads_the_target_verbatim_and_never_a_floor`,
`::test_module_never_posts`; `test_competitive_trend.py` (the four movement
classes and the two refusals).

## 6. De-duplication → a fixture open-issue list with the fingerprint

Every draft carries `competitive-id: <label>/<axis>/<tool>/<corpus>`. Over
a fixture issue list: an open issue with the fingerprint updates the draft
in place (a dated block appended, the head kept); a closed one does not
block and the draft names it; an unreadable tracker refuses to draft. The
only `gh` verb in the module is `issue list`.

Tests: `test_competitive_findings.py::test_dedupe_and_draft_files`,
`::test_cli_refuses_when_the_tracker_is_unreadable_and_no_list_is_given`;
`test_competitive_workflows.py::test_ledger_merge_keeps_a_humans_head_and_appends_a_dated_block`.

## 7. Kill switch → the inbound workflow tests over `competitive-*.yml`

The three workflows are graded by the inbound layer's tests, extended:
actorless triggers; the read-only token everywhere but the switch read and
the ledger push; every write preceded by a switch read in its own job; the
competitor job without an App token and without a write; no write on the
never-touch list; pushes to `inbound-ledger` only; timeouts from the budget
rows; the post job reading both `INBOUND_ENABLED` and
`COMPETITIVE_POST_ENABLED` before the gate and again before the write; the
feed dispatching at most one re-run, once per release. The variables and
labels do not exist, so none of the three can act (CF-57).

Tests: `test_competitive_workflows.py` (19 tests, every one named above by
subject: `test_actorless_triggers_and_read_only_token`,
`test_every_write_follows_a_kill_switch_read_in_its_job`,
`test_the_competitor_job_holds_no_app_token_and_writes_nothing`,
`test_no_write_touches_the_never_touch_list_and_pushes_go_to_the_ledger_only`,
`test_timeouts_match_the_budget_rows`, `test_the_ledger_job_re_reads_the_switch_before_the_artifact_download`,
`test_post_reads_both_switches_before_the_gate_and_before_the_write`,
`test_post_refuses_without_both_switches`, `test_feed_dispatches_at_most_one_rerun_and_only_with_the_app_token`,
`test_feed_does_not_redispatch_a_rerun_an_earlier_feed_recorded`, and the
rest); `test_inbound_plumbing.py` (the budget rows and POLICY §7).

## 8. The skeptical-competitor review, per axis

Written as the argument a competitor's maintainer would make on reading
`latest.md`, then what the tier does about it. Each argument ends in one
of two places: a DESIGN.md edit, or a FINDINGS.md row that the summary
and any draft must carry. Nothing here softens a row where we lose.

**tokens_per_task.** *"You charge my search hits but not the file read my
user does next with their own tool; or you charge my whole pattern
output for a query you chose."* Both true, both recorded: CF-24
(code-review-graph has no read-source tool, so its token row is three
hits and the body is read elsewhere, uncharged) and CF-29 (serena's T
tasks charge every match of an alternation the harness built). The rule
is "charged as returned" (DESIGN §5.1) and the caveat travels with the row
in the summary and in any draft (CF-24's instruction to item 4). *"cl100k
is not my tokenizer."* One tokenizer on every row, named in every header;
the number is the cost to the agent's context, not the tool's own
accounting. *"Your T tasks have no ground truth."* Correct, and they are
scored for nothing but tokens (DESIGN §4.1). *Disposition: FINDINGS
limitations CF-24, CF-29; no DESIGN change.*

**f1_P1, f1_P2, f1_P4.** *"Your tasks were written by the person who wrote
your adapter."* True for every adapter and every task file, and it cannot
be otherwise with one author; CF-47 records it, the mitigations (a third
party's rules for three corpora, generated expected sets for the rest, the
reviewer told on every task PR), and that a second author is a human's
step. *"My P2 lead is against a misconfigured you."* Also true: CF-51,
our adapter asks the import-graph tool for reference finding and scores
0 on every corpus; the row is recorded as measured, the hypothesis on it
is `tool_not_called`, and the fix is the next jcodemunch adapter PR.
*"Your line tolerance rewards a dense citer."* One-to-one matching
(DESIGN §5.1): each expected line takes the nearest still-unmatched cited
line, so grep's every-match answer is paid once per hit and charged in
precision for the rest. *"Your gold for P4 is bigger than my answer can
be."* CF-33: a tool that prints eight names and `+K more` is capped at
eight citations on a 29-file gold, and the shape travels with the row.
*Disposition: FINDINGS CF-47 (open, needs a human), CF-51 (open, ours to
fix), CF-33 (caveat on the row); no DESIGN change.*

**calls_per_task.** *"A tool that reads everything makes one call; counting
calls rewards it."* Yes: the read-all null is ahead of us on this axis by
construction and CF-56 says a draft from that row says nothing a user
should act on. The axis is reported beside tokens and never alone.
*Disposition: FINDINGS CF-56; no DESIGN change.*

**latency_call_ms.** *"Our calls do different work."* DESIGN §5.1 says so on
every table: the median wait per call, not a like-for-like operation.
*"Your box penalises anything that touches the bind mount."* CF-14 and
CF-29 (disadvantage 7): true, and the axis is unstable on 21 of 33 rows
here, so it cannot be `meaningful` on this box; CF-8 records that a
sub-millisecond tool can never win this axis under a relative rule, and
whether an absolute floor in ms belongs beside it is answered from three
runs on a Linux runner, not here. *Disposition: FINDINGS CF-8, CF-14
(open, Phase 4 on a runner: CF-53); no DESIGN change.*

**index_cold_seconds.** *"Your container makes you 6x slower than your own
host; the number is the box's."* CF-14 says exactly that, from our own
row; every row of the axis is unstable in the recorded run and none is
read as a finding for or against anyone. *Disposition: FINDINGS CF-14; no
DESIGN change.*

**tools_list_tokens.** *"A CLI has no schema; calling that an advantage is
not a comparison."* DESIGN §2 reports CLI tools as `interface: cli`, 0
schema cost, and says it is a real advantage; `findings.py` never drafts
a gap from it. *"My 30 tools are a default my users trim."* We measure the
documented default configuration at the pinned release (D-rules, DESIGN
§1), because that is what a user gets; a trimmed surface is a different
pin. *Disposition: no change.*

**breadth (criterion 10).** *"You never index my language."* The claimed
count is reported beside the measured count of corpus files the tool
indexed (`files_indexed`), and the measured one is the honest one; CF-15
found the difference cutting against us (cymbal indexes 277 files where
our default withholds two large ones). *Disposition: FINDINGS CF-15; no
DESIGN change.*

**one-file reindex (3b) and install friction (6).** *"You dropped the axes
where I win."* DESIGN §2 lists both as COMPARABLE and neither is measured:
`score.py`'s axes are the seven above, no adapter implements `reindex_one`,
and image build seconds are in the build logs, not in a result file. A
design that names an axis it does not measure invites exactly this
argument. *Disposition: DESIGN §2 now marks both "designed, not measured
(CF-61)"; FINDINGS CF-61 records the gap and what measuring each would
take.*

**The set and the box.** *"You chose the competitors."* FIELD.md names the
selection rule and the eight; a tool outside it is a FIELD edit, not a
row. *"Everything ran on one Windows workstation."* Yes, and CF-53 records
that the full set did not fit the budget here and that the runner
measurement is pending; every result header names the runner. *"Your own
tree is your corpus."* It is one of three in the recorded run, and the P2
zero (CF-51) is on all three; the corpus check (§3) refuses a set that is
one language or one domain.

## 9. What Phase 4 did not do

No three-run measurement on a Linux runner (CF-53): the two unstable axes
stay unread until then. No second author for the authored task set (CF-47).
The jcodemunch adapter's P2 mapping (CF-51) and the variant rows (CF-54)
are the next adapter PR. The scheduled jobs have not run (CF-57: the
variables and labels are a human's, RUNBOOK §10 in Phase 5).
