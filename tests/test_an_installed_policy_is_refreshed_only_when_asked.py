"""An installed agent policy can be brought up to date, and only when the user asks.

`init` appends the policy block to CLAUDE.md once and never rewrites it, so a
corrected policy never reached an existing install; `config --check` could only
say it differed. `jcodemunch-mcp claude-md --refresh` replaces the block after
showing the diff and asking, keeps a backup, and leaves every byte outside the
block alone. The first `resolve_repo` answer of an install tells the agent the
command exists when a block differs, once for each policy text this version
installs (owner's ruling, 2026-10-13).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from jcodemunch_mcp import server
from jcodemunch_mcp.cli import policy, policy_refresh

OLD_BLOCK = (
    "## Code Exploration Policy\n"
    "\n"
    "Always use jCodemunch-MCP tools for code navigation. Never fall back to Read, Grep, Glob, or Bash.\n"
    "\n"
    "**Start any session:**\n"
    "1. `resolve_repo` first.\n"
)
MARKER = "policy_notice_state.json"


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """A home directory, a project directory and a store the test owns; the config is this version's."""
    h = tmp_path / "home"
    (h / ".claude").mkdir(parents=True)
    work = tmp_path / "work"
    work.mkdir()
    store = tmp_path / "store"
    store.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("USERPROFILE", str(h))
    monkeypatch.setenv("CODE_INDEX_PATH", str(store))
    monkeypatch.chdir(work)
    # conftest points every scope at one per-test file; this suite needs the two scopes apart.
    from jcodemunch_mcp.cli import init

    monkeypatch.setattr(
        init, "_claude_md_path", lambda scope: h / ".claude" / "CLAUDE.md" if scope == "global" else Path.cwd() / "CLAUDE.md"
    )
    return h, work, store


def _global(h: Path) -> Path:
    return h / ".claude" / "CLAUDE.md"


def _write(path: Path, text: str, newline: str = "\n") -> bytes:
    data = text.replace("\n", newline).encode("utf-8")
    path.write_bytes(data)
    return data


def _refresh(monkeypatch, *args: str, answer: str | None = None, tty: bool = False):
    monkeypatch.setattr(policy_refresh, "_stdin_is_tty", lambda: tty)
    if answer is not None:
        monkeypatch.setattr("builtins.input", lambda prompt="": answer)
    else:
        def _no_input(prompt=""):
            raise AssertionError("asked a question it should not have asked")

        monkeypatch.setattr("builtins.input", _no_input)
    return server.main(["claude-md", "--refresh", *args])


def _current() -> str:
    """The policy the command compares against: after the config is loaded, as the command loads it."""
    from jcodemunch_mcp.cli import init

    init.ensure_config_loaded()
    return policy.active_policy()


def _backups(path: Path) -> list[Path]:
    return sorted(p for p in path.parent.iterdir() if p.name != path.name and p.name.startswith(path.name))


# -- the command ---------------------------------------------------------------


def test_a_differing_block_becomes_the_current_policy(home, monkeypatch, capsys):
    h, _, _ = home
    path = _global(h)
    _write(path, "# Mine\n\nKeep this.\n\n" + OLD_BLOCK)
    _refresh(monkeypatch, "--yes")
    text = path.read_text(encoding="utf-8")
    assert policy.installed_policy_drift(text) == {"state": "current"}
    assert text.startswith("# Mine\n\nKeep this.\n\n")
    assert "Never fall back" not in text
    out = capsys.readouterr().out
    assert "-Always use jCodemunch-MCP tools" in out, "the diff is shown before the write"


@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_every_byte_outside_the_block_is_kept(home, monkeypatch, newline):
    h, _, _ = home
    path = _global(h)
    before = "# Mine\n\nKeep this.\n\n"
    after = "\n## My own notes\n\nAlso keep this.\n"
    _write(path, before + OLD_BLOCK + after, newline)
    _refresh(monkeypatch, "--yes")
    raw = path.read_bytes()
    nl = newline.encode()
    assert raw.startswith(before.replace("\n", newline).encode())
    assert raw.endswith(after.replace("\n", newline).encode())
    if newline == "\r\n":
        assert raw.count(b"\n") == raw.count(b"\r\n"), "a CRLF file stays CRLF throughout"
    middle = raw[len(before.replace("\n", newline)) : len(raw) - len(after.replace("\n", newline))]
    assert middle.replace(nl, b"\n").decode("utf-8").rstrip("\n") == _current().rstrip("\n")


