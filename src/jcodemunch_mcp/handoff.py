"""Server-owned canonical handoff contract (``jcodemunch.handoff/v1``, #374).

A multi-step repository audit ends with one authoritative Markdown result.
The assistant authors the analysis (the server never writes conclusions);
this module owns everything downstream of authorship: deterministic assembly,
evidence attestation, persistence, identity, hashing, and immutable serving
via the ``munch://handoff/<id>`` resource.

The differentiator no client Stop hook can provide: ``evidence_refs`` are
validated against the session's actual retrieval record (the yield tracker's
served-symbol ids) at finalization time, so a finalized handoff attests that
every evidence reference it cites corresponds to something actually served
by this server in this session. Unknown refs fail closed.

``handoff/v2`` (#377, claim-scoped evidence) is the same contract one level
deeper: a section may carry caller-authored ``claims``, each with its OWN
``evidence_refs``, so the body shows which retrieval backs which sentence
instead of one global list at the end. v1 proved "this was retrieved in this
session"; v2 proves that per claim. The schema string is chosen by the INPUT —
no claims anywhere means a v1 body, byte-identical to what v1 produced.

Charter constraints (accepted scope, issue #374):
- Deterministic: same repo/task/profile/sections/evidence/appendices ->
  byte-identical body, same id, same sha256.
- Each appendix exactly once; duplicate names rejected.
- No arbitrary character limit.
- Session-scoped (process == session, the _steer_state precedent), local-first,
  in-memory only — never writes to the user's repository or index store.
- ``canonical: true`` in the receipt is advisory metadata for direct-render
  clients; it forces nothing.
"""

from __future__ import annotations

import hashlib
import json
import threading
from typing import Iterable, Optional

HANDOFF_SCHEMA = "jcodemunch.handoff/v1"
HANDOFF_SCHEMA_V2 = "jcodemunch.handoff/v2"
HANDOFF_URI_PREFIX = "munch://handoff/"
HANDOFF_CONTENT_TYPE = "text/markdown"

# Session-scoped store: handoff_id -> {"body": str, "receipt": dict}.
# Process lifetime == MCP session lifetime (same precedent as server._steer_state);
# process exit is the cleanup.
_lock = threading.Lock()
_handoffs: dict[str, dict] = {}

# --- absence evidence (#377 phase 3) -----------------------------------------
#
# A zero-result search cannot be cited under the v1/v2 rules: nothing was
# served, so there is no id to reference. But "we searched the complete, fresh,
# non-truncated index and it is not there" is exactly the claim an audit most
# needs attested, and the one agents most often assert with no proof at all.
#
# The substrate already exists: `retrieval/verdict.build_verdict` reports a
# state (ok / low_confidence / absent / degraded), the scan counts backing it,
# per-channel status, index coverage, and a scorer pin. This records those
# verdicts under a deterministic ref so a claim can cite the scan itself.
#
# The refusal rules are the whole point and are deliberately strict:
#   - only `absent` can prove absence;
#   - `low_confidence` and `degraded` cannot (a weak or partial scan is not a
#     proof of nothing);
#   - a stale index cannot silently prove absence (it describes an older tree);
#   - a truncated index cannot either (the target may sit in the dropped tail).
# Unknown coverage does NOT refuse, but is disclosed as unknown rather than
# rendered as if the scope were complete.
ABSENCE_REF_PREFIX = "absent:"
_ABSENCE_MAXSIZE = 500
_absences: dict[str, dict] = {}

# Args that NARROW a search. They belong in the ref identity and in the
# rendered proof: "not found" means nothing without the scope it was not
# found in.
_SCOPE_ARGS = (
    "file_pattern",
    "language",
    "kind",
    "scope_path",
    "path_prefix",
    "include_kinds",
    "exclude_tests",
    # #377 hardening item 12: the ref names the scan, so anything that changes
    # WHICH operation ran belongs in its identity. A literal substring search
    # and a regex search of the same string are different operations, and two
    # different scans must not collide on one id.
    "is_regex",
    "decorator",
    "semantic",
    "semantic_only",
    "semantic_weight",
    "fuzzy",
    "fuzzy_threshold",
    "fusion",
    "sort_by",
    "strategy",
)


