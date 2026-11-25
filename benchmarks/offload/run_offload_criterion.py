"""Measure the offloadable-work criterion against a pinned corpus.

This is the INSTRUMENT, and it ships before the label does. The criterion's
close condition says no ``offloadable`` verdict appears in a README, on the
site, or in any announcement until its rate has been measured on a named corpus
with n and limits stated. This produces that measurement.

What it reports
---------------

* **Label rate** across the three states, per arm.
* **First gate**, separately from **all gates**. An aggregate over a rule chain
  that short-circuits measures two populations at once; counting only the first
  disqualifier says which rule is actually doing the work, and counting all of
  them says how much redundancy is in the chain. Reporting one without the
  other has burned us before.
* **False-offloadable count**, deterministically.

What "false offloadable" means here, exactly
--------------------------------------------

⚠⚠ **This measures a NECESSARY condition, not a sufficient one, and the
distinction is the whole honesty of the number.**

For every payload the criterion labels ``offloadable``, we check that the
ground truth is *actually present in the payload*: the symbol's stored body,
non-empty, matching the content hash the index recorded. A payload we called
EXTRACTIVE that does not contain the thing to extract is a false offloadable,
caught with no model in the loop and no judgement call.

It does **not** measure whether a cheap model then gets the answer right. That
needs a model, a task suite, and a grader, none of which exist yet. So:

* A **failure** here is conclusive. The label was wrong.
* A **pass** here is NOT "the cheap model will succeed". It is only "the
  information the cheap model needs was present".

Do not publish the pass rate as an accuracy figure. It is an upper bound on
achievable accuracy and nothing more.

Usage
-----

    PYTHONPATH=src python benchmarks/offload/run_offload_criterion.py \
        --repo local/jcodemunch-mcp-0394b683 --sample 300 --write
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from jcodemunch_mcp.retrieval import offload  # noqa: E402
from jcodemunch_mcp.retrieval.offload import (  # noqa: E402
    MODE_IDENTITY,
    MODE_RANKED,
    NOT_EVALUATED,
    NOT_OFFLOADABLE,
    OFFLOADABLE,
    classify,
    shape_from_response,
)
from jcodemunch_mcp.retrieval.confidence import compute_confidence  # noqa: E402
from jcodemunch_mcp.storage import IndexStore  # noqa: E402
from jcodemunch_mcp.tools.get_ranked_context import get_ranked_context  # noqa: E402
from jcodemunch_mcp.tools.get_symbol import get_symbol_source  # noqa: E402

OUT_PATH = Path(__file__).with_name("results.json")

#: Query classes the ranked arm has to separate, with what each is FOR.
#:
#: ⚠ These replace a fixed literal list that was jCodeMunch's own vocabulary
#: (``compute_confidence``, ``FreshnessProbe``). Against any other corpus that
#: list partly measured whether the words happened to exist there, and on
#: fastapi one query gated on ``ABSENCE_CLAIM`` for exactly that reason. A
#: rejection caused by a word not being in the corpus is indistinguishable, in
#: the tally, from a rejection the criterion earned. Deriving the vocabulary
#: from the corpus removes that confound; the ``absent`` class then puts the
#: off-corpus case back deliberately, LABELLED, where it is a control instead
#: of a contaminant.
CLASS_UNIQUE = "unique"
CLASS_AMBIGUOUS = "ambiguous"
CLASS_ABSENT = "absent"

#: A name must be shared by at least this many symbols to count as ambiguous.
AMBIGUOUS_MIN_OWNERS = 5

#: Control queries, fixed and corpus-independent BY DESIGN. Nothing can match
#: them, so they are the ranked arm's non-vacuity check: if the criterion ever
#: labels one offloadable it has approved a payload with no evidence in it.
ABSENT_QUERIES = (
    "zzq_no_such_identifier_alpha",
    "zzq_no_such_identifier_beta",
)


def _split_repo(repo: str) -> tuple[str, str]:
    owner, _, name = repo.partition("/")
    return (owner, name) if name else ("local", repo)


#: Files that produce the number. If these are not committed at the pinned
#: commit, nobody can reproduce the run from the pin, however clean the tree
#: reads.
_INSTRUMENT_PATHS = (
    "src/jcodemunch_mcp/retrieval/offload.py",
    "benchmarks/offload/run_offload_criterion.py",
)


def _is_self_corpus(source_root: str | None) -> bool:
    """Is the corpus under test this very repository?

    Decided by path, not by name: a fork or a clone under another name still
    contains the instrument and still self-measures.
    """
    if not source_root:
        return False
    try:
        return Path(source_root).resolve() == _ROOT.resolve()
    except OSError:
        return False


def _instrument_provenance(source_root: str) -> dict:
    """Are the criterion and harness themselves committed?

    ⚠ A clean ``git status`` is NOT the same fact as a reproducible run. These
    files are held in ``.git/info/exclude`` while the work is unannounced, which
    makes them invisible to porcelain. Without this check the pin would report
    ``dirty: false`` about a tree whose measuring code exists nowhere in git,
    which is a false reassurance of exactly the kind a pin exists to prevent.
    """
    try:
        proc = subprocess.run(
            ["git", "ls-files", "--", *_INSTRUMENT_PATHS],
            cwd=source_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except Exception as exc:  # pragma: no cover - environment dependent
        return {"all_committed": None, "note": f"git unavailable: {exc}"}
    tracked = {line.strip() for line in proc.stdout.splitlines() if line.strip()}
    untracked = [p for p in _INSTRUMENT_PATHS if p not in tracked]
    return {
        "all_committed": not untracked,
        "untracked": untracked,
        "note": (
            "Instrument is not in git, so this run is NOT reproducible from the "
            "pin alone. Expected while the work is unannounced."
            if untracked
            else ""
        ),
    }


def _corpus_pin(source_root: str | None) -> dict:
    """Pin the corpus by commit. An unpinned number is not reproducible."""
    if not source_root:
        return {"git_head": None, "dirty": None, "note": "no source root"}
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=source_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=source_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except Exception as exc:  # pragma: no cover - environment dependent
        return {"git_head": None, "dirty": None, "note": f"git unavailable: {exc}"}
    if head.returncode != 0:
        return {"git_head": None, "dirty": None, "note": "not a git checkout"}
    return {
        "git_head": head.stdout.strip(),
        # Tracked modifications only. Excluded files are invisible here by
        # design, which is why `instrument` is reported beside it and not
        # folded into it.
        "dirty": bool(status.stdout.strip()),
        "instrument": _instrument_provenance(source_root),
    }


def _sample(index, n: int) -> list:
    """Deterministic stride sample over sorted symbol ids.

    No RNG: a benchmark that samples differently each run cannot separate a
    criterion change from a sample change.
    """
    ids = sorted(
        (s.get("id") if isinstance(s, dict) else getattr(s, "id", None))
        for s in index.symbols
    )
    ids = [i for i in ids if i]
    if n <= 0 or n >= len(ids):
        return ids
    stride = len(ids) / n
    return [ids[int(i * stride)] for i in range(n)]


def _symbol_names(index) -> dict[str, int]:
    """Map every indexed symbol name to how many symbols carry it."""
    counts: dict[str, int] = {}
    for s in index.symbols:
        name = s.get("name") if isinstance(s, dict) else getattr(s, "name", None)
        if not name or not isinstance(name, str):
            continue
        counts[name] = counts.get(name, 0) + 1
    return counts


def _derive_queries(index, per_class: int = 4) -> list[dict]:
    """Build the ranked arm's query list FROM the corpus under test.

    Three labelled classes, so a rejection can be attributed:

    - ``unique``    a name exactly one symbol carries. Nothing to adjudicate,
                    so the rank margin should be wide.
    - ``ambiguous`` a name at least :data:`AMBIGUOUS_MIN_OWNERS` symbols share.
                    The retrieval has to pick, which is where a cheap model
                    reading the payload would have to pick too.
    - ``absent``    cannot match anything, in any corpus. A control.

    Deterministic by construction: candidates are sorted, then strided. No RNG,
    for the same reason :func:`_sample` has none.

    ⚠ The two corpus-derived classes are a sampling of names, not a sampling of
    the queries a user would type. Real queries are often prose. This measures
    the criterion against a controlled spread of retrieval shapes; it does not
    estimate a rate over real traffic, and no arm here does.
    """
    counts = _symbol_names(index)
    unique = sorted(n for n, c in counts.items() if c == 1)
    ambiguous = sorted(n for n, c in counts.items() if c >= AMBIGUOUS_MIN_OWNERS)

    def _stride(pool: list[str], n: int) -> list[str]:
        if n <= 0 or not pool:
            return []
        if n >= len(pool):
            return pool
        step = len(pool) / n
        return [pool[int(i * step)] for i in range(n)]

    out = [{"query": q, "class": CLASS_UNIQUE} for q in _stride(unique, per_class)]
    out += [{"query": q, "class": CLASS_AMBIGUOUS} for q in _stride(ambiguous, per_class)]
    out += [{"query": q, "class": CLASS_ABSENT} for q in ABSENT_QUERIES]
    return out


def _ground_truth_present(response: dict, symbol_id: str, index) -> tuple[bool, str]:
    """Is the thing we promised was extractable actually in the payload?

    Returns ``(ok, reason)``. This is the deterministic falsifier for a
    positive label.
    """
    body = response.get("source")
    if body is None and isinstance(response.get("symbols"), list):
        for entry in response["symbols"]:
            if isinstance(entry, dict) and entry.get("id") == symbol_id:
                body = entry.get("source")
                break
    if body is None:
        return False, "no source field in a payload labelled extractive"
    if not str(body).strip():
        return False, "empty body under an extractive label"

    try:
        sym = index.get_symbol(symbol_id)
    except Exception:
        sym = None
    if not sym:
        return False, "symbol vanished from the index mid-run"

    def _f(key: str) -> int:
        raw = sym.get(key) if isinstance(sym, dict) else getattr(sym, key, None)
        try:
            return int(raw or 0)
        except (TypeError, ValueError):
            return 0

    # Prefer the indexed byte extent. It is what the extractor actually
    # recorded for this symbol, so the comparison carries no assumption about
    # what a line number means.
    #
    # ⚠ The line-derived check this replaced produced FALSE POSITIVES, and the
    # reason generalises: it assumed ``end_line`` is a symbol's last line, and
    # jCodeMunch's TOML extractor sets it to the first line of the NEXT table
    # (verified against fastapi/pyproject.toml at a64dfbbd: `[build-system]` is
    # lines 1-3 with end_line 5, where line 5 is `[project]`). The bodies were
    # complete; the derived expectation was wrong. A falsifier resting on a
    # convention not every extractor honours reports defects that do not exist.
    #
    # Only the SHORT direction is a failure. A truncated body is the hazard we
    # are hunting; an over-long one cannot hide missing evidence.
    expected_bytes = _f("byte_length")
    if expected_bytes > 0:
        got_bytes = len(str(body).encode("utf-8"))
        if got_bytes < expected_bytes:
            return False, f"body short: {got_bytes} bytes vs {expected_bytes} indexed"
        return True, ""

    # Fallback only when the index carries no byte extent.
    expected_lines = _f("end_line") - _f("line") + 1
    got_lines = len(str(body).splitlines())
    if expected_lines > 1 and got_lines < expected_lines:
        return False, f"body short: {got_lines} lines vs {expected_lines} indexed (no byte_length)"
    return True, ""


def _tally(verdicts: list[dict]) -> dict:
    states = {OFFLOADABLE: 0, NOT_OFFLOADABLE: 0, NOT_EVALUATED: 0}
    first_gate: dict[str, int] = {}
    all_gates: dict[str, int] = {}
    for v in verdicts:
        states[v["offloadable"]] = states.get(v["offloadable"], 0) + 1
        dq = v.get("disqualifiers") or []
        if dq:
            first_gate[dq[0]] = first_gate.get(dq[0], 0) + 1
            for code in dq:
                all_gates[code] = all_gates.get(code, 0) + 1
    return {
        "n": len(verdicts),
        "states": states,
        "label_rate": {
            k: (round(c / len(verdicts), 4) if verdicts else None)
            for k, c in states.items()
        },
        "first_gate": dict(sorted(first_gate.items(), key=lambda kv: -kv[1])),
        "all_gates": dict(sorted(all_gates.items(), key=lambda kv: -kv[1])),
    }


def run_identity_arm(repo: str, index, sample_ids: list[str]) -> dict:
    """Identity lookups, read from the SERVED annotation where possible.

    ⚠ This arm used to build its own :class:`EvidenceShape` from the response.
    That measured a shape the caller never receives, so a defect in the wiring
    — the thing an end user actually gets — was invisible to it. Now that
    ``get_symbol_source`` emits ``_meta.offloadable`` under its env gate, the
    harness turns the gate on and reads what the tool served.

    The locally-computed verdict is kept as a fallback for an unwired build,
    and the SOURCE of every verdict is counted, because a silent fallback that
    keeps answering while the wiring is broken is exactly the failure mode the
    node-mark line map had.
    """
    verdicts: list[dict] = []
    falsified: list[dict] = []
    sources: dict[str, int] = {}
    disagreements: list[dict] = []
    errors = 0
    os.environ[offload.ENV_GATE] = "1"
    for sid in sample_ids:
        try:
            resp = get_symbol_source(repo=repo, symbol_id=sid)
        except Exception:
            errors += 1
            continue
        shape = shape_from_response(
            resp,
            retrieval_mode=MODE_IDENTITY,
            verify_with={"tool": "get_symbol_source", "args": {"symbol_id": sid, "verify": True}},
        )
        local = classify(shape)
        served = (resp.get("_meta") or {}).get("offloadable")
        if isinstance(served, dict) and served.get("offloadable"):
            v = served
            sources["served"] = sources.get("served", 0) + 1
            # The two must agree. They read the same payload through the same
            # criterion, so a divergence means the wiring passes different
            # arguments than the harness does — a real defect, and one only
            # visible because both are computed.
            if served.get("offloadable") != local.get("offloadable"):
                disagreements.append(
                    {
                        "symbol_id": sid,
                        "served": served.get("offloadable"),
                        "local": local.get("offloadable"),
                    }
                )
        else:
            v = local
            sources["local_fallback"] = sources.get("local_fallback", 0) + 1
        verdicts.append(v)
        if v["offloadable"] == OFFLOADABLE:
            ok, reason = _ground_truth_present(resp, sid, index)
            if not ok:
                falsified.append({"symbol_id": sid, "reason": reason})
    out = _tally(verdicts)
    out["errors"] = errors
    out["verdict_source"] = sources
    out["served_vs_local_disagreements"] = disagreements[:10]
    out["falsified"] = {
        "count": len(falsified),
        "of_positives": out["states"][OFFLOADABLE],
        "rate": (
            round(len(falsified) / out["states"][OFFLOADABLE], 4)
            if out["states"][OFFLOADABLE]
            else None
        ),
        "examples": falsified[:10],
    }
    return out


def run_batch_arm(repo: str, index, sample_ids: list[str], width: int = 6) -> dict:
    """Multi-symbol fetches, where the container rule should actually bite.

    ⚠ The identity arm is close to a tautology on a healthy index: a single
    body, fresh, untruncated, passes every rule by construction. Measured at
    100% on the first clean run, which is a criterion with no demonstrated
    selectivity rather than a criterion that works. This arm exists to make the
    shape rules earn their place, by asking for symbols that span containers.
    """
    verdicts: list[dict] = []
    falsified: list[dict] = []
    errors = 0
    batches = [sample_ids[i : i + width] for i in range(0, len(sample_ids), width)]
    for batch in batches:
        if len(batch) < 2:
            continue
        try:
            resp = get_symbol_source(repo=repo, symbol_ids=batch)
        except Exception:
            errors += 1
            continue
        shape = shape_from_response(
            resp,
            retrieval_mode=MODE_IDENTITY,
            verify_with={"tool": "get_symbol_source", "args": {"symbol_ids": batch, "verify": True}},
        )
        v = classify(shape)
        verdicts.append(v)
        if v["offloadable"] == OFFLOADABLE:
            for sid in batch:
                ok, reason = _ground_truth_present(resp, sid, index)
                if not ok:
                    falsified.append({"symbol_id": sid, "reason": reason})
    out = _tally(verdicts)
    out["errors"] = errors
    out["batch_width"] = width
    out["falsified"] = {
        "count": len(falsified),
        "of_positive_batches": out["states"][OFFLOADABLE],
        "examples": falsified[:10],
    }
    return out


def run_ranked_arm(repo: str, index, per_class: int = 4, token_budget: int = 1500) -> dict:
    """Ranked retrieval, over a call that actually returns bodies.

    ⚠ This arm used ``search_symbols``, and that made it unable to measure
    anything. Verified against the live response 2026-08-04: ``search_symbols``
    returns ``id``/``signature``/``summary`` and NO ``source``, and emits no
    ``confidence_components``. So ``NO_BODIES`` and ``TRI_STATE_UNKNOWN`` fired
    on 100% of queries in 100% of classes, structurally, and the arm's "0%
    offloadable" was a property of the tool it called rather than of the
    criterion. Same shape of error as the identity arm's 100%: a rate that
    describes the harness, not the thing under test.

    Worse, it meant the criterion's ranking branch — ``NARROW_RANK_MARGIN`` and
    ``WIDE_RANK_MARGIN``, the only rules that exist specifically FOR ranked
    retrieval — had never once been exercised on real data.

    ``get_ranked_context`` returns ``source`` per item and a ``score``, so both
    are reachable.
    """
    verdicts: list[dict] = []
    per_query = []
    by_class: dict[str, list[dict]] = {}
    errors = 0
    for spec in _derive_queries(index, per_class):
        q, cls = spec["query"], spec["class"]
        try:
            resp = get_ranked_context(repo=repo, query=q, token_budget=token_budget)
        except Exception:
            errors += 1
            continue

        # ⚠ The margin comes from the REAL `compute_confidence`, on the same
        # `score` field the ranking used, never from a second derivation here.
        # `get_ranked_context` does not put the components in `_meta` today, so
        # a wired implementation has to emit them or compute them inline; that
        # is a prerequisite recorded in stated_limits, not something the
        # harness may paper over by inventing its own margin.
        items = resp.get("context_items") or []
        margin = compute_confidence(items, score_field="score")["components"]["gap"]

        shape = shape_from_response(
            resp,
            retrieval_mode=MODE_RANKED,
            unit_field="context_items",
            verify_with={"tool": "check_references", "args": {"identifier": q}},
            rank_margin=margin,
        )
        v = classify(shape)
        verdicts.append(v)
        by_class.setdefault(cls, []).append(v)
        per_query.append(
            {
                "query": q,
                "class": cls,
                "verdict": v["offloadable"],
                "disqualifiers": v["disqualifiers"],
                "units": v["shape"]["units"],
                "containers": v["shape"]["containers"],
                "rank_margin": round(margin, 4),
            }
        )

    out = _tally(verdicts)
    out["errors"] = errors
    out["per_class"] = {cls: _tally(vs) for cls, vs in sorted(by_class.items())}
    out["per_query"] = per_query

    # The control class must never come back labelled. This is the ranked arm's
    # non-vacuity check, and it reports as a defect list rather than an
    # assertion so a failure lands in the artifact instead of aborting the run.
    out["control_violations"] = [
        r["query"]
        for r in per_query
        if r["class"] == CLASS_ABSENT and r["verdict"] == OFFLOADABLE
    ]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", required=True, help="Indexed repo id, e.g. local/foo-abc123")
    ap.add_argument("--sample", type=int, default=300, help="Symbols in the identity arm")
    ap.add_argument("--storage-path", default=None)
    ap.add_argument("--write", action="store_true", help="Write results.json beside this script")
    args = ap.parse_args()

    store = IndexStore(args.storage_path) if args.storage_path else IndexStore()
    owner, name = _split_repo(args.repo)
    index = store.load_index(owner, name)
    if index is None:
        print(f"Not indexed: {args.repo}", file=sys.stderr)
        return 2

    source_root = getattr(index, "source_root", None)
    sample_ids = _sample(index, args.sample)

    report = {
        "criterion_version": offload.CRITERION_VERSION,
        "thresholds": {
            "MIN_RANK_MARGIN": offload.MIN_RANK_MARGIN,
            "MAX_CONTAINERS": offload.MAX_CONTAINERS,
        },
        "corpus": {
            "repo": args.repo,
            "pin": _corpus_pin(source_root),
            "symbols_indexed": len(index.symbols),
            "files_indexed": len(getattr(index, "source_files", []) or []),
        },
        "arms": {
            "identity": run_identity_arm(args.repo, index, sample_ids),
            "batch": run_batch_arm(args.repo, index, sample_ids),
            "ranked": run_ranked_arm(args.repo, index),
        },
        "stated_limits": [
            "The identity arm is close to a tautology on a healthy index: a "
            "single fresh untruncated body passes every rule by construction. "
            "Its label rate characterises the corpus, NOT the criterion's "
            "selectivity. Read the batch and ranked arms for that.",
            "The batch arm's label rate is a FLOOR, not a representative rate. "
            "It batches a stride sample, which maximises container spread by "
            "construction, so CROSS_CONTAINER_SYNTHESIS fires on nearly every "
            "batch. A real batch call fetches related symbols from one or two "
            "files. Do not quote this arm as 'the criterion rejects 98% of "
            "batches'; it rejects 98% of deliberately scattered ones.",
            "The falsifier checks a NECESSARY condition (was the ground truth "
            "present in the payload), not a sufficient one (would a cheap model "
            "answer correctly). A pass is an upper bound on achievable accuracy, "
            "never an accuracy figure.",
            "One corpus, one run, no repeats. Cross-repo generalisation is "
            "unmeasured.",
            "The ranked arm's queries are DERIVED FROM THE CORPUS under test "
            "(symbol names, split into unique and ambiguous classes) plus a "
            "fixed absent-control class. n is small and each class is smaller "
            "still: it characterises the criterion's behaviour per retrieval "
            "shape, it does not estimate a population rate.",
            "The ranked arm samples NAMES, not queries a user would type. Real "
            "queries are frequently prose. Nothing here measures prose "
            "retrieval, and the per-class rates must not be read as such.",
            "Because the ranked queries are corpus-derived, the arm is NOT "
            "comparable across corpora at the query level. The per-class rates "
            "are the comparable quantity; the query list is not.",
            "PREREQUISITE FOR WIRING, measured not assumed: no ranked jCodeMunch "
            "tool emits _meta.confidence_components today, so nothing downstream "
            "can read a rank margin off a live response. The harness supplies it "
            "by calling compute_confidence on the returned items. A wired "
            "annotation must emit those components or compute them inline; until "
            "one does, the criterion's ranking branch is unreachable in "
            "production and every ranked payload would gate on TRI_STATE_UNKNOWN.",
            "The ranked arm's container counts depend on token_budget: a larger "
            "budget packs more files in and trips CROSS_CONTAINER_SYNTHESIS on "
            "its own. The budget is a harness parameter, so this arm's label "
            "rate is a rate AT THAT BUDGET and not a property of the criterion.",
            "A dirty working tree at measurement time makes the pin advisory; "
            "check corpus.pin.dirty before quoting anything. Check "
            "corpus.pin.instrument.all_committed as well: while this work is "
            "unannounced the criterion is held in .git/info/exclude, so it is "
            "invisible to `git status` and a clean tree does NOT imply a "
            "reproducible run.",
        ],
    }

    # ⚠ Emitted only when it is TRUE. It was previously in the static list, so
    # the fastapi run shipped an artifact asserting its corpus was jCodeMunch's
    # own repository. A limits section that states a limit the run does not
    # have is not a cautious limits section, it is a wrong one.
    if _is_self_corpus(source_root):
        report["stated_limits"].append(
            "Self-measurement: this corpus IS jCodeMunch's own repository, so "
            "it contains the criterion and harness being measured. Small (3 "
            "files of ~755) but not zero. Read a second, unrelated corpus "
            "before treating any number here as a property of the criterion."
        )

    print(json.dumps(report, indent=2))
    if args.write:
        # Per-corpus filename. A single fixed path silently overwrote the
        # previous corpus's result, so with three corpora only the last one
        # run survived and the file's name did not say which that was.
        out_path = OUT_PATH.with_name(
            f"results-{args.repo.replace('/', '__')}.json"
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"\nwrote {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    # Windows: a redirected stdout is cp1252; write UTF-8 (L-145, L-320).
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
