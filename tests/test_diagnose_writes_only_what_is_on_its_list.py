"""`jcodemunch-mcp diagnose` writes one local file built from a fixed list of fields.

The status commands print full paths and repository names, so a user asked what
the product did for them had nothing safe to hand over. Calls per tool lived
only inside a running process, and tokens returned per tool were recorded
nowhere.

`diagnose` builds its report from an allowlist: a key nobody listed fails the
build, an index is `index-1` and never its name, a tool name outside the
catalog is folded into `other`. Two local counters (calls and tokens returned,
per tool) are kept in `_tool_counters.json`, beside and apart from the savings
file whose three-field telemetry payload is unchanged. Nothing is sent.
"""

from __future__ import annotations

import ast
import asyncio
import json
import re
from pathlib import Path

import pytest

from jcodemunch_mcp import diagnose, server
from jcodemunch_mcp.storage import tool_counters

CANARY = "zzcanaryrepo"
CATALOG = ("search_symbols", "list_repos")


@pytest.fixture()
def store(tmp_path, monkeypatch):
    root = tmp_path / "store"
    root.mkdir()
    monkeypatch.setenv("CODE_INDEX_PATH", str(root))
    monkeypatch.setattr(tool_counters, "_state", tool_counters._State())
    return root


@pytest.fixture(autouse=True)
def _own_policy_files(tmp_path, monkeypatch):
    """The default policy targets include the developer's ~/.claude/CLAUDE.md; every test here reads its own."""
    monkeypatch.setattr(diagnose, "_default_policy_paths", lambda: {"claude_md_project": tmp_path / "none" / "CLAUDE.md"})


def _call(tool: str, args: dict):
    return asyncio.run(server.call_tool(tool, args))


def _counters(root: Path) -> dict:
    tool_counters.flush()
    return json.loads((root / tool_counters.FILE).read_text(encoding="utf-8"))


# --- the two counters ---------------------------------------------------------------------


def test_a_tool_call_is_counted_with_the_size_of_its_answer(store):
    out = _call("list_repos", {})
    size = sum(len(c.text.encode("utf-8")) for c in out)
    _call("list_repos", {})
    data = _counters(store)
    row = data["tools"]["list_repos"]
    assert row["calls"] == 2
    assert row["tokens_returned"] >= size // 4 > 0
    assert data["estimator"] == "bytes/4", "a count with no named estimator reads as a tokenizer count"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", data["since"])
    assert set(data) == {"version", "since", "estimator", "tools"}
    assert set(row) == {"calls", "tokens_returned"}


def test_an_error_answer_is_counted_too(store):
    _call("get_file_outline", {"repo": "no/such-repo", "file_path": "x.py"})
    assert _counters(store)["tools"]["get_file_outline"]["calls"] == 1


def test_a_name_outside_the_catalog_is_not_written(store):
    """The name comes from the client; an arbitrary string must not reach the file."""
    _call("list_repos", {})
    _call("C:/Users/someone/secret-project", {})
    assert set(_counters(store)["tools"]) == {"list_repos"}


def test_switched_off_it_records_nothing_and_writes_no_file(store, monkeypatch):
    from jcodemunch_mcp import config

    real = config.get
    monkeypatch.setattr(config, "get", lambda k, d=None, *a, **kw: False if k == "tool_counters" else real(k, d, *a, **kw))
    _call("list_repos", {})
    tool_counters.flush()
    assert not (store / tool_counters.FILE).exists()


def test_it_is_on_by_default():
    from jcodemunch_mcp import config

    assert config.DEFAULTS["tool_counters"] is True


def test_a_counter_failure_leaves_the_answer_alone(store, monkeypatch):
    plain = _call("list_repos", {})[0].text

    def boom(*a, **k):
        raise RuntimeError("no")

    monkeypatch.setattr(tool_counters, "record", boom)
    again = _call("list_repos", {})
    assert not getattr(again, "isError", False)
    assert json.loads(again[0].text)["count"] == json.loads(plain)["count"]


