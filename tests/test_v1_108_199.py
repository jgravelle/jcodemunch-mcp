"""Regression tests for v1.108.199 — three findings from @rknighton.

- #392 `search_text` hangs on a zero-result query in a persistent stdio server
  on Windows, because the working-tree git probe inherits the server's stdin.
- #393 `config --check` reports env-derived AI Summarizer values instead of the
  effective loaded configuration.
- #394 `find_references` enriches the full match set and then discards
  everything past `max_results`.
"""

from __future__ import annotations

import ast
import pathlib
import subprocess
import sys

import pytest


# ── #392: no in-server subprocess may inherit the JSON-RPC stdin ────────────

SRC_ROOT = pathlib.Path(__file__).resolve().parents[1] / "src" / "jcodemunch_mcp"

_SPAWNERS = {"run", "Popen", "call", "check_call", "check_output"}

# Modules that own their own process and whose stdin is a terminal, not the MCP
# JSON-RPC channel. Inheriting stdin there is correct (an installer may prompt),
# so they are exempt BY NAME — a new server-path module cannot land in here by
# accident, it has to be added deliberately.
_NOT_SERVER_PATH = {
    "cli/hooks/reindex.py",
    "cli/init.py",
    "cli/upgrade.py",
    "service_installer.py",
    "groq/explainer.py",
}

# Sites that deliberately pass a stdin OTHER than DEVNULL, with the reason each
# one is safe. Both hand the child a source that terminates on its own, which is
# what #392's inherited-and-never-written JSON-RPC pipe does not.
_INTENTIONAL_PIPE = {
    # Speaks LSP over the child's stdin; PIPE is the entire point.
    "enrichment/lsp_bridge.py",
    # Feeds the .mmd file to the viewer over stdin from an open REGULAR FILE
    # handle, not the server's stdin. A real file reaches EOF, so the child
    # cannot block on it the way #392's child blocked on the JSON-RPC channel.
    # Surfaced by the DEVNULL check when this guard was ported to jdoc/jdata.
    "tools/mermaid_viewer.py",
}


def _spawn_sites() -> list[tuple[str, int, bool]]:
    """(relative_path, lineno, passes_stdin) for every subprocess spawn in src."""
    sites: list[tuple[str, int, bool]] = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rel = path.relative_to(SRC_ROOT).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "subprocess"
                and func.attr in _SPAWNERS
            ):
                kwargs = {kw.arg for kw in node.keywords}
                sites.append((rel, node.lineno, "stdin" in kwargs))
    return sites


def test_working_tree_probe_does_not_inherit_stdin():
    """#392: the specific site that hung.

    Fails on revert: the probe launched Git without `stdin=`, Git for Windows
    inherited the live JSON-RPC stdin, and its child kept the captured pipes
    open past the `timeout=` that killed the immediate process.
    """
    src = (SRC_ROOT / "retrieval" / "subject_state.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    probes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "subprocess"
        and node.func.attr in _SPAWNERS
    ]
    assert probes, "expected at least one subprocess spawn in subject_state.py"
    for probe in probes:
        kwargs = {kw.arg: kw.value for kw in probe.keywords}
        assert "stdin" in kwargs, (
            f"subject_state.py:{probe.lineno} spawns a process without stdin=; "
            "inside the MCP server that stdin IS the JSON-RPC channel (#392)"
        )
        value = kwargs["stdin"]
        assert (
            isinstance(value, ast.Attribute) and value.attr == "DEVNULL"
        ), f"subject_state.py:{probe.lineno} must pass stdin=subprocess.DEVNULL"


def test_no_server_path_subprocess_inherits_stdin():
    """#392, generalised: the convention was already everywhere and unenforced.

    Every other in-server git probe already passed `stdin=DEVNULL`, with the
    deadlock named in a comment (`git_root.py`, jcm#303 follow-up). Nothing
    checked it, so a call site added later silently opted out and reintroduced
    the hang. This is the check that was missing.
    """
    offenders = [
        f"{rel}:{lineno}"
        for rel, lineno, has_stdin in _spawn_sites()
        if not has_stdin and rel not in _NOT_SERVER_PATH and rel not in _INTENTIONAL_PIPE
    ]
    assert not offenders, (
        "these run inside the MCP server process and must pass stdin=subprocess.DEVNULL "
        f"(#392): {offenders}"
    )


