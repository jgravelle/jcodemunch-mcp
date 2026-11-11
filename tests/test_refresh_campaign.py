"""Bounded, resumable repo-wide refresh (#395, @dkiaulakis).

We tell users to re-index in issue replies, in staleness warnings, in the
`stale_index` verdict channel, and on every `PARSER_GENERATION` bump. That advice
assumes re-indexing is cheap enough to run on request, which is true on one
laptop and false on a fleet sharing a store, where it is a scheduled maintenance
event. `refresh` slices the same work into bounded runs that resume.

The two assertions that carry this file:

1. `TestSliceDoesNotEscalateToFullReindex` — the whole feature is a lie without
   it. `index_folder` escalates a stale generation to a FULL re-parse, so before
   the exemption every "bounded" slice ran the entire unbounded maintenance
   event. Measured on an 8-file fixture: four full re-parses where four bounded
   slices were requested.

2. `TestStampOnlyOnFullCoverage` — the stamp is a claim about every symbol in the
   index. `storage/index_store.py` states the rule: an incremental save leaves it
   alone because a partially re-parsed index must keep claiming the older
   generation. A campaign that stamps early is that bug with a scheduler in front
   of it.
"""

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from jcodemunch_mcp.tools import refresh
from jcodemunch_mcp.tools.index_folder import index_folder
from jcodemunch_mcp.storage.index_store import PARSER_GENERATION
from jcodemunch_mcp.storage.sqlite_store import SQLiteIndexStore

NEWLINE = chr(10)


@pytest.fixture
def repo(tmp_path):
    """An indexed 8-file repo plus direct DB access."""
    src = tmp_path / "repo" / "pkg"
    src.mkdir(parents=True)
    for i in range(1, 9):
        (src / f"m{i}.py").write_text(f"def f{i}():{NEWLINE}    return {i}{NEWLINE}", encoding="utf-8")
    store_dir = tmp_path / "store"
    store_dir.mkdir()
    out = index_folder(
        path=str(tmp_path / "repo"), use_ai_summaries=False, storage_path=str(store_dir)
    )
    assert out.get("success"), out
    dbs = list(store_dir.glob("*.db"))
    assert len(dbs) == 1, dbs
    return {
        "root": str(tmp_path / "repo"),
        "store": str(store_dir),
        "db": dbs[0],
        "repo_id": out["repo"],
    }


def _sql(db, query, *args):
    conn = sqlite3.connect(str(db))
    try:
        cur = conn.execute(query, args)
        conn.commit()
        return cur.fetchall()
    finally:
        conn.close()


def _generation(db) -> int:
    rows = _sql(db, "SELECT value FROM meta WHERE key='parser_generation'")
    return int(rows[0][0]) if rows else 0


def _damage(db):
    """Reproduce the #414 shape: stored symbols wrong, file content identical."""
    _sql(db, "UPDATE meta SET value='0' WHERE key='parser_generation'")
    _sql(db, "UPDATE symbols SET name='WRONG_NAME' WHERE name='f5'")


def _f5_ok(db) -> bool:
    return _sql(db, "SELECT COUNT(*) FROM symbols WHERE name='f5'")[0][0] == 1


