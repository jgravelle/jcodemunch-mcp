"""Persistent token savings tracker.

Records cumulative tokens saved across all tool calls by comparing
raw file sizes against actual MCP response sizes.

Stored in ~/.code-index/_savings.json — a single small JSON file.
No API calls, no file reads — only os.stat for file sizes.

Community meter: token savings are shared anonymously by default to the
global counter at https://j.gravelle.us. Only {"delta": N, "anon_id":
"<uuid>"} is sent — never code, paths, repo names, or anything identifying.
Set JCODEMUNCH_SHARE_SAVINGS=0 to disable.
"""

import json
import os
import threading
import uuid
from pathlib import Path
from typing import Optional, Any

_SAVINGS_FILE = "_savings.json"
_BYTES_PER_TOKEN = 4  # ~4 bytes per token (rough but consistent)
_TELEMETRY_URL = "https://j.gravelle.us/APIs/savings/post.php"

# Input token pricing ($ per token). Update as models reprice.
PRICING = {
    "claude_opus":  15.00 / 1_000_000,  # Claude Opus 4.6 — $15.00 / 1M input tokens
    "gpt5_latest":  10.00 / 1_000_000,  # GPT-5.2 (latest flagship GPT) — $10.00 / 1M input tokens
}


def _savings_path(base_path: Optional[str] = None) -> Path:
    # Keep CLI reporting and tool-side recording aligned by honoring
    # CODE_INDEX_PATH as the implicit base path when explicit base_path
    # is not provided.
    configured_base = base_path or os.environ.get("CODE_INDEX_PATH")
    root = Path(configured_base) if configured_base else Path.home() / ".code-index"
    root.mkdir(parents=True, exist_ok=True)
    return root / _SAVINGS_FILE


def _get_or_create_anon_id(data: dict) -> str:
    """Return the persistent anonymous install ID, creating it if absent."""
    if "anon_id" not in data:
        data["anon_id"] = str(uuid.uuid4())
    return data["anon_id"]


def _read_savings_data(path: Path) -> dict[str, Any]:
    """Load savings JSON robustly across platforms/encodings."""
    if not path.exists():
        return {}

    try:
        raw = path.read_bytes()
    except Exception:
        return {}

    if not raw:
        return {}

    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return json.loads(raw.decode(encoding))
        except Exception:
            continue

    return {}


def _write_savings_data(path: Path, data: dict[str, Any]) -> None:
    """Persist savings JSON using stable UTF-8 encoding."""
    try:
        path.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        pass


def _share_savings(delta: int, anon_id: str) -> None:
    """Fire-and-forget POST to the community meter. Never raises."""
    def _post() -> None:
        try:
            import httpx
            httpx.post(
                _TELEMETRY_URL,
                json={"delta": delta, "anon_id": anon_id},
                timeout=3.0,
            )
        except Exception:
            pass

    threading.Thread(target=_post, daemon=True).start()


def record_savings(tokens_saved: int, base_path: Optional[str] = None) -> int:
    """Add tokens_saved to the running total. Returns new cumulative total."""
    path = _savings_path(base_path)
    data = _read_savings_data(path)

    delta = max(0, tokens_saved)
    total = data.get("total_tokens_saved", 0) + delta
    data["total_tokens_saved"] = total

    if delta > 0 and os.environ.get("JCODEMUNCH_SHARE_SAVINGS", "1") != "0":
        anon_id = _get_or_create_anon_id(data)
        _share_savings(delta, anon_id)

    _write_savings_data(path, data)

    return total




def get_savings_report(base_path: Optional[str] = None) -> dict[str, Any]:
    """Return an enriched summary of token savings for CLI and dashboards."""
    path = _savings_path(base_path)
    data = _read_savings_data(path)

    total_tokens_saved = max(0, int(data.get("total_tokens_saved", 0) or 0))
    approx_raw_bytes_avoided = total_tokens_saved * _BYTES_PER_TOKEN
    context_windows = {
        "32k": round(total_tokens_saved / 32_000, 2),
        "128k": round(total_tokens_saved / 128_000, 2),
        "1m": round(total_tokens_saved / 1_000_000, 4),
    }

    return {
        "total_tokens_saved": total_tokens_saved,
        "approx_raw_bytes_avoided": approx_raw_bytes_avoided,
        "pricing_usd_per_token": PRICING,
        "total_cost_avoided": {
            model: round(total_tokens_saved * rate, 4)
            for model, rate in PRICING.items()
        },
        "equivalent_context_windows": context_windows,
        "telemetry_enabled": os.environ.get("JCODEMUNCH_SHARE_SAVINGS", "1") != "0",
        "anon_id_present": bool(data.get("anon_id")),
        "savings_file": str(path),
    }

def get_total_saved(base_path: Optional[str] = None) -> int:
    """Return the current cumulative total without modifying it."""
    path = _savings_path(base_path)
    return _read_savings_data(path).get("total_tokens_saved", 0)


def estimate_savings(raw_bytes: int, response_bytes: int) -> int:
    """Estimate tokens saved: (raw - response) / bytes_per_token."""
    return max(0, (raw_bytes - response_bytes) // _BYTES_PER_TOKEN)


def cost_avoided(tokens_saved: int, total_tokens_saved: int) -> dict:
    """Return cost avoided estimates for this call and the running total.

    Returns a dict ready to be merged into a _meta envelope:
        cost_avoided:       {claude_opus: float, gpt5_latest: float}
        total_cost_avoided: {claude_opus: float, gpt5_latest: float}

    Values are in USD, rounded to 4 decimal places.
    """
    return {
        "cost_avoided": {
            model: round(tokens_saved * rate, 4)
            for model, rate in PRICING.items()
        },
        "total_cost_avoided": {
            model: round(total_tokens_saved * rate, 4)
            for model, rate in PRICING.items()
        },
    }
