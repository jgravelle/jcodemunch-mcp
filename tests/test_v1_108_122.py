"""Licensing hardening for #364 and its follow-ups.

Confidence sweep after two field reports from a paying client:
  1. #364: `license` ignored a config-file `license_key`.
  2. follow-up: a valid Builder license rendered as "evaluation (unlicensed)".

This file locks in the remaining licensing correctness so there is no strike 3:
  - `check_gate().key_status` classifies a key honestly (valid / absent /
    rejected / unverified_offline) — a network failure never reads as a bad key.
  - `format_license_status()` (the `license` CLI renderer, pure) never tells a
    holder of a valid license they are unlicensed, and never shows a scary trial
    countdown to a licensed non-team tier.
  - `resolve_effective_license_key()` makes premium-pack downloads honor the
    env / config `license_key` (a Builder tier's actual entitlement), not only
    an explicit `--license`.
"""

from __future__ import annotations

import time

import pytest

from jcodemunch_mcp.org import license as lic
from jcodemunch_mcp.cli import install_pack as ip


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    # Isolate all state under a temp CODE_INDEX_PATH and reset the process-global
    # config so no real ~/.code-index/config.jsonc bleeds a license_key in. We do
    # NOT stub _license_key here (unlike test_org_license) because the install-pack
    # fallback test genuinely needs real env→config resolution.
    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path))
    monkeypatch.delenv("JCODEMUNCH_LICENSE_KEY", raising=False)
    from jcodemunch_mcp import config as _cfg
    monkeypatch.setattr(_cfg, "_GLOBAL_CONFIG", {}, raising=False)
    yield


def _server(monkeypatch, answer):
    monkeypatch.setattr(lic, "_check_server", lambda key: answer)


# --------------------------------------------------------------------------- #
# key_status — the honest classifier
# --------------------------------------------------------------------------- #

def test_key_status_valid(monkeypatch):
    monkeypatch.setenv("JCODEMUNCH_LICENSE_KEY", "STUDIOKEY001")
    _server(monkeypatch, {"valid": True, "tier": "studio", "error": None})
    assert lic.check_gate()["key_status"] == "valid"


def test_key_status_absent(monkeypatch):
    _server(monkeypatch, None)  # not consulted; no key
    assert lic.check_gate()["key_status"] == "absent"


def test_key_status_rejected(monkeypatch):
    monkeypatch.setenv("JCODEMUNCH_LICENSE_KEY", "BOGUSKEY0001")
    _server(monkeypatch, {"valid": False, "tier": None, "error": "License key not found"})
    assert lic.check_gate()["key_status"] == "rejected"


def test_key_status_unverified_offline_never_reads_as_rejected(monkeypatch):
    # A real key that has never been cached, on a server outage, must NOT be
    # classified as rejected — that's the alarming false negative for a paying
    # customer behind a proxy/firewall on first run.
    monkeypatch.setenv("JCODEMUNCH_LICENSE_KEY", "REALKEY00001")
    _server(monkeypatch, None)  # unreachable, no prior cache
    g = lic.check_gate()
    assert g["key_status"] == "unverified_offline"
    assert g["key_valid"] is False  # not yet confirmed
    assert g["allowed"] is True     # grace still lets them work


def test_key_status_sticky_valid_survives_outage(monkeypatch):
    key = "STICKY000001"
    monkeypatch.setenv("JCODEMUNCH_LICENSE_KEY", key)
    _server(monkeypatch, {"valid": True, "tier": "studio", "error": None})
    assert lic.check_gate()["key_status"] == "valid"
    # Force a recheck window, then outage: a confirmed key stays valid.
    state = lic._load_state()
    state["checked_at"] = time.time() - (lic.RECHECK_SECONDS + 10)
    lic._save_state(state)
    _server(monkeypatch, None)
    assert lic.check_gate()["key_status"] == "valid"


# --------------------------------------------------------------------------- #
# format_license_status — the CLI renderer (pure, network-free)
# --------------------------------------------------------------------------- #

def _text(gate, **kw):
    return "\n".join(lic.format_license_status(gate, **kw))


def test_render_valid_builder_reads_as_licensed_not_unlicensed():
    gate = {"mode": "grace", "key_status": "valid", "tier": "builder",
            "key_masked": "JCM…CCCC", "get_license": "https://x/#pricing",
            "grace_days_left": 14, "key_valid": True}
    out = _text(gate)
    assert out.startswith("License: licensed (builder)")
    assert "unlicensed" not in out.lower()
    assert "trial" not in out.lower()
    assert "evaluation" not in out.lower()          # no scary countdown
    # A neutral fact, no pitch (jjg, 2026-10-20: paid clients are never marketed to).
    assert "Team features (org-rollup): not included in your license" in out
    assert "https://" not in out and "Upgrade" not in out and "Studio" not in out
    assert "free and need no license" in out


