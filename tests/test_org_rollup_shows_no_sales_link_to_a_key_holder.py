"""`org-rollup` shows the pricing link only to an install with no license key.

`license` already follows the rule that a key holder may be a paying customer
and is never shown where to buy (`license_status_json`, `format_license_status`). `org-rollup` printed `gate["get_license"]`
on every refusal and every evaluation note, so a Builder key holder asking for
the team rollup was handed the pricing URL. The reason line still names the
tier the feature needs ("requires Studio or Platform"), which is a fact, not a
pitch.

`license.sales_link(gate)` is the one place that decides; the three renderings
read it.
"""
from __future__ import annotations

import json

import pytest

from jcodemunch_mcp import config as config_module
from jcodemunch_mcp import server
from jcodemunch_mcp.org import license as lic
from jcodemunch_mcp.org import store as org_store

URL = lic.GET_LICENSE_URL


def _gate(mode, key_status, *, tier=None, insufficient=False):
    allowed = mode != "blocked"
    if mode == "blocked":
        req = "a Studio or Platform license" if insufficient else "a license"
        base = "the builder tier does not include org-rollup (requires Studio or Platform)" if insufficient else "no license key set"
        reason = f"org-rollup requires {req} ({base}); evaluation period ended"
    else:
        reason = "tier upgrade needed (requires Studio or Platform); 3 day(s) left in trial"
    return {
        "allowed": allowed, "mode": mode, "reason": reason, "tier": tier,
        "grace_days_left": 0 if mode == "blocked" else 3, "get_license": URL,
        "key_masked": "JCM...CCCC" if key_status != "absent" else "",
        "key_valid": key_status == "valid", "key_status": key_status, "feature": "org-rollup",
    }


@pytest.fixture
def run(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path))
    monkeypatch.setattr(config_module, "load_config", lambda *a, **k: {})
    monkeypatch.setattr(org_store, "org_rollup", lambda org: {
        "totals": {"seat_count": 0, "tokens_saved": 0, "usd": 0.0, "calls": 0}, "seats": []})

    def _run(gate, *argv):
        monkeypatch.setattr(lic, "check_gate", lambda **k: gate)
        capsys.readouterr()
        server.main(["org-rollup", "--org", "acme", *argv])
        return capsys.readouterr()

    return _run


KEY_STATES = ["valid", "unverified_offline", "rejected"]


@pytest.mark.parametrize("key_status", KEY_STATES)
def test_a_key_holder_refused_gets_no_link(run, key_status):
    gate = _gate("blocked", key_status, tier="builder", insufficient=key_status == "valid")
    out = run(gate)
    assert URL not in out.err + out.out
    assert "org-rollup is unavailable" in out.err
    if key_status == "valid":
        assert "requires Studio or Platform" in out.err


@pytest.mark.parametrize("key_status", KEY_STATES)
def test_a_key_holder_refused_gets_no_link_in_json(run, key_status):
    out = run(_gate("blocked", key_status, tier="builder"), "--json")
    data = json.loads(out.out)
    assert data["get_license"] is None
    assert URL not in out.out + out.err


@pytest.mark.parametrize("key_status", KEY_STATES)
def test_a_key_holder_in_evaluation_gets_no_link(run, key_status):
    out = run(_gate("grace", key_status, tier="builder"))
    assert "note:" in out.err
    assert URL not in out.err + out.out


def test_no_key_refused_still_sees_the_link(run):
    out = run(_gate("blocked", "absent"))
    assert f"Get a license: {URL}" in out.err
    assert json.loads(run(_gate("blocked", "absent"), "--json").out)["get_license"] == URL


def test_no_key_in_evaluation_still_sees_the_link(run):
    out = run(_gate("grace", "absent"))
    assert URL in out.err


@pytest.mark.parametrize("key_status,expected", [("absent", URL), ("valid", None),
                                                 ("unverified_offline", None), ("rejected", None)])
def test_sales_link_is_the_one_rule(key_status, expected):
    assert lic.sales_link(_gate("blocked", key_status)) == expected
    assert lic.license_status_json(_gate("blocked", key_status))["get_license"] == expected