def test_an_unwritable_file_is_not_an_error(store, monkeypatch):
    def refuse(self, *a, **k):
        raise OSError("read-only")

    tool_counters.record("list_repos", 400)
    with monkeypatch.context() as m:
        m.setattr(Path, "write_text", refuse)
        tool_counters.flush()  # does not raise
        assert not (store / tool_counters.FILE).exists()
        # A due write goes through the state itself, not the wrapper above.
        tool_counters.record("list_repos", 400)
        tool_counters._state.flush()
        assert not (store / tool_counters.FILE).exists()
        assert not list(store.glob("*.tmp")) and not (store / tool_counters.LOCK_FILE).exists()
    # The temp file was written and the replace refused (the file is open elsewhere): no temp file stays.
    with monkeypatch.context() as m:
        m.setattr(Path, "replace", refuse)
        tool_counters.flush()
        assert not (store / tool_counters.FILE).exists()
        assert not [p.name for p in store.iterdir() if p.name.endswith(".tmp")]
    # A write that could not be made kept its counts: the next one carries them.
    assert _counters(store)["tools"]["list_repos"] == {"calls": 2, "tokens_returned": 200}


def test_a_flush_that_cannot_take_the_lock_keeps_its_counts(store, monkeypatch):
    monkeypatch.setattr(tool_counters, "_LOCK_WAIT", 0.05)
    (store / tool_counters.LOCK_FILE).write_text("", encoding="utf-8")  # another process is writing
    tool_counters.record("list_repos", 400)
    tool_counters.flush(0.05)
    assert not (store / tool_counters.FILE).exists()
    (store / tool_counters.LOCK_FILE).unlink()
    assert _counters(store)["tools"]["list_repos"]["calls"] == 1


def test_a_lock_being_deleted_by_its_holder_is_waited_for(store, monkeypatch):
    """On Windows that create raises PermissionError, not FileExistsError; giving up there lost exit-time counts."""
    import os

    real, refusals = os.open, []

    def held(path, flags, *a, **k):
        if str(path).endswith(tool_counters.LOCK_FILE) and len(refusals) < 3:
            refusals.append(1)
            raise PermissionError(13, "delete pending")
        return real(path, flags, *a, **k)

    monkeypatch.setattr(os, "open", held)
    tool_counters.record("list_repos", 400)
    assert _counters(store)["tools"]["list_repos"]["calls"] == 1
    assert len(refusals) == 3


def test_a_storage_directory_that_is_gone_is_not_waited_for(tmp_path):
    import time

    t0 = time.monotonic()
    assert tool_counters._acquire(tmp_path / "gone" / tool_counters.LOCK_FILE, 5.0) is False
    assert time.monotonic() - t0 < 1.0


def test_a_write_that_keeps_failing_does_not_start_a_thread_per_call(store, monkeypatch):
    """Put back, the call counter stayed over the threshold: 281 threads in 300 calls on a read-only directory."""
    started = []
    with monkeypatch.context() as m:
        m.setattr(tool_counters._State, "_flush_in_thread", lambda self: started.append(1))
        m.setattr(tool_counters, "_merge_into_file", lambda *a, **k: False)
        for _ in range(tool_counters._FLUSH_EVERY):
            tool_counters.record("list_repos", 400)
        _join_writers()
        assert len(started) == 1
        tool_counters._state._flushing = False
        tool_counters.flush()  # fails, and keeps its counts
        for _ in range(tool_counters._FLUSH_EVERY - 1):
            tool_counters.record("list_repos", 400)
        _join_writers()
        assert len(started) == 1, "the kept counts wait for the next twenty calls or the clock"
    tool_counters._state._flushing = False
    assert _counters(store)["tools"]["list_repos"]["calls"] == 2 * tool_counters._FLUSH_EVERY - 1


def test_a_lock_left_by_a_dead_process_is_broken(store):
    import os
    import time

    lock = store / tool_counters.LOCK_FILE
    lock.write_text("", encoding="utf-8")
    old = time.time() - tool_counters._LOCK_STALE_SECONDS - 5
    os.utime(lock, (old, old))
    tool_counters.record("list_repos", 400)
    assert _counters(store)["tools"]["list_repos"]["calls"] == 1
    assert not lock.exists()


