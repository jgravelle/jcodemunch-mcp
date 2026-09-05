# Fairness note: Aider RepoMap (aider-chat 0.86.2)

Adapter `benchmarks/competitive/adapters/aider.py`; image
`benchmarks/competitive/sandbox/aider.Dockerfile` with the hash lockfile
`sandbox/aider.requirements.txt`. Written 2026-09-05 before the first
number was recorded (DESIGN §1.3). Everything quoted from the tool is data
(principle 5), read from PyPI `aider-chat` 0.86.2 (wheel sha256
`64f6a0c6…`, uploaded 2026-02-12, `Requires-Python <3.13,>=3.10`, the
latest release on 2026-09-05), the docs site `aider.chat/docs/`
(`repomap.html`, `install.html`, `config/options.html`, read 2026-09-05)
and the tagged source `Aider-AI/aider` at `v0.86.2` (`aider/main.py`,
`aider/repomap.py`, `aider/models.py`, `aider/onboarding.py`). No page was
browsed during a run.

**This row is the token axis only** (FIELD.md set row 6, DESIGN §1.3): the
repo map is one ranked text per repository, not an answer to a question,
so no F1 row exists for it and none is invented. What is measured is the
cost of that text at the tool's default budget, which is what an agent
using Aider pays on every change request: "Aider sends a **repo map** to
the LLM along with each change request from the user."

## What the docs recommend

Install: "python -m pip install aider-install" then "aider-install" is the
page's first route; the alternatives are "uv tool install --force --python
python3.12 --with pip aider-chat@latest", "pipx install aider-chat", and
"python -m pip install -U --upgrade-strategy only-if-needed aider-chat",
each stated for "python versions 3.9-3.12".

The map: aider "solves this problem by sending just the **most relevant**
portions of the repo map. It does this by analyzing the full repo map using
a graph ranking algorithm, computed on a graph where each source file is a
node and edges connect files which have dependencies." "The token budget is
influenced by the `--map-tokens` switch, which defaults to 1k tokens."
"Aider adjusts the size of the repo map dynamically based on the state of
the chat." In the source (`repomap.py`), with no files in the chat the
budget is `max_map_tokens` times the no-files multiplier, capped by the
model's context window; the CLI's `--map-tokens` is "Suggested number of
tokens to use for repo map, use 0 to disable" and `--map-multiplier-no-files`
is "Multiplier for map tokens when no files are specified (default: 2)".
`--map-refresh` is "auto" by default.

Printing it: `--show-repo-map` is "Print the repo map and exit (debug)";
`main.py` runs `coder.get_repo_map()` and prints it with `io.tool_output`.

A model is required before that point: with no `--model` and no key,
`select_default_model` prints "No LLM model was specified and no API keys
were provided." and offers an interactive OpenRouter login; with `--model`
given it "return[s] args.model" and `sanity_check_model` only warns
("Warning: {model} expects these environment variables") and does not
exit. Token counts come from `litellm.encode(model=self.name, ...)`, a
local tokenizer.

Housekeeping the docs give switches for: `--gitignore` "Enable/disable
adding .aider* to .gitignore (default: True)"; `--check-update` "Check for
new aider versions on launch" (default True); `--analytics`
"Enable/disable analytics for current session (default: random)" and
`main.py` asks "Allow collection of anonymous analytics to help improve
aider?" when it has not been told; the tags cache is
`.aider.tags.cache.v{N}` in the repository, and when it cannot be opened
the tool warns "Unable to use tags cache at {path}, falling back to memory
cache".

## What we configured and why

- **Install**: `pip install --require-hashes` from a lockfile that pins
  `aider-chat==0.86.2` and its 108 dependencies by version and sha256
  (compiled with `uv pip compile --generate-hashes` for linux / Python
  3.12 on 2026-09-05), in a `python:3.12-slim-bookworm` image pinned by
  digest, because the package declares `<3.13` and the docs' install
  routes are all stated for 3.9-3.12. The pip route is the documented one
  whose resolution a lockfile can pin; `aider-install` and `uv tool
  install ... @latest` resolve at install time, which is not a pin.
- **Tokenizer assets at build**: the model's tiktoken encodings are
  fetched once at image build into `TIKTOKEN_CACHE_DIR`, and litellm reads
  its bundled model table (`LITELLM_LOCAL_MODEL_COST_MAP=True`), so the run
  under `--network none` counts tokens without a fetch. A network failure
  there would read as the tool's failure and it is not.
- **Model**: `--model gpt-4o`, the model the tool itself selects when an
  `OPENAI_API_KEY` is present (`onboarding.py`), named explicitly because
  no key is set and the map path never calls a model. The model only
  chooses the tokenizer that sizes the budget. No key of any kind is in
  the image or the environment; the run is `--network none`.
- **Command**: `aider --model gpt-4o --show-repo-map --no-analytics
  --no-check-update --no-gitignore --no-show-model-warnings --yes-always`,
  run in the corpus root. Each switch is the documented one for a
  behaviour the sandbox cannot host: no analytics prompt, no update check,
  no `.gitignore` edit, and the missing-key warning kept off the map text
  (it is a warning for a chat the run does not start; the map is what an
  agent puts in context). `--yes-always` answers any remaining prompt as a
  non-interactive run would.
