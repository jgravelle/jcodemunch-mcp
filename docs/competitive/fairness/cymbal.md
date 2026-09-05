# Fairness note: cymbal 0.14.0

Adapter `benchmarks/competitive/adapters/cymbal.py`; image
`benchmarks/competitive/sandbox/cymbal.Dockerfile`. Written 2026-09-05
before the first number was recorded (DESIGN §1.3). Everything quoted from
the tool's README is data (principle 5), read from
`1broseidon/cymbal` README.md at tag v0.14.0 (commit `8df611f6e2c7`,
release 2026-06-20) through the GitHub API; no page was browsed during a run.

## What the docs recommend

The README's "AI Agents" section states the agent policy verbatim:

> - Start with `cymbal investigate <symbol>`
> - Use `cymbal show <file:L1-L2>` or `cymbal outline <file>` before broad file reads
> - Use `cymbal search <query>` before raw grep
> - Batch symbol searches as `cymbal search Foo Bar Baz`

The "Commands at a Glance" table, same file:

> | `investigate` | **Start here.** Kind-adaptive exploration in one call |
> | `importers` | Reverse import lookup. Add `--graph` for a visual fan-in map |
> | `search` | Symbol search, or `--text` for grep-style lookup |
> | `show` | Display a symbol's source code, or a specific file range |
> | `refs` | Find references / call sites. Use `--file` to scope by path |

"All commands support `--json` for structured output", and
`docs/guide/agent-native.md` is cited for "frontmatter output format and
why it is cheaper than JSON by default". Install: a release tarball per OS
with a `checksums.txt`; "The index auto-builds on first use" and
`cymbal index .` is an "optional warm-up". Index location on Linux:
`~/.cache/cymbal/repos/<hash>/index.db`.

## What we configured and why

- **Install**: the `linux_x86_64` release tarball, verified against the
  published sha256 (`bfc9517…`) at image build; the binary is the only
  thing added to a `debian:bookworm-slim` image pinned by digest.
- **Index**: `cymbal index /corpus` once per run, timed, before any query,
  so the cold index cost is its own row and no query pays it (the docs call
  the warm-up optional; an agent that skips it pays it on the first query
  instead, which would be charged to latency, not to index time; we chose
  the split that keeps the two axes separate).
- **Payload**: the DEFAULT output of each command (frontmatter), because the
  docs say it is the cheaper format and an agent following the policy gets
  it. Citations `(rel_path, line)` come from a second `--json` call of the
  same command that is **not charged** to tokens, calls or latency.
- **Commands per task category** (DESIGN §4.1):
  - P1 definition lookup: `investigate <symbol>`, the policy's "start
    here"; its default output carries source, callers and impact in one
    call, which is more than a definition, and all of it is charged because
    that is what the recommended call returns.
  - T token task (a phrase, not a symbol): `search <terms>` (the policy's
    "before raw grep"; multi-word queries are its documented batch form),
    then `show <name>` on the top 3 results, mirroring our own
    search-then-read-3 workflow (R27) so the T rows are the same shape.
  - P2 reference finding: `refs <symbol>`.
  - P4 file dependencies: `importers <path>`.
- **Environment**: `HOME=/out` so the cache lands in the writable mount;
  the corpus is read-only, which the docs' Docker section also does
  (`-v repo:/workspace`); no other variable reaches the tool.

## Where the harness may disadvantage it

1. **Process start per call.** Every command is a new process, measured
   inside the container with `date +%s%N` around the call. Its README
   quotes "roughly 10-40 ms" per query on its own benchmark corpus; the
   first probe in this sandbox measured about 750 ms per query on the self
   corpus (277 files) after a 4.0 s index. Part of that is the read-only
   rootfs and the `/out` bind mount, part may be its per-query auto-refresh
   ("queries auto-refresh" when files change) walking the corpus. The
   number reported is what an agent running it as a subprocess in this
   sandbox waits; the fairness debt is that jCodeMunch's per-call figure is
   a function call inside one process. `latency_call_ms` is defined as the
   wait per call for exactly this reason (DESIGN §5.1) and the row is
   read with it.
2. **A git repository is required.** Its queries key the index by git
   root and answer "not inside a git repository, results may be empty"
   otherwise (measured: every query empty on a plain directory copy, every
   query answered after `git init` + one commit). The runner now makes
   every corpus a git repository (CF-10), which the pinned corpora already
   are; the self corpus is a fresh single-commit repo, which gives it no
   history to use and none to pay for.
3. **`investigate` returns more than a definition.** On P1 it is charged for
   callers and impact it returns unasked. That follows its own policy; an
   alternative configuration (`search` + `show`) would be cheaper and is
   what the T rows use. If the P1 token row is read as a loss for cymbal,
   the T row is the like-for-like comparison.
4. **Symbol miss handling.** When a command finds nothing it prints the
   miss to stderr and exits non-zero; the payload charged is that stderr
   text (what the agent sees), for the primary call and for each `show`,
   and the row cites nothing. That is the same
   treatment as an empty jCodeMunch result.
5. **Batch mode is not used** beyond the multi-term `search`. A
   `investigate Foo Bar Baz` batch could answer several tasks in one
   process; tasks are independent questions here, as they are for every
   tool.

## What we could not make work

Nothing at v0.14.0 on the self corpus. `investigate`, `search`, `show`,
`refs` and `importers` all answered with `--json` and with the default
format once the corpus was a git repository.
