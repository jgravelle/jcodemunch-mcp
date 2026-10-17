"""``jcodemunch-mcp receipt`` — token-economy ledger.

Parses Claude Code's ``<projects-root>/**/*.jsonl`` transcripts, extracts every
``mcp__jcodemunch__*`` tool call + its result, applies per-tool savings
multipliers calibrated against the published RAG benchmarks, and prints
an honest dollar-denominated ROI ledger.

The savings model is **modeled, not measured** — token-savings is
inherently counterfactual (we can't observe what naive Read+Grep would
have cost without running it). The methodology is auditable via
``--explain``; raw per-call data is exportable via ``--export``.
"""

from __future__ import annotations

import argparse
import collections
import csv
import datetime as _dt
import io
import json
import os
import sys
from pathlib import Path
from typing import Iterable, Optional

from ..savings_credit import TEXT_SEARCH_STYLE_TOOLS

# Per-tool savings multipliers: jcodemunch result tokens × multiplier ≈
# what naive Read+Grep would have spent answering the same query.
# Calibrated to be *conservative* against published RAG benchmarks
# (which show 30–56× reductions for retrieval-style queries on Express,
# FastAPI, Gin). Underestimating savings keeps credibility — a number
# that's plausibly low is more useful than one that's optimistic.
_TOOL_MULTIPLIERS: dict[str, float] = {
    # Pure retrieval — narrow query against indexed corpus.
    "search_symbols": 20.0,
    # ⚠ L-180: search_text and check_references are absent ON PURPOSE. Their
    # answer is the matching lines, which Grep returns as well, so they earn no
    # credit until a measurement supports one (savings_credit.py). Listing a
    # multiplier for either here is refused by tests/test_text_search_no_credit.py.
    "search_columns": 15.0,
    "search_ast": 18.0,
    "get_ranked_context": 18.0,
    "winnow_symbols": 15.0,
    # Targeted symbol/file fetch — surgical vs whole-file Read.
    "get_symbol_source": 8.0,
    "get_context_bundle": 10.0,
    "get_file_outline": 6.0,
    "get_file_content": 2.0,  # nearly 1:1, only saves on filtering
    # Repo structure / orientation.
    "get_repo_outline": 8.0,
    "get_file_tree": 4.0,
    "get_project_intel": 12.0,
    "get_session_context": 6.0,
    "get_session_snapshot": 6.0,
    # Graph queries — the structurally hardest things to do with grep.
    "find_importers": 25.0,
    "find_references": 25.0,
    "get_call_hierarchy": 30.0,
    "get_dependency_graph": 25.0,
    "get_dependency_cycles": 25.0,
    "get_blast_radius": 35.0,
    "get_class_hierarchy": 20.0,
    "get_layer_violations": 20.0,
    "get_extraction_candidates": 25.0,
    "get_signal_chains": 30.0,
    "get_cross_repo_map": 25.0,
    "get_related_symbols": 18.0,
    # Risk / health / quality — composite metrics that have no naive
    # equivalent (you'd have to write the analysis yourself).
    "get_pr_risk_profile": 40.0,
    "get_repo_health": 35.0,
    "get_hotspots": 25.0,
    "get_symbol_complexity": 12.0,
    "get_churn_rate": 6.0,
    "get_symbol_provenance": 15.0,
    "get_untested_symbols": 30.0,
    "find_dead_code": 35.0,
    "get_dead_code_v2": 35.0,
    "get_tectonic_map": 30.0,
    "get_coupling_metrics": 20.0,
    "get_symbol_importance": 15.0,
    # Refactoring / maintenance.
    "plan_refactoring": 25.0,
    "check_rename_safe": 15.0,
    "get_symbol_diff": 12.0,
    "get_changed_symbols": 12.0,
    "audit_agent_config": 8.0,
    # Indexing / repo management.
    "resolve_repo": 3.0,
    "list_repos": 2.0,
    "index_folder": 2.0,
    "index_repo": 2.0,
    "index_file": 2.0,
}

# Default multiplier for tools not in the table above. Conservative
# middle-of-the-road estimate.
_DEFAULT_MULTIPLIER = 8.0

#: The label of the savings figure on every surface (L-180). GROSS because only
#: the answer's own size is subtracted from the modeled baseline; nothing else
#: the call cost is. It read "Net savings" until this change.
SAVINGS_LABEL = "Modeled gross savings"

#: The Counter front door runs another tool under its own name: `order` names
#: the action in its INPUT, `route(execute=true)` names it in the first block of
#: its RESULT (`executed_action`). Read there, a text-search action is priced as
#: the text search it was, or it earns the default multiplier. ⚠ Only a
#: text-search action is re-priced: every other action keeps the default, as
#: before L-180, because moving those figures is outside the ruling.
_ORDER = "order"
_ROUTE = "route"


