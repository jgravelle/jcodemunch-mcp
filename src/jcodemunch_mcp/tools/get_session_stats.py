"""Get token savings stats for the current session."""

from ..storage import get_session_stats as _get_session_stats
from typing import Optional


def get_session_stats(storage_path: Optional[str] = None) -> dict:
    """Return token savings stats for the current MCP session.

    Args:
        storage_path: Custom storage path (matches other tools).

    Returns:
        Dict with session and all-time token savings, cost avoided estimates,
        per-tool breakdown, and session duration. ``savings_provenance``
        cites the committed benchmark artifacts behind the savings model
        (basis: measured — see benchmarks/provenance/measured.json).
    """
    from ..retrieval.provenance import measured_provenance

    result = _get_session_stats(base_path=storage_path)
    result["savings_provenance"] = measured_provenance()

    # How many other jcodemunch processes share this index store (jcm#375
    # follow-up). A user found 25+ live instances against one store, mostly
    # stdio servers their client never reaped, holding ~17 GB between them, and
    # had no way to see it. Read-only, PID-liveness filtered, and wrapped so a
    # registry problem can never fail a stats call.
    try:
        from ..storage.process_registry import sprawl_report

        result["processes"] = sprawl_report(storage_path)
    except Exception:  # pragma: no cover - diagnostics must never break stats
        import logging

        logging.getLogger(__name__).debug("sprawl report failed", exc_info=True)

    # Cursor agent reads of indexed code beside our lookups, counted by
    # `hook-cursor`. Present only when that hook has written a count, so no
    # other client's response changes.
    try:
        from ..storage.direct_reads import summary as _direct_reads

        block = _direct_reads(storage_path)
        if block is not None:
            result["direct_reads"] = block
    except Exception:  # pragma: no cover - diagnostics must never break stats
        import logging

        logging.getLogger(__name__).debug("direct-read summary failed", exc_info=True)
    return result
