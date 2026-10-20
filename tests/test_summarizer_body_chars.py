"""summarizer_body_chars: opt-in symbol body + decorators in the AI summary prompt.

The body comes from the text the index holds (the pipeline's `file_contents`, or
the index's content cache), never from the file on disk; it is redacted; and it
leaves the machine for a remote provider only with `allow_remote_summarizer`.
"""

import sys
import types
from unittest.mock import MagicMock, patch

from jcodemunch_mcp.parser import Symbol
from jcodemunch_mcp.redact import redact_text
from jcodemunch_mcp.summarizer import (
    BatchSummarizer,
    GeminiBatchSummarizer,
    OpenAIBatchSummarizer,
    summarize_symbols,
)

SOURCE = (
    "# éè two multi-byte chars before the symbols\n"
    "@app.route('/ping', methods=['POST'])\n"
    "def ping(x):\n"
    "    return x % 2 == 0\n"
    "\n"
    "def plain(x):\n"
    "    return x\n"
)
FILES = {"app.py": SOURCE}
REPO = "owner/name"  # a deferred-summary repo id: not a directory
LOCAL = {"OPENAI_API_BASE": "http://localhost:11434/v1"}  # a local provider needs no allow
BODY = "   | def plain(x):\n   |     return x\n"


def _symbol(repo_file, name, decorators=(), source=SOURCE):
    """A Symbol whose byte span is `name`'s `def` through the end of its body."""
    raw = source.encode("utf-8")
    start = raw.index(f"def {name}(".encode())
    end = raw.index(b"\n\n", start) if b"\n\n" in raw[start:] else len(raw) - 1
    return Symbol(
        id=f"{repo_file}::{name}#function",
        file=repo_file,
        name=name,
        qualified_name=name,
        kind="function",
        language="python",
        signature=f"def {name}(x)",
        decorators=list(decorators),
        byte_offset=start,
        byte_length=end - start,
    )


def _config(**values):
    """A `_config.get` stand-in: `values` override, everything else is the default."""
    def fake_get(key, default=None, repo=None, **kwargs):
        return values.get(key, default)

    return patch("jcodemunch_mcp.summarizer.batch_summarize._config.get", side_effect=fake_get)


def _local_env():
    """A local OpenAI-compatible base with a stub client: no real HTTP client (on Windows
    `os.environ` cleared of SYSTEMROOT makes httpx's SSL context fail)."""
    return patch.dict("os.environ", LOCAL, clear=True), patch.object(OpenAIBatchSummarizer, "_init_client")


def _prompt(symbols, body_chars=None, files=None, **config):
    """The prompt `_build_prompt` produces with summarizer_body_chars set.

    `files` is the indexed text (rel path -> content), what the pipeline holds in
    `file_contents`. `REPO` is only the config key, never a place to read from.
    """
    if body_chars is not None:
        config["summarizer_body_chars"] = body_chars
    env, client = _local_env()
    with env, client, _config(**config):
        summarizer = OpenAIBatchSummarizer(repo=REPO)
        summarizer.source_for = files.get if files is not None else None
        return summarizer._build_prompt(symbols)


def test_default_prompt_is_unchanged_even_with_decorators_and_indexed_text():
    syms = [_symbol("app.py", "ping", ["@app.route('/ping', methods=['POST'])"])]
    expected = (
        "Summarize each code symbol in ONE short sentence (max 15 words).\n"
        "Focus on what it does, not how. Use business context when available.\n"
        "\n"
        "Input:\n"
        "1. function: def ping(x)\n"
        "\n"
        "Output format: NUMBER. SUMMARY\n"
        "Example: 1. Authenticates users with username and password.\n"
        "\n"
        "Summaries:"
    )
    assert _prompt(syms, files=FILES) == expected
    assert _prompt(syms, files=FILES, body_chars=0) == expected


def test_body_and_decorators_are_added_when_set():
    syms = [_symbol("app.py", "ping", ["@app.route('/ping', methods=['POST'])"])]
    assert (
        "Input:\n"
        "1. function: def ping(x)\n"
        "   decorators: @app.route('/ping', methods=['POST'])\n"
        "   body:\n"
        "   | def ping(x):\n"
        "   |     return x % 2 == 0\n"
        "\n"
        "Output format"
    ) in _prompt(syms, files=FILES, body_chars=1500)


def test_body_is_cut_to_the_cap():
    prompt = _prompt([_symbol("app.py", "ping")], files=FILES, body_chars=17)
    # "def ping(x):\n    " is 17 characters; the rest of the line is cut.
    assert "   body:\n   | def ping(x):\n   |     \n\nOutput format" in prompt
    assert "return" not in prompt


