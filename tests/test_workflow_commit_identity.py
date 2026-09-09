"""Every git identity a workflow commits or tags under is an account we own (cicd FINDINGS C-17).

`<name>@users.noreply.github.com` is not a made-up address: GitHub resolves it to
whichever account owns `<name>`. `main.yml` committed the weekly results as
`harness-bot@users.noreply.github.com` and the commit was attributed to a real
account created 2026-05-18 that is not ours, so CLA Assistant saw an unsigned
stranger and the results PR (#633) sat BLOCKED with every check green;
`release.yml` tagged as `release-bot`, an account since 2014. The numeric form,
`<id>+<login>@users.noreply.github.com`, is the one GitHub issues per account, so a
login alone can no longer claim it. This test proves the FORM: a wrong numeric id
still resolves to whoever holds that id, and only a live `users/<login>` read
proves ownership (C-17 review round 4).

The five inbound/competitive workflows push with the App token under
`inbound@users.noreply.github.com`, a real account since 2012 (inbound FINDINGS
IN-20). Their fix is the App's own numeric address, not `github-actions[bot]`,
and it is allowlisted here BY SITE with the finding that owns it, so the
allowlist shrinks with the fix and a sixth made-up address fails this test.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((REPO / ".github" / "workflows").glob("*.yml"))
# Every spelling git accepts for a commit or tag email, not only `git config
# user.email` (inbound FINDINGS IN-21): `-c user.email=`, the GIT_AUTHOR_EMAIL /
# GIT_COMMITTER_EMAIL environment, and `--author="Name <email>"`. A guard written
# against one spelling is fixed for that spelling only (Standing lesson 09-01).
EMAIL = re.compile(
    r"""user\.email\s*[=\s]\s*["']?([^"'\s]+)["']?"""
    r"""|GIT_(?:AUTHOR|COMMITTER)_EMAIL\s*[:=]\s*["']?([^"'\s]+)["']?"""
    r"""|--author[=\s]+["']?[^<"'\n]*<([^>\s]+)>"""
)
OWNED = re.compile(r"^\d+\+[A-Za-z0-9\-]+(\[bot\])?@users\.noreply\.github\.com$")
# IN-20 (fixed 2026-09-08): the five App-pushing workflows carried
# `inbound@users.noreply.github.com`, a stranger's login, allowlisted here by
# site until they moved to the App's own numeric address. Empty now; a new
# made-up address has nowhere to hide.
ALLOWED_UNTIL_FIXED: dict[str, set[str]] = {}


def _emails_in(text: str) -> list[str]:
    return [next(g for g in m.groups() if g) for m in EMAIL.finditer(text)]


def _emails(path: Path) -> list[str]:
    return _emails_in(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_workflow_commit_identity_is_an_account_we_own(path):
    bad = [
        e
        for e in _emails(path)
        if not OWNED.match(e) and path.name not in ALLOWED_UNTIL_FIXED.get(e, set())
    ]
    assert not bad, (
        f"{path.name}: {bad} resolves to whichever GitHub account owns that login "
        "(C-17: harness-bot and release-bot were strangers); use the numeric "
        "<id>+<login>@users.noreply.github.com form of an account we own"
    )


def test_the_scan_is_not_vacuous():
    assert sum(len(_emails(p)) for p in WORKFLOWS) >= 7


def test_the_allowlist_names_only_sites_that_still_carry_the_address():
    """An allowlist entry outliving its site is a ratchet that stopped describing the tree."""
    for email, names in ALLOWED_UNTIL_FIXED.items():
        for name in names:
            assert email in _emails(REPO / ".github" / "workflows" / name), (
                f"{name} no longer uses {email}; drop it from ALLOWED_UNTIL_FIXED"
            )


@pytest.mark.parametrize(
    "email, owned",
    [
        ("41898282+github-actions[bot]@users.noreply.github.com", True),
        ("harness-bot@users.noreply.github.com", False),
        ("release-bot@users.noreply.github.com", False),
        ("inbound@users.noreply.github.com", False),
        # IN-20: the App's own address, `gh api "users/jcodemunch-inbound[bot]" --jq .id`
        ("325112034+jcodemunch-inbound[bot]@users.noreply.github.com", True),
        ("12345+some-app[bot]@users.noreply.github.com", True),
        ("noreply@example.com", False),
    ],
)
def test_the_owned_form_is_the_numeric_one(email, owned):
    assert bool(OWNED.match(email)) is owned


# IN-21: the spellings git accepts besides `git config user.email`, each with a
# stranger's address the first scanner (user.email followed by whitespace) never
# saw. None is in .github today; the ratchet is widened before one arrives.
@pytest.mark.parametrize(
    "text",
    [
        'git config user.email "harness-bot@users.noreply.github.com"',
        "git config --global user.email harness-bot@users.noreply.github.com",
        'git -c user.email="harness-bot@users.noreply.github.com" -c user.name=x commit -m m',
        "git -c user.email=harness-bot@users.noreply.github.com commit -m m",
        "        GIT_AUTHOR_EMAIL: harness-bot@users.noreply.github.com",
        '        GIT_COMMITTER_EMAIL: "harness-bot@users.noreply.github.com"',
        "export GIT_COMMITTER_EMAIL=harness-bot@users.noreply.github.com",
        'git commit --author="Harness Bot <harness-bot@users.noreply.github.com>" -m m',
        "git commit --author 'Harness Bot <harness-bot@users.noreply.github.com>' -m m",
    ],
    ids=["config", "config-global", "dash-c-quoted", "dash-c-bare", "env-author", "env-committer-quoted", "export-committer", "author-eq", "author-space"],
)
def test_every_spelling_of_a_commit_email_is_scanned(text):
    assert _emails_in(text) == ["harness-bot@users.noreply.github.com"], text


def test_a_script_argument_named_author_is_not_a_git_identity():
    # inbound-intake.yml passes `--author "$AUTHOR"` to our own script; no email, no match
    assert _emails_in('python x.py --event "$EVENT" --author "$AUTHOR" --labels "$LABELS"') == []
