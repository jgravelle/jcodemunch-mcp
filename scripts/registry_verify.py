"""Verify the MCP registry serves a version (CLAUDE.md "Registry verification reads a NESTED row").

`python scripts/registry_verify.py --version X.Y.Z [--name io.github.jgravelle/jcodemunch-mcp]`

Rows come back as `{server: {...}, _meta: {...}}` (schema 2025-12-11): `name`,
`version` and `packages[]` sit under `server`, `isLatest` under
`_meta["io.modelcontextprotocol.registry/official"]`. A flat `row["name"]`
read returned ZERO rows on a publish that had completely succeeded, and it
survives `&limit=100`. Never re-publish on a zero-row read; fix the parse.
Exit 1 unless a row with `server.version == X` exists, is marked latest, and
its `packages[].version` advanced too.

The read is retried, and the exit code says which thing went wrong (LEDGER
L-96). 1: the registry ANSWERED and the row is wrong. `UNREADABLE` (75, the
sysexits temporary-failure code): no attempt got an answer, which says nothing
about the publish. Not 2: argparse and a missing script file both exit 2, and
`release.yml` titles an issue from this code. One read with no
retry timed out after the 1.108.321 publish had succeeded, and `release.yml`
opened "registry publish failed" over it.
"""

from __future__ import annotations

import argparse
import http.client
import json
import sys
import time
import urllib.error
import urllib.request

API = "https://registry.modelcontextprotocol.io/v0/servers"
UNREADABLE = 75


MAX_PAGES = 50
REQUEST_SECONDS = 30.0
# The whole run, every attempt and every page. `release.yml`'s registry job is
# cancelled at its `timeout-minutes`, and a cancelled job opens no issue at
# all, so the script must finish, and say what it found, before that.
BUDGET_SECONDS = 600.0


def _page(url: str, timeout: float = REQUEST_SECONDS) -> tuple[list[dict], str | None]:
    with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310
        data = json.load(r)
    if not isinstance(data, dict):
        # `null` or a list is a proxy's or an error page's body, not the registry's answer.
        raise ValueError(f"body is {type(data).__name__}, not an object")
    key = next((k for k in ("servers", "items") if k in data), None)
    rows = data.get(key) if key else None
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        # An error object served with a 200 (`{"error": ...}`) has no row
        # list. Read as zero rows it would be a FAIL about the publish.
        raise ValueError(f"no row list in the body (keys: {sorted(data)[:5]})")
    meta = data.get("metadata")
    if meta is None:
        return rows, None
    if not isinstance(meta, dict):
        raise ValueError(f"metadata is {type(meta).__name__}, not an object")
    cursor = meta.get("nextCursor")
    if cursor is None:
        return rows, None
    if not isinstance(cursor, str) or not cursor:
        # Read as "last page", a cursor in a shape this script does not know
        # would end the read early and the partial read would be judged.
        raise ValueError(f"nextCursor is {cursor!r}, not a non-empty string")
    return rows, cursor


def fetch(name: str, deadline: float | None = None) -> list[dict]:
    """Every page (LEDGER L-99).

    Rows come back `limit` a page, ordered by version STRING (the live
    read ends on `1.8.6`), and `metadata.nextCursor` names the next page.
    Which page holds the latest row is therefore not knowable from here:
    one page of 100 held all 66 rows on 2026-10-01 and would have dropped
    rows at 101. A page that fails fails the whole read; half a read is
    never judged.
    """
    base = f"{API}?search={urllib.request.quote(name)}&limit=100"
    rows: list[dict] = []
    seen: set[str] = set()
    cursor = None
    for _ in range(MAX_PAGES):
        url = (
            base
            if cursor is None
            else f"{base}&cursor={urllib.request.quote(cursor, safe='')}"
        )
        timeout = REQUEST_SECONDS
        if deadline is not None:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError("the time budget ran out before the last page")
            timeout = min(REQUEST_SECONDS, left)
        page, cursor = _page(url, timeout)
        rows.extend(page)
        if cursor is None:
            return rows
        if cursor in seen:
            raise ValueError(f"the cursor did not advance ({cursor!r})")
        seen.add(cursor)
    raise ValueError(f"more than {MAX_PAGES} pages")


