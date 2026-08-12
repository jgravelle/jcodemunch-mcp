"""install-pack subcommand -- download and install Starter Pack indexes."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import httpx

# The packs are built by the jcodemunch-starter-packs CI job, which deploys to
# jcodemunch.com. The legacy j.gravelle.us copy is no longer refreshed and served
# an April build for months after the pipeline moved -- a stale artifact reads as
# a working one, so the host that CI writes to is the only correct value here.
STARTER_PACK_API = "https://jcodemunch.com/starter-packs-system/api/index.php"


def _safe_extract_path(base: Path, relative: str) -> Optional[Path]:
    """Resolve an archive member's destination and confine it to ``base``.

    Mirrors ``IndexStore._safe_content_path`` -- the same resolve-then-compare
    form already used for untrusted repository paths on every cached write.

    A leading-``/`` and ``..`` string test is not sufficient on Windows: a
    member named ``C:/Windows/Temp/x`` (or its backslash form) contains no
    ``..`` and does not start with ``/``, yet ``base / relative`` yields an
    absolute path that DISCARDS ``base`` entirely. Resolving and comparing
    against the base catches every such form, including UNC-shaped names,
    without having to enumerate them.
    """
    try:
        resolved_base = base.resolve()
        candidate = (base / relative).resolve()
        if os.path.commonpath([str(resolved_base), str(candidate)]) != str(resolved_base):
            return None
        return candidate
    except (OSError, ValueError):
        # ValueError is also what commonpath raises for paths on different
        # drives, which is exactly the escape we are refusing.
        return None


def _pack_api_headers(extra: Optional[dict] = None) -> dict:
    """Headers for every starter-pack API request.

    Identifies our traffic in the pack API's own logs, which is worth having on
    its own: a request from this client is otherwise indistinguishable from any
    other Python process.

    ⚠ It also happens to be what gets the request past the CDN in front of
    jcodemunch.com (#417, @MotoMato85). That edge answers httpx's exact default
    header set — `Host, Accept, Accept-Encoding, Connection, User-Agent`,
    Title-Case, in that order — with a 403 bot-challenge page, so `--list` and
    any keyless download never reached `index.php` at all. The rule keys on the
    header NAME SET, not on any value: replacing the `User-Agent` value does
    nothing, while adding ANY sixth header clears it. That is also why a
    licensed seat was rescued by accident — `X-JCM-License` perturbs the set.

    ⚠⚠ This is a MITIGATION, not the fix, and it is worth knowing which is
    which. The durable fix would be a bot-protection exception for the pack API
    at the CDN edge; that rule is not in this repo and can change under us at
    any time.

    ⚠⚠ **That exception was considered and declined (2026-08-06), so this is
    permanent by decision, not by neglect.** The reasoning: our own client is
    fixed here, licensing (`validate.php`) and ordering (the Stripe webhook) are
    POST and were never affected, and the residual exposure is third-party
    fetchers of `/badge.php` and `/llms.txt`. Do NOT delete these headers
    because "the 403 stopped happening" — that would be the mitigation working.
    `X-JCM-Client` is the load-bearing one; it is sixth-header padding as much
    as it is telemetry.

    The tripwire is `install-pack --list` failing for everyone at once. If that
    happens the rule has tightened and the CDN exception is back on the table; a
    drafted support ticket with the measured variant table exists for that day.
    """
    from .. import __version__

    headers = {
        "User-Agent": f"jcodemunch-mcp/{__version__} (+https://jcodemunch.com)",
        "X-JCM-Client": f"jcodemunch-mcp/{__version__}",
    }
    if extra:
        headers.update(extra)
    return headers

# ANSI helpers
_BOLD = "\033[1m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_DIM = "\033[2m"
_RESET = "\033[0m"

# Unicode with ASCII fallback for Windows cp1252
try:
    "\u2714\u2718\u00b7".encode(sys.stdout.encoding or "utf-8")
    _CHECK = "\u2714"
    _CROSS = "\u2718"
    _DOT = " \u00b7 "
except (UnicodeEncodeError, LookupError):
    _CHECK = "+"
    _CROSS = "x"
    _DOT = " - "


def _catalog_version(pack_id: str) -> Optional[str]:
    """Current published version of `pack_id`, or None if it can't be resolved.

    Used only to pin the download URL's CDN cache key. Never raises: every
    failure mode (network, non-200, malformed JSON, pack absent) returns None
    and the caller falls back to the unversioned URL.
    """
    try:
        resp = httpx.get(
            f"{STARTER_PACK_API}?action=catalog",
            headers=_pack_api_headers(),
            timeout=15.0,
            follow_redirects=True,
        )
        if resp.status_code != 200:
            return None
        for pack in (resp.json() or {}).get("packs", []):
            if pack.get("id") == pack_id:
                v = pack.get("version")
                return str(v) if v else None
    except Exception:
        return None
    return None


def _clear_builder_paths(db_path: Path) -> None:
    """Blank `source_root` / `git_root` on a freshly extracted pack index.

    Pack indexes are built from clones on the pack CI machine, so their meta
    carries that machine's `/tmp/jcm-pack-clones/<owner>-<repo>` path. Shipped
    verbatim, it made the server's startup orphan sweep classify every pack as
    a vanished local repo and delete it — silently, on every start, while the
    `.pack-<id>.json` marker survived and kept reporting the pack installed
    (#419, @MotoMato85).

    An empty `source_root` is the store's existing, honest way of saying "no
    local clone backs this index", which is the truth for a downloaded pack.
    It is also what keeps packs out of `watch-all`.

    ⚠ Deliberately best-effort: a pack that installs but is not neutralised is
    still recoverable (`heal_pack_index_paths()` repairs it at next start, and
    the sweep skips it regardless). Failing the install here would turn a
    cosmetic meta problem into an unusable pack.
    """
    try:
        import sqlite3

        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute(
                "UPDATE meta SET value = '' WHERE key IN ('source_root', 'git_root')"
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def _storage_path() -> Path:
    """Return the global index directory."""
    return Path(os.environ.get("CODE_INDEX_PATH", str(Path.home() / ".code-index")))


def resolve_effective_license_key(explicit: Optional[str]) -> Optional[str]:
    """Resolve the license key for a premium-pack download.

    Premium packs are a licensed entitlement (e.g. a Builder tier), so honor the
    same key sources as every other license consumer: an explicit ``--license``
    wins, else the ``JCODEMUNCH_LICENSE_KEY`` env var, else the config
    ``license_key``. Without this, a customer who set ``license_key`` in
    ``config.jsonc`` (rather than passing ``--license`` every time) silently
    can't download the packs they paid for.
    """
    if explicit:
        return explicit
    try:
        from ..config import load_config
        from ..org.license import _license_key
        load_config()  # idempotent; makes the config `license_key` visible
        return _license_key() or None
    except Exception:
        return None


def _mask_license(key: str) -> str:
    """Mask a license key for display: first 4 + last 4 chars."""
    if len(key) <= 8:
        return key[:4] + "****"
    return key[:4] + "****" + key[-4:]


def _list_packs() -> int:
    """Fetch and display the starter pack catalog."""
    # ⚠ No raise_for_status. It used to sit here, inside a `except httpx.HTTPError`
    # — and HTTPStatusError is a SUBCLASS of HTTPError, so every non-2xx response
    # was reported as "check your network connection" for a server that was up and
    # answering (#417, @MotoMato85: that message sent him to DNS, his Pi-hole and
    # his firewall before he looked at the response body). Only a transport failure
    # means unreachable. `_install_pack` already had this right and its comment
    # described this exact bug; the two paths now agree.
    try:
        resp = httpx.get(
            f"{STARTER_PACK_API}?action=catalog",
            timeout=15,
            headers=_pack_api_headers(),
        )
    except httpx.HTTPError as exc:
        print(
            f"  {_RED}{_CROSS} Could not reach the starter packs server. "
            f"Check your network connection.{_RESET}",
            file=sys.stderr,
        )
        print(f"  {_DIM}({type(exc).__name__}: {exc}){_RESET}", file=sys.stderr)
        return 1

    # Gate on "is this the catalog", not on a header guess. A content-type test
    # would reject a server that labels correct JSON unusually; parseability is
    # the property actually required two lines down.
    try:
        catalog = resp.json()
    except ValueError:
        content_type = str(resp.headers.get("content-type", "") or "")
        print(
            f"  {_RED}{_CROSS} The starter packs server answered "
            f"{resp.status_code} ({content_type or 'no content-type'}) "
            f"instead of the catalog.{_RESET}",
            file=sys.stderr,
        )
        if "html" in content_type:
            print(
                f"  {_DIM}That is an HTML page from an edge/proxy, not the pack "
                f"API. If it persists, please report it with this line.{_RESET}",
                file=sys.stderr,
            )
        return 1
    packs = catalog.get("packs", [])
    if not packs:
        print("  No starter packs available yet.")
        return 0

    print()
    print(f"  {_BOLD}jCodeMunch Starter Packs{_RESET}")
    print(f"  {'=' * 50}")
    print()

    for pack in packs:
        pack_id = pack.get("id", "unknown")
        name = pack.get("name", pack_id)
        description = pack.get("description", "")
        symbols = pack.get("symbols", 0)
        size = pack.get("size", "")
        free = pack.get("free", False)
        # Composed here, not taken from the catalog's own `download_url`. The
        # server writes that field, and when the two disagree the printed line
        # is the one a user copies -- which is how a stale host kept handing out
        # its own address after the client had moved off it. This URL is the one
        # `install-pack` will actually fetch, by construction.
        download_url = f"{STARTER_PACK_API}?action=download&pack={pack_id}"

        tag = f"{_GREEN}  FREE  {_RESET}" if free else f"{_YELLOW} LICENSE {_RESET}"
        print(f"  {tag}  {pack_id:<20s}  {_BOLD}{name}{_RESET}")
        details = []
        if symbols:
            details.append(f"{symbols:,} symbols")
        if size:
            details.append(size)
        if details:
            print(f"           {_DOT.join(details)}")
        if description:
            print(f"           {_DIM}{description}{_RESET}")
        # A pack is somebody else's source, indexed. Name the terms before the
        # download, not only in a file the user finds afterwards.
        licenses = pack.get("licenses") or []
        spdx = sorted({l.get("spdx", "") for l in licenses if l.get("spdx")})
        if spdx:
            print(f"           {_DIM}Upstream licence: {', '.join(spdx)}{_RESET}")
        print(f"           jcodemunch-mcp install-pack {pack_id}")
        if download_url:
            print(f"           {_DIM}{download_url}{_RESET}")
        print()

    print(f"  {_DIM}Free packs require no license. Licensed packs require a jCodeMunch license.{_RESET}")
    print(f"  {_DIM}Get a license: https://jcodemunch.com/#pricing{_RESET}")
    print()
    return 0


def _install_pack(
    pack_id: str,
    license_key: Optional[str] = None,
    force: bool = False,
    base_path: Optional[Path] = None,
) -> int:
    """Download and install a starter pack."""
    base = base_path if base_path is not None else _storage_path()
    marker = base / f".pack-{pack_id}.json"

    # Already installed?
    if marker.exists() and not force:
        print(f"  Pack '{pack_id}' is already installed. Use --force to re-download.")
        return 0

    # Build download URL. The license key travels in the X-JCM-License header,
    # NEVER the URL query string, so it can't land in server/proxy access logs
    # (audit finding V12).
    #
    # `v` pins the CDN cache key to a specific build. The unversioned URL never
    # changed between releases, so hcdn served the PREVIOUS pack for a full day
    # after each deploy -- on 2026-08-07 that handed out the pack whose indexes
    # delete themselves on the next server start (#419), hours after the fix
    # was live. A version-scoped URL cannot be shadowed by a stale entry.
    #
    # ⚠ Best-effort by design. If the catalog is unreachable we fetch the
    # unversioned URL, which still works and now carries a 5-minute server-side
    # cache instead of 24 hours. Failing the install because a cache hint could
    # not be resolved would trade a rare staleness window for a hard outage.
    pack_version = _catalog_version(pack_id)
    url = f"{STARTER_PACK_API}?action=download&pack={pack_id}"
    if pack_version:
        url += f"&v={quote(str(pack_version), safe='')}"
    headers = _pack_api_headers(
        {"X-JCM-License": license_key} if license_key else None
    )

    print(f"  Downloading starter pack '{pack_id}'...", flush=True)
    # No raise_for_status: the API reports license/pack errors as 4xx + JSON
    # (handled below); raising on them rendered every one as a bogus
    # "could not reach the server". Only transport failures are unreachable.
    try:
        resp = httpx.get(url, timeout=120, follow_redirects=True, headers=headers)
    except httpx.HTTPError:
        print(
            f"  {_RED}{_CROSS} Could not reach the starter packs server. "
            f"Check your network connection.{_RESET}",
        )
        return 1

    content_type = resp.headers.get("content-type", "")

    # Error response (JSON)
    if "application/json" in content_type:
        data = resp.json()
        error = data.get("error", "Unknown error")
        print(f"  {_RED}{_CROSS} {error}{_RESET}")
        print()
        free_packs = data.get("free_packs")
        if free_packs:
            print(f"  Free packs available: {', '.join(free_packs)}")
        get_license = data.get("get_license")
        if get_license:
            print(f"  Get a license: {get_license}")
        hint = data.get("hint")
        # ⚠ Suppress the server's hint when we ALREADY did what it advises.
        # `action=download` refuses a key sent in `X-JCM-License` and answers
        # "This pack requires a jCodeMunch license" with the hint "Use:
        # install-pack <pack> --license YOUR-KEY" — advice the client has
        # already followed, since --license lands in that same header. Relaying
        # it tells a paying user their valid key was rejected (#418,
        # @MotoMato85). Say what actually happened instead.
        if hint and not license_key:
            print(f"  Hint: {hint}")
        if license_key:
            print(f"  License: {_mask_license(license_key)} (sent as X-JCM-License)")
            print()
            print(
                f"  {_YELLOW}A license key WAS sent with this request and the "
                f"server still reports the pack as unlicensed.{_RESET}"
            )
            print(
                "  That is not a problem with your key — check it with: "
                "jcodemunch-mcp license"
            )
            print(
                "  If it validates, this is a known server-side gap: "
                "https://github.com/jgravelle/jcodemunch-mcp/issues/418"
            )
        return 1

    # Expect a zip
    if "application/zip" not in content_type and "application/octet-stream" not in content_type:
        print(
            f"  {_RED}{_CROSS} Unexpected response (content-type: {content_type}){_RESET}",
            file=sys.stderr,
        )
        return 1

    pack_version = resp.headers.get("X-Pack-Version", "unknown")

    # Save to temp file, extract
    base.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        tmp.write(resp.content)
        tmp_path = Path(tmp.name)

    try:
        with zipfile.ZipFile(tmp_path, "r") as zf:
            # Validate -- no path traversal
            for info in zf.infolist():
                if info.filename.startswith("/") or ".." in info.filename:
                    print(
                        f"  {_RED}{_CROSS} Archive contains unsafe paths. Aborting.{_RESET}",
                        file=sys.stderr,
                    )
                    return 1

            manifest_data = None
            symbol_count = 0

            for info in zf.infolist():
                if info.is_dir():
                    continue

                # Strip top-level <pack-id>/ prefix
                parts = info.filename.split("/", 1)
                if len(parts) < 2 or not parts[1]:
                    continue
                relative = parts[1]

                # Capture manifest
                if relative == "manifest.json":
                    manifest_data = json.loads(zf.read(info.filename))
                    symbol_count = manifest_data.get("total_symbols", 0)
                    continue

                dest = _safe_extract_path(base, relative)
                if dest is None:
                    print(
                        f"  {_RED}{_CROSS} Archive member escapes the install "
                        f"directory: {info.filename!r}. Aborting.{_RESET}",
                        file=sys.stderr,
                    )
                    return 1
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info.filename) as src, open(dest, "wb") as dst:
                    dst.write(src.read())
                if dest.suffix == ".db":
                    _clear_builder_paths(dest)

            # Write install marker
            if manifest_data is None:
                manifest_data = {"pack": pack_id, "version": pack_version}
            manifest_data["installed_version"] = pack_version
            marker.write_text(json.dumps(manifest_data), encoding="utf-8")

    except zipfile.BadZipFile:
        print(
            f"  {_RED}{_CROSS} Downloaded file is corrupted. Try again with --force.{_RESET}",
        )
        return 1
    finally:
        tmp_path.unlink(missing_ok=True)

    # Success
    pack_name = (manifest_data or {}).get("name", pack_id)
    repos = (manifest_data or {}).get("repos", [])

    print()
    print(f"  {_GREEN}{_CHECK} Installed starter pack: {pack_name}{_RESET}")
    if symbol_count:
        print(f"  {_GREEN}{_CHECK} {symbol_count:,} symbols ready for retrieval{_RESET}")
    print(f"  {_GREEN}{_CHECK} Index location: {base}{_RESET}")
    print()

    if repos:
        print("  Repos included:")
        for repo in repos:
            repo_name = repo if isinstance(repo, str) else repo.get("repo", "")
            if repo_name:
                print(f"    - {repo_name}")
        print()

    # The pack carries each upstream's licence verbatim. Say where it landed and
    # under what terms, so the answer does not depend on the user going looking.
    licenses = (manifest_data or {}).get("licenses") or []
    if licenses:
        print("  Upstream licences (shipped with the pack):")
        for entry in licenses:
            spdx = entry.get("spdx") or "see file"
            print(f"    - {entry.get('repo', '?')}: {spdx}")
        print(f"  {_DIM}Full text: {base / 'licenses'}{_RESET}")
        print()

    # Opt-in telemetry
    if os.environ.get("JCODEMUNCH_SHARE_SAVINGS", "1") != "0":
        try:
            httpx.post(
                f"{STARTER_PACK_API}?action=telemetry",
                json={
                    "pack": pack_id,
                    "version": pack_version,
                    "platform": sys.platform,
                },
                timeout=5,
            )
        except Exception:
            pass

    return 0


def run_install_pack(
    pack_id: Optional[str] = None,
    license_key: Optional[str] = None,
    list_packs: bool = False,
    force: bool = False,
) -> int:
    """Entry point for the install-pack subcommand."""
    if list_packs or not pack_id:
        return _list_packs()
    effective_key = resolve_effective_license_key(license_key)
    return _install_pack(pack_id, license_key=effective_key, force=force)
