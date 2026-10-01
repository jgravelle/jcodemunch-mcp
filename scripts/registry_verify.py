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


def fetch(name: str) -> list[dict]:
    url = f"{API}?search={urllib.request.quote(name)}&limit=100"
    with urllib.request.urlopen(url, timeout=30) as r:  # noqa: S310
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
    return rows


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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True)
    ap.add_argument("--name", default="io.github.jgravelle/jcodemunch-mcp")
    ap.add_argument("--attempts", type=int, default=6)
    ap.add_argument(
        "--delay", type=float, default=20.0, help="seconds between attempts"
    )
    a = ap.parse_args(argv)
    attempts = max(a.attempts, 1)
    answered: list[str] = []
    for attempt in range(1, attempts + 1):
        if attempt > 1:
            time.sleep(a.delay)
        try:
            rows = fetch(a.name)
        except (OSError, http.client.HTTPException, ValueError) as exc:
            # URLError, HTTPError and a socket timeout are OSError; a cut or
            # malformed response (IncompleteRead, BadStatusLine) is
            # HTTPException and NOT OSError; a body that is not a JSON
            # object carrying a row list is ValueError. None of them is an
            # answer.
            print(
                f"attempt {attempt}: no answer ({type(exc).__name__}: {exc})",
                flush=True,
            )
            continue
        ok, answered = verdict(rows, a.name, a.version)
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
        f"UNREADABLE: the registry gave no answer in {attempts} attempt(s). "
        "This is not evidence about the publish; read the registry again before any re-publish."
    )
    return UNREADABLE


if __name__ == "__main__":
    sys.exit(main())
