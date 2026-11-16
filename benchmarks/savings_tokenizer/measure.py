"""Measure bytes per token per language over the pinned corpus.

Reads the corpus manifest (``corpus.json``: repo, language, sha), walks each
clone with the indexer's own extension table and skip list, and counts
deterministic samples with one of two tokenizers:

- ``claude``: Anthropic's count_tokens endpoint (free) on MODEL.
- ``cl100k``: tiktoken's cl100k_base, locally, no network.

Both tokenizers count the SAME chunks, so the two tables are comparable row
for row. ``src/jcodemunch_mcp/savings_tokenizer.py`` holds the published
figures; ``tests/test_savings_tokenizer_table.py`` fails if they drift from
``<tokenizer>_table.json`` here.

Per repo: its manifest language gets up to PRIMARY_BYTES of source, and up to
SECONDARY_LANGS other languages get one chunk each. Files are taken in sorted
path order; files over the indexer's default max_file_size and files with a
NUL byte are skipped. Each chunk's per-request overhead (a one-character
message, measured once) is subtracted.

The API key is read at runtime (os.environ, then HKCU\\Environment) and is
never printed, logged or written. Writes ``<tokenizer>_counts.json`` (every
chunk, raw) and ``<tokenizer>_table.json`` (bytes per token per language plus
the overall figure).

Usage: measure.py <claude|cl100k> <corpus root> <out dir>

The corpus is cloned shallow at each manifest SHA:
``git clone --depth 1 --single-branch --no-tags https://github.com/<repo>``
then ``git fetch --depth 1 origin <sha> && git checkout <sha>`` if HEAD moved,
into ``<corpus root>/<owner>__<name>``.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

MODEL = "claude-opus-5-5"
URL = "https://api.anthropic.com/v1/messages/count_tokens"
CHUNK_BYTES = 300_000
PRIMARY_BYTES = 1_200_000
SECONDARY_LANGS = 3
MAX_FILE = 512_000
MIN_REPOS_FOR_ROW = 2


def _key():
    k = os.environ.get("ANTHROPIC_API_KEY")
    if k:
        return k
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as h:
            return winreg.QueryValueEx(h, "ANTHROPIC_API_KEY")[0] or None
    except OSError:
        return None


TOKENIZER = sys.argv[1] if len(sys.argv) > 1 else ""
if TOKENIZER not in ("claude", "cl100k"):
    print(__doc__)
    sys.exit(2)
KEY = None
ENC = None
if TOKENIZER == "claude":
    KEY = _key()
    if not KEY:
        print("ANTHROPIC_API_KEY: not set; nothing counted")
        sys.exit(2)
else:
    import tiktoken

    ENC = tiktoken.get_encoding("cl100k_base")
REQUESTS = 0


def count(text: str) -> int:
    global REQUESTS
    if ENC is not None:
        REQUESTS += 1
        return len(ENC.encode(text, disallowed_special=()))
    body = json.dumps(
        {"model": MODEL, "messages": [{"role": "user", "content": text}]}
    ).encode("utf-8")
    for attempt in range(8):
        req = urllib.request.Request(
            URL,
            data=body,
            method="POST",
            headers={
                "x-api-key": KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                REQUESTS += 1
                return json.loads(r.read())["input_tokens"]
        except urllib.error.HTTPError as e:
            if e.code in (429, 529) or e.code >= 500:
                time.sleep(min(float(e.headers.get("retry-after") or 2**attempt), 60))
                continue
            try:
                etype = json.loads(e.read()).get("error", {}).get("type")
            except Exception:
                etype = None
            raise SystemExit(f"count_tokens failed: HTTP {e.code} {etype}")
    raise SystemExit("count_tokens failed: retries exhausted")


from jcodemunch_mcp.parser.languages import LANGUAGE_EXTENSIONS  # noqa: E402
from jcodemunch_mcp.security import _SKIP_DIRECTORY_NAMES  # noqa: E402

SKIP = set(_SKIP_DIRECTORY_NAMES) | {".git"}


def files_by_language(root: Path) -> dict[str, list[Path]]:
    out: dict[str, list[Path]] = {}
    for dp, dns, fns in os.walk(root):
        dns[:] = sorted(d for d in dns if d not in SKIP)
        for fn in sorted(fns):
            lang = LANGUAGE_EXTENSIONS.get(Path(fn).suffix.lower())
            if lang:
                out.setdefault(lang, []).append(Path(dp) / fn)
    return out


def read_ok(p: Path) -> str | None:
    try:
        b = p.read_bytes()
    except OSError:
        return None
    if not b or len(b) > MAX_FILE or b"\x00" in b:
        return None
    return b.decode("utf-8", "replace")


def chunks(paths: list[Path], budget: int):
    buf, size, used = [], 0, 0
    for p in paths:
        t = read_ok(p)
        if t is None:
            continue
        b = len(t.encode("utf-8"))
        if used + b > budget:
            break
        if buf and size + b > CHUNK_BYTES:
            yield buf
            buf, size = [], 0
        buf.append(t)
        size += b
        used += b
    if buf:
        yield buf


def main(corpus: Path, out_dir: Path) -> None:
    manifest = json.loads(
        (Path(__file__).parent / "corpus.json").read_text(encoding="utf-8")
    )
    overhead = count("x") - 1 if TOKENIZER == "claude" else 0
    rows = []
    for m in manifest:
        root = corpus / m["repo"].replace("/", "__")
        by_lang = files_by_language(root)
        primary = m["language"]
        others = sorted(
            (l for l in by_lang if l != primary), key=lambda l: -len(by_lang[l])
        )[:SECONDARY_LANGS]
        plan = [(primary, PRIMARY_BYTES)] + [(l, CHUNK_BYTES) for l in others]
        for lang, budget in plan:
            for buf in chunks(by_lang.get(lang, []), budget):
                text = "\n".join(buf)
                n = max(0, count(text) - overhead)
                rows.append(
                    {
                        "repo": m["repo"],
                        "sha": m["sha"],
                        "language": lang,
                        "files": len(buf),
                        "bytes": len(text.encode("utf-8")),
                        "input_tokens": n,
                    }
                )
        print(f"{m['repo']:32} requests so far {REQUESTS}", flush=True)
    table = {}
    for lang in sorted({r["language"] for r in rows}):
        rs = [r for r in rows if r["language"] == lang]
        repos = sorted({r["repo"] for r in rs})
        b, t = sum(r["bytes"] for r in rs), sum(r["input_tokens"] for r in rs)
        per_repo = {}
        for rp in repos:
            rb = sum(r["bytes"] for r in rs if r["repo"] == rp)
            rt = sum(r["input_tokens"] for r in rs if r["repo"] == rp)
            per_repo[rp] = round(rb / rt, 3) if rt else None
        table[lang] = {
            "bytes": b,
            "tokens": t,
            "bytes_per_token": round(b / t, 3) if t else None,
            "repos": len(repos),
            "per_repo": per_repo,
            "published": len(repos) >= MIN_REPOS_FOR_ROW,
        }
    b_all, t_all = sum(r["bytes"] for r in rows), sum(r["input_tokens"] for r in rows)
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "tokenizer": TOKENIZER,
        "model": MODEL if TOKENIZER == "claude" else "cl100k_base",
        "overhead_tokens": overhead,
        "requests": REQUESTS,
        "chunk_bytes": CHUNK_BYTES,
        "primary_bytes": PRIMARY_BYTES,
        "secondary_langs": SECONDARY_LANGS,
        "max_file": MAX_FILE,
        "min_repos_for_row": MIN_REPOS_FOR_ROW,
    }
    (out_dir / f"{TOKENIZER}_counts.json").write_text(
        json.dumps({"meta": meta, "chunks": rows}, indent=1), encoding="utf-8"
    )
    (out_dir / f"{TOKENIZER}_table.json").write_text(
        json.dumps(
            {
                "meta": meta,
                "overall": {
                    "bytes": b_all,
                    "tokens": t_all,
                    "bytes_per_token": round(b_all / t_all, 3),
                },
                "languages": table,
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    print("overall bytes/token", round(b_all / t_all, 3), "requests", REQUESTS)
    for lang, v in table.items():
        print(
            f"{lang:14} {v['bytes_per_token']}  repos={v['repos']} published={v['published']}"
        )


if __name__ == "__main__":
    main(Path(sys.argv[2]), Path(sys.argv[3]))