def test_a_file_that_is_busy_this_instant_is_not_replaced_by_an_empty_one(store, monkeypatch):
    """Merging into an empty record because a read failed would erase every count on disk."""
    tool_counters.record("list_repos", 400)
    before = _counters(store)
    real = Path.read_text

    def busy(self, *a, **k):
        if self.name == tool_counters.FILE:
            raise PermissionError("in use")
        return real(self, *a, **k)

    tool_counters.record("search_symbols", 400)
    with monkeypatch.context() as m:
        m.setattr(Path, "read_text", busy)
        tool_counters.flush()
    assert json.loads((store / tool_counters.FILE).read_text(encoding="utf-8")) == before
    after = _counters(store)["tools"]
    assert after["list_repos"]["calls"] == 1 and after["search_symbols"]["calls"] == 1


def test_processes_that_stop_together_lose_no_count(store):
    """Real processes: the first draft kept 5 of 40 counts here, twelve trials in twelve."""
    import subprocess
    import sys

    (store / tool_counters.FILE).write_text(json.dumps(
        {"version": 1, "since": "2026-01-01", "estimator": "bytes/4",
         "tools": {"list_repos": {"calls": 1000, "tokens_returned": 5}}}), encoding="utf-8")
    code = "\n".join([
        "import sys, time",
        "from jcodemunch_mcp.storage import tool_counters as t",
        "[t.record('list_repos', 400, sys.argv[1]) for _ in range(5)]",
        "time.sleep(max(0.0, float(sys.argv[2]) - time.time() - 0.2))",
        "while time.time() < float(sys.argv[2]):  # busy-wait to the same instant",
        "    pass",
    ])
    import os
    import time

    src = str(Path(tool_counters.__file__).resolve().parents[2])
    env = dict(os.environ, PYTHONPATH=src)
    go = str(time.time() + 4.0)  # every process exits at the same moment
    procs = [subprocess.Popen([sys.executable, "-c", code, str(store), go], env=env) for _ in range(8)]
    assert [p.wait(timeout=60) for p in procs] == [0] * 8
    data = json.loads((store / tool_counters.FILE).read_text(encoding="utf-8"))
    assert data["tools"]["list_repos"] == {"calls": 1040, "tokens_returned": 5 + 40 * 100}
    assert data["since"] == "2026-01-01", "history is kept, with its start date"
    assert not list(store.glob("*.tmp")) and not (store / tool_counters.LOCK_FILE).exists()


def _join_writers():
    import threading

    for t in threading.enumerate():
        if t.name == "jcm-tool-counters":
            t.join(timeout=10)


def test_counting_a_call_never_waits_for_the_disk(store, monkeypatch):
    """A synchronous write cost about 15 ms on every twentieth call, on the event loop."""
    import threading

    seen = []
    real = tool_counters._merge_into_file
    monkeypatch.setattr(tool_counters, "_merge_into_file",
                        lambda *a, **k: seen.append(threading.current_thread().name) or real(*a, **k))
    for _ in range(tool_counters._FLUSH_EVERY):
        tool_counters.record("list_repos", 400)
    _join_writers()
    assert seen == ["jcm-tool-counters"], "the write ran, once, and not in the caller's thread"
    data = json.loads((store / tool_counters.FILE).read_text(encoding="utf-8"))
    assert data["tools"]["list_repos"]["calls"] == tool_counters._FLUSH_EVERY


def test_a_quiet_server_writes_on_the_first_call_after_the_interval(store):
    tool_counters.record("list_repos", 400)
    assert not (store / tool_counters.FILE).exists()
    tool_counters._state._last_flush -= tool_counters._FLUSH_SECONDS + 1
    tool_counters.record("list_repos", 400)
    _join_writers()
    assert json.loads((store / tool_counters.FILE).read_text(encoding="utf-8"))["tools"]["list_repos"]["calls"] == 2


def test_a_call_relayed_by_the_front_door_has_its_size_counted_once(store):
    """`order` re-dispatches, so one request runs two frames; the bytes belong to the tool that answered."""
    out = _call("order", {"action": "list_repos", "args": {}})
    size = sum(len(c.text.encode("utf-8")) for c in out)
    tools = _counters(store)["tools"]
    assert tools["list_repos"]["calls"] == 1 and tools["order"]["calls"] == 1
    assert tools["list_repos"]["tokens_returned"] > 0
    assert tools["order"]["tokens_returned"] == 0
    assert tools["list_repos"]["tokens_returned"] <= size // 4 + 1


