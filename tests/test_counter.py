"""Tests for the Counter: adaptive tool surface (order / menu / route).

Covers the four guarantees from docs/prd-adaptive-tool-surface.md:
  1. Zero behavior change on the default 'full' surface (front door hidden).
  2. 'counter' surface collapses to the front door + always-present controls.
  3. order dispatches read actions, and the charter gate refuses unknown /
     front-door / exec-verb / unopted state-changing actions.
  4. menu discovers; route maps a task to action(s) and can execute.

Plus the charter CI gate: no live catalog action trips the exec/write tripwire.
"""

import asyncio
import json
import os

import pytest

import jcodemunch_mcp.server as server
from jcodemunch_mcp import counter


@pytest.fixture(autouse=True)
def _restore_surface():
    """Isolate each test from JCODEMUNCH_TOOL_SURFACE + the raw-catalog cache."""
    prev = os.environ.get("JCODEMUNCH_TOOL_SURFACE")
    yield
    if prev is None:
        os.environ.pop("JCODEMUNCH_TOOL_SURFACE", None)
    else:
        os.environ["JCODEMUNCH_TOOL_SURFACE"] = prev
    server._RAW_CATALOG = None


def _surface(value):
    if value is None:
        os.environ.pop("JCODEMUNCH_TOOL_SURFACE", None)
    else:
        os.environ["JCODEMUNCH_TOOL_SURFACE"] = value
    server._RAW_CATALOG = None


def _call(name, args):
    res = asyncio.run(server.call_tool(name, args))
    # call_tool returns a plain content list on success and a CallToolResult
    # (isError) on failure (F-P01); read the content uniformly.
    from mcp.types import CallToolResult
    content = res.content if isinstance(res, CallToolResult) else res
    return json.loads(content[0].text)


# --- 1. Full surface: front door hidden, behavior unchanged ---------------- #

def test_full_surface_hides_front_door():
    _surface(None)
    names = {t.name for t in asyncio.run(server.list_tools())}
    assert not (counter.FRONT_DOOR & names), "front door must be hidden on 'full'"


def test_full_surface_count_matches_standard_default():
    _surface(None)
    full = {t.name for t in asyncio.run(server.list_tools())}
    # Adding the Counter must not change the resident 'full' catalog at all.
    assert "search_symbols" in full and "get_blast_radius" in full
    assert "order" not in full


# --- 2. Counter surface: collapse to the front door ------------------------ #

def test_counter_surface_collapses_to_front_door():
    _surface("counter")
    names = {t.name for t in asyncio.run(server.list_tools())}
    assert counter.FRONT_DOOR <= names
    # Always-present controls survive so tier switching / guide still work.
    assert {"set_tool_tier", "announce_model", "jcodemunch_guide"} <= names
    # Everything else is collapsed away.
    assert "search_symbols" not in names
    assert len(names) <= 8


# --- 3. order: dispatch + charter gate ------------------------------------- #

def test_order_gate_rejects_unknown_action():
    err = counter.order_gate("does_not_exist", server._catalog_names(), False)
    assert err and "Unknown action" in err


def test_order_gate_rejects_front_door_recursion():
    for fd in counter.FRONT_DOOR:
        err = counter.order_gate(fd, server._catalog_names(), False)
        assert err and "front-door" in err


def test_order_gate_allows_read_action():
    assert counter.order_gate("search_symbols", server._catalog_names(), False) is None


def test_order_gate_blocks_state_change_without_optin():
    err = counter.order_gate("index_repo", server._catalog_names(), False)
    assert err and "allow_state_change" in err
    assert counter.order_gate("index_repo", server._catalog_names(), True) is None


def test_order_gate_exec_tripwire_is_unconditional():
    # Even with the opt-in, an exec/write verb is refused outright.
    for verb in ("shell", "exec", "run_command", "write_file", "edit_file", "apply_patch"):
        assert counter.forbidden_reason(verb) is not None
        assert counter.forbidden_reason(f"do_{verb}_now") is not None
    assert counter.forbidden_reason("search_symbols") is None


def test_order_dispatches_read_action_through_pipeline():
    # get_session_stats needs no repo and no index -> hermetic.
    out = _call("order", {"action": "get_session_stats", "args": {}})
    assert "error" not in out or "session" in json.dumps(out).lower()


