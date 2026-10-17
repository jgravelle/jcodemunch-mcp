"""L-198: LSP enrichment starts rust-analyzer with build scripts and proc macros off.

With `enrichment.lsp_enabled` on, `enrichment/lsp_bridge.py` started each
language server with no `initializationOptions`. rust-analyzer's defaults run
the indexed project's build scripts (`build.rs`) and procedural macros, which is
code from the repository, and `cargo check` on save does the same. That made
the one jcodemunch switch that could run repository code. The bridge now sends
the options that turn those off. What enrichment loses is unmeasured:
macro-generated items and references inside code a build script enables or
generates, which rust-analyzer then treats as inactive.
"""

from __future__ import annotations

from jcodemunch_mcp.enrichment.lsp_bridge import LSPServer


def _initialize_params(command, tmp_path, monkeypatch):
    sent = {}

    def _send_request(self, method, params):
        sent[method] = params
        return {"capabilities": {}}

    monkeypatch.setattr(LSPServer, "_send_request", _send_request)
    monkeypatch.setattr(LSPServer, "_send_notification", lambda self, method, params: None)
    server = LSPServer("rust", command, str(tmp_path), 5)
    assert server._initialize() is True
    return sent["initialize"]


def test_rust_analyzer_is_told_not_to_run_build_scripts_or_proc_macros(tmp_path, monkeypatch):
    params = _initialize_params(["rust-analyzer"], tmp_path, monkeypatch)
    opts = params.get("initializationOptions") or {}

    assert opts.get("cargo", {}).get("buildScripts", {}).get("enable") is False
    assert opts.get("procMacro", {}).get("enable") is False
    assert opts.get("checkOnSave") is False


def test_a_full_path_to_rust_analyzer_gets_the_same_options(tmp_path, monkeypatch):
    params = _initialize_params([str(tmp_path / "bin" / "rust-analyzer.exe")], tmp_path, monkeypatch)
    assert (params.get("initializationOptions") or {}).get("procMacro", {}).get("enable") is False


def test_other_servers_are_sent_no_options(tmp_path, monkeypatch):
    params = _initialize_params(["pyright-langserver", "--stdio"], tmp_path, monkeypatch)
    assert "initializationOptions" not in params