def test_a_front_door_answer_of_its_own_keeps_its_size(store):
    _call("menu", {"query": "find a symbol"})
    row = _counters(store)["tools"]["menu"]
    assert row["calls"] == 1 and row["tokens_returned"] > 0


def test_a_dispatch_that_raises_is_a_call_with_no_tokens(store, monkeypatch):
    async def boom(name, arguments):
        raise RuntimeError("no")

    monkeypatch.setattr(server, "_call_tool_impl", boom)
    with pytest.raises(RuntimeError):
        _call("list_repos", {})
    assert _counters(store)["tools"]["list_repos"] == {"calls": 1, "tokens_returned": 0}


def test_the_switch_is_read_from_a_real_config_file(store):
    from jcodemunch_mcp import config

    (store / "config.jsonc").write_text('{"tool_counters": false}', encoding="utf-8")
    config.load_config(str(store), create_missing=False)
    assert config.get("tool_counters") is False
    _call("list_repos", {})
    tool_counters.flush()
    assert not (store / tool_counters.FILE).exists()


def test_a_count_is_written_where_it_was_counted(store, tmp_path, monkeypatch):
    """A flush at exit must not follow the environment to another storage directory."""
    tool_counters.record("list_repos", 400)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv("CODE_INDEX_PATH", str(elsewhere))
    tool_counters.flush()
    assert (store / tool_counters.FILE).exists()
    assert not (elsewhere / tool_counters.FILE).exists()


def test_two_flushes_add_up_and_a_second_process_is_not_overwritten(store):
    tool_counters.record("list_repos", 400)
    tool_counters.flush()
    other = tool_counters._State()  # what a second server process holds
    other.record("list_repos", 800, None)
    other.record("search_symbols", 40, None)
    other.flush()
    tool_counters.record("list_repos", 400)
    data = _counters(store)
    assert data["tools"]["list_repos"] == {"calls": 3, "tokens_returned": 400}
    assert data["tools"]["search_symbols"] == {"calls": 1, "tokens_returned": 10}


def test_a_damaged_file_is_replaced_not_fatal(store):
    (store / tool_counters.FILE).write_text("{not json", encoding="utf-8")
    tool_counters.record("list_repos", 400)
    assert _counters(store)["tools"]["list_repos"]["calls"] == 1
    assert tool_counters.read(str(store))["tools"]["list_repos"]["calls"] == 1


def test_the_counters_never_touch_the_savings_file_or_the_telemetry_payload(store):
    _call("list_repos", {})
    tool_counters.flush()
    savings = store / "_savings.json"
    if savings.exists():
        assert "tokens_returned" not in savings.read_text(encoding="utf-8")
    src = Path(tool_counters.__file__).read_text(encoding="utf-8")
    assert "_savings" not in src and "anon_id" not in src


# --- the report -----------------------------------------------------------------------------


@pytest.fixture()
def install(store, tmp_path):
    """One indexed repository with a tell-tale name, a savings file and some counts."""
    from jcodemunch_mcp.tools.index_folder import index_folder

    repo = tmp_path / CANARY
    repo.mkdir()
    (repo / "mod.py").write_text("def alpha():\n    return 1\n\n\ndef beta():\n    return alpha()\n", encoding="utf-8")
    done = index_folder(str(repo), use_ai_summaries=False, storage_path=str(store))
    assert done.get("success"), done
    (store / "_savings.json").write_text(json.dumps({
        "total_tokens_saved": 900,
        "anon_id": "11111111-2222-3333-4444-555555555555",
        "by_tool": {"search_symbols": 700, CANARY + "_tool": 200},
    }), encoding="utf-8")
    tool_counters.record("search_symbols", 4000)
    tool_counters.record(CANARY + "_tool", 80)
    tool_counters.flush()
    policy = tmp_path / "policies"
    policy.mkdir()
    return store, policy


