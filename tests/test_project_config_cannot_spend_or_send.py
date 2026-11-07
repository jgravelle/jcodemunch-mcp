"""A repo's own `.jcodemunch.jsonc` cannot switch on paid or remote summaries.

A project file may tighten a setting, never loosen it.
The settings that spend the user's money on a summarizer or send the user's
code to a remote one are the user's to make, in their own config or
environment; a repository's own file does not make them on the user's behalf.
Each arm sets one such key in the project file only, with a provider key in
the environment, and checks the decision that key feeds. Each control sets the
same key in the global config and checks it is still honored.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp import config as config_module
from jcodemunch_mcp.summarizer import batch_summarize as bs


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path / "storage"))
    for var in (
        "JCODEMUNCH_ALLOW_PAID_SUMMARIES",
        "JCODEMUNCH_ALLOW_REMOTE_SUMMARIZER",
        "JCODEMUNCH_SUMMARIZER_PROVIDER",
        "JCODEMUNCH_USE_AI_SUMMARIES",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "OPENAI_API_KEY",
        "OPENAI_API_BASE",
        "MINIMAX_API_KEY",
        "ZHIPUAI_API_KEY",
        "OPENROUTER_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-real-key")
    root = tmp_path / "repo"
    root.mkdir()
    (root / "app.py").write_text(
        "def handler():\n    return 'secret logic'\n", encoding="utf-8"
    )
    return root


def _project_file(root, text):
    (root / ".jcodemunch.jsonc").write_text(text, encoding="utf-8")
    config_module.invalidate_project_config_cache(str(root))
    config_module.load_project_config(str(root))


def test_a_project_file_cannot_opt_into_paid_summaries(project):
    _project_file(project, '{"allow_paid_summaries": true}')
    assert bs._paid_summaries_allowed(repo=str(project)) is False
    assert bs.get_provider_name(repo=str(project)) is None


def test_a_project_file_cannot_name_a_paid_provider(project):
    """Choosing a paid provider is the user's decision, not the repo's."""
    _project_file(project, '{"summarizer_provider": "anthropic"}')
    assert bs.get_provider_name(repo=str(project)) is None


def test_a_project_file_cannot_allow_a_remote_endpoint(project, monkeypatch):
    monkeypatch.setenv("OPENAI_API_BASE", "https://api.example.invalid/v1")
    _project_file(project, '{"allow_remote_summarizer": true}')
    s = bs.OpenAIBatchSummarizer(api_key="sk-test", repo=str(project))
    assert s.api_base is None, "a remote endpoint was accepted on the repo's word"


def test_a_project_file_cannot_send_source_to_a_remote_summarizer(project):
    _project_file(
        project, '{"allow_remote_summarizer": true, "summarizer_body_chars": 4000}'
    )
    s = bs.OpenAIBatchSummarizer(api_key="sk-test", api_base=None, repo=str(project))
    assert s._is_remote()
    assert s._body_chars() == 0, "symbol source would go to a remote summarizer"


def test_indexing_reads_the_summarizer_settings_for_the_repo(project, monkeypatch):
    """The decisions above are the ones indexing makes: `index_folder` resolves
    them with the repo it is indexing."""
    from jcodemunch_mcp.tools.index_folder import index_folder

    built = []

    class PaidSummarizer:
        """Stands in for the Anthropic summarizer: records that indexing chose
        it, and has no client, so nothing is sent."""

        def __init__(self, repo=None):
            built.append(repo)
            self.client = None

    monkeypatch.setattr(bs, "BatchSummarizer", PaidSummarizer)
    _project_file(
        project, '{"summarizer_provider": "anthropic", "use_ai_summaries": true}'
    )
    result = index_folder(
        str(project),
        use_ai_summaries=True,
        storage_path=str(project.parent / "store"),
        incremental=False,
        context_providers=False,
    )
    assert result.get("success"), result
    assert built == [], f"indexing chose the paid provider on the repo's word: {built}"


# ── controls: the same keys in the user's own config are honored ──────────


@pytest.fixture
def global_key(monkeypatch):
    def set_(key, value):
        monkeypatch.setitem(config_module._GLOBAL_CONFIG, key, value)

    return set_


def test_the_users_own_config_can_opt_into_paid_summaries(project, global_key):
    global_key("allow_paid_summaries", True)
    assert bs._paid_summaries_allowed(repo=str(project)) is True
    assert bs.get_provider_name(repo=str(project)) == "anthropic"


def test_the_users_own_config_can_name_a_paid_provider(project, global_key):
    global_key("summarizer_provider", "anthropic")
    assert bs.get_provider_name(repo=str(project)) == "anthropic"


def test_the_users_own_config_can_allow_a_remote_endpoint(
    project, monkeypatch, global_key
):
    monkeypatch.setenv("OPENAI_API_BASE", "https://api.example.invalid/v1")
    global_key("allow_remote_summarizer", True)
    s = bs.OpenAIBatchSummarizer(api_key="sk-test", repo=str(project))
    assert s.api_base == "https://api.example.invalid/v1"


def test_the_users_own_config_can_send_source(project, global_key):
    global_key("allow_remote_summarizer", True)
    global_key("summarizer_body_chars", 4000)
    s = bs.OpenAIBatchSummarizer(api_key="sk-test", api_base=None, repo=str(project))
    assert s._body_chars() == 4000


def test_a_project_file_cannot_trust_its_own_folder(project, tmp_path, global_key):
    """The user's whitelist names other folders; the repo names itself."""
    from jcodemunch_mcp.tools.index_folder import index_folder

    elsewhere = tmp_path / "my_projects"
    elsewhere.mkdir()
    global_key("trusted_folders", [str(elsewhere.resolve())])
    _project_file(project, '{"trusted_folders": ["."]}')
    result = index_folder(
        str(project),
        use_ai_summaries=False,
        storage_path=str(tmp_path / "store"),
        incremental=False,
        context_providers=False,
    )
    assert result.get("success") is False, result


