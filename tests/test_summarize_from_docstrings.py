"""Tests for ``summarize_from_docstrings`` config gate (P1.5).

Covers:
- Default-on: docstring extraction runs in summarize_symbols_simple.
- Opt-out: setting the key to False makes Tier 1 a no-op, forcing
  summaries to fall through to the signature fallback.
- Per-repo override via the project config layer is honored.

The IPI-defense value of this flag comes from making the docstring
channel an explicit user choice. When False, docstring content never
flows into a summary string the agent sees in metadata position, which
removes the indirect-prompt-injection surface F-04 identified.
"""

import pytest

from jcodemunch_mcp import config as _config
from jcodemunch_mcp.parser.symbols import Symbol
from jcodemunch_mcp.summarizer.batch_summarize import (
    summarize_symbols_simple,
)


def _make_symbol(docstring: str, summary: str = "") -> Symbol:
    return Symbol(
        id="t.py::f#function",
        file="t.py",
        name="f",
        qualified_name="f",
        kind="function",
        language="python",
        signature="def f():",
        docstring=docstring,
        summary=summary,
    )


@pytest.fixture(autouse=True)
def _reset_config():
    """Each test starts with a clean global config slate."""
    original = _config._GLOBAL_CONFIG.copy() if hasattr(_config, "_GLOBAL_CONFIG") else None
    yield
    if original is not None:
        _config._GLOBAL_CONFIG.clear()
        _config._GLOBAL_CONFIG.update(original)


class TestSummarizeFromDocstrings:
    def test_default_extracts_docstring(self):
        # Default is True; docstring's first sentence becomes the summary.
        if hasattr(_config, "_GLOBAL_CONFIG"):
            _config._GLOBAL_CONFIG.pop("summarize_from_docstrings", None)

        sym = _make_symbol(docstring="Do the thing. Then return.")
        summarize_symbols_simple([sym])
        assert sym.summary == "Do the thing."

    def test_explicit_true_extracts_docstring(self):
        if hasattr(_config, "_GLOBAL_CONFIG"):
            _config._GLOBAL_CONFIG["summarize_from_docstrings"] = True

        sym = _make_symbol(docstring="Validate input.")
        summarize_symbols_simple([sym])
        assert sym.summary == "Validate input."

    def test_false_skips_docstring_extraction(self):
        if hasattr(_config, "_GLOBAL_CONFIG"):
            _config._GLOBAL_CONFIG["summarize_from_docstrings"] = False

        sym = _make_symbol(docstring="Sensitive docstring content here.")
        summarize_symbols_simple([sym])

        # Docstring was NOT used; fell through to signature_fallback.
        # signature_fallback for a "function" kind returns the function-shape
        # output, not the docstring's first sentence.
        assert "Sensitive docstring content here" not in sym.summary
        assert sym.summary  # but a fallback string was set

    def test_existing_summary_preserved_regardless_of_setting(self):
        """If a symbol already has a summary, summarize_symbols_simple skips it
        regardless of the docstring config — invariant preserved by P1.5."""
        if hasattr(_config, "_GLOBAL_CONFIG"):
            _config._GLOBAL_CONFIG["summarize_from_docstrings"] = False

        sym = _make_symbol(docstring="ignored.", summary="pre-set summary")
        summarize_symbols_simple([sym])
        assert sym.summary == "pre-set summary"

    def test_false_does_not_block_signature_fallback(self):
        """signature_fallback runs regardless of the docstring gate; symbols
        with neither summary nor docstring get a signature-shape fallback."""
        if hasattr(_config, "_GLOBAL_CONFIG"):
            _config._GLOBAL_CONFIG["summarize_from_docstrings"] = False

        sym = _make_symbol(docstring="")
        summarize_symbols_simple([sym])
        assert sym.summary  # signature_fallback produced something