def _report(install, **kw):
    store, policy = install
    kw.setdefault("known_tools", CATALOG)
    kw.setdefault("policy_paths", {"claude_md_project": policy / "CLAUDE.md"})
    kw.setdefault("surface_stats", {"surface": "full", "profile": "full", "visible_tools": 90, "catalog_tools": 94,
                                    "schema_tokens_visible": 27132, "estimator": "bytes/4",
                                    "heaviest_tools": {CANARY: 1}})
    return diagnose.build_report(storage_path=str(store), **kw)


def test_no_name_or_path_reaches_the_report(install, tmp_path):
    text = json.dumps(_report(install, include_anon_id=True))
    assert CANARY not in text
    for private in (str(tmp_path), str(tmp_path).replace("\\", "\\\\"), tmp_path.as_posix(), str(Path.home()), Path.home().as_posix()):
        assert private not in text, private
    assert chr(92) not in text and ":/" not in text, "no value in the report is a path"


def test_an_index_is_a_label_and_its_sizes(install):
    indexes = _report(install)["indexes"]
    assert [i["label"] for i in indexes] == ["index-1"]
    row = indexes[0]
    assert row["file_count"] == 1 and row["symbol_count"] >= 2
    assert row["index_bytes"] > 0
    assert row["embeddings"] in ("yes", "no", "unknown")
    assert set(row) == {"label", "file_count", "symbol_count", "source_bytes", "index_bytes", "embeddings"}


def test_source_bytes_are_a_whole_sum_or_unknown(install):
    """Some indexing routes store no file size; a sum over the rest would read as the corpus."""
    import sqlite3

    store, _ = install
    db = next(p for p in store.glob("*.db"))
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO files (path, size_bytes) VALUES ('extra.py', NULL)")
    conn.execute("UPDATE files SET size_bytes = 59 WHERE path = 'mod.py'")
    conn.commit()
    assert _report(install)["indexes"][0]["source_bytes"] is None, "one file has no size"
    conn.execute("UPDATE files SET size_bytes = 41 WHERE path = 'extra.py'")
    conn.commit()
    conn.close()
    row = _report(install)["indexes"][0]
    assert row["source_bytes"] == 100 and row["file_count"] == 2


def test_embeddings_that_could_not_be_read_are_unknown_and_never_no(install, monkeypatch):
    from jcodemunch_mcp.storage.embedding_store import EmbeddingStore

    monkeypatch.setattr(EmbeddingStore, "has_any", lambda self: None)
    assert _report(install)["indexes"][0]["embeddings"] == "unknown"
    monkeypatch.setattr(EmbeddingStore, "has_any", lambda self: True)
    assert _report(install)["indexes"][0]["embeddings"] == "yes"
    monkeypatch.setattr(EmbeddingStore, "has_any", lambda self: False)
    assert _report(install)["indexes"][0]["embeddings"] == "no"


def test_an_index_that_cannot_be_opened_is_listed_with_unknown_counts(install):
    store, _ = install
    (store / "local-broken.db").write_bytes(b"not a database")
    rows = _report(install)["indexes"]
    assert len(rows) == 2
    broken = next(r for r in rows if r["file_count"] is None)
    assert broken["symbol_count"] is None and broken["source_bytes"] is None
    assert broken["index_bytes"] == len(b"not a database") and broken["embeddings"] == "unknown"


def test_a_tool_row_joins_the_three_counts_and_folds_an_unlisted_name(install):
    tools = _report(install)["tools"]
    assert tools["estimator"] == "bytes/4"
    assert tools["by_tool"]["search_symbols"] == {"calls": 1, "tokens_returned": 1000, "tokens_saved": 700}
    assert tools["by_tool"]["other"] == {"calls": 1, "tokens_returned": 20, "tokens_saved": 200}
    assert set(tools["by_tool"]) == {"search_symbols", "other"}
    assert tools["tokens_saved_total"] == 900


def test_nothing_counted_yet_claims_no_start_date(store):
    tools = diagnose.build_report(storage_path=str(store), policy_paths={})["tools"]
    assert tools["counting_since"] is None and tools["by_tool"] == {}
    tool_counters.record("list_repos", 4)
    tool_counters.flush()
    again = diagnose.build_report(storage_path=str(store), policy_paths={}, known_tools=CATALOG)["tools"]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", again["counting_since"])