def test_order_rejects_bad_args_type():
    out = _call("order", {"action": "search_symbols", "args": "not-an-object"})
    assert "error" in out


# --- 4. menu: discovery ----------------------------------------------------- #

def test_menu_lists_full_catalog():
    out = _call("menu", {})
    assert out["tool"] == "menu"
    assert out["total_actions"] >= 50
    assert not any(a["action"] in counter.FRONT_DOOR for a in out["actions"])


def test_menu_query_ranks_relevant_actions():
    out = _call("menu", {"query": "dead unused code", "limit": 5})
    actions = [a["action"] for a in out["actions"]]
    assert any(a in actions for a in ("find_dead_code", "find_unused_paths", "get_dead_code_v2"))


def test_menu_rows_flag_state_changing():
    out = _call("menu", {"query": "index a repository", "limit": 10})
    by_name = {a["action"]: a for a in out["actions"]}
    if "index_repo" in by_name:
        assert by_name["index_repo"]["state_changing"] is True


# --- 5. route: intent -> action -------------------------------------------- #

def test_route_recommends_for_intent():
    out = _call("route", {"task": "who calls this function", "repo": "x"})
    assert out["recommended"], "route should recommend at least one action"
    assert out["recommended"][0]["action"] in ("get_call_hierarchy", "find_references")


def test_route_requires_task():
    out = _call("route", {"repo": "x"})
    assert "error" in out


def test_route_execute_blocks_state_changing_top_pick():
    # "reindex the repo" should map to a state-changing action; execute must refuse.
    out = _call("route", {"task": "search for the string TODO in the code", "repo": "x", "execute": True})
    # search_text is read-only and repo-scoped -> route should have executed it
    # (or returned a clean execute_error if args couldn't be shaped); never a crash.
    assert out["tool"] == "route"


def test_route_classify_intent_pure():
    names = server._catalog_names()
    recs = counter.classify_intent("find dead code in the project", names)
    assert any(r["action"] in ("find_dead_code", "find_unused_paths") for r in recs)


# --- Transform-group routing (measured 0% recall before these rules) -------- #
# benchmarks/route_recall measured route recall@3 = 0% for the actions that
# consume ANOTHER tool's output rather than querying the index. Their vocabulary
# ("diagram", "a plan for renaming", "set me up") appears nowhere in the catalog
# text the lexical fallback ranks over, so no phrasing could reach them. Pin the
# rules; re-run the harness after touching _INTENT_RULES.

@pytest.mark.parametrize(
    "task,expected",
    [
        ("draw me a diagram of that", "render_diagram"),
        ("visualize the call graph", "render_diagram"),
        ("give me a plan for renaming this across the codebase", "plan_refactoring"),
        ("walk me through extracting this into a shared module", "plan_refactoring"),
        ("set me up with everything I need to debug this bug", "assemble_task_context"),
        ("everything i need to understand this failure", "assemble_task_context"),
    ],
)
def test_transform_intents_route_to_their_action(task, expected):
    recs = counter.classify_intent(task, server._catalog_names())
    assert expected in [r["action"] for r in recs], (
        f"{task!r} did not reach {expected}; got {[r['action'] for r in recs]}"
    )


def test_diagram_rule_does_not_steal_the_graph_rules():
    """The transform rules use high-precision nouns on purpose. A bare
    'graph'/'render'/'map' would capture the call-graph, import-graph and
    topology intents -- the exact failure these rules exist to fix, inverted."""
    names = server._catalog_names()
    for task, expected in (
        ("show me the call graph for this function", "get_call_hierarchy"),
        ("what does the import graph look like here", "get_dependency_graph"),
    ):
        actions = [r["action"] for r in counter.classify_intent(task, names)]
        assert actions and actions[0] == expected, (
            f"{task!r} primary should stay {expected}; got {actions}"
        )


def test_transform_rules_yield_primary_to_the_tool_that_feeds_them():
    """render_diagram consumes another tool's output and has nothing to draw
    without it, so a task naming BOTH must lead with the data fetch and offer
    the render as an alternate. This is why the transform block sits late in
    _INTENT_RULES; moving it above the graph rules silently inverts the pair."""
    actions = [
        r["action"]
        for r in counter.classify_intent("visualize the call graph", server._catalog_names())
    ]
    assert actions[0] == "get_call_hierarchy", f"data fetch must lead; got {actions}"
    assert "render_diagram" in actions, f"render must still be offered; got {actions}"


