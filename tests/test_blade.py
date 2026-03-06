"""Tests for Blade template symbol extraction."""

import pytest
from pathlib import Path

from src.jcodemunch_mcp.parser.extractor import parse_file
from src.jcodemunch_mcp.parser.languages import get_language_for_path, LANGUAGE_EXTENSIONS


FIXTURE = Path(__file__).parent / "fixtures" / "blade" / "sample.blade.php"


def _load():
    return FIXTURE.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Extension / language detection
# ---------------------------------------------------------------------------

def test_blade_extension_detected():
    assert get_language_for_path("resources/views/home.blade.php") == "blade"


def test_blade_not_confused_with_php():
    # Plain .php files must still resolve to php, not blade
    assert get_language_for_path("app/Http/Controllers/HomeController.php") == "php"


def test_blade_extension_in_registry():
    assert ".blade.php" in LANGUAGE_EXTENSIONS
    assert LANGUAGE_EXTENSIONS[".blade.php"] == "blade"


# ---------------------------------------------------------------------------
# Symbol extraction
# ---------------------------------------------------------------------------

def _symbols():
    return parse_file(_load(), "resources/views/profile.blade.php", "blade")


def test_blade_returns_symbols():
    syms = _symbols()
    assert len(syms) >= 5


def test_blade_extends_extracted():
    syms = _symbols()
    kinds = {s.kind for s in syms}
    names = {s.name for s in syms}
    assert "type" in kinds
    assert "layouts.app" in names


def test_blade_sections_extracted():
    syms = _symbols()
    section_names = {s.name for s in syms if s.kind == "method"}
    assert "content" in section_names
    assert "sidebar" in section_names


def test_blade_component_extracted():
    syms = _symbols()
    components = [s for s in syms if s.kind == "class"]
    assert any(s.name == "alert" for s in components)


def test_blade_include_extracted():
    syms = _symbols()
    includes = [s for s in syms if s.kind == "function"]
    names = {s.name for s in includes}
    assert "partials.avatar" in names
    assert "partials.nav" in names


def test_blade_push_extracted():
    syms = _symbols()
    pushes = [s for s in syms if s.kind == "constant" and s.name == "scripts"]
    assert len(pushes) >= 1


def test_blade_livewire_extracted():
    syms = _symbols()
    livewire = [s for s in syms if s.kind == "class" and s.name == "user-stats"]
    assert len(livewire) == 1


def test_blade_symbols_have_line_numbers():
    syms = _symbols()
    for s in syms:
        assert s.line >= 1


def test_blade_symbols_sorted_by_line():
    syms = _symbols()
    lines = [s.line for s in syms]
    assert lines == sorted(lines)


def test_blade_symbol_ids_unique():
    syms = _symbols()
    ids = [s.id for s in syms]
    assert len(ids) == len(set(ids))


def test_blade_language_field():
    syms = _symbols()
    for s in syms:
        assert s.language == "blade"