def _routed_action(content) -> Optional[str]:
    """`executed_action` from a `route` result's first text block, else None."""
    if isinstance(content, list):
        first = next(
            (b.get("text") for b in content if isinstance(b, dict) and b.get("type") == "text"),
            None,
        )
    else:
        first = content
    if not isinstance(first, str) or '"executed_action"' not in first:
        return None
    try:
        head = json.loads(first)
    except ValueError:
        return None
    action = head.get("executed_action") if isinstance(head, dict) else None
    return action if isinstance(action, str) else None


def _multiplier(tool: str) -> float:
    """Baseline multiplier for one call. 1.0 (no saving) for a text search."""
    if tool in TEXT_SEARCH_STYLE_TOOLS:
        return 1.0
    return _TOOL_MULTIPLIERS.get(tool, _DEFAULT_MULTIPLIER)

# Model prices in USD per million input tokens. Cache-read pricing is
# typically 10% of normal input pricing for Anthropic models, but we use
# normal input pricing here because savings are computed against a
# counterfactual (naive Read+Grep would have been *fresh* input, not
# cached). Opus is the default: most jcodemunch users run an Opus-grade
# model where savings actually move a budget needle.
# Rates as of 2026-09-01 (platform.claude.com/docs/en/about-claude/pricing).
# Update when the public price list changes; the test suite pins these to that
# dated source.
#
# ⚠⚠ A KEY NAMES A FAMILY, AND THE FAMILY'S MEMBERS CAN BE PRICED DIFFERENTLY.
# `sonnet` read 3.0 from 2026-06-24 to 2026-09-01 with the comment "Sonnet 5 /
# 4.6" -- but Sonnet 5 has NEVER been $3. It launched at $2 introductory with a
# $3 increase SCHEDULED for 2026-09-01, and that increase was cancelled the day
# before it would have applied. ⚠ **A rate written for a future date is wrong
# for the whole interval before it, and nothing distinguishes it from a stale
# one** -- both read as a plausible number beside a plausible date. Price the
# CURRENT member of the family and say which one; a superseded member's rate
# goes in the comment, never in the value.
_MODEL_PRICES_USD_PER_MTOK: dict[str, float] = {
    "fable":  10.0,   # Claude Fable 5 ($10/MTok input)
    "opus":   5.0,    # Claude Opus 5 / 4.8 / 4.7 / 4.6 ($5/MTok input; retired 4.0/4.1 were $15)
    "sonnet": 2.0,    # Claude Sonnet 5 ($2/MTok input; superseded Sonnet 4.6 was $3)
    "haiku":  1.0,    # Claude Haiku 4.5 ($1/MTok input)
}

_DEFAULT_MODEL = "opus"

#: What the dollar figure actually prices, stated because a figure whose rate
#: basis is unstated gets one supplied for free. Fourth instance of the family
#: after `hit_rate_basis`, `schema_tokens_basis` and `basis: excess_calls`.
#:
#: ⚠⚠ **A LABEL, NEVER A SCALED NUMBER.** The arithmetic below is unchanged and
#: must stay unchanged. Prompted by a competitor (Graft, 2026-09-02) converting
#: claimed savings to a *blended session rate* — correct for tokens CONSUMED and
#: wrong for tokens AVOIDED, which is what this measures. Measured on this box
#: across 25 transcripts: **98.6% of input is cache reads**, a 0.1166x blended
#: multiplier. Dividing by that would cut the figure ~8.6x and would be pricing
#: an avoided token as though it were sitting in the cache being re-read. It is
#: not there: it was never written, so it is never read.
#:
#: ⚠ The honest direction is that this is a FLOOR. A token we did not return
#: would have been paid once at the fresh-input or cache-write rate (the latter
#: carries a premium) and then again at the cache-read rate on every subsequent
#: turn it sat in the prefix. In a session that is 98.6% cache reads — i.e. a
#: long one — that sum exceeds one list-price charge. Same inversion
#: `tier_switch_cost.py` documents: the intuition flips once the block is cached.
SAVINGS_USD_BASIS = "uncached_list_input_rate_once_per_token"

SAVINGS_USD_NOTE = (
    "Each avoided token is priced ONCE at the uncached list input rate. "
    "Counts neither the cache-write premium avoided nor the cache reads "
    "avoided on later turns, so this is a floor, not an estimate."
)

