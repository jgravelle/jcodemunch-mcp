"""Do query-text features predict search_symbols vs search_text? Measured: no.

v1.108.253 fixed `route`'s @3 collapse on emitted task strings and named the
remaining gap without guessing at it: a rank-1 discriminator, "most likely
'does the task name an identifier-shaped token'". This script tests that
hypothesis before anyone builds a rule on it.

⚠⚠ THE PREDICATE IS DECLARED BEFORE ANY LABEL IS READ. That ordering is the
only thing separating a test from a search for a pattern that fits.

Two hypotheses tested, both refuted (2026-08-20, see
route_discriminator_results.json):

  H1 identifier-shape  - "does the task name an identifier-shaped token"
  H2 verb/intent       - "where is X defined" vs "everywhere X appears"

⚠⚠ BOTH FAIL ON COVERAGE, NOT PURITY, and that is the finding. H1 fires on
~15% of cases and H2 on 5-14%; the 85-95% residue sits at ~50% purity in both.
**The information needed to route these requests is not in the query string.**

Usage:
    python measure_route_discriminators.py [--corpus path/to/route_candidates_v0.1.csv]

Without --corpus the file is fetched from the published dataset and its sha256
is verified against the digest this measurement was taken over.
"""

from __future__ import annotations

import sys
import argparse
import csv
import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path

CORPUS_URL = (
    "https://raw.githubusercontent.com/rknighton/jcm-route-benchmark-corpus"
    "/main/data/route_candidates_v0.1.csv"
)
# The digest emitted_task_cases.json recorded, so both measurements are over
# one corpus rather than two spellings of a moving one.
CORPUS_SHA256 = "25d09f6ae9e8e668d1a3b9a30755cf8dbd7d063ce08868529cc152fdfe84b86e"

HERE = Path(__file__).parent
SEARCH_FAMILY = {"search_text", "search_symbols"}

# ── H1: identifier shape. Declared before labels. ─────────────────────────
PATTERNS = {
    "snake_case": re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b"),
    "camelCase": re.compile(r"\b[a-z]+[A-Z][A-Za-z0-9]*\b"),
    "PascalCase": re.compile(r"\b[A-Z][a-z0-9]+[A-Z][A-Za-z0-9]*\b"),
    "CONSTANT_CASE": re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b"),
    "call_form": re.compile(r"\b\w+\(\s*\)"),
    "dotted_path": re.compile(r"\b[A-Za-z_]\w*\.[A-Za-z_]\w*\b"),
    "backticked": re.compile(r"`[^`\n]+`"),
}


def shape_hits(text: str) -> list[str]:
    return sorted(name for name, pat in PATTERNS.items() if pat.search(text or ""))


def is_identifier_shaped(text: str) -> bool:
    return bool(shape_hits(text))


# ── H2: verb / intent. Declared before labels. ────────────────────────────
#
# ⚠ Weaker prior than H1: v1.108.253 ALREADY ships a content rule covering
# occurrence phrasing, so separation on that half partly measures the fix.
DEFINITION = {
    "defined": re.compile(r"\b(?:is|are|was|it)?\s*defin(?:ed|ition)\b", re.I),
    "declared": re.compile(r"\bdeclar(?:ed|ation)\b", re.I),
    "implementation": re.compile(r"\bimplement(?:ation|ed)\s+(?:of|in|by)\b", re.I),
    "the_kind_named": re.compile(
        r"\b(?:the\s+)?(?:function|class|method|struct|interface|enum|type|module"
        r"|component)\s+(?:called\s+|named\s+)?\w",
        re.I,
    ),
    "where_lives": re.compile(
        r"\bwhere\s+(?:does|do|is|are)\b.{0,30}\b(?:live|located|come from|defined)\b", re.I
    ),
    "source_of": re.compile(r"\b(?:source|body|signature)\s+(?:of|for)\b", re.I),
}