def test_stdin_guard_is_not_vacuous():
    """The walker must actually find server-path spawns.

    Back-ported from the jdoc/jdata port of this guard. Without it, the check
    above passes trivially if the AST walk stops matching, if the package moves,
    or if every spawn migrates behind a helper it cannot see. A structural test
    that cannot fail reads as coverage and provides none.
    """
    covered = [
        s for s in _spawn_sites()
        if s[0] not in _NOT_SERVER_PATH and s[0] not in _INTENTIONAL_PIPE
    ]
    assert covered, (
        "no server-path subprocess spawns found in src/jcodemunch_mcp. Either the "
        "AST walker stopped matching or the spawns moved. Fix the walker rather "
        "than deleting this file."
    )


def test_server_path_stdin_is_devnull():
    """`stdin=` alone isn't enough: PIPE would reintroduce a blocking child.

    Also back-ported from the jdoc port. `_INTENTIONAL_PIPE` is the one place
    PIPE is correct (lsp_bridge speaks LSP over the child's stdin); everywhere
    else DEVNULL is the only value that closes #392.
    """
    wrong: list[str] = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        rel = path.relative_to(SRC_ROOT).as_posix()
        if rel in _NOT_SERVER_PATH or rel in _INTENTIONAL_PIPE:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "subprocess"
                and func.attr in _SPAWNERS
            ):
                continue
            for kw in node.keywords:
                if kw.arg == "stdin" and not (
                    isinstance(kw.value, ast.Attribute) and kw.value.attr == "DEVNULL"
                ):
                    wrong.append(f"{rel}:{node.lineno}")
    assert not wrong, f"server-path stdin must be subprocess.DEVNULL: {wrong}"


def test_exemption_lists_have_no_dead_entries():
    """A stale exemption is a hole nobody can see. If a listed module stops
    spawning processes, drop it from the list rather than leaving cover for a
    future call site with the same path."""
    spawning = {rel for rel, _, _ in _spawn_sites()}
    dead = (_NOT_SERVER_PATH | _INTENTIONAL_PIPE) - spawning
    assert not dead, f"exemption entries no longer spawn any process: {sorted(dead)}"


