# Contributing to jCodeMunch-MCP

Thanks for your interest in contributing! A few things to know before you submit a PR.

## Contributor License Agreement

This project is dual-licensed: free for non-commercial use, with paid licenses
for commercial use. To keep that model legally sound, **all contributors must
sign the CLA before their PR can be merged.**

The CLA is short and plain-English: you keep your copyright, you grant the
project the right to sublicense your contribution commercially, and you confirm
the work is yours to submit.

**[Sign the CLA](https://cla-assistant.io/jgravelle/jcodemunch-mcp)**

CLA Assistant will prompt you automatically when you open a PR. It takes about
30 seconds.

### The signing window is 24 hours

Once your PR is reviewed and green, you have **24 hours** to sign. If the CLA is
not signed by then, we implement the fix ourselves and credit you in the
CHANGELOG, the release notes and the close comment.

So the window decides whose commit it is. It does not decide whether you are
credited, and it does not decide whether your fix ships — both of those are
settled the moment the fix is right. We keep it short because a signed CLA takes
30 seconds and an unsigned one parks finished work behind a form.

If 24 hours does not work for you — you need legal review, or you are away — say
so on the PR and we will hold it. The clock exists to stop PRs going quiet, not
to catch anyone out.

## Commercial Licensing

If you're using jCodeMunch in a commercial context, see the [license section in the
README](README.md) for options.

## Getting Started

Dev dependencies are declared in a PEP 735 `[dependency-groups]` block, not an
optional-dependencies extra, so there is no `.[test]` or `.[dev]` to install.

```bash
git clone https://github.com/jgravelle/jcodemunch-mcp
cd jcodemunch-mcp

# with uv
uv sync
uv run pytest tests/ -q

# or with pip
pip install -e . pytest pytest-asyncio pytest-cov
PYTHONPATH=src python -m pytest tests/ -q
```

Run the suite with `PYTHONPATH=src`. An installed copy of the package from PyPI
will otherwise shadow `src/`, and you will be testing the released code instead
of your change.

## Guidelines

- Open an issue before starting large features. Saves everyone time if direction needs discussion.
- Keep PRs focused; one feature or fix per PR
- Include tests for new functionality
- Run the full test suite before submitting

## One issue, one verdict

**An issue should be a single thing that can be judged true or false and then
closed.** If your report contains several independent findings, please open
several issues, or say so plainly and we will split it at triage.

This is not a request for less detail. Detailed, adversarial, multi-part reports
are some of the most valuable things this project receives, and none of the
scope gets dropped in a split; every part keeps its own thread, its own
reproduction, and its own credit.

It is about how they close. A report with four findings closes only when the
last one is settled, so three finished fixes sit behind one unfinished
conversation and the tracker cannot tell anyone which is which. Split into four,
three close within a day and the fourth is visibly the only thing outstanding.
That is better for you as well: your finished work ships instead of waiting.

What we do at triage:

- Split a multi-finding report into one issue per finding, cross-linked, credit
  on each.
- Keep the original as the parent only if it still has its own verdict. If it is
  purely an index of the others, we close it and say so.
- Accepted design work with no start date does not stay open as an issue at all.
  It moves to the roadmap with its close condition verbatim and its author
  credited. Parking is not rejection, and the roadmap says so.

## A release is never blocked on an open issue

**We do not hold a release hostage to an unfinished verification, including a
verification we asked for.**

When work is done, tested, and green, it ships on schedule. If review or
independent re-verification is still outstanding, the release says so in plain
language rather than waiting:

> Verified against the reviewer's pre-registered harness at a frozen SHA. Not
> independently re-verified by its author.

That wording is deliberately weaker than a sign-off and we will not blur the two
in a changelog. When the re-verification lands, whenever it lands, it counts in
full and we announce it retroactively. Nothing expires.

Every timebox we set names its default action, because a date with no stated
consequence is a wish. "Verification by X, or Y ships with disclosure Z."

**No timebox we offer runs longer than 24 hours.** That applies to all of them,
not only the CLA window above: signing a form, opening a PR you have already
written, or taking an issue you want to implement. At expiry the default action
fires — usually that we do the work ourselves — and you are credited in the
CHANGELOG, the release notes and the close comment either way.

