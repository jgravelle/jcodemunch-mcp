"""Every `DEFAULTS` key is documented, or is named here as internal.

`tests/test_docs_config_parity.py` checks the one direction (a documented key
exists). This is the other: a key that exists is documented in
CONFIGURATION.md or CLI-AND-ENV.md, by name or by its env-var spelling, or it
is in INTERNAL_KEYS with a reason. The 16 names below were undocumented
everywhere on 2026-09-03 (docs/standard/DISCOVERY.md section 7); listing
them is a FINDING (docs/harness/FINDINGS.md F-03), not an endorsement.
Removing a name from this list is the direction the list may move.
"""

from __future__ import annotations

import re

from harness import thresholds as T

REPO = T.REPO_ROOT
# CLAUDE.md is included because the 2026-08-31 split left the INVARIANT rows
# (13 env vars) there and moved the rest to CLI-AND-ENV.md; both are documentation.
DOCS = [REPO / "CONFIGURATION.md", REPO / "CLI-AND-ENV.md", REPO / "README.md", REPO / "CLAUDE.md"]

INTERNAL_KEYS = {
    "server_output": "F-03",
    "server_output_threshold": "F-03",
    "worktree_base_path": "F-03",
    "git_root_identity": "F-03",
    "summarizer_max_failures": "F-03",
    "cache_mode": "F-03",
    "summarize_from_docstrings": "F-03",
    "render_diagram_viewer_enabled": "F-03",
    "mermaid_viewer_path": "F-03",
    # env var is JCODEMUNCH_RUNTIME_REDACT (documented), key name differs from the derived spelling
    "runtime_redact_enabled": "F-03: documented only under its env-var name JCODEMUNCH_RUNTIME_REDACT",
}


def _doc_text() -> str:
    return "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in DOCS if p.exists())


def test_every_default_key_is_documented_or_declared_internal():
    from jcodemunch_mcp.config import DEFAULTS
    text = _doc_text()
    missing = []
    for key in DEFAULTS:
        env = "JCODEMUNCH_" + key.upper()
        if re.search(rf"\b{re.escape(key)}\b|\b{re.escape(env)}\b", text):
            continue
        if key in INTERNAL_KEYS:
            continue
        missing.append(key)
    assert not missing, (
        f"config keys documented nowhere and not declared internal: {missing}. "
        "Add a row to CONFIGURATION.md or CLI-AND-ENV.md."
    )


def test_internal_keys_list_does_not_go_stale():
    """The list may only shrink: a key that became documented must leave it,
    and a key that no longer exists must leave it."""
    from jcodemunch_mcp.config import DEFAULTS
    text = _doc_text()
    stale = []
    for key in INTERNAL_KEYS:
        if key not in DEFAULTS:
            stale.append(f"{key}: no longer in DEFAULTS")
        elif re.search(rf"\b{re.escape(key)}\b|\bJCODEMUNCH_{re.escape(key.upper())}\b", text):
            stale.append(f"{key}: now documented; remove it from INTERNAL_KEYS")
    assert not stale, "\n".join(stale)