def test_render_valid_studio_includes_org_rollup():
    gate = {"mode": "licensed", "key_status": "valid", "tier": "studio",
            "key_masked": "JCM…CCCC", "get_license": None, "key_valid": True}
    out = _text(gate)
    assert out.startswith("License: licensed (studio)")
    assert "org-rollup: included in your tier" in out
    assert "unlicensed" not in out.lower()


def test_render_offline_is_not_rejection():
    gate = {"mode": "grace", "key_status": "unverified_offline", "tier": None,
            "key_masked": "JCM…CCCC", "get_license": "https://x/#pricing",
            "grace_days_left": 14, "key_valid": False}
    out = _text(gate)
    assert "not yet verified" in out
    assert "not recognized" not in out
    assert "verify automatically" in out


def test_render_no_key_and_rejected_are_honest():
    absent = _text({"mode": "grace", "key_status": "absent", "tier": None,
                    "key_masked": "", "get_license": "https://x/#pricing",
                    "grace_days_left": 14, "key_valid": False})
    assert absent.startswith("License: no license key set")

    rejected = _text({"mode": "grace", "key_status": "rejected", "tier": None,
                      "key_masked": "BAD…9999", "get_license": "https://x/#pricing",
                      "grace_days_left": 14, "key_valid": False})
    assert "not recognized by the license server" in rejected


@pytest.mark.parametrize("status", ["valid", "unverified_offline", "rejected"])
def test_any_key_holder_is_shown_no_pricing_link(status):
    """jjg, DECISIONS 2026-10-20: paid clients are never marketed to. Any key may
    be a paying customer's, so only a keyless install sees where to buy."""
    out = _text({"mode": "grace", "key_status": status, "tier": "builder" if status == "valid" else None,
                 "key_masked": "JCM…CCCC", "get_license": "https://x/#pricing",
                 "grace_days_left": 14, "key_valid": status == "valid"})
    assert "https://x/#pricing" not in out and "Get a license" not in out, out


@pytest.mark.parametrize("status", ["valid", "unverified_offline", "rejected"])
def test_license_json_gives_a_key_holder_no_pricing_link(status):
    """`license --json` is the same command: the text and JSON renderings agree."""
    gate = {"mode": "grace", "key_status": status, "get_license": "https://x/#pricing",
            "reason": "r", "tier": None, "grace_days_left": 14}
    out = lic.license_status_json(gate)
    assert out["get_license"] is None
    assert {k: v for k, v in out.items() if k != "get_license"} == {k: v for k, v in gate.items() if k != "get_license"}
    assert gate["get_license"] == "https://x/#pricing"  # the gate itself is not mutated


def test_license_json_keeps_the_link_for_a_keyless_install():
    gate = {"mode": "grace", "key_status": "absent", "get_license": "https://x/#pricing"}
    assert lic.license_status_json(gate)["get_license"] == "https://x/#pricing"


def test_a_keyless_install_is_shown_where_to_get_a_license():
    out = _text({"mode": "grace", "key_status": "absent", "tier": None, "key_masked": "",
                 "get_license": "https://x/#pricing", "grace_days_left": 14, "key_valid": False})
    assert "Get a license: https://x/#pricing" in out


# --------------------------------------------------------------------------- #
# install-pack honors env / config license key (Builder's real entitlement)
# --------------------------------------------------------------------------- #

def test_pack_license_explicit_wins():
    assert ip.resolve_effective_license_key("EXPLICIT-KEY") == "EXPLICIT-KEY"


def test_pack_license_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("JCODEMUNCH_LICENSE_KEY", "ENVKEY-000001")
    assert ip.resolve_effective_license_key(None) == "ENVKEY-000001"


def test_pack_license_falls_back_to_config(monkeypatch, tmp_path):
    # No explicit, no env: the config `license_key` must be resolved so a
    # customer who persisted it in config.jsonc can download premium packs.
    monkeypatch.delenv("JCODEMUNCH_LICENSE_KEY", raising=False)
    cfg = tmp_path / "config.jsonc"
    cfg.write_text('{ "license_key": "CONFIG-KEY-42" }', encoding="utf-8")
    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path))
    assert ip.resolve_effective_license_key(None) == "CONFIG-KEY-42"


def test_pack_license_none_when_unset(monkeypatch, tmp_path):
    monkeypatch.delenv("JCODEMUNCH_LICENSE_KEY", raising=False)
    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path))  # empty store, no config
    assert ip.resolve_effective_license_key(None) is None