# Approximate bytes-per-token used to convert tool_result content
# byte-length into a token estimate. Same heuristic the rest of the
# package uses (see _BYTES_PER_TOKEN in storage/token_tracker.py).
_BYTES_PER_TOKEN = 4


def _projects_root() -> Path:
    """The default profile's projects root.

    Kept for callers that want the one canonical path; the ledger itself scans
    ``_projects_roots()``, which is a union (jcm#421).
    """
    from ..storage.transcript_roots import default_root
    return default_root()


def _projects_roots(explicit: Optional[list[Path]] = None) -> list[Path]:
    """Every transcript root to scan.

    Claude Code writes transcripts under the *active* profile's config dir, so a
    box running two ``CLAUDE_CONFIG_DIR`` profiles has two disjoint trees and no
    single root covers both (jcm#421 measured 12 of 348 calls counted). With no
    ``--projects-root``, scan the union of the default root, ``CLAUDE_CONFIG_DIR``,
    and the roots the server and hooks registered.

    ``--projects-root`` stays an **override** — it is now repeatable, so it can
    name every profile, but naming any root means "scan exactly these". Making it
    additive instead would break the one thing it was already good for: pinning a
    scan to a known tree.
    """
    if explicit:
        out: list[Path] = []
        seen: set[str] = set()
        for item in explicit:
            path = Path(os.path.expanduser(str(item)))
            key = str(path).casefold() if os.name == "nt" else str(path)
            if key in seen:
                continue
            seen.add(key)
            out.append(path)
        return out
    from ..storage.transcript_roots import known_roots
    return known_roots()


def _index_root() -> Path:
    """Index storage root (honors CODE_INDEX_PATH, default ~/.code-index)."""
    env = os.environ.get("CODE_INDEX_PATH")
    return Path(env) if env else Path.home() / ".code-index"


def lifetime_meter(root: Optional[Path] = None) -> Optional[dict]:
    """Read the persistent per-call savings meter (``_savings.json``).

    This is the cumulative token savings the MCP server records on *every*
    tool call, stored under the index root. It survives Claude Code
    reinstalls (it does not live with the transcripts), so it reflects true
    lifetime usage even when the local transcript history the window ledger
    scans has been cleared. Byte-approximate estimate, like the community
    meter it feeds. Returns None when absent/unreadable/empty.
    """
    path = (root or _index_root()) / "_savings.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    total = data.get("total_tokens_saved")
    if not isinstance(total, (int, float)) or total <= 0:
        return None
    return {"total_tokens_saved": int(total), "anon_id": data.get("anon_id")}


def _result_byte_length(content) -> int:
    """Return the byte length of a tool_result `content` field.

    Claude Code stores tool_result.content as either a string or a list
    of content blocks ({type: 'text', text: '...'}). Sum text lengths;
    other block types contribute nothing to the token estimate.
    """
    if content is None:
        return 0
    if isinstance(content, str):
        return len(content.encode("utf-8", errors="replace"))
    if isinstance(content, list):
        total = 0
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                t = block.get("text") or ""
                total += len(t.encode("utf-8", errors="replace"))
        return total
    return 0


def _parse_iso(ts: str) -> Optional[_dt.datetime]:
    if not ts:
        return None
    try:
        # Claude Code timestamps end in 'Z'.
        return _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def iter_calls(
    projects_root: Path,
    *,
    since: Optional[_dt.datetime] = None,
    until: Optional[_dt.datetime] = None,
) -> Iterable[dict]:
    """Yield {tool, result_tokens, timestamp, project, session} per jcodemunch call.

    Walks the entire ~/.claude/projects/ tree. For each tool_use block
    naming an mcp__jcodemunch__* tool, finds the matching tool_result
    (by tool_use_id) in subsequent user events within the same session
    file, and yields one entry per resolved pair.

    ``since``/``until`` bound the window on the tool_use timestamp;
    ``until`` is exclusive so adjacent calendar windows can't double-count
    a call that lands exactly on the boundary.
    """
    yield from iter_calls_multi([projects_root], since=since, until=until)


# A transcript whose last write predates the window start cannot hold an event
# stamped inside it, so we can skip the file without opening it. The margin
# absorbs coarse filesystem timestamps and small clock skew; the cost of being
# generous is one re-parsed file, the cost of being tight is a lost call.
_MTIME_SKIP_MARGIN = _dt.timedelta(hours=1)


