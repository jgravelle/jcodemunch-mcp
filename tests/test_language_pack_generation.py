"""An install on tree-sitter-language-pack 1.x says so, and says what it costs (#608).

#382 measured, and #608's probe (2026-09-11, pack 1.17.0) reproduced: the 1.x
pack bundles no grammars and fetches each one over the network into a cache
directory at first parse, and the nim grammar changed upstream so our
extractor reads it as empty (the manifest also lacks `autohotkey`, `ejs` and
`verse`, which #382 recorded as dropped languages; the probe found our own regex
extractors parse those three and never ask tree-sitter, so nim is the one loss). The extractor's `except Exception: return
[]` turned every one of those into a file "indexed for text search only" with
no warning anywhere, so a user who ran `pip install -U tree-sitter-language-pack`
(the override the issue asks us to document) got a silent capability gap, and an
airgapped 1.x install parsed nothing and said nothing.

The property: the pack's GENERATION (bundled / download / absent) is a fact the
install reports wherever capability is reported, a grammar failure is recorded
per language rather than swallowed, and an index result on a download-generation
pack carries the notice. On 0.x nothing changes, byte for byte.
"""

from __future__ import annotations

import copy
import logging
from pathlib import Path

import pytest

from jcodemunch_mcp.parser import extractor, grammar_pack

README = Path(__file__).resolve().parent.parent / "README.md"


@pytest.fixture(autouse=True)
def _clean_failures():
    grammar_pack.reset_failures()
    yield
    grammar_pack.reset_failures()


# --- 1. the generation is derived from the version, and absent is a value --- #

@pytest.mark.parametrize("version, expected", [
    ("0.7.0", "bundled"), ("0.13.0", "bundled"), ("0.99.9", "bundled"),
    ("1.0.0", "download"), ("1.13.3", "download"), ("1.17.0", "download"), ("2.0.0", "download"),
    (None, "absent"), ("", "absent"), ("garbage", "absent"),
])
def test_generation_from_version(version, expected):
    assert grammar_pack.generation(version) == expected


def test_the_live_generation_matches_the_installed_pack():
    v = grammar_pack.pack_version()
    assert grammar_pack.generation() == grammar_pack.generation(v)


# --- 2. a grammar failure is recorded, once, and the file still yields [] --- #

class _DownloadError(Exception):
    """Shaped like tree_sitter_language_pack.DownloadError (1.x)."""


def test_extractor_records_a_grammar_failure_instead_of_swallowing_it(monkeypatch, caplog):
    def boom(name):
        raise _DownloadError(f"Language '{name}' is not in the download manifest")

    monkeypatch.setattr(extractor, "get_parser", boom)
    src = "def f():\n    pass\n"
    with caplog.at_level(logging.WARNING, logger="jcodemunch_mcp.parser.extractor"):
        assert extractor.parse_file(src, "a.py", "python") == []
        assert extractor.parse_file(src, "b.py", "python") == []
    failures = grammar_pack.failures()
    assert "python" in failures, failures
    assert failures["python"].startswith("_DownloadError"), failures["python"]
    assert "download manifest" in failures["python"]
    warned = [r for r in caplog.records if "python" in r.getMessage() and "grammar" in r.getMessage().lower()]
    assert len(warned) == 1, "one warning per language, not one per file"


def test_a_healthy_parse_records_nothing():
    assert extractor.parse_file("def f():\n    pass\n", "a.py", "python")
    assert grammar_pack.failures() == {}


# --- 3. on a bundled pack the result is byte-identical to today ------------- #

def test_bundled_pack_attaches_nothing(monkeypatch):
    monkeypatch.setattr(grammar_pack, "pack_version", lambda: "0.13.0")
    result = {"success": True, "warnings": ["unrelated"], "symbol_count": 3}
    before = copy.deepcopy(result)
    grammar_pack.attach(result)
    assert result == before
    assert grammar_pack.notice() is None


# --- 4. on a download pack the result says what the install does ------------ #

def test_download_pack_attaches_the_notice_and_the_failed_languages(monkeypatch):
    monkeypatch.setattr(grammar_pack, "pack_version", lambda: "1.17.0")
    monkeypatch.setattr(grammar_pack, "cache_dir", lambda: r"C:\Users\x\AppData\Local\tree-sitter-language-pack\v1.17.0\libs")
    grammar_pack.record_failure("autohotkey", _DownloadError("not in the download manifest"))
    grammar_pack.record_failure("verse", _DownloadError("not in the download manifest"))
    result = {"success": True, "symbol_count": 3}
    grammar_pack.attach(result)
    block = result["grammar_pack"]
    assert block["generation"] == "download"
    assert block["version"] == "1.17.0"
    assert block["cache_dir"].endswith("libs")
    assert set(block["grammar_failures"]) == {"autohotkey", "verse"}
    text = "\n".join(result["warnings"])
    assert "1.17.0" in text and "network" in text and "libs" in text
    assert "autohotkey" in text and "verse" in text
    assert "tree-sitter-language-pack" in text


def test_absent_pack_is_a_notice_too(monkeypatch):
    monkeypatch.setattr(grammar_pack, "pack_version", lambda: None)
    result: dict = {}
    grammar_pack.attach(result)
    assert result["grammar_pack"]["generation"] == "absent"
    assert any("absent" in w for w in result["warnings"])


# --- 5. the capability certificate and install-status carry it -------------- #

def test_parser_fingerprint_carries_the_grammar_source(monkeypatch):
    from jcodemunch_mcp.evidence import capability

    fp = capability.parser_fingerprint()
    assert fp["grammar_source"] == grammar_pack.generation()
    monkeypatch.setattr(grammar_pack, "pack_version", lambda: "1.17.0")
    assert capability.parser_fingerprint()["grammar_source"] == "download"


def test_install_status_carries_the_grammar_pack_block(monkeypatch, tmp_path):
    from jcodemunch_mcp.cli import init

    monkeypatch.setattr(grammar_pack, "pack_version", lambda: "1.17.0")
    monkeypatch.setattr(grammar_pack, "cache_dir", lambda: str(tmp_path))
    report = init.install_status()
    block = report["grammar_pack"]
    assert block["generation"] == "download" and block["version"] == "1.17.0"


# --- 6. index_folder end to end on a download pack -------------------------- #

def test_index_folder_result_carries_the_notice_on_a_download_pack(monkeypatch, tmp_path):
    from jcodemunch_mcp.tools.index_folder import index_folder

    monkeypatch.setattr(grammar_pack, "pack_version", lambda: "1.17.0")
    monkeypatch.setattr(grammar_pack, "cache_dir", lambda: str(tmp_path / "libs"))
    root = tmp_path / "proj"
    root.mkdir()
    (root / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
    result = index_folder(path=str(root), storage_path=str(tmp_path / "idx"), use_ai_summaries=False,
                          context_providers=False, identity_mode="local")
    assert result["success"], result
    assert result["grammar_pack"]["generation"] == "download"
    assert any("tree-sitter-language-pack 1.17.0" in w for w in result["warnings"]), result.get("warnings")


# --- 7. the override is documented with its disclosure, before it ships ------ #

def test_readme_discloses_the_override_and_its_costs():
    text = README.read_text(encoding="utf-8")
    start = text.index("## Security, privacy, and background behavior")
    end = text.index("\n## ", start + 10)
    section = text[start:end]
    assert "tree-sitter-language-pack" in section
    for word in ("pip install -U tree-sitter-language-pack", "network", "autohotkey", "ejs", "verse", "nim"):
        assert word in section, f"README security section does not disclose {word!r}"