def test_a_file_with_no_final_newline_keeps_none(home, monkeypatch):
    h, _, _ = home
    path = _global(h)
    _write(path, "# Mine\n\n" + OLD_BLOCK.rstrip("\n"))
    _refresh(monkeypatch, "--yes")
    raw = path.read_bytes()
    assert not raw.endswith(b"\n")
    assert policy.installed_policy_drift(raw.decode("utf-8")) == {"state": "current"}


def test_without_a_terminal_and_without_yes_nothing_is_written(home, monkeypatch, capsys):
    h, _, _ = home
    path = _global(h)
    data = _write(path, OLD_BLOCK)
    _refresh(monkeypatch, tty=False)
    assert path.read_bytes() == data
    assert _backups(path) == []
    out = capsys.readouterr().out
    assert "-Always use jCodemunch-MCP tools" in out
    assert "--yes" in out, "it says how to write it"


@pytest.mark.parametrize("answer", ["", "n", "no", "N", "maybe"])
def test_anything_but_yes_writes_nothing(home, monkeypatch, answer):
    h, _, _ = home
    path = _global(h)
    data = _write(path, OLD_BLOCK)
    _refresh(monkeypatch, answer=answer, tty=True)
    assert path.read_bytes() == data
    assert _backups(path) == []


@pytest.mark.parametrize("answer", ["y", "yes", "Y", " yes "])
def test_yes_at_the_prompt_writes(home, monkeypatch, answer):
    h, _, _ = home
    path = _global(h)
    _write(path, OLD_BLOCK)
    _refresh(monkeypatch, answer=answer, tty=True)
    assert policy.installed_policy_drift(path.read_text(encoding="utf-8")) == {"state": "current"}


def test_dry_run_writes_nothing_even_with_yes(home, monkeypatch, capsys):
    h, _, _ = home
    path = _global(h)
    data = _write(path, OLD_BLOCK)
    _refresh(monkeypatch, "--dry-run", "--yes")
    assert path.read_bytes() == data
    assert _backups(path) == []
    assert "-Always use" in capsys.readouterr().out


def test_every_write_keeps_the_previous_bytes_and_never_overwrites_a_backup(home, monkeypatch):
    h, _, _ = home
    path = _global(h)
    first = _write(path, "# A\n\n" + OLD_BLOCK)
    _refresh(monkeypatch, "--yes")
    [b1] = _backups(path)
    assert b1.read_bytes() == first
    second = _write(path, "# B\n\n" + OLD_BLOCK)
    _refresh(monkeypatch, "--yes")
    backups = _backups(path)
    assert len(backups) == 2
    assert b1.read_bytes() == first, "the earlier backup is untouched"
    assert {b.read_bytes() for b in backups} == {first, second}


