"""The Windows watcher task's logon trigger names the calling user (#1564, @rknighton).

`watch-install` registered a `LogonTrigger` with no `UserId`. A trigger with no
user fires at ANY user's logon, and Windows refuses to register that from a
shell that is not elevated ("Access is denied"), so the watcher installed only
from an elevated shell. The reporter measured both halves on Windows 11 from a
non-elevated shell: a `UserId` in the trigger registers, one in the principal
alone does not.

⚠ The symptom is not reproducible from an elevated shell, which registers
either form, so these tests pin the CAUSE on the generated XML. That's how the
sibling L-149 tests treat the same definition. NON-VACUITY: the trigger tests
fail on the pre-fix template, which has no `UserId` anywhere. The controls (the
principal is unchanged, an unreadable user leaves the trigger as it was) pass
on both sides.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from unittest.mock import patch

from jcodemunch_mcp import service_installer as si

_NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
_EXEC = [r"C:\Program Files\Python 3\python.exe", "-m", "jcodemunch_mcp", "watch-all"]
B = chr(92)


def _xml(user_id):
    with patch.object(si, "_exec_cmd", lambda: list(_EXEC)), patch.object(
        si, "_windows_user_id", lambda: user_id
    ):
        return ET.fromstring(si._windows_task_xml().encode("utf-16"))


def test_the_logon_trigger_names_the_calling_user():
    root = _xml(f"WORKGROUP{B}jo")
    trigger = root.find("t:Triggers/t:LogonTrigger", _NS)
    assert trigger.findtext("t:UserId", namespaces=_NS) == f"WORKGROUP{B}jo"


def test_a_user_id_with_xml_characters_is_escaped():
    root = _xml(f"D&O{B}a<b")
    assert root.find("t:Triggers/t:LogonTrigger", _NS).findtext("t:UserId", namespaces=_NS) == f"D&O{B}a<b"


def test_the_principal_still_names_no_user():
    """CONTROL: the reporter measured that a UserId in the principal does not help."""
    principal = _xml(f"WORKGROUP{B}jo").find("t:Principals/t:Principal", _NS)
    assert principal.find("t:UserId", _NS) is None


def test_an_unreadable_user_leaves_the_trigger_as_before():
    """CONTROL: no user readable -> the pre-#1564 trigger, still valid XML."""
    trigger = _xml(None).find("t:Triggers/t:LogonTrigger", _NS)
    assert trigger is not None and trigger.find("t:UserId", _NS) is None


def test_the_user_id_comes_from_the_environment_when_the_api_is_absent(monkeypatch):
    monkeypatch.setenv("USERNAME", "jo")
    monkeypatch.setenv("USERDOMAIN", "WORKGROUP")
    import ctypes

    if hasattr(ctypes, "windll"):
        monkeypatch.setattr(ctypes.windll, "secur32", None, raising=False)
    assert si._windows_user_id() == f"WORKGROUP{B}jo"


# DEFINED only on Windows, never skipped elsewhere (the budget rule in
# test_windows_watch_task_settings.py). Every test above patches the user or
# removes the API, so this is the one that runs GetUserNameExW itself.
if hasattr(__import__("ctypes"), "windll"):

    def test_the_api_names_the_logged_on_user_as_domain_backslash_user(monkeypatch):
        monkeypatch.setenv("USERNAME", "not-the-api")
        monkeypatch.setenv("USERDOMAIN", "NOT-THE-API")
        import ctypes

        buf = ctypes.create_unicode_buffer(257)
        size = ctypes.c_ulong(257)
        assert ctypes.windll.advapi32.GetUserNameW(buf, ctypes.byref(size))
        user_id = si._windows_user_id()
        assert user_id and B in user_id, user_id
        assert user_id.split(B)[-1].lower() == buf.value.lower(), (user_id, buf.value)
