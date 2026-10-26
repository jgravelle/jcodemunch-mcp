"""The counter-surface instructions say what the index adds when an issue names the code.

A bug report usually names the function it is about, and the instructions
already say that a known name is often as cheap to find with Grep (an owner
ruling of 2026-10-14). They said nothing about what comes after finding it: who
calls it and what an edit to it reaches. The `counter` surface is what a fresh
`init` serves, and there the model sees three front-door tools and this string.

The line names two `route` phrasings. This file checks that `route`'s intent
rules still map each phrasing to the action the line promises, so the promise
cannot go stale when a rule changes. `full`'s text is at its budget and is not
changed.
"""

from __future__ import annotations

import re

import pytest

from jcodemunch_mcp import counter, server

PROMISES = {
    "who calls <name>": "get_call_hierarchy",
    "what breaks if I change <name>": "get_blast_radius",
}


@pytest.fixture(scope="module")
def counter_text():
    return server._mcp_instructions(surface="counter", indexed_files=1)


def test_the_counter_instructions_cover_named_code(counter_text):
    assert "names the code" in counter_text or "named code" in counter_text
    for phrasing in PROMISES:
        assert 'route("%s")' % phrasing in counter_text, phrasing


def test_the_known_name_ruling_still_stands(counter_text):
    """The new line complements the 2026-10-14 wording; it does not replace it."""
    assert "a known name or a file you are editing, built-in Grep and Read are often as cheap" in counter_text


@pytest.mark.parametrize("phrasing,action", sorted(PROMISES.items()))
def test_route_maps_each_quoted_phrasing_to_the_promised_action(phrasing, action):
    task = phrasing.replace("<name>", "parse_config")
    recs = counter.classify_intent(task, server._catalog_names())
    assert recs and recs[0]["action"] == action, (task, recs)


def test_every_route_phrasing_in_the_text_is_checked(counter_text):
    """Non-vacuity: a phrasing added to the text without a promise here fails."""
    quoted = set(re.findall(r'route\("([^"]+)"\)', counter_text))
    assert quoted == set(PROMISES), quoted