class TestForceReparse:
    """The primitive. Without it, slicing is impossible."""

    def test_subset_refresh_is_a_no_op_without_force(self, repo):
        """Not a bug — the correct answer for an edit, and the reason a
        generation upgrade could not be sliced before `force_reparse`."""
        out = index_folder(
            path=repo["root"], paths=["pkg/m1.py"], incremental=True,
            use_ai_summaries=False, storage_path=repo["store"],
        )
        assert out.get("success")
        assert out.get("message") == "No changes detected"

    def test_force_reparse_repairs_stored_symbols(self, repo):
        """Content is unchanged, so only a forced re-parse can fix this."""
        _damage(repo["db"])
        assert not _f5_ok(repo["db"])
        out = index_folder(
            path=repo["root"], paths=["pkg/m5.py"], incremental=True,
            force_reparse=True, use_ai_summaries=False, storage_path=repo["store"],
        )
        assert out.get("success"), out
        assert _f5_ok(repo["db"]), "forced re-parse did not rewrite the symbol"

    def test_force_reparse_without_paths_does_not_force_the_corpus(self, repo):
        """`force_reparse` is scoped to an explicit list on purpose. Forcing
        without one would re-parse everything in a single call, which is the
        unbounded event this issue exists to avoid."""
        out = index_folder(
            path=repo["root"], incremental=True, force_reparse=True,
            use_ai_summaries=False, storage_path=repo["store"],
        )
        assert out.get("success")
        assert out.get("message") == "No changes detected"

    def test_force_reparse_leaves_unlisted_files_alone(self, repo):
        _damage(repo["db"])
        before = _sql(repo["db"], "SELECT COUNT(*) FROM symbols WHERE name='f7'")[0][0]
        index_folder(
            path=repo["root"], paths=["pkg/m1.py"], incremental=True,
            force_reparse=True, use_ai_summaries=False, storage_path=repo["store"],
        )
        after = _sql(repo["db"], "SELECT COUNT(*) FROM symbols WHERE name='f7'")[0][0]
        assert before == after == 1
        assert not _f5_ok(repo["db"]), "an unlisted file must not be touched"


class TestSliceDoesNotEscalateToFullReindex:
    """⚠⚠ The regression that would make the whole feature a lie.

    `index_folder` turns a stale `parser_generation` into a FULL re-parse. Before
    the exemption, each bounded slice ran the entire corpus, so a campaign of N
    slices cost N full re-indexes — strictly worse than the one event it replaced.
    """

    def test_a_forced_subset_stays_incremental_under_a_stale_generation(self, repo):
        _damage(repo["db"])
        out = index_folder(
            path=repo["root"], paths=["pkg/m5.py"], incremental=True,
            force_reparse=True, use_ai_summaries=False, storage_path=repo["store"],
        )
        assert out.get("performed_incremental") is True, (
            "the slice escalated to a full reindex; every slice would re-parse "
            "the whole corpus"
        )
        assert out.get("rebuild_reason") != "parser_generation_upgrade"

    def test_an_ordinary_call_still_escalates(self, repo):
        """Control. The exemption must be exactly one caller wide — anything
        else has no mechanism to finish the upgrade."""
        _damage(repo["db"])
        out = index_folder(
            path=repo["root"], use_ai_summaries=False, storage_path=repo["store"],
        )
        assert out.get("rebuild_reason") == "parser_generation_upgrade"

    def test_an_unforced_subset_keeps_the_upgrade_due(self, repo):
        """`paths=` alone must not make the upgrade disappear.

        This read `rebuild_reason == "parser_generation_upgrade"` until #1397,
        i.e. it required the escalation to a full re-parse. Under `paths=` that
        "full" re-parse covered the listed file only and saved it as the whole
        index, so the old assertion passed exactly while seven of eight files
        were dropped. The property it protected is that an unforced subset
        cannot quietly skip the upgrade: the generation stays due, the warning
        names it, and the next run without `paths=` performs it.
        """
        _damage(repo["db"])
        out = index_folder(
            path=repo["root"], paths=["pkg/m5.py"], incremental=True,
            use_ai_summaries=False, storage_path=repo["store"],
        )
        assert out.get("success"), out
        assert "rebuild_reason" not in out
        assert any("parser_generation_upgrade" in w for w in out.get("warnings") or [])
        assert _sql(repo["db"], "SELECT COUNT(*) FROM files")[0][0] == 8
        assert _generation(repo["db"]) < PARSER_GENERATION
        full = index_folder(
            path=repo["root"], use_ai_summaries=False, storage_path=repo["store"],
        )
        assert full.get("rebuild_reason") == "parser_generation_upgrade"
        assert _generation(repo["db"]) == PARSER_GENERATION


