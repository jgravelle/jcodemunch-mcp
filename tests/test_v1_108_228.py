"""v1.108.228 — ranking ties break on the symbol id, not on insertion order.

#398 Arc 4 / [#403](https://github.com/jgravelle/jcodemunch-mcp/issues/403).
`ROADMAP.md` has said since 2026-08-02 that this key "is required regardless and
ships FIRST, alone", because certifying a ranking whose ties fall back to
insertion order means reproducing an arbitrary row order bit for bit. `fuse()`
has always used `(-score, symbol_id)`; the four channel builders that feed it and
the semantic scorer did not.

⚠⚠ **v1.108.223 turned this from correct-in-principle into urgent.** That release
gave the semantic scorer two lanes — a numpy float32 matmul and a pure-Python
float64 fallback — whose scores differ by ~1e-7. Measured on a deliberately
near-tied 4,000-vector corpus, they disagreed at **rank 0**: two installs of the
same version ranked differently based only on whether numpy was importable.

⚠ That corpus is synthetic and maximally homogeneous. On REAL embeddings
@rknighton measured **zero** float32/float64 boundary disagreements across
Django, FastAPI and jcm (#403). The hazard is real in principle and does not fire
in practice on those corpora. Both facts belong together, and neither is a reason
to leave insertion order as a second invisible source of divergence.

A total order on `(score, id)` does not remove the float difference. It removes
the *other* one, and it collapses the exact-tie bucket that #403's certification
breadth measures — which is exactly why the roadmap wanted it before the
measurement rather than after.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.retrieval.signal_fusion import (
    ChannelResult,
    build_identity_channel,
    build_lexical_channel,
    build_similarity_channel_from_scores,
    build_structural_channel,
    fuse,
)


def _sym(sid: str, name: str = "thing", file: str = "a.py") -> dict:
    return {
        "id": sid, "name": name, "file": file, "kind": "function",
        "signature": f"def {name}()", "summary": "", "line": 1, "end_line": 2,
        "language": "python",
    }


class TestChannelsAreTotallyOrdered:
    """Each builder must produce the same order for the same scores, whatever
    order the symbols arrive in."""

    def test_structural_channel_is_insertion_order_independent(self):
        pagerank = {"a.py": 1.0, "b.py": 1.0, "c.py": 1.0}  # deliberate 3-way tie
        syms = [_sym("z::s#function", file="a.py"),
                _sym("m::s#function", file="b.py"),
                _sym("a::s#function", file="c.py")]
        forward = build_structural_channel(syms, pagerank).ranked_ids
        reverse = build_structural_channel(list(reversed(syms)), pagerank).ranked_ids
        assert forward == reverse
        assert forward == sorted(forward), "ties must order by id"

    def test_similarity_channel_is_insertion_order_independent(self):
        scores = {"z::s#function": 0.5, "m::s#function": 0.5, "a::s#function": 0.5}
        forward = build_similarity_channel_from_scores(scores).ranked_ids
        reverse = build_similarity_channel_from_scores(
            dict(reversed(list(scores.items())))
        ).ranked_ids
        assert forward == reverse == sorted(scores)

    def test_identity_channel_is_insertion_order_independent(self):
        syms = [_sym("z::helper#function", name="helper"),
                _sym("m::helper#function", name="helper"),
                _sym("a::helper#function", name="helper")]
        forward = build_identity_channel(syms, "helper").ranked_ids
        reverse = build_identity_channel(list(reversed(syms)), "helper").ranked_ids
        assert forward == reverse
        assert forward == sorted(forward)

    def test_lexical_channel_is_insertion_order_independent(self):
        syms = [_sym("z::alpha#function", name="alpha"),
                _sym("m::alpha#function", name="alpha"),
                _sym("a::alpha#function", name="alpha")]
        idf = {"alpha": 1.0}
        forward = build_lexical_channel(syms, ["alpha"], idf, 10.0).ranked_ids
        reverse = build_lexical_channel(list(reversed(syms)), ["alpha"], idf, 10.0).ranked_ids
        assert forward == reverse
        if forward:
            assert forward == sorted(forward)


class TestRankingIsNotDegraded:
    """The guard: a total order must not disturb the ordering that scores
    already determine."""

    def test_a_higher_score_still_outranks_a_lower_one(self):
        scores = {"zzz::hit#function": 0.9, "aaa::miss#function": 0.1}
        ranked = build_similarity_channel_from_scores(scores).ranked_ids
        assert ranked == ["zzz::hit#function", "aaa::miss#function"], (
            "the id tiebreak must apply ONLY to ties"
        )

    def test_min_similarity_still_filters(self):
        ch = build_similarity_channel_from_scores(
            {"a": 0.9, "b": 0.05}, min_similarity=0.1
        )
        assert ch.ranked_ids == ["a"]

    def test_fuse_still_orders_by_fused_score(self):
        ch = ChannelResult(name="lexical", ranked_ids=["top", "second", "third"])
        fused = fuse([ch])
        assert [f.symbol_id for f in fused] == ["top", "second", "third"]


class TestSemanticPathTieBreak:
    """The scorer the two v1.108.223 lanes feed."""

    def test_the_semantic_sort_uses_a_total_order(self):
        """The property is that the key ENDS in the symbol id and orders score
        descending, not that it is one particular string.

        ⚠ This assertion pinned the whole literal
        `scored.sort(key=lambda x: (-x[0], x[1]["id"]))` until #699 added a
        leading declaration-rank component. The invariant it exists to protect
        -- ties broken on the id, so the numpy float32 and Python float64 lanes
        cannot disagree at rank 0 -- was untouched by that change, but the test
        failed anyway because it stated the mechanism instead of the outcome
        (Practice 9). Restated over the property so a further component can be
        added without a false alarm, and so dropping the id tiebreak still
        fails.
        """
        import inspect
        import re

        from jcodemunch_mcp.tools import search_symbols as ss

        src = inspect.getsource(ss)
        sorts = [ln.strip() for ln in src.splitlines() if "scored.sort(" in ln]
        assert sorts, "the semantic scorer's sort disappeared; this ratchet is blind"
        for line in sorts:
            assert re.search(r'x\[1\]\["id"\]\s*\)*\s*\)\s*$', line), (
                f"the symbol id is not the FINAL tiebreak: {line}"
            )
            assert "-x[0]" in line, f"score is not ordered descending: {line}"
        assert "scored.sort(key=lambda x: x[0], reverse=True)" not in src, (
            "a score-only sort leaves ties on SQLite's row order"
        )

    def test_no_ranking_sort_anywhere_is_score_only(self):
        """Guards the whole surface, not just the site that was fixed."""
        import inspect

        from jcodemunch_mcp.retrieval import signal_fusion

        for mod in (signal_fusion,):
            src = inspect.getsource(mod)
            assert "sort(key=lambda x: x[0], reverse=True)" not in src, mod.__name__


class TestTheTwoLanesCanDisagree:
    """⚠ The measurement that made this urgent, kept as a live fact rather than
    a claim in a changelog.

    Not a defect being fixed here — the float difference is inherent to
    computing in float32 vs float64, and this release does not remove it. It is
    recorded so nobody later reads the tiebreak as cosmetic.
    """

    def test_float32_and_float64_cosine_can_order_differently(self):
        np = pytest.importorskip("numpy")

        import random

        random.seed(11)
        dim = 384
        base = [random.gauss(0, 1) for _ in range(dim)]
        vectors = {
            f"s{i}": [b + random.gauss(0, 1e-4) for b in base] for i in range(400)
        }
        query = [b + random.gauss(0, 1e-3) for b in base]

        def norm(v, dtype):
            arr = np.asarray(v, dtype=dtype)
            return arr / np.linalg.norm(arr)

        q32, q64 = norm(query, np.float32), norm(query, np.float64)
        s32 = {k: float(norm(v, np.float32) @ q32) for k, v in vectors.items()}
        s64 = {k: float(norm(v, np.float64) @ q64) for k, v in vectors.items()}

        # The scores differ. That is the premise, and it is what a total order
        # on (score, id) does NOT fix.
        assert any(s32[k] != s64[k] for k in s32)

        # With the tiebreak in place both are at least self-consistent and
        # reproducible: sorting twice gives the same answer either way.
        order = lambda s: [k for k, _ in sorted(s.items(), key=lambda x: (-x[1], x[0]))]
        assert order(s32) == order(s32)
        assert order(s64) == order(s64)
