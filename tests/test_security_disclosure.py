"""SECURITY.md's enumerations are checked against the code they describe (#449).

The "Background behavior, fully disclosed" section opens with **"Everything
jCodeMunch does beyond answering a tool call is listed here."** Completeness is the
promise, so an enumeration that silently falls behind the code breaks the one
guarantee that section exists to give.

⚠⚠ **This is the second time that exact failure mode has shipped**, which is why it
gets a test rather than another careful edit:

* v1.108.261 — `SECURITY.md` said the telemetry ping sends "**only** an integer delta
  plus an anonymous UUID" while the payload also carried a lifetime `total`.
* v1.108.274 (#449, @elfrost) — it said `POST /org/report` "is the only route in
  jCodeMunch that accepts writes from another computer" while `make_runtime_routes()`
  had added three more.

Both sentences were true when written and were not revisited as the code grew. Prose
cannot notice that happening; a test can. These assertions are deliberately about
**enumerations and defaults** — the claims that rot — and not about wording, so
editing the document freely stays cheap.
"""

import re
from pathlib import Path

import pytest

SECURITY_MD = Path(__file__).resolve().parents[1] / "SECURITY.md"


@pytest.fixture(scope="module")
def security_text() -> str:
    return SECURITY_MD.read_text(encoding="utf-8")


def _declared_post_routes() -> set[str]:
    """Every remote-write route the server actually mounts.

    Read from the route builders rather than hardcoded, so a route added later is
    covered by construction — the same reason the #435 sweep enumerates profiles
    instead of listing them.
    """
    pytest.importorskip("starlette", reason="route builders need the [http] extra")
    from jcodemunch_mcp.org.http_routes import make_org_routes
    from jcodemunch_mcp.runtime.http_routes import make_runtime_routes

    paths: set[str] = set()
    for route in list(make_org_routes()) + list(make_runtime_routes()):
        if "POST" in (getattr(route, "methods", None) or set()):
            paths.add(route.path)
    return paths


def test_every_remote_write_route_is_disclosed(security_text):
    """Each POST route the server mounts must appear in SECURITY.md by path."""
    routes = _declared_post_routes()
    assert routes, "no POST routes found — the builders changed shape, fix this test"
    missing = sorted(r for r in routes if r not in security_text)
    assert not missing, (
        "SECURITY.md does not disclose these remote-write routes: "
        f"{missing}. The 'Background behavior' section promises the enumeration is "
        "complete; add them there rather than deleting this assertion."
    )


def test_the_disclosed_route_count_matches_reality(security_text):
    """The prose states a count. Pin it, because that is the part that went stale.

    Naming each route is not sufficient on its own — the previous defect was a
    sentence claiming exclusivity ('the only route') while the paths were listed
    elsewhere in the file.
    """
    routes = _declared_post_routes()
    m = re.search(
        r"has \*\*(two|three|four|five|six)\*\* routes that accept writes",
        security_text,
    )
    assert m, (
        "SECURITY.md no longer states how many remote-write routes exist. That "
        "sentence is the disclosure; if it moved, update this test to find it."
    )
    words = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
    assert words[m.group(1)] == len(routes), (
        f"SECURITY.md says {m.group(1)} remote-write routes; the server mounts "
        f"{len(routes)}: {sorted(routes)}"
    )


def test_no_route_is_described_as_the_only_one(security_text):
    """The exact phrasing that went stale, refused by name.

    A softer version of this test would pass the moment someone re-introduced the
    claim about a different route.
    """
    assert "only route in jCodeMunch that accepts writes" not in security_text, (
        "the 'only route that accepts writes' claim is back; there are several"
    )


def test_response_redaction_exemptions_match_the_code(security_text):
    """#448. The three exempt tools are a documented gap, so the list must be true.

    Read from the module rather than restated here, so this fails if the exemption
    set changes without the document following.
    """
    src = (
        SECURITY_MD.parent / "src" / "jcodemunch_mcp" / "server.py"
    ).read_text(encoding="utf-8", errors="replace")
    block = re.search(
        r"_SOURCE_DUMP_TOOLS = frozenset\(\{(.*?)\}\)", src, re.DOTALL
    )
    assert block, "_SOURCE_DUMP_TOOLS changed shape; fix this test, not the doc"
    exempt = set(re.findall(r'"([a-z_]+)"', block.group(1)))
    assert exempt, "parsed no tool names out of _SOURCE_DUMP_TOOLS"

    missing = sorted(t for t in exempt if t not in security_text)
    assert not missing, (
        f"SECURITY.md does not name these redaction-exempt tools: {missing}"
    )


def test_response_redaction_default_and_off_switch_are_stated(security_text):
    """An on-by-default control is as much a disclosure obligation as a gap.

    The reader this document is written for cannot attest to what it does not say.
    """
    assert "JCODEMUNCH_REDACT_RESPONSE_SECRETS" in security_text
    assert "Response-Level Secret Redaction" in security_text


def test_runtime_ingest_defaults_off_in_both_doc_and_code(security_text):
    """The gate names are the load-bearing half of 'off by default'."""
    assert "runtime_ingest_enabled" in security_text
    assert "org_ingest_enabled" in security_text

    from jcodemunch_mcp import config as _config

    for key in ("runtime_ingest_enabled", "org_ingest_enabled"):
        assert _config.get(key, False, repo=None) is False, (
            f"{key} is not defaulting to False; SECURITY.md says it does"
        )