class TestBoundedAndResumable:
    def test_a_run_respects_its_file_budget(self, repo):
        out = refresh.run(repo["root"], max_files=3, batch_size=2, storage_path=repo["store"])
        assert out["files_this_run"] <= 3
        assert out["stopped_because"] == "file_budget"
        assert out["complete"] is False

    def test_running_again_continues_from_the_cursor(self, repo):
        first = refresh.run(repo["root"], max_files=3, batch_size=1, storage_path=repo["store"])
        second = refresh.run(repo["root"], max_files=3, batch_size=1, storage_path=repo["store"])
        assert second["completed_files"] > first["completed_files"]
        assert second["completed_files"] == first["completed_files"] + second["files_this_run"]

    def test_repeated_runs_converge(self, repo):
        seen = 0
        for _ in range(10):
            out = refresh.run(repo["root"], max_files=2, batch_size=1, storage_path=repo["store"])
            seen += 1
            if out["complete"]:
                break
        assert out["complete"], "campaign never finished"
        assert out["remaining_files"] == 0
        assert seen > 1, "fixture too small to prove slicing"

    def test_a_time_budget_stops_the_run(self, repo):
        out = refresh.run(
            repo["root"], max_seconds=0.001, max_files=999, batch_size=1,
            storage_path=repo["store"],
        )
        assert out["stopped_because"] in ("time_budget", "complete")

    def test_reset_re_enumerates(self, repo):
        refresh.run(repo["root"], max_files=4, batch_size=2, storage_path=repo["store"])
        out = refresh.run(repo["root"], max_files=1, reset=True, storage_path=repo["store"])
        assert out["campaign_started"] is True
        assert out["completed_files"] <= 1

    def test_a_completed_campaign_is_idempotent(self, repo):
        for _ in range(10):
            out = refresh.run(repo["root"], storage_path=repo["store"])
            if out["complete"]:
                break
        again = refresh.run(repo["root"], storage_path=repo["store"])
        assert again["files_this_run"] == 0
        assert again["complete"] is True

    def test_zero_budgets_are_refused(self, repo):
        assert refresh.run(repo["root"], max_files=0, storage_path=repo["store"])["success"] is False
        assert refresh.run(repo["root"], max_seconds=0, storage_path=repo["store"])["success"] is False


class TestStampOnlyOnFullCoverage:
    """⚠⚠ The stamp claims every symbol in the index was produced by this
    generation. Partial coverage must never claim it."""

    def test_partial_coverage_does_not_stamp(self, repo):
        _damage(repo["db"])
        out = refresh.run(repo["root"], max_files=4, batch_size=2, reset=True,
                          storage_path=repo["store"])
        assert out["complete"] is False
        assert _generation(repo["db"]) == 0, "stamped an index that is only half re-parsed"

    def test_full_coverage_stamps(self, repo):
        _damage(repo["db"])
        refresh.run(repo["root"], max_files=4, batch_size=2, reset=True, storage_path=repo["store"])
        out = refresh.run(repo["root"], storage_path=repo["store"])
        assert out["complete"] is True
        assert out["stamped"] is True
        assert _generation(repo["db"]) == PARSER_GENERATION
        assert _f5_ok(repo["db"]), "campaign completed without repairing the damage"

    def test_corpus_growth_defers_the_stamp(self, repo):
        """Exhausting the manifest is not covering the corpus. A repo that is
        still being worked on grows underneath a campaign."""
        _damage(repo["db"])
        refresh.run(repo["root"], max_files=4, batch_size=2, reset=True, storage_path=repo["store"])
        Path(repo["root"], "pkg", "m99.py").write_text(
            f"def f99():{NEWLINE}    return 99{NEWLINE}", encoding="utf-8"
        )
        out = refresh.run(repo["root"], storage_path=repo["store"])
        assert out["stamped"] is False
        assert out["stamp_skipped_reason"] == "corpus_drifted"
        assert out["corpus_drifted"] == 1
        assert _generation(repo["db"]) == 0

    def test_a_drifted_campaign_still_converges(self, repo):
        _damage(repo["db"])
        refresh.run(repo["root"], max_files=4, batch_size=2, reset=True, storage_path=repo["store"])
        Path(repo["root"], "pkg", "m99.py").write_text(
            f"def f99():{NEWLINE}    return 99{NEWLINE}", encoding="utf-8"
        )
        for _ in range(6):
            out = refresh.run(repo["root"], storage_path=repo["store"])
            if out.get("stamped"):
                break
        assert out["stamped"] is True
        assert _generation(repo["db"]) == PARSER_GENERATION

    def test_batch_errors_block_the_stamp(self, repo, monkeypatch):
        _damage(repo["db"])
        real = refresh.index_folder if hasattr(refresh, "index_folder") else None
        import jcodemunch_mcp.tools.index_folder as ifmod

        def _boom(*a, **k):
            raise RuntimeError("parse exploded")

        monkeypatch.setattr(ifmod, "index_folder", _boom)
        out = refresh.run(repo["root"], reset=True, storage_path=repo["store"])
        assert out.get("errors")
        assert out.get("stamped") is False
        assert out.get("stamp_skipped_reason") == "batch_errors"
        assert _generation(repo["db"]) == 0
        assert real is None or True