def test_the_users_whitelist_still_refuses_an_untrusted_folder(
    project, tmp_path, global_key
):
    from jcodemunch_mcp.tools.index_folder import index_folder

    elsewhere = tmp_path / "my_projects"
    elsewhere.mkdir()
    global_key("trusted_folders", [str(elsewhere.resolve())])
    result = index_folder(
        str(project),
        use_ai_summaries=False,
        storage_path=str(tmp_path / "store"),
        incremental=False,
        context_providers=False,
    )
    assert result.get("success") is False


# ── the soft rule, key by key ────────────────────────────────────────────


@pytest.mark.parametrize(
    "text, key, user_value",
    [
        ('{"use_ai_summaries": true}', "use_ai_summaries", "auto"),
        ('{"summarizer_body_chars": 4000}', "summarizer_body_chars", 0),
        ('{"openai_extra_body": {"temperature": 0}}', "openai_extra_body", {}),
        ('{"allow_remote_summarizer": true}', "allow_remote_summarizer", False),
        ('{"allow_paid_summaries": true}', "allow_paid_summaries", False),
    ],
)
def test_a_project_file_cannot_turn_a_summary_setting_on(
    project, text, key, user_value
):
    _project_file(project, text)
    assert config_module.get(key, None, repo=str(project)) == user_value


@pytest.mark.parametrize(
    "text, key, user_value, project_value",
    [
        ('{"use_ai_summaries": false}', "use_ai_summaries", "auto", False),
        ('{"summarizer_body_chars": 100}', "summarizer_body_chars", 4000, 100),
        ('{"allow_remote_summarizer": false}', "allow_remote_summarizer", True, False),
        ('{"allow_paid_summaries": false}', "allow_paid_summaries", True, False),
        ('{"openai_extra_body": {}}', "openai_extra_body", {"temperature": 0}, {}),
    ],
)
def test_a_project_file_can_turn_a_summary_setting_off(
    project, global_key, text, key, user_value, project_value
):
    global_key(key, user_value)
    _project_file(project, text)
    assert config_module.get(key, None, repo=str(project)) == project_value


def test_a_project_file_can_turn_summaries_off_by_naming_no_provider(
    project, global_key
):
    global_key("summarizer_provider", "anthropic")
    _project_file(project, '{"summarizer_provider": "none"}')
    assert bs.get_provider_name(repo=str(project)) is None


def test_a_user_who_opted_in_keeps_the_project_provider_and_model(project, global_key):
    """#304: a project file choosing the provider and model works for a user
    whose own config already allows paid summaries."""
    global_key("allow_paid_summaries", True)
    _project_file(
        project, '{"summarizer_provider": "gemini", "summarizer_model": "gemini-x"}'
    )
    assert config_module.get("summarizer_provider", "", repo=str(project)) == "gemini"
    assert config_module.get("summarizer_model", "", repo=str(project)) == "gemini-x"


