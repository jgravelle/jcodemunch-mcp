"""A readiness probe must ask the question the next step asks.

⚠⚠ `release.yml`'s post-publish job is what stands between a published artifact
and the GitHub release plus the MCP registry entry. On v1.108.319 it failed on
both platforms 13 seconds after the upload finished and gave up in under a
second, against a ten-minute budget -- which skipped both of those steps and
left the release half-finished behind a dispatch line that had already reported
success (cicd FINDINGS C-19, issues #706 / #707 / #709):

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

# ⚠⚠ Never keyed on how the VERSION is spelled. The first draft matched `==$V`
# and therefore missed `==${{ needs.preflight.outputs.version }}` -- the
# workflow's OWN idiom, the expression `V` is assigned from two lines above each
# step -- along with a literal version, `pip install` and `uv tool install`. A
# third remote install written any of those ways would have been ungated on
# arrival while `assert len(found) >= 2` stayed green, because the existing two
# still matched. That is "a guard written against a SPELLING is fixed for that
# spelling only" (09-01, #566), found by the reviewer probing the regex rather
# than the tree. See `_is_remote_install` for the other two conjuncts and for
# what dropping the version alone swept in.
# ⚠ `uvx` is here deliberately: CLAUDE.md's `server.py` entry names probing
# through bare `uvx` as a known wrong route ("it served a CACHED build once"),
# which makes it the likeliest wrong spelling to appear in this file.
_INSTALL_VERB = re.compile(r"\b(?:uv (?:pip|tool) install|uv add|uvx|pip3? install)\b")

# A local artifact needs no retry: nothing has to propagate. The dry-run arms
# install `dist/*.whl`, and wrapping those in a loop that can never help would
# be worse than leaving them out.
_LOCAL_SOURCE = re.compile(r"\bdist/|\.whl\b|\.tar\.gz\b|\s-e\s|--find-links")


def _distribution() -> str:
    """The name from `pyproject.toml`, never a second copy of it here."""
    m = re.search(
        r'^name\s*=\s*"([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M
    )
    assert m, "no name in pyproject.toml"
    return m.group(1)


# ⚠ `-` and `_` fold: `jcodemunch_mcp==$V` is the PEP 503 normalised form and both
# `uv` and `pip` accept it, so a literal hyphen would have been a fourth spelling.
# ⚠⚠ Built by SPLITTING on the separators, not by chained `.replace`. The first
# draft was `re.escape(name).replace(r"\-", "[-_]").replace("-", "[-_]")`, and the
# second replace rewrote the `-` inside the `[-_]` the first had just inserted,
# yielding `[[-_]_]` -- a regex that compiles with a FutureWarning and matches
# NOTHING, so every scan silently returned False. `test_the_scan_finds_the_steps_it_is_about`
# is what catches that, and it is why the non-vacuity floor is in this file.
_DIST = re.compile("[-_]".join(re.escape(part) for part in re.split(r"[-_]", _distribution())))

# The readiness endpoints a probe could ask about. The JSON API is the one that
# burned us; `/simple/` is the honest surface and is still not the install.
_PROBE_URL = re.compile(r"https://(?:test\.)?pypi\.org/(?:pypi|simple)/")


_ASSIGN = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def _logical_lines(run: str) -> list[str]:
    """Continuations joined and comments stripped. NOT a shell.

    ⚠ The summary line used to read "normalised the way the shell reads it",
    which is the line a traceback shows and was over-claiming in both halves at
    once: it expanded variables in a way no shell does (see below), and it cut
    at a `#` that shell would not treat as a comment. It does two textual
    things; naming them is the whole honesty of the helper.

    ⚠⚠ Two normalisations, and the first is this repo's own 09-04 lesson
    verbatim: *a ratchet's first draft matched per PHYSICAL line and stayed
    green with the defect back -- normalise the text the way the shell does
    before scanning it* (inbound item 6, where the reviewer named one site and
    the ratchet then found four more).

    **Backslash continuations are joined**, and comments are stripped. That is
    all, deliberately. `release.yml` already writes `uv pip install --python
    "$VENV" \\` with the argument on the next line, two lines below each install
    for the `scripts/handshake.py` call. Split per physical line, the verb and
    the distribution name land apart, neither matches, and `assert len(found) >=
    2` stays green because the existing two still do.

    ⚠ This is the THIRD costume of one defect, which is why the fix is
    normalisation rather than another pattern: round 1 keyed on the VERSION's
    spelling, round 3 keyed on the NAME's. A conjunct can be right while what it
    matches on is still a spelling.

    ⚠⚠ **A variable holding the package name (`"$PKG==$V"`) is NOT covered, and
    that is a decision rather than an oversight.** A draft expanded simple
    assignments to reach it and was worse than the gap: substituting with
    `str.replace` in dict order made `$V` eat the prefix of `$VENV`, so every
    normalised line of BOTH real steps read `--python "<value of V>ENV"` -- text
    no shell would ever produce, inside a helper whose docstring claimed shell
    semantics. Ordering by length fixes that one and not the class: expanding
    ANY variable whose value contains the distribution name pulls it into
    unrelated lines, and `release.yml` already assigns `EXE="$B/jcodemunch-mcp.exe"`,
    so `uv pip install ... pytest && "$EXE" --version` would read as a remote
    install of the artifact. Reaching the real case needs the package ARGUMENT's
    position, not textual substitution.
    ⚠ The gap is narrow and bounded: `test_the_scan_finds_the_steps_it_is_about`
    still fails if either existing install stops being seen, and a THIRD install
    written that way would be ungated. **A false positive here tells an author to
    wrap a `pytest` install in a propagation retry, which is nonsense advice and
    an invitation to weaken the test** -- the worse of the two errors, and the
    reviewer's own round-2 argument pointed the same way.
    """
    joined = re.sub(r"\\\s*\n\s*", " ", run)
    return [_strip_comment(line) for line in joined.splitlines()]


def _strip_comment(line: str) -> str:
    """Shell comment removed, so a line ABOUT the defect is not read as the defect.

    ⚠ Found by the reviewer, not by the suite: writing this step's own
    explanatory comment with the scheme included
    (`# https://pypi.org/pypi/<pkg>/<version>/json`) turned
    `test_no_step_gates_an_install_on_a_different_endpoint` red and named the
    comment as the gate. The tree was green only because that comment happens to
    omit `https://`. **The next person who documents the defect in full would
    have got a red test blaming them for it.**

    ⚠ A `#` is only a comment at a WORD boundary. Splitting on the first one
    anywhere truncated parameter expansion: `"jcodemunch-mcp==${TAG#v}"` became
    `"jcodemunch-mcp==${TAG`. No line in `release.yml` writes that today and no
    verdict moved, but a helper that claims to read shell has to read it.
    """
    return re.split(r"(?:^|\s)#", line)[0]


def _is_remote_install(line: str) -> bool:
    """Installs THIS distribution from somewhere that has to propagate.

    Three conjuncts, and the third is the one the first two rounds missed in
    opposite directions. Keying on the version SPELLING (`==$V`) missed this
    workflow's own `${{ needs.preflight.outputs.version }}`; dropping the
    version and keeping only "an install with no local artifact" then swept in
    `-r requirements.txt`, `build twine` and `pytest`, so a future dev-tooling
    step would fail three tests and be told to wrap itself in a propagation
    retry — advice that is nonsense for `pytest`, and an invitation to weaken
    the test rather than fix the step.

    The property is "installs the just-published artifact", so the distribution
    NAME is the conjunct that says so, read from `pyproject.toml` rather than
    written here a second time.
    """
    return bool(
        _INSTALL_VERB.search(line)
        and _DIST.search(line)
        and not _LOCAL_SOURCE.search(line)
    )


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
    return [s for s in _steps_with_run() if any(_is_remote_install(ln) for ln in _logical_lines(s[2]))]


_PREDICATE_CASES = [
    # (label, step shell, is this a remote install of the published artifact)
    ("backslash continuation", 'V="1.1.1"\nuv pip install --python "$VENV" \\\n  "jcodemunch-mcp==$V"\n', True),
    ("PEP 503 underscore form", 'uv pip install "jcodemunch_mcp==$V"\n', True),
    # ⚠ The two STATED GAPS, asserted as gaps rather than left unsaid: see
    # `_logical_lines`. The draft that reached them corrupted both real steps.
    ("name held in $PKG (stated gap)", 'PKG="jcodemunch-mcp"\nV="1.2.3"\nuv pip install "$PKG==$V"\n', False),
    ("name held in ${PKG} (stated gap)", 'PKG=jcodemunch-mcp\nuv pip install "${PKG}==${V}"\n', False),
    ("workflow expression version", 'uv pip install "jcodemunch-mcp==${{ needs.preflight.outputs.version }}"\n', True),
    ("literal version", 'uv pip install "jcodemunch-mcp==1.108.320"\n', True),
    ("uvx --from", 'uvx --from "jcodemunch-mcp==$V" jcodemunch-mcp\n', True),
    ("dev tooling", 'uv pip install --python "$VENV" build twine\n', False),
    ("requirements file", 'uv pip install --python "$VENV" -r requirements.txt\n', False),
    ("a single dev dependency", "uv pip install pytest\n", False),
    ("the local wheel (dry-run arm)", 'uv pip install --python "$VENV" dist/*.whl\n', False),
    ("commented out", '# uv pip install "jcodemunch-mcp==$V"\n', False),
    # ⚠ The reviewer's three reproduced false positives from the expansion draft,
    # pinned so it cannot come back quietly. release.yml already assigns the second.
    ("a log path named after the package", 'LOG="${RUNNER_TEMP}/jcodemunch-mcp.log"\nuv pip install pytest 2>&1 | tee "$LOG"\n', False),
    ("the installed exe path release.yml assigns", 'EXE="$B/jcodemunch-mcp.exe"\nuv pip install --python "$VENV" pytest && "$EXE" --version\n', False),
    ("a requirements path under a package-named dir", 'PKGDIR=/tmp/jcodemunch-mcp\nuv pip install --python "$VENV" -r "$PKGDIR/req.txt"\n', False),
    ("a continuation that is not an install", '"$B/python" scripts/handshake.py \\\n  --fixture tests/fixtures/pkg_smoke\n', False),
    # ⚠ `${TAG#v}` is parameter expansion, not a comment; splitting on a bare `#`
    # truncated it and the line stopped naming the distribution.
    ("a version from parameter expansion", 'uv pip install "jcodemunch-mcp==${TAG#v}"\n', True),
]

# The four shapes `_INSTALL_VERB` selects that the retry assertion could not
# accept while it anchored on the literal `if uv pip install` — each a correctly
# retried install. Asserted against the RETRY check, not the selector.
_RETRIED_SHAPES = [
    ("uvx", 'for i in $(seq 1 3); do\n  if uvx --from "jcodemunch-mcp==$V" jcodemunch-mcp --version; then break; fi\ndone\n'),
    ("uv tool install", 'for i in $(seq 1 3); do\n  if uv tool install "jcodemunch-mcp==$V"; then break; fi\ndone\n'),
    ("pip install", 'for i in $(seq 1 3); do\n  if pip install "jcodemunch-mcp==$V"; then break; fi\ndone\n'),
    ("a compound condition", 'for i in $(seq 1 3); do\n  if cd "$D" && uv pip install "jcodemunch-mcp==$V"; then break; fi\ndone\n'),
]


@pytest.mark.parametrize("label,shell", _RETRIED_SHAPES, ids=[c[0] for c in _RETRIED_SHAPES])
def test_any_install_verb_satisfies_the_retry_check(label: str, shell: str):
    """A correctly retried install passes whatever verb it is written with.

    ⚠⚠ All four of these were SELECTED as remote installs and could never
    satisfy the retry assertion while it anchored on `if uv pip install`, so a
    correct step would have failed with advice to rewrite it — and for `uvx`,
    advice this repo's own notes argue against. The selector and the assertion
    must agree about what an install is, which is why both call
    `_is_remote_install` now.
    """
    for line in _logical_lines(shell):
        if not _is_remote_install(line):
            continue
        condition = line.split("; then", 1)[0]
        assert re.match(r"\s*if\s", line) and _is_remote_install(condition), (
            f"{label}: a retried install written this way is rejected by the retry check"
        )
        return
    raise AssertionError(f"{label}: the scan did not see this as a remote install at all")


@pytest.mark.parametrize("label,shell,expected", _PREDICATE_CASES, ids=[c[0] for c in _PREDICATE_CASES])
def test_the_predicate_answers_each_spelling(label: str, shell: str, expected: bool):
    """The scan's own behaviour, pinned rather than argued.

    ⚠⚠ Every True case here is a spelling a previous round of this file MISSED,
    and every False case is one a previous round wrongly MATCHED. Three rounds
    went: keyed on the version's spelling (missed the workflow's own
    expression), then keyed on nothing but "an install with no local file"
    (swept in `pytest`), then keyed on the name's spelling (missed the
    underscore form, a `$PKG` variable and a `\\` continuation).

    ⚠ It also catches the failure that broke the predicate outright while
    writing this: `_DIST` was built with chained `.replace`, the second rewrote
    the `-` inside the `[-_]` the first had inserted, and the resulting
    `[[-_]_]` compiled with a FutureWarning and matched nothing. Every scan
    silently returned False and every assertion below passed.
    """
    got = any(_is_remote_install(line) for line in _logical_lines(shell))
    hint = ""
    if "stated gap" in label:
        hint = (
            "\n⚠ This case is a STATED GAP asserted as a gap (see `_logical_lines`). "
            "A failure here may mean the gap was CLOSED, not that something broke: if the "
            "scan now sees a package name held in a shell variable AND the three pinned "
            "false positives above still pass, flip this case to True — do NOT revert the "
            "change that closed it."
        )
    assert got is expected, f"{label}: expected {expected}, got {got}{hint}"


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
    inside a retry whose CONDITION is that install — `if <install>; then break;
    fi` — so a version still propagating is waited for by the operation that
    needs it.

    ⚠⚠ **Asserted as the property, because the literal was the FOURTH costume of
    this file's recurring defect — this time in the assertion rather than the
    selector.** It read `^\\s*if\\s+uv pip install\\b` while `_INSTALL_VERB`
    accepts five spellings, so `if uvx --from ...`, `if uv tool install ...`,
    `if pip install ...` and `if cd "$D" && uv pip install ...` were all
    SELECTED as remote installs and could never satisfy it — four correctly
    retried constructions failing with a message telling the author to make a
    rewrite that is not required, and for `uvx` one this repo's own notes argue
    against. It entered when the verb list widened and the assertion did not
    follow.

    The property is "the install is the retry's condition", so the check is
    `_is_remote_install` on the text before `; then` — the same predicate that
    selected the line, which is what stops the two drifting apart again.
    """
    for line in _logical_lines(run):
        if not _is_remote_install(line):
            continue
        condition = line.split("; then", 1)[0]
        assert re.match(r"\s*if\s", line) and _is_remote_install(condition), (
            f"{RELEASE.name} job {job!r}, step {name!r}: this installs a pinned version "
            f"from a remote index without retrying the install itself:\n    {line.strip()}\n"
            f"Put the install in the retry's CONDITION — `if <install>; then break; fi` — "
            f"inside the loop. Any install verb will do; what matters is that the thing "
            f"being retried is the install. A probe on any other endpoint confirms "
            f"readiness by luck."
        )


@pytest.mark.parametrize("job,name,run", _remote_install_steps(), ids=lambda v: str(v)[:40])
def test_no_step_gates_an_install_on_a_different_endpoint(job: str, name: str, run: str):
    """The defect itself, asserted as its own rule.

    A step may not both probe a PyPI URL and install from the index: that is
    two surfaces with two caches, and the one that answers first is not the one
    that serves the file.
    """
    probes = [
        ln.strip()
        for ln in _logical_lines(run)
        # ⚠ An assignment holding a URL gates nothing; it is a value, not a check.
        if _PROBE_URL.search(ln) and not _is_remote_install(ln) and not _ASSIGN.match(ln)
    ]
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

    The name said "poll up to 10 min" while the resolver gave up in 250 ms
    inside a step that had already built a venv, 13 seconds after the upload.
    That is why the first read of this failure blamed PyPI rather than the
    workflow. ⚠ The 250 ms is the RESOLVER's duration, not the gap from the
    upload; attaching it to the wrong interval is how the number first got
    published, and the reviewer caught it in four places.

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
