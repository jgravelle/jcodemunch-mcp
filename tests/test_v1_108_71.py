"""v1.108.71 — telemetry worker hardening (PRD F-T01 / F-T02).

The community-meter sender used to spawn a daemon thread at module import,
unconditionally, regardless of the share_savings opt-out. That is an import-time
background-service side effect (the shape package scanners flag) and it ran even
for opted-out users. The worker is now started lazily on the first share, behind
the existing opt-out gate, and the endpoint is overridable via env.
"""

from __future__ import annotations

import subprocess
import sys
import threading

import pytest

from jcodemunch_mcp.storage import token_tracker as tt


_WORKER_NAME = "jcodemunch-telemetry"


def _worker_threads():
    return [t for t in threading.enumerate() if t.name == _WORKER_NAME]


class TestNoImportTimeThread:
    def test_fresh_import_starts_no_worker_thread(self):
        """A clean `import` must not spawn the telemetry thread (run in a fresh
        interpreter so prior in-process tests can't have started it)."""
        code = (
            "import threading\n"
            "import jcodemunch_mcp.storage.token_tracker as t\n"
            "live = [x for x in threading.enumerate() if x.name == 'jcodemunch-telemetry']\n"
            "assert not live, 'worker thread started at import'\n"
            "assert t._telemetry_worker_started is False, 'started flag set at import'\n"
            "print('OK')\n"
        )
        res = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, timeout=60
        )
        assert res.returncode == 0, f"stderr={res.stderr}\nstdout={res.stdout}"
        assert "OK" in res.stdout


class TestLazyStart:
    def test_ensure_starts_one_worker_and_is_idempotent(self):
        before = len(_worker_threads())
        tt._ensure_telemetry_worker()
        after_first = _worker_threads()
        assert len(after_first) == before + 1 or before >= 1, "worker did not start"
        assert tt._telemetry_worker_started is True
        # Idempotent: a second call must not spawn another thread.
        tt._ensure_telemetry_worker()
        assert len(_worker_threads()) == len(after_first)


class TestOptOutNeverStartsWorker:
    def test_opted_out_flush_does_not_touch_the_worker(self, monkeypatch, tmp_path):
        """With share_savings disabled, flush must not even reach the worker
        starter (the enqueue is gated upstream)."""
        called = {"ensure": False}
        monkeypatch.setattr(
            tt, "_ensure_telemetry_worker",
            lambda: called.__setitem__("ensure", True),
        )
        monkeypatch.setattr(
            tt._config, "get",
            lambda key, default=None, repo=None: False if key == "share_savings" else default,
        )
        st = tt._State()
        st.add(1000, str(tmp_path))
        st.flush()
        assert called["ensure"] is False, "worker was started despite opt-out"


class TestTelemetryUrlOverride:
    def test_default_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("JCODEMUNCH_TELEMETRY_URL", raising=False)
        assert tt._telemetry_url() == tt._TELEMETRY_URL

    def test_env_override_wins(self, monkeypatch):
        monkeypatch.setenv("JCODEMUNCH_TELEMETRY_URL", "https://example.test/meter")
        assert tt._telemetry_url() == "https://example.test/meter"

    def test_empty_env_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("JCODEMUNCH_TELEMETRY_URL", "")
        assert tt._telemetry_url() == tt._TELEMETRY_URL
