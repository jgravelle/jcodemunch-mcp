"""suggest_corrections: turn retrieval regret into suggested config fixes.

Fuses three read-only signals into a prioritized, explainable set of
**suggested** corrections:
  * regret clusters from the ranking_events ledger (``retrieval/regret.py``),
  * stale-config findings from ``audit_agent_config`` (telemetry-blind, static),
  * a dry-run weight proposal from ``WeightTuner`` (the existing tuner).

Charter: this NEVER writes a user file. Each correction carries its evidence
and, where applicable, a unified-diff *preview* of the CLAUDE.md edit; applying
it is the user's keystroke. The only thing the suite may persist is the ranking
weights sidecar (tuning.jsonc), and only behind an explicit ``apply_weights``.

Clean-room note: the differentiator is "suggest, don't write" — the read-only
charter ([[feedback_jcm_read_only_charter]]) expressed as a feature.
"""

from __future__ import annotations

import difflib
import os
from typing import Any, Optional

from ..retrieval.regret import analyze_regret, DEFAULT_WINDOW_DAYS
from ..retrieval.tuning import WeightTuner
from .audit_agent_config import (
    audit_agent_config,
    _discover_files,
    _fuzzy_suggest,
)

# Tools that signal a routing problem when they recur on a failing query: a
# text/content scan where a structural tool would have answered cleanly.
_ROUTING_REDIRECT = {
    "search_text": "search_symbols",
    "get_file_content": "get_file_outline",
}

# Cap corrections so a noisy ledger can't bury CLAUDE.md in churn.
_MAX_CORRECTIONS = 12


def _primary_config(files: list[dict]) -> Optional[dict]:
    """Pick the CLAUDE.md-class file to target for suggested patches.
    Prefer a project-scoped CLAUDE.md, else the first project file, else the
    first global one."""
    project = [f for f in files if f.get("scope") == "project"]
    for f in project:
        if os.path.basename(f["path"]).upper().startswith("CLAUDE"):
            return f
    if project:
        return project[0]
    return files[0] if files else None


def _append_diff(target: Optional[dict], section: str, lines: list[str]) -> Optional[str]:
    """Render a unified-diff preview that appends ``lines`` under a markdown
    ``section`` heading at the end of the target file. Returns None when there
    is no target file or the exact lines already appear (dedupe)."""
    if target is None:
        return None
    content = target.get("content", "") or ""
    # Dedupe: if every suggested line is already present, suggest nothing.
    if all(ln.strip() and ln in content for ln in lines):
        return None
    path = os.path.basename(target["path"])
    old = content.splitlines(keepends=True)
    addition = []
    if content and not content.endswith("\n"):
        addition.append("\n")
    addition.append(f"\n{section}\n")
    addition.extend(ln + "\n" for ln in lines)
    new = old + addition
    diff = difflib.unified_diff(
        old, new, fromfile=f"a/{path}", tofile=f"b/{path}", n=2,
    )
    return "".join(diff)


def _first_query(cluster: dict) -> str:
    ex = cluster.get("query_examples") or []
    return ex[0] if ex else ""


def _routing_correction(cluster: dict, target: Optional[dict]) -> Optional[dict]:
    """Map a thin/ambiguous/churn cluster whose tool was a redirectable scan
    into a CLAUDE.md routing rule."""
    redirects = [(t, _ROUTING_REDIRECT[t]) for t in cluster.get("tools", [])
                 if t in _ROUTING_REDIRECT]
    if not redirects:
        return None
    frm, to = redirects[0]
    q = _first_query(cluster)
    rule = f"- For \"{q[:60]}\"-style lookups, prefer `{to}` over `{frm}`." if q \
        else f"- Prefer `{to}` over `{frm}` for symbol lookups."
    patch = _append_diff(target, "## Retrieval routing (suggested)", [rule])
    return {
        "kind": "routing",
        "severity": cluster["severity"],
        "cause": (
            f"`{frm}` recurred on a query that returned weak/ambiguous results "
            f"({cluster['signal']}, {cluster['event_count']}x); a structural "
            f"tool (`{to}`) usually answers these in one call."
        ),
        "evidence": {
            "query_examples": cluster.get("query_examples", []),
            "event_count": cluster["event_count"],
            "tools": cluster.get("tools", []),
        },
        "recommended_action": f"Add a CLAUDE.md routing line steering `{frm}` -> `{to}`.",
        "suggested_patch": patch,
    }


