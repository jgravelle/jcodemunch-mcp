"""Tests for CSS and CSS preprocessor symbol extraction."""

import pytest

# ⚠ Import via `jcodemunch_mcp`, NEVER `src.jcodemunch_mcp`. The two spellings
# produce two DISTINCT module objects, each with its own `config._GLOBAL_CONFIG`.
# conftest's `_reset_global_config` resets only the canonical one, so a
# `src.`-prefixed `parse_file` consults the developer's real
# `~/.code-index/config.jsonc` — and `is_language_enabled` there gates `scss`
# out of any allowlist that omits it, making `parse_file` return []. These tests
# then passed only because `test_config.py` (also `src.`-prefixed) happened to
# overwrite the twin's config earlier in the serial run.
from jcodemunch_mcp.parser.extractor import parse_file, _parse_css_symbols, _parse_scss_symbols
from jcodemunch_mcp.parser.languages import get_language_for_path, LANGUAGE_EXTENSIONS


# ---------------------------------------------------------------------------
# Extension / language detection
# ---------------------------------------------------------------------------

def test_css_extension_detected():
    assert get_language_for_path("static/css/main.css") == "css"


def test_css_extension_in_registry():
    assert ".css" in LANGUAGE_EXTENSIONS
    assert LANGUAGE_EXTENSIONS[".css"] == "css"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_CSS_SRC = b"""
:root {
  --primary-color: #333;
  --spacing: 8px;
}

body {
  margin: 0;
  padding: 0;
  font-family: sans-serif;
}

.container {
  display: flex;
  max-width: 1200px;
}

.navbar .item {
  color: red;
}

#header {
  font-size: 24px;
  font-weight: bold;
}

h1, h2, h3 {
  font-weight: bold;
}

@keyframes slideIn {
  from { transform: translateX(-100%); }
  to   { transform: translateX(0); }
}

@media (max-width: 768px) {
  .container { flex-direction: column; }
}

@supports (display: grid) {
  .layout { display: grid; }
}
"""


def _syms():
    return _parse_css_symbols(_CSS_SRC, "styles/main.css")


def _parse_syms():
    return parse_file(_CSS_SRC.decode(), "styles/main.css", "css")


# ---------------------------------------------------------------------------
# Symbol extraction
# ---------------------------------------------------------------------------

def test_css_returns_symbols():
    assert len(_syms()) >= 7


def test_css_rule_sets_extracted():
    syms = _syms()
    names = {s.name for s in syms}
    assert ":root" in names
    assert "body" in names
    assert ".container" in names


def test_css_class_selector():
    syms = _syms()
    s = next(s for s in syms if s.name == ".container")
    assert s.kind == "class"
    assert s.language == "css"
    assert s.line > 0


def test_css_id_selector():
    syms = _syms()
    s = next(s for s in syms if s.name == "#header")
    assert s.kind == "class"


def test_css_tag_selector():
    syms = _syms()
    s = next(s for s in syms if s.name == "body")
    assert s.kind == "class"


def test_css_compound_selector():
    syms = _syms()
    s = next(s for s in syms if s.name == ".navbar .item")
    assert s.kind == "class"


def test_css_grouped_selectors():
    syms = _syms()
    s = next(s for s in syms if s.name == "h1, h2, h3")
    assert s.kind == "class"


def test_css_keyframes_extracted():
    syms = _syms()
    kf = next(s for s in syms if "@keyframes" in s.name)
    assert kf.name == "@keyframes slideIn"
    assert kf.kind == "function"


def test_css_media_query_extracted():
    syms = _syms()
    mq = next(s for s in syms if "@media" in s.name)
    assert "@media" in mq.name
    assert mq.kind == "type"


def test_css_supports_extracted():
    syms = _syms()
    sup = next(s for s in syms if "@supports" in s.name)
    assert sup.kind == "type"


def test_css_symbol_ids_unique():
    syms = _syms()
    ids = [s.id for s in syms]
    assert len(ids) == len(set(ids)), "Duplicate symbol IDs found"


def test_css_symbol_has_byte_info():
    syms = _syms()
    for s in syms:
        assert s.byte_offset >= 0
        assert s.byte_length > 0
        assert s.content_hash != ""


def test_css_via_parse_file():
    """parse_file() should dispatch to _parse_css_symbols."""
    syms = _parse_syms()
    assert len(syms) >= 7
    names = {s.name for s in syms}
    assert ".container" in names
    assert "@keyframes slideIn" in names


def test_css_empty_file():
    assert _parse_css_symbols(b"", "empty.css") == []
    assert _parse_css_symbols(b"   \n\n   ", "blank.css") == []


def test_css_no_symbols_file():
    # File with only comments — no rule sets
    src = b"/* just a comment */\n/* another */\n"
    assert _parse_css_symbols(src, "comments.css") == []


# ---------------------------------------------------------------------------
# SCSS extension / language detection
# ---------------------------------------------------------------------------

def test_scss_extension_detected():
    assert get_language_for_path("styles/main.scss") == "scss"


def test_sass_extension_detected():
    assert get_language_for_path("styles/main.sass") == "sass"