def iter_calls_multi(
    roots: Iterable[Path],
    *,
    since: Optional[_dt.datetime] = None,
    until: Optional[_dt.datetime] = None,
) -> Iterable[dict]:
    """``iter_calls`` over a union of transcript roots (jcm#421).

    Sessions are de-duplicated by file stem — the session UUID — so a root that
    is a symlink, a copy, or an ancestor of another contributes each session
    once. Scanning several roots multiplies the walk, so files whose mtime
    predates ``since`` are skipped unopened; jMunch Console gives this
    subprocess 60 seconds before it drops its Savings panel to fixtures.
    """
    mtime_floor: Optional[float] = None
    if since is not None:
        mtime_floor = (since - _MTIME_SKIP_MARGIN).timestamp()

    seen_sessions: set[str] = set()
    for root in roots:
        try:
            if not root.exists():
                continue
            files = sorted(root.rglob("*.jsonl"))
        except OSError:
            continue
        for jsonl in files:
            key = jsonl.stem
            if key in seen_sessions:
                continue
            try:
                if mtime_floor is not None and jsonl.stat().st_mtime < mtime_floor:
                    # Deliberately does NOT claim the stem: if a later root
                    # holds a fresher copy of the same session, that one must
                    # still be read.
                    continue
            except OSError:
                continue
            seen_sessions.add(key)
            try:
                yield from _iter_calls_in_file(jsonl, since=since, until=until)
            except OSError:
                continue


