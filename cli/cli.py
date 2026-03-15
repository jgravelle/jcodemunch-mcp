#!/usr/bin/env python3
"""jCodeMunch CLI — thin shell over the shared ~/.code-index/ store.

Prefer the MCP interface for AI-agent use. This is for pipelines and terminals.
"""
import argparse, json, sys
sys.path.insert(0, __import__("os").path.join(__import__("os").path.dirname(__file__), "..", "src"))

from jcodemunch_mcp.tools.list_repos      import list_repos
from jcodemunch_mcp.tools.index_folder    import index_folder
from jcodemunch_mcp.tools.index_repo      import index_repo
from jcodemunch_mcp.tools.get_repo_outline import get_repo_outline
from jcodemunch_mcp.tools.get_file_outline import get_file_outline
from jcodemunch_mcp.tools.search_symbols   import search_symbols
from jcodemunch_mcp.tools.get_symbol       import get_symbol
from jcodemunch_mcp.tools.search_text      import search_text
from jcodemunch_mcp.tools.get_file_content import get_file_content
from jcodemunch_mcp.tools.invalidate_cache import invalidate_cache

p = argparse.ArgumentParser(description="jCodeMunch CLI")
sub = p.add_subparsers(dest="cmd", required=True)

sub.add_parser("list")

ix = sub.add_parser("index")
ix.add_argument("target", help="Local path or owner/repo")

ol = sub.add_parser("outline")
ol.add_argument("repo")
ol.add_argument("file", nargs="?")

ss = sub.add_parser("search")
ss.add_argument("repo")
ss.add_argument("query")
ss.add_argument("--kind"); ss.add_argument("--lang"); ss.add_argument("-n", type=int, default=10)

gs = sub.add_parser("get")
gs.add_argument("repo"); gs.add_argument("symbol_id")

st = sub.add_parser("text")
st.add_argument("repo"); st.add_argument("query")
st.add_argument("-C", type=int, default=2, dest="context_lines")

fc = sub.add_parser("file")
fc.add_argument("repo"); fc.add_argument("file_path")
fc.add_argument("--start", type=int); fc.add_argument("--end", type=int)

iv = sub.add_parser("invalidate")
iv.add_argument("repo")

args = p.parse_args()

def out(result): print(json.dumps(result, indent=2))

if   args.cmd == "list":     out(list_repos())
elif args.cmd == "index":
    t = args.target
    out(index_folder(t) if "/" not in t or t.startswith("/") or (len(t) > 1 and t[1] == ":") else index_repo(t))
elif args.cmd == "outline":  out(get_file_outline(args.repo, args.file) if args.file else get_repo_outline(args.repo))
elif args.cmd == "search":   out(search_symbols(args.repo, args.query, kind=args.kind, language=args.lang, max_results=args.n))
elif args.cmd == "get":      out(get_symbol(args.repo, args.symbol_id))
elif args.cmd == "text":     out(search_text(args.repo, args.query, context_lines=args.context_lines))
elif args.cmd == "file":     out(get_file_content(args.repo, args.file_path, start_line=args.start, end_line=args.end))
elif args.cmd == "invalidate": out(invalidate_cache(args.repo))