class TestStampPrimitive:
    def test_it_refuses_to_move_backwards(self, repo):
        store = SQLiteIndexStore(base_path=repo["store"])
        owner, name = repo["repo_id"].split("/", 1)
        assert _generation(repo["db"]) == PARSER_GENERATION
        assert store.stamp_parser_generation(owner, name, 0) is False
        assert _generation(repo["db"]) == PARSER_GENERATION

    def test_it_refuses_a_repeat_of_the_current_value(self, repo):
        store = SQLiteIndexStore(base_path=repo["store"])
        owner, name = repo["repo_id"].split("/", 1)
        assert store.stamp_parser_generation(owner, name, PARSER_GENERATION) is False

    def test_it_advances(self, repo):
        store = SQLiteIndexStore(base_path=repo["store"])
        owner, name = repo["repo_id"].split("/", 1)
        _sql(repo["db"], "UPDATE meta SET value='0' WHERE key='parser_generation'")
        assert store.stamp_parser_generation(owner, name, PARSER_GENERATION) is True
        assert _generation(repo["db"]) == PARSER_GENERATION

    def test_a_missing_index_is_not_an_exception(self, tmp_path):
        store = SQLiteIndexStore(base_path=str(tmp_path))
        assert store.stamp_parser_generation("local", "nope", 1) is False


