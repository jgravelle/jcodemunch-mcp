"""Turn the triage model's JSON into exactly the actions POLICY section 2
permits, and nothing else (docs/inbound/DESIGN.md section 2).

purpose:  the model classifies; this script is the only thing that labels,
          comments, or files a draft, so what the model "decided" cannot
          exceed what the policy allows for the category and confidence
invokes:  `gh issue edit --add-label/--remove-label`, `gh issue comment`
          (duplicate link only) when run with --apply; nothing otherwise
produces: a plan (labels to add and remove, at most one comment, at most
          one draft file) and, with --apply, the actions; a draft file
          under the artifact dir with `approved: false`
refuses:  a result that fails the schema; any action for a `medium` or
          `low` confidence beyond the category label plus needs-human; any
          comment but the duplicate link; any action beyond the label for
          an item filed by the repository owner (POLICY section 10); a
          result whose `issue` is not the one the workflow named

The write target is `--issue`, the number the WORKFLOW is processing, never
the model's `issue` field (#670): a failed model is exactly the case with no
number in its output, and reading it from there discarded the escalation
while the job stayed green and the item was re-triaged every 15 minutes.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import subprocess
import sys
from pathlib import Path

CATEGORIES = {
    "security": ("inbound:security", None),
    "dependency": ("inbound:dependency", None),
    "duplicate": ("inbound:duplicate", None),
    "spam": ("inbound:spam", None),
    "question": ("inbound:question", "question"),
    "feature": ("inbound:feature", "enhancement"),
    # No human `bug` label before a reproduction: the fix job applies it
    # when a failing test exists (POLICY section 1 attaches it to
    # bug-reproducible; a human label implies a verdict).
    "bug-candidate": ("inbound:bug-candidate", None),
    "unknown": ("inbound:unknown", None),
}
CONFIDENCES = ("high", "medium", "low")
DRAFTABLE = ("question", "feature")  # bug-unreproducible drafts come from the fix job
QUEUE_LABEL = "inbound:queued"


class SchemaError(ValueError):
    pass


def validate(result: dict) -> dict:
    for k in ("issue", "category", "confidence", "evidence"):
        if k not in result:
            raise SchemaError(f"missing field {k}")
    if result["category"] not in CATEGORIES:
        raise SchemaError(f"unknown category {result['category']!r}")
    if result["confidence"] not in CONFIDENCES:
        raise SchemaError(f"unknown confidence {result['confidence']!r}")
    if not isinstance(result["evidence"], list) or len(result["evidence"]) > 3:
        raise SchemaError("evidence must be a list of at most three quotes")
    if result["category"] == "duplicate" and not result.get("duplicate_of"):
        raise SchemaError("duplicate without duplicate_of")
    return result


def plan(result: dict, author: str, owner: str) -> dict:
    """Pure. Returns {add, remove, comment, draft}."""
    r = validate(result)
    cat, conf = r["category"], r["confidence"]
    add = [CATEGORIES[cat][0]]
    remove = [QUEUE_LABEL]
    comment = None
    draft = None
    human_label = CATEGORIES[cat][1]

    # Security: the label, needs-human, nothing else, whatever the confidence
    # (POLICY 5.1: security overrides confidence downward only).
    if cat == "security":
        return {
            "add": add + ["needs-human"],
            "remove": remove,
            "comment": None,
            "draft": None,
        }

    if conf == "low":
        return {
            "add": ["inbound:unknown", "needs-human"],
            "remove": remove,
            "comment": None,
            "draft": None,
        }
    if conf == "medium":
        return {
            "add": add + ["needs-human"],
            "remove": remove,
            "comment": None,
            "draft": None,
        }

    # high
    if human_label:
        add.append(human_label)
    if cat == "unknown":
        add.append("needs-human")
    if author.lower() == owner.lower():
        # The maintainer's own records: labels only (POLICY section 10).
        return {"add": add, "remove": remove, "comment": None, "draft": None}
    if cat == "duplicate":
        dup = r["duplicate_of"]
        quotes = r["evidence"][:2]
        body = (
            f"Possible duplicate of #{dup}. The two sentences that match:\n\n"
            + "\n".join(f"> {q}" for q in quotes)
            + "\n\n_Posted by the inbound triage job; a wrong link costs one reply. "
            "Neither issue is closed by this comment._"
        )
        comment = {"issue_to": r["issue"], "body": body}
    elif cat in DRAFTABLE and r.get("draft"):
        draft = {
            "issue": r["issue"],
            "category": cat,
            "body": r["draft"],
        }
    return {"add": add, "remove": remove, "comment": comment, "draft": draft}


def write_draft(draft: dict, out_dir: Path, run_id: str) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / f"{draft['issue']}-{run_id}.md"
    original = draft["body"].rstrip("\n")
    p.write_text(
        "---\n"
        f"issue: {draft['issue']}\n"
        f"category: {draft['category']}\n"
        f"run_id: {run_id}\n"
        f"created: {_dt.datetime.now(_dt.timezone.utc).isoformat(timespec='seconds')}\n"
        "approved: false\n"
        "edited: false\n"
        "---\n"
        f"{original}\n"
        "\n<!-- original -->\n"
        f"{original}\n"
        "<!-- /original -->\n",
        encoding="utf-8",
        newline="\n",
    )
    return p


def _gh(args: list[str], repo: str) -> None:
    subprocess.run(["gh", *args, "-R", repo], check=True, timeout=60)


def apply(p: dict, repo: str, issue: int) -> None:
    if p["add"]:
        _gh(
            [
                "issue",
                "edit",
                str(issue),
                *sum((["--add-label", x] for x in p["add"]), []),
            ],
            repo,
        )
    if p["remove"]:
        _gh(
            [
                "issue",
                "edit",
                str(issue),
                *sum((["--remove-label", x] for x in p["remove"]), []),
            ],
            repo,
        )
    if p["comment"]:
        _gh(
            [
                "issue",
                "comment",
                str(p["comment"]["issue_to"]),
                "--body",
                p["comment"]["body"],
            ],
            repo,
        )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("result", type=Path, help="the model's JSON")
    ap.add_argument("--author", required=True)
    ap.add_argument("--owner", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--drafts-dir", type=Path, required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument(
        "--issue",
        type=int,
        required=True,
        help="the item the workflow is processing; the only write target",
    )
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)
    try:
        result = json.loads(args.result.read_text(encoding="utf-8"))
        if not isinstance(result, dict):
            raise SchemaError("result is not an object")
        if "issue" in result and str(result["issue"]) != str(args.issue):
            raise SchemaError(
                f"result names issue {str(result['issue'])[:40]!r}, "
                f"the workflow processed {args.issue}"
            )
        p = plan(result, args.author, args.owner)
    except (OSError, json.JSONDecodeError, SchemaError) as e:
        # A malformed result is an escalation, not a guess. ⚠ #670: the
        # escalation is OUR fixed response to the model failing, carries
        # nothing the model said, and is WRITTEN -- to the issue the workflow
        # named. Planning it and not applying it left the item queued forever.
        p = {
            "add": ["inbound:unknown", "needs-human"],
            "remove": [QUEUE_LABEL],
            "comment": None,
            "draft": None,
            "error": f"{type(e).__name__}: {e}",
        }
    if p.get("draft"):
        p["draft_path"] = str(write_draft(p["draft"], args.drafts_dir, args.run_id))
    print(json.dumps(p, sort_keys=True))
    if args.apply:
        apply(p, args.repo, args.issue)
    return 0


if __name__ == "__main__":
    sys.exit(main())
