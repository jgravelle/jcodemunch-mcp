---
name: mechanism-not-instance
description: "The two questions that separate a fix for the reported spelling from a fix for the property. Load in /fix-issue steps 4-5 and in review of any fix."
---
# Mechanism, not instance

Authority: CLAUDE.md "Standing lessons" 08-19 (#506-#509: a second
generator, a second call site, a second derivation), 09-01 (#566: a guard
written against a spelling), 09-02 (#572: fixed in the cache, not at the
two call sites); `docs/harness/ARCHAEOLOGY.md` for the guard that already
names the lesson.

Ask, and write the answers into ISSUE.md:
1. **Does the fix belong one layer down?** If the call site was
   reproducing an authority's logic (a skip list, a path rule, a cache
   contract, a version read), fix the authority so the tool written next
   inherits it. A per-consumer patch arms the trap again.
2. **What other spellings of this input exist?** `find_references` on the
   fixed symbol and its callers; the qualified form, the aliased form, the
   second transport, the other OS's separator. A test per spelling found.

Also: **run the reintroduced defect against the new test** (Practice 9,
non-vacuity). A guard green only against the fixed tree may be measuring
nothing. And when an OLD test turns red, check whether it was the defect's
witness before changing the code back.