def test_the_anon_id_is_left_out_unless_asked_for(install):
    assert "anon_id" not in _report(install)
    assert "11111111" not in json.dumps(_report(install))
    assert _report(install, include_anon_id=True)["anon_id"] == "11111111-2222-3333-4444-555555555555"


def test_a_policy_is_present_current_or_differing_with_a_digest(install):
    from jcodemunch_mcp.cli.policy import active_policy

    _, policy = install
    absent = _report(install)["policies"]
    assert absent["targets"]["claude_md_project"] == {"present": False, "state": None, "digest": None}
    (policy / "CLAUDE.md").write_text("# Mine\n\n" + active_policy() + "\n\n## My own section\n\ntext\n", encoding="utf-8")
    current = _report(install)["policies"]
    row = current["targets"]["claude_md_project"]
    assert row["present"] is True and row["state"] == "current"
    assert row["digest"] == current["this_version_digest"] and re.fullmatch(r"[0-9a-f]{12}", row["digest"])
    (policy / "CLAUDE.md").write_text(active_policy().replace("index", "catalogue", 1), encoding="utf-8")
    edited = _report(install)["policies"]["targets"]["claude_md_project"]
    assert edited["state"] == "differs" and edited["digest"] != current["this_version_digest"]


def test_the_surface_block_takes_only_its_listed_fields(install):
    surface = _report(install)["surface"]
    assert surface == {"surface": "full", "profile": "full", "visible_tools": 90, "catalog_tools": 94,
                       "schema_tokens_visible": 27132, "estimator": "bytes/4"}


def test_versions_name_the_three_servers_and_the_platform(install):
    v = _report(install)["versions"]
    assert set(v) == {"jcodemunch-mcp", "jdocmunch-mcp", "jdatamunch-mcp", "python", "os"}
    assert re.fullmatch(r"\d+\.\d+\.\d+", v["python"])
    import platform

    assert platform.node() not in v["os"] or not platform.node()


def test_a_key_nobody_listed_fails_the_build():
    good = {"schema": diagnose.SCHEMA, "generated_at": "2026-10-13T00:00:00Z"}
    diagnose.check_allowed(good)
    with pytest.raises(ValueError, match="source_root"):
        diagnose.check_allowed({**good, "indexes": [{"label": "index-1", "source_root": "C:/x"}]})
    with pytest.raises(ValueError, match="hostname"):
        diagnose.check_allowed({**good, "hostname": "box"})
    with pytest.raises(ValueError, match="label"):
        diagnose.check_allowed({**good, "indexes": [{"label": "my-private-repo"}]})


    tools = {"by_tool": {"my-private-tool": {"calls": 1}}}
    diagnose.check_allowed({**good, "tools": tools})  # no catalog given: names are not judged
    with pytest.raises(ValueError, match="my-private-tool"):
        diagnose.check_allowed({**good, "tools": tools}, known_tools=CATALOG)


@pytest.mark.parametrize("extra", [
    {"schema": {"hostname": "box", "path": "C:/Users/alice"}},
    {"surface": {"profile": {"source_root": "C:/x"}}},
    {"surface": {"profile": "C:/Users/alice/acme-secret-repo"}},
    {"surface": {"surface": "my-surface"}},
    {"indexes": [{"label": "index-1", "file_count": {"path": "C:/x"}}]},
    {"indexes": [{"label": "index-1", "embeddings": "C:/x"}]},
    {"indexes": {"label": "index-1"}},
    {"anon_id": ["C:/Users/alice"]},
    {"anon_id": "C:/Users/alice"},
    {"tools": {"estimator": "C:/Users/alice"}},
    {"tools": {"by_tool": {"other": {"calls": "C:/x"}}}},
    {"tools": {"counting_since": "last tuesday"}},
    {"versions": {"os": "Windows 11 C:\\Users\\alice"}},
    {"versions": {"jdocmunch-mcp": "/home/alice/src"}},
    {"policies": {"this_version_digest": "0123456789ab", "targets": {"agents_md": {"digest": "C:/x"}}}},
    {"sent_anywhere": True},
], ids=lambda e: next(iter(e)) + ":" + str(next(iter(e.values())))[:24])
def test_a_value_is_checked_as_well_as_its_key(extra):
    """Keys alone were checked at first, and the user's own profile string carried a path through."""
    good = {"schema": diagnose.SCHEMA, "generated_at": "2026-10-13T00:00:00Z"}
    with pytest.raises(ValueError):
        diagnose.check_allowed({**good, **extra})