The short clock is only fair because of that last sentence. It decides whose
commit it is. It never decides whether you are credited, and it never decides
whether the fix ships.

If 24 hours does not fit — you want the weekend for it, you are away, the change
is large — **say so and we will hold it.** An extension you ask for is not the
same as a default we hand out, and we would rather you told us than went quiet.
Timeboxes already posted are honoured as posted; we do not shorten a promise
after making it.

The point of this rule is that a reviewer's thoroughness should never become a
veto. If being careful can stall a release, then careful review is expensive to
accept, and that is the opposite of what we want. This way your findings are an
upgrade that can arrive at any time, and neither of us is negotiating under a
clock.

## Catalog moratorium: new top-level actions are paused

**jCodeMunch is not accepting new top-level catalog actions right now.** This is
not a judgement about any particular idea, and it is not permanent. The
reasoning, and the conditions that end it, are written down so you can hold us
to them.

The catalog is 91 actions. Under the default `counter` surface an agent reaches
them through `route`, and `route` proposes the right action for a plain-language
task **45.8% of the time at rank 1** (`benchmarks/route_recall/`). An action
`route` never proposes is functionally absent — it still costs a schema, a
documentation obligation, a compatibility promise under 1.x, an output contract
and a test matrix, and it still competes with 91 siblings for the same ranking.
Issue #397 is the sharp end of this: generated `CLAUDE.md` named 25 tools while
the server exposed 6, so the policy meant to make jCodeMunch useful instructed
the agent to call tools that were not there.

**The moratorium lifts when all three hold:**

1. `route@1` reaches **60%** on `benchmarks/route_recall/queries.json`
   (baseline **45.8%**);
2. mean name leakage at that measurement stays at or below **0.15** — a recall
   bar with no leakage bar is trivially met by writing queries that paraphrase
   tool descriptions, so both move together or neither counts;
3. generated guidance references only actions callable under the active surface.

All three are enforced by `tests/test_catalog_moratorium.py`, including a pinned
ceiling on the catalog size. **We hold ourselves to this first:** three
capabilities we built and tested are deliberately not exposed —
`investigate_deletion_safety` (v1.108.214, 19 tests) and the retrieval
counterfactuals (v1.108.217) are importable and unregistered. They do not jump
the queue merely because we wrote them.

### What this means for your PR

- **A new action** will be asked to wait, or to land as a **parameter or mode on
  an existing action** instead. That is usually the better shape anyway.
- **Everything else is unaffected** — bug fixes, language support, new
  parameters, output fields, performance, docs, and tests are all as welcome as
  ever. Most merged contributions have never touched the catalog count.
- **If your action is genuinely warranted**, say so in the PR. The ceiling is
  one constant in one file; raising it in the same commit is allowed, and the
  visible diff is the whole point. We would rather have the conversation than
  pretend the rule decides it.

Route-recall work is the fastest way to end this, and
`benchmarks/route_recall/explain_misses.py` prints the current defect list with
each miss labelled by the gate that caused it.

## Quality gates that run on every release

- **Schema budget** — `tests/test_schema_budget.py` fails when `tools/list` token count grows more than 5% above `benchmarks/schema_baseline.json`. If you intentionally grow the schema (new tool / longer description), regenerate the baseline in the same PR with justification:
  ```bash
  PYTHONPATH=src python benchmarks/harness/capture_schema_baseline.py
  ```
- **Retrieval-quality replay (v1.76.0+)** — `benchmarks/replay/run_replay.py` runs golden queries through `search_symbols` and reports nDCG@10 / MRR@10 / Recall@10 against the locked v1.75.0 baseline. Any aggregate metric drop > 2% fails the gate:
  ```bash
  PYTHONPATH=src python benchmarks/replay/run_replay.py \
      --fixture benchmarks/replay/fixtures/self_v1_75_0.json \
      --baseline 1.75.0 --gate 0.02
  ```
  If your change legitimately moves a metric, capture a new fixture/baseline and document the reason in the PR description.