def verdict(rows: list[dict], name: str, version: str) -> tuple[bool, list[str]]:
    lines = [f"{len(rows)} row(s) for {name!r}"]
    hits = [r for r in rows if (r.get("server") or {}).get("name") == name]
    if not hits:
        return False, lines + [
            "FAIL: no row whose server.name matches (nested read; zero rows means the parse or the name, not the publish)"
        ]
    latest = [
        r
        for r in hits
        if (r.get("_meta") or {})
        .get("io.modelcontextprotocol.registry/official", {})
        .get("isLatest")
    ]
    if not latest:
        return False, lines + [f"FAIL: {len(hits)} rows, none marked isLatest"]
    srv = latest[0]["server"]
    lines.append(
        f"latest: server.version={srv.get('version')} packages={[p.get('version') for p in srv.get('packages') or []]}"
    )
    if srv.get("version") != version:
        return False, lines + [
            f"FAIL: latest server.version is {srv.get('version')!r}, expected {version!r}"
        ]
    pk = [p.get("version") for p in srv.get("packages") or []]
    if pk and any(v != version for v in pk):
        return False, lines + [
            f"FAIL: packages[].version {pk} did not advance to {version}"
        ]
    return True, lines + ["PASS"]


def _judge(rows: list[dict], name: str, version: str) -> tuple[bool, list[str]]:
    """`verdict`, with a row it cannot read turned into no answer (LEDGER L-98).

    Only `verdict` is wrapped. An AttributeError or TypeError anywhere else is
    a bug in this script and must raise with its traceback, not be retried six
    times and reported as a registry that could not be read.
    """
    try:
        return verdict(rows, name, version)
    except (AttributeError, TypeError) as exc:
        raise ValueError(f"malformed row ({type(exc).__name__}: {exc})") from exc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True)
    ap.add_argument("--name", default="io.github.jgravelle/jcodemunch-mcp")
    ap.add_argument("--attempts", type=int, default=6)
    ap.add_argument(
        "--delay", type=float, default=20.0, help="seconds between attempts"
    )
    ap.add_argument(
        "--budget",
        type=float,
        default=BUDGET_SECONDS,
        help="seconds for the whole run, every attempt and every page",
    )
    a = ap.parse_args(argv)
    attempts = max(a.attempts, 1)
    deadline = time.monotonic() + a.budget
    answered: list[str] = []
    made = 0
    for attempt in range(1, attempts + 1):
        if attempt > 1:
            if time.monotonic() + a.delay >= deadline:
                print(f"the {a.budget:g} s budget is spent after {made} attempt(s)")
                break
            time.sleep(a.delay)
        made = attempt
        try:
            rows = fetch(a.name, deadline)
            ok, lines = _judge(rows, a.name, a.version)
        except (OSError, http.client.HTTPException, ValueError) as exc:
            # URLError, HTTPError and a socket timeout are OSError; a cut or
            # malformed response (IncompleteRead, BadStatusLine) is
            # HTTPException and NOT OSError; a body, a cursor or a row that
            # is not the registry's shape is ValueError. None of them is an
            # answer.
            print(
                f"attempt {attempt}: no answer ({type(exc).__name__}: {exc})",
                flush=True,
            )
            continue
        answered = lines
        if ok:
            print("\n".join(answered))
            return 0
        # A wrong row is asked again too: the registry can serve the old
        # version for a moment after a publish.
        print(f"attempt {attempt}: {answered[-1]}")
    if answered:
        print("\n".join(answered))
        return 1
    print(
        f"UNREADABLE: the registry gave no answer in {made} attempt(s). "
        "This is not evidence about the publish; read the registry again before any re-publish. "
        "The same cause on every attempt that is not a timeout or a connection error "
        "means the registry's shape changed or this script is wrong, and waiting will not fix it."
    )
    return UNREADABLE


if __name__ == "__main__":
    sys.exit(main())
