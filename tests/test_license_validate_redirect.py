"""A redirect on the license-validation POST is reported as a moved endpoint, never followed.

`_check_server` POSTed the key with `follow_redirects=True`. httpx answers a
301 or 302 on a POST by re-sending it as a GET with no body, so if
`validate.php` were ever redirected the way the retired host's other pages
are, the key would never reach the validator. The GET's answer (a page, or
`{"valid": false}` for a request with no key) was then read as the key's
verdict: a paying customer's key cached as rejected, with no hint why.

Now the POST does not follow a redirect. A 3xx answer is reported as
"validation endpoint moved", treated like an unreachable server (a key the
server confirmed earlier stays valid, an unconfirmed one reads as
unverified, never rejected), and nothing is cached.
"""

from __future__ import annotations

import time

import pytest

from jcodemunch_mcp.org import license as lic

KEY = "PAIDKEY00001"


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path))
    monkeypatch.delenv("JCODEMUNCH_LICENSE_KEY", raising=False)
    monkeypatch.setattr(
        lic, "_license_key", lambda: __import__("os").environ.get("JCODEMUNCH_LICENSE_KEY", "").strip()
    )
    yield


class _Resp:
    def __init__(self, status, *, location=None, body=None):
        self.status_code = status
        self.headers = {"location": location} if location else {}
        self._body = body

    @property
    def is_redirect(self):
        return 300 <= self.status_code < 400 and "location" in self.headers

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


def _fake_post(monkeypatch, resp, calls):
    import httpx

    def post(url, **kw):
        calls.append(kw)
        if kw.get("follow_redirects"):
            # What httpx would do: follow as a GET with no body, which the
            # validator answers as a request with no key.
            return _Resp(400, body={"valid": False, "error": "missing license"})
        return resp

    monkeypatch.setattr(httpx, "post", post)


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_a_redirect_is_not_followed_and_names_the_move(monkeypatch, status):
    calls: list = []
    _fake_post(monkeypatch, _Resp(status, location="https://jcodemunch.com/validate.php"), calls)
    res = lic._is_validated(KEY)
    assert calls and calls[0].get("follow_redirects") is False
    assert res["valid"] is False
    assert res["confirmed"] is False
    assert "moved" in (res["error"] or "")
    assert lic._key_status(KEY, res) == "unverified_offline"
    # Nothing is cached from a redirect.
    assert lic._load_state().get("key") != KEY


def test_a_confirmed_key_stays_valid_through_a_redirect(monkeypatch):
    lic._save_state({"key": KEY, "valid": True, "tier": "studio", "checked_at": time.time() - lic.RECHECK_SECONDS - 5})
    _fake_post(monkeypatch, _Resp(301, location="https://jcodemunch.com/validate.php"), [])
    res = lic._is_validated(KEY)
    assert res == {"valid": True, "tier": "studio", "error": None, "confirmed": True}


def test_the_license_line_says_the_endpoint_moved(monkeypatch):
    monkeypatch.setenv("JCODEMUNCH_LICENSE_KEY", KEY)
    _fake_post(monkeypatch, _Resp(301, location="https://jcodemunch.com/validate.php"), [])
    gate = lic.check_gate()
    assert gate["key_status"] == "unverified_offline"
    text = "\n".join(lic.format_license_status(gate))
    assert "not recognized" not in text
    assert "moved" in text


def test_a_direct_answer_is_still_read(monkeypatch):
    _fake_post(monkeypatch, _Resp(200, body={"valid": True, "tier": "platform"}), [])
    res = lic._is_validated(KEY)
    assert res["valid"] is True and res["tier"] == "platform" and res["confirmed"] is True