class TestStatusAndRefusals:
    def test_status_does_no_work(self, repo):
        _damage(repo["db"])
        out = refresh.status(repo["root"], storage_path=repo["store"])
        assert out["needs_refresh"] is True
        assert out["needs_refresh_reason"] == "parser_generation_upgrade"
        assert out["campaign"] is None
        assert _generation(repo["db"]) == 0, "status mutated the index"
        assert not _f5_ok(repo["db"]), "status did work"

    def test_status_reports_progress(self, repo):
        refresh.run(repo["root"], max_files=4, batch_size=2, storage_path=repo["store"])
        c = refresh.status(repo["root"], storage_path=repo["store"])["campaign"]
        assert c["completed_files"] == 4
        assert c["total_files"] == 8
        assert c["percent"] == 50.0
        assert c["complete"] is False

    def test_refresh_refuses_a_repo_with_no_index(self, tmp_path):
        folder = tmp_path / "unindexed"
        (folder / "pkg").mkdir(parents=True)
        (folder / "pkg" / "a.py").write_text("def a(): pass" + NEWLINE, encoding="utf-8")
        out = refresh.run(str(folder), storage_path=str(tmp_path / "store"))
        assert out["success"] is False
        assert "index" in out["error"].lower()
        assert "jcodemunch-mcp index" in out["error"], "the refusal must name the fix"

    def test_a_non_directory_is_refused(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("x", encoding="utf-8")
        assert refresh.run(str(f), storage_path=str(tmp_path))["success"] is False


class TestTheCliSurface:
    """⚠⚠ `refresh --json` shipped BROKEN in v1.108.259 and stayed broken for
    four releases.

    The handler called `_json.dumps(...)`, copying a name other branches use.
    Several handlers further down do `import json as _json`, which makes `_json`
    a LOCAL for the whole of `main()`, so a branch dispatched ABOVE those imports
    raises `UnboundLocalError` -- not `NameError`, and not anything the reader
    notices while copying the line.

    Two independent things missed it: ruff reported F821 on every one of those
    four releases and nobody read the check, and nothing in this file touched the
    CLI at all. The tests exercised `run()` and `status()` directly, which is the
    layer BELOW the defect. Testing the function you wrote instead of the command
    the user types is how a whole flag ships dead.
    """

    def _cli(self, args, storage):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
        env["PYTHONIOENCODING"] = "utf-8"
        return subprocess.run(
            [sys.executable, "-m", "jcodemunch_mcp.server", "refresh", *args,
             "--storage-path", storage],
            capture_output=True, text=True, encoding="utf-8", env=env, timeout=300,
        )

    def test_status_json_is_parseable(self, repo):
        out = self._cli([repo["root"], "--status", "--json"], repo["store"])
        assert out.returncode == 0, out.stderr[-2000:]
        body = json.loads(out.stdout)
        assert body["success"] is True
        assert body["repo"] == repo["repo_id"]

    def test_run_json_is_parseable(self, repo):
        out = self._cli([repo["root"], "--max-files", "2", "--json"], repo["store"])
        assert out.returncode == 0, out.stderr[-2000:]
        body = json.loads(out.stdout)
        assert body["success"] is True
        assert body["files_this_run"] <= 2

    def test_no_unbound_local_anywhere_in_the_json_path(self, repo):
        """The exact failure mode, named so a regression is unambiguous."""
        for args in ([repo["root"], "--status", "--json"],
                     [repo["root"], "--max-files", "1", "--json"]):
            out = self._cli(args, repo["store"])
            assert "UnboundLocalError" not in out.stderr, out.stderr[-1500:]
            assert "NameError" not in out.stderr, out.stderr[-1500:]

    def test_text_output_still_works(self, repo):
        """Control: the path that was always fine stays fine."""
        out = self._cli([repo["root"], "--status"], repo["store"])
        assert out.returncode == 0
        assert "repo:" in out.stdout

    def test_a_refusal_exits_nonzero_in_json_mode(self, repo, tmp_path):
        """An error must be machine-detectable, not just readable."""
        unindexed = tmp_path / "nope"
        (unindexed / "pkg").mkdir(parents=True)
        out = self._cli([str(unindexed), "--json"], repo["store"])
        assert out.returncode == 1
        assert json.loads(out.stdout)["success"] is False


class TestOperationalSafety:
    def test_ai_summaries_are_off_by_default(self):
        """A scheduled background job must not bill a paid summarizer API
        without being asked. `index_folder` defaults the other way."""
        import inspect
        sig = inspect.signature(refresh.run)
        assert sig.parameters["use_ai_summaries"].default is False
        assert inspect.signature(index_folder).parameters["use_ai_summaries"].default is True

    def test_a_corrupt_state_file_self_heals(self, repo):
        """A corrupt cursor must not strand the operator on every future run."""
        refresh.run(repo["root"], max_files=2, storage_path=repo["store"])
        state = Path(repo["store"]) / refresh.STATE_DIRNAME
        f = next(state.glob("*.json"))
        f.write_text("{not json at all", encoding="utf-8")
        out = refresh.run(repo["root"], max_files=2, storage_path=repo["store"])
        assert out["success"] is True
        assert out["campaign_started"] is True

    def test_state_is_written_atomically(self, repo):
        """A killed run must not leave a half-written cursor."""
        import inspect
        src = inspect.getsource(refresh._write_state)
        assert ".replace(" in src, "state write must be atomic"
        refresh.run(repo["root"], max_files=2, storage_path=repo["store"])
        state = Path(repo["store"]) / refresh.STATE_DIRNAME
        assert not list(state.glob("*.tmp")), "temp file left behind"

    def test_state_round_trips_as_json(self, repo):
        refresh.run(repo["root"], max_files=2, storage_path=repo["store"])
        f = next((Path(repo["store"]) / refresh.STATE_DIRNAME).glob("*.json"))
        data = json.loads(f.read_text(encoding="utf-8"))
        assert data["cursor"] >= 1
        assert data["files"]
        assert data["generation_target"] == PARSER_GENERATION

    def test_defaults_are_bounded(self):
        """An operator who passes no bounds must still get a bounded run, not
        the maintenance event this replaces."""
        assert refresh.DEFAULT_MAX_FILES > 0
        assert refresh.DEFAULT_MAX_SECONDS > 0
        import inspect
        sig = inspect.signature(refresh.run)
        assert sig.parameters["max_files"].default is None
        assert sig.parameters["max_seconds"].default is None