def test_less_extension_detected():
    assert get_language_for_path("styles/main.less") == "less"


def test_styl_extension_detected():
    assert get_language_for_path("styles/main.styl") == "styl"


def test_scss_extension_in_registry():
    assert ".scss" in LANGUAGE_EXTENSIONS
    assert LANGUAGE_EXTENSIONS[".scss"] == "scss"


def test_less_extension_in_registry():
    assert ".less" in LANGUAGE_EXTENSIONS
    assert LANGUAGE_EXTENSIONS[".less"] == "less"


# ---------------------------------------------------------------------------
# SCSS symbol extraction
# ---------------------------------------------------------------------------

_SCSS_SRC = b"""
$primary-color: #333;
$font-size: 16px;

@mixin flex-center($dir: row) {
  display: flex;
  flex-direction: $dir;
}

@mixin respond-to($breakpoint) {
  @media (max-width: $breakpoint) { @content; }
}

%visually-hidden {
  position: absolute;
  clip: rect(0, 0, 0, 0);
}

.container {
  max-width: 1200px;
}

.navbar .item {
  color: red;
}

@function px-to-rem($px) {
  @return $px / 16 * 1rem;
}

@media (max-width: 768px) {
  .container { flex-direction: column; }
}

@supports (display: grid) {
  .layout { display: grid; }
}
"""


def _scss_syms():
    return _parse_scss_symbols(_SCSS_SRC, "styles/main.scss")


def test_scss_returns_symbols():
    assert len(_scss_syms()) >= 6


def test_scss_variables_extracted():
    syms = _scss_syms()
    names = {s.name for s in syms}
    assert "$primary-color" in names
    assert "$font-size" in names


def test_scss_variable_kind():
    syms = _scss_syms()
    s = next(s for s in syms if s.name == "$primary-color")
    assert s.kind == "constant"
    assert s.language == "scss"
    assert "$primary-color" in s.signature


def test_scss_mixin_extracted():
    syms = _scss_syms()
    names = {s.name for s in syms}
    assert "@mixin flex-center" in names


def test_scss_mixin_kind():
    syms = _scss_syms()
    s = next(s for s in syms if s.name == "@mixin flex-center")
    assert s.kind == "function"
    assert "flex-center" in s.signature


def test_scss_function_extracted():
    syms = _scss_syms()
    names = {s.name for s in syms}
    assert "@function px-to-rem" in names


def test_scss_function_kind():
    syms = _scss_syms()
    s = next(s for s in syms if s.name == "@function px-to-rem")
    assert s.kind == "function"


def test_scss_rule_set_extracted():
    syms = _scss_syms()
    names = {s.name for s in syms}
    assert ".container" in names


def test_scss_rule_set_kind():
    syms = _scss_syms()
    s = next(s for s in syms if s.name == ".container")
    assert s.kind == "class"
    assert s.language == "scss"


def test_scss_placeholder_extracted():
    syms = _scss_syms()
    names = {s.name for s in syms}
    assert "%visually-hidden" in names


def test_scss_placeholder_kind():
    syms = _scss_syms()
    s = next(s for s in syms if s.name == "%visually-hidden")
    assert s.kind == "class"


def test_scss_media_query_extracted():
    syms = _scss_syms()
    mq = next((s for s in syms if "@media" in s.name), None)
    assert mq is not None
    assert mq.kind == "type"


def test_scss_supports_extracted():
    syms = _scss_syms()
    sup = next((s for s in syms if "@supports" in s.name), None)
    assert sup is not None
    assert sup.kind == "type"


def test_scss_symbol_ids_unique():
    syms = _scss_syms()
    ids = [s.id for s in syms]
    assert len(ids) == len(set(ids))


def test_scss_symbol_has_byte_info():
    syms = _scss_syms()
    for s in syms:
        assert s.byte_offset >= 0
        assert s.byte_length > 0
        assert s.content_hash != ""


def test_scss_via_parse_file():
    syms = parse_file(_SCSS_SRC.decode(), "styles/main.scss", "scss")
    assert len(syms) >= 6
    names = {s.name for s in syms}
    assert "$primary-color" in names
    assert "@mixin flex-center" in names


def test_scss_empty_file():
    assert _parse_scss_symbols(b"", "empty.scss") == []


# ---------------------------------------------------------------------------
# Less / Sass / Styl — text-search only (no symbol extraction)
# ---------------------------------------------------------------------------

def test_less_parse_returns_empty_symbols():
    """Less has no tree-sitter grammar; parse_file returns [] but files are indexed."""
    src = "@primary: #333;\n.container { color: @primary; }\n"
    syms = parse_file(src, "styles/main.less", "less")
    assert syms == []


def test_sass_parse_returns_empty_symbols():
    src = "$primary: #333\n.container\n  color: $primary\n"
    syms = parse_file(src, "styles/main.sass", "sass")
    assert syms == []


def test_styl_parse_returns_empty_symbols():
    src = "primary = #333\n.container\n  color primary\n"
    syms = parse_file(src, "styles/main.styl", "styl")
    assert syms == []
