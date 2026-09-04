---
description: "Classify a GitHub issue (bug, feature, question, duplicate, security, dependency), recommend labels, propose a split when it carries more than one finding, and draft the response with a timebox and its default. Drafts only; nothing is posted or labelled."
argument-hint: "<issue number>"
---

<!--
purpose:  89% of issues carry no label and multi-finding reports were split
          by hand; the draft makes the record without touching it
invokes:  gh issue view / gh issue list / gh label list (read only); the
          spokesperson agent for the outward-bound pass; CLAUDE.md "Issue +
          release policy" (1, 2, 3a, 3c); skill pr-description (comment half)
produces: .claude/state/runs/triage-<n>-<UTC>/TRIAGE.md: classification,
          label recommendation, split proposal, draft response
refuses:  to comment, label, close, assign or transfer; to post anything
-->

Issue: #$ARGUMENTS

1. **Read.** `GITHUB_TOKEN="" gh issue view $ARGUMENTS --comments --json
   number,title,body,author,labels,createdAt,comments`. And the neighbours
   for duplicates: `GITHUB_TOKEN="" gh issue list --state all --limit 40
   --json number,title,state`, PLUS every issue or PR the body cites by
   number (`gh issue view <n>`; the window misses an older predecessor).
   `GITHUB_TOKEN="" gh label list` for the label set (never a remembered one).
2. **Classify.** One of: bug, feature, question, duplicate (of #N),
   security, dependency, release-followup. Evidence for the class in one
   line each: a reproduction present? a version named? a stack trace? a
   request for behaviour that does not exist? A body with more than one
   finding is classified PER FINDING. For a bug, is it a regression of a
   Standing lesson (CLAUDE.md "Standing lessons") or an ARCHAEOLOGY row
   (`docs/harness/ARCHAEOLOGY.md`)? An existing label that contradicts the
   class is listed for removal in step 6.
3. **Split.** Count the findings in the body. More than one: propose one
   issue per finding (policy 1), each with a title and the paragraph that
   belongs to it, cross-linked, credit on each; write each proposed body to
   `issue-<letter>.md` in the run directory. The parent closes once the
   split issues carry the text; the draft says so.
4. **Vendor shape.** If the issue asks for a NEW named provider, gateway,
   SDK or endpoint (an existing dependency is not one), run policy 3c's three profile queries (`gh api users/<login>`,
   the two `search/issues` counts) and the demand query, and put the
   numbers in TRIAGE.md. Quality is not the discriminator.
5. **Draft the response.** Load `pr-description` (its comment half). A
   reproduction request names exactly what is missing. A timebox states
   the deadline AND the default action in the same sentence, anchored on
   the time the comment would be posted (now, UTC), never exceeding 24
   hours (policy 3a); no extension clause is advertised; a default that
   promises OUR work names who does it and is feasible in the window. Never
   answer pain with aggregates (3b). Hand the draft to the `spokesperson`
   agent for the outward-bound pass and keep its version.
6. **Write** `TRIAGE.md` and `response.md` (the draft alone) in
   `.claude/state/runs/triage-<n>-<YYYYMMDDTHHMMSSZ>/`: class, labels to
   add and remove (from step 1's list), split proposal, draft response, and
   the exact `gh` lines the human would run to apply them in cmd.exe form,
   referencing the body files written in steps 3 and 5. ⚠ Write these
   files with the Write tool, never a Bash heredoc: the deny guard blocks
   any Bash line whose TEXT carries a posting verb (FINDINGS W-8). Print
   TRIAGE.md. Post nothing.
