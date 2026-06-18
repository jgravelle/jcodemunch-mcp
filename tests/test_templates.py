"""Templating-language indexing tests (Jinja2 / Twig).

A template file of the form ``name.<underlying-ext>.<engine-ext>`` (e.g.
``foo.ts.j2``) is recognized, the engine constructs are masked offset-preserving,
and the body is re-parsed as the underlying language. See issue #336.
"""

from pathlib import Path

from jcodemunch_mcp.parser import parse_file
from jcodemunch_mcp.parser.imports import extract_imports
from jcodemunch_mcp.parser.languages import (
    LANGUAGE_REGISTRY,
    get_language_for_path,
    template_underlying_language,
)
from jcodemunch_mcp.parser.template_shared import (
    TEMPLATE_ENGINE_LANGUAGES,
    mask_template_keep_offsets,
)
from jcodemunch_mcp.tools.index_folder import discover_local_files


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "templates"


def _read_fixture(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# Recognition / registry                                                      #
# --------------------------------------------------------------------------- #


def test_engine_languages_in_registry():
    # First cut ships Jinja2 + Twig only (registry stays pluggable).
    assert set(TEMPLATE_ENGINE_LANGUAGES) == {"jinja", "twig"}
    for engine in ("jinja", "twig"):
        assert engine in LANGUAGE_REGISTRY


def test_extension_resolution_infers_engine_and_underlying():
    cases = {
        "src/models/user.ts.j2": ("jinja", "typescript"),
        "settings.py.jinja": ("jinja", "python"),
        "ui/widget.ts.twig": ("twig", "typescript"),
        "a/b/service.py.jinja2": ("jinja", "python"),
    }
    for path, (engine, underlying) in cases.items():
        assert get_language_for_path(path) == engine, path
        assert template_underlying_language(path) == underlying, path


def test_bare_template_without_underlying_is_skipped():
    # No middle language extension -> nothing to parse as source.
    assert get_language_for_path("report.j2") is None
    assert get_language_for_path("email.jinja") is None
    assert template_underlying_language("report.j2") is None


def test_plain_source_extension_unchanged():
    # The template branch must not perturb ordinary files.
    assert get_language_for_path("foo.ts") == "typescript"
    assert get_language_for_path("foo.py") == "python"
    assert template_underlying_language("foo.ts") is None


def test_template_fallback_never_reresolves_existing_extension():
    # The template step runs AFTER the compound and last-extension checks, so a
    # path that already resolves there must win unchanged and never be hijacked
    # by the engine-stripping fallback. None of these are template files.
    non_collisions = {
        "types/foo.d.ts": "typescript",   # .d.ts -> last-extension .ts
        "user.test.ts": "typescript",     # .test.ts -> last-extension .ts
        "View.blade.php": "blade",        # compound .blade.php wins at step 4
    }
    for path, expected in non_collisions.items():
        assert get_language_for_path(path) == expected, path
        assert template_underlying_language(path) is None, path
    # A bare .j2 with no inner language extension still skips (turns nothing
    # into a language) — the fallback only ever upgrades an unresolved path.
    assert get_language_for_path("report.j2") is None


# --------------------------------------------------------------------------- #
# Symbol extraction (offset preservation)                                     #
# --------------------------------------------------------------------------- #


def test_jinja_ts_underlying_symbols_with_correct_lines():
    content = _read_fixture("sample.ts.j2")
    symbols = parse_file(content, "src/models/user.ts.j2", "jinja")
    by_name = {s.name: s for s in symbols}

    # Underlying TypeScript symbols keep their sub-language and exact line numbers.
    assert "User" in by_name
    assert by_name["User"].kind == "type"
    assert by_name["User"].language == "typescript"
    assert by_name["User"].line == 4

    assert "buildUser" in by_name
    assert by_name["buildUser"].kind == "function"
    assert by_name["buildUser"].language == "typescript"
    # buildUser's body has a value-position {{ hole }} (masked to an identifier
    # filler); its line is correct only because masking preserves offsets.
    assert by_name["buildUser"].line == 9

    # Real TypeScript inside a {% block %} (whole-line tags) is still extracted.
    assert "FOOTER" in by_name
    assert by_name["FOOTER"].language == "typescript"
    assert by_name["FOOTER"].line == 15


def test_jinja_macro_and_block_extracted_as_symbols():
    content = _read_fixture("sample.ts.j2")
    symbols = parse_file(content, "src/models/user.ts.j2", "jinja")
    by_name = {s.name: s for s in symbols}

    assert "greeting" in by_name
    assert by_name["greeting"].kind == "function"
    assert by_name["greeting"].language == "jinja"
    assert by_name["greeting"].line == 19

    assert "footer" in by_name
    assert by_name["footer"].kind == "constant"
    assert by_name["footer"].language == "jinja"
    assert by_name["footer"].line == 14


def test_python_underlying_language():
    content = _read_fixture("sample.py.jinja")
    symbols = parse_file(content, "conf/settings.py.jinja", "jinja")
    by_name = {s.name: s for s in symbols}

    assert by_name["Settings"].kind == "class"
    assert by_name["Settings"].language == "python"
    assert by_name["build_settings"].kind == "function"
    assert by_name["build_settings"].language == "python"
    # The macro is still surfaced.
    assert by_name["render_path"].language == "jinja"


def test_repo_forwarded_to_underlying_body_parse():
    # The template body re-parse must honor per-project language gating, so the
    # repo passed to parse_file has to reach the underlying-language enablement
    # check (config.is_language_enabled) — not get dropped at the template hop.
    from unittest.mock import patch

    seen: list = []

    def _record(language, repo=None):
        seen.append((language, repo))
        return True

    with patch("jcodemunch_mcp.config.is_language_enabled", side_effect=_record):
        parse_file(
            _read_fixture("sample.ts.j2"),
            "src/models/user.ts.j2",
            "jinja",
            repo="/proj/root",
        )

    # The underlying TypeScript body was gated with the same repo we passed in.
    assert ("typescript", "/proj/root") in seen


def test_twig_engine_same_delimiters():
    content = _read_fixture("widget.ts.twig")
    symbols = parse_file(content, "ui/widget.ts.twig", "twig")
    by_name = {s.name: s for s in symbols}

    assert by_name["Widget"].kind == "class"
    assert by_name["Widget"].language == "typescript"
    # Twig reuses the Jinja directive extractor; the macro tags as twig.
    assert by_name["field"].kind == "function"
    assert by_name["field"].language == "twig"


def test_bom_and_crlf_preserve_line_numbers():
    content = "﻿" + _read_fixture("sample.ts.j2").replace("\n", "\r\n")
    symbols = parse_file(content, "src/models/user.ts.j2", "jinja")
    by_name = {s.name: s for s in symbols}
    assert "User" in by_name
    assert by_name["User"].line == 4
    assert by_name["buildUser"].line == 9


def test_multiline_block_keeps_following_symbol_aligned():
    content = (
        "export const A = 1;\n"
        "{% if cond %}\n"
        "  {{ extra }}\n"
        "  {{ more }}\n"
        "{% endif %}\n"
        "export function tail(): void {}\n"
    )
    symbols = parse_file(content, "x.ts.j2", "jinja")
    by_name = {s.name: s for s in symbols}
    assert by_name["tail"].line == 6  # unshifted by the 4-line {% if %} block


# --------------------------------------------------------------------------- #
# Masking primitive                                                           #
# --------------------------------------------------------------------------- #


def test_mask_preserves_length_and_newlines():
    text = "a = {{ v }}\n{% if x %}\nkeep me\n{% endif %}\n"
    masked = mask_template_keep_offsets(text, "jinja")
    assert len(masked) == len(text)
    assert masked.count("\n") == text.count("\n")
    assert "if x" not in masked
    assert "{{" not in masked and "{%" not in masked
    assert "keep me" in masked  # inert content between constructs is untouched


def test_mask_unknown_engine_is_identity():
    text = "{{ x }}"
    assert mask_template_keep_offsets(text, "not-an-engine") == text


# --------------------------------------------------------------------------- #
# Imports                                                                     #
# --------------------------------------------------------------------------- #


def test_imports_extracted_from_template_body():
    content = _read_fixture("sample.ts.j2")
    edges = extract_imports(content, "src/models/user.ts.j2", "jinja")
    specifiers = {e["specifier"] for e in edges}
    assert "./helper" in specifiers
    assert "./config" in specifiers


def test_imports_with_templated_specifier_do_not_crash():
    content = 'import { X } from "{{ pkg }}";\nimport { Y } from "./static";\n'
    edges = extract_imports(content, "m.ts.j2", "jinja")
    specifiers = {e["specifier"] for e in edges}
    assert "./static" in specifiers  # static import survives masking


# --------------------------------------------------------------------------- #
# Discovery (end-to-end, hermetic)                                            #
# --------------------------------------------------------------------------- #


def test_discovery_indexes_template_not_wrong_extension(tmp_path):
    (tmp_path / "foo.ts.j2").write_text(
        _read_fixture("sample.ts.j2"), encoding="utf-8"
    )
    (tmp_path / "bare.j2").write_text("nothing parseable here\n", encoding="utf-8")
    (tmp_path / "plain.ts").write_text("export const k = 1;\n", encoding="utf-8")

    files, _warnings, skip_counts = discover_local_files(tmp_path.resolve())
    names = {p.name for p in files}

    assert "foo.ts.j2" in names      # recognized as a template
    assert "plain.ts" in names       # ordinary file still discovered
    assert "bare.j2" not in names    # bare template skipped
    # The template was NOT counted as an unsupported extension.
    assert skip_counts.get("wrong_extension", 0) <= 1  # only bare.j2 may count
