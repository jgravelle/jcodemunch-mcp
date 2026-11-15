"""Claude tokens per cl100k token, per language, from the two count files.

Both ``claude_counts.json`` and ``cl100k_counts.json`` count the SAME chunks
(``measure.py`` walks the corpus identically for both tokenizers), so the
ratio of their token totals per language is the factor that turns a local
cl100k count into a Claude estimate. Writes ``claude_over_cl100k.json``:
per language the ratio, the repos behind it and whether it is published
(two repos or more, the same rule as the bytes/token tables), plus the overall
ratio used where a language is not published.

``src/jcodemunch_mcp/savings_tokenizer.py`` ships the published ratios and
``tests/test_savings_tokenizer_table.py`` fails if they drift from this file.

Usage: ratios.py   (reads and writes next to this file)
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent
MIN_REPOS_FOR_ROW = 2


def main() -> None:
    claude = json.loads((HERE / "claude_counts.json").read_text(encoding="utf-8"))[
        "chunks"
    ]
    cl100k = json.loads((HERE / "cl100k_counts.json").read_text(encoding="utf-8"))[
        "chunks"
    ]
    assert len(claude) == len(cl100k), "the two runs did not count the same chunks"
    by_lang: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    repos: dict[str, set] = defaultdict(set)
    per_repo: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    for a, b in zip(claude, cl100k):
        key = (a["repo"], a["language"], a["bytes"], a["files"])
        assert key == (b["repo"], b["language"], b["bytes"], b["files"]), key
        by_lang[a["language"]][0] += a["input_tokens"]
        by_lang[a["language"]][1] += b["input_tokens"]
        repos[a["language"]].add(a["repo"])
        per_repo[(a["language"], a["repo"])][0] += a["input_tokens"]
        per_repo[(a["language"], a["repo"])][1] += b["input_tokens"]
    languages = {}
    for lang in sorted(by_lang):
        c, o = by_lang[lang]
        languages[lang] = {
            "claude_tokens": c,
            "cl100k_tokens": o,
            "ratio": round(c / o, 3) if o else None,
            "repos": len(repos[lang]),
            "per_repo": {
                r: round(per_repo[(lang, r)][0] / per_repo[(lang, r)][1], 3)
                for r in sorted(repos[lang])
                if per_repo[(lang, r)][1]
            },
            "published": len(repos[lang]) >= MIN_REPOS_FOR_ROW,
        }
    c_all = sum(v[0] for v in by_lang.values())
    o_all = sum(v[1] for v in by_lang.values())
    out = {
        "meta": {
            "from": ["claude_counts.json", "cl100k_counts.json"],
            "min_repos_for_row": MIN_REPOS_FOR_ROW,
        },
        "overall": {
            "claude_tokens": c_all,
            "cl100k_tokens": o_all,
            "ratio": round(c_all / o_all, 3),
        },
        "languages": languages,
    }
    (HERE / "claude_over_cl100k.json").write_text(
        json.dumps(out, indent=1) + "\n", encoding="utf-8"
    )
    print("overall", out["overall"]["ratio"])
    for lang, row in languages.items():
        if row["published"]:
            print(f"{lang:12} {row['ratio']}  repos={row['repos']}")


if __name__ == "__main__":
    main()
