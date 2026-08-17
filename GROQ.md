# jCodeMunch + Groq — Instant Codebase Intelligence

Use [Groq's](https://groq.com) ultra-fast inference with jCodeMunch's token-efficient code retrieval to answer questions about any codebase in seconds.

Groq's **Remote MCP** support means their API connects directly to a jCodeMunch server — tool discovery, execution, and response synthesis all happen in a single API call. No local setup required.

## Quick Start

### Python

```python
import os
from openai import OpenAI

client = OpenAI(
    api_key=os.environ["GROQ_API_KEY"],
    base_url="https://api.groq.com/openai/v1",
)

response = client.responses.create(
    model="llama-3.3-70b-versatile",
    input="What does the parse_file function do in jgravelle/jcodemunch-mcp?",
    tools=[{
        "type": "mcp",
        "server_label": "jcodemunch",
        "server_url": "https://YOUR_JCODEMUNCH_URL",
        "headers": {"Authorization": "Bearer YOUR_TOKEN"},
        "server_description": "Code intelligence: search symbols, get source, analyze dependencies across any indexed GitHub repo.",
        "require_approval": "never",
    }],
)

for item in response.output:
    if hasattr(item, "text"):
        print(item.text)
```

### cURL

```bash
curl -s https://api.groq.com/openai/v1/responses \
  -H "Authorization: Bearer $GROQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "llama-3.3-70b-versatile",
    "input": "What does the parse_file function do in jgravelle/jcodemunch-mcp?",
    "tools": [{
      "type": "mcp",
      "server_label": "jcodemunch",
      "server_url": "https://YOUR_JCODEMUNCH_URL",
      "headers": {"Authorization": "Bearer YOUR_TOKEN"},
      "server_description": "Code intelligence: search symbols, get source, analyze dependencies across any indexed GitHub repo.",
      "require_approval": "never"
    }]
  }'
```

### JavaScript

```javascript
import OpenAI from "openai";

const client = new OpenAI({
  apiKey: process.env.GROQ_API_KEY,
  baseURL: "https://api.groq.com/openai/v1",
});

const response = await client.responses.create({
  model: "llama-3.3-70b-versatile",
  input: "What does the parse_file function do in jgravelle/jcodemunch-mcp?",
  tools: [{
    type: "mcp",
    server_label: "jcodemunch",
    server_url: "https://YOUR_JCODEMUNCH_URL",
    headers: { Authorization: "Bearer YOUR_TOKEN" },
    server_description: "Code intelligence: search symbols, get source, analyze dependencies across any indexed GitHub repo.",
    require_approval: "never",
  }],
});

for (const item of response.output) {
  if (item.type === "message") {
    for (const block of item.content) {
      if (block.type === "output_text") console.log(block.text);
    }
  }
}
```

## Allowed-Tools Presets

Groq's `allowed_tools` parameter lets you restrict which jCodeMunch tools the model can call. This improves focus and reduces latency. Pick the preset that matches your use case:

### Explore (fast discovery)

Best for: browsing a new codebase, finding entry points, understanding structure.

```json
"allowed_tools": [
    "list_repos", "resolve_repo", "get_repo_outline",
    "get_file_tree", "get_file_outline",
    "search_symbols", "get_symbol_source"
]
```

### Deep Analysis

Best for: answering detailed questions, understanding call chains, assessing impact.

```json
"allowed_tools": [
    "list_repos", "resolve_repo", "get_repo_outline",
    "get_file_tree", "get_file_outline",
    "search_symbols", "get_symbol_source",
    "get_ranked_context", "get_context_bundle",
    "get_blast_radius", "get_call_hierarchy",
    "find_importers", "find_references"
]
```

### Code Review

Best for: understanding changes, checking impact, validating renames.

```json
"allowed_tools": [
    "list_repos", "resolve_repo",
    "search_symbols", "get_symbol_source",
    "get_ranked_context", "get_context_bundle",
    "get_changed_symbols", "get_blast_radius",
    "get_impact_preview", "check_rename_safe"
]
```

### Full (all tools)

Omit `allowed_tools` entirely to expose all 40+ jCodeMunch tools. Best for power users who want the model to choose freely.

## Available Tools Reference

| Category | Tools |
|----------|-------|
| **Indexing** | `index_repo`, `index_folder`, `summarize_repo`, `index_file` |
| **Discovery** | `list_repos`, `resolve_repo`, `suggest_queries`, `get_repo_outline`, `get_file_tree`, `get_file_outline` |
| **Search & Retrieval** | `search_symbols`, `get_symbol_source`, `get_context_bundle`, `get_file_content`, `search_text`, `search_columns`, `get_ranked_context` |
| **Relationships** | `find_importers`, `find_references`, `check_references`, `get_dependency_graph`, `get_class_hierarchy`, `get_related_symbols`, `get_call_hierarchy` |
| **Impact & Safety** | `get_blast_radius`, `check_rename_safe`, `get_impact_preview`, `get_changed_symbols`, `plan_refactoring` |
| **Architecture** | `get_dependency_cycles`, `get_coupling_metrics`, `get_layer_violations`, `get_extraction_candidates`, `get_cross_repo_map` |
| **Quality & Metrics** | `get_symbol_complexity`, `get_churn_rate`, `get_hotspots`, `get_repo_health`, `get_symbol_importance`, `find_dead_code`, `get_dead_code_v2`, `get_untested_symbols` |

## Hosting Your Own Endpoint

Groq's remote MCP requires an HTTPS endpoint. jCodeMunch supports SSE and streamable-http transports out of the box.

### Option A: Docker + Caddy (recommended for self-hosting)

```bash
git clone https://github.com/jgravelle/jcodemunch-mcp.git
cd jcodemunch-mcp

# Set your domain and bearer token
export DOMAIN=mcp.example.com
export JCODEMUNCH_HTTP_TOKEN=your-secret-token

docker compose up -d
```

Caddy handles TLS certificates automatically. Your endpoint will be available at `https://mcp.example.com`.

### Option B: Cloud deploy (Railway / Fly.io / Render)

```bash
# Railway
railway init
railway up

# Fly.io
fly launch --image jcodemunch-mcp
fly secrets set JCODEMUNCH_HTTP_TOKEN=your-secret-token
```

Set these environment variables on your cloud platform:

| Variable | Value |
|----------|-------|
| `JCODEMUNCH_TRANSPORT` | `sse` |
| `JCODEMUNCH_HOST` | `0.0.0.0` |
| `JCODEMUNCH_PORT` | `8901` |
| `JCODEMUNCH_HTTP_TOKEN` | Your bearer token |
| `JCODEMUNCH_RATE_LIMIT` | `60` (requests/min) |

### Option C: Direct (local testing, no TLS)

```bash
pip install jcodemunch-mcp
JCODEMUNCH_HTTP_TOKEN=test123 jcodemunch-mcp serve --transport sse --host 0.0.0.0
```

> Note: Groq requires HTTPS for remote MCP. Use [ngrok](https://ngrok.com) or [Cloudflare Tunnels](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/) to expose a local server with TLS for testing.

### Pre-indexing repos

Once your server is running, index repos so they're ready for queries:

```python
# Via Groq (the model will call index_repo for you)
response = client.responses.create(
    model="llama-3.3-70b-versatile",
    input="Index the repository pallets/flask",
    tools=[mcp_tool],
)

# Or directly via the MCP endpoint (faster, no LLM round-trip)
# POST to your jCodeMunch SSE endpoint with the index_repo tool call
```

## Recommended Groq Models

| Model | Speed | Best for |
|-------|-------|----------|
| `llama-3.3-70b-versatile` | 280 tps | Detailed analysis, code review — best quality |
| `openai/gpt-oss-120b` | 500 tps | Complex reasoning with tool use |
| `openai/gpt-oss-20b` | 1000 tps | Fast exploration, simple lookups |
| `llama-3.1-8b-instant` | 560 tps | High-throughput, batch queries, demos with rate limit headroom |

## Validation

Run the included validation script to verify your setup:

```bash
export GROQ_API_KEY=your-groq-key
export JCODEMUNCH_MCP_URL=https://mcp.example.com
export JCODEMUNCH_MCP_TOKEN=your-mcp-token

python examples/groq_validate.py
python examples/groq_validate.py --repo pallets/flask --verbose
```

## Architecture

```
Your App                     Groq API                     jCodeMunch Server
────────                     ────────                     ──────────────────
POST /responses  ──────►  Discover tools via MCP  ──►  SSE endpoint (HTTPS)
  tools: [{mcp}]           Model selects tool(s)        Bearer token auth
                           Execute server-side    ──►  Tool handler (search, retrieve, analyze)
                           Receive result         ◄──  JSON response
                           Synthesize answer
  ◄──────────────────────  Stream final response
```

Key properties:
- **Single API call** — Groq handles all MCP orchestration server-side
- **No local install** — jCodeMunch runs on the remote server, not on the client
- **Token-efficient** — jCodeMunch returns only relevant symbols/context, not raw files
- **Fast** — Groq inference at 280-1000 tok/s means sub-second answer synthesis

---

### speedreview — AI Code Review GitHub Action

Get a structured PR review in under 5 seconds:

```yaml
# .github/workflows/speedreview.yml
- uses: jgravelle/jcodemunch-mcp/speedreview@v1.108.52
  with:
    groq_api_key: ${{ secrets.GROQ_API_KEY }}
```

For stricter supply-chain hygiene, pin to the tag's commit SHA instead of the
tag itself (`git ls-remote https://github.com/jgravelle/jcodemunch-mcp refs/tags/v1.108.52`).
The action installs pinned package versions by default and exposes
`jcodemunch_version` / `openai_version` inputs for override.

See **[speedreview/README.md](speedreview/README.md)** for full setup and configuration.

### gcm — Codebase Q&A CLI

Ask any question about any codebase. Get an answer in under 3 seconds.

```bash
pip install "jcodemunch-mcp[groq]"
export GROQ_API_KEY=gsk_...

# Ask about a GitHub repo (auto-indexes on first use)
gcm "how does authentication work?" --repo pallets/flask

# Ask about the current directory
gcm "where are the API routes defined?"

# Interactive chat mode
gcm --chat --repo facebook/react

# Use the fast 8B model
gcm "what does parse_file do?" --fast
```

Combines jCodeMunch's token-efficient retrieval (BM25 + PageRank) with Groq's 280+ tok/s inference for near-instant answers. See `gcm --help` for all options.

### gcm --voice — Voice-to-Codebase

Speak a question, hear the answer. Full audio loop: Whisper STT → retrieval → LLM → Orpheus TTS.

```bash
pip install "jcodemunch-mcp[groq-voice]"

# Voice conversation with a codebase
gcm --voice --repo pallets/flask

# Press Enter to start recording, Enter again to stop
# Or type a question directly as text fallback
```

Push-to-talk via Enter key. Caps answers to ~100 words for natural spoken delivery. Requires a microphone.

### gcm explain — Auto Repo Explainer

Generate a narrated explainer video for any codebase in a single command.

```bash
pip install "jcodemunch-mcp[groq-explain]"

# Generate a 60-second narrated explainer
gcm explain --repo pallets/flask -o flask-explainer.mp4

# With verbose timing
gcm explain --repo facebook/react -v
```

Pipeline: repo structure → LLM narration script → Orpheus TTS → Pillow slides → FFmpeg MP4. Requires FFmpeg on PATH.

### Choosing a TTS backend

Both `gcm --voice` and `gcm explain` use the default audio endpoint unless a TTS
backend is configured. Set `JCODEMUNCH_TTS_PROVIDER=minimax` to synthesize
speech through the MiniMax T2A endpoint instead:

```bash
export JCODEMUNCH_TTS_PROVIDER=minimax
export MINIMAX_API_KEY=...

# Optional — defaults shown
export JCODEMUNCH_MINIMAX_T2A_REGION=global_en   # or cn_zh
export JCODEMUNCH_MINIMAX_T2A_MODEL=speech-2.8-hd
export JCODEMUNCH_MINIMAX_T2A_VOICE=...          # voice_id; endpoint default if unset
```

The `global_en` and `cn_zh` regions are separate deployments and an API key is
valid on one of them only, so an unrecognised region is rejected rather than
quietly resolved to the default. Available models are `speech-2.8`, `speech-2.6`,
`speech-02` and `speech-01`, each in an `-hd` and a `-turbo` variant.

---