def _iter_calls_in_file(
    jsonl: Path,
    *,
    since: Optional[_dt.datetime],
    until: Optional[_dt.datetime] = None,
) -> Iterable[dict]:
    """Walk one transcript file once; pair tool_use → tool_result by id."""
    pending: dict[str, dict] = {}  # tool_use_id → call metadata

    try:
        with open(jsonl, encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(ev, dict):
                    continue
                msg = ev.get("message")
                if not isinstance(msg, dict):
                    continue
                content = msg.get("content")
                if not isinstance(content, list):
                    continue

                ts_raw = ev.get("timestamp", "")
                ts = _parse_iso(ts_raw)
                if since and ts and ts < since:
                    # Per-event since filter — but we still walk the whole
                    # file because session files aren't strictly ordered
                    # by event timestamp.
                    pass

                for block in content:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type")

                    if btype == "tool_use":
                        name = block.get("name") or ""
                        if not name.startswith("mcp__jcodemunch"):
                            continue
                        tu_id = block.get("id") or ""
                        if not tu_id:
                            continue
                        # Strip the mcp__jcodemunch__ prefix to get the
                        # bare tool name (search_symbols, etc.).
                        bare = name.split("__")[-1] if "__" in name else name
                        priced_as = bare
                        if bare == _ORDER:
                            tool_input = block.get("input")
                            action = tool_input.get("action") if isinstance(tool_input, dict) else None
                            if isinstance(action, str) and action in TEXT_SEARCH_STYLE_TOOLS:
                                priced_as = action
                        pending[tu_id] = {
                            "tool": bare,
                            "priced_as": priced_as,
                            "timestamp": ts_raw,
                            "_ts_parsed": ts,
                        }

                    elif btype == "tool_result":
                        tu_id = block.get("tool_use_id") or ""
                        if not tu_id or tu_id not in pending:
                            continue
                        meta = pending.pop(tu_id)
                        call_ts = meta["_ts_parsed"]
                        if since and call_ts and call_ts < since:
                            continue
                        if until and call_ts and call_ts >= until:
                            continue
                        result_bytes = _result_byte_length(block.get("content"))
                        result_tokens = max(1, result_bytes // _BYTES_PER_TOKEN)
                        priced_as = meta["priced_as"]
                        if meta["tool"] == _ROUTE:
                            routed = _routed_action(block.get("content"))
                            if routed in TEXT_SEARCH_STYLE_TOOLS:
                                priced_as = routed
                        yield {
                            "tool": meta["tool"],
                            "priced_as": priced_as,
                            "timestamp": meta["timestamp"],
                            "result_tokens": result_tokens,
                            "result_bytes": result_bytes,
                            "session_file": str(jsonl),
                        }
    except OSError:
        return


def aggregate(calls: Iterable[dict]) -> dict:
    """Aggregate per-tool savings from a stream of call records."""
    per_tool: dict[str, dict] = collections.defaultdict(
        lambda: {"calls": 0, "actual_tokens": 0, "baseline_tokens": 0, "savings_tokens": 0}
    )
    total_calls = 0
    for call in calls:
        tool = call["tool"]
        actual = call["result_tokens"]
        mult = _multiplier(call.get("priced_as") or tool)
        baseline = int(actual * mult)
        savings = baseline - actual

        bucket = per_tool[tool]
        bucket["calls"] += 1
        bucket["actual_tokens"] += actual
        bucket["baseline_tokens"] += baseline
        bucket["savings_tokens"] += savings
        total_calls += 1

    totals = {
        "calls": total_calls,
        "actual_tokens": sum(b["actual_tokens"] for b in per_tool.values()),
        "baseline_tokens": sum(b["baseline_tokens"] for b in per_tool.values()),
        "savings_tokens": sum(b["savings_tokens"] for b in per_tool.values()),
    }
    return {"totals": totals, "per_tool": dict(per_tool)}


def aggregate_by_day(calls: Iterable[dict], *, model: str) -> list[dict]:
    """Bucket a stream of call records into per-calendar-day savings rows.

    Days are the caller's LOCAL calendar days (transcript timestamps are
    UTC), because a window like "yesterday" means yesterday where the
    person is sitting, not in UTC. Days with no calls are absent — the
    caller decides whether a gap renders as zero or as nothing.
    """
    per_day: dict[str, dict] = {}
    for call in calls:
        ts = _parse_iso(call.get("timestamp", ""))
        if ts is None:
            continue
        day = ts.astimezone().date().isoformat()
        actual = call["result_tokens"]
        mult = _multiplier(call.get("priced_as") or call["tool"])
        baseline = int(actual * mult)
        bucket = per_day.setdefault(
            day,
            {"date": day, "calls": 0, "actual_tokens": 0, "baseline_tokens": 0, "savings_tokens": 0},
        )
        bucket["calls"] += 1
        bucket["actual_tokens"] += actual
        bucket["baseline_tokens"] += baseline
        bucket["savings_tokens"] += baseline - actual

    rows = [per_day[d] for d in sorted(per_day)]
    for row in rows:
        row["savings_usd"] = dollar_savings(row["savings_tokens"], model)
    return rows


def dollar_savings(savings_tokens: int, model: str) -> float:
    rate = _MODEL_PRICES_USD_PER_MTOK.get(model.lower())
    if rate is None:
        return 0.0
    return (savings_tokens / 1_000_000.0) * rate


def _write_lifetime(out: "io.StringIO", meter: dict, model: str) -> None:
    """Render the persistent lifetime-meter section."""
    total = meter["total_tokens_saved"]
    rate = _MODEL_PRICES_USD_PER_MTOK.get(
        model.lower(), _MODEL_PRICES_USD_PER_MTOK[_DEFAULT_MODEL]
    )
    usd = dollar_savings(total, model)
    out.write("  Lifetime savings (jCodeMunch meter, all-time):\n")
    out.write(f"    Tokens saved:                {total:>15,}\n")
    out.write(f"    Value at {model.title()} pricing (${rate:.2f}/MTok input):  ${usd:,.2f}\n")
    out.write("      Floor: priced once at the uncached list rate.\n")
    out.write("    Persistent per-call meter under the index root; survives\n")
    out.write("    Claude Code reinstalls. The windowed figure above only counts\n")
    out.write("    tool calls still present in local transcripts.\n\n")


def _window_label(since: Optional[_dt.datetime], until: Optional[_dt.datetime]) -> str:
    """Human phrase for an explicit --since/--until window."""
    if since and until:
        return f"{since.date().isoformat()} to {until.date().isoformat()} (end exclusive)"
    if since:
        return f"since {since.date().isoformat()}"
    return f"up to {until.date().isoformat()} (exclusive)"


def render_text(
    agg: dict,
    *,
    days: int,
    model: str,
    primary_only: bool = False,
    meter: Optional[dict] = None,
    window_label: Optional[str] = None,
) -> str:
    """Human-readable ledger output."""
    out = io.StringIO()
    totals = agg["totals"]
    per_tool = agg["per_tool"]
    header = window_label or f"last {days} days"
    out.write(f"jCodeMunch token-economy ledger — {header}\n")
    out.write("=" * 56 + "\n\n")

    if totals["calls"] == 0:
        out.write("No jcodemunch tool calls found in the scanned transcript\n")
        out.write("window (~/.claude/projects/). Local transcripts are cleared on\n")
        out.write("reinstall; the lifetime meter below is the durable record.\n\n")
        if meter:
            _write_lifetime(out, meter, model)
        else:
            out.write("No lifetime meter data yet (index root _savings.json).\n")
        return out.getvalue()

    out.write(f"  Tool calls:                    {totals['calls']:>12,}\n")
    out.write(f"  Tokens delivered (actual):     {totals['actual_tokens']:>12,}\n")
    out.write(f"  Tokens you would have spent:   {totals['baseline_tokens']:>12,}\n")
    out.write(f"                                 {'-' * 12}\n")
    out.write(f"  {SAVINGS_LABEL + ':':<31}{totals['savings_tokens']:>12,} tokens\n")
    # Call-count-invariant companion to the total. The total scales with calls
    # by construction (baseline = actual x multiplier, per call), so a rising
    # total can mean more calls rather than better ones. This figure cannot.
    per_call = totals["savings_tokens"] / totals["calls"] if totals["calls"] else 0
    out.write(f"  Per call:                      {per_call:>12,.0f} tokens/call\n\n")

    rate = _MODEL_PRICES_USD_PER_MTOK.get(model.lower(), _MODEL_PRICES_USD_PER_MTOK[_DEFAULT_MODEL])
    primary_dollars = dollar_savings(totals["savings_tokens"], model)
    out.write(f"  Saved at {model.title()} pricing (${rate:.2f}/MTok input):  ${primary_dollars:,.2f}\n")
    # ⚠ The human surface carries the basis too. A machine-readable field the
    # CLI does not print leaves a human to supply the missing basis himself,
    # and a human is exactly who does that (the v1.108.312 lesson).
    out.write("    Floor: each avoided token priced ONCE at the uncached list\n")
    out.write("    input rate — counts neither the cache-write premium avoided\n")
    out.write("    nor the cache reads avoided on every later turn.\n")

    if not primary_only:
        for other in ("fable", "opus", "sonnet", "haiku"):
            if other == model.lower():
                continue
            other_rate = _MODEL_PRICES_USD_PER_MTOK[other]
            other_dollars = dollar_savings(totals["savings_tokens"], other)
            out.write(f"     ... at {other.title()} pricing (${other_rate:.2f}/MTok):                 ${other_dollars:,.2f}\n")
    out.write("\n")

    if meter:
        _write_lifetime(out, meter, model)

    if per_tool:
        out.write("  Top tools by savings:\n")
        ranked = sorted(
            per_tool.items(),
            key=lambda kv: kv[1]["savings_tokens"],
            reverse=True,
        )[:10]
        out.write(f"    {'tool':<28} {'calls':>8} {'savings (tokens)':>20}\n")
        for name, b in ranked:
            out.write(f"    {name:<28} {b['calls']:>8,} {b['savings_tokens']:>20,}\n")
        out.write("\n")

    out.write("  Methodology: per-tool savings multipliers calibrated against\n")
    out.write("  published RAG benchmarks (Express/FastAPI/Gin). Run with --explain\n")
    out.write("  to see the full multiplier table; --export csv|json for raw data.\n")
    out.write("  Read the total WITH the call count: savings is computed per call,\n")
    out.write("  so it rises with calls by construction and is not cost-per-task.\n")
    out.write("  Tool calls are themselves a cost carrier. --explain has the detail.\n")
    out.write(f"  No credit for {', '.join(sorted(TEXT_SEARCH_STYLE_TOOLS))}: their\n")
    out.write("  answer is the matching lines, which Grep returns as well.\n")
    out.write("  Provenance: basis = measured — committed, drift-guarded artifacts\n")
    out.write("  at benchmarks/provenance/measured.json (tiktoken methodology +\n")
    out.write("  CI-gated replay retrieval golden). --export json carries the block.\n")

    return out.getvalue()


def render_explain() -> str:
    """Per-tool multiplier table + methodology notes."""
    out = io.StringIO()
    out.write("jcodemunch receipt — savings model methodology\n")
    out.write("=" * 56 + "\n\n")
    out.write("Per-tool savings multipliers. For each call:\n")
    out.write("  baseline_tokens = actual_tokens × multiplier\n")
    out.write("  savings_tokens  = baseline_tokens − actual_tokens\n\n")
    out.write(f"The total is labelled '{SAVINGS_LABEL}'. It is MODELED: the\n")
    out.write("baseline is a multiplier, not a measurement. It is GROSS: only\n")
    out.write("the answer's own size is subtracted, not anything else the call\n")
    out.write("cost.\n\n")
    out.write("⚠ Savings is computed PER CALL, so the total rises with the\n")
    out.write("number of calls by construction. Doubling the calls doubles the\n")
    out.write("reported savings even when the work done is identical. The total\n")
    out.write("therefore cannot distinguish 'did more with less' from 'made more\n")
    out.write("calls', and it is not a cost-per-task figure.\n\n")
    out.write("This matters because token cost is not the only carrier. arXiv\n")
    out.write("2608.01347 measures verification loops as a separate, TOOL-borne\n")
    out.write("carrier: the runs with the most redundant verification cost 18x\n")
    out.write("the clean-run median and made 2.5x the tool calls, at no gain in\n")
    out.write("success rate. A workload can cut tokens per call and still cost\n")
    out.write("more. Read the per-call figure alongside the total, and read the\n")
    out.write("call count as a cost, not as evidence of value.\n\n")
    out.write("Calibrated against published RAG benchmarks\n")
    out.write("(benchmarks/rag_baseline_results.md) which show 30–56×\n")
    out.write("retrieval savings on Express/FastAPI/Gin. Multipliers below\n")
    out.write("are deliberately conservative — underestimating savings keeps\n")
    out.write("the dollar number defensible.\n\n")
    out.write(f"Default multiplier (unlisted tools): {_DEFAULT_MULTIPLIER}×\n\n")

    rows = sorted(_TOOL_MULTIPLIERS.items(), key=lambda kv: kv[1], reverse=True)
    out.write(f"  {'tool':<32} {'multiplier':>12}\n")
    for tool, mult in rows:
        out.write(f"  {tool:<32} {mult:>11.1f}×\n")
    out.write("\nNo credit (L-180). A text-search-style tool's answer is the\n")
    out.write("matching lines, which Grep returns as well, so it earns nothing\n")
    out.write("until a measurement supports a number: its multiplier is 1.0,\n")
    out.write("so baseline equals actual and savings is 0. The same holds when\n")
    out.write("one runs through the front door (`order`, or `route` with\n")
    out.write("execute=true); every other front-door action keeps the default.\n")
    for tool, reason in sorted(TEXT_SEARCH_STYLE_TOOLS.items()):
        out.write(f"  {tool:<32} {_multiplier(tool):>11.1f}×  no credit: {reason}\n")
    out.write("\nTo override a tool's multiplier, edit cli/receipt.py and\n")
    out.write("send a PR with your reasoning. The numbers should reflect\n")
    out.write("realistic naive-tool-call counterfactuals, not optimism.\n")
    return out.getvalue()


def render_rates() -> str:
    """The model input-price table as JSON. Cheap — scans no transcripts.

    Exists so a consumer that prices its own token counts (the jMunch Console
    values jcm's lifetime meter, which the receipt never scans) reads the rates
    from the one table instead of keeping a copy. A copy is not a hypothetical
    hazard: the Console's duplicate sat at the retired $15 Opus rate long after
    this table moved to $5, and its two dollar figures silently disagreed by 3x.
    """
    return json.dumps(
        {
            "rates_usd_per_mtok": _MODEL_PRICES_USD_PER_MTOK,
            "default_model": _DEFAULT_MODEL,
            "savings_usd_basis": SAVINGS_USD_BASIS,
            "savings_usd_note": SAVINGS_USD_NOTE,
        },
        indent=2,
    )


def render_csv(agg: dict) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["tool", "calls", "actual_tokens", "baseline_tokens", "savings_tokens", "savings_label"])
    for tool, b in sorted(agg["per_tool"].items()):
        w.writerow([tool, b["calls"], b["actual_tokens"], b["baseline_tokens"], b["savings_tokens"], SAVINGS_LABEL])
    return out.getvalue()


def render_json(
    agg: dict,
    *,
    model: str,
    meter: Optional[dict] = None,
    by_day: Optional[list[dict]] = None,
    window: Optional[dict] = None,
    roots: Optional[list[Path]] = None,
) -> str:
    from ..retrieval.provenance import measured_provenance

    payload = {
        "totals": agg["totals"],
        "per_tool": agg["per_tool"],
        "model": model,
        "savings_usd": dollar_savings(agg["totals"]["savings_tokens"], model),
        "savings_label": SAVINGS_LABEL,
        "no_credit_tools": dict(TEXT_SEARCH_STYLE_TOOLS),
        "savings_usd_basis": SAVINGS_USD_BASIS,
        "savings_usd_note": SAVINGS_USD_NOTE,
        "provenance": measured_provenance(),
    }
    if window:
        payload["window"] = window
    if roots:
        # What was actually walked. A consumer that sees a suspiciously low
        # call count can tell "one profile scanned" from "no calls made"
        # without guessing (jcm#421).
        payload["transcript_roots"] = [str(r) for r in roots]
    if by_day is not None:
        payload["by_day"] = by_day
    if meter:
        payload["lifetime"] = {
            "tokens_saved": meter["total_tokens_saved"],
            "usd": dollar_savings(meter["total_tokens_saved"], model),
        }
    return json.dumps(payload, indent=2)


def parse_window_bound(value: str) -> _dt.datetime:
    """Parse a --since/--until bound into an aware datetime.

    Accepts a calendar date (``2026-07-16`` → local midnight) or a full
    ISO datetime (``2026-07-16T09:30``, trailing ``Z`` allowed). A naive
    datetime is read as local time, since that's what the person meant.
    """
    text = (value or "").strip()
    if not text:
        raise argparse.ArgumentTypeError("expected a date (YYYY-MM-DD) or ISO datetime")
    try:
        parsed = _dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = _dt.datetime.combine(_dt.date.fromisoformat(text), _dt.time())
        except ValueError:
            raise argparse.ArgumentTypeError(
                f"not a date or ISO datetime: {value!r} (try 2026-07-16 or 2026-07-16T09:30)"
            ) from None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="jcodemunch-mcp receipt — token-economy ledger from Claude Code transcripts.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Rolling window size in days back from now (default 30; use 0 for all-time). "
             "Ignored when --since or --until is given.",
    )
    parser.add_argument(
        "--since",
        type=parse_window_bound,
        default=None,
        metavar="DATE",
        help="Window start, inclusive. A date (2026-07-16) means local midnight. "
             "Use with --until for calendar windows (today, yesterday, this month).",
    )
    parser.add_argument(
        "--until",
        type=parse_window_bound,
        default=None,
        metavar="DATE",
        help="Window end, EXCLUSIVE — so --since 2026-07-16 --until 2026-07-17 is "
             "exactly that one day, and adjacent windows never double-count a call.",
    )
    parser.add_argument(
        "--by-day",
        action="store_true",
        help="Include a per-calendar-day savings series in the JSON export "
             "(--export FILE.json). One scan, one row per day with any calls.",
    )
    parser.add_argument(
        "--model",
        choices=sorted(_MODEL_PRICES_USD_PER_MTOK.keys()),
        default=_DEFAULT_MODEL,
        help="Model rate to apply for the dollar conversion (default opus).",
    )
    parser.add_argument(
        "--export",
        metavar="FILE.csv|FILE.json",
        help="Write raw per-tool data to a file instead of the human report.",
    )
    parser.add_argument(
        "--explain",
        action="store_true",
        help="Print the per-tool savings multiplier table + methodology, then exit.",
    )
    parser.add_argument(
        "--rates",
        action="store_true",
        help="Print the model input-price table as JSON, then exit. Scans nothing, so "
             "a consumer pricing its own token counts can read the rates from here "
             "instead of hardcoding a copy that silently drifts when pricing changes.",
    )
    parser.add_argument(
        "--projects-root",
        type=Path,
        action="append",
        default=None,
        metavar="DIR",
        help="Claude Code projects directory to scan. Repeatable — pass it once "
             "per profile. Overrides discovery: with no --projects-root, the "
             "default root, CLAUDE_CONFIG_DIR, and roots seen in earlier "
             "sessions are all scanned.",
    )
    parser.add_argument(
        "--roots",
        action="store_true",
        help="Print the transcript roots that would be scanned, then exit.",
    )
    args = parser.parse_args(argv)

    if args.explain:
        sys.stdout.write(render_explain())
        return 0

    if args.rates:
        sys.stdout.write(render_rates())
        return 0

    roots = _projects_roots(args.projects_root)

    if args.roots:
        sys.stdout.write(json.dumps({"roots": [str(r) for r in roots]}, indent=2) + "\n")
        return 0

    since, until = args.since, args.until
    explicit_window = since is not None or until is not None
    if since is not None and until is not None and until <= since:
        print("--until must be after --since", file=sys.stderr)
        return 2
    if not explicit_window and args.days > 0:
        since = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=args.days)

    calls = list(iter_calls_multi(roots, since=since, until=until))
    agg = aggregate(calls)
    meter = lifetime_meter()

    if args.export:
        target = Path(args.export)
        ext = target.suffix.lower()
        if ext == ".json":
            window = {
                "since": since.isoformat() if since else None,
                "until": until.isoformat() if until else None,
            }
            if not explicit_window:
                window["days"] = args.days
            target.write_text(
                render_json(
                    agg,
                    model=args.model,
                    meter=meter,
                    by_day=aggregate_by_day(calls, model=args.model) if args.by_day else None,
                    window=window,
                    roots=roots,
                ),
                encoding="utf-8",
            )
        elif ext == ".csv":
            target.write_text(render_csv(agg), encoding="utf-8")
        else:
            print(
                f"--export needs a .csv or .json filename, got {target}",
                file=sys.stderr,
            )
            return 2
        print(f"wrote {target}")
        return 0

    sys.stdout.write(
        render_text(
            agg,
            days=args.days,
            model=args.model,
            meter=meter,
            window_label=_window_label(since, until) if explicit_window else None,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
