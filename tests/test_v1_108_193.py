"""v1.108.193 — the size cap can be raised, and a withheld file blocks absence.

Both reported by @dkiaulakis (jcodemunch-mcp #375 thread, by mail 2026-07-27),
measured on 1.108.188.

The ask was small: give `MAX_FILE_SIZE` the env override its two neighbours in
`security.py` already have. The finding underneath it is not small. A file
refused for size is counted as an ordinary structural exclusion, so the coverage
contract shipped in 1.108.176 calls the index complete — correctly by its own
definition — while the exact file an agent asked about is missing. In his words:

    Size is the one exclusion reason where the file is real, current, and wanted.

That is the same disease the 1.108.171-.176 releases were about, arriving
through a different door: "I never learned that file" rendering as "that file
does not exist."
"""

import pytest

from jcodemunch_mcp import handoff
from jcodemunch_mcp.tools.index_folder import (
    WITHHELD_SKIP_REASONS,
    _coverage_report,
)


# ── the escape hatch ────────────────────────────────────────────────────────


class TestMaxFileSizeIsMovable:
    """Parity with the two limits three lines below it in security.py."""

    def test_resolver_exists_alongside_its_siblings(self):
        from jcodemunch_mcp import security

        for name in ("get_max_file_size", "get_max_index_files", "get_max_folder_files"):
            assert hasattr(security, name), name

    def test_env_var_is_registered_like_its_siblings(self):
        from jcodemunch_mcp.config import ENV_VAR_MAPPING

        assert ENV_VAR_MAPPING["JCODEMUNCH_MAX_FILE_SIZE"] == "max_file_size"

    def test_explicit_argument_wins(self):
        from jcodemunch_mcp.security import get_max_file_size

        assert get_max_file_size(1_000_000) == 1_000_000

    def test_non_positive_argument_is_rejected(self):
        from jcodemunch_mcp.security import get_max_file_size

        with pytest.raises(ValueError):
            get_max_file_size(0)
        with pytest.raises(ValueError):
            get_max_file_size(-1)

    def test_default_is_unchanged(self):
        """The ask was an escape hatch, NOT a higher default, and it stays that
        way: 500KB protects the common case from a parse worth less than it
        costs."""
        from jcodemunch_mcp.security import DEFAULT_MAX_FILE_SIZE, get_max_file_size

        assert DEFAULT_MAX_FILE_SIZE == 500 * 1024
        assert get_max_file_size() == DEFAULT_MAX_FILE_SIZE

    def test_index_folder_resolves_rather_than_hardcoding(self):
        """The constant used to be passed straight through, which is why no
        route existed at all."""
        import inspect

        from jcodemunch_mcp.tools import index_folder as mod

        src = inspect.getsource(mod)
        assert "max_size=get_max_file_size()" in src
        assert "max_size=DEFAULT_MAX_FILE_SIZE," not in src.split("def _should_index_file")[0]

    @pytest.mark.parametrize(
        "paths",
        [None, ["large.py"]],
        ids=["full-walk", "explicit-path"],
    )
    def test_index_folder_applies_configured_size_to_discovery(
        self, tmp_path, paths
    ):
        from jcodemunch_mcp import config as config_module
        from jcodemunch_mcp.security import DEFAULT_MAX_FILE_SIZE
        from jcodemunch_mcp.tools.index_folder import index_folder

        project = tmp_path / "project"
        project.mkdir()
        source = project / "large.py"
        source.write_text(
            "def marker():\n    return 1\n#" + ("x" * (DEFAULT_MAX_FILE_SIZE + 1)),
            encoding="utf-8",
        )

        original = config_module._GLOBAL_CONFIG.copy()
        config_module._GLOBAL_CONFIG.clear()
        config_module._GLOBAL_CONFIG.update(config_module.DEFAULTS)
        config_module._GLOBAL_CONFIG["max_file_size"] = source.stat().st_size

        try:
            result = index_folder(
                str(project),
                use_ai_summaries=False,
                storage_path=str(tmp_path / "store"),
                incremental=False,
                context_providers=False,
                paths=paths,
            )
        finally:
            config_module._GLOBAL_CONFIG.clear()
            config_module._GLOBAL_CONFIG.update(original)

        assert result["success"] is True, result
        assert result["file_count"] == 1


# ── the finding underneath it ───────────────────────────────────────────────


def _cov(skips, indexed=10, accepted=10):
    return _coverage_report(skips, indexed, 0, files_accepted=accepted)


class TestWithheldFilesBreakCompleteness:
    def test_an_oversize_file_makes_the_corpus_incomplete(self):
        """The defect, stated as the assertion that used to fail.

        `too_large` is refused during DISCOVERY, so the file never reaches
        `files_accepted` and the reconciliation balances perfectly. Coverage
        called that complete.
        """
        assert _cov({"too_large": 1})["complete"] is False

    def test_structural_exclusions_still_complete(self):
        """Non-vacuity, and the boundary that keeps this signal useful.

        A `.png` is `binary`, a vendored tree is `gitignore`, a lockfile is
        `wrong_extension`. Those are the corpus being DEFINED. If they blocked
        absence too, no real repo could ever prove absence and the signal would
        be worthless.
        """
        cov = _cov({"binary": 40, "gitignore": 900, "wrong_extension": 120, "secret": 2})
        assert cov["complete"] is True
        assert "withheld" not in cov

    def test_withheld_is_reported_separately_from_excluded(self):
        cov = _cov({"too_large": 2, "binary": 5})
        assert cov["withheld"] == {"too_large": 2}
        assert cov["skip_counts"] == {"too_large": 2, "binary": 5}

    def test_the_three_withheld_reasons(self):
        assert WITHHELD_SKIP_REASONS == {"too_large", "file_limit", "unreadable"}

    def test_file_limit_counts_too(self):
        assert _cov({"file_limit": 300})["complete"] is False

    def test_unknown_coverage_is_still_unknown(self):
        """An index with no files_accepted reports None, never True or False."""
        assert _coverage_report({"too_large": 1}, 10, 0)["complete"] is None


class TestAbsenceIsRefusedOverAWithheldCorpus:
    def test_refusal_names_the_cause_and_the_remedy(self):
        record = {
            "state": "absent",
            "channels": {"lexical": "ok", "index": "fresh"},
            "coverage": {"complete": False, "withheld": {"too_large": 1}},
        }
        reason = handoff.absence_refusal(record)
        assert reason is not None
        assert "too_large" in reason
        assert "re-index" in reason
        # It must NOT be the generic partial message: a reader told "the index
        # did not cover the whole tree" goes looking at their repo, when the
        # actual remedy is one setting.
        assert "did not cover the whole tree" not in reason

    def test_a_clean_corpus_still_proves_absence(self):
        record = {
            "state": "absent",
            "channels": {"lexical": "ok", "index": "fresh"},
            "coverage": {"complete": True, "skip_counts": {"binary": 3}},
        }
        assert handoff.absence_refusal(record) is None

    def test_structural_exclusions_alone_do_not_refuse(self):
        record = {
            "state": "absent",
            "channels": {"lexical": "ok", "index": "fresh"},
            "coverage": {"complete": True, "skip_counts": {"gitignore": 4000}},
        }
        assert handoff.absence_refusal(record) is None
