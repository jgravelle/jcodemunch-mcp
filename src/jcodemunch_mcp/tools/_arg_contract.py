"""Argument contract: an argument we ignored cannot back an absence claim.

Every tool reads its arguments key-by-key (``arguments.get("is_regex", False)``),
so a key the tool does not know is dropped in silence. That is fine for a typo on
an optional flag and NOT fine for one specific consequence: the call the agent
believed it made is not the call that ran, and the result can still reach
``state: "absent"`` with the note "treat this as strong evidence the target is
not present" and a citable ``evidence_ref``.

Found live while auditing #375: a ``search_text`` call passed ``regex=true``
(the parameter is ``is_regex``). The flag was dropped, the regex source text was
searched as a LITERAL substring, nothing matched, and the response minted an
absence proof over a corpus that plainly contained the target.

The rule mirrors the ``rebuilding`` gate from v1.108.168:

  * DISCLOSE on every state — a caller reading an ``ok`` result still deserves to
    know part of its call was discarded.
  * REFUSE only the absence CLAIM, by downgrading ``absent`` to ``degraded``, so
    the existing "only ``absent`` proves absence" check in
    ``handoff.absence_refusal`` does the refusing. No second rule to keep in sync.

Deliberately never rejects the call. Under the 1.x zero-surprise contract an
unknown key has always been accepted, and a client that has been sending a
harmless extra for a year must not start erroring; and a hard reject would turn a
recoverable mistake into a dead call. Disclosure is strictly additive.
"""

from __future__ import annotations

from typing import Optional, Sequence

# Keys the MCP layer itself may attach to an arguments object. `_meta` is
# reserved by the protocol (progress tokens ride there), so it is never the
# caller getting a parameter name wrong.
#
# `suppress_meta` and `_current_model` are read by the DISPATCHER rather than by
# any tool, so no per-tool schema declares them. They are honored, not dropped —
# calling them caller mistakes downgraded the verdict and silently cost a
# well-formed call its absence evidence (found while shipping #377 item 10).
PROTOCOL_KEYS = frozenset({"_meta", "suppress_meta", "_current_model"})


def unrecognized_keys(
    arguments: Optional[dict], declared: Optional[Sequence[str]]
) -> list[str]:
    """Argument keys this tool does not declare, sorted.

    Returns empty when the schema is unknown: an absent declaration is not
    evidence that a key is wrong, and guessing would manufacture false warnings
    on every tool whose schema we failed to read.
    """
    if not isinstance(arguments, dict) or not declared:
        return []
    known = set(declared) | PROTOCOL_KEYS
    return sorted(k for k in arguments if k not in known)


def _note(ignored: list[str]) -> str:
    names = ", ".join(ignored)
    subject = (
        f"{len(ignored)} arguments this tool does not accept were ignored"
        if len(ignored) > 1
        else "An argument this tool does not accept was ignored"
    )
    return (
        f"{subject}: {names}. "
        "The call that ran is not the call that was requested, so this result "
        "cannot be read as evidence the target is absent. Check the parameter "
        "name against the tool schema and search again."
    )


def apply_argument_contract(result, ignored: list[str]) -> None:
    """Disclose ignored arguments in-band; strip any absence claim built on them.

    In place, best-effort, never raises for the caller.
    """
    if not ignored or not isinstance(result, dict):
        return
    meta = result.setdefault("_meta", {})
    if not isinstance(meta, dict):
        return
    meta["ignored_arguments"] = list(ignored)
    verdict = meta.get("verdict")
    # (#872) Through the one downgrade, which also sets `absence_refused`, so
    # the dispatcher's refusal carrier reaches a default install.
    from ..retrieval.verdict import refuse_absence

    if isinstance(verdict, dict) and refuse_absence(verdict, _note(ignored)):
        # Carried so a caller inspecting the verdict alone (the shape recorded
        # by note_absence) can still see WHY it was degraded.
        verdict["ignored_arguments"] = list(ignored)
