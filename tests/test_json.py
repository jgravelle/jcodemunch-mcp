"""Tests for JSON symbol extraction."""

# ⚠ Import via `jcodemunch_mcp`, NEVER `src.jcodemunch_mcp` — see the note in
# tests/test_css.py. The two spellings are two module objects with two configs;
# conftest resets only the canonical one, so the `src.` twin reads the
# developer's real `~/.code-index/config.jsonc` and gates `json` out.
from jcodemunch_mcp.parser.extractor import parse_file, _parse_json_symbols
from jcodemunch_mcp.parser.languages import get_language_for_path, LANGUAGE_EXTENSIONS


# ---------------------------------------------------------------------------
# Extension / language detection
# ---------------------------------------------------------------------------

def test_json_extension_detected():
    assert get_language_for_path("package.json") == "json"


def test_json_extension_in_registry():
    assert ".json" in LANGUAGE_EXTENSIONS
    assert LANGUAGE_EXTENSIONS[".json"] == "json"


def test_openapi_json_not_overridden():
    # Compound extension must still resolve to openapi, not json
    assert get_language_for_path("api.openapi.json") == "openapi"
    assert get_language_for_path("api.swagger.json") == "openapi"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_PACKAGE_JSON = b"""{
  "name": "myapp",
  "version": "1.0.0",
  "description": "A sample app",
  "dependencies": {
    "react": "^18.0.0",
    "axios": "^1.0.0"
  },
  "devDependencies": {
    "vite": "^4.0.0"
  },
  "scripts": {
    "build": "vite build",
    "dev": "vite"
  }
}
"""


def _syms():
    return _parse_json_symbols(_PACKAGE_JSON, "package.json")


# ---------------------------------------------------------------------------
# Symbol extraction
# ---------------------------------------------------------------------------

def test_json_returns_symbols():
    assert len(_syms()) >= 6


def test_json_top_level_keys_extracted():
    names = {s.name for s in _syms()}
    assert "name" in names
    assert "version" in names
    assert "dependencies" in names
    assert "scripts" in names


def test_json_symbol_kind():
    syms = _syms()
    for s in syms:
        assert s.kind == "constant"
        assert s.language == "json"


def test_json_symbol_line():
    syms = _syms()
    s = next(s for s in syms if s.name == "name")
    assert s.line >= 1


def test_json_symbol_signature():
    syms = _syms()
    s = next(s for s in syms if s.name == "name")
    assert "myapp" in s.signature


def test_json_symbol_ids_unique():
    ids = [s.id for s in _syms()]
    assert len(ids) == len(set(ids))


def test_json_symbol_has_byte_info():
    for s in _syms():
        assert s.byte_offset >= 0
        assert s.byte_length > 0
        assert s.content_hash != ""


def test_json_via_parse_file():
    syms = parse_file(_PACKAGE_JSON.decode(), "package.json", "json")
    names = {s.name for s in syms}
    assert "name" in names
    assert "dependencies" in names


def test_json_empty_object():
    assert _parse_json_symbols(b"{}", "empty.json") == []


def test_json_empty_file():
    assert _parse_json_symbols(b"", "empty.json") == []


def test_json_array_root():
    # Arrays at root have no top-level keys — no symbols
    assert _parse_json_symbols(b'["a", "b", "c"]', "list.json") == []


def test_json_no_nested_keys():
    # Only root-level keys extracted, not nested
    syms = _syms()
    names = {s.name for s in syms}
    assert "react" not in names
    assert "build" not in names
