"""Tests for the share_savings opt-in / opt-out lever (P1.1).

Covers:
- ``set_bool_key`` regex mutator across all three input shapes
  (commented template form, existing active form, absent key).
- ``apply_share_savings`` end-to-end against a fresh storage dir.
- The critical PRD invariant: ``upgrade_config`` preserves the user-set
  ``share_savings`` value across package upgrades. Without this guarantee
  the install-time flag would be defeated by the next package upgrade,
  which is exactly the failure mode the flag is meant to prevent.
"""

import tempfile
from pathlib import Path

import pytest

from jcodemunch_mcp.config import (
    apply_share_savings,
    generate_template,
    set_bool_key,
    upgrade_config,
)


class TestSetBoolKey:
    """Three input shapes the regex must handle."""

    def test_commented_template_form_becomes_active(self):
        template = generate_template()
        # Sanity: template ships the key commented out.
        assert '// "share_savings": true,' in template

        result = set_bool_key(template, "share_savings", False)

        assert '"share_savings": false,' in result
        assert '// "share_savings": true,' not in result

    def test_existing_active_value_gets_replaced(self):
        seed = '{\n  "share_savings": false,\n}'
        result = set_bool_key(seed, "share_savings", True)

        assert '"share_savings": true,' in result
        # Old value gone (allowing the new line to still mention share_savings).
        assert result.count('"share_savings":') == 1

    def test_absent_key_gets_appended_before_closing_brace(self):
        seed = '{\n  "other_key": 5\n}'
        result = set_bool_key(seed, "share_savings", False)

        assert '"share_savings": false,' in result

    def test_idempotent_when_value_unchanged(self):
        seed = '{\n  "share_savings": false,\n}'
        once = set_bool_key(seed, "share_savings", False)
        twice = set_bool_key(once, "share_savings", False)

        assert once == twice
        assert twice.count('"share_savings":') == 1


class TestApplyShareSavings:
    """End-to-end against a temporary storage dir."""

    def test_creates_config_if_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = Path(tmp)
            path = apply_share_savings(False, storage)

            assert path.exists()
            assert path == storage / "config.jsonc"
            assert '"share_savings": false,' in path.read_text(encoding="utf-8")

    def test_flips_value_on_re_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = Path(tmp)
            apply_share_savings(False, storage)
            path = apply_share_savings(True, storage)

            content = path.read_text(encoding="utf-8")
            assert '"share_savings": true,' in content
            assert content.count('"share_savings":') == 1


class TestUpgradePreservesShareSavings:
    """PRD invariant: config --upgrade must not silently re-enable a user opt-out.

    This is the property that makes the install-time flag durable. Without it,
    the next ``jcodemunch-mcp config --upgrade`` (run automatically or manually
    after a package upgrade) could reset share_savings back to the template
    default and the user's explicit choice would silently disappear.
    """

    def test_user_set_false_survives_upgrade(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = Path(tmp)
            path = apply_share_savings(False, storage)

            upgrade_config(path)

            content = path.read_text(encoding="utf-8")
            assert '"share_savings": false,' in content
            assert content.count('"share_savings":') == 1

    def test_user_set_true_survives_upgrade(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = Path(tmp)
            path = apply_share_savings(True, storage)

            upgrade_config(path)

            content = path.read_text(encoding="utf-8")
            assert '"share_savings": true,' in content
            # Either the active true line replaced the commented one, or both
            # coexist with the active one winning; in either case the user-set
            # value must be present and there must not be an active false.
            assert '"share_savings": false' not in content


class TestTelemetrySelfCorrecting:
    """The community-meter wire now sends the absolute lifetime `total` (not
    only a `delta`) so the server can GREATEST-converge: a dropped/failed POST
    is recovered by the next one instead of permanently undercounting."""

    def _capture(self, monkeypatch):
        from jcodemunch_mcp.storage import token_tracker as tt
        sent: list = []
        monkeypatch.setattr(
            tt, "_share_savings",
            lambda delta, total, anon_id: sent.append((delta, total, anon_id)),
        )
        # Force the opt-in on regardless of any global config state.
        monkeypatch.setattr(
            tt._config, "get",
            lambda key, default=None, repo=None: True if key == "share_savings" else default,
        )
        return tt, sent

    def test_flush_sends_absolute_total_not_just_delta(self, monkeypatch, tmp_path):
        tt, sent = self._capture(monkeypatch)
        st = tt._State()
        st.add(1000, str(tmp_path))
        st.flush()
        assert sent, "telemetry should enqueue on flush when share_savings is on"
        delta, total, anon = sent[-1]
        assert total == 1000           # absolute lifetime total
        assert delta == 1000           # new savings since the last send
        assert isinstance(anon, str) and anon

    def test_total_carries_full_lifetime_so_a_dropped_send_self_corrects(self, monkeypatch, tmp_path):
        tt, sent = self._capture(monkeypatch)
        st = tt._State()
        st.add(1000, str(tmp_path))
        st.flush()
        # Even if that first POST were dropped on the wire, the NEXT flush still
        # carries the full absolute total (1700), so the server's GREATEST
        # recovers the gap rather than missing the lost 1000.
        st.add(700, str(tmp_path))
        st.flush()
        assert sent[-1][1] == 1700     # absolute total includes the prior amount
        assert sent[-1][0] == 700      # delta is only the new savings
