"""Published docs run the executable the package installs, `jcodemunch-mcp`.

`benchmarks/README.md` and the whitepaper's reproduction appendix told readers
to run `jcodemunch index_repo expressjs/express`. No `jcodemunch` executable
exists (pyproject's console script is `jcodemunch-mcp`), and `index_repo`
indexes whatever upstream `main` is today, so even a corrected command would
not reproduce numbers measured on pinned commits. The pinned steps live in
`benchmarks/REPRODUCING.md`; the other docs point there.
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# History files quote what shipped at the time; they are not instructions.
_HISTORY = {"CHANGELOG.md", "ISSUE-HISTORY.md"}


def _dispatcher_names() -> tuple[set[str], set[str]]:
    """The CLI's subcommands and its MCP-tool aliases, read from the dispatcher."""
    tree = ast.parse((REPO / "src" / "jcodemunch_mcp" / "server.py").read_text(encoding="utf-8"))
    commands: set[str] = set()
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            name = getattr(node.targets[0], "id", None)
            if name == "known_commands" and isinstance(node.value, ast.Set):
                commands |= {e.value for e in node.value.elts if isinstance(e, ast.Constant)}
            if name == "_CLI_ALIASES" and isinstance(node.value, ast.Dict):
                aliases |= {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
    assert "index" in commands and "index_repo" in aliases, "dispatcher's verb sets not found"
    return commands, aliases


def _cli_verbs() -> set[str]:
    commands, aliases = _dispatcher_names()
    return commands | aliases


def _alternation(words: set[str]) -> str:
    return "|".join(sorted((re.escape(w) for w in words), key=len, reverse=True))


# `jcodemunch <subcommand>` written as a command: at a line start, after a
# prompt, or opening an inline code span. A line that starts with the product
# name followed by a word that is not a verb ("jcodemunch reads ...") is not
# a hit; one followed by a verb ("jcodemunch index ...") is, even in prose.
_BARE = re.compile(
    r"(?:^|`|\$ )jcodemunch +(?:" + _alternation(_cli_verbs()) + r")(?![\w-])",
    re.MULTILINE,
)

# The MCP-tool spellings the CLI accepts only as aliases (`index_repo`, ...),
# read from `_CLI_ALIASES` so a new alias is covered on arrival. They are
# never prose, so they are a hit anywhere, in any tracked text file: the
# benchmark harness printed one in its "not indexed" error.
_ALIAS = re.compile(r"\bjcodemunch +(?:" + _alternation(_dispatcher_names()[1]) + r")(?![\w-])")


def _tracked(*patterns: str) -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z", *patterns], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.split("\0")
    return [REPO / p for p in out if p and Path(p).name not in _HISTORY]


def _tracked_markdown() -> list[Path]:
    return _tracked("*.md")


def test_the_console_script_is_jcodemunch_mcp():
    # tomllib is 3.11+; the project supports 3.10, so read the table by hand.
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    table = text.split("[project.scripts]", 1)[1].split("\n[", 1)[0]
    scripts = {line.split("=", 1)[0].strip() for line in table.splitlines() if "=" in line}
    assert "jcodemunch-mcp" in scripts
    assert "jcodemunch" not in scripts


def test_no_doc_runs_a_bare_jcodemunch_command():
    hits = []
    for path in _tracked_markdown():
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in _BARE.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            hits.append(f"{path.relative_to(REPO).as_posix()}:{line}: {m.group(0)}")
    assert hits == [], "docs name an executable that does not exist:\n" + "\n".join(hits)


def test_no_tracked_text_suggests_an_mcp_tool_name_as_a_command():
    hits = []
    for path in _tracked("*.md", "*.py", "*.txt", "*.yml", "*.toml"):
        if path.name == Path(__file__).name:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in _ALIAS.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            hits.append(f"{path.relative_to(REPO).as_posix()}:{line}: {m.group(0)}")
    assert hits == [], "tells a reader to run a command that does not exist:\n" + "\n".join(hits)


def test_the_alias_scan_finds_a_planted_message():
    assert _ALIAS.search('return {"error": f"Not indexed: x. Run: jcodemunch index_repo x"}')
    assert not _ALIAS.search("Cross-references against the jcodemunch index to catch")
    assert not _ALIAS.search("jcodemunch-mcp index ./express")


def test_the_scan_finds_a_planted_bare_command():
    planted = "Run this:\n\n```bash\njcodemunch index_repo expressjs/express\n```\n"
    assert _BARE.search(planted)
    assert not _BARE.search("jcodemunch-mcp index ./express --no-ai-summaries")
    assert not _BARE.search("the jcodemunch index is fast")


def test_the_benchmark_readme_points_at_the_pinned_steps():
    readme = (REPO / "benchmarks" / "README.md").read_text(encoding="utf-8")
    section = readme.split("## Reproducing the results", 1)[1].split("\n## ", 1)[0]
    assert "REPRODUCING.md" in section
    assert "index_repo" not in section
