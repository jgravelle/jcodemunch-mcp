"""Each platform's full tier is judged by its own N7 skip ceiling.

Until 2026-11-14 `_skips_floor_id` returned the ubuntu ceiling for every
platform that was not Windows, so the first macOS full tier was judged by
`ci.skips_ubuntu` and failed N7 with every test passing: macOS skips the
Windows-only tests and three optional imports that Linux CI installs. macOS
now has `ci.skips_macos`, set from a measured run.
"""

from __future__ import annotations

import pytest

import harness.__main__ as H
from harness import thresholds as T


@pytest.mark.parametrize(
    "platform,want",
    [
        ("win32", "ci.skips_windows"),
        ("linux", "ci.skips_ubuntu"),
        ("darwin", "ci.skips_macos"),
    ],
)
def test_each_platform_gets_its_skip_ceiling(monkeypatch, platform, want):
    monkeypatch.setattr(H.sys, "platform", platform)
    assert H._skips_floor_id() == want


@pytest.mark.parametrize("platform", ["win32", "linux", "darwin"])
def test_the_chosen_ceiling_is_a_threshold_the_runner_can_read(monkeypatch, platform):
    monkeypatch.setattr(H.sys, "platform", platform)
    tid = H._skips_floor_id()
    assert T.get(tid)["criterion"] == "N7"
    assert tid in H.DELEGATED, f"{tid} has no DELEGATED entry naming who reads it"


def test_the_macos_ceiling_clears_the_run_it_was_set_from():
    e = T.get("ci.skips_macos")
    observed = int(e["set_at"]["reason"].split("observed ")[1].split()[0])
    assert T.passes("ci.skips_macos", observed)
    assert not T.passes("ci.skips_macos", T.floor("ci.skips_macos") + 1)