def _vocabulary_correction(
    cluster: dict, symbol_names: set, target: Optional[dict]
) -> Optional[dict]:
    """Identity miss rescued by semantic search => agent vocabulary doesn't
    match a real symbol name. Name the intended symbol via fuzzy match and
    suggest a glossary line."""
    q = _first_query(cluster)
    if not q or not symbol_names:
        return None
    # Map the most symbol-like token of the query to a real symbol name.
    tokens = [t for t in q.replace("_", " ").replace(".", " ").split() if len(t) >= 3]
    suggested = None
    agent_term = None
    for tok in sorted(tokens, key=len, reverse=True):
        hit = _fuzzy_suggest(tok, symbol_names, max_dist=3)
        if hit and hit.lower() != tok.lower():
            suggested, agent_term = hit, tok
            break
    if not suggested:
        return None
    line = f"- When the task says \"{agent_term}\", the codebase calls it `{suggested}`."
    patch = _append_diff(target, "## Vocabulary map (suggested)", [line])
    return {
        "kind": "vocabulary",
        "severity": cluster["severity"],
        "cause": (
            f"Queries for \"{agent_term}\" missed every symbol name but were "
            f"rescued by semantic search ({cluster['event_count']}x) — a "
            f"vocabulary gap. Nearest real symbol: `{suggested}`."
        ),
        "evidence": {
            "query_examples": cluster.get("query_examples", []),
            "event_count": cluster["event_count"],
            "agent_term": agent_term,
            "codebase_symbol": suggested,
        },
        "recommended_action": (
            f"Add a CLAUDE.md glossary line: \"{agent_term}\" -> `{suggested}`."
        ),
        "suggested_patch": patch,
    }


def _freshness_correction(cluster: dict, repo: Optional[str], storage_path: Optional[str]) -> dict:
    """Stale-at-query => the index lagged edits when the agent searched.
    Operational hint, no file patch."""
    service_active = False
    try:
        from .get_watch_status import get_watch_status
        ws = get_watch_status(storage_path=storage_path)
        if isinstance(ws, dict):
            service_active = bool((ws.get("service") or {}).get("active"))
    except Exception:
        service_active = False
    rate = cluster.get("evidence", {}).get("stale_rate")
    return {
        "kind": "index_freshness",
        "severity": cluster["severity"],
        "cause": (
            f"{int((rate or 0) * 100)}% of recent queries hit a stale index — "
            f"symbols lagged edits at query time."
        ),
        "evidence": cluster.get("evidence", {}),
        "recommended_action": (
            "The watch service isn't running — run `jcodemunch-mcp watch-install` "
            "(or `watch-all`) to auto-reindex on change."
            if not service_active else
            "The watch service is running but the index still went stale; check "
            "`get_watch_status` for per-repo reindex lag."
        ),
        "suggested_patch": None,
    }


def _stale_config_corrections(audit: dict) -> list[dict]:
    """Fold audit_agent_config dead-symbol / dead-path findings into the
    correction stream — these directly cause agent confusion and are already
    detected by the static auditor."""
    out = []
    for f in (audit.get("findings") or []):
        # ⚠ audit_agent_config labels findings with "category", never "type".
        # This read `f.get("type", "")`, so the membership test could not match
        # and this correction kind had never once been emitted. Accept both
        # spellings rather than only the right one: a caller may pass a
        # finding stream from elsewhere, and silently dropping it is the exact
        # failure being fixed here.
        ftype = f.get("category") or f.get("type", "")
        # ``unresolved_reference`` (info) is deliberately NOT folded: it
        # records absence-only evidence -- the name may be an external
        # tool or harness facility that is not a repository symbol -- and
        # folding it here would turn "could not corroborate" back into
        # "remove this reference" (e.g. the harness tool ``AskUserQuestion``).
        # Only evidence-backed staleness becomes an actionable correction.
        if ftype in ("stale_symbol", "dead_path", "stale_reference", "dead_file"):
            out.append({
                "kind": "stale_config",
                "severity": f.get("severity", "warning"),
                "cause": f.get("message", "Config references something that no longer exists."),
                "evidence": {k: f[k] for k in ("file", "line", "reference", "suggestion")
                             if k in f},
                "recommended_action": f.get(
                    "suggestion", "Remove or update the stale reference in your config file."),
                "suggested_patch": None,
            })
    return out


def _skill_candidate_corrections(audit: dict) -> list[dict]:
    """Fold skill-candidate findings into the correction stream.

    Emitted only when the advisory is enabled (``skill_advisor_mode: advise``);
    ``audit_agent_config`` returns no such findings otherwise, so this is a
    no-op on a default install.

    ⚠ ``suggested_patch`` stays None on purpose. The action is "move this prose
    into a skill and leave a pointer" — a two-file edit whose destination this
    tool does not know. A unified diff that only showed the deletion would read
    as "delete this section", which is not the recommendation.
    """
    out = []
    for f in (audit.get("findings") or []):
        if (f.get("category") or f.get("type", "")) != "skill_candidate":
            continue
        out.append({
            "kind": "skill_candidate",
            "severity": f.get("severity", "info"),
            "cause": f.get("message", ""),
            "evidence": {
                k: f[k] for k in (
                    "file", "line", "end_line", "section", "tokens", "subtree",
                    "concentration", "repo_share", "resolved_refs",
                    "symbols_resolved", "paths_resolved", "relevance_measured",
                ) if k in f
            },
            "recommended_action": (
                f"Move the '{f.get('section', '')}' section into a skill loaded on "
                f"demand and leave a one-line pointer in its place. The prose moves "
                f"unchanged — nothing is summarised or rewritten."
            ),
            "suggested_patch": None,
        })
    return out


