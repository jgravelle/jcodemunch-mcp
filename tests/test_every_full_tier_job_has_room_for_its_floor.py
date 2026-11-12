"""Every CI job that runs the full tier leaves room for its legs' wall Floors.

The pr-gate's full job got `timeout-minutes: 32` when the windows wall Floor
went to 1700 s, and `test_ci_timeout_matches_threshold` binds that one literal.
`nightly.yml`'s full job kept 20, because nothing read it. Nightly runs
42754071420, 42957450811 and 43152986559 each had a windows full-tier leg
cancelled at the 20-minute mark; in the last, the leg had already logged every
test passing. A cancelled leg writes no summary, so it opens no drift issue and
the nightly failed in silence.

The property is per job, not per file. A job that runs `harness full` must
outlast the wall Floor of every OS it runs on, plus setup, and no job may
exceed `ci.test_job_timeout_minutes`, the cap that keeps a hang from holding a
runner. A job is found by what its steps RUN, so a new workflow that runs the
full tier is covered on arrival.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from tests.test_windows_wall_floor_admits_measured_legs import SETUP_SECONDS

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

# The wall Floor each runner OS is judged by on CI. An ubuntu leg is held to
# the C-tracer Floor, the larger of the two it can be judged by, so the bound
# is safe whichever Python version the leg runs.
OS_FLOORS = {
    "windows": "suite.full_seconds_ci_windows",
    "ubuntu": "suite.full_seconds_ci_ubuntu_ctrace",
}


def _floors() -> dict:
    data = json.loads(
        (ROOT / "harness" / "thresholds.json").read_text(encoding="utf-8")
    )
    rows = data["thresholds"] if isinstance(data, dict) else data
    return {r["id"]: r["floor"] for r in rows}


def _full_tier_jobs():
    for path in sorted(WORKFLOWS.glob("*.yml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for job_id, job in (data.get("jobs") or {}).items():
            steps = job.get("steps") or []
            if any("harness full" in str(s.get("run", "")) for s in steps):
                yield path.name, job_id, job


def _oses(job: dict) -> set[str]:
    named = [str(job.get("runs-on", ""))]
    named += [
        str(o)
        for o in ((job.get("strategy") or {}).get("matrix") or {}).get("os") or []
    ]
    return {os_ for os_ in OS_FLOORS if any(os_ in n for n in named)}


def test_the_scan_finds_every_known_full_tier_job():
    found = {(name, job_id) for name, job_id, _ in _full_tier_jobs()}
    # Non-vacuity: the three workflows that run the full tier today.
    assert {
        ("pr-gate.yml", "full"),
        ("nightly.yml", "full"),
        ("main.yml", "full"),
    } <= found, found


def test_every_full_tier_job_names_an_os_it_can_be_judged_on():
    for name, job_id, job in _full_tier_jobs():
        assert _oses(job), f"{name}:{job_id} runs on no OS this test knows a Floor for"


def test_every_full_tier_job_has_room_for_its_floors():
    floors = _floors()
    cap = floors["ci.test_job_timeout_minutes"]
    problems = []
    for name, job_id, job in _full_tier_jobs():
        minutes = job.get("timeout-minutes")
        if not isinstance(minutes, int):
            problems.append(f"{name}:{job_id} has no integer timeout-minutes")
            continue
        if minutes > cap:
            problems.append(
                f"{name}:{job_id} timeout {minutes} min exceeds the {cap} min cap"
            )
        for os_ in sorted(_oses(job)):
            need = floors[OS_FLOORS[os_]] + SETUP_SECONDS
            if minutes * 60 < need:
                problems.append(
                    f"{name}:{job_id} runs a {os_} leg with {minutes} min; its wall Floor "
                    f"plus setup needs {need} s, so a slow green leg is cancelled with no verdict"
                )
    assert not problems, problems
