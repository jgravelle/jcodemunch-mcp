"""Every CI job that runs the full tier leaves room for its leg's wall Floor.

The pr-gate's full job got `timeout-minutes: 32` when the windows wall Floor
went to 1700 s, and `test_ci_timeout_matches_threshold` binds that one literal.
`nightly.yml`'s full job kept 20, because nothing read it: three nightlies in a
row (2026-11-10, 11-11, 11-12) had a windows leg cancelled at 20 minutes,
the last one after `20794 passed` in 1182 s. A cancelled leg writes no summary,
so it opens no drift issue and the nightly failed in silence.

The property is per job, not per file: a job that runs `harness full` on a
windows runner must outlast the windows wall Floor plus setup, and no job may
exceed `ci.test_job_timeout_minutes`, the cap that keeps a hang from holding a
runner. A job is found by what its steps RUN, so a new workflow that runs the
full tier is covered on arrival.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
SETUP_SECONDS = (
    180  # checkout, uv install and sync (F-38); same figure as the windows Floor test
)


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


def _runs_on_windows(job: dict) -> bool:
    runs_on = str(job.get("runs-on", ""))
    oses = ((job.get("strategy") or {}).get("matrix") or {}).get("os") or []
    return "windows" in runs_on or any("windows" in str(o) for o in oses)


def test_the_scan_finds_every_known_full_tier_job():
    found = {(name, job_id) for name, job_id, _ in _full_tier_jobs()}
    # Non-vacuity: the three workflows that run the full tier today.
    assert {
        ("pr-gate.yml", "full"),
        ("nightly.yml", "full"),
        ("main.yml", "full"),
    } <= found, found


def test_every_full_tier_job_has_room_for_its_floor():
    floors = _floors()
    cap = floors["ci.test_job_timeout_minutes"]
    windows_need = floors["suite.full_seconds_ci_windows"] + SETUP_SECONDS
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
        if _runs_on_windows(job) and minutes * 60 < windows_need:
            problems.append(
                f"{name}:{job_id} runs a windows leg with {minutes} min; the windows wall Floor "
                f"plus setup needs {windows_need} s, so a slow green leg is cancelled with no verdict"
            )
    assert not problems, problems
