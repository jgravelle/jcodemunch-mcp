"""Audit records for headless inbound jobs (docs/inbound/POLICY.md section 6).

purpose:  one JSON record per run, written even on failure or skip, rolled
          daily into `ledger/<YYYY-MM>.jsonl` on the `inbound-ledger` branch
invokes:  nothing outside the standard library
produces: `make_record` (a dict with every POLICY 6.1 field present),
          `write_record` (one file per run), `roll` (artifact dir -> jsonl,
          deduplicated on run_id)
refuses:  a record missing a required field; a record for a security item
          that carries an excerpt (POLICY 2: number only)
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from pathlib import Path

REQUIRED = (
    "job",
    "job_version",
    "prompt_file",
    "prompt_version",
    "prompt_sha256",
    "model",
    "claude_code_version",
    "action_sha",
    "event",
    "item",
    "kill_switch_state",
    "budget_state_at_start",
    "classification",
    "decision",
    "actions_taken",
    "cost_usd",
    "turns",
    "duration_s",
    "outcome",
    "error",
    "run_id",
    "recorded_at",
)
OUTCOMES = ("acted", "drafted", "escalated", "skipped", "failed")
# `write` passes every --field value through json.loads, so a shell writer's
# bare `kill_switch_state=true` arrived as a boolean and `item=685` as an int,
# beside the string an inline-Python writer's json.dumps produced. The digest
# compared the two with `!=` and reported flips that never happened (#690).
TEXT_FIELDS = ("item", "kill_switch_state")


def as_text(value):
    """One spelling per value: a boolean is ``"true"``/``"false"``, a number
    its decimal string; ``None`` and strings pass through unchanged, so a
    mis-set ``"True"`` is recorded as it was read."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return value


def make_record(**fields) -> dict:
    """Every POLICY 6.1 field is present; absent values are ``None`` so a
    reader can tell "not recorded" from "recorded empty"."""
    rec = {k: None for k in REQUIRED}
    rec.update(
        {
            "job_version": fields.pop("job_version", 1),
            "event": fields.pop("event", os.environ.get("GITHUB_EVENT_NAME")),
            "run_id": fields.pop("run_id", os.environ.get("GITHUB_RUN_ID")),
            "action_sha": fields.pop(
                "action_sha", os.environ.get("INBOUND_ACTION_SHA")
            ),
            "recorded_at": fields.pop(
                "recorded_at",
                _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
            ),
            "actions_taken": fields.pop("actions_taken", []),
            "classification": fields.pop("classification", {}),
        }
    )
    rec.update(fields)
    for k in TEXT_FIELDS:
        rec[k] = as_text(rec[k])
    unknown = set(rec) - set(REQUIRED)
    if unknown:
        raise ValueError(f"unknown record fields: {sorted(unknown)}")
    if rec["outcome"] not in OUTCOMES:
        raise ValueError(f"outcome must be one of {OUTCOMES}, got {rec['outcome']!r}")
    cls = rec["classification"] or {}
    if cls.get("category") == "security" and not str(rec.get("item") or "").isdigit():
        raise ValueError(
            "a security record names the item by number only (POLICY section 6.1)"
        )
    if cls.get("category") == "security" and (
        cls.get("evidence")
        or rec.get("decision", "")
        and len(str(rec["decision"])) > 200
    ):
        raise ValueError(
            "a security record carries the item number only (POLICY section 2)"
        )
    return rec


def write_record(path: Path, record: dict) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")
    return path


def roll(artifacts_dir: Path, ledger_dir: Path) -> int:
    """Append every `*.json` record under ``artifacts_dir`` to the month file
    it belongs to, skipping run_ids already present. Returns the count added."""
    ledger_dir = Path(ledger_dir)
    ledger_dir.mkdir(parents=True, exist_ok=True)
    seen: dict[str, set] = {}
    added = 0
    for src in sorted(Path(artifacts_dir).rglob("*.json")):
        try:
            rec = json.loads(src.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        missing = [k for k in REQUIRED if k not in rec]
        if missing:
            continue
        month = (rec.get("recorded_at") or "")[:7] or "unknown"
        target = ledger_dir / f"{month}.jsonl"
        if month not in seen:
            seen[month] = set()
            if target.exists():
                for line in target.read_text(encoding="utf-8").splitlines():
                    try:
                        seen[month].add(json.loads(line).get("run_id"))
                    except json.JSONDecodeError:
                        # a malformed ledger line holds no run_id to dedupe on;
                        # the digest counts and discloses it (digest.py)
                        continue
        if rec.get("run_id") in seen[month]:
            continue
        with target.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
        seen[month].add(rec.get("run_id"))
        added += 1
    return added


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("write", help="write one record from --field key=value pairs")
    w.add_argument("path", type=Path)
    w.add_argument("--field", action="append", default=[], metavar="KEY=VALUE")
    r = sub.add_parser("roll", help="append artifact records into the ledger")
    r.add_argument("artifacts_dir", type=Path)
    r.add_argument("ledger_dir", type=Path)
    args = ap.parse_args(argv)
    if args.cmd == "write":
        fields = {}
        for kv in args.field:
            k, _, v = kv.partition("=")
            try:
                fields[k] = json.loads(v)
            except json.JSONDecodeError:
                fields[k] = v
        write_record(args.path, make_record(**fields))
        return 0
    n = roll(args.artifacts_dir, args.ledger_dir)
    print(json.dumps({"added": n}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
