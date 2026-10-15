"""``jcodemunch-mcp upgrade`` — package upgrade + refresh hooks/config.

Single command for the post-release path that previously required two
steps (``pip install -U jcodemunch-mcp`` and ``jcodemunch-mcp init
--hooks``). Picked up from issue #273: users on Copilot/parallel-session
workflows kept missing the second step and ending up with stale hook
templates pointing at older binaries.

Install-mechanism aware (#357): a pipx- or uv-managed venv ships no
``pip`` module, so the old ``python -m pip install -U`` died with
``No module named pip`` and skipped the hook refresh entirely. We do NOT
guess-and-run a foreign package manager; instead we detect the mechanism,
print the exact upgrade command for it, and still refresh hooks/config
in-process (which needs no pip).
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys


def _pip_available() -> bool:
    """True when ``pip`` is importable in the current interpreter.

    pipx and uv-tool venvs intentionally omit pip, so ``python -m pip``
    fails with ``No module named pip`` rather than upgrading anything.
    """
    try:
        return importlib.util.find_spec("pip") is not None
    except (ImportError, ValueError):
        return False


def detect_install_mechanism() -> tuple[str, str | None]:
    """Best-effort detection of how jcodemunch-mcp was installed.

    Returns ``(mechanism, upgrade_command)`` where ``mechanism`` is one of
    ``pip`` / ``pipx`` / ``uv`` / ``uvx`` / ``venv``, and ``upgrade_command``
    is the exact shell command to upgrade the package outside this process
    (or ``None`` for plain pip, where in-process ``pip install -U`` works).

    Detection is path/env heuristic — it never runs a subprocess.
    """
    exe = (sys.executable or "").replace("\\", "/").lower()

    # pipx: venvs live under .../pipx/venvs/<pkg>/...
    if "/pipx/venvs/" in exe or "/pipx/venv/" in exe or os.environ.get("PIPX_HOME"):
        return "pipx", "pipx upgrade jcodemunch-mcp"

    # uv tool install: .../uv/tools/<pkg>/...
    if "/uv/tools/" in exe:
        return "uv", "uv tool upgrade jcodemunch-mcp"

    # uvx ephemeral run: resolved out of the uv cache; nothing to "upgrade"
    # persistently, so point at the @latest refresh form.
    if "/uv/" in exe or "/.cache/uv/" in exe or "/uv/cache/" in exe:
        return "uvx", "uvx jcodemunch-mcp@latest --version"

    # Plain virtualenv without pip (rare) — caller must upgrade it themselves.
    if not _pip_available():
        return "venv", None

    return "pip", None


def watch_extra_install_command() -> str:
    """Install-mechanism-appropriate command to add the optional ``watch`` extra.

    The ``watch`` extra pulls in ``watchfiles``. A bare ``pip install
    'jcodemunch-mcp[watch]'`` only works under plain pip; a pipx/uv-managed
    install has no reachable ``pip`` and needs ``pipx inject`` / ``uv tool
    install`` instead (the same install-mechanism blind spot as #357). Reuses
    :func:`detect_install_mechanism` so the hint matches how jcm was installed.
    """
    mechanism, _ = detect_install_mechanism()
    if mechanism == "pipx":
        return "pipx inject jcodemunch-mcp watchfiles"
    if mechanism == "uv":
        return "uv tool install --force 'jcodemunch-mcp[watch]'"
    if mechanism == "uvx":
        return "uvx --with watchfiles jcodemunch-mcp <command>"
    # pip, or a pip-less venv where we can't do better than the canonical form.
    return "pip install 'jcodemunch-mcp[watch]'"


def _refresh_hooks(*, yes: bool = True) -> int:
    """Refresh hook templates/config via ``init --hooks`` (no pip required).

    Runs this install's own executable (`install_layout`), which the upgrade
    just replaced in place; otherwise PATH's; otherwise the in-process module
    entry so a venv shim isn't bypassed.
    """
    from ..install_layout import executable_for_commands

    exe = executable_for_commands()
    if exe:
        init_cmd = [exe, "init", "--hooks"]
    else:
        init_cmd = [sys.executable, "-m", "jcodemunch_mcp", "init", "--hooks"]
    if yes:
        init_cmd.append("--yes")

    print(f"$ {' '.join(init_cmd)}")
    try:
        r = subprocess.run(init_cmd, check=False)
    except OSError as e:
        print(f"  init invocation failed: {e}", file=sys.stderr)
        return 1
    return r.returncode


def _print_external_upgrade_hint(mechanism: str, command: str | None) -> None:
    """Tell the user the exact command for their install mechanism."""
    if command:
        print(
            f"pip is not available in this environment "
            f"({mechanism}-managed install detected)."
        )
        print("To upgrade the package, run:")
        print(f"    {command}")
        print("Then restart your editor / MCP client so it relaunches the server.")
    else:
        print(
            f"pip is not available in this environment "
            f"({mechanism} install detected)."
        )
        print(
            "Upgrade jcodemunch-mcp through whatever installed it, restart your "
            "MCP client, then re-run `jcodemunch-mcp upgrade --no-pip` to refresh hooks."
        )


def run_upgrade(*, no_pip: bool = False, yes: bool = True) -> int:
    """Upgrade jcodemunch-mcp (when possible) then refresh hooks/config.

    Behavior by environment:

    - **pip available** (default venv / system pip): runs
      ``python -m pip install -U jcodemunch-mcp``; on a genuine pip failure
      (network, permissions) the non-zero code is preserved but hooks are
      still refreshed so a partial state can recover.
    - **pip absent** (pipx / uv tool / uvx): does NOT shell out to a foreign
      package manager. Prints the exact upgrade command for the detected
      mechanism, then refreshes hooks/config in-process and returns that
      refresh's exit code.

    Returns an exit code (0 on success).
    """
    package_upgrade_rc = 0

    if not no_pip:
        if _pip_available():
            pip_args = [sys.executable, "-m", "pip", "install", "-U", "jcodemunch-mcp"]
            print(f"$ {' '.join(pip_args)}")
            try:
                r = subprocess.run(pip_args, check=False)
            except OSError as e:
                print(f"  pip invocation failed: {e}", file=sys.stderr)
                package_upgrade_rc = 1
            else:
                if r.returncode != 0:
                    print(
                        "  pip exited non-zero; refreshing hooks anyway. "
                        "Fix the package install, then re-run with --no-pip.",
                        file=sys.stderr,
                    )
                    package_upgrade_rc = r.returncode
        else:
            mechanism, command = detect_install_mechanism()
            _print_external_upgrade_hint(mechanism, command)

    print("Refreshing hooks/config in-process (no pip required)...")
    refresh_rc = _refresh_hooks(yes=yes)

    # A genuine pip failure is a real error the caller should see; a
    # pip-absent environment is not a failure (the package upgrade is the
    # user's separate keystroke, which we've printed), so the hook-refresh
    # result governs the exit code there.
    return package_upgrade_rc or refresh_rc
