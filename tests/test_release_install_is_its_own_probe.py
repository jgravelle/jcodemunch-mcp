"""A readiness probe must ask the question the next step asks.

⚠⚠ `release.yml`'s post-publish job is what stands between a published artifact
and the GitHub release plus the MCP registry entry. On v1.108.319 it failed on
both platforms 250 milliseconds after the upload, which skipped both of those
steps and left the release half-finished behind a dispatch line that had already
reported success (cicd FINDINGS C-19, issues #706 / #707 / #709):

    × No solution found when resolving dependencies:
    ╰─▶ Because there is no version of jcodemunch-mcp==1.108.319 and you require
        jcodemunch-mcp==1.108.319, we can conclude that your requirements are
        unsatisfiable.

The step is named "poll up to 10 min" and it did not poll. Its loop asked

    curl -fsS "https://pypi.org/pypi/<pkg>/$V/json"     # the JSON API

and the install that followed read

    uv pip install "<pkg>==$V"                          # the /simple/ index

Two surfaces, separately cached. The JSON API answered 200 on the first
iteration, the loop broke, and the install ran against an index that had not
published the file yet. **The ten-minute budget was real and was never spent** —
a probe on a different endpoint can only confirm readiness by luck.

⚠ The job one stage earlier had it right the whole time. `smoke from test pypi`
retries the install itself inside an `if`, which is load-bearing twice over: it
retries, and it keeps `set -e` from aborting on the first attempt.

**The property, and the reason it is worth a test rather than a fix alone: the
probe and the consumer cannot drift when they are the same operation.** Polling
`/simple/` instead would also work today and would rot the moment the installer
changes what it reads. Retrying the install cannot, because it IS what the step
needs.

Same family as the CI-env reproduce command that never built CI's environment,
and as Practice 6's Action step nobody read: the thing being verified was not
the thing being run.

Red arm: restore the `curl`-then-install shape and
`test_a_remote_install_retries_itself` fails naming the step.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github" / "workflows"
RELEASE = WF / "release.yml"

# An install that reaches the network for a version that may not be served yet.
# A local wheel (`dist/*.whl`, the dry-run arm) is NOT one: nothing has to
# propagate, so it must stay outside the retry requirement rather than be
# wrapped in a loop that can never help.
_REMOTE_INSTALL = re.compile(r"\buv pip install\b(?![^\n]*\bdist/)[^\n]*==\$?\{?V\}?")

# The readiness endpoints a probe could ask about. The JSON API is the one that
# burned us; `/simple/` is the honest surface and is still not the install.
_PROBE_URL = re.compile(r"https://(?:test\.)?pypi\.org/(?:pypi|simple)/")


def _steps_with_run() -> list[tuple[str, str, str]]:
    """(job, step name, run text) for every step in release.yml that runs shell."""
    doc = yaml.safe_load(RELEASE.read_text(encoding="utf-8"))
    out = []
    for job_name, job in (doc.get("jobs") or {}).items():
        for step in job.get("steps") or []:
            run = step.get("run")
            if run:
                out.append((job_name, step.get("name") or step.get("id") or "<unnamed>", run))
    return out


def _remote_install_steps() -> list[tuple[str, str, str]]:
    return [s for s in _steps_with_run() if _REMOTE_INSTALL.search(s[2])]


def test_the_scan_finds_the_steps_it_is_about():
    """Non-vacuity: a regex that matches nothing passes every assertion below.

    Both the Test PyPI smoke and the post-publish check install a pinned version
    from a remote index, so fewer than two means the scan stopped seeing them.
    """
    found = _remote_install_steps()
    assert len(found) >= 2, (
        f"expected at least the smoke and post-publish installs; found {[(j, n) for j, n, _ in found]}"
    )


@pytest.mark.parametrize("job,name,run", _remote_install_steps(), ids=lambda v: str(v)[:40])
def test_a_remote_install_retries_itself(job: str, name: str, run: str):
    """The install is the probe.

    Every line that installs a pinned version from a remote index must sit
    inside a retry whose CONDITION is that install — `if uv pip install ...;
    then break; fi` — so a version still propagating is waited for by the
    operation that needs it.
    """
    for line in run.splitlines():
        if not _REMOTE_INSTALL.search(line):
            continue
        assert re.search(r"^\s*if\s+uv pip install\b", line), (
            f"{RELEASE.name} job {job!r}, step {name!r}: this installs a pinned version "
            f"from a remote index without retrying the install itself:\n    {line.strip()}\n"
            f"Wrap it as `if uv pip install ...; then break; fi` inside the loop. A probe "
            f"on any other endpoint confirms readiness by luck."
        )


@pytest.mark.parametrize("job,name,run", _remote_install_steps(), ids=lambda v: str(v)[:40])
def test_no_step_gates_an_install_on_a_different_endpoint(job: str, name: str, run: str):
    """The defect itself, asserted as its own rule.

    A step may not both probe a PyPI URL and install from the index: that is
    two surfaces with two caches, and the one that answers first is not the one
    that serves the file.
    """
    probes = [ln.strip() for ln in run.splitlines() if _PROBE_URL.search(ln) and "pip install" not in ln]
    assert not probes, (
        f"{RELEASE.name} job {job!r}, step {name!r} gates an install on a separate "
        f"readiness probe:\n    " + "\n    ".join(probes) + "\n"
        f"The JSON API and the /simple/ index are cached separately; retry the install instead."
    )


@pytest.mark.parametrize("job,name,run", _remote_install_steps(), ids=lambda v: str(v)[:40])
def test_an_exhausted_retry_still_fails(job: str, name: str, run: str):
    """The `if` that makes the loop possible also swallows the last attempt.

    ⚠ This is the cost of the fix above, and it was already live one job
    earlier: `smoke from test pypi` has retried its own install since it was
    written, and on exhaustion it fell through to the handshake with nothing
    installed. The failure still surfaced, as a confusing error from a later
    command rather than as the thing that went wrong.

    So a retried install must be followed by a check that the package is
    actually there, failing with a message naming the version and the budget.
    """
    assert re.search(r"uv pip show\b", run), (
        f"{RELEASE.name} job {job!r}, step {name!r} retries an install but never "
        f"confirms it succeeded; `set -e` cannot see it, because the `if` consumed "
        "the status. Add a `uv pip show ...` check after the loop that exits 1 with "
        "an ::error line."
    )


def test_the_step_name_does_not_promise_polling_it_does_not_do():
    """A step name is read by whoever triages the failure.

    The name said "poll up to 10 min" while the step exited in 250 ms, which is
    why the first read of this failure blamed PyPI rather than the workflow.

    ⚠⚠ **This test did NOT catch C-19 and could not have.** The loop existed;
    it polled the wrong endpoint. It is kept because it closes the adjacent
    spelling — a name promising a wait over a body with no loop at all — and it
    is documented here as a guard that has never fired against a real defect, so
    nobody reads its green as evidence about the probe.
    """
    for job, name, run in _steps_with_run():
        if "poll" not in name.lower():
            continue
        assert re.search(r"for\s+\w+\s+in\s+\$\(seq", run), (
            f"{RELEASE.name} job {job!r}: step name {name!r} promises polling and the "
            f"step body has no retry loop"
        )