def _norm_file(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def _absence_ref(tool: str, repo: str, query: str, scope: dict) -> str:
    """Deterministic ref: the same scan in the same scope is the same proof."""
    payload = json.dumps(
        {"tool": tool, "repo": repo, "query": query, "scope": scope},
        sort_keys=True,
        separators=(",", ":"),
    )
    return ABSENCE_REF_PREFIX + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def refused_absence_note(why: str) -> str:
    """The note for a zero-result scan whose absence claim ``absence_refusal`` refused.

    (#872) Replaces ``build_verdict``'s ``absent`` note, which says the scan is
    strong evidence and not to search again: a scan refused for staleness is
    exactly the one a re-index would change.
    """
    return (
        f"No match was found, but absence is NOT proven: {why}. Resolve that and "
        "search again before treating this as evidence the target is not present."
    )


def absence_refusal(record: Optional[dict]) -> Optional[str]:
    """Why this recorded scan may NOT prove absence, or None when it may."""
    if not record:
        return None
    state = record.get("state")
    channels = record.get("channels") or {}
    if channels.get("index") == "rebuilding":
        # Checked before the generic state rule so the reason names the cause:
        # a zero-result scan mid-rebuild already downgrades to 'degraded', so
        # the generic branch below would refuse it correctly but unhelpfully.
        return (
            "the index was being rewritten during the scan, so the target may sit "
            "in rows written after the scan passed them"
        )
    if channels.get("index") == "unknown":
        # Named before the generic state rule (#377 item 4): a zero-result scan
        # whose freshness could not be established already downgrades, and
        # "the verdict was degraded" hides which capability failed.
        return (
            "index freshness could not be established at query time, so whether the "
            "scan describes the current tree or an older one is unknown"
        )
    if channels.get("index") == "partial":
        # Checked before the generic state rule so the reason names the cause:
        # a zero-result scan over a corpus with known gaps already downgrades to
        # 'degraded', so the generic branch would refuse it correctly but
        # unhelpfully (#375 sub-problem C).
        return (
            "the index did not cover the whole tree at scan time, so the target "
            "may sit in a file that never entered the corpus"
        )
    _withheld = (record.get("coverage") or {}).get("withheld") or {}
    if _withheld:
        # v1.108.193. Named BEFORE the generic state rule, and separately from
        # `partial`, because the cause is ours and the remedy is a setting.
        # `partial` says the corpus has a gap; this says WE made it, by refusing
        # a file that is real, current and wanted. A reader who is told "the
        # index did not cover the whole tree" goes looking at their repo; a
        # reader told a file was too large goes and raises the limit.
        _detail = ", ".join(f"{n} {reason}" for reason, n in sorted(_withheld.items()))
        return (
            f"the index withheld files that belong to this corpus ({_detail}), so "
            "the target may sit in a real, current file that our own limits kept "
            "out; raise the relevant limit and re-index before claiming absence"
        )
    _tree = record.get("working_tree") or {}
    if _tree.get("files_not_in_index"):
        # Named before the generic state rule (#377 item 5). Work OUTSIDE the
        # scanned scope never reaches here, and neither does an edit the index
        # has already re-read: refusing on either would fire on every developer
        # with unsaved work and teach people to ignore the signal.
        return (
            f"{_tree['files_not_in_index']} uncommitted change(s) inside the scanned "
            "scope had not reached the index, so the target may sit in an edit the "
            "scan could not read"
        )
    _moved = record.get("moved_during_scan") or {}
    if _moved.get("reason"):
        # Named before the generic state rule: the scan itself was fine, and
        # what disqualifies it is that the subject did not hold still for it.
        return (
            f"the subject moved while the scan ran ({_moved['reason']}), so the scan "
            "describes neither the state it started against nor the one it finished in"
        )
    _reval = record.get("revalidated") or {}
    if _reval.get("stale_cache"):
        # Named before the generic state rule: this scan really did reach
        # `absent`, and what disqualifies it is that the subject moved after it
        # was cached, which "the verdict was degraded" does not convey.
        return (
            f"this result was replayed from cache and {_reval.get('reason')}, so it "
            "describes a state that no longer holds"
        )
    _ignored = record.get("ignored_arguments") or []
    if _ignored:
        # (#872) Named before the generic state rule: the scan was a different
        # call from the one requested, which "the verdict was degraded" hides.
        return (
            f"argument(s) this tool does not accept were ignored ({', '.join(_ignored)}), "
            "so the call that ran is not the call that was requested"
        )
    _omitted = record.get("omitted") or {}
    if _omitted.get("returned") == 0 and (_omitted.get("matches_found") or 0) > 0:
        # Named before the generic state rule for the same reason as the
        # rebuilding and partial gates: the downgrade already refuses this scan,
        # but "the verdict was degraded" does not tell the reader that matches
        # were found and dropped by the packer.
        return (
            f"{_omitted['matches_found']} match(es) were found and omitted to fit the "
            "response budget, so the empty response describes packing, not absence"
        )
    _unprovable = record.get("absence_unprovable") or {}
    if _unprovable.get("reason"):
        # Named before the generic state rule (v1.108.184). Every other gate here
        # describes something that could be different next time — re-index, widen
        # the scope, raise the budget. This one is a permanent property of the
        # retrieval mode that ran, so the reason has to say so or a caller will
        # keep re-running a search that can never answer the question.
        return (
            f"{_unprovable['reason']} A ranking cannot establish that a symbol is "
            "absent from the corpus; re-run the search without the semantic "
            "channel to get a scan that can"
        )
    _incomplete = record.get("incomplete") or {}
    if _incomplete.get("reason") == "empty_scope":
        return (
            "the scope selected no eligible input, so nothing was searched and "
            "nothing can be proven absent from it"
        )
    if _incomplete.get("reason") == "unreadable_inputs":
        return (
            f"{_incomplete.get('files_unreadable')} eligible input(s) could not be "
            "read, so the target may sit in a file this scan never opened"
        )
    if state != "absent":
        return (
            f"the scan's verdict was '{state}', and only 'absent' can prove absence "
            "(a weak or partial scan is not evidence of nothing)"
        )
    if channels.get("index") == "stale":
        return (
            "the index was stale at query time, so the scan describes an older "
            "tree than the one being audited"
        )
    if record.get("truncated"):
        return (
            "the index was truncated at query time, so the target may sit in the "
            "files the walk dropped"
        )
    return None


def note_absence(tool: str, repo, query, verdict, arguments=None, truncated=False):
    """Record a search verdict so an absence claim can cite the scan itself.

    Returns ``(ref, refusal)``: a citable ref when this scan can prove absence,
    otherwise the reason it cannot. The record is kept either way, so a caller
    that cites a refused scan gets the reason instead of a bare unknown-ref
    error, and the live response can say why no token was offered.
    """
    if not isinstance(verdict, dict) or not isinstance(query, str) or not query.strip():
        return None, None
    state = verdict.get("state")
    # `ok` scans are not absence evidence and would only bloat the map.
    if state not in ("absent", "low_confidence", "degraded"):
        return None, None
    scope = {}
    for key in _SCOPE_ARGS:
        val = (arguments or {}).get(key)
        if val not in (None, "", [], {}):
            scope[key] = val
    repo_s = repo if isinstance(repo, str) else ""
    ref = _absence_ref(tool, repo_s, query.strip(), scope)
    record = {
        "ref": ref,
        "tool": tool,
        "repo": repo_s,
        "query": query.strip(),
        "scope": scope,
        "state": state,
        "scanned": verdict.get("scanned") or {},
        "channels": verdict.get("channels") or {},
        "coverage": verdict.get("coverage"),
        "omitted": verdict.get("omitted"),
        "incomplete": verdict.get("incomplete"),
        "revalidated": verdict.get("revalidated"),
        "moved_during_scan": verdict.get("moved_during_scan"),
        "working_tree": verdict.get("working_tree"),
        "absence_unprovable": verdict.get("absence_unprovable"),
        "ignored_arguments": verdict.get("ignored_arguments"),
        "truncated": bool(truncated),
        "scorer": verdict.get("scorer"),
    }
    with _lock:
        _absences[ref] = record
        while len(_absences) > _ABSENCE_MAXSIZE:
            _absences.pop(next(iter(_absences)))
    refusal = absence_refusal(record)
    return (None, refusal) if refusal else (ref, None)


def absence_ref_for(tool: str, repo, query, arguments=None) -> str:
    """The ref ``note_absence`` records a scan under, derived the same way.

    Exposed for the evidence-receipt producers (#377 phase 2): an absence
    receipt LINKS to the recorded scan instead of re-deriving whether that scan
    proves anything, so ``absence_refusal`` stays the single implementation of
    the refusal rules. Deriving the ref twice from the same projection is what
    keeps the two in step.
    """
    scope = {}
    for key in _SCOPE_ARGS:
        val = (arguments or {}).get(key)
        if val not in (None, "", [], {}):
            scope[key] = val
    repo_s = repo if isinstance(repo, str) else ""
    q = query.strip() if isinstance(query, str) else ""
    return _absence_ref(tool, repo_s, q, scope)


def absence_record(ref: str) -> Optional[dict]:
    with _lock:
        rec = _absences.get(ref)
        return dict(rec) if rec else None


def clear_absences() -> None:
    """Test hook: drop the session absence record."""
    with _lock:
        _absences.clear()


#: Proof kinds whose subject IS a whole file, and which could therefore attest a
#: file-level citation exactly. jcodemunch mints none today: every positive
#: receipt it can produce is symbol-scoped, which is precisely why a file-level
#: citation is broader than anything it retrieved. jdocmunch's document-level and
#: jdatamunch's dataset-level equivalents are the suite-parity stage (§10), and
#: this is the hook they attach to rather than a second rule written later.
_FILE_LEVEL_PROOF_KINDS: frozenset = frozenset()


def _receipts_cited(refs) -> bool:
    """Whether any ref anywhere in this handoff is an evidence receipt.

    Decided ONCE per finalization, not per claim: the input picks the contract,
    and it would be worse than useless if a claim's refs were held to a stricter
    subject rule than the global list in the same document.
    """
    from .evidence import receipts as _receipts

    return any(_receipts.is_evidence_ref(r) for r in refs if isinstance(r, str))


def _validate_evidence(refs, served_ids: Iterable[str], *, strict_subject: bool = False):
    """Attest each ref against the session retrieval record.

    Three kinds of ref, three attestation routes:

    * a served **symbol id** — exact, and always has been;
    * the **file component** of a served id — a file-level citation of retrieved
      code, which is BROADER than what was retrieved and is the Phase 1 caveat;
    * an **absence ref** (``absent:<sha>``, phase 3), which attests against the
      recorded scan since by construction nothing was served;
    * an **evidence receipt** (``munch://evidence/<id>`` or the bare id, phase
      2), which attests exactly the subject the receipt binds: one symbol id,
      one line range, one content hash, one snapshot.

    ``strict_subject`` is the Phase 2 narrowing and the input picks it: when this
    handoff cites at least one receipt, a file-level ref that is backed only by a
    served SYMBOL from that file is refused as over-broad rather than silently
    attested as though the file had been retrieved. A handoff citing no receipts
    keeps the historical broadening exactly, so no shipped caller changes; what
    it gains is that the finalization receipt now NAMES the broadened refs
    instead of leaving a reader unable to tell the two citations apart.

    Returns a dict: ``ordered`` (unique refs, caller order), ``unknown`` (the
    session never produced it), ``refused`` (a real scan that cannot prove
    absence), ``over_broad`` (a file-level citation held to the receipt
    contract), ``broadened`` (file-level refs attested by broadening),
    ``receipts`` (resolved receipt envelopes, in citation order), and
    ``exact_symbols``. Each failure mode gets its own message upstream because
    each one has a different fix.
    """
    from .evidence import receipts as _receipts

    served = set(served_ids or ())
    served_files = {_norm_file(sid.split("::", 1)[0]) for sid in served}
    seen: set[str] = set()
    out = {
        "ordered": [],
        "unknown": [],
        "refused": [],
        "over_broad": [],
        "broadened": [],
        "receipts": [],
        "exact_symbols": 0,
    }
    for ref in refs:
        if not isinstance(ref, str) or not ref.strip():
            out["unknown"].append(repr(ref))
            continue
        ref = ref.strip()
        if ref in seen:
            continue
        seen.add(ref)
        out["ordered"].append(ref)
        if ref.startswith(ABSENCE_REF_PREFIX):
            record = absence_record(ref)
            if record is None:
                out["unknown"].append(ref)
                continue
            reason = absence_refusal(record)
            if reason:
                out["refused"].append({"ref": ref, "reason": reason})
            continue
        if _receipts.is_evidence_ref(ref):
            envelope, why = _receipts.lookup(ref)
            if envelope is None:
                out["unknown"].append(f"{ref} ({why})")
                continue
            out["receipts"].append({"ref": ref, "receipt": envelope})
            if _receipts.is_absence_kind(envelope.get("proof_kind")):
                # `absence_refusal` remains the ONLY implementation of the
                # refusal rules, so a receipt cannot disagree with the gate that
                # issued it. What changed in v1.108.192 (#377 P3) is what it is
                # handed: the record FROZEN INTO the receipt at mint time, not
                # whatever now sits at the mutable `absent:` key.
                #
                # That key is sha256(tool, repo, query, scope)[:12] with no
                # snapshot in it, so re-running the same query over a different
                # tree overwrites it. Resolving through it meant an immutable,
                # snapshot-bound receipt could be validated against a scan it
                # was never minted from, in either direction: a provable receipt
                # refused because a later scan went stale, or a receipt riding a
                # later scan's provability.
                frozen = envelope.get("absence_record")
                if frozen is None:
                    # Pre-v1.108.192 receipt, or a producer that minted without
                    # one. Fall back to the legacy record rather than refusing
                    # outright, and say so when it is gone.
                    frozen = absence_record(envelope.get("absence_ref") or "")
                    reason = absence_refusal(frozen) if frozen else (
                        "the scan behind this receipt is no longer on record, so "
                        "whether it could prove absence cannot be re-established"
                    )
                else:
                    reason = absence_refusal(frozen)
                if reason:
                    out["refused"].append({"ref": ref, "reason": reason})
            continue
        if ref in served:
            out["exact_symbols"] += 1
            continue
        if _norm_file(ref) in served_files:
            backing = sorted(
                sid for sid in served if _norm_file(sid.split("::", 1)[0]) == _norm_file(ref)
            )
            entry = {"ref": ref, "backed_by": backing[:5], "backing_count": len(backing)}
            if strict_subject:
                out["over_broad"].append(entry)
            else:
                out["broadened"].append(entry)
            continue
        out["unknown"].append(ref)
    return out


def _validate_claims(raw, si, seen_ids):
    """Validate one section's caller-authored claims (#377 phase 1).

    Claim ids are unique across the WHOLE handoff, not per section: they are
    the machine-readable anchor a caller cites, and two sections owning the
    same id would make that citation ambiguous. Returns (claims, error).
    """
    if raw is None:
        return [], None
    if not isinstance(raw, list) or not raw:
        return None, f"sections[{si}].claims must be a non-empty list when present"
    out = []
    for j, claim in enumerate(raw):
        where = f"sections[{si}].claims[{j}]"
        if not isinstance(claim, dict):
            return None, f"{where} must be an object with 'id', 'statement' and 'evidence_refs'"
        cid = claim.get("id")
        statement = claim.get("statement")
        if not isinstance(cid, str) or not cid.strip():
            return None, f"{where}.id must be a non-empty string"
        if not isinstance(statement, str) or not statement.strip():
            return None, f"{where}.statement must be a non-empty string"
        cid = cid.strip()
        if cid in seen_ids:
            return None, f"duplicate claim id: {cid!r} (claim ids must be unique across the handoff)"
        seen_ids.add(cid)
        refs = claim.get("evidence_refs")
        if not isinstance(refs, list) or not refs:
            return None, (
                f"{where}.evidence_refs must be a non-empty list of session "
                "retrieval references (a claim with no evidence is not attestable)"
            )
        classification = claim.get("classification")
        if classification is not None and (
            not isinstance(classification, str) or not classification.strip()
        ):
            return None, f"{where}.classification must be a non-empty string when present"
        out.append(
            {
                "id": cid,
                # Caller-authored text is preserved verbatim; the server never
                # rewrites a statement or a classification.
                "statement": statement.strip(),
                "classification": classification.strip() if classification else None,
                "raw_refs": refs,
            }
        )
    return out, None


def _validate_sections(sections):
    if not isinstance(sections, list) or not sections:
        return None, "sections must be a non-empty list of {heading, content} objects"
    out = []
    seen_ids: set[str] = set()
    for i, sec in enumerate(sections):
        if not isinstance(sec, dict):
            return None, f"sections[{i}] must be an object with 'heading' and 'content'"
        heading = sec.get("heading")
        content = sec.get("content")
        if not isinstance(heading, str) or not heading.strip():
            return None, f"sections[{i}].heading must be a non-empty string"
        claims, err = _validate_claims(sec.get("claims"), i, seen_ids)
        if err:
            return None, err
        # Content stays required in the v1 shape; a claims-carrying section may
        # omit it, since the claims themselves are then the section's body.
        if content is None and claims:
            content = ""
        if not isinstance(content, str) or (not content.strip() and not claims):
            return None, f"sections[{i}].content must be a non-empty string"
        out.append((heading.strip(), content.rstrip(), claims))
    return out, None


def _validate_appendices(appendices):
    if appendices is None:
        return [], None
    if not isinstance(appendices, list):
        return None, "appendices must be a list of {name, content} objects"
    out = []
    names: set[str] = set()
    for i, app in enumerate(appendices):
        if not isinstance(app, dict):
            return None, f"appendices[{i}] must be an object with 'name' and 'content'"
        name = app.get("name")
        content = app.get("content")
        if not isinstance(name, str) or not name.strip():
            return None, f"appendices[{i}].name must be a non-empty string"
        if not isinstance(content, str) or not content.strip():
            return None, f"appendices[{i}].content must be a non-empty string"
        name = name.strip()
        if name in names:
            return None, f"duplicate appendix name: {name!r} (each appendix appears exactly once)"
        names.add(name)
        ctype = app.get("content_type") or "text/markdown"
        out.append((name, str(ctype), content.rstrip()))
    return out, None


def _render_absence_detail(ref: str, indent: str) -> list:
    """The scan behind an absence ref, rendered so a reader can audit it.

    A bare token proves nothing to a human. What makes an absence claim
    checkable is the scope it was not found in, how much was actually scanned,
    and whether the index was complete — so all of that goes in the body.
    """
    rec = absence_record(ref)
    if not rec:
        return []
    lines = [f"{indent}- absence proof: `{rec['tool']}` query {rec['query']!r}"]
    scope = rec.get("scope") or {}
    if scope:
        rendered = ", ".join(f"{k}={scope[k]!r}" for k in sorted(scope))
        lines.append(f"{indent}- scope: {rendered}")
    else:
        lines.append(f"{indent}- scope: whole indexed repository")
    scanned = rec.get("scanned") or {}
    if scanned:
        lines.append(
            f"{indent}- scanned: {scanned.get('symbols', 0)} symbols / "
            f"{scanned.get('files', 0)} files"
        )
    channels = rec.get("channels") or {}
    if channels:
        rendered = ", ".join(f"{k}={channels[k]}" for k in sorted(channels))
        lines.append(f"{indent}- channels: {rendered}")
    coverage = rec.get("coverage")
    if coverage:
        files_indexed = coverage.get("files_indexed")
        if files_indexed is not None:
            lines.append(f"{indent}- coverage: {files_indexed} files indexed")
        excluded = coverage.get("excluded") or {}
        if excluded:
            rendered = ", ".join(f"{k}={excluded[k]}" for k in sorted(excluded))
            lines.append(f"{indent}  - excluded: {rendered}")
        generation = coverage.get("generation") or {}
        if generation.get("git_head"):
            lines.append(f"{indent}  - index generation: {generation['git_head']}")
    else:
        # Never render unknown scope as if it were a complete one.
        lines.append(f"{indent}- coverage: not recorded for this index (scope unknown)")
    if rec.get("scorer") is not None:
        lines.append(f"{indent}- scorer: {rec['scorer']}")
    return lines


def _render_receipt_detail(ref: str, indent: str, resolved: Optional[dict] = None) -> list:
    """The receipt behind an evidence ref, rendered so a reader can audit it.

    Renders ONLY for a receipt ref, which means it renders only for a handoff
    that cited one. A handoff citing no receipts is byte-identical to what it
    rendered before Phase 2 — the input picks the contract, in the body as well
    as in the schema string.

    ``resolved`` is the ref -> envelope map that validation already built
    (#377 P3, v1.108.192). This function used to perform its OWN
    ``_receipts.lookup``, which made rendering a SECOND, independent read of a
    BOUNDED store: a receipt evicted between attestation and rendering produced
    a body with no receipt block, and a sha256 over that body, while the
    attestation said the evidence was proved. Validation, rendering and hashing
    now consume one resolution.
    """
    envelope = (resolved or {}).get(ref)
    if not envelope:
        return []
    subject = envelope.get("subject") or {}
    snapshot = envelope.get("snapshot") or {}
    lines = [f"{indent}- receipt: {envelope.get('proof_kind')} ({envelope.get('schema')})"]
    if subject.get("symbol_id"):
        lines.append(f"{indent}- subject: `{subject['symbol_id']}`")
    elif subject.get("query") is not None:
        lines.append(f"{indent}- subject: absence of {subject['query']!r}")
    if subject.get("start_line") is not None:
        end = subject.get("end_line")
        span = f"{subject['start_line']}-{end}" if end is not None else f"{subject['start_line']}+"
        lines.append(f"{indent}- lines: {subject.get('file', '?')}:{span}")
    if subject.get("content_sha256"):
        lines.append(f"{indent}- content sha256: {subject['content_sha256'][:12]}…")
    lines.append(
        f"{indent}- snapshot: generation {snapshot.get('index_generation') or 'unknown'}, "
        f"freshness {snapshot.get('freshness')}, working tree "
        f"{snapshot.get('working_tree')}, scorer {snapshot.get('scorer_version')}"
    )
    if snapshot.get("indexed_revision"):
        live = snapshot.get("live_revision") or "unknown"
        lines.append(
            f"{indent}- revisions: indexed {str(snapshot['indexed_revision'])[:12]}, "
            f"live {str(live)[:12]}"
        )
    for limitation in envelope.get("limitations") or []:
        lines.append(f"{indent}- limitation: {limitation}")
    return lines


def render_handoff(
    repo: str,
    task: str,
    profile: str,
    sections,
    evidence_refs,
    appendices,
    schema: str = HANDOFF_SCHEMA,
    resolved_receipts: Optional[dict] = None,
) -> str:
    """Deterministic canonical Markdown. No timestamps, no randomness.

    ``resolved_receipts`` carries the envelopes validation already resolved, so
    the rendered body cannot disagree with the attestation (#377 P3).
    """
    lines = [
        f"# Handoff: {task}",
        "",
        f"- Schema: {schema}",
        f"- Repo: {repo}",
        f"- Profile: {profile}",
        "",
    ]
    # Absence detail renders once. Under its claim when it has one, else in the
    # global index — repeating the whole scan block twice is noise in the exact
    # artifact meant to be read.
    detailed: set[str] = set()
    for _, _, claims in sections:
        for claim in claims:
            detailed.update(claim["refs"])

    for heading, content, claims in sections:
        lines += [f"## {heading}", ""]
        if content:
            lines += [content, ""]
        for claim in claims:
            # Evidence renders BESIDE the claim it supports — the whole point of
            # v2. The global index below stays for v1 compatibility.
            lines += [f"### {claim['statement']}", ""]
            lines.append(f"- Claim id: `{claim['id']}`")
            if claim["classification"]:
                lines.append(f"- Classification: {claim['classification']}")
            lines.append("- Evidence:")
            for ref in claim["refs"]:
                lines.append(f"  - `{ref}`")
                lines += _render_absence_detail(ref, "    ")
                lines += _render_receipt_detail(ref, "    ", resolved_receipts)
            lines.append("")
    lines += [
        "## Evidence",
        "",
        "Every reference below was validated against this session's retrieval",
        "record at finalization time (server-attested).",
        "",
    ]
    for ref in evidence_refs:
        lines.append(f"- `{ref}`")
        if ref not in detailed:
            lines += _render_absence_detail(ref, "  ")
            lines += _render_receipt_detail(ref, "  ", resolved_receipts)
    lines.append("")
    for name, ctype, content in appendices:
        lines += [f"## Appendix: {name}", "", f"_Content type: {ctype}_", "", content, ""]
    return "\n".join(lines).rstrip() + "\n"


def finalize_handoff(
    *,
    repo,
    task,
    sections,
    evidence_refs,
    profile: str = "general",
    appendices=None,
    served_ids: Optional[Iterable[str]] = None,
) -> dict:
    """Assemble, attest, persist, and return the compact receipt.

    Any validation failure returns ``{"error": ...}`` — the server dispatcher
    maps in-band error dicts to ``CallToolResult(isError=True)`` (v1.108.74
    contract). The server never authors content: sections/appendices arrive
    verbatim from the caller; only assembly and attestation happen here.
    """
    if not isinstance(repo, str) or not repo.strip():
        return {"error": "repo must be a non-empty string"}
    if not isinstance(task, str) or not task.strip():
        return {"error": "task must be a non-empty string"}
    if not isinstance(profile, str) or not profile.strip():
        return {"error": "profile must be a non-empty string"}
    sec, err = _validate_sections(sections)
    if err:
        return {"error": err}
    apps, err = _validate_appendices(appendices)
    if err:
        return {"error": err}
    served = list(served_ids or ())
    claim_count = sum(len(claims) for _, _, claims in sec)

    # The input picks the contract (#377 phase 2). Decided ONCE, over every ref
    # in the document, before a single one is attested: a claim's refs and the
    # global list must be held to the same subject rule, or a reader could not
    # tell which contract a given citation was judged under.
    _all_raw = list(evidence_refs if isinstance(evidence_refs, list) else [])
    for _, _, _claims in sec:
        for _claim in _claims:
            _all_raw.extend(_claim["raw_refs"] if isinstance(_claim["raw_refs"], list) else [])
    strict_subject = _receipts_cited(_all_raw)

    # Attest each claim's refs on their own, so an unknown ref names the claim
    # that cited it instead of vanishing into one global failure list (#377).
    invalid_claims = []
    refused_claims = []
    over_broad_claims = []
    for _, _, claims in sec:
        for claim in claims:
            att = _validate_evidence(
                claim["raw_refs"], served, strict_subject=strict_subject
            )
            claim["refs"] = att["ordered"]
            if att["unknown"]:
                invalid_claims.append({"claim_id": claim["id"], "unknown_refs": att["unknown"]})
            if att["refused"]:
                refused_claims.append({"claim_id": claim["id"], "refused": att["refused"]})
            if att["over_broad"]:
                over_broad_claims.append(
                    {"claim_id": claim["id"], "over_broad_refs": att["over_broad"]}
                )
    if over_broad_claims:
        # The Phase 1 caveat, closed. A served symbol does not attest a
        # file-level claim: one unrelated symbol from that file was the only
        # thing retrieved, and the server is the only party in the exchange that
        # knows the difference.
        return {
            "error": (
                "evidence subject too broad: the following claims cite a whole "
                "file, but only individual symbols from it were retrieved"
            ),
            "over_broad_claims": over_broad_claims,
            "hint": (
                "This handoff cites evidence receipts, so every reference is held "
                "to the subject its evidence actually proves. Cite the symbol id, "
                "or the munch://evidence/<id> receipt for it, instead of the file "
                "path. A file-level citation would need a file-level proof, which "
                "jcodemunch does not mint."
            ),
        }
    if refused_claims:
        # Distinct from an unknown ref: the scan is real, it just cannot prove
        # absence. Saying so by name is the point of the contract (#377).
        return {
            "error": (
                "absence evidence refused: the following claims cite a recorded "
                "scan that cannot prove absence"
            ),
            "refused_absence_claims": refused_claims,
            "hint": (
                "Only a verdict of 'absent' over a fresh, non-truncated index "
                "proves absence. Re-index or widen the scope, re-run the search, "
                "then cite the new scan."
            ),
        }
    if invalid_claims:
        return {
            "error": (
                "claim evidence attestation failed: the following claims cite "
                "refs that do not correspond to anything retrieved in this session"
            ),
            "invalid_claims": invalid_claims,
            "hint": (
                "Every claim-scoped ref must be a symbol id (or its file path) "
                "this session actually served. Retrieve the evidence that "
                "supports the claim, then finalize."
            ),
        }

    # `evidence_refs` stays required in the v1 shape, but claims can satisfy it:
    # a caller who scoped everything to claims should not have to restate it.
    claim_refs_flat = [ref for _, _, claims in sec for claim in claims for ref in claim["refs"]]
    if not isinstance(evidence_refs, list) or (not evidence_refs and not claim_refs_flat):
        return {
            "error": (
                "evidence_refs must be a non-empty list of session retrieval "
                "references (symbol ids or file paths served this session by "
                "search_symbols / get_ranked_context), or every claim must "
                "carry its own evidence_refs"
            )
        }
    # The canonical index is the union, caller order first: every claim ref is
    # discoverable from the global list, which keeps v1 consumers whole.
    att = _validate_evidence(
        list(evidence_refs) + claim_refs_flat, served, strict_subject=strict_subject
    )
    refs, unknown, refused = att["ordered"], att["unknown"], att["refused"]
    if att["over_broad"]:
        return {
            "error": (
                "evidence subject too broad: a cited reference names a whole file, "
                "but only individual symbols from it were retrieved"
            ),
            "over_broad": att["over_broad"],
            "hint": (
                "This handoff cites evidence receipts, so every reference is held "
                "to the subject its evidence actually proves. Cite the symbol id, "
                "or the munch://evidence/<id> receipt for it, instead of the file "
                "path. A file-level citation would need a file-level proof, which "
                "jcodemunch does not mint."
            ),
        }
    if refused:
        return {
            "error": (
                "absence evidence refused: a cited scan cannot prove absence"
            ),
            "refused_absence": refused,
            "hint": (
                "Only a verdict of 'absent' over a fresh, non-truncated index "
                "proves absence. Re-index or widen the scope, re-run the search, "
                "then cite the new scan."
            ),
        }
    if unknown:
        return {
            "error": (
                "evidence attestation failed: the following refs do not "
                "correspond to anything retrieved in this session"
            ),
            "unknown_refs": unknown,
            "hint": (
                "Evidence refs must be symbol ids (or their file paths) that "
                "this session actually served via search_symbols or "
                "get_ranked_context. Retrieve the evidence first, then finalize."
            ),
        }

    # The input picks the contract: no claims anywhere is still a v1 handoff,
    # and its body is byte-identical to what v1 rendered.
    schema = HANDOFF_SCHEMA_V2 if claim_count else HANDOFF_SCHEMA
    # One resolution, shared by validation, rendering and hashing (#377 P3).
    resolved_receipts = {r["ref"]: r["receipt"] for r in att["receipts"]}
    body = render_handoff(
        repo.strip(), task.strip(), profile.strip(), sec, refs, apps, schema,
        resolved_receipts=resolved_receipts,
    )
    raw = body.encode("utf-8")
    sha256 = hashlib.sha256(raw).hexdigest()
    handoff_id = sha256[:16]
    receipt = {
        "schema": schema,
        "handoff_id": handoff_id,
        "repo": repo.strip(),
        "profile": profile.strip(),
        "content_type": HANDOFF_CONTENT_TYPE,
        "resource_uri": f"{HANDOFF_URI_PREFIX}{handoff_id}",
        "sha256": sha256,
        "length": len(raw),
        "canonical": True,
        "evidence_attested": True,
        "evidence_count": len(refs),
        "appendices": [name for name, _, _ in apps],
    }
    if claim_count:
        # Omitted entirely on a v1 handoff, so v1 receipts stay unchanged.
        receipt["claims_attested"] = claim_count
    # An absence RECEIPT is an attested absence scan too — it points at the same
    # recorded scan the bare `absent:` token does, and a reader counting absence
    # proofs would otherwise undercount every receipt-citing handoff.
    absence_count = sum(1 for r in refs if r.startswith(ABSENCE_REF_PREFIX))
    for entry in att["receipts"]:
        from .evidence import receipts as _receipts_mod

        if _receipts_mod.is_absence_kind((entry["receipt"] or {}).get("proof_kind")):
            absence_count += 1
    if absence_count:
        receipt["absence_attested"] = absence_count
    if att["receipts"]:
        # Omitted entirely when no receipt was cited, so a v1/v2 receipt from
        # before Phase 2 is unchanged.
        receipt["receipts_attested"] = len(att["receipts"])
    if att["receipts"] or att["broadened"]:
        # What a reader could not previously tell apart, stated as a count.
        # `exact` = attested against a receipt's bound subject (symbol id, line
        # range, content hash, snapshot). `broadened` = a file-level citation
        # attested only because a symbol from that file was served — the Phase 1
        # caveat, now visible instead of indistinguishable.
        receipt["evidence_precision"] = {
            "exact": len(att["receipts"]),
            "symbol_exact": att["exact_symbols"],
            "broadened": len(att["broadened"]),
        }
    if att["broadened"]:
        receipt["broadened_refs"] = [
            {
                "ref": entry["ref"],
                "backed_by": entry["backed_by"],
                "backing_count": entry["backing_count"],
                "note": (
                    "file-level citation: only individual symbols from this file "
                    "were retrieved this session, so this reference is broader "
                    "than the evidence behind it"
                ),
            }
            for entry in att["broadened"]
        ]
    with _lock:
        _handoffs[handoff_id] = {"body": body, "receipt": receipt}
    return dict(receipt)


def get_handoff(handoff_id: str) -> Optional[dict]:
    """Return {"body", "receipt"} for a stored handoff, or None."""
    with _lock:
        rec = _handoffs.get(handoff_id)
        return {"body": rec["body"], "receipt": dict(rec["receipt"])} if rec else None


def handoff_for_uri(uri: str) -> Optional[dict]:
    """Resolve a munch://handoff/<id> URI to its stored record, or None."""
    s = str(uri)
    if not s.startswith(HANDOFF_URI_PREFIX):
        return None
    return get_handoff(s[len(HANDOFF_URI_PREFIX):])


def list_handoff_resources() -> list[dict]:
    """Rows for list_resources(): one per finalized handoff this session."""
    with _lock:
        return [
            {
                "uri": rec["receipt"]["resource_uri"],
                "name": f"handoff-{hid}",
                "description": (
                    f"Canonical handoff for {rec['receipt']['repo']} "
                    f"({rec['receipt']['profile']}); immutable, "
                    f"sha256 {rec['receipt']['sha256'][:12]}…"
                ),
            }
            for hid, rec in _handoffs.items()
        ]


def clear_handoffs() -> None:
    """Test hook: drop all session handoffs."""
    with _lock:
        _handoffs.clear()
