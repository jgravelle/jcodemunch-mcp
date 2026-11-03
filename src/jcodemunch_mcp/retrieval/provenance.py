"""Confidence provenance — every confidence number traces to a stated basis.

Phase A of the self-attesting retrieval contract: a confidence constant the
suite emits is either

- ``measured`` — backed by a committed, reproducible benchmark artifact
  (``benchmarks/provenance/measured.json``, drift-guarded by
  ``tests/test_provenance.py``), or
- ``declared`` — an engineering prior, honestly labeled as such.

The rule this module enforces culturally and the tests enforce mechanically:
**never present a prior as a measurement.** A ``declared`` entry graduates to
``measured`` only when a gold-labeled corpus exists for it; until then the
label tells the caller exactly how much weight the number can bear.

Wheel installs don't ship ``benchmarks/``, so measured values are embedded
here as constants with a ``source`` pointing at the committed artifact; the
in-repo test suite asserts the two never diverge (the SigMap-style drift
guard, built in our idiom).
"""

BASIS_MEASURED = "measured"
BASIS_DECLARED = "declared"

MEASURED_ARTIFACT = "benchmarks/provenance/measured.json"
CHANNEL_ACCURACY_ARTIFACT = "benchmarks/provenance/channel_accuracy.json"

# ── Declared constants (engineering priors) ─────────────────────────────────
# tests/test_provenance.py asserts each `value` equals the live constant in
# the module that emits it, so code and registry can't drift apart.

CONFIDENCE_PROVENANCE: dict[str, dict] = {
    "find_implementations.lsp": {
        "value": 1.0,
        "basis": BASIS_DECLARED,
        "note": "live LSP dispatch resolution — compiler-grade channel, prior not yet gold-measured",
    },
    "find_implementations.scip": {
        "value": 1.0,
        "basis": BASIS_DECLARED,
        "note": "compile-time SCIP evidence — compiler-grade channel, prior not yet gold-measured",
    },
    # The operating constants below stay DECLARED — they are ranking priors,
    # and recalibrating them to the small-n corpus numbers would jitter
    # ranking for false precision. Each carries its measured reference
    # (CHANNEL_ACCURACY_ARTIFACT, re-measured in CI) so callers see both the
    # prior in force and what the gold corpus actually measured.
    "find_implementations.ast": {
        "value": 0.85,
        "basis": BASIS_DECLARED,
        "note": "AST class-hierarchy channel",
        "measured_ref": {
            "precision": 0.818,
            "recall": 1.0,
            "corpus": "authored-scenarios-v2",
            "source": "benchmarks/provenance/channel_accuracy.json",
        },
    },
    "find_implementations.duck": {
        "value": 0.65,
        "basis": BASIS_DECLARED,
        "note": "duck-typed name-match channel",
        "measured_ref": {
            "precision": 0.5,
            "recall": 1.0,
            "corpus": "authored-scenarios-v2",
            "source": "benchmarks/provenance/channel_accuracy.json",
        },
    },
    "find_implementations.decorator": {
        "value": 0.45,
        "basis": BASIS_DECLARED,
        "note": "decorator-registered handler channel; prior is more conservative than measured",
        "measured_ref": {
            "precision": 0.556,
            "recall": 1.0,
            "corpus": "authored-scenarios-v2",
            "source": "benchmarks/provenance/channel_accuracy.json",
        },
    },
    "retrieval.negative_evidence_threshold": {
        "value": 0.5,
        "basis": BASIS_DECLARED,
        "note": "raw-BM25 floor below which a non-empty result is flagged low-confidence",
    },
    "get_ranked_context.exact_seed_verdict_floor": {
        "value": 1.0,
        "basis": BASIS_DECLARED,
        "note": "verdict floor credited when a source-shaped token exact-matched a symbol name",
    },
}

# ── Measured entries (embedded copies of the committed artifact) ────────────

MEASURED: dict[str, dict] = {
    "token_reduction": {
        "average_pct": 99.6,
        "task_runs": 15,
        "tokenizer": "cl100k_base",
        "basis": BASIS_MEASURED,
        "source": MEASURED_ARTIFACT,
        "methodology": "benchmarks/METHODOLOGY.md",
    },
    "replay_retrieval_quality": {
        "fixture": "self_v1_75_0",
        "k": 10,
        "ndcg": 1.0,
        "mrr": 1.0,
        "recall": 1.0,
        "basis": BASIS_MEASURED,
        "source": MEASURED_ARTIFACT,
        "ci_gated": True,
    },
    "implementation_channel_accuracy": {
        "corpus": "authored-scenarios-v2",
        "languages": ["python", "typescript", "go"],
        "channels": {
            "ast": {"precision": 0.818, "recall": 1.0},
            "duck": {"precision": 0.5, "recall": 1.0},
            "decorator": {"precision": 0.556, "recall": 1.0},
        },
        "basis": BASIS_MEASURED,
        "source": CHANNEL_ACCURACY_ARTIFACT,
        "ci_gated": True,
        "scope_note": (
            "authored gold corpus — channel discrimination on known patterns "
            "and traps, not in-the-wild base rates; n small and disclosed in "
            "the artifact"
        ),
    },
}

_CONTRACT_NOTE = (
    "declared = engineering prior; measured = committed benchmark artifact "
    f"({MEASURED_ARTIFACT})"
)


def measured_provenance() -> dict:
    """The measured-artifact block for reporting surfaces (receipt,
    get_session_stats, jcodemunch_guide).

    Returns fresh copies of the ``MEASURED`` entries plus the contract note,
    so callers can attach it to a response without aliasing module state.
    Deliberately kept OFF the hot retrieval path — provenance rides the
    surfaces where a human reads the numbers, not every query.
    """
    return {
        **{key: dict(entry) for key, entry in MEASURED.items()},
        "contract": _CONTRACT_NOTE,
    }


def channel_provenance(prefix: str) -> dict:
    """Compact per-channel basis block for a tool's ``_meta``.

    Returns ``{"channels": {<channel>: {"value", "basis"}}, "contract": ...}``
    for every registry key under ``<prefix>.``.
    """
    channels = {}
    for key, entry in CONFIDENCE_PROVENANCE.items():
        if not key.startswith(prefix + "."):
            continue
        ch = {"value": entry["value"], "basis": entry["basis"]}
        if "measured_ref" in entry:
            ch["measured_ref"] = dict(entry["measured_ref"])
        channels[key.split(".", 1)[1]] = ch
    return {"channels": channels, "contract": _CONTRACT_NOTE}