def test_no_decorators_means_no_decorators_line_and_no_blank_line():
    prompt = _prompt([_symbol("app.py", "plain")], files=FILES, body_chars=1500)
    assert "decorators" not in prompt
    assert "Input:\n1. function: def plain(x)\n   body:\n   | def plain(x):\n   |     return x\n" in prompt
    assert "\n\n\n" not in prompt


def test_no_indexed_text_falls_back_to_the_default_prompt():
    syms = [_symbol("app.py", "ping", ["@app.route('/ping', methods=['POST'])"])]
    with_cap = _prompt(syms, files={}, body_chars=1500)
    assert with_cap == _prompt(syms)
    assert "body:" not in with_cap
    assert _prompt(syms, files=None, body_chars=1500) == with_cap


def test_only_the_symbol_without_indexed_text_falls_back_in_a_mixed_batch():
    ghost = _symbol("missing.py", "plain")
    prompt = _prompt([_symbol("app.py", "plain"), ghost], files=FILES, body_chars=1500)
    assert "1. function: def plain(x)\n   body:\n" in prompt
    assert "2. function: def plain(x)\n\nOutput format" in prompt


def test_body_is_the_indexed_text_for_a_cp1252_file():
    # A cp1252 file: the index stores its text decoded as UTF-8 with errors="replace",
    # and byte_offset/byte_length are positions in that text re-encoded as UTF-8.
    # Cutting the raw file by them sends the wrong lines.
    raw = "# caf\xe9 \xe9\xe8\n\ndef twice(x):\n    return x * 2\n".encode("cp1252")
    indexed = raw.decode("utf-8", "replace")
    sym = _symbol("old.py", "twice", source=indexed)
    prompt = _prompt([sym], files={"old.py": indexed}, body_chars=1500)
    assert "   | def twice(x):\n   |     return x * 2\n" in prompt
    # What a read of the raw file by those offsets (the PR's first version) sends:
    stale = raw[sym.byte_offset : sym.byte_offset + sym.byte_length].decode("utf-8", "replace")
    assert stale != "def twice(x):\n    return x * 2"


def test_a_repo_id_that_is_not_a_directory_is_never_read_from_the_working_directory(tmp_path, monkeypatch):
    # Deferred summaries run with repo="owner/name"; os.path.join(repo, file) then
    # resolved against the server's working directory.
    (tmp_path / "owner" / "name").mkdir(parents=True)
    (tmp_path / "owner" / "name" / "app.py").write_text(SOURCE)
    monkeypatch.chdir(tmp_path)
    syms = [_symbol("app.py", "plain")]
    assert "body:" not in _prompt(syms, body_chars=1500)  # no indexed text given
    assert "body:" in _prompt(syms, files=FILES, body_chars=1500)


def test_a_file_edited_since_indexing_sends_the_indexed_text(tmp_path):
    (tmp_path / "app.py").write_text("# edited since indexing\n" + SOURCE)
    prompt = _prompt([_symbol("app.py", "plain")], files=FILES, body_chars=1500)
    assert BODY in prompt
    assert "edited" not in prompt


def test_a_key_shaped_literal_in_the_body_is_redacted():
    key = "sk-ant-api03-" + "A1b2C3d4E5" * 8
    source = f'def connect():\n    token = "{key}"\n    return token\n'
    assert key not in redact_text(source)  # the literal is one redact_text recognises
    prompt = _prompt([_symbol("conn.py", "connect", source=source)], files={"conn.py": source}, body_chars=1500)
    assert key not in prompt
    assert "[REDACTED:" in prompt
    assert "def connect():" in prompt


def test_a_key_the_cap_would_cut_is_redacted_before_the_cut():
    key = "sk-ant-api03-" + "A1b2C3d4E5" * 8
    source = f'def connect():\n    token = "{key}"\n'
    prompt = _prompt([_symbol("conn.py", "connect", source=source)], files={"conn.py": source}, body_chars=45)
    assert "sk-ant" not in prompt


