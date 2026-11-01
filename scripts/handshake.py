"""Real stdio `initialize` against an INSTALLED jcodemunch-mcp.  `python scripts/handshake.py --expect-version X.Y.Z`

Exit 0 only when the wire carries `serverInfo.version == --expect-version`
and a non-empty `instructions` string. Prints the JSON it saw either way.

Why this is a separate probe (ENFORCEMENT-PLAN item 5, #536): `__version__`
is `"unknown"` under `PYTHONPATH=src`, so every test in this suite and every
CI leg runs from source and cannot see what the published artifact puts on
the wire. The 1.108.293 handshake was done by hand once and never since.
This script is meant to run from a FRESH venv holding only
`jcodemunch-mcp==X.Y.Z` from PyPI (see .github/workflows/release.yml post-publish, and pr-gate.yml stage 3); it
imports nothing from the repository and refuses if the server it spawns is
being served out of a source tree.

⚠ Never run this through bare `uvx`: it served a cached 1.108.275 once and
reproduced the pre-fix symptoms exactly (ISSUE-HISTORY 2026-08-24).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile


async def probe(command: str, timeout: float, fixture: str | None = None) -> dict:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = dict(os.environ)
    # A scratch index root: the server must not write into a runner's home
    # and a first-ever install here must not be mistaken for the maintainer's.
    env.setdefault("CODE_INDEX_PATH", tempfile.mkdtemp(prefix="jcm-handshake-"))
    env.setdefault("JCODEMUNCH_NO_VERSION_HINT", "1")
    params = StdioServerParameters(command=command, args=[], env=env)
    # The full surface, so the smoke test sees every tool the wheel ships;
    # the counter front door would hide all but six.
    env.setdefault("JCODEMUNCH_TOOL_SURFACE", "full")
    env.setdefault(
        "JCODEMUNCH_DEFAULT_FORMAT", "raw"
    )  # config key server_output; the probe parses JSON
    env.setdefault("JCODEMUNCH_TRUSTED_FOLDERS", fixture or "")
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await asyncio.wait_for(session.initialize(), timeout)
            tools = await asyncio.wait_for(session.list_tools(), timeout)
            fixture_result = None
            if fixture:
                fixture_result = await _exercise_fixture(session, fixture, timeout)
    return {
        "serverInfo": {
            "name": init.serverInfo.name,
            "version": init.serverInfo.version,
        },
        "instructions": init.instructions or "",
        "protocolVersion": init.protocolVersion,
        "tool_count": len(tools.tools),
        "fixture": fixture_result,
    }


def _leaves(e: BaseException) -> list[BaseException]:
    """Flatten an ExceptionGroup so the real error is printed, not 'unhandled errors in a TaskGroup'."""
    subs = getattr(e, "exceptions", None)
    if not subs:
        return [e]
    out: list[BaseException] = []
    for x in subs:
        out.extend(_leaves(x))
    return out


def _text(result) -> str:
    return "".join(getattr(c, "text", "") for c in result.content)


def _json(result, what: str) -> dict:
    txt = _text(result)
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        raise RuntimeError(
            f"{what} did not return JSON (JCODEMUNCH_DEFAULT_FORMAT=raw is set by the probe; this is another encoding): {txt[:300]!r}"
        )


async def _exercise_fixture(session, fixture: str, timeout: float) -> dict:
    """Index a small repo and make two real tool calls over the wire (DESIGN stage 3).

    A missing grammar wheel, a package-data file left out of the wheel, or a
    broken console-script entry point shows up HERE and in no unit test,
    because the tests run from source.
    """
    idx = await asyncio.wait_for(
        session.call_tool("index_folder", {"path": fixture, "use_ai_summaries": False}),
        timeout,
    )
    idx_json = _json(idx, "index_folder")
    repo = idx_json.get("repo")
    if not repo:
        raise RuntimeError(f"index_folder returned no repo id: {_text(idx)[:300]}")
    langs = idx_json.get("languages") or {}
    srch = await asyncio.wait_for(
        session.call_tool(
            "search_symbols",
            {
                "repo": repo,
                "query": "compute order total",
                "max_results": 5,
                "detail_level": "standard",
            },
        ),
        timeout,
    )
    first = _first_hit_id(_text(srch))
    if not first:
        raise RuntimeError(
            f"search_symbols found nothing in the fixture: {_text(srch)[:300]}"
        )
    src = await asyncio.wait_for(
        session.call_tool("get_symbol_source", {"repo": repo, "symbol_id": first}),
        timeout,
    )
    name = first.split("::")[-1].split("#")[0].split(".")[-1]
    if name not in _text(src):
        raise RuntimeError(
            f"get_symbol_source for {first} did not contain {name!r}: {_text(src)[:300]}"
        )
    return {
        "repo": repo,
        "file_count": idx_json.get("file_count"),
        "languages": sorted(langs),
        "first_hit": first,
    }


def _first_hit_id(txt: str) -> str | None:
    """First result id from either the JSON shape or the MUNCH table encoding.

    The wire format is the server's choice (`server_output`, default adaptive)
    and the smoke test must not depend on which one a fresh install picks.
    """
    try:
        rows = json.loads(txt).get("results") or []
        return rows[0].get("id") if rows else None
    except (json.JSONDecodeError, AttributeError):
        pass
    if txt.startswith("#MUNCH/"):
        for line in txt.splitlines():
            if line.startswith("s,"):
                return line.split(",")[1] or None
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--expect-version", required=True)
    ap.add_argument(
        "--command",
        default="jcodemunch-mcp",
        help="server executable on PATH (the installed console script)",
    )
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument(
        "--fixture",
        help="directory to index and query over stdio (the packaging smoke test); must be absolute",
    )
    ap.add_argument(
        "--expect-languages",
        default="",
        help="comma-separated languages the fixture must index (e.g. python,typescript,go)",
    )
    a = ap.parse_args(argv)

    exe = shutil.which(a.command)
    if not exe:
        print(
            f"FAIL: {a.command!r} not on PATH; install jcodemunch-mcp=={a.expect_version} into this venv first"
        )
        return 1
    print(f"server: {exe}")

    try:
        seen = asyncio.run(
            probe(exe, a.timeout, os.path.abspath(a.fixture) if a.fixture else None)
        )
    except (
        BaseException
    ) as e:  # a hung or crashed handshake is the defect this exists to see
        print(f"FAIL: handshake did not complete: {type(e).__name__}: {e}")
        for sub in _leaves(e):
            print(f"      {type(sub).__name__}: {sub}")
        return 1
    print(
        json.dumps(
            {
                **seen,
                "instructions": seen["instructions"][:200]
                + ("..." if len(seen["instructions"]) > 200 else ""),
            },
            indent=2,
        )
    )

    ok = True
    if seen["serverInfo"]["name"] != "jcodemunch-mcp":
        print(f"FAIL: serverInfo.name is {seen['serverInfo']['name']!r}")
        ok = False
    if seen["serverInfo"]["version"] != a.expect_version:
        # The SDK's own version here (e.g. 1.26.0) means `Server(..., version=)` was dropped.
        print(
            f"FAIL: serverInfo.version is {seen['serverInfo']['version']!r}, expected {a.expect_version!r}"
        )
        ok = False
    if not seen["instructions"].strip():
        print(
            "FAIL: instructions is empty (the only prose that survives tool deferral)"
        )
        ok = False
    if seen["tool_count"] == 0:
        print("FAIL: list_tools returned nothing")
        ok = False
    if a.fixture:
        fx = seen.get("fixture") or {}
        want = {x.strip().lower() for x in a.expect_languages.split(",") if x.strip()}
        have = {x.lower() for x in fx.get("languages", [])}
        missing = want - have
        if missing:
            print(
                f"FAIL: fixture indexed languages {sorted(have)}, missing {sorted(missing)} (a grammar the wheel did not ship?)"
            )
            ok = False
    print("HANDSHAKE", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    # A Windows console or redirect is cp1252; repo text is not (L-145).
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