def test_a_remote_opt_in_does_not_cover_a_paid_provider(project, global_key):
    """Each gate checks its own key: allowing a remote endpoint is not
    agreeing to be billed."""
    global_key("allow_remote_summarizer", True)
    _project_file(
        project, '{"summarizer_provider": "gemini", "summarizer_model": "gemini-x"}'
    )
    assert config_module.get("summarizer_provider", "", repo=str(project)) == ""
    assert config_module.get("summarizer_model", "", repo=str(project)) == ""


@pytest.mark.parametrize(
    "opt_ins, applies",
    [
        ((), False),
        (("allow_paid_summaries",), False),
        (("allow_remote_summarizer",), False),
        (("allow_paid_summaries", "allow_remote_summarizer"), True),
    ],
)
def test_a_remote_openai_base_needs_both_opt_ins(
    project, monkeypatch, global_key, opt_ins, applies
):
    monkeypatch.setenv("OPENAI_API_BASE", "https://api.example.invalid/v1")
    for k in opt_ins:
        global_key(k, True)
    _project_file(project, '{"summarizer_provider": "openai"}')
    got = config_module.get("summarizer_provider", "", repo=str(project))
    assert (got == "openai") is applies


def test_a_local_openai_base_needs_no_opt_in(project, monkeypatch):
    monkeypatch.setenv("OPENAI_API_BASE", "http://localhost:11434/v1")
    _project_file(
        project, '{"summarizer_provider": "openai", "summarizer_model": "qwen3"}'
    )
    assert config_module.get("summarizer_provider", "", repo=str(project)) == "openai"
    assert config_module.get("summarizer_model", "", repo=str(project)) == "qwen3"


# ── the other settings a project file may turn off, never on ──────────────


def test_a_project_file_cannot_switch_on_git_blame(project):
    _project_file(project, '{"git_blame_enabled": true}')
    assert config_module.get("git_blame_enabled", False, repo=str(project)) is False


def test_a_project_file_can_switch_off_git_blame(project, global_key):
    global_key("git_blame_enabled", True)
    _project_file(project, '{"git_blame_enabled": false}')
    assert config_module.get("git_blame_enabled", False, repo=str(project)) is False


def test_a_project_file_cannot_unredact_the_source_root(project, global_key):
    global_key("redact_source_root", True)
    _project_file(project, '{"redact_source_root": false}')
    assert config_module.get("redact_source_root", False, repo=str(project)) is True


def test_a_project_file_can_redact_the_source_root(project):
    _project_file(project, '{"redact_source_root": true}')
    assert config_module.get("redact_source_root", False, repo=str(project)) is True


@pytest.mark.parametrize(
    "text", ['{"response_max_bytes": 0}', '{"response_max_bytes": 50000000}']
)
def test_a_project_file_cannot_raise_the_reply_ceiling(project, text):
    from jcodemunch_mcp import security

    _project_file(project, text)
    assert (
        security.get_max_response_bytes(repo=str(project))
        == config_module.DEFAULTS["response_max_bytes"]
    )


def test_a_project_file_can_lower_the_reply_ceiling(project):
    from jcodemunch_mcp import security

    _project_file(project, '{"response_max_bytes": 4096}')
    assert security.get_max_response_bytes(repo=str(project)) == 4096


def test_a_project_file_cannot_switch_on_lsp(project):
    from jcodemunch_mcp.enrichment import lsp_bridge

    _project_file(project, '{"enrichment": {"lsp_enabled": true}}')
    assert lsp_bridge.is_lsp_enabled(repo=str(project)) is False


def test_a_project_file_cannot_name_a_language_server_program(project, global_key):
    """The server table names the program LSP enrichment starts."""
    from jcodemunch_mcp.enrichment import lsp_bridge

    global_key("enrichment", {"lsp_enabled": True})
    _project_file(
        project,
        '{"enrichment": {"lsp_enabled": true, "lsp_servers": {"python": "./tools/other-server"}}}',
    )
    cfg = lsp_bridge.get_lsp_config(repo=str(project))
    assert cfg["lsp_enabled"] is True
    assert cfg["lsp_servers"] == lsp_bridge.DEFAULT_LSP_SERVERS


def test_a_project_file_can_switch_off_lsp(project, global_key):
    from jcodemunch_mcp.enrichment import lsp_bridge

    global_key("enrichment", {"lsp_enabled": True})
    _project_file(project, '{"enrichment": {"lsp_enabled": false}}')
    assert lsp_bridge.is_lsp_enabled(repo=str(project)) is False


def test_a_project_file_cannot_index_a_secret_file(project):
    from jcodemunch_mcp import security

    (project / ".env").write_text("TOKEN=x\n", encoding="utf-8")
    assert security.is_secret_file(str(project / ".env"), repo=str(project)) is True
    _project_file(project, '{"exclude_secret_patterns": [".env"]}')
    assert security.is_secret_file(str(project / ".env"), repo=str(project)) is True


