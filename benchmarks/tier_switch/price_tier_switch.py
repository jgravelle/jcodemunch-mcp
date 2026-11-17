"""Price a mid-session tool-list change against the cache it invalidates.

Run:  PYTHONPATH=src python benchmarks/tier_switch/price_tier_switch.py [--json]

⚠ Schema weights are READ LIVE from the built tool list, never hardcoded, so
this cannot report a number the shipped surface no longer produces. The rate
multipliers are PUBLISHED (Anthropic prompt caching), not measured here.
"""
from __future__ import annotations

import json
import sys

from jcodemunch_mcp import server as s
from jcodemunch_mcp.tier_switch_cost import (
    CACHE_READ_MULTIPLIER, CACHE_WRITE_MULTIPLIER, breakeven_requests, classify,
)

TIERS = ("core", "standard", "full")
PAIRS = (("full", "core"), ("full", "standard"), ("standard", "core"),
         ("core", "full"), ("standard", "full"))
HISTORIES = (0, 10_000, 25_000, 50_000, 100_000)


def measure() -> dict:
    weights = {t: s._schema_tokens_for_profile(t) for t in TIERS}
    counts = {t: len(s._build_tools_list(profile_override=t)) for t in TIERS}

    switches = []
    for src, dst in PAIRS:
        verdict, be = classify(weights[src], weights[dst])
        row = {
            "from": src, "to": dst, "verdict": verdict,
            "one_time_write": round((weights[dst]) * CACHE_WRITE_MULTIPLIER, 1),
            "saved_per_request": round((weights[src] - weights[dst]) * CACHE_READ_MULTIPLIER, 1),
            "breakeven_requests": None if be is None else round(be, 1),
            "breakeven_by_history": {
                str(h): (lambda v: None if v is None else round(v, 1))(
                    breakeven_requests(weights[src], weights[dst], history_tokens=h)
                ) for h in HISTORIES
            },
        }
        switches.append(row)

    return {
        "estimator": "bytes/4",
        "rates": {"cache_read": CACHE_READ_MULTIPLIER,
                  "cache_write_5min": CACHE_WRITE_MULTIPLIER,
                  "cache_write_1hour": 2.0,
                  "basis": "multiples of base input price; PUBLISHED, not measured here"},
        "tiers": {t: {"tools": counts[t], "schema_tokens": weights[t]} for t in TIERS},
        "switches": switches,
    }


def main() -> int:
    data = measure()
    if "--json" in sys.argv:
        print(json.dumps(data, indent=2))
        return 0
    print("tier          tools   schema tokens")
    for t, v in data["tiers"].items():
        print(f"  {t:<10}{v['tools']:>6}{v['schema_tokens']:>16,}")
    print(f"\n{'switch':<20}{'one-time':>10}{'saved/req':>11}{'break-even':>13}  verdict")
    for r in data["switches"]:
        be = "never" if r["breakeven_requests"] is None else f"{r['breakeven_requests']:,.0f} reqs"
        print(f"{r['from']+' -> '+r['to']:<20}{r['one_time_write']:>10,.0f}"
              f"{r['saved_per_request']:>11,.0f}{be:>13}  {r['verdict']}")
    print("\nbreak-even including the invalidated conversation history:")
    print(f"{'history':>9}" + "".join(f"{r['from']+'->'+r['to']:>20}" for r in data["switches"]
                                      if r["breakeven_requests"] is not None))
    paying = [r for r in data["switches"] if r["breakeven_requests"] is not None]
    for h in HISTORIES:
        print(f"{h:>9,}" + "".join(f"{r['breakeven_by_history'][str(h)]:>16,.0f} req"
                                   for r in paying))
    return 0


if __name__ == "__main__":
    # Windows: a redirected stdout is cp1252; write UTF-8 (L-145, L-320).
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