- **Budget**: `--map-tokens` at its default (not passed), so the map is the
  tool's own default size for a chat with no files added (DESIGN §1.3:
  "with `--map-tokens` at its default"). That default is not the docs'
  "1k": `models.py`'s `get_repo_map_tokens` is `max_input_tokens / 8`,
  clamped to 1024..4096, so for gpt-4o the tool prints "Repo-map: using
  4096 tokens" and the no-files multiplier of 2 sits on top of that. Both
  defaults untouched; the probe section has the sizes.
- **Payload**: the map is what follows the tool's own repo-map preface
  ("Here are summaries of some files present in my git repository."),
  which is the text it sends the LLM. What precedes it on stdout is the
  CLI's announce block for a human (version, model, "Git repo: .git with N
  files", the budget line) plus, in this sandbox, two lines the tool
  prints when its daily fetch of litellm's price table from GitHub fails
  under `--network none`, and two git-identity hints. Those lines are kept
  in a side file for the note and are not charged: they are not the map,
  and the fetch failure is the sandbox's, not the tool's. The budget line
  is read from them for the index report.
- **Corpus copy**: the corpus is mounted read-only and the tags cache
  lives in the repository root, so the corpus is copied to `/private/project`
  (the uid-owned tmpfs) once per container and the tool runs there. The
  copy is a harness cost, not charged. This gives the tool its cache: the
  first invocation scans every file (cold), the later ones read the cache.
- **Index**: the first `--show-repo-map` invocation in a container, timed;
  its wall time is the index time (cold: the container is new and the cache
  empty). `files_indexed` is not reported; the tool prints no such count.
- **Answers**: one further invocation per T task, timed and charged; the
  payload is the printed map. The task's query is not passed anywhere,
  because the tool takes none: the map is the same text for every task,
  which is exactly the per-request cost the row reports. Citations: none
  (no F1).
- **Environment**: `HOME=/private`, `AIDER_ANALYTICS=false`,
  `AIDER_CHECK_UPDATE=false`, `AIDER_GITIGNORE=false` (the documented
  variables for the same switches, so a config file the tool looks for in
  `HOME` is not needed), `TIKTOKEN_CACHE_DIR`, `LITELLM_LOCAL_MODEL_COST_MAP`.
- **Surface**: none; a CLI has no `tools/list`.
- Every invocation after the first is charged; the first is the index.

## Where the harness may disadvantage it

1. **The map is scored on cost alone.** Its whole claim is orientation for
   the LLM (S8: cross-file awareness; the whitepaper: "complementary"),
   which no axis here measures. The row says what the map costs per
   request at the default budget and nothing about what it buys.
2. **The no-files budget is the largest the defaults produce.** In a chat
   with files added the map shrinks toward 1k; an agent's per-request cost
   is somewhere between the two, and the row reports the empty-chat case.
3. **Calls per task is 1 by construction** and per-call latency is a whole
   process start plus the map build, not a query; the latency row is not
   like-for-like with a server's call (DESIGN §5.1 already says the wait
   ratio is not an operation ratio).
4. **Token counting for the budget uses gpt-4o's tokenizer** while the row
   charges the payload with the harness tokenizer like every other tool; a
   different `--model` would size the budget differently.
5. **Python only so far**; its other languages are not exercised.

## Where the harness may advantage it

- The corpus copy on tmpfs: its file reads are memory reads, where the
  MCP rows read the bind mount (CF-14). Confined to this row's index and
  calls alike.

## What we could not make work

Nothing of the tool's is broken. Probe figures (2026-09-05, one container,
not results; the first probe ran over a corpus copy that still carried
this tree's bytecode, CF-39, and its counts are labelled so; the section
is re-run on the corrected corpus before the recorded run):

- The first probe ran over a corpus copy that still carried this tree's
  bytecode: the tool's banner read "Git repo: .git with 1,093 files" for
  277 sources, which is how CF-39 was found. Re-run on the corrected
  copy: "Git repo: .git with 277 files".
- Image build 123.0 s (pip from the lockfile, the tokenizer assets);
  `aider --version` inside it: `aider 0.86.2`.
- The banner's budget line: "Repo-map: using 4096 tokens, auto refresh"
  at the defaults (the `max_input_tokens / 8` clamp for gpt-4o, not the
  docs' 1k); with `--map-tokens 2048` passed once for the note, "using
  2048 tokens".
- Wall per invocation (`date +%s%N` around the process): cold 6,910 ms
  (the index), then 4,679 and 4,595 ms with the tags cache warm, 3,944 ms
  at the 2048 budget, 4,544 ms without `--no-show-model-warnings` (the
  warnings print, the map is the same size). The process start and its
  imports are most of a warm call; the cache saves about two seconds.
- The map after the preface, harness tokenizer: 8,573 tokens (cold run),
  8,718 and 8,569 on the two warm runs of the SAME command over the SAME
  corpus, 4,178 at the 2048 budget. Three invocations at identical
  settings gave three sizes; the recorded row's spread will say how wide
  that is.
- The announce block ahead of the preface: 257 to 260 tokens per run,
  not charged (see "Payload"); it holds the two price-table fetch
  failures the sandbox causes and, in the first probe, two git-identity
  hints.
- What the tool writes beside the project copy: `.aider.tags.cache.v4/`
  (1.2 MB, `cache.db`) and `.aider.chat.history.md` (131 KB, written even
  on the `--show-repo-map` path). Both on the tmpfs copy; the read-only
  mount is untouched.
- Its stderr per run: "Warning: Input is not a terminal (fd=0)." only.
