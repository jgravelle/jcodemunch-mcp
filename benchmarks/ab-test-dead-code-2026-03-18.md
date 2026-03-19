# A/B Test: Dead Code Detection (2026-03-18)

**Author:** @Mharbulous
**Issue:** https://github.com/jgravelle/jcodemunch-mcp/issues/130
**Raw data:** https://gist.github.com/Mharbulous/bb097396fa92ef1d34d03a72b56b2c61

---

## Setup

- **Task:** Classify 8 source files and their individual exports as dead or alive
- **Ground truth:** knip v5 + grep import analysis, manually verified post-test
- **Model:** Claude Sonnet 4.6 (both variants)
- **Iterations:** 50 (25 pairs, same 8 files per pair)
- **Timeout:** 300s per iteration
- **Codebase:** Vue 3 + Vite + Vuetify 3 + Firebase + Cloud Functions (Windows 11, MINGW64)
- **Design note:** No subagent consensus overhead — pure tool-layer cost measurement. This fixes the dilution issue from the naming-audit test.

---

## Results

| Metric | Variant A (Native) | Variant B (JCodeMunch) |
|--------|-------------------|----------------------|
| Successful iterations | 24/25 (96%) | 23/25 (92%) |
| Mean cost/iteration | $0.4474 | $0.3560 (−20.0%) |
| Mean file-level F1 | 95.8% | 86.2% |
| Mean export-level F1 | 93.3% | 64.1% |
| Mean total tokens | 449,356 | 289,275 (−36%) |
| Mean cache reads | — | −39% vs A |
| Mean duration (s) | 129 | 117 |
| Timeouts | 0 | 0 |

Cost savings are statistically significant (Wilcoxon p=0.0074, Cohen's d=−0.583).

### File-level F1 by category

| Category | A mean F1 | B mean F1 |
|----------|-----------|-----------|
| dead (all exports unused) | 95.8% | 95.7% |
| alive-with-dead-exports | 100.0% | 69.6% |
| alive (all exports used) | 100.0% | 69.6% |

---

## What worked well

**Dead file detection is equivalent.** For fully dead files, JCodeMunch matched native tools at ~96% F1. The 20% cost advantage applies here with no accuracy penalty.

**Cost efficiency confirmed.** With no fixed overhead diluting the measurement, this test validates the 15–25% tool-layer savings estimated in the naming-audit test. Mechanism: 39% fewer cache reads — structured queries return smaller payloads than raw file reads.

**Zero timeouts.** Both variants completed all iterations within 300s.

---

## Gaps identified and resolved

### Gap 1: Dynamic import detection — fixed in v1.8.1

`find_importers` missed Vue Router lazy routes:

```js
// src/router/routes/featureRoutes.js
component: () => import('../../features/lists/views/Lists.vue')
```

The JS extractor handled `import ... from`, `import '...'` (side-effect), `require()`, and re-exports — but not the `import()` call form. Four Vue view files (`Lists.vue`, `Cast.vue`, `DocumentTable.vue`, `Matters.vue`) were misclassified as dead.

**Fix:** Added `_JS_DYNAMIC_IMPORT` regex to `_extract_js_imports`. Released v1.8.1.

### Gap 2: Export-level granularity — task framing issue

Largest accuracy gap (F1 64.1% vs 93.3%) at export level. When a file had any live import, B classified all its exports as alive without individual verification.

Example: `useDocumentColumns.js` exports `useDocumentColumns` (alive), `NON_SYSTEM_COLUMNS` (dead), `TIMESTAMP_COLUMNS` (dead). B classified all three as alive in 6/6 iterations.

The tool for this exists: `find_references("NON_SYSTEM_COLUMNS", repo)` returns zero results. B's strategy stopped at file-level liveness. File-level liveness and export-level liveness are separate questions requiring separate queries.

**Resolution:** Updated `find_importers` tool description to note this distinction explicitly. No code change.

### Gap 3: Transitive dead code — fixed in v1.8.3

`storageLoader.js` was misclassified as alive by both variants. Its sole importer (`firestoreDocumentLoader.js`) is itself dead — replaced by `useFirestoreDocumentLoader.js`. Neither variant followed the chain.

**Fix:** `find_importers` now returns `has_importers: bool` on each result. When `false`, the importer has no importers of its own — the chain is transitively dead. One additional O(n) pass over the import graph; no re-indexing required. Released v1.8.3. Closes #132.

---

## Summary

The 20% cost figure is the cleanest tool-layer measurement to date. Two of the three identified gaps are now fixed (v1.8.1, v1.8.3). The export-level gap is a task-framing issue, not a missing capability.