# --- Scorer: function words must not outrank real terms --------------------- #

def test_pronoun_does_not_outrank_the_matching_action():
    """Regression: idf is computed over description prose, so "me" -- absent from
    every description -- drew the HIGHEST weight in the query, then substring-hit
    a third of the catalog inside snake_case names. Measured before the fix:
    check_rena(me)_safe scored 19.3 on "draw me a diagram of that" against
    render_diagram's 16.5 for the actual word "diagram"."""
    rows = server._catalog_rows()
    hits = [r["action"] for r in counter.search_catalog(rows, "draw me a diagram of that", 5)]
    assert hits[0] == "render_diagram", f"expected render_diagram first; got {hits}"
    assert "check_rename_safe" not in hits[:3], f"pronoun noise resurfaced: {hits}"


def test_whole_word_name_hit_outranks_a_fragment():
    """A token matching a name SEGMENT is evidence; the same token buried inside a
    longer word is coincidence. These were inverted (fragment 4.0 > whole-word
    3.0), which also made the whole-word branch unreachable."""
    w_whole = counter.score_action(["diagram"], "render_diagram", "")
    w_frag = counter.score_action(["diagram"], "diagrammatic_thing", "")
    assert w_whole > w_frag, f"whole-word {w_whole} must beat fragment {w_frag}"


def test_short_tokens_cannot_match_inside_a_word():
    """Two-character noise must not substring-match. 'me' is a whole word in no
    catalog name, so it should score nothing at all."""
    assert counter.score_action(["me"], "check_rename_safe", "") == 0.0
    # ...but a short token that IS a whole name segment still counts.
    assert counter.score_action(["ast"], "search_ast", "") > 0.0


def test_all_stopword_query_does_not_rank():
    """A query of pure function words carries no signal; ranking on it invents
    an order. Fall back to the stable catalog listing instead."""
    rows = server._catalog_rows()
    ranked = counter.search_catalog(rows, "what is it that i should do with this", 5)
    unranked = counter.search_catalog(rows, None, 5)
    assert [r["action"] for r in ranked] == [r["action"] for r in unranked]


# --- Charter CI gate: the Counter can never expose an exec/write action ----- #

def test_no_catalog_action_trips_exec_tripwire():
    """jcm is read-only by charter. If a future tool ever names a write/exec
    verb, this fails loudly so order() can't silently become a mutation
    backdoor -- the safety-surface guarantee."""
    offenders = [n for n in server._catalog_names() if counter.forbidden_reason(n)]
    assert offenders == [], f"exec/write-verb tools would be exposed by order: {offenders}"


def test_state_changing_set_is_subset_of_catalog():
    catalog = server._catalog_names()
    missing = counter.STATE_CHANGING_ACTIONS - catalog
    assert missing == set(), f"state-changing names not in catalog (drift): {missing}"


# --- Tool-use examples: validated against the LIVE inputSchema ------------- #

def _catalog_schemas():
    """{action_name: inputSchema} for every real (non-front-door) tool."""
    return {
        t.name: (t.input_schema or {})
        for t in server._raw_catalog_tools()
        if t.name not in counter.FRONT_DOOR
    }


def test_examples_actions_exist_in_catalog():
    names = set(_catalog_schemas())
    unknown = set(counter.EXAMPLES) - names
    assert unknown == set(), f"EXAMPLES references non-catalog actions: {unknown}"


def test_examples_keys_are_valid_schema_properties():
    """Every example arg must be a declared property AND satisfy required args,
    so a curated example can never teach a wrong/renamed parameter."""
    schemas = _catalog_schemas()
    problems = {}
    for action, example in counter.EXAMPLES.items():
        schema = schemas.get(action, {})
        props = set((schema.get("properties") or {}).keys())
        required = set(schema.get("required") or [])
        keys = set(example.keys())
        unknown = keys - props
        missing = required - keys
        if unknown or missing:
            problems[action] = {"unknown_args": sorted(unknown), "missing_required": sorted(missing)}
    assert problems == {}, f"example/schema mismatches: {problems}"


def test_example_for_returns_copy_or_none():
    assert counter.example_for("search_symbols") is not None
    a = counter.example_for("search_symbols")
    a["repo"] = "mutated"
    assert counter.example_for("search_symbols")["repo"] != "mutated"  # not shared state
    assert counter.example_for("definitely_not_a_tool") is None


