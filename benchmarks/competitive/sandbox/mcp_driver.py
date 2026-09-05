"""A minimal MCP stdio client that runs INSIDE a competitor's container
(docs/competitive/DESIGN.md s2, the tools/list weight; s1.3, mcp-stdio adapters).

purpose:  measure what an MCP client pays: `initialize`, `tools/list` (the
          schema weight, serialised as name/description/inputSchema, the
          zhang-liz shape), then each requested `tools/call`, timing every
          round trip and recording the exact result payload
invokes:  the server command given on the command line, over stdio,
          JSON-RPC 2.0 with the MCP 2025-06-18 handshake; nothing else
produces: /out/mcp.json: {tools_list_json, calls: [{id, tool, args, ms,
          result_text, is_error}], stderr_tail}; stderr is drained
          continuously (a server that logs every result there would
          otherwise block on a full pipe and read as a hang)
refuses:  a server that does not answer `initialize` within the timeout
          (recorded as an error, the adapter maps it to not_runnable)
pinned:   n/a (stdlib only; copied into each mcp-stdio image)
fairness: the client is the same for every MCP server measured, jCodeMunch
          included when its stdio variant runs; a call's payload is the
          concatenated text content of the result, which is what an agent
          receives.

Usage: python mcp_driver.py <out.json> <calls.json> -- <server command...>
  calls.json: [{"id": "...", "tool": "...", "args": {...}}, ...]
"""

from __future__ import annotations

import collections
import json
import os
import select
import subprocess
import sys
import threading
import time

TIMEOUT_S = float(os.environ.get("MCP_DRIVER_TIMEOUT_S", "120"))


class Client:
    def __init__(self, cmd: list[str]) -> None:
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)  # bytes: the protocol is framed by newlines and decoded per message
        self._id = 0
        self._buf = b""
        # stderr is drained continuously into a bounded tail: a server that logs
        # every tool result to stderr (Serena does) fills the pipe and blocks
        # mid-call if nobody reads it, which read as a hang of the tool.
        self._err = collections.deque(maxlen=64)
        self._err_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._err_thread.start()

    def _drain_stderr(self) -> None:
        assert self.proc.stderr is not None
        try:
            for line in iter(self.proc.stderr.readline, b""):
                self._err.append(line[-400:])
        except Exception:
            pass

    def _send(self, msg: dict) -> None:
        data = (json.dumps(msg) + "\n").encode("utf-8")
        assert self.proc.stdin is not None
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def _readline(self, deadline: float) -> bytes | None:
        assert self.proc.stdout is not None
        fd = self.proc.stdout.fileno()
        while b"\n" not in self._buf:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            r, _, _ = select.select([fd], [], [], min(remaining, 1.0))
            if not r:
                if self.proc.poll() is not None:
                    return None
                continue
            chunk = os.read(fd, 65536)
            if not chunk:
                return None
            self._buf += chunk
        line, _, self._buf = self._buf.partition(b"\n")
        return line

    def request(self, method: str, params: dict | None = None, timeout: float = TIMEOUT_S) -> tuple[dict | None, float]:
        self._id += 1
        rid = self._id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        t0 = time.perf_counter()
        deadline = time.monotonic() + timeout
        while True:
            line = self._readline(deadline)
            if line is None:
                return None, (time.perf_counter() - t0) * 1000
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue  # a log line on stdout: skipped, but it is a protocol violation worth a note
            if msg.get("id") == rid:
                return msg, (time.perf_counter() - t0) * 1000

    def notify(self, method: str, params: dict | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def close(self) -> str:
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()
        self._err_thread.join(timeout=5)
        return b"".join(self._err).decode("utf-8", "replace")[-2000:]


def result_text(msg: dict | None) -> tuple[str, bool]:
    if msg is None:
        return "", True
    if "error" in msg:
        return json.dumps(msg["error"], separators=(",", ":")), True
    res = msg.get("result") or {}
    parts = []
    for c in res.get("content") or []:
        if isinstance(c, dict) and c.get("type") == "text":
            parts.append(c.get("text") or "")
        else:
            parts.append(json.dumps(c, separators=(",", ":")))
    if not parts and "structuredContent" in res:
        parts.append(json.dumps(res["structuredContent"], separators=(",", ":")))
    return "".join(parts), bool(res.get("isError"))


def main(argv: list[str]) -> int:
    out_path, calls_path = argv[1], argv[2]
    sep = argv.index("--")
    cmd = argv[sep + 1:]
    calls = json.loads(open(calls_path, encoding="utf-8").read())
    out: dict = {"server_cmd": cmd, "tools_list_json": None, "calls": [], "error": None}
    c = Client(cmd)
    try:
        init, init_ms = c.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                                 "clientInfo": {"name": "jcm-competitive-driver", "version": "1"}})
        out["initialize_ms"] = round(init_ms, 2)
        if init is None or "error" in init:
            out["error"] = f"initialize failed: {init}"
            return 0
        out["server_info"] = (init.get("result") or {}).get("serverInfo")
        c.notify("notifications/initialized")
        tl, tl_ms = c.request("tools/list")
        out["tools_list_ms"] = round(tl_ms, 2)
        tools = ((tl or {}).get("result") or {}).get("tools") or []
        out["tools_list_json"] = json.dumps([{"name": t.get("name"), "description": t.get("description"), "inputSchema": t.get("inputSchema")} for t in tools],
                                            separators=(",", ":"))
        out["tool_names"] = [t.get("name") for t in tools]
        for call in calls:
            msg, ms = c.request("tools/call", {"name": call["tool"], "arguments": call.get("args") or {}}, timeout=float(call.get("timeout_s", TIMEOUT_S)))
            text, is_err = result_text(msg)
            out["calls"].append({"id": call["id"], "tool": call["tool"], "args": call.get("args") or {}, "ms": round(ms, 2),
                                 "result_text": text, "is_error": is_err, "timed_out": msg is None})
        return 0
    finally:
        out["stderr_tail"] = c.close()
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(out, fh)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
