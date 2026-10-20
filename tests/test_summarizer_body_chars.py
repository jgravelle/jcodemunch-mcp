"""summarizer_body_chars: opt-in symbol body + decorators in the AI summary prompt."""

from unittest.mock import MagicMock, patch

from jcodemunch_mcp.parser import Symbol
from jcodemunch_mcp.summarizer import OpenAIBatchSummarizer

SOURCE = (
    "# éè two multi-byte chars before the symbols\n"
    "@app.route('/ping', methods=['POST'])\n"
    "def ping(x):\n"
    "    return x % 2 == 0\n"
    "\n"
    "def plain(x):\n"
    "    return x\n"
)


def _symbol(repo_file, name, decorators=()):
    """A Symbol whose byte span is `name`'s `def` through the end of its body."""
    raw = SOURCE.encode("utf-8")
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


def _prompt(repo, symbols, body_chars=None):
    """The prompt `_build_prompt` produces for `repo` with summarizer_body_chars set."""
    def fake_get(key, default=None, repo=None, **kwargs):
        if key == "summarizer_body_chars" and body_chars is not None:
            return body_chars
        return default

    with patch.dict("os.environ", {}, clear=True), patch(
        "jcodemunch_mcp.summarizer.batch_summarize._config.get", side_effect=fake_get
    ):
        return OpenAIBatchSummarizer(repo=str(repo))._build_prompt(symbols)


def _repo(tmp_path):
    (tmp_path / "app.py").write_bytes(SOURCE.encode("utf-8"))
    return tmp_path


def test_default_prompt_is_unchanged_even_with_decorators_and_a_readable_file(tmp_path):
    repo = _repo(tmp_path)
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
    assert _prompt(repo, syms) == expected
    assert _prompt(repo, syms, body_chars=0) == expected


def test_body_and_decorators_are_added_when_set(tmp_path):
    repo = _repo(tmp_path)
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
    ) in _prompt(repo, syms, body_chars=1500)


def test_body_is_cut_to_the_cap(tmp_path):
    repo = _repo(tmp_path)
    prompt = _prompt(repo, [_symbol("app.py", "ping")], body_chars=17)
    # "def ping(x):\n    " is 17 characters; the rest of the line is cut.
    assert "   body:\n   | def ping(x):\n   |     \n\nOutput format" in prompt
    assert "return" not in prompt


def test_no_decorators_means_no_decorators_line_and_no_blank_line(tmp_path):
    repo = _repo(tmp_path)
    prompt = _prompt(repo, [_symbol("app.py", "plain")], body_chars=1500)
    assert "decorators" not in prompt
    assert "Input:\n1. function: def plain(x)\n   body:\n   | def plain(x):\n   |     return x\n" in prompt
    assert "\n\n\n" not in prompt


def test_unreadable_file_falls_back_to_the_default_prompt(tmp_path):
    # No app.py on disk, as for a repo indexed from GitHub.
    syms = [_symbol("app.py", "ping", ["@app.route('/ping', methods=['POST'])"])]
    with_cap = _prompt(tmp_path, syms, body_chars=1500)
    assert with_cap == _prompt(tmp_path, syms)
    assert "body:" not in with_cap


def test_only_the_symbol_without_a_file_falls_back_in_a_mixed_batch(tmp_path):
    repo = _repo(tmp_path)
    ghost = _symbol("missing.py", "plain")
    prompt = _prompt(repo, [_symbol("app.py", "plain"), ghost], body_chars=1500)
    assert "1. function: def plain(x)\n   body:\n" in prompt
    assert "2. function: def plain(x)\n\nOutput format" in prompt


def test_default_is_zero_and_a_project_config_turns_it_on_for_that_repo_only(tmp_path):
    from jcodemunch_mcp import config as cfg

    on, off = tmp_path / "on", tmp_path / "off"
    for repo in (on, off):
        repo.mkdir()
        (repo / "app.py").write_bytes(SOURCE.encode("utf-8"))
    (on / ".jcodemunch.jsonc").write_text('{"summarizer_body_chars": 1500}')
    syms = [_symbol("app.py", "plain")]
    cfg.load_project_config(str(on))
    try:
        with patch.dict("os.environ", {}, clear=True):
            assert cfg.DEFAULTS["summarizer_body_chars"] == 0
            assert "body:" in OpenAIBatchSummarizer(repo=str(on))._build_prompt(syms)
            assert "body:" not in OpenAIBatchSummarizer(repo=str(off))._build_prompt(syms)
    finally:
        cfg._PROJECT_CONFIGS.pop(cfg._resolve_repo_key(str(on)), None)


def test_body_reaches_the_llm_request(tmp_path):
    repo = _repo(tmp_path)
    response = MagicMock()
    response.json.return_value = {"choices": [{"message": {"content": "1. Returns x."}}]}
    client = MagicMock()
    client.post.return_value = response

    def fake_get(key, default=None, repo=None, **kwargs):
        return 1500 if key == "summarizer_body_chars" else default

    with patch.dict("os.environ", {"OPENAI_API_BASE": "http://localhost:11434/v1"}, clear=True), patch(
        "jcodemunch_mcp.summarizer.batch_summarize._config.get", side_effect=fake_get
    ), patch.object(OpenAIBatchSummarizer, "_init_client"):
        summarizer = OpenAIBatchSummarizer(repo=str(repo))
        summarizer.client = client
        summarizer.summarize_batch([_symbol("app.py", "plain")])

    sent = client.post.call_args[1]["json"]["messages"][0]["content"]
    assert "   | def plain(x):\n   |     return x\n" in sent
