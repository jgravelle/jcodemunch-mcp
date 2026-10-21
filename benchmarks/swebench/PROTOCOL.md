# SWE-bench Verified — protocol, registered before any instance is run

## STATUS: PARKED 2026-08-23, BEFORE ANY INSTANCE WAS RUN

**Nothing here was executed. There is no result, hidden or otherwise.**

⚠⚠ Stated at the top because a registered protocol with no result file is the
exact shape of a buried negative, and the honest reason is not a finding: the
setup cost was judged too high for the value. No host we own has the 120 GB the
harness wants (see the table below), the real run needs a rented box, and the
agent runs cost real money on a personal card.

⚠ **Nothing about the design was found wanting** — it is registered, the power
table is computed, the hosts are measured. If a lower-friction path to running
SWE-bench appears, this resumes from here unchanged; that is what a registered
protocol is for. The only thing missing is a reason to spend the money now.

---

## The question

Does an agent with jCodeMunch fix more real GitHub issues than the same agent
without it?

Every benchmark this project owns measures tokens saved or retrieval recall.
None measures whether the work got done. That gap is why the catalog moratorium
keeps grinding: `route` is judged on whether it picks the labelled action, when
the question a user has is whether the issue got fixed.

## What this pilot is, and what it may NOT conclude

**This is a 50-instance PRICING RUN. It publishes nothing, whatever it says.**

Registered here so a good-looking pilot cannot be quoted later. At n=50 the
design cannot resolve the effect it is looking for, in either direction:

    Paired design, McNemar exact, alpha=0.05, contested=30% (ASSUMED)

        n     +6pt    +10pt    +12pt
       50      7%     18%     27%
      100     16%     37%     52%
      150     22%     56%     73%
      200     29%     71%     86%
      300     43%     87%     96%
      500     67%     98%    100%

Printed by `power.py`, not typed. Re-run it if the design changes.

⚠⚠ **Read the top row before quoting anyone's 50-instance result, ours or a
competitor's: a real 12-point effect is detected 27% of the time.** A published
50-instance win is one draw from a distribution that hides three quarters of the
true effects, and a 50-instance NULL is nearly uninformative. That is the whole
reason this run is a pricing exercise and not a result.

⚠ `contested` is an assumption, not a measurement — the fraction of instances
where the arms could plausibly disagree at all. Measuring it is one of the four
outputs below, and it is what makes the real run's size a computed number rather
than a guess.

## What the pilot measures

Four things, none of them a solve rate:

1. **Dollars and wall-clock per instance**, per arm. The real run's size is a
   budget decision and there is currently no number under it.
2. **The contested fraction.** Replaces the assumption above.
3. **Per-instance index build time and cost.** Every SWE-bench instance sits at
   its own base commit, so this is hundreds of index builds across ~12 repos,
   not twelve.
4. **Peak disk.** See the blocker below.

## Design (fixed now, not after seeing results)

- **Two arms.** Claude Code cold, versus Claude Code + jCodeMunch. No third
  surface arm: `counter`-vs-`full` triples the cost to answer a question nobody
  outside this repo has asked.
- **Paired.** Both arms run the same instances. Pairing is what buys the power
  in the table; unpaired, every number above gets worse.
- **McNemar exact on discordant pairs.** Concordant pairs are not evidence.
- **k >= 2 repeats per instance per arm.** Agents are stochastic. A single pass
  reports one draw as if it were the value.
- **Grading is the official harness, unmodified.** `swebench eval verified`.
  That is the entire credibility of the number. We do not write a scorer.

## Setup cost is charged to us

Index build time and dollars count **against the jCodeMunch arm**, with the
figure reported separately beside the result.

⚠ The alternative — treating the index as pre-existing, like a warm cache — is
defensible for a returning user and is the first thing a skeptic reaches for.
Excluding our own setup cost is the kind of omission that gets found by someone
else later, and then the finding is about us rather than about the number.

## Pre-registration

The evidence of pre-registration is **commit order**: this file lands before any
instance is run and before any result file exists. See `git log` for this
directory. Nothing else can be evidence of it.

**We publish the result whichever way it goes.** A negative is a finding about
what jCodeMunch does not do, and this project has published one before
(`route_binary_pilot/RESULT.md`, H3 refuted).

⚠⚠ **The honesty gate that fired on `codex_surface` applies here unchanged**:
if the instrument cannot resolve the effect, we report that it could not, and we
do not report the arm numbers as though it had. That benchmark's arms differed
by 568,617 tokens against a baseline varying against ITSELF by 1,143,229, and
the arm numbers are still not quoted anywhere.

## Known blocker: storage

The harness documents **120 GB free storage, 16 GB RAM, 8 CPU cores** for
x86_64. Three candidate hosts were measured 2026-08-23:

| host | arch | cores | RAM | free disk |
|---|---|---|---|---|
| megaboxen3000 (dev box) | x86_64 | 24 | 34 GB | **45 GB** of 952 (96% used) |
| gravelles-mac-mini | arm64 | 10 | 32 GB | **27 GB** of 228 |
| prog-16 | x86_64 | ? | ? | not measured |

**Compute is fine everywhere. No host has the disk.**

⚠⚠ **The Mac was checked as the presumed rescue and is the WORST of the three
on the only axis that was blocking** — 27 GB against the dev box's 45. It also
carries an architecture caveat the dev box does not (below). Checking it cost
one command; assuming it would help would have cost the pilot.

### Cache level trades disk for time

| `--cache_level` | storage | speed |
|---|---|---|
| `none` / `base` | ~120 GB during run | slowest |
| `env` (default) | ~100 GB | moderate |
| `instance` | **~2,000 GB** | fastest |

⚠ Those are FULL-500 figures. A 50-instance pilot at `base` needs a fraction,
and no per-instance number is published anywhere — which is why peak disk is
one of the four things this pilot measures, and now the one that decides where
the real run is hosted.

### Where the pilot runs

**Grading: the dev box, x86_64 NATIVE, `--cache_level=base`.** Native x86 means
the official images with no architecture question, and it has both the most free
disk and the most cores.

⚠ **The agent runs need no Docker at all.** Docker is only the grader, so the
expensive half — the API spend — is host-agnostic and architecture never touches
the measurement we care about.

⚠ arm64 is documented as experimental and the known breakages (Java toolchain,
Chrome/JS dependencies) do not apply to SWE-bench **Verified**, which is
Python-only across twelve repos. One published comparison found 11 of 11
instances identical between native arm64 and emulated x86_64, with a single
sphinx discrepancy traced to a package version rather than the harness. **That
is n=11 and it is not a license to grade on arm** — it is the reason arm is a
fallback rather than a refusal.

⚠⚠ **The 200-to-300 instance run cannot happen on any machine we own.** Whatever
the pilot says about effect size, the real run needs a rented host. Deciding
that is cheaper before the pilot than after.