def test_a_profile_the_user_named_reads_custom_through_the_real_command(store, capsys):
    """Through the producer, not a supplied dict: `tool_profile` is the user's string."""
    (store / "config.jsonc").write_text('{"tool_profile": "C:/Users/alice/acme-secret-repo"}', encoding="utf-8")
    with pytest.raises(SystemExit):
        server.main(["diagnose", "--stdout"])
    out = capsys.readouterr().out
    assert "acme-secret-repo" not in out and "alice" not in out
    assert json.loads(out)["surface"]["profile"] == "custom"


def test_stdout_writes_no_file_at_all(store, capsys):
    """The default config loader creates config.jsonc; a command documented to write nothing must not."""
    assert list(store.iterdir()) == []
    with pytest.raises(SystemExit):
        server.main(["diagnose", "--stdout"])
    assert json.loads(capsys.readouterr().out)["schema"] == diagnose.SCHEMA
    assert list(store.iterdir()) == []


def test_a_savings_file_that_cannot_be_read_gives_no_saved_figure(install):
    store, _ = install
    (store / "_savings.json").write_text("{damaged", encoding="utf-8")
    tools = _report(install)["tools"]
    assert tools["tokens_saved_total"] is None, "unknown is not zero"
    assert tools["by_tool"]["search_symbols"] == {"calls": 1, "tokens_returned": 1000, "tokens_saved": None}
    (store / "_savings.json").unlink()
    absent = _report(install)["tools"]
    assert absent["tokens_saved_total"] == 0 and absent["by_tool"]["search_symbols"]["tokens_saved"] == 0


@pytest.mark.parametrize("how", ["damaged", "busy"])
def test_a_counters_file_that_cannot_be_read_gives_no_count(install, monkeypatch, how):
    """Fixed for the savings file first and missed one file over: it read `0 calls, counting since today`."""
    store, _ = install
    if how == "damaged":
        (store / tool_counters.FILE).write_text("{damaged", encoding="utf-8")
    else:
        real = Path.read_text

        def busy(self, *a, **k):
            if self.name == tool_counters.FILE:
                raise PermissionError("in use")
            return real(self, *a, **k)

        monkeypatch.setattr(Path, "read_text", busy)
    tools = _report(install)["tools"]
    assert tools["counting_since"] is None
    assert tools["by_tool"]["search_symbols"] == {"calls": None, "tokens_returned": None, "tokens_saved": 700}


def test_a_saved_total_that_is_not_a_count_is_unknown(install):
    store, _ = install
    (store / "_savings.json").write_text(json.dumps({"total_tokens_saved": "lots", "by_tool": {"search_symbols": 700}}),
                                         encoding="utf-8")
    assert _report(install)["tools"]["tokens_saved_total"] is None


@pytest.mark.parametrize("system, release, label", [
    ("Linux", "6.6.30-alice-laptop", "Linux 6.6.30"),
    ("Windows", "11", "Windows 11"),
    ("Darwin", "23.5.0", "Darwin 23.5.0"),
    ("Linux", "5.15.167.4-microsoft-standard-WSL2", "Linux 5.15.167.4"),
    ("C:/Users/alice", "x", "unknown"),
    ("", "", "unknown"),
])
def test_the_os_label_keeps_the_name_and_the_numbers(system, release, label):
    assert diagnose._os_label(system, release) == label


def test_a_version_loses_its_local_label_and_an_odd_one_is_unknown_not_an_error(install, monkeypatch):
    assert diagnose._public_version("1.108.358+local.alice.acme") == "1.108.358"
    assert diagnose._public_version("1.2.3rc1") == "1.2.3rc1"
    assert diagnose._public_version("x" * 44) is None
    assert diagnose._public_version("/home/alice/src") is None
    from importlib import metadata

    monkeypatch.setattr(metadata, "version", lambda dist: "9" * 44 + "+alice")
    assert set(_report(install)["versions"].values()) >= {None}  # built, and checked, without raising