def suggest_corrections(
    *,
    repo: Optional[str] = None,
    project_path: Optional[str] = None,
    storage_path: Optional[str] = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    all_time: bool = False,
    apply_weights: bool = False,
) -> dict[str, Any]:
    """Suggest read-only retrieval/config corrections mined from regret.

    Combines ledger regret + static config audit + a dry-run weight proposal.
    Returns the contract documented in the PRD. Writes nothing to user files;
    ``apply_weights=True`` is the only path that persists state, and only to the
    ranking-weights sidecar (tuning.jsonc), never user source.
    """
    storage_path = storage_path or os.environ.get("CODE_INDEX_PATH")

    regret = analyze_regret(
        repo, window_days=window_days, storage_path=storage_path, all_time=all_time,
    ) if repo else {"telemetry_present": False, "clusters": [], "events_analyzed": 0,
                    "inflation": {"basis": "calls", "measurable": False,
                                  "reason": "no_repo"},
                    "hint": "Pass a repo to mine its retrieval ledger."}

    # Config files (for patch targets + dedupe) and the static audit.
    files = _discover_files(project_path or os.getcwd())
    target = _primary_config(files)

    audit = {}
    symbol_names: set = set()
    if repo:
        try:
            audit = audit_agent_config(
                repo=repo, project_path=project_path, storage_path=storage_path)
        except Exception:
            audit = {}
        # Pull current symbol names for vocabulary mapping + staleness re-verify.
        try:
            from ._utils import resolve_repo as _resolve
            from ..storage import IndexStore
            owner, name = _resolve(repo, storage_path)
            idx = IndexStore(base_path=storage_path).load_index(owner, name)
            if idx:
                symbol_names = {s.get("name", "") for s in idx.symbols if s.get("name")}
        except Exception:
            symbol_names = set()

    corrections: list[dict] = []
    for c in regret.get("clusters", []):
        sig = c["signal"]
        if sig in ("thin_result", "ambiguous_top", "requery_churn"):
            corr = _routing_correction(c, target)
            if corr:
                corrections.append(corr)
        elif sig == "vocabulary_gap":
            corr = _vocabulary_correction(c, symbol_names, target)
            if corr:
                corrections.append(corr)
        elif sig == "stale_at_query":
            corrections.append(_freshness_correction(c, repo, storage_path))
        # low_confidence/ambiguous_top weight-shaped regret is covered by the
        # weight_proposal below rather than a per-cluster correction.

    corrections.extend(_stale_config_corrections(audit))
    corrections.extend(_skill_candidate_corrections(audit))

    # Dedupe: multiple regret signals can converge on the same recommendation
    # (e.g. thin_result and requery_churn both steering search_text ->
    # search_symbols). Keep the highest-severity instance, merge the evidence
    # query examples so the surviving correction still shows the full picture.
    _rank0 = {"high": 0, "error": 0, "medium": 1, "warning": 1, "low": 2, "info": 2}
    deduped: dict[tuple, dict] = {}
    for c in corrections:
        key = (c["kind"], c.get("recommended_action"))
        prev = deduped.get(key)
        if prev is None:
            deduped[key] = c
            continue
        keep, drop = (c, prev) if _rank0.get(c.get("severity"), 9) < _rank0.get(prev.get("severity"), 9) else (prev, c)
        ev_keep = keep.setdefault("evidence", {})
        merged = list(ev_keep.get("query_examples", []))
        for q in (drop.get("evidence", {}).get("query_examples") or []):
            if q not in merged:
                merged.append(q)
        if merged:
            ev_keep["query_examples"] = merged[:5]
        deduped[key] = keep
    corrections = list(deduped.values())

    # Dry-run (or applied) weight proposal for the semantic/identity split.
    weight_proposal = None
    if repo:
        try:
            weight_proposal = WeightTuner(base_path=storage_path).learn(
                repo, dry_run=(not apply_weights), max_age_days=window_days if not all_time else 0,
            )
        except Exception:
            weight_proposal = None

    # Severity-rank + cap.
    _rank = {"high": 0, "error": 0, "medium": 1, "warning": 1, "low": 2, "info": 2}
    corrections.sort(key=lambda c: _rank.get(c.get("severity"), 9))
    corrections = corrections[:_MAX_CORRECTIONS]

    return {
        "repo": repo,
        "telemetry_present": regret.get("telemetry_present", False),
        "window_days": regret.get("window_days"),
        "events_analyzed": regret.get("events_analyzed", 0),
        # v1.108.290. Passed through rather than recomputed, and passed through
        # even when unmeasurable: a caller who cannot see WHY the ratio is absent
        # reads its absence as zero inflation. #500's lesson -- a number computed
        # and then discarded is the same defect as not computing it.
        "inflation": regret.get("inflation"),
        "corrections": corrections,
        "weight_proposal": weight_proposal,
        "config_files_scanned": [os.path.basename(f["path"]) for f in files],
        "hint": regret.get("hint"),
        "_meta": {
            "charter": "read-only: suggestions only, no user file is written",
            "weights_applied": bool(apply_weights and weight_proposal
                                    and weight_proposal.get("applied")),
        },
    }
