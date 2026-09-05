"""jCodeMunch's in-container worker (docs/competitive/DESIGN.md s1.4; FINDINGS CF-3).

purpose:  index /corpus into CODE_INDEX_PATH and answer every task in
          /out/tasks.json with the published workflow, writing
          /out/answers.json; the same file is also run on the host when the
          sandbox is `none` (tests, a box without Docker), so the two modes
          run one code path
invokes:  index_folder with its shipped defaults (AI summaries off, R28),
          search_symbols(max_results=5, detail_level="standard") +
          get_symbol_source on the top 3 (R27) for P1 and T,
          find_references for P2, find_importers for P4; the live
          tools/list weight from server._build_tools_list (CF-6)
produces: /out/answers.json {index, answers, tools_list_chars}
refuses:  nothing; a task that raises is recorded as an error on its row
pinned:   the checkout the image was built from
fairness: DESIGN s1.4; runs under the same sandbox flags as every competitor

Usage: python jcm_worker.py <corpus> <store> <tasks.json> <answers.json>
"""

from __future__ import annotations

import json
import sys
import time

SEARCH_MAX_RESULTS = 5
SYMBOLS_FETCHED = 3


def ser(o) -> str:
    return json.dumps(o, separators=(",", ":"), default=str)


def main(argv: list[str]) -> int:
    corpus, store, tasks_json, answers_json = argv[1:5]
    tasks = json.loads(open(tasks_json, encoding="utf-8").read())
    from jcodemunch_mcp.tools.index_folder import index_folder

    t = time.perf_counter()
    r = index_folder(path=corpus, use_ai_summaries=False, storage_path=store)
    idx = {"secs": time.perf_counter() - t, "repo": r.get("repo"), "success": r.get("success"),
           "file_count": r.get("file_count"), "symbol_count": r.get("symbol_count"), "error": r.get("error")}
    out: dict = {"index": idx, "answers": {}, "tools_list_chars": None}
    try:
        from jcodemunch_mcp import server as _server

        tools = _server._build_tools_list()
        out["tools_list_chars"] = len(ser([{"name": x.name, "description": x.description, "inputSchema": x.inputSchema} for x in tools]))
        out["tools_list_json"] = ser([{"name": x.name, "description": x.description, "inputSchema": x.inputSchema} for x in tools])
    except Exception as e:  # reported, never fatal
        out["tools_list_error"] = f"{type(e).__name__}: {e}"
    if idx["success"] and idx["repo"]:
        from jcodemunch_mcp.tools.search_symbols import search_symbols
        from jcodemunch_mcp.tools.get_symbol import get_symbol_source
        from jcodemunch_mcp.tools.find_references import find_references
        from jcodemunch_mcp.tools.find_importers import find_importers

        repo = idx["repo"]
        for task in tasks:
            cat, q = task["category"], task["query"]
            payload, lat, cited, err = [], [], [], None
            try:
                if cat in ("P1", "T"):
                    t0 = time.perf_counter()
                    s = search_symbols(repo=repo, query=q, max_results=SEARCH_MAX_RESULTS, detail_level="standard")
                    lat.append((time.perf_counter() - t0) * 1000)
                    payload.append(ser(s))
                    rows = s.get("results") or s.get("symbols") or []
                    for row in rows:
                        f, ln = row.get("file") or row.get("file_path"), row.get("line") or row.get("start_line")
                        if f and ln:
                            cited.append([f, int(ln)])
                    ids = [x.get("id") or x.get("symbol_id") for x in rows if x.get("id") or x.get("symbol_id")][:SYMBOLS_FETCHED]
                    for sid in ids:
                        t0 = time.perf_counter()
                        g = get_symbol_source(repo=repo, symbol_id=sid)
                        lat.append((time.perf_counter() - t0) * 1000)
                        payload.append(ser(g))
                        f, ln = g.get("file") or g.get("file_path"), g.get("start_line") or g.get("line")
                        if f and ln:
                            cited.append([f, int(ln)])
                elif cat == "P2":
                    t0 = time.perf_counter()
                    s = find_references(repo=repo, identifier=q)
                    lat.append((time.perf_counter() - t0) * 1000)
                    payload.append(ser(s))
                    for key in ("references", "results", "importers"):
                        for row in s.get(key) or []:
                            f = row.get("file") or row.get("file_path") or row.get("path")
                            ln = row.get("line") or row.get("start_line") or 0
                            if f:
                                cited.append([f, int(ln or 0)])
                elif cat == "P4":
                    t0 = time.perf_counter()
                    s = find_importers(repo=repo, file_path=q)
                    lat.append((time.perf_counter() - t0) * 1000)
                    payload.append(ser(s))
                    for row in s.get("importers") or s.get("results") or []:
                        f = row.get("file") or row.get("file_path") or row.get("path") if isinstance(row, dict) else row
                        if f:
                            cited.append([f, 0])
                else:
                    err = "category not answered by this adapter"
            except Exception as e:  # the row fails, not the run
                err = f"{type(e).__name__}: {e}"
            out["answers"][task["id"]] = {"payload": "".join(payload), "calls": len(lat), "latency_ms": lat, "cited": cited, "error": err}
    with open(answers_json, "w", encoding="utf-8") as fh:
        json.dump(out, fh)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
