"""§7 schema-budget regression guardrail.

Parses tools/list output at every profile x compact_schemas combination,
counts tokens with tiktoken cl100k_base, and fails if any count drifts
more than 5% above the frozen baseline in benchmarks/schema_baseline.json.

This is the load-bearing CI check that prevents schema bloat from sneaking
in via new tool additions or description expansions. If you legitimately
need the schema to grow (e.g. documented v2.1 change), update the baseline
in the same PR — the CI diff will make the change reviewable.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE = REPO_ROOT / "benchmarks" / "schema_baseline.json"
DRIFT_TOLERANCE = 0.05  # 5%

try:
    import tiktoken  # noqa: F401
    _HAS_TIKTOKEN = True
except ImportError:
    _HAS_TIKTOKEN = False


@pytest.mark.skipif(not _HAS_TIKTOKEN, reason="tiktoken not installed")
@pytest.mark.skipif(not BASELINE.is_file(), reason="benchmarks/schema_baseline.json missing — run benchmarks/harness/capture_schema_baseline.py")
def test_schema_tokens_within_baseline_tolerance():
    """tools/list token count must stay within 5% of baseline for every
    profile x compact_schemas combo. Catches accidental bloat from new
    tools, expanded descriptions, or schema regressions."""
    import tiktoken as _tk

    from jcodemunch_mcp import config as config_module
    from jcodemunch_mcp.server import _build_tools_list

    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    encoding = _tk.get_encoding("cl100k_base")

    cfg = config_module._GLOBAL_CONFIG  # type: ignore[attr-defined]
    original = {k: cfg.get(k) for k in ("tool_profile", "compact_schemas", "tool_surface")}
    drifts: list[str] = []
    try:
        for profile in ("core", "standard", "full"):
            for compact in (True, False):
                cfg["tool_surface"] = "full"
                cfg["tool_profile"] = profile
                cfg["compact_schemas"] = compact
                tools = _build_tools_list()
                payload = [
                    {"name": t.name, "description": t.description, "inputSchema": t.inputSchema}
                    for t in tools
                ]
                text = json.dumps(payload, separators=(",", ":"))
                count = len(encoding.encode(text))

                key = f"{profile}_{'compact' if compact else 'full'}"
                base = baseline.get(key)
                if base is None:
                    drifts.append(f"{key}: no baseline entry (add {count} to schema_baseline.json)")
                    continue
                ceiling = int(base * (1 + DRIFT_TOLERANCE))
                if count > ceiling:
                    drifts.append(
                        f"{key}: {count} tokens > {ceiling} ceiling "
                        f"(baseline {base}, +{(count - base) / base:.1%})"
                    )
    finally:
        for k, v in original.items():
            if v is None:
                cfg.pop(k, None)
            else:
                cfg[k] = v

    assert not drifts, (
        "Schema token budget exceeded. Either shrink the change or update "
        "benchmarks/schema_baseline.json in the same PR with justification. "
        f"Drifts: {drifts}"
    )


@pytest.mark.skipif(not _HAS_TIKTOKEN, reason="tiktoken not installed")
@pytest.mark.skipif(not BASELINE.is_file(), reason="benchmarks/schema_baseline.json missing — run benchmarks/harness/capture_schema_baseline.py")
def test_counter_surface_within_baseline_tolerance():
    """`tool_surface: "counter"` is the largest lever this project has, and it
    sat outside the guardrail until 2026-08-14 — the loop above only walks
    tool_profile, which does not apply to the front door at all.

    ⚠ The cost of that gap was not drift, it was a published number nothing
    checked: `run_route_recall.py` claimed the Counter avoided ~98% of resident
    schema tokens for two months. Measured, it is lower. A saving quoted in a
    docstring with no test under it is a transcription, and this is the test.
    """
    import tiktoken as _tk

    from jcodemunch_mcp import config as config_module
    from jcodemunch_mcp.server import _build_tools_list

    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    encoding = _tk.get_encoding("cl100k_base")

    cfg = config_module._GLOBAL_CONFIG  # type: ignore[attr-defined]
    original = {k: cfg.get(k) for k in ("tool_profile", "compact_schemas", "tool_surface")}
    drifts: list[str] = []
    try:
        for compact in (True, False):
            cfg["tool_surface"] = "counter"
            cfg["compact_schemas"] = compact
            tools = _build_tools_list()
            payload = [
                {"name": t.name, "description": t.description, "inputSchema": t.inputSchema}
                for t in tools
            ]
            count = len(encoding.encode(json.dumps(payload, separators=(",", ":"))))

            key = f"counter_{'compact' if compact else 'full'}"
            base = baseline.get(key)
            if base is None:
                drifts.append(f"{key}: no baseline entry (add {count} to schema_baseline.json)")
                continue
            ceiling = int(base * (1 + DRIFT_TOLERANCE))
            if count > ceiling:
                drifts.append(
                    f"{key}: {count} tokens > {ceiling} ceiling "
                    f"(baseline {base}, +{(count - base) / base:.1%})"
                )
    finally:
        for k, v in original.items():
            if v is None:
                cfg.pop(k, None)
            else:
                cfg[k] = v

    assert not drifts, (
        "Counter front-door token budget exceeded. The front door is six tools; "
        "if it grew, that is a product decision that needs saying out loud, not a "
        f"baseline regeneration. Drifts: {drifts}"
    )


@pytest.mark.skipif(not BASELINE.is_file(), reason="benchmarks/schema_baseline.json missing")
def test_counter_saving_is_read_not_typed():
    """`run_route_recall.py` must compute the Counter's saving from the baseline.

    ⚠⚠ It used to assert `~98%` in its own docstring, which is why this test
    exists in the negative: it fails if any hardcoded percentage returns to that
    file. Maintenance practice #4 — never hand-type a benchmark number — and the
    same failure shape as the four-month `jcm_reference.json` drift.
    """
    import re

    harness = REPO_ROOT / "benchmarks" / "route_recall" / "run_route_recall.py"
    text = harness.read_text(encoding="utf-8")

    assert "_schema_saving" in text, (
        "run_route_recall.py must read the saving from schema_baseline.json via "
        "_schema_saving(); reinstating a literal is the defect this guards."
    )
    # A percentage adjacent to schema/token/resident wording is the transcription
    # shape. Recall percentages (recall@3, per-group output) are formatted at
    # runtime, so they never appear as literals in the source.
    offenders = [
        m.group(0)
        for m in re.finditer(r"~?\d{1,3}(?:\.\d+)?%[^\n]{0,60}", text)
        if re.search(r"schema|resident|token", m.group(0), re.IGNORECASE)
    ]
    assert not offenders, (
        f"Hardcoded schema-saving percentage back in run_route_recall.py: {offenders}. "
        "Read it from benchmarks/schema_baseline.json instead."
    )


@pytest.mark.skipif(not BASELINE.is_file(), reason="benchmarks/schema_baseline.json missing")
def test_v2_success_criterion_core_compact_under_4000():
    """§10 success criterion: core + compact_schemas stays under 4,000 tokens."""
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    core_compact = baseline.get("core_compact")
    assert core_compact is not None, "baseline missing core_compact"
    assert core_compact <= 4000, (
        f"v2.0.0 success criterion requires core + compact_schemas <= 4000 tokens; "
        f"current baseline is {core_compact}."
    )


@pytest.mark.skipif(not _HAS_TIKTOKEN, reason="tiktoken not installed")
def test_live_core_compact_under_4000_hard_ceiling():
    """§10 criterion enforced on the LIVE build, not just the frozen baseline.

    The sibling test above reads benchmarks/schema_baseline.json, so a description
    or param breach (e.g. v1.108.70's 3956->4011) only fails AFTER the baseline is
    regenerated — by which point the breach has shipped. This recomputes core +
    compact_schemas straight from _build_tools_list() and fails the moment it
    exceeds 4000, so a breach can't reach a release (F-Q03 / F-A02).
    """
    import tiktoken as _tk

    from jcodemunch_mcp import config as config_module
    from jcodemunch_mcp.server import _build_tools_list

    encoding = _tk.get_encoding("cl100k_base")
    cfg = config_module._GLOBAL_CONFIG  # type: ignore[attr-defined]
    original = {k: cfg.get(k) for k in ("tool_profile", "compact_schemas")}
    try:
        cfg["tool_profile"] = "core"
        cfg["compact_schemas"] = True
        tools = _build_tools_list()
        payload = [
            {"name": t.name, "description": t.description, "inputSchema": t.inputSchema}
            for t in tools
        ]
        count = len(encoding.encode(json.dumps(payload, separators=(",", ":"))))
    finally:
        for k, v in original.items():
            if v is None:
                cfg.pop(k, None)
            else:
                cfg[k] = v

    assert count <= 4000, (
        f"LIVE core_compact is {count} tokens, over the §10 <=4000 ceiling. Trim a "
        f"core-tier tool description, or strip/demote a param under compact "
        f"(_COMPACT_STRIP_PARAMS / _COMPACT_DEMOTE_ENUM_PARAMS). Do NOT just "
        f"regenerate benchmarks/schema_baseline.json to paper over it."
    )


def test_compact_demotes_language_enum_keeps_capability():
    """Under compact_schemas, the ~76-value `language` enum is demoted to a
    plain string filter (reclaims ~200 tokens) but the param stays usable. The
    full surface keeps the enum. Guards the core_compact-under-4000 headroom."""
    from jcodemunch_mcp import config as config_module
    from jcodemunch_mcp.server import _build_tools_list

    cfg = config_module._GLOBAL_CONFIG  # type: ignore[attr-defined]
    original = {k: cfg.get(k) for k in ("tool_profile", "compact_schemas")}
    try:
        cfg["tool_profile"] = "core"

        cfg["compact_schemas"] = True
        ss = next(t for t in _build_tools_list() if t.name == "search_symbols")
        lang = ss.inputSchema["properties"]["language"]
        assert "enum" not in lang, "language enum must be demoted under compact"
        assert lang.get("type") == "string", "demoted language must remain a string filter"
        assert lang.get("description"), "demoted language must keep its description"

        cfg["compact_schemas"] = False
        ss_full = next(t for t in _build_tools_list() if t.name == "search_symbols")
        assert len(ss_full.inputSchema["properties"]["language"].get("enum", [])) > 10, (
            "full surface must keep the language enum"
        )
    finally:
        for k, v in original.items():
            if v is None:
                cfg.pop(k, None)
            else:
                cfg[k] = v