def test_a_project_file_keeps_only_the_users_own_secret_exclusions(project, global_key):
    global_key("exclude_secret_patterns", [".env"])
    _project_file(project, '{"exclude_secret_patterns": [".env", "*.pem"]}')
    assert config_module.get("exclude_secret_patterns", [], repo=str(project)) == [
        ".env"
    ]


def test_a_project_file_cannot_re_enable_a_disabled_tool(project, global_key):
    global_key("disabled_tools", ["test_summarizer", "index_repo"])
    _project_file(project, '{"disabled_tools": []}')
    assert config_module.is_tool_disabled("index_repo", repo=str(project)) is True
    assert (
        config_module.disabled_tool_source("index_repo", repo=str(project)) == "global"
    )


def test_a_project_file_can_disable_another_tool(project, global_key, caplog):
    import logging

    global_key("disabled_tools", ["test_summarizer"])
    with caplog.at_level(logging.WARNING, logger="jcodemunch_mcp.config"):
        _project_file(project, '{"disabled_tools": ["index_repo"]}')
        in_effect, ignored = config_module.project_keys_in_effect(str(project))
    assert "disabled_tools" in in_effect and "disabled_tools" not in ignored
    assert not [r for r in caplog.records if "'disabled_tools'" in r.getMessage()]
    assert config_module.is_tool_disabled("index_repo", repo=str(project)) is True
    assert (
        config_module.disabled_tool_source("index_repo", repo=str(project)) == "project"
    )
    assert config_module.is_tool_disabled("test_summarizer", repo=str(project)) is True


def test_a_project_model_needs_the_users_opt_in(project):
    _project_file(project, '{"summarizer_model": "some-expensive-model"}')
    assert config_module.get("summarizer_model", "", repo=str(project)) == ""


@pytest.mark.parametrize(
    "text",
    [
        '{"trusted_folders": []}',
        '{"trusted_folders_whitelist_mode": false}',
    ],
)
def test_a_project_file_cannot_change_which_folders_are_trusted(
    project, tmp_path, global_key, text
):
    from jcodemunch_mcp.tools.index_folder import index_folder

    elsewhere = tmp_path / "my_projects"
    elsewhere.mkdir()
    global_key("trusted_folders", [str(elsewhere.resolve())])
    _project_file(project, text)
    result = index_folder(
        str(project),
        use_ai_summaries=False,
        storage_path=str(tmp_path / "store"),
        incremental=False,
        context_providers=False,
    )
    assert result.get("success") is False, result


def test_each_ignored_key_warns_once_naming_it(project, caplog):
    import logging

    _project_file(project, '{"allow_paid_summaries": true, "trusted_folders": ["."]}')
    with caplog.at_level(logging.WARNING, logger="jcodemunch_mcp.config"):
        for _ in range(3):
            config_module.get("allow_paid_summaries", False, repo=str(project))
    paid = [r for r in caplog.records if "'allow_paid_summaries'" in r.getMessage()]
    assert len(paid) == 1, [r.getMessage() for r in caplog.records]


def test_the_config_report_does_not_show_an_ignored_key_as_a_project_override(
    project, global_key
):
    """`config` answers whether a project's setting took effect; an ignored
    key must not be reported as the project's."""
    global_key("allow_remote_summarizer", True)
    _project_file(
        project,
        (
            '{"allow_paid_summaries": true, "trusted_folders": ["."], '
            '"allow_remote_summarizer": false, "summarizer_provider": "none"}'
        ),
    )
    in_effect, ignored = config_module.project_keys_in_effect(str(project))
    assert ignored == {"allow_paid_summaries", "trusted_folders"}
    assert in_effect == {"allow_remote_summarizer", "summarizer_provider"}

    rows = {r["key"]: r for r in config_module.config_report(repo=str(project))}
    assert rows["allow_paid_summaries"]["source"] != "project"
    assert rows["allow_paid_summaries"].get("project_ignored") is True
    assert rows["allow_paid_summaries"]["value"] is False
    assert rows["trusted_folders"]["source"] != "project"
    assert rows["allow_remote_summarizer"]["source"] == "project"
    assert "project_ignored" not in rows["allow_remote_summarizer"]


def test_a_trusted_folders_key_warns_at_load(project, caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger="jcodemunch_mcp.config"):
        _project_file(project, '{"trusted_folders": ["."]}')
    assert any("'trusted_folders'" in r.getMessage() for r in caplog.records)