OCCURRENCE = {
    "everywhere": re.compile(
        r"\b(?:every\s*where|everywhere|every\s+place|all\s+the\s+places|all\s+places)\b", re.I
    ),
    "occurrences": re.compile(r"\boccurrenc\w*\b", re.I),
    "all_uses": re.compile(
        r"\b(?:all|every)\s+(?:the\s+)?(?:uses?|usages?|calls?|instances?|references?"
        r"|mentions?)\b",
        re.I,
    ),
    "anywhere": re.compile(r"\banywhere\b", re.I),
    "grep": re.compile(r"\bgrep\b", re.I),
    "any_file_with": re.compile(
        r"\b(?:any|which|what)\s+files?\s+(?:that\s+)?(?:contain|has|have|mention)\w*\b", re.I
    ),
}


def verb_class(text: str) -> str:
    """definition | occurrence | both | neither."""
    d = any(p.search(text or "") for p in DEFINITION.values())
    o = any(p.search(text or "") for p in OCCURRENCE.values())
    if d and not o:
        return "definition"
    if o and not d:
        return "occurrence"
    return "both" if d else "neither"


def _score_verb(pairs: list[tuple[str, str]]) -> dict:
    """definition -> symbols, occurrence -> text, rest -> majority guess."""
    tab = Counter(pairs)
    n = len(pairs)
    covered = correct = 0
    table = {}
    for cls in ("definition", "occurrence", "both", "neither"):
        sy, st = tab[(cls, "search_symbols")], tab[(cls, "search_text")]
        if sy + st:
            table[cls] = {"search_symbols": sy, "search_text": st}
        if cls == "definition":
            covered += sy + st
            correct += sy
        elif cls == "occurrence":
            covered += sy + st
            correct += st
    rest = [(c, g) for c, g in pairs if c in ("neither", "both")]
    rest_correct = max(
        sum(1 for _, g in rest if g == "search_symbols"),
        sum(1 for _, g in rest if g == "search_text"),
    )
    symbols = sum(1 for _, g in pairs if g == "search_symbols")
    majority = max(symbols, n - symbols)
    acc = (correct + rest_correct) / n * 100
    return {
        "n": n,
        "table": table,
        "coverage_pct": round(covered / n * 100, 1),
        "rule_accuracy_pct": round(acc, 1),
        "majority_floor_pct": round(majority / n * 100, 1),
        "lift_over_floor_pts": round(acc - majority / n * 100, 1),
        "covered_correct": f"{correct}/{covered}",
    }


# ── Measurement ───────────────────────────────────────────────────────────

def _binom_ge(k: int, n: int, p: float) -> float:
    """P(at least k of n) under the null. Small buckets need this stated."""
    return sum(math.comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(k, n + 1))


def _score(pairs: list[tuple[bool, str]]) -> dict:
    """pairs of (shaped, gold). The rule under test: shaped -> symbols."""
    tab = Counter(pairs)
    n = len(pairs)
    correct = tab[(True, "search_symbols")] + tab[(False, "search_text")]
    symbols = sum(tab[(s, "search_symbols")] for s in (True, False))
    majority = max(symbols, n - symbols)
    fires = sum(tab[(True, g)] for g in SEARCH_FAMILY)
    return {
        "n": n,
        "table": {
            "shaped": {g: tab[(True, g)] for g in sorted(SEARCH_FAMILY)},
            "unshaped": {g: tab[(False, g)] for g in sorted(SEARCH_FAMILY)},
        },
        "rule_accuracy_pct": round(correct / n * 100, 1),
        "majority_floor_pct": round(majority / n * 100, 1),
        "lift_over_floor_pts": round((correct - majority) / n * 100, 1),
        "coverage_pct": round(fires / n * 100, 1),
        "residue_purity_pct": round(
            max(tab[(False, g)] for g in SEARCH_FAMILY) / max(n - fires, 1) * 100, 1
        ),
    }


