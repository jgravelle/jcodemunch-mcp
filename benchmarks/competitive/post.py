"""Post the drafts a human approved (docs/competitive/DESIGN.md s7.3;
`competitive-post.yml`; the brief's Phase 3 item 6).

purpose:  turn a draft file on the ledger branch that a human marked
          `approved: true` into one issue with exactly its label plus
          `needs-human`, and write the issue number back into the draft so
          it is never posted twice
invokes:  the ledger checkout's `competitive/drafts/*.md` (files), and
          `gh issue create` ONLY under `--apply` with the App token the
          workflow hands it; without `--apply` it prints what it would do
produces: one issue per approved, unposted draft; the draft rewritten with
          `posted: #<n>`; a JSON summary for the audit record
refuses:  a draft whose `approved:` line is not literally `true`; a draft
          already carrying `posted:`; a draft whose label is not one of the
          four s7.1/s8 labels; a draft with no `competitive-id` line (the
          fingerprint is what de-duplication reads back); to run at all
          when either switch is not `true` (the workflow reads both before
          this runs; `--require-switches` re-reads them here)
pinned:   the four labels; the `needs-human` label; the draft shape
          written by findings.py and feed.py (title/labels/competitive-id/
          approved head lines, then dated blocks)
fairness: a human's `approved: true` is the only path to a post; the App
          never approves; the issue body is the draft verbatim, so the
          fingerprint and the inbound preamble travel with it
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

LABELS = ("competitive-gap", "competitive-watch", "competitive-idea", "standard-proposal")


def parse(text: str) -> dict:
    head, body = {}, []
    lines = text.replace("\r\n", "\n").split("\n")
    i = 0
    for i, line in enumerate(lines):
        if not line.strip():
            break
        k, sep, v = line.partition(":")
        if not sep:
            break
        head[k.strip()] = v.strip()
    body = "\n".join(lines[i:]).strip("\n")
    return {"head": head, "body": body}


def eligible(d: dict) -> tuple[bool, str]:
    h = d["head"]
    if h.get("approved") != "true":
        return False, "not approved (the line must read exactly `approved: true`)"
    if h.get("posted"):
        return False, f"already posted as {h['posted']}"
    if not h.get("competitive-id"):
        return False, "no competitive-id line"
    labels = [x.strip() for x in h.get("labels", "").split(",") if x.strip()]
    own = [x for x in labels if x in LABELS]
    if len(own) != 1:
        return False, f"labels must carry exactly one of {LABELS}: {labels}"
    if not h.get("title"):
        return False, "no title"
    return True, own[0]


def create_issue(repo: str, title: str, body: str, labels: list[str]) -> int:
    proc = subprocess.run(["gh", "issue", "create", "-R", repo, "--title", title, "--body", body, "--label", ",".join(labels)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, check=True)
    url = proc.stdout.strip().splitlines()[-1]
    return int(url.rstrip("/").rsplit("/", 1)[-1])


def read_switch(name: str, repo: str) -> bool:
    proc = subprocess.run(["gh", "variable", "get", name, "-R", repo], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def run(drafts_dir: Path, repo: str, apply: bool, poster=create_issue) -> list[dict]:
    out = []
    for path in sorted(drafts_dir.glob("*.md")):
        d = parse(path.read_text(encoding="utf-8"))
        ok, why = eligible(d)
        rec = {"draft": path.name, "eligible": ok, "reason": None if ok else why, "issue": None}
        if ok:
            label = why
            fp_line = f"competitive-id: {d['head']['competitive-id']}"
            body = f"{fp_line}\n\n{d['body']}"
            if apply:
                n = poster(repo, d["head"]["title"], body, [label, "needs-human"])
                rec["issue"] = n
                text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
                text = text.replace("approved: true\n", f"approved: true\nposted: #{n}\n", 1)
                path.write_text(text, encoding="utf-8")
            else:
                rec["reason"] = "dry run"
        out.append(rec)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--drafts", required=True, help="the ledger checkout's competitive/drafts directory")
    ap.add_argument("--repo", default="jgravelle/jcodemunch-mcp")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--require-switches", action="store_true", help="re-read INBOUND_ENABLED and COMPETITIVE_POST_ENABLED here (needs a token that can read variables)")
    ap.add_argument("--summary", help="write the JSON summary here")
    a = ap.parse_args(argv)
    if a.require_switches and not (read_switch("INBOUND_ENABLED", a.repo) and read_switch("COMPETITIVE_POST_ENABLED", a.repo)):
        print("refused: a switch is not `true` (INBOUND_ENABLED and COMPETITIVE_POST_ENABLED must both read exactly true)", file=sys.stderr)
        return 78
    results = run(Path(a.drafts), a.repo, a.apply)
    if a.summary:
        Path(a.summary).parent.mkdir(parents=True, exist_ok=True)
        Path(a.summary).write_text(json.dumps(results, indent=1) + "\n", encoding="utf-8")
    posted = [r for r in results if r["issue"]]
    print(f"post: {len(results)} draft(s), {sum(1 for r in results if r['eligible'])} eligible, {len(posted)} posted{'' if a.apply else ' (dry run)'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