@pytest.mark.skipif(sys.platform != "win32", reason="the deadlock is Windows-specific")
def test_working_tree_probe_returns_with_a_closed_stdin(tmp_path):
    """End-to-end at the user's entry point: the probe answers when the parent's
    stdin is a pipe nobody writes to — the shape of a live stdio server.

    Vacuous as a proof of the FIX on a box where Git for Windows does not wrap;
    it is here so the call cannot start blocking on stdin again undetected.
    """
    subprocess.run(
        ["git", "init"], cwd=tmp_path, capture_output=True,
        stdin=subprocess.DEVNULL, timeout=30, check=False,
    )
    script = (
        "import sys;"
        f"sys.path.insert(0, {str(SRC_ROOT.parent)!r});"
        "from jcodemunch_mcp.retrieval.subject_state import _working_tree_reading;"
        f"print(_working_tree_reading({str(tmp_path)!r}) is not None)"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        stdin=subprocess.PIPE,  # open, and deliberately never written to
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        out, err = proc.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        pytest.fail("the working-tree probe blocked on an inherited stdin (#392)")
    assert proc.returncode == 0, err.decode("utf-8", "replace")
    assert out.decode("utf-8", "replace").strip() == "True"


# ── #393: config --check must report the LOADED config ──────────────────────


def _run_config_check(tmp_path, config_body: str, env_extra: dict) -> str:
    """Run `config --check` in a subprocess against a scratch CODE_INDEX_PATH.

    A subprocess, not an in-process call, because the defect is about what the
    command PRINTS to a person — and because config load is global module state.
    """
    import os

    (tmp_path / "config.jsonc").write_text(config_body, encoding="utf-8")
    env = os.environ.copy()
    env["CODE_INDEX_PATH"] = str(tmp_path)
    # Neutralise the ambient environment so the assertions describe the config
    # file, not the developer's shell.
    for var in (
        "JCODEMUNCH_USE_AI_SUMMARIES",
        "JCODEMUNCH_SUMMARIZER_PROVIDER",
        "JCODEMUNCH_SUMMARIZER_MODEL",
    ):
        env.pop(var, None)
    env.update(env_extra)
    env["PYTHONPATH"] = str(SRC_ROOT.parent)
    proc = subprocess.run(
        [sys.executable, "-m", "jcodemunch_mcp", "config", "--check"],
        capture_output=True, text=True, timeout=180,
        stdin=subprocess.DEVNULL, env=env, cwd=str(tmp_path),
    )
    # `config --check` exits 1 when it reports any prerequisite issue (an
    # uninstalled optional AI package is enough), so a nonzero exit is normal
    # here. What is under test is what it PRINTS.
    assert proc.returncode in (0, 1), proc.stderr
    return proc.stdout


def _row_value(output: str, key: str) -> str:
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith(key) and (
            len(stripped) == len(key) or stripped[len(key)] == " "
        ):
            return stripped[len(key):].strip()
    raise AssertionError(f"row {key!r} not found in:\n{output}")


def test_config_check_reports_disabled_summarizer_from_config(tmp_path):
    """#393 exactly: the reporter's config, the reporter's expected output.

    Fails on revert with `true` and `(auto-detect)`.
    """
    out = _run_config_check(
        tmp_path,
        '{\n  "use_ai_summaries": false,\n  "summarizer_provider": "none"\n}\n',
        {},
    )
    assert "false" in _row_value(out, "use_ai_summaries")
    assert "true" not in _row_value(out, "use_ai_summaries")
    assert "none" in _row_value(out, "summarizer_provider")
    assert "auto-detect" not in _row_value(out, "summarizer_provider")


def test_config_check_disabled_banner_follows_the_config(tmp_path):
    """The second half of #393, which the report did not have to name.

    The `AI summaries disabled` banner branched on the same env-only read, so a
    config-disabled summarizer printed an `Active provider` line instead. A user
    checking whether they had turned it off got told twice that they had not.
    """
    out = _run_config_check(
        tmp_path,
        '{\n  "use_ai_summaries": false,\n  "summarizer_provider": "none"\n}\n',
        {"ANTHROPIC_API_KEY": "sk-ant-not-a-real-key-for-display-only"},
    )
    assert "AI summaries disabled" in out
    assert "Active provider" not in out


def test_config_check_env_still_wins_when_config_is_silent(tmp_path):
    """Reading _cfg must not cost the env-var path. `config.jsonc` omits the
    keys here, so the env fallback is the effective source."""
    out = _run_config_check(
        tmp_path,
        "{\n}\n",
        {
            "JCODEMUNCH_USE_AI_SUMMARIES": "false",
            "JCODEMUNCH_SUMMARIZER_PROVIDER": "gemini",
        },
    )
    assert "false" in _row_value(out, "use_ai_summaries")
    assert "gemini" in _row_value(out, "summarizer_provider")


def test_config_check_renders_the_auto_tristate(tmp_path):
    """`use_ai_summaries` is tri-state (True/False/"auto"). The old code
    collapsed it to a bool before display, so `auto` was unreportable. Render
    the configured value AND where auto landed — collapsing it again would
    answer a different question than the one the row asks."""
    out = _run_config_check(tmp_path, '{\n  "use_ai_summaries": "auto"\n}\n', {})
    value = _row_value(out, "use_ai_summaries")
    assert "auto" in value


def test_config_check_summarizer_rows_read_the_config_shim():
    """Structural guard on the fix itself: the two rows must not go back to
    reading os.environ behind a hardcoded default. The `summarizer_model` row
    below them was already fixed this way once (#300/#304) and its neighbours
    were left on the old path for three years of releases — the pattern
    survives review, so pin it."""
    src = (SRC_ROOT / "server.py").read_text(encoding="utf-8")
    start = src.index('section("AI Summarizer")')
    block = src[start:src.index("summarizer_model display", start)]
    assert 'env("JCODEMUNCH_USE_AI_SUMMARIES"' not in block
    assert 'env("JCODEMUNCH_SUMMARIZER_PROVIDER"' not in block
    assert '_cfg.get("use_ai_summaries"' in block
    assert '_cfg.get("summarizer_provider"' in block


# ── #394: slice before enrichment in find_references ────────────────────────


class _CountingStore:
    """Records every file read so the test asserts WORK, not wall-clock.

    A timing assertion here would be a flaky gate that passes on a fast box
    with the bug present. The read count is exact and deterministic.
    """

    def __init__(self, contents: dict[str, str]):
        self._contents = contents
        self.reads: list[str] = []

    def get_file_content(self, owner, name, path):
        self.reads.append(path)
        return self._contents.get(path, "")


class _FakeIndex:
    def __init__(self, imports, symbols=None):
        self.imports = imports
        self.symbols = symbols or []
        self._import_name_index = None


def _build_reference_fixture(n_files: int):
    """`n_files` files each importing `target`, plus their contents."""
    imports = {}
    contents = {}
    for i in range(n_files):
        path = f"pkg/mod_{i:04d}.py"
        imports[path] = [{"specifier": "target", "names": ["target"]}]
        contents[path] = 'from "target" import target\n\n\ndef caller():\n    target()\n'
    return imports, contents


def _call(identifier, index, store, max_results, include_call_chain=False):
    import time as _time

    from jcodemunch_mcp.tools.find_references import _find_references_single

    return _find_references_single(
        identifier, index, max_results, "local", "fixture",
        _time.perf_counter(), include_call_chain=include_call_chain, store=store,
    )


def test_find_references_reads_only_the_files_it_returns():
    """#394: the work-count assertion the report asked for.

    Fails on revert with 300 reads instead of 50.
    """
    imports, contents = _build_reference_fixture(300)
    store = _CountingStore(contents)
    result = _call("target", _FakeIndex(imports), store, max_results=50)

    assert len(result["references"]) == 50
    assert len(store.reads) == 50, (
        f"read {len(store.reads)} files to return 50 references; "
        "enrichment must run on the slice, not the full match set (#394)"
    )
    assert store.reads == [r["file"] for r in result["references"]]


def test_find_references_call_chain_also_respects_the_limit():
    """`include_call_chain=True` runs a SECOND pass over the same list. Fixing
    only the first loop would have halved the waste and left the shape."""
    imports, contents = _build_reference_fixture(300)
    symbols = [
        {"id": f"{p}::caller#function", "name": "caller", "kind": "function", "line": 4}
        | {"file": p}
        for p in contents
    ]
    store = _CountingStore(contents)
    result = _call(
        "target", _FakeIndex(imports, symbols), store,
        max_results=50, include_call_chain=True,
    )

    assert len(result["references"]) == 50
    # One read per visible file for the import line, one more for the call chain.
    assert len(store.reads) == 100
    assert set(store.reads) == {r["file"] for r in result["references"]}
    assert all("calling_symbols" in r for r in result["references"])


def test_find_references_counts_still_describe_the_full_match_set():
    """The invariant that makes the slice safe: `reference_count` and
    `_meta.truncated` are computed from everything found, not from what was
    served. If these ever start reading the slice, the tool would report 50
    references in a repo with 300 and nothing would look wrong."""
    imports, contents = _build_reference_fixture(300)
    result = _call("target", _FakeIndex(imports), _CountingStore(contents), max_results=50)

    assert result["reference_count"] == 300
    assert result["_meta"]["truncated"] is True


def test_find_references_response_is_unchanged_below_the_limit():
    """The no-op half: under `max_results` the slice changes nothing, which is
    the common case for most identifiers in most repositories."""
    imports, contents = _build_reference_fixture(12)
    store = _CountingStore(contents)
    result = _call("target", _FakeIndex(imports), store, max_results=50)

    assert result["reference_count"] == 12
    assert len(result["references"]) == 12
    assert result["_meta"]["truncated"] is False
    assert len(store.reads) == 12
    assert all(m.get("line") == 1 for r in result["references"] for m in r["matches"])
