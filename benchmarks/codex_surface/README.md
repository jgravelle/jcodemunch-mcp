# Codex tool-surface arms

Measures jcodemunch's net token effect on Codex CLI, and separates the two
terms that net out to a single percentage.

Motivated by [this r/codex benchmark](https://www.reddit.com/r/codex/comments/1vjfepe/almost_all_token_saving_tools_dont_seem_to_work/),
which measured jCodeMunch at **+28.45% on Codex** and **-3.34% on OpenCode**.
Two numbers worth reading together: on Codex we cost tokens, and on OpenCode we
saved the least of any tool measured. One mechanism can produce both.

## The hypothesis

The tool-schema payload is a **fixed** per-request cost. Retrieval savings are
**proportional** to how much the agent would otherwise have read. On a fat
baseline the savings dominate; on a lean one the fixed cost does. Codex is lean.

The fixed term is measured and needs no API credits:

```
$ python run_codex_arms.py --surface-only

arm               tools      bytes    tokens
----------------------------------------------
baseline              0          0         0
full                 90    107,276    24,007
full+policy          90    107,276    24,007
counter               6      4,533     1,030
```

Installing jcm at the default `full` surface costs a Codex user **24,007 tokens
in every request**, for the whole session. `tool_surface: "counter"` removes
95.7% of that. `counter` is already the default for fresh installs; `full` is
the default for configs created before it existed, and `upgrade_config` will not
move anyone, so long-time users are on the expensive setting.

The savings term needs live runs. That is what the arms are for.

## Result, 2026-08-10: NEGATIVE. Do not quote the arm numbers.

First full run, 4 arms x 3 repeats x 6 steps on FastAPI at `a9134f62`. The
honesty gate fired:

```
arm            schema tok     median        min        max   vs base
baseline                0  2,247,575  1,428,363  2,571,592    +0.0%
full               24,007  2,060,389  1,868,660  2,142,615    -8.3%
full+policy        24,007  2,507,796  2,417,364  4,729,117   +11.6%
counter             1,030  2,816,192  1,903,373  2,913,121   +25.3%

baseline within-arm spread: 1,143,229 tokens (50.9% of median)
```

Largest arm difference is 568,617 tokens; the baseline varies against itself by
1,143,229. **Every effect is inside the noise.** The directions are incoherent
as well: `full` carries 24,007 tokens of schema and came out cheaper than
baseline, `counter` carries 1,030 and came out most expensive. Both backwards
from the mechanism. That is what noise looks like.

Three metrics were checked from the same saved run. Best within-arm spread was
11% (uncached input+output on one arm), worst 139%. None resolve a 24k effect.

### Why the design cannot see it

Summing per-invocation input across a RESUMED conversation counts accumulated
context on every step: step 6 alone costs 500-700k because the whole transcript
is resent. The total is therefore dominated by how much the agent happened to
read early on, which compounds across steps. A fixed 24k difference is invisible
underneath it.

### The finding that outlived the arms

**86% of baseline input is cached** (1,938,176 of 2,247,575). The tool-schema
block is stable across requests, so it is paid at full rate roughly ONCE and at
cache-read rates thereafter. Any framing of "24,007 tokens in every request" is
wrong, and this repository said exactly that before measuring. The fixed-cost
term is real and much cheaper than the raw number implies, which makes it a
WEAKER explanation for the r/codex result, not a stronger one.

### The cache-rate cut does not rescue it either

Tried 2026-08-27, prompted by CacheRouter (arXiv 2608.22708), which reports
cache-hit rate rather than input totals. The saved run already carries
`cached_input_tokens`, so this needed no new API spend. It does not help.

| arm | schema | hit% (all steps) | step-1 input, each repeat | step-1 uncached |
|---|---|---|---|---|
| A baseline | 0 | 89.8% | 49,769 / 64,351 / 65,480 | 8,553-16,072 |
| B full | 24,007 | 89.9% | 64,662 / 66,855 / 91,861 | 15,254-31,445 |
| C full+policy | 24,007 | 90.4% | 77,497 / 107,574 / 171,065 | 12,985-33,081 |
| D counter | 1,030 | 92.3% | 78,978 / 80,522 / 85,646 | 16,014-17,034 |

Step 1 is the turn where the schema block is the largest share of the prompt and
nothing has accumulated yet, so it is the best case for the cut. `full` sits
2,504 tokens above baseline while carrying 24,007 tokens of schema, and
`counter` sits 16,171 ABOVE baseline while carrying 1,030. Same incoherence as
the totals, at a tenth of the magnitude. Within-arm spread (15k-27k on the
step-1 medians) still swamps the effect.

⚠ **Hit RATE cannot separate these arms by construction.** It is a ratio, and
adding a stable prefix raises numerator and denominator together — a bigger
cached block makes the rate go UP, so `counter` scoring highest at 92.3% says
nothing about which arm is cheaper. Any future paper reporting hit rate invites
this same cut; the answer is that the arms are still the arms.

⚠ One thing is real and is about variance, not level: `counter`'s step-1
uncached input spans **1,020 tokens across three repeats**, against 7,519 for
baseline and 16,191 for `full`. A small fixed surface is more PREDICTABLE per
turn. n=3 makes that an observation, not a result.

### What it would take

More repeats (n=3 against 50% variance resolves nothing), or a task flow with a
deterministic number of tool calls, or measuring per-request rather than
end-to-end. The `--surface-only` number needs none of this: it is exact.

## Arms

| arm | MCP | surface | AGENTS.md | isolates |
| --- | --- | --- | --- | --- |
| A | no | n/a | no | baseline |
| B | yes | `full` | **no** | schema cost with no routing policy |
| C | yes | `full` | yes | whether the policy changes tool use |
| D | yes | `counter` | yes | schema cost removed |

**B vs C** is the question of whether the tools get used *instead of* native
search or *in addition to* it. jcm's routing policy ships in `CLAUDE.md` for
Claude Code and in `AGENTS.md` for Codex and OpenCode, written by
`jcodemunch-mcp init`. Installing the MCP server alone does not create it. An agent
with 90 unexplained tools and no policy plausibly greps *and* calls them.

**C vs D** is the schema tax on its own.

If D lands near or below baseline while B is well above it, the r/codex result
is a configuration story and the fix is a default change. If D is still above
baseline, the fixed-cost hypothesis is wrong and the answer is somewhere else.
Either outcome is worth having.

## Running it

```bash
python run_codex_arms.py --preflight                    # cheap, catches everything below
python run_codex_arms.py --surface-only                 # no credits needed
python run_codex_arms.py --repo /path/to/pinned/clone --model gpt-5.1-codex
```

Nothing touches the operator's real `~/.codex`. Auth and config are isolated via
`CODEX_HOME`, jcm storage via `CODE_INDEX_PATH`, and each arm gets a fresh copy
of the target tree so `AGENTS.md` cannot leak between arms. All arms run
`--sandbox read-only`.

## Known blockers, all hit on 2026-08-10

These are why this harness exists unrun. **Both auth paths are blocked, and
differently.** Preflight checks for all of it.

### API-key auth

**1. The account had no API credits.** Every model returned
`You have no credits remaining`. No static check catches this, which is why
preflight spends one real token on a probe.

**2. The `*-codex` models are not served to API keys at all.** `gpt-5-codex`,
`gpt-5.1-codex` and `gpt-5.2-codex` all appear in `/v1/models` and then 404 on
`/v1/responses` with `Model not found`.

### ChatGPT-account auth

**3. Every codex model is refused for a ChatGPT account on CLI 0.98.0.**

```
{"detail":"The 'gpt-5.2-codex' model is not supported when using Codex
           with a ChatGPT account."}
```

Identical for `gpt-5.1-codex`, `gpt-5.1-codex-max`, `gpt-5.3-codex` and the CLI
default. Three things this is **not**, each checked rather than assumed:

- Not billing. The token's claims show `chatgpt_plan_type: plus` with
  `chatgpt_subscription_active_until` in the future and
  `chatgpt_subscription_last_checked` the same day.
- Not this harness's isolation. The same probe fails identically against the
  operator's real `~/.codex`.
- Not a stale login. It appeared immediately after a fresh `codex login`.

That leaves the CLI version or an entitlement change as the candidates, and this
harness cannot tell them apart. **Fastest discriminator: run `codex` interactively.**
If the TUI works, the problem is specific to `codex exec`; if it fails the same
way, it is the account/CLI pairing and no harness change helps.

### Restoring auth once that is resolved

```bash
CODEX_HOME=benchmarks/codex_surface/.codexhome codex login
# or, to reuse an existing login:
cp ~/.codex/auth.json benchmarks/codex_surface/.codexhome/auth.json
```

The isolated home is deliberately left empty between runs, because it holds a
live credential. Isolation matters beyond hygiene: the operator's real
`~/.codex/config.toml` enables eight plugins (chrome, browser, documents, pdf,
spreadsheets, presentations, template-creator, computer-use), which would load
into every arm and inflate the baseline the arms are measured against.

`.codexhome/` and `results/` are gitignored. The first holds a live auth token,
the second holds raw transcripts.

## Before quoting any number from this

- **Pin the target commit.** `tasks.json` ships `PIN_ME` and the harness warns.
  Indexing whatever `main` points at measures a different corpus.
- **Confirm the token parser.** The shape of Codex's usage payload was *not*
  observable when this was written, because no turn ever completed. `find_usage`
  scans for plausible keys and `sum_usage` assumes Codex reports **cumulative**
  session usage, so it takes the last record rather than summing. `results.json`
  records both `total_last_step` and `total_summed` so the first real run can
  settle it without re-running. Preflight prints the actual keys it finds.
- **This is not a reproduction of the r/codex result.** Different model,
  different task flow, different repo. It tests our mechanism, not their number.
  The author offered evaluation details to anyone building one of these; taking
  that offer would make a genuine replication possible.
