# Self-run A/B kit: your repo, with and without jCodeMunch

This kit lets a team measure jCodeMunch on its own code and its own questions. Run each lookup with and without jCodeMunch, then compare the tokens and the correct answers. The numbers are yours. Nothing is sent to us.

## Before you run tier 2: it spends your money

- **`run_lookups.py` starts real Claude Code sessions,** using the `claude` CLI on your PATH. Each session sends your repository's code to the Anthropic API as model input.
- **It is billed to whatever account your `claude` CLI is signed in to:** your API key or your subscription. It is **not** billed to jCodeMunch or to us.
- **Cost depends on your repo and your questions.** A default run is 15 lookups × 3 seeds × 2 arms = 90 sessions. Run a pilot first (`--seeds 1` and a small `--cap`): the run prints what it spent, which tells you what a full run will cost.
- **`--cap` is required.** The run stops once the billed totals Claude Code reports reach it, and each session gets at most `--session-cap` (default $0.50).
- **The only network traffic is those model calls,** made by Claude Code itself. The kit makes no network calls of its own, and it sets jCodeMunch's savings sharing off and its telemetry URL to a dead local address.

Tier 1 (`run_benchmark.py`) makes no model calls and costs nothing.

**Where it comes from:** the kit lives in the git repository and the source distribution (sdist), under `benchmarks/ab-kit/`. It is **not** in the wheel, so a `pip install` won't include it. Clone the repo, or unpack the sdist.

## Tiers: stop after any one

| Tier | What runs | Model calls | Cost to you | What it answers |
|---|---|---|---|---|
| 1. Retrieval only | `run_retrieval.py --repo PATH --lookups lookups.json` (indexes into its own store, then runs `benchmarks/harness/run_benchmark.py --tasks`) | None | $0 | Tokens per lookup for the code each method returns. Retrieval only. |
| 2. Agent lookups | `run_lookups.py`, then `report.py` | Yes, on your own Claude Code credentials | Depends on your repo; run a pilot with `--seeds 1` first. `--cap` bounds it | Tokens per lookup **and** correctness, in a Claude Code Explore agent |
| 3. Whole tasks | Not in this kit yet | Yes | Not measured here | Cost per solved task |

## Tier 1, step by step

- **Needs:** a Python where `jcodemunch_mcp` is importable (`pip install jcodemunch-mcp`). There are no model calls and no cost.
- **Each lookup** may carry a short `query`, which is the search terms. Without one, its `question` is used. Tier 1 doesn't use the answer key.
- **Run it:** `python run_retrieval.py --repo /path/to/your/repo --lookups lookups.json`. The report is `retrieval.md`, in `--out`.

## Tier 2, step by step

1. **Install the tools:** `claude` (Claude Code) and `jcodemunch-mcp` must both be on PATH. Tier 2 runs whatever jCodeMunch version you have installed, and records it.
2. **Write `lookups.json`:** 10 to 30 questions, in your own words, about your repo. Each needs an **answer key**: the symbols you'd accept as right, each with its file. Start from `lookups.example.json`.
   - A lookup counts as correct when the answer names an accepted symbol **and** that symbol's file. `alt_files` lists other files that also count.
   - Write the key **before** you run anything. The kit records its hash, and refuses to grade if the key has changed since the run started.
3. **Run it:**

   ```bash
   python run_lookups.py --repo /path/to/your/repo --lookups lookups.json --cap 20
   ```

   - **`--cap`** is required. It's a dollar limit on the billed totals Claude Code reports, and the run stops when it's reached.
   - **`--seeds`** sets how many times each lookup runs per arm (default 3).
   - **`--explore-model`** chooses the Explore agent's model: `haiku` (the default) or `sonnet`.
   - **`--out`** sets where results go. It must be outside your repo. The default is `~/jcodemunch-ab-results/<timestamp>`.
   - Re-running with the same `--out` resumes a stopped run.
4. **Read the report:**

   ```bash
   python report.py --results <out dir> --lookups lookups.json
   ```

## What the two arms are

Each arm runs on its own copy of your repo, without `.git`. Each copy gets a project `.claude/agents/Explore.md` override with the same model and the same instructions. The arms differ only in tools:
- **G:** `Read, Grep, Glob`, the tools Claude Code's built-in Explore agent searches with.
- **J:** the same tools, plus jCodeMunch's search and read tools, and one sentence naming them. Grep stays available, so jCodeMunch has to earn its calls.

**Each session works like this:** a headless Claude Code session is told to use the Explore agent for one lookup. The kit records the Explore agent's billed tokens and every tool call. A session that is wired wrong is retried once and then left out of the results: for example, the parent searched by itself, or the Explore agent didn't run on the chosen model.

## What the report shows

- **Per lookup:** median Explore tokens in each arm, the ratio, correct counts, and the number of jCodeMunch calls.
- **Tokens:** a paired Hodges-Lehmann ratio (J/G), with an exact Wilcoxon signed-rank p-value.
- **Correctness:** correct counts in each arm, with an exact McNemar p-value.
- **Quality guard:** if J has more than the guard's number of fewer correct answers (default 10% of pairs), the report says no token saving can be claimed.
- **Losses appear in full:** lookups where J used more tokens, and the case where the agent never called jCodeMunch at all.

## Privacy and credentials

- **Nothing is uploaded.** jCodeMunch's savings sharing is turned off, and its telemetry URL points at a dead local address, for every process the kit starts.
- **The kit never reads, prints or stores an API key.** Sessions use your existing Claude Code credentials. After each session, the kit checks its own output files for the value of `ANTHROPIC_API_KEY`, if it's set, and deletes any file that contains it.
- **Results stay in `--out`.** Don't commit them.

## Limits

- **Tier 1 measures retrieval only.** It doesn't measure a session or a bill.
- **Tier 2 measures lookups, not finished work.** A team whose own gate measures whole tasks should not read tier 2 as an answer to that question.
- **Small sets are noisy.** Under 10 lookups, read the ratio as descriptive.
- **If the agent never calls jCodeMunch, the arms come out the same.** That's a real result for that setup, and the report states it.
