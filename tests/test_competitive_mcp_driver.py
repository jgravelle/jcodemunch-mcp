"""The competitive MCP stdio driver (benchmarks/competitive/sandbox/mcp_driver.py;
docs/competitive/FINDINGS.md CF-27).

What this pins, and why (for docs/harness/ARCHAEOLOGY.md): the driver read a
server's stderr only at exit, so a server that logs every tool result there
(Serena does, at INFO) filled the pipe on its first large result, blocked
inside the tool, and every later call read as a hang; the second Serena probe
timed out at the session ceiling. The stub server here writes more to stderr
than any pipe buffers BEFORE it answers a call, which is the shape of the
defect: with the drain disabled (`drain_stderr=False`, the pre-change
driver's behaviour, an arm of this file) the request times out; with the
drain it answers, and the tail the driver keeps is bounded. A driver that could only ever block or
time out can never move a measured number, which is why the tail is all it
records.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
COMPETE = REPO / "benchmarks" / "competitive"
if not COMPETE.is_dir():  # excluded from the sdist (pyproject); the tests are meaningless without it
    pytest.skip("benchmarks/competitive is not in this tree (not shipped in the sdist)", allow_module_level=True)
sys.path.insert(0, str(COMPETE / "sandbox"))

import mcp_driver  # noqa: E402

# A JSON-RPC server that logs 4 MB to stderr before answering each tools/call
# (Linux pipes buffer 64 KB, Windows anonymous pipes 4 KB).
STUB = r'''
import json, sys
NOISE = ("x" * 1023 + "\n") * 4096
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    m = json.loads(line)
    rid = m.get("id")
    if m["method"] == "initialize":
        r = {"protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {"name": "stub", "version": "0"}}
    elif m["method"] == "tools/list":
        r = {"tools": [{"name": "echo", "description": "echo", "inputSchema": {"type": "object"}}]}
    elif m["method"] == "tools/call":
        sys.stderr.write(NOISE)
        sys.stderr.flush()
        r = {"content": [{"type": "text", "text": "answer:" + json.dumps(m["params"]["arguments"])}]}
    else:
        continue
    if rid is None:
        continue
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "result": r}) + "\n")
    sys.stdout.flush()
'''


def _stub(tmp_path: Path) -> list[str]:
    p = tmp_path / "stub_server.py"
    p.write_text(STUB, encoding="utf-8")
    return [sys.executable, "-u", str(p)]


def test_a_server_that_floods_stderr_before_answering_is_answered_not_timed_out(tmp_path):
    c = mcp_driver.Client(_stub(tmp_path))
    try:
        init, _ = c.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}, timeout=30)
        assert init is not None and "result" in init
        for i in range(3):  # three calls: 12 MB of stderr in total, every one answered
            msg, ms = c.request("tools/call", {"name": "echo", "arguments": {"i": i}}, timeout=30)
            text, is_err = mcp_driver.result_text(msg)
            assert msg is not None, f"call {i} timed out: the stderr pipe was not drained"
            assert text == f'answer:{{"i": {i}}}' and not is_err and ms < 30_000
    finally:
        tail = c.close()
    assert tail and len(tail) <= 2000  # the tail is kept and bounded: 12 MB of log never reaches the result file


def test_the_driver_records_the_tail_and_the_calls_through_main(tmp_path):
    calls = tmp_path / "calls.json"
    calls.write_text(json.dumps([{"id": "a", "tool": "echo", "args": {"q": 1}}]), encoding="utf-8")
    out = tmp_path / "out.json"
    rc = mcp_driver.main(["mcp_driver.py", str(out), str(calls), "--", *_stub(tmp_path)])
    d = json.loads(out.read_text(encoding="utf-8"))
    assert rc == 0 and d["error"] is None
    assert d["tool_names"] == ["echo"] and json.loads(d["tools_list_json"])[0]["inputSchema"] == {"type": "object"}
    assert d["calls"][0]["result_text"] == 'answer:{"q": 1}' and d["calls"][0]["timed_out"] is False
    assert 0 < len(d["stderr_tail"]) <= 2000


def test_without_the_drain_the_same_server_times_out(tmp_path):
    """The non-vacuity arm: the pre-change driver's behaviour, reproduced on any box."""
    c = mcp_driver.Client(_stub(tmp_path), drain_stderr=False)
    try:
        init, _ = c.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}, timeout=30)
        assert init is not None and "result" in init  # the handshake writes nothing to stderr and still answers
        msg, ms = c.request("tools/call", {"name": "echo", "arguments": {"i": 0}}, timeout=6)
        assert msg is None and ms >= 6_000  # the server is blocked writing stderr; the call never answers
    finally:
        c.close()