def test_saved_tokens_and_counts_each_carry_their_own_start_date(install):
    """Savings have been recorded far longer than calls; one date over both misleads."""
    store, _ = install
    (store / "_savings.json").write_text(json.dumps(
        {"total_tokens_saved": 5000000, "by_tool": {"search_symbols": 4000000}, "by_tool_since": "2026-07-01"}),
        encoding="utf-8")
    tools = _report(install)["tools"]
    assert tools["saved_since"] == "2026-07-01"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", tools["counting_since"]) and tools["counting_since"] != "2026-07-01"
    (store / "_savings.json").write_text(json.dumps({"by_tool_since": "C:/Users/alice"}), encoding="utf-8")
    assert _report(install)["tools"]["saved_since"] is None


def test_every_report_passes_its_own_check(install):
    diagnose.check_allowed(_report(install, include_anon_id=True))


def test_a_source_that_grows_a_field_fails_the_build_of_the_report(install, monkeypatch):
    """The check runs inside the build, so a block that gains a key cannot be returned."""
    real = diagnose._versions
    monkeypatch.setattr(diagnose, "_versions", lambda: {**real(), "hostname": "box"})
    with pytest.raises(ValueError, match="hostname"):
        _report(install)


def test_the_module_can_send_nothing():
    for mod in (diagnose, tool_counters):
        tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
        imported = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                imported |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom):
                imported.add((n.module or "").split(".")[0])
        assert not imported & {"httpx", "requests", "urllib", "urllib3", "socket", "http", "aiohttp", "smtplib"}, imported


def test_the_report_is_written_to_one_local_file(install):
    store, _ = install
    report = _report(install)
    path = diagnose.write_report(report, storage_path=str(store))
    assert path.parent == store / "diagnose"
    assert re.fullmatch(r"jcodemunch-diagnose-\d{8}T\d{6}Z\.json", path.name)
    assert json.loads(path.read_text(encoding="utf-8")) == report


def test_the_command_prints_where_the_file_is_and_stdout_writes_none(install, capsys, monkeypatch, tmp_path):
    store, policy = install
    monkeypatch.chdir(tmp_path)
    # The default targets include the developer's own ~/.claude/CLAUDE.md; a test reads its own.
    monkeypatch.setattr(diagnose, "_default_policy_paths", lambda: {"claude_md_project": policy / "CLAUDE.md"})
    with pytest.raises(SystemExit) as done:
        server.main(["diagnose"])
    assert done.value.code == 0
    out = capsys.readouterr().out
    written = list((store / "diagnose").glob("*.json"))
    assert len(written) == 1 and written[0].name in out
    assert "Nothing was sent" in out
    report = json.loads(written[0].read_text(encoding="utf-8"))
    assert "anon_id" not in report and CANARY not in json.dumps(report)
    assert report["indexes"][0]["label"] == "index-1"
    with pytest.raises(SystemExit):
        server.main(["diagnose", "--stdout", "--include-anon-id"])
    printed = json.loads(capsys.readouterr().out)
    assert printed["anon_id"].startswith("11111111")
    assert len(list((store / "diagnose").glob("*.json"))) == 1


def test_the_two_files_are_disclosed():
    text = (Path(__file__).resolve().parent.parent / "SECURITY.md").read_text(encoding="utf-8")
    para = next(p for p in text.split("\n- ") if tool_counters.FILE in p)
    config_doc = (Path(__file__).resolve().parent.parent / "CONFIGURATION.md").read_text(encoding="utf-8")
    for typed in (f"every {tool_counters._FLUSH_EVERY} calls", f"after {tool_counters._FLUSH_SECONDS:g} seconds"):
        assert typed in config_doc, "the schedule is typed in CONFIGURATION.md too"
    assert f"up to {tool_counters._LOCK_WAIT_AT_EXIT:g} seconds longer to exit" in para
    for word in ("tool_counters", "calls", "tokens returned", "Nothing is sent", "diagnose", "anon_id",
                 f"every {tool_counters._FLUSH_EVERY} calls", f"after {tool_counters._FLUSH_SECONDS:g} seconds",
                 tool_counters.LOCK_FILE, "counted under both names"):
        assert word in para, word