def test_default_is_zero_and_a_project_config_turns_it_on_for_that_repo_only(tmp_path):
    from jcodemunch_mcp import config as cfg

    on, off = tmp_path / "on", tmp_path / "off"
    for repo in (on, off):
        repo.mkdir()
    (on / ".jcodemunch.jsonc").write_text('{"summarizer_body_chars": 1500}')
    syms = [_symbol("app.py", "plain")]
    cfg.load_project_config(str(on))
    try:
        env, client = _local_env()
        with env, client:
            assert cfg.DEFAULTS["summarizer_body_chars"] == 0
            for repo, expected in ((on, True), (off, False)):
                summarizer = OpenAIBatchSummarizer(repo=str(repo))
                summarizer.source_for = FILES.get
                assert ("body:" in summarizer._build_prompt(syms)) is expected
    finally:
        cfg._PROJECT_CONFIGS.pop(cfg._resolve_repo_key(str(on)), None)


# --- the remote gate: one test per provider ---------------------------------


def _anthropic_sent(env, **config):
    client = MagicMock()
    client.messages.create.return_value.content = [MagicMock(text="1. Returns x.")]
    module = types.SimpleNamespace(Anthropic=MagicMock(return_value=client))
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "k", **env}, clear=True), patch.dict(
        sys.modules, {"anthropic": module}
    ), _config(summarizer_body_chars=1500, **config):
        summarizer = BatchSummarizer(repo=REPO)
        summarizer.source_for = FILES.get
        summarizer.summarize_batch([_symbol("app.py", "plain")])
    return client.messages.create.call_args[1]["messages"][0]["content"]


def _gemini_sent(**config):
    client = MagicMock()
    client.generate_content.return_value.text = "1. Returns x."
    genai = types.SimpleNamespace(configure=MagicMock(), GenerativeModel=MagicMock(return_value=client))
    google = types.ModuleType("google")
    google.generativeai = genai
    with patch.dict("os.environ", {"GOOGLE_API_KEY": "k"}, clear=True), patch.dict(
        sys.modules, {"google": google, "google.generativeai": genai}
    ), _config(summarizer_body_chars=1500, **config):
        summarizer = GeminiBatchSummarizer(repo=REPO)
        summarizer.source_for = FILES.get
        summarizer.summarize_batch([_symbol("app.py", "plain")])
    return client.generate_content.call_args[0][0]


def _stub_client(self):
    """Stand-in for OpenAIBatchSummarizer._init_client: records every POST."""
    response = MagicMock()
    response.json.return_value = {"choices": [{"message": {"content": "1. Returns x."}}]}
    self.client = MagicMock()
    self.client.post.return_value = response
    _stub_client.last = self.client


def _prompts(client):
    return [c[1]["json"]["messages"][0]["content"] for c in client.post.call_args_list]


def _openai_requests(base, **config):
    """The prompts an OpenAI-compatible summarizer for `base` sent."""
    _stub_client.last = None
    with patch.dict("os.environ", {"OPENAI_API_BASE": base}, clear=True), _config(
        summarizer_body_chars=1500, **config
    ), patch.object(OpenAIBatchSummarizer, "_init_client", _stub_client):
        summarizer = OpenAIBatchSummarizer(repo=REPO)
        summarizer.source_for = FILES.get
        summarizer.summarize_batch([_symbol("app.py", "plain")])
    return _prompts(_stub_client.last) if _stub_client.last else []


def test_default_anthropic_endpoint_gets_no_body_without_allow_remote_summarizer():
    sent = _anthropic_sent({})
    assert "body:" not in sent
    assert "1. function: def plain(x)" in sent


def test_default_anthropic_endpoint_gets_the_body_with_allow_remote_summarizer():
    assert BODY in _anthropic_sent({}, allow_remote_summarizer=True)


def test_local_anthropic_base_gets_the_body_without_allow_remote_summarizer():
    assert BODY in _anthropic_sent({"ANTHROPIC_BASE_URL": "http://localhost:8080"})


def test_a_remote_anthropic_base_that_is_ignored_leaves_the_remote_default_gated():
    # Without the allow, the base is ignored and the default (remote) endpoint is used.
    assert "body:" not in _anthropic_sent({"ANTHROPIC_BASE_URL": "https://proxy.example.com"})
    assert BODY in _anthropic_sent({"ANTHROPIC_BASE_URL": "https://proxy.example.com"}, allow_remote_summarizer=True)


def test_gemini_gets_no_body_without_allow_remote_summarizer():
    sent = _gemini_sent()
    assert "body:" not in sent
    assert "1. function: def plain(x)" in sent


def test_gemini_gets_the_body_with_allow_remote_summarizer():
    assert BODY in _gemini_sent(allow_remote_summarizer=True)


def test_remote_openai_compatible_base_sends_nothing_without_allow_remote_summarizer():
    assert _openai_requests("https://api.example.com/v1") == []