def test_a_file_changed_after_its_diff_was_shown_is_not_written(home, monkeypatch, capsys):
    """The user approved one diff; a write over different bytes would apply a change nobody saw."""
    h, _, _ = home
    path = _global(h)
    _write(path, OLD_BLOCK)
    edited = OLD_BLOCK + "\nA line the user added while the prompt was open.\n"

    def _answer(prompt=""):
        path.write_text(edited, encoding="utf-8")
        return "y"

    monkeypatch.setattr(policy_refresh, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr("builtins.input", _answer)
    with pytest.raises(SystemExit) as stop:
        server.main(["claude-md", "--refresh"])
    assert stop.value.code == 1
    assert path.read_text(encoding="utf-8") == edited
    assert _backups(path) == []
    assert "changed after its diff was shown" in capsys.readouterr().out


def test_the_project_file_is_refreshed_too(home, monkeypatch):
    h, work, _ = home
    proj = work / "CLAUDE.md"
    _write(proj, OLD_BLOCK)
    _refresh(monkeypatch, "--yes")
    assert policy.installed_policy_drift(proj.read_text(encoding="utf-8")) == {"state": "current"}


@pytest.mark.parametrize(
    "text",
    [
        None,
        "# Mine\n\nNo policy here.\n",
        "# Mine\n\nCall the jcodemunch_guide tool and strictly follow its instructions.\n",
        "CURRENT",
    ],
    ids=["no-file", "no-block", "one-line-guide-form", "already-current"],
)
def test_nothing_to_do_is_left_alone(home, monkeypatch, capsys, text):
    h, _, _ = home
    path = _global(h)
    if text == "CURRENT":
        text = "# Mine\n\n" + _current()
    data = _write(path, text) if text is not None else None
    _refresh(monkeypatch, "--yes")
    if data is None:
        assert not path.exists()
    else:
        assert path.read_bytes() == data
        assert _backups(path) == []
    assert "Nothing to refresh" in capsys.readouterr().out


def test_a_users_own_section_after_the_block_is_not_part_of_it(home, monkeypatch):
    """The block ends at the first `## ` heading that is not one of the policy's own."""
    h, _, _ = home
    path = _global(h)
    _write(path, OLD_BLOCK + "\n## Deploying\n\nAlways use the staging box.\n")
    _refresh(monkeypatch, "--yes")
    text = path.read_text(encoding="utf-8")
    assert text.endswith("\n## Deploying\n\nAlways use the staging box.\n")


@pytest.mark.parametrize("heading", ["# Deploying", "### Deploying", "#### Deploying"])
def test_a_users_section_under_any_heading_level_is_kept(home, monkeypatch, heading):
    """The policy at every release tag uses only `## ` headings; any other heading is the user's (review, 2026-10-14)."""
    h, _, _ = home
    path = _global(h)
    tail = f"\n{heading}\n\nKeep me.\n"
    _write(path, OLD_BLOCK + tail)
    _refresh(monkeypatch, "--yes")
    assert path.read_text(encoding="utf-8").endswith(tail)


def test_a_heading_inside_a_code_fence_does_not_end_the_block():
    """A `# comment` in a fenced shell snippet is not a heading; ending there would split the fence."""
    fence = "`" * 3
    lines = (OLD_BLOCK + f"{fence}bash\n# a comment\necho hi\n{fence}\n\n# Mine\n").splitlines()
    assert policy.policy_block_span(lines) == (0, lines.index("# Mine"))


def test_init_and_the_refresh_agree_that_a_sentence_is_not_a_block(home):
    """`init` asks the same boundary rule, so it installs a policy beside a sentence that names one."""
    h, _, _ = home
    path = _global(h)
    _write(path, "See the ## Code Exploration Policy section.\n")
    from jcodemunch_mcp.cli import init

    assert "would append" in init.install_claude_md("global", dry_run=True)
    assert policy.installed_policy_drift(path.read_text(encoding="utf-8")) is None


def test_a_sentence_naming_the_policy_is_not_the_block(home, monkeypatch):
    h, _, _ = home
    path = _global(h)
    before = "# Me\nSee the ## Code Exploration Policy section below.\nMY OWN TEXT\n\n"
    _write(path, before + OLD_BLOCK)
    _refresh(monkeypatch, "--yes")
    text = path.read_text(encoding="utf-8")
    assert text.startswith(before)
    assert policy.installed_policy_drift(text) == {"state": "current"}


def test_a_byte_order_mark_is_kept(home, monkeypatch):
    h, _, _ = home
    path = _global(h)
    _write(path, chr(0xFEFF) + OLD_BLOCK)
    _refresh(monkeypatch, "--yes")
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf## Code Exploration Policy")
    text = raw.decode("utf-8")
    assert policy.installed_policy_drift(text) == {"state": "current"}, "it was found and refreshed, not skipped"
    assert "Never fall back" not in text


def test_a_mixed_file_keeps_each_parts_line_endings(home, monkeypatch):
    h, _, _ = home
    path = _global(h)
    head = "# Mine\r\n\r\n"
    path.write_bytes((head + OLD_BLOCK).encode("utf-8"))  # CRLF head, LF block
    _refresh(monkeypatch, "--yes")
    raw = path.read_bytes()
    assert raw.startswith(head.encode())
    assert b"\r\n" not in raw[len(head):], "the block was LF and stays LF"


def test_a_symlinked_file_is_written_through_the_link(home, monkeypatch, tmp_path):
    """A dotfiles manager links CLAUDE.md; the link stays a link and its source gets the text."""
    h, _, _ = home
    source = tmp_path / "dotfiles" / "CLAUDE.md"
    source.parent.mkdir()
    _write(source, OLD_BLOCK)
    link = _global(h)
    try:
        link.symlink_to(source)
    except OSError:
        pytest.skip("this box cannot create a symlink without privileges")
    _refresh(monkeypatch, "--yes")
    assert link.is_symlink()
    assert policy.installed_policy_drift(source.read_text(encoding="utf-8")) == {"state": "current"}
    assert [b.read_text(encoding="utf-8") for b in _backups(source)] == [OLD_BLOCK]


# -- the one boundary rule ------------------------------------------------------


def test_the_command_and_the_drift_check_share_one_boundary():
    lines = (OLD_BLOCK + "\n## Deploying\n\nx\n").splitlines()
    span = policy.policy_block_span(lines)
    assert span == (0, lines.index("## Deploying"))
    assert policy.installed_policy_block("\n".join(lines)) == policy._normalise(lines[span[0] : span[1]])
    assert policy.policy_block_span(["# nothing"]) is None


# -- the notice -------------------------------------------------------------------


def _call(tool: str, args: dict) -> dict:
    out = asyncio.run(server.call_tool(tool, args))
    content = out.content if hasattr(out, "content") else out
    return json.loads(content[0].text)


@pytest.fixture()
def notice_on(home, monkeypatch):
    monkeypatch.setitem(policy_refresh.NOTICE_STATE, "checked", False)
    return home


def test_the_first_resolve_repo_answer_names_the_command(notice_on):
    h, work, store = notice_on
    path = _global(h)
    _write(path, OLD_BLOCK)
    notice = _call("resolve_repo", {"path": str(work)}).get("policy_notice")
    assert notice, "an install holding a differing block is told the command exists"
    assert notice["command"] == "jcodemunch-mcp claude-md --refresh"
    assert notice["command"] in notice["message"]
    [f] = notice["files"]
    assert f["path"] == str(path)
    assert f["lines_differing"] == policy.installed_policy_drift(path.read_text(encoding="utf-8"))["lines_differing"]
    assert "backup" in notice["message"] and "asks" in notice["message"]
    assert "on purpose" in notice["message"], "a block edited on purpose is the user's to keep"
    assert "Do not run" in notice["relay"]
    assert (store / MARKER).is_file()


def test_it_is_said_once(notice_on, monkeypatch):
    h, work, store = notice_on
    _write(_global(h), OLD_BLOCK)
    assert _call("resolve_repo", {"path": str(work)}).get("policy_notice")
    assert "policy_notice" not in _call("resolve_repo", {"path": str(work)})
    monkeypatch.setitem(policy_refresh.NOTICE_STATE, "checked", False)  # a new process, marker on disk
    assert "policy_notice" not in _call("resolve_repo", {"path": str(work)})


def test_a_new_policy_text_is_news_again(notice_on, monkeypatch):
    """The marker records which policy it was about; a later version's policy is a new fact."""
    h, work, store = notice_on
    _write(_global(h), OLD_BLOCK)
    assert _call("resolve_repo", {"path": str(work)}).get("policy_notice")
    marker = json.loads((store / MARKER).read_text(encoding="utf-8"))
    marker["policy_digest"] = "0" * 64
    (store / MARKER).write_text(json.dumps(marker), encoding="utf-8")
    monkeypatch.setitem(policy_refresh.NOTICE_STATE, "checked", False)
    assert _call("resolve_repo", {"path": str(work)}).get("policy_notice")


@pytest.mark.parametrize("text", [None, "# Mine\n", "CURRENT"], ids=["no-file", "no-block", "current"])
def test_nothing_is_said_and_nothing_spent_when_there_is_nothing_to_refresh(notice_on, monkeypatch, text):
    h, work, store = notice_on
    if text == "CURRENT":
        text = _current()
    if text is not None:
        _write(_global(h), text)
    assert "policy_notice" not in _call("resolve_repo", {"path": str(work)})
    assert not (store / MARKER).exists(), "nothing said, so nothing recorded"


def test_an_error_answer_does_not_spend_it(notice_on):
    h, work, store = notice_on
    _write(_global(h), OLD_BLOCK)
    assert "policy_notice" not in _call("resolve_repo", {})
    assert not (store / MARKER).exists()
    assert _call("resolve_repo", {"path": str(work)}).get("policy_notice")


def test_the_suite_keeps_it_off_by_default(home):
    """conftest switches it off, as it does the surface notice: otherwise whichever test asked first gets it."""
    h, work, store = home
    _write(_global(h), OLD_BLOCK)
    assert "policy_notice" not in _call("resolve_repo", {"path": str(work)})


# -- the other places that report a differing block --------------------------------


def test_init_and_config_check_name_the_command(home, monkeypatch, capsys):
    h, _, _ = home
    path = _global(h)
    _write(path, OLD_BLOCK)
    from jcodemunch_mcp.cli import init

    assert "claude-md --refresh" in init.install_claude_md("global", dry_run=True)
    import inspect

    assert "claude-md --refresh" in inspect.getsource(server._run_config)


def test_security_md_discloses_the_marker_and_the_reads():
    text = (Path(__file__).resolve().parent.parent / "SECURITY.md").read_text(encoding="utf-8")
    assert MARKER in text
    assert "claude-md --refresh" in text
