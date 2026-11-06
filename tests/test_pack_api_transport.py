"""The starter-pack API transport, and the three ways it misreported failure.

From #417 and #418 (@MotoMato85), whose measurements are in the issues. These
are transport-and-reporting concerns rather than catalog rendering, which is why
they live beside `test_install_pack.py` instead of inside it.

⚠⚠ Neither underlying defect is in this repo, and that shapes every assertion
here. The 403 is a CDN rule in front of jcodemunch.com; the ignored
`X-JCM-License` header is `index.php`. What this file pins is the only half we
own — the SHAPE of the request we send, and the WORDS we print when it fails.

⚠ Nothing here talks to the network, deliberately. Asserting the CDN's live
behaviour would make our suite fail on someone else's config change, and would
go green the moment the real fix landed — which is exactly when we would still
want to know our request had not silently reverted to the default header set.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx

from jcodemunch_mcp.cli.install_pack import (
    _install_pack,
    _list_packs,
    _pack_api_headers,
)
from tests.conftest_helpers import stream_through_get


# ── The request we send ───────────────────────────────────────────────────
#
# The CDN in front of jcodemunch.com answers httpx's EXACT default header set
# with a 403 challenge page. The rule keys on the header NAME SET, not on any
# value: replacing the User-Agent value does nothing, adding any sixth header
# clears it. So the guarantee is "we send at least one header httpx would not
# have sent by itself", and a test that asserts particular VALUES would pin the
# wrong property.

HTTPX_DEFAULT_HEADER_NAMES = {
    "host", "accept", "accept-encoding", "connection", "user-agent",
}


def test_we_send_a_header_httpx_would_not_have_sent():
    """The load-bearing property, stated once.

    Not "we send X-JCM-Client" — that is how it is implemented today. The thing
    that must stay true is that our request is distinguishable from a bare
    httpx one, because a bare httpx one does not reach the API.
    """
    sent = {k.lower() for k in _pack_api_headers()}
    assert sent - HTTPX_DEFAULT_HEADER_NAMES, (
        "every header we set is one httpx sets by default, so our request is "
        f"byte-identical to the default set the CDN refuses; sent={sorted(sent)}"
    )


def test_the_identifying_header_survives_a_license_key():
    """A licensed seat was rescued by ACCIDENT — `X-JCM-License` happened to
    perturb the header set. Merging must not have made that the only reason it
    works, or removing the license key would silently reintroduce the 403."""
    with_key = _pack_api_headers({"X-JCM-License": "JCM-DUMMY"})
    assert with_key["X-JCM-License"] == "JCM-DUMMY"
    assert (
        {k.lower() for k in with_key} - HTTPX_DEFAULT_HEADER_NAMES - {"x-jcm-license"}
    ), "with a key, the ONLY non-default header is the key itself"


def test_a_none_extra_is_not_an_error():
    """`_install_pack` passes `None` when no key is configured — the keyless
    path is exactly the one that was broken, so it must not throw."""
    assert _pack_api_headers(None)
    assert _pack_api_headers()


@patch("jcodemunch_mcp.cli.install_pack.httpx")
def test_the_catalog_request_carries_them(mock_httpx):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"packs": []}
    mock_httpx.get.return_value = resp

    assert _list_packs() == 0

    headers = mock_httpx.get.call_args.kwargs["headers"]
    assert {k.lower() for k in headers} - HTTPX_DEFAULT_HEADER_NAMES


@patch("jcodemunch_mcp.cli.install_pack.httpx")
def test_the_download_request_carries_them_without_a_key(mock_httpx, tmp_path):
    """The keyless download is the case the issue was filed about: a free pack
    on a seat with no license, which is precisely the audience it exists for."""
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {"content-type": "text/html"}   # what the CDN served
    mock_httpx.get.return_value = resp
    mock_httpx.HTTPError = httpx.HTTPError

    _install_pack("nodejs", license_key=None, base_path=tmp_path)

    headers = mock_httpx.get.call_args.kwargs["headers"]
    assert headers, "no headers at all were sent on the keyless download path"
    assert {k.lower() for k in headers} - HTTPX_DEFAULT_HEADER_NAMES


# ── What we print when it fails ───────────────────────────────────────────
#
# `_list_packs` called raise_for_status() inside `except httpx.HTTPError`, and
# HTTPStatusError subclasses HTTPError, so a 403 from a server that was up and
# answering printed "check your network connection". The reporter checked DNS,
# his Pi-hole and his firewall before reading the response body.


class _FakeResponse:
    def __init__(self, status_code, content_type, body):
        self.status_code = status_code
        self.headers = {"content-type": content_type}
        self._body = body

    def json(self):
        return json.loads(self._body)


@patch("jcodemunch_mcp.cli.install_pack.httpx")
def test_an_html_interstitial_is_not_reported_as_a_network_failure(
    mock_httpx, capsys
):
    mock_httpx.get.return_value = _FakeResponse(
        403, "text/html", "<title>Checking your browser</title>"
    )
    mock_httpx.HTTPError = httpx.HTTPError

    assert _list_packs() == 1

    err = capsys.readouterr().err
    assert "network connection" not in err.lower(), (
        "a server that answered 403 was reported as unreachable; that is the "
        f"misdiagnosis this test exists for. got: {err!r}"
    )
    assert "403" in err, f"the status the server actually returned is not shown: {err!r}"


@patch("jcodemunch_mcp.cli.install_pack.httpx")
def test_a_real_transport_failure_still_says_network(mock_httpx, capsys):
    """Non-vacuity. If everything became a status report, the honest
    connectivity message would be gone and this fix would have traded one wrong
    diagnosis for another."""
    mock_httpx.HTTPError = httpx.HTTPError
    mock_httpx.get.side_effect = httpx.ConnectError("nodename nor servname provided")

    assert _list_packs() == 1

    err = capsys.readouterr().err
    assert "network connection" in err.lower(), (
        f"a genuine transport failure must still read as one: {err!r}"
    )
    assert "ConnectError" in err, "the underlying exception is not named"


@patch("jcodemunch_mcp.cli.install_pack.httpx")
def test_a_json_api_error_is_still_rendered_as_the_api_error(mock_httpx, capsys):
    """The 4xx+JSON path is how the API reports license and pack errors. It
    must survive: raising on it is the bug `_install_pack`'s comment already
    warned about, and the one `_list_packs` had."""
    mock_httpx.get.return_value = _FakeResponse(
        403, "application/json", json.dumps({"packs": [], "error": "nope"})
    )
    mock_httpx.HTTPError = httpx.HTTPError

    # Parseable JSON: it reaches the catalog reader rather than the error branch.
    assert _list_packs() == 0
    assert "network connection" not in capsys.readouterr().err.lower()


# ── #418: do not relay advice the user already followed ───────────────────
#
# `action=download` ignores `X-JCM-License` and answers a valid key with
# "This pack requires a jCodeMunch license" plus the hint "Use: install-pack
# <pack> --license YOUR-KEY". `--license` lands in that same ignored header, so
# the hint asks the user to redo what just failed. The transport fix is
# server-side; what the client owes is an accurate account of what happened.

_LICENSE_REFUSAL = json.dumps({
    "error": "This pack requires a jCodeMunch license.",
    "pack": "fastapi",
    "free_packs": ["nodejs"],
    "get_license": "https://jcodemunch.com/#pricing",
    "hint": "Use: jcodemunch-mcp install-pack fastapi --license YOUR-KEY",
})


@patch("jcodemunch_mcp.cli.install_pack.httpx")
def test_a_sent_key_is_not_answered_with_use_the_license_flag(
    mock_httpx, tmp_path, capsys
):
    stream_through_get(mock_httpx)
    mock_httpx.HTTPError = httpx.HTTPError
    mock_httpx.get.return_value = _FakeResponse(
        403, "application/json", _LICENSE_REFUSAL
    )

    assert _install_pack("fastapi", license_key="JCM-REAL-KEY", base_path=tmp_path) == 1

    out = capsys.readouterr().out
    assert "--license YOUR-KEY" not in out, (
        "the server's hint was relayed to a user who already passed a key; it "
        f"reads as 'your key was rejected'. got: {out!r}"
    )
    assert "WAS sent" in out, "the output never says the key was actually sent"
    assert "418" in out, "no pointer to the tracked server-side gap"


@patch("jcodemunch_mcp.cli.install_pack.httpx")
def test_a_keyless_seat_still_gets_the_servers_hint(mock_httpx, tmp_path, capsys):
    """Non-vacuity. Without a key the hint is CORRECT and actionable — someone
    who owns a license and forgot the flag needs to be told. Suppressing it for
    everyone would trade one bad message for another."""
    stream_through_get(mock_httpx)
    mock_httpx.HTTPError = httpx.HTTPError
    mock_httpx.get.return_value = _FakeResponse(
        403, "application/json", _LICENSE_REFUSAL
    )

    assert _install_pack("fastapi", license_key=None, base_path=tmp_path) == 1

    out = capsys.readouterr().out
    assert "--license YOUR-KEY" in out, f"the useful hint was suppressed too: {out!r}"
    assert "WAS sent" not in out