def test_remote_openai_compatible_base_gets_the_body_with_allow_remote_summarizer():
    (sent,) = _openai_requests("https://api.example.com/v1", allow_remote_summarizer=True)
    assert BODY in sent


def test_local_openai_compatible_base_gets_the_body_without_allow_remote_summarizer():
    (sent,) = _openai_requests("http://localhost:11434/v1")
    assert BODY in sent


def test_a_blocked_body_is_said_once_in_the_log(caplog):
    with caplog.at_level("WARNING"):
        _anthropic_sent({})
    said = [r for r in caplog.records if "summarizer_body_chars" in r.getMessage()]
    assert len(said) == 1
    assert "allow_remote_summarizer" in said[0].getMessage()


# --- the callers hand the summarizer the indexed text ------------------------


def _local_prompts(summarize):
    """Run `summarize()` against a local OpenAI-compatible stub; the prompts it sent."""
    _stub_client.last = None
    with patch.dict("os.environ", {"OPENAI_API_BASE": "http://localhost:11434/v1"}, clear=True), _config(
        summarizer_body_chars=1500
    ), patch.object(OpenAIBatchSummarizer, "_init_client", _stub_client):
        summarize()
    return _prompts(_stub_client.last) if _stub_client.last else []


def test_summarize_symbols_sends_the_text_it_is_given():
    prompts = _local_prompts(lambda: summarize_symbols([_symbol("app.py", "plain")], repo=REPO, source_for=FILES.get))
    assert any(BODY in p for p in prompts)


def test_summarize_symbols_without_text_sends_no_body():
    prompts = _local_prompts(lambda: summarize_symbols([_symbol("app.py", "plain")], repo=REPO))
    assert prompts and all("body:" not in p for p in prompts)


def test_the_deferred_pipeline_summarizes_from_file_contents():
    from jcodemunch_mcp.tools._indexing_pipeline import deferred_summarize

    prompts = _local_prompts(lambda: deferred_summarize([_symbol("app.py", "plain")], FILES, repo=REPO))
    assert any(BODY in p for p in prompts)


def test_the_full_pipeline_summarizes_from_file_contents():
    from jcodemunch_mcp.tools._indexing_pipeline import parse_and_prepare_full

    prompts = _local_prompts(lambda: parse_and_prepare_full(FILES, repo=REPO))
    assert any(BODY in p for p in prompts)


def test_summarize_repo_sends_the_index_content_cache(tmp_path):
    # An index whose files are not on this disk (as for index_repo): the body comes
    # from the content cache the index keeps, the text get_symbol_source reads.
    from jcodemunch_mcp.storage import IndexStore
    from jcodemunch_mcp.tools.summarize_repo import summarize_repo

    IndexStore(base_path=str(tmp_path)).save_index(
        owner="o", name="n", source_files=["app.py"], symbols=[_symbol("app.py", "plain")],
        raw_files=FILES, languages={"python": 1},
    )
    prompts = _local_prompts(lambda: summarize_repo("o/n", force=True, storage_path=str(tmp_path)))
    assert any(BODY in p for p in prompts)


def test_the_content_cache_read_stays_inside_the_cache(tmp_path):
    from jcodemunch_mcp.storage import IndexStore

    (tmp_path / "secret.txt").write_text(SOURCE)
    store = IndexStore(base_path=str(tmp_path / "store"))
    store.save_index(
        owner="o", name="n", source_files=["app.py"], symbols=[_symbol("app.py", "plain")],
        raw_files=FILES, languages={"python": 1},
    )
    assert store.read_cached_source("o", "n", "app.py") == SOURCE
    assert store.read_cached_source("o", "n", "../../secret.txt") is None
    assert store.read_cached_source("o", "n", str(tmp_path / "secret.txt")) is None


def test_a_source_reader_that_raises_leaves_the_symbol_signature_only_and_is_logged(caplog):
    # A broken content cache must not look like the option being off.
    def broken(rel):
        raise OSError("cache unreadable")

    syms = [_symbol("app.py", "plain")]
    env, client = _local_env()
    with env, client, _config(summarizer_body_chars=1500), caplog.at_level(
        "DEBUG", logger="jcodemunch_mcp.summarizer.batch_summarize"
    ):
        summarizer = OpenAIBatchSummarizer(repo=REPO)
        summarizer.source_for = broken
        prompt = summarizer._build_prompt(syms)
    assert "body:" not in prompt
    (record,) = [r for r in caplog.records if "app.py" in r.getMessage()]
    assert record.exc_info and "cache unreadable" in str(record.exc_info[1])