def _load_corpus(path: str | None) -> list[dict]:
    if path:
        raw = Path(path).read_bytes()
    else:
        import urllib.request

        with urllib.request.urlopen(CORPUS_URL) as resp:  # noqa: S310 (pinned host)
            raw = resp.read()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != CORPUS_SHA256:
        raise SystemExit(
            f"corpus digest {digest} != {CORPUS_SHA256}. The dataset moved; this "
            "measurement's numbers are not comparable to it until re-run."
        )
    return list(csv.DictReader(raw.decode("utf-8").splitlines()))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default=None)
    ap.add_argument("--write", action="store_true", help="update the results artifact")
    args = ap.parse_args()

    cases = json.loads(
        (HERE / "emitted_task_cases.json").read_text(encoding="utf-8")
    )["cases"]
    emitted = [c for c in cases if c["gold_primary"] in SEARCH_FAMILY]
    emitted_result = _score(
        [(is_identifier_shaped(c["emitted_task"]), c["gold_primary"]) for c in emitted]
    )
    base = sum(1 for c in emitted if c["gold_primary"] == "search_symbols") / len(emitted)
    shaped_right = emitted_result["table"]["shaped"]["search_symbols"]
    shaped_n = sum(emitted_result["table"]["shaped"].values())
    emitted_result["null_p_of_shaped_bucket"] = round(
        _binom_ge(shaped_right, shaped_n, base), 3
    )

    rows = _load_corpus(args.corpus)
    prompts = [r for r in rows if r["gold_primary_action"] in SEARCH_FAMILY]
    prompt_result = _score(
        [(is_identifier_shaped(r["prompt_text"]), r["gold_primary_action"]) for r in prompts]
    )

    per_pattern = {}
    for name in PATTERNS:
        hit = [r for r in prompts if name in shape_hits(r["prompt_text"])]
        if hit:
            sy = sum(1 for r in hit if r["gold_primary_action"] == "search_symbols")
            per_pattern[name] = {"fires": len(hit), "search_symbols": sy}
        else:
            per_pattern[name] = {"fires": 0}

    verb_emitted = _score_verb(
        [(verb_class(c["emitted_task"]), c["gold_primary"]) for c in emitted]
    )
    verb_prompts = _score_verb(
        [(verb_class(r["prompt_text"]), r["gold_primary_action"]) for r in prompts]
    )

    out = {
        "H1_identifier_shape": {
            "hypothesis": "identifier-shape predicts search_symbols over search_text",
            "verdict": "REFUTED",
            "emitted_tasks": emitted_result,
            "raw_prompts": prompt_result,
            "per_pattern_exploratory": per_pattern,
        },
        "H2_verb_intent": {
            "hypothesis": (
                "definition-seeking phrasing predicts search_symbols, "
                "occurrence-seeking predicts search_text"
            ),
            "verdict": "REFUTED",
            "emitted_tasks": verb_emitted,
            "raw_prompts": verb_prompts,
            "note": (
                "On emitted tasks the `definition` bucket is 1 search_symbols / 4 "
                "search_text -- the sign is BACKWARDS, not merely absent. A request "
                "that names what it wants DESCRIPTIVELY ('the function that parses "
                "config') gives a symbol-name index nothing to match, so descriptive "
                "definition-seeking favours search_text. n=5; directional, not proven."
            ),
        },
        "joint_finding": (
            "Both hypotheses fail on COVERAGE, not purity: H1 fires on ~15% of cases, "
            "H2 on 5-14%, and the residue sits at ~50% purity in both. Two independent "
            "properties of the query TEXT, both absent from 85-95% of real requests. "
            "The information needed to route them is not in the query string."
        ),
        "corpus": {"source": CORPUS_URL, "sha256": CORPUS_SHA256},
        "per_pattern_note": (
            "Seven patterns tested on one sample. Expect one to look good by chance; "
            "none is a finding on its own."
        ),
        "rows_remain_usable": (
            "Both predicates were declared before labels were read and each was run "
            "ONCE. Nothing was fitted to these rows, so they retain their value for "
            "the next hypothesis -- which a tuning pass would have destroyed."
        ),
    }
    print(json.dumps(out, indent=1))
    if args.write:
        (HERE / "route_discriminator_results.json").write_text(
            json.dumps(out, indent=1) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    # Windows: a redirected stdout is cp1252; write UTF-8 (L-145, L-320).
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    main()
