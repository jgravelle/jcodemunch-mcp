"""Corpus capability certificate: what this INSTALLATION could establish.

The distinction this module exists to draw, and it is the whole of it:

    coverage      — what this SCAN did not see
    capability    — what this INSTALLATION was ABLE to see, at this generation

They are not the same claim, and today only the first is recorded. Two machines
indexing the same commit with different grammar packs, different
``max_file_size``, different ignore patterns or a missing embedding model
produce different corpora and both report ``complete: true``, because
``complete`` reconciles the walk against the policy that produced it. It is an
honest statement about the walk and a weaker statement about the tree than a
reader assumes.

⚠⚠ **The sharp case is `wrong_extension`, and it is live.** On jcm's own index
59 files are skipped under that reason while coverage reports
``complete: true``. That reason conflates two things a negative claim must keep
apart:

    "that file is not source code"        — a scope decision, correctly excluded
    "this install cannot parse that"      — a CAPABILITY gap wearing a scope
                                            decision's clothes

Issue #382 established the mechanism rather than hypothesising it:
``tree-sitter-language-pack`` 1.x stopped bundling grammars, three languages in
our registry do not exist there at all, and the nim grammar was swapped for a
different upstream that our extractor returns zero symbols against. An install
on a different pack version has different capability, silently.

⚠ Recording this does NOT make coverage wrong. ``WITHHELD_SKIP_REASONS``
already refuses ``complete`` for ``too_large`` / ``file_limit`` / ``unreadable``
(v1.108.193), which are the config-dependent limits. The certificate names the
remaining axis: the parser and channel capability that the walk never had a
reason to question.

**What it is deliberately NOT.** Not a corpus manifest and not a file list —
those are large, they change on every commit, and nothing consumes them. The
certificate is bounded, content-addressed, and designed so a receipt carries its
32-hex digest rather than the document.

⚠ **Honest scoping.** The report that proposed this named its own gate:
*"a resource that appears only in diagnostics becomes decorative metadata."*
Nothing external consumes jcm receipts today. This ships as the missing INPUT to
``receipts.coverage_fingerprint`` — which has carried a comment naming this as
its extension point since v1.108.183 — and not as a claim that anyone is reading
it yet.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

CERTIFICATE_VERSION = "munch.corpus-capability/v1"

#: Config keys that change WHICH files can enter the corpus. A different value
#: here means a different corpus from the same commit, so it belongs to
#: capability rather than to coverage.
ELIGIBILITY_KEYS = (
    "max_file_size",
    "max_folder_files",
    "max_index_files",
    "extra_ignore_patterns",
)


def _canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _digest(obj) -> str:
    return hashlib.sha256(_canonical(obj).encode("utf-8")).hexdigest()[:32]


def parser_fingerprint() -> dict:
    """What this install can parse, and with which grammar build.

    ⚠ The versions matter more than the count. #382 measured that
    ``tree-sitter-language-pack`` 1.x does not bundle grammars (fetched over
    the network at first parse) and changed the nim grammar to an upstream our
    extractor reads as empty; #608's probe corrected #382's "drops three
    languages": those three are parsed by our own regex extractors. Two
    installs on different packs have different capability while reporting
    identical coverage; ``grammar_source`` names which (#608).
    """
    out: dict = {"languages": None, "extensions": None, "packages": {}}
    try:
        from ..parser import grammar_pack
        # #608: bundled (0.x) / download (1.x, grammars fetched at first parse) / absent.
        out["grammar_source"] = grammar_pack.generation()
    except Exception:
        logger.debug("Grammar pack generation unreadable", exc_info=True)
        out["grammar_source"] = "absent"
    try:
        from ..parser.languages import LANGUAGE_EXTENSIONS

        # ⚠ The mapping is extension -> language, so `len()` of it is the
        # EXTENSION count. Reporting that as the language count overstated
        # capability by every multi-suffix language we support.
        exts = sorted(LANGUAGE_EXTENSIONS)
        out["languages"] = len(set(LANGUAGE_EXTENSIONS.values()))
        out["extensions"] = len(exts)
        out["extensions_digest"] = _digest(exts)
    except Exception:
        logger.debug("Parser registry unreadable for the capability certificate",
                     exc_info=True)
    try:
        import importlib.metadata as md

        for pkg in ("tree-sitter", "tree-sitter-language-pack"):
            try:
                out["packages"][pkg] = md.version(pkg)
            except Exception:
                # ⚠ Absent is a capability statement, not a blank. An install
                # without the pack parses nothing, and null would read as
                # "not measured".
                out["packages"][pkg] = "absent"
    except Exception:
        logger.debug("Package versions unreadable", exc_info=True)
    return out


def eligibility_fingerprint(config_get=None) -> dict:
    """The policy that decided which files were allowed in.

    Values, not just a hash: a reader comparing two certificates needs to see
    WHICH limit differs, and the hash alone answers only that one does.
    """
    policy: dict = {}
    if config_get is None:
        try:
            from ..config import get as config_get  # type: ignore
        except Exception:
            config_get = None
    for key in ELIGIBILITY_KEYS:
        val = None
        if config_get is not None:
            try:
                val = config_get(key, None)
            except Exception:
                val = None
        policy[key] = val
    policy["_digest"] = _digest(policy)
    return policy


def channel_generations(index, db_path: Optional[str] = None,
                        *, store=None, owner: str = "", name: str = "") -> dict:
    """Which evidence channels this installation could draw on.

    ⚠ Tri-state, never boolean. ``absent`` means the channel is not present;
    ``unknown`` means we could not establish whether it is. Collapsing them
    would let a failed probe read as a clean "no embeddings", which is the
    v1.108.215 false-absence shape one layer up.
    """
    out = {"semantic": "unknown", "compiler_evidence": "unknown"}
    path = db_path or getattr(index, "_db_path", None)
    if not path:
        return out
    try:
        from ..storage.embedding_store import EmbeddingStore

        # ⚠ NOT `store`: that name is the IndexStore this function needs below
        # for the compiler-evidence probe, and rebinding it here made the SCIP
        # answer depend on a variable that had already been overwritten.
        embeddings = EmbeddingStore(path)
        has = embeddings.has_any()
        if has is True:
            model = None
            try:
                model = embeddings.get_model()  # type: ignore[attr-defined]
            except Exception:
                model = None
            out["semantic"] = {"present": True, "model": model or "unknown"}
        elif has is False:
            out["semantic"] = "absent"
        # has is None -> stays "unknown", which is the point of the tri-state
    except Exception:
        logger.debug("Semantic channel probe failed", exc_info=True)
    # ⚠ Needs (store, owner, name); without them the answer is genuinely
    # unknown and must stay that way rather than defaulting to "absent".
    if store is None or not owner or not name:
        return out
    try:
        from ..tools._scip_consume import open_scip_reader

        reader = open_scip_reader(store, owner, name)
        out["compiler_evidence"] = "absent" if reader is None else "present"
        if reader is not None:
            try:
                reader.close()
            except Exception:
                pass
    except Exception:
        logger.debug("Compiler-evidence probe failed", exc_info=True)
    return out


def build_certificate(index, coverage: Optional[dict] = None,
                      *, config_get=None, store=None,
                      owner: str = "", name: str = "") -> dict:
    """The `munch.corpus-capability/v1` document. Never raises.

    Bounded by construction: counts, versions and digests, never file lists.
    """
    cert: dict = {"version": CERTIFICATE_VERSION}
    try:
        from ..storage.generation import describe

        gen = describe(index)
        cert["generation"] = {
            "index_generation": gen.generation,
            "indexed_revision": gen.indexed_revision,
        }
        cert["parser"] = parser_fingerprint()
        cert["eligibility"] = eligibility_fingerprint(config_get)
        cert["channels"] = channel_generations(
            index, gen.db_path, store=store,
            owner=owner or getattr(index, "owner", "") or "",
            name=name or getattr(index, "name", "") or "",
        )
        cov = coverage if coverage is not None else getattr(index, "coverage", None)
        cert["coverage"] = _coverage_summary(cov)
    except Exception:
        logger.debug("Capability certificate build failed", exc_info=True)
    cert["certificate_id"] = _digest(
        {k: v for k, v in cert.items() if k != "certificate_id"}
    )
    return cert


def _coverage_summary(coverage: Optional[dict]) -> dict:
    """The coverage block, plus the question it does not answer.

    ⚠ ``unsupported_extension_skips`` is surfaced SEPARATELY from the other skip
    reasons because it is the one that may be a capability gap rather than a
    scope decision, and the walk has no way to tell which. Naming it is the
    honest move: a reader qualifying a negative claim can see that N files were
    excluded for a reason this installation cannot fully justify.
    """
    if not coverage:
        return {"recorded": False}
    skips = coverage.get("skip_counts") or {}
    return {
        "recorded": True,
        "complete": coverage.get("complete"),
        "files_indexed": coverage.get("files_indexed"),
        "files_accepted": coverage.get("files_accepted"),
        "withheld": coverage.get("withheld") or {},
        "unsupported_extension_skips": skips.get("wrong_extension", 0),
        "skip_reasons": sorted(skips),
        "no_symbols_count": coverage.get("no_symbols_count"),
    }


def certificate_id(index, coverage: Optional[dict] = None, **kw) -> Optional[str]:
    """Just the digest, for a receipt that carries the id rather than the doc."""
    try:
        return build_certificate(index, coverage, **kw).get("certificate_id")
    except Exception:
        return None