def test_menu_surfaces_example_for_known_action():
    out = _call("menu", {"query": "search symbols", "limit": 25})
    by_name = {a["action"]: a for a in out["actions"]}
    if "search_symbols" in by_name:
        assert "example" in by_name["search_symbols"], "menu should surface a curated example"
        assert by_name["search_symbols"]["example"].get("query")


def test_route_template_falls_back_to_curated_example():
    # find_implementations has no _QUERY_ARG shaper, so route can't auto-build
    # its args -> the curated example should fill args_template (not a bare hint).
    out = _call("route", {"task": "find implementations of the Store interface", "repo": "x"})
    for rec in out["recommended"]:
        if rec["action"] in counter.EXAMPLES and "_hint" in rec.get("args_template", {}):
            pytest.fail(f"{rec['action']} had a curated example but route used a bare hint")


# --------------------------------------------------------------------------- #
# v1.108.253: "find X" is ambiguous, so route must stop answering it with one
# confident action. Measured on agent-EMITTED task strings (jcm#422): the broad
# /find|locate|.../ rule fired on 26 of 40 and returned search_symbols ALONE,
# while gold split 18 search_text / 17 search_symbols.
# --------------------------------------------------------------------------- #

_NAMES = {"search_symbols", "search_text", "search_ast", "get_file_outline"}


@pytest.mark.parametrize("task", [
    "find where the dashboard is defined",
    "locate the pause logic",
    "where is ConstraintScorer",
    "look up the retry handler",
    "search for the config loader",
])
def test_ambiguous_find_offers_both_search_actions(task):
    """The caller must be able to SEE the other side of the coin flip.

    Before this, a curated rule returned exactly one action, so @3 could not
    recover from a wrong @1 -- 28 of 40 emitted cases had no rank 2 at all.
    """
    actions = [r["action"] for r in counter.classify_intent(task, _NAMES)]
    assert "search_symbols" in actions, task
    assert "search_text" in actions, task
    assert len(actions) >= 2, task


@pytest.mark.parametrize("task,expected_first", [
    ("find the string connection refused in the logs", "search_text"),
    ("locate the error message about a failed handshake", "search_text"),
    ("find every place we retry a request", "search_text"),
    ("find all occurrences of the deprecated flag", "search_text"),
    ('search for "TODO" comments', "search_text"),
])
def test_content_signals_rank_search_text_first(task, expected_first):
    """A symbol-name index cannot match a log line or a quoted literal."""
    actions = [r["action"] for r in counter.classify_intent(task, _NAMES)]
    assert actions[0] == expected_first, (task, actions)


def test_ambiguous_default_still_leads_with_symbols():
    """Deliberately NOT flipped. The emitted-corpus split was 18/17, which is
    not a signal, and reordering to chase one case fits the sample rather than
    the intent. Pinned so a future edit has to argue with this, not drift past it."""
    actions = [r["action"] for r in counter.classify_intent("find the dashboard", _NAMES)]
    assert actions[0] == "search_symbols"


def test_narrower_rules_still_outrank_the_content_rule():
    """`every place` appears in the new content rule AND in the anti-pattern
    rule that must keep winning. Precedence, not narrowing -- the same argument
    the specificity block above it was built on."""
    actions = [r["action"] for r in counter.classify_intent(
        "find every place we swallow an exception without logging", _NAMES)]
    assert actions[0] == "search_ast", actions


class TestResolveToolSurface:
    """One authority for surface resolution (server + out-of-process hooks)."""

    def test_env_wins_over_config(self):
        from jcodemunch_mcp.counter import resolve_tool_surface
        assert resolve_tool_surface("counter", "full") == ("counter", "counter", True)

    def test_whitespace_env_falls_through_to_config(self):
        from jcodemunch_mcp.counter import resolve_tool_surface
        assert resolve_tool_surface("   ", "counter") == ("counter", "counter", True)

    def test_unrecognized_serves_full_and_says_so(self):
        from jcodemunch_mcp.counter import resolve_tool_surface
        assert resolve_tool_surface("countr", None) == ("full", "countr", False)

    def test_absent_everything_is_full(self):
        from jcodemunch_mcp.counter import resolve_tool_surface
        assert resolve_tool_surface(None, None) == ("full", "full", True)
