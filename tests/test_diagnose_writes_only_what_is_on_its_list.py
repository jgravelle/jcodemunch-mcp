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
    monkeypatch.setattr(Path, "write_text", refuse)
    tool_counters.flush()  # does not raise
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


def test_every_report_passes_its_own_check(install):
    diagnose.check_allowed(_report(install, include_anon_id=True))


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
    store, _ = install
    monkeypatch.chdir(tmp_path)
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
    for word in ("tool_counters", "calls", "tokens returned", "Nothing is sent", "diagnose", "anon_id"):
        assert word in para, word
