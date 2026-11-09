"""Ruby ERB templates (`.erb`) are indexed as language `erb`.

The parser runs up to three passes over one file, each over a same-length copy
of it, so every symbol's line and byte offset are the original file's:

- the Ruby inside `<% %>` tags, parsed as Ruby;
- the host text, parsed in the language its inner suffix names (`.js.erb` as
  JavaScript, `.rb.erb` as Ruby, `.yml.erb` as YAML, ...), with output tags
  filled by an identifier and statement tags blanked;
- for an HTML host (`.html.erb`, bare `.erb`, or a format with no parser such as
  `.text.erb`): a view symbol, static HTML ids, `content_for`/`provide`/`yield`
  slots, and inline `<script>` bodies parsed as JavaScript.

Fixtures and their licenses: `tests/fixtures/erb/SOURCES.md`.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from jcodemunch_mcp.cli.hooks._common import _CODE_EXTENSIONS
from jcodemunch_mcp.parser import extractor, parse_budget, parse_file
from jcodemunch_mcp.parser.languages import LANGUAGE_EXTENSIONS, LANGUAGE_REGISTRY, get_language_for_path
from jcodemunch_mcp.parser.symbols import compute_content_hash
from jcodemunch_mcp.tools.index_folder import index_folder
from jcodemunch_mcp.tools.search_symbols import search_symbols

FIXTURES = Path(__file__).parent / "fixtures" / "erb"
ALL_FIXTURES = sorted(p.name for p in FIXTURES.glob("*.erb"))


def _parse(name: str, path: str | None = None):
    source = (FIXTURES / name).read_bytes()
    symbols = parse_file(source.decode("utf-8"), path or f"app/views/fixtures/{name}", "erb")
    return source, symbols


def _view(symbols):
    views = [s for s in symbols if s.language == "erb" and s.signature.startswith("view ")]
    assert len(views) == 1, [(s.name, s.signature) for s in views]
    return views[0]


def _line_of(source: bytes, offset: int) -> int:
    return source.count(b"\n", 0, offset) + 1


def _named(symbols, name, language=None):
    found = [s for s in symbols if s.name == name and (language is None or s.language == language)]
    assert found, f"no {language or ''} symbol named {name!r} in {[(s.name, s.language) for s in symbols]}"
    return found[0]


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "path",
    [
        "app/views/users/show.html.erb",
        "app/views/users/show.en.html.erb",
        "app/views/users/index.html+phone.erb",
        "app/views/layouts/rescue.erb",
        "app/views/user_mailer/welcome.text.erb",
        "app/views/comments/create.js.erb",
        "app/views/messages/show.json.erb",
        "app/views/posts/feed.xml.erb",
        "lib/generators/model/templates/model.rb.erb",
        "config/database.yml.erb",
        "app/views/option_values/update.turbo_stream.erb",
        "app/views/v1.2/_partial.html.erb",
    ],
)
def test_every_erb_suffix_form_maps_to_erb(path):
    assert get_language_for_path(path) == "erb"


def test_erb_is_registered_and_no_existing_mapping_moves():
    assert LANGUAGE_EXTENSIONS[".erb"] == "erb"
    assert "erb" in LANGUAGE_REGISTRY
    for path, language in {
        "a.rb": "ruby", "a.rake": "ruby", "a.html": "html", "a.js": "javascript",
        "a.yml": "yaml", "a.ejs": "ejs", "a.cshtml": "razor", "a.aspx": "aspx",
    }.items():
        assert get_language_for_path(path) == language, path


def test_edit_hooks_treat_an_erb_file_as_code():
    assert ".erb" in _CODE_EXTENSIONS


def test_plan_refactoring_reads_a_render_line_and_a_definition():
    import re

    from jcodemunch_mcp.tools.plan_refactoring import _DEF_PATTERNS, _IMPORT_PATTERNS, fill_name_template

    assert _IMPORT_PATTERNS["erb"].match('<%= render "shared/nav" %>')
    assert not _IMPORT_PATTERNS["erb"].match("<p>render</p>")
    definition = re.compile(fill_name_template(_DEF_PATTERNS["erb"].pattern, "sidebar"))
    assert definition.match("<% def sidebar(x)")
    assert definition.match("<% content_for :sidebar do %>")
    assert definition.match("<%- provide(:sidebar, 'x') -%>")
    assert not definition.match("<%= sidebar(1) %>")
    assert not definition.match("<% def sidebar_extra %>")


# ---------------------------------------------------------------------------
# The masks keep every byte where it was
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_both_masks_keep_the_length_and_every_newline(name):
    source = (FIXTURES / name).read_bytes()
    ruby, host = extractor._erb_masks(source)
    assert len(ruby) == len(source) and len(host) == len(source)
    newlines = [i for i, b in enumerate(source) if b == 0x0A]
    assert [i for i, b in enumerate(ruby) if b == 0x0A] == newlines
    assert [i for i, b in enumerate(host) if b == 0x0A] == newlines


def test_the_ruby_mask_keeps_tag_code_in_place_and_drops_everything_else():
    source = (FIXTURES / "handwritten_slots.html.erb").read_bytes()
    ruby, host = extractor._erb_masks(source)
    at = source.index(b"current_user.name")
    assert ruby[at:at + len(b"current_user.name")] == b"current_user.name"
    # A literal `<%%= tags %>` is template TEXT, never Ruby.
    assert b"tags" not in ruby
    # The comment tag, and the HTML around the tags, are gone from the Ruby copy.
    assert b"Hand-written" not in ruby and b"<nav" not in ruby
    # The host copy keeps the HTML and loses the tags.
    assert b'<nav id="side-nav">' in host
    assert b"current_user" not in host and b"<%=" not in host.replace(b"<%%=", b"")


# ---------------------------------------------------------------------------
# Integrity over every fixture
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_every_symbol_is_a_slice_of_the_original_file(name):
    source, symbols = _parse(name)
    view = _view(symbols)
    assert (view.line, view.byte_offset, view.byte_length) == (1, 0, len(source))
    assert view.parent is None and view.kind == "class"

    ids = {s.id for s in symbols}
    for s in symbols:
        if s is view:
            continue
        # The YAML parser represents a key merged in by `<<: *anchor` as a
        # zero-length symbol at line 0; that is its own shape, not ERB's.
        if s.language == "yaml" and s.line == 0 and s.byte_length == 0:
            continue
        piece = source[s.byte_offset:s.byte_offset + s.byte_length]
        assert 0 <= s.byte_offset and s.byte_offset + s.byte_length <= len(source), s.name
        assert s.byte_length > 0, s.name
        assert s.line == _line_of(source, s.byte_offset), (s.name, s.line)
        assert s.end_line >= s.line
        assert s.content_hash == compute_content_hash(piece), s.name
        assert s.name.encode("utf-8") in piece, (s.name, piece[:80])
        assert s.parent in ids, (s.name, s.parent)
        assert s.file == f"app/views/fixtures/{name}"

    own = [s.id for s in symbols if s.language in ("erb", "ruby", "javascript")]
    assert len(own) == len(set(own)), "duplicate symbol ids"


# ---------------------------------------------------------------------------
# HTML hosts
# ---------------------------------------------------------------------------

def test_the_view_is_named_up_to_its_first_dot():
    _, symbols = _parse("rails_rescues_trace.html.erb", "actionpack/rescues/_trace.html.erb")
    view = _view(symbols)
    assert view.name == "_trace" and view.signature == "view _trace"


def test_a_trace_view_keeps_its_script_function_and_skips_dynamic_ids():
    source, symbols = _parse("rails_rescues_trace.html.erb")
    view = _view(symbols)
    fn = _named(symbols, "changeSourceExtract", "javascript")
    assert fn.kind == "function"
    assert fn.line == _line_of(source, source.index(b"function changeSourceExtract"))
    # Both ids in this file are built by a tag (`id="traces-<%= ... %>"`); neither is a symbol.
    assert not [s for s in symbols if s.language == "erb" and s is not view and s.kind == "constant"
                and ("traces" in s.name or "X" * 3 in s.name)]


def test_a_layout_yields_slots_ids_and_script_functions():
    source, symbols = _parse("rails_rescues_layout.erb", "actionpack/rescues/layout.erb")
    view = _view(symbols)
    assert view.name == "layout"
    slot = _named(symbols, "style", "erb")
    assert slot.kind == "constant" and slot.signature == "yield :style"
    assert slot.qualified_name == "layout.yield:style"
    copy_id = _named(symbols, "exception-message-for-copy", "erb")
    assert copy_id.kind == "constant" and copy_id.parent == view.id
    toggle = _named(symbols, "toggle", "javascript")
    assert toggle.line == _line_of(source, source.index(b"var toggle = function"))
    assert toggle.parent == view.id


def test_content_for_provide_and_yield_are_slots_and_static_ids_are_constants():
    _, symbols = _parse("handwritten_slots.html.erb", "app/views/layouts/handwritten_slots.html.erb")
    view = _view(symbols)
    slots = {s.qualified_name: s for s in symbols if s.language == "erb" and ":" in s.qualified_name}
    assert set(slots) == {
        "handwritten_slots.content_for:sidebar",
        "handwritten_slots.provide:title",
        "handwritten_slots.yield:title",
    }
    assert slots["handwritten_slots.content_for:sidebar"].signature == "content_for :sidebar"
    ids = {s.name for s in symbols if s.language == "erb" and s.kind == "constant" and ":" not in s.qualified_name}
    assert ids == {"side-nav", "greeting"}
    helper = _named(symbols, "helper_inside_a_view", "ruby")
    assert helper.parent == view.id and helper.qualified_name == "handwritten_slots.helper_inside_a_view"
    wire = _named(symbols, "wireSidebar", "javascript")
    assert wire.parent == view.id


def test_a_script_whose_body_loops_in_tags_still_parses():
    _, symbols = _parse("solidus_orders_form.html.erb", "backend/app/views/spree/admin/orders/_form.html.erb")
    view = _view(symbols)
    assert view.name == "_form"
    order_number = _named(symbols, "order_number", "javascript")
    assert order_number.parent == view.id


def test_an_application_layout_has_its_static_id():
    _, symbols = _parse("solidus_admin_application.html.erb")
    main = _named(symbols, "main", "erb")
    assert main.kind == "constant"


# ---------------------------------------------------------------------------
# Host-language passes
# ---------------------------------------------------------------------------

def test_a_js_erb_function_is_javascript_and_keeps_its_line():
    source, symbols = _parse("handwritten_widget.js.erb", "app/views/widgets/show.js.erb")
    view = _view(symbols)
    render = _named(symbols, "renderWidget", "javascript")
    assert render.kind == "function" and render.parent == view.id
    assert render.line == _line_of(source, source.index(b"function renderWidget"))
    assert render.qualified_name == "show.renderWidget"
    # A JavaScript host has no HTML ids.
    assert not [s for s in symbols if s.language == "erb" and s.kind == "constant"]


def test_the_view_is_the_first_symbol_even_when_host_code_starts_at_byte_zero(tmp_path):
    # The indexer reads a file's language off its FIRST symbol, so a host
    # function at offset 0 sorted ahead of the view filed a `.js.erb` as
    # javascript (found on forem's initializeCommentsPage.js.erb).
    # `alpha` sorts ahead of the view's name `widget`: a tie broken by name.
    source = "function alpha() {\n  return '<%= j t(:x) %>';\n}\n"
    symbols = parse_file(source, "app/assets/javascripts/widget.js.erb", "erb")
    assert symbols[0].signature == "view widget" and symbols[0].language == "erb"
    assert _named(symbols, "alpha", "javascript").byte_offset == 0

    root = tmp_path / "app"
    (root / "assets").mkdir(parents=True)
    (root / "assets" / "widget.js.erb").write_text(source, encoding="utf-8")
    result = index_folder(str(root), use_ai_summaries=False, storage_path=str(tmp_path / "store"))
    assert result["languages"] == {"erb": 1}


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_every_fixture_leads_with_its_view(name):
    # Including a `.yml.erb`, whose YAML merge keys come back at line 0.
    _, symbols = _parse(name)
    assert symbols[0].signature.startswith("view ") and symbols[0].language == "erb"


def test_a_yml_erb_with_a_merge_key_is_indexed_as_erb(tmp_path):
    root = tmp_path / "app"
    (root / "config").mkdir(parents=True)
    shutil.copy(FIXTURES / "handwritten_database.yml.erb", root / "config" / "database.yml.erb")
    result = index_folder(str(root), use_ai_summaries=False, storage_path=str(tmp_path / "store"))
    assert result["languages"] == {"erb": 1}


@pytest.mark.parametrize(
    "path, source, name",
    [
        ("lib/templates/model.rb.erb",
         "<% if options[:api] %>\ndef bar\n  1\nend\n<% else %>\ndef bar\n  2\nend\n<% end %>\n", "bar"),
        ("app/views/a/show.js.erb", "function go() { return 1; }\nfunction go() { return 2; }\n", "go"),
        ("app/views/a/show.html.erb",
         "<% if a %><% def helper; 1; end %><% else %><% def helper; 2; end %><% end %>\n", "helper"),
    ],
    ids=["rb-erb-branches", "js-erb-redefinition", "tag-ruby-branches"],
)
def test_same_named_definitions_in_two_branches_are_both_kept(path, source, name):
    symbols = parse_file(source, path, "erb")
    found = [s for s in symbols if s.name == name]
    assert len(found) == 2, [(s.name, s.id) for s in symbols]
    assert (found[0].line, found[0].byte_offset) < (found[1].line, found[1].byte_offset)
    assert len({s.id for s in symbols}) == len(symbols)


def test_two_tag_built_names_of_the_same_length_keep_their_own_tags():
    source = (
        "class <%= a %>Report\n  def first; end\nend\n"
        "class <%= b %>Report\n  def second; end\nend\n"
    )
    symbols = parse_file(source, "lib/templates/model.rb.erb", "erb")
    data = source.encode("utf-8")
    a = _named(symbols, "<%= a %>Report", "ruby")
    b = _named(symbols, "<%= b %>Report", "ruby")
    assert a.line == 1 and b.line == 4
    assert _named(symbols, "first", "ruby").qualified_name == "model.<%= a %>Report.first"
    assert _named(symbols, "second", "ruby").qualified_name == "model.<%= b %>Report.second"
    for s in (a, b):
        assert s.name.encode() in data[s.byte_offset:s.byte_offset + s.byte_length]


def test_a_tag_built_class_nested_in_another_keeps_each_owner_in_order():
    # Outer and inner tags have one length, so they fill identically; the
    # qualified name must read outer-then-inner, never the other way round.
    source = (
        "class <%= a %>Report\n"
        "  class <%= b %>Report\n"
        "    def run; end\n"
        "  end\n"
        "end\n"
    )
    symbols = parse_file(source, "lib/templates/model.rb.erb", "erb")
    run = _named(symbols, "run", "ruby")
    assert run.qualified_name == "model.<%= a %>Report.<%= b %>Report.run"
    inner = _named(symbols, "<%= b %>Report", "ruby")
    assert inner.qualified_name == "model.<%= a %>Report.<%= b %>Report"
    assert run.parent == inner.id


def test_a_literal_owner_that_looks_like_the_filler_keeps_its_name():
    # `XXXXXXXXReport` is a real class name; its member `<%= b %>Report` fills
    # to the same text, and only the member's own segment is restored.
    source = "class XXXXXXXXReport\n  class <%= b %>Report\n    def run; end\n  end\nend\n"
    symbols = parse_file(source, "lib/templates/model.rb.erb", "erb")
    inner = _named(symbols, "<%= b %>Report", "ruby")
    assert inner.qualified_name == "model.XXXXXXXXReport.<%= b %>Report"
    assert _named(symbols, "run", "ruby").qualified_name == "model.XXXXXXXXReport.<%= b %>Report.run"


def test_a_literal_name_that_looks_like_the_filler_is_never_renamed():
    # `<%= abcd %>` is 11 bytes, the length of `XXXXXXXXXXX`.
    source = "var n = <%= abcd %>;\nfunction XXXXXXXXXXXReady() { return n; }\n"
    symbols = parse_file(source, "app/views/a/show.js.erb", "erb")
    assert _named(symbols, "XXXXXXXXXXXReady", "javascript").qualified_name == "show.XXXXXXXXXXXReady"


def test_a_name_built_by_a_tag_is_named_by_the_tag_never_by_the_filler():
    _, symbols = _parse("handwritten_widget.js.erb")
    ready = [s for s in symbols if s.name.endswith("Ready")]
    assert [s.name for s in ready] == ["<%= @callback_name %>Ready"]
    assert not [s for s in symbols if "XXX" in s.name or "XXX" in s.qualified_name]


# Filler as the host copy writes it: a run of X, or a per-tag token (`X` and a
# four-character code). No symbol field may show either.
_FILLER = re.compile(r"XXXXX|X[0-9a-z]{4}")


def _text_fields(s):
    return [s.name, s.qualified_name, s.id, s.signature, s.docstring, s.summary,
            *s.decorators, *s.keywords, *s.call_references]


def _leaked(symbols):
    return [_text_fields(s) for s in symbols if any(_FILLER.search(f) for f in _text_fields(s))]


# YAML and XML hosts give a child no parent link to its tag-built owner, so a
# restore that walks parent links never reaches the owner's segment. Equal-length
# tags, adjacent tags, a tag beside a literal X, a merge key's copy and a span
# that starts before the name are the shapes a positional restore got wrong.
_UNLINKED_OWNER_SOURCES = [
    ("config/x.yml.erb", "<%= Rails.env %>:\n  adapter: pg\n"),
    ("config/database.yml.erb", "<%= env %>:\n  <%= k1 %>: <%= v %>\n"),
    ("config/database.yml.erb", "production:\n  database: app_<%= Rails.env %>\n"),
    ("app/views/a/feed.xml.erb", '<feed>\n<<%= tag %> id="a"></<%= tag %>>\n</feed>\n'),
    ("config/a.yml.erb", "<%= env %>:\n  <%= key %>: x\n  adapter: pg\n"),
    ("config/a.yml.erb", "production:\n  database: <%= pre %><%= env %>\n"),
    ("config/a.yml.erb", "production:\n  database: X<%= env %>\n"),
    ("config/a.yml.erb", "defaults: &d\n  adapter: <%= aa %>x\ndev:\n  <<: *d\n  db: <%= bb %>y\n"),
    ("config/a.yml.erb", "en:\n  <%= k1 %>: one\n  <%= k2 %>: two\n"),
    ("lib/t/m.java.erb", "class A {\n  @Named(<%= aa %>) void <%= bb %>() {}\n}\n"),
    ("lib/a.py.erb", "@<%= deco %>\ndef f():\n    '''<%= doc %>'''\n    <%= callee %>()\n"),
    ("app/a.js.erb", "/** <%= doc %> */\nfunction f() {}\n"),
    ("lib/a.py.erb", "<%= a\n%>0000 = 1\n"),
    ("app/a.js.erb", "var v = <%= x %>;\nfunction f<%=\n  y\n%>0000() {}\n"),
]


# A multi-line output tag: a line too short for a code, and a continuation
# line's indentation, are blanked, so no name is a bare X run, none starts
# with whitespace, and text after the tag is never read as another tag.
_MULTI_LINE_TAG_SOURCES = [
    ("lib/a.rb.erb", "class <%=\n  name\n%>Report\nend\n"),
    ("config/a.yml.erb", "a:\n  k<%= x\n%>: v\n"),
    ("lib/a.py.erb", "<%= a\n%>0000 = 1\n"),
    ("app/a.js.erb", "var v = <%= x %>;\nfunction f<%=\n  y\n%>0000() {}\n"),
    ("app/a.js.erb", "function <%=\n  name %>() {}\n"),
]


@pytest.mark.parametrize("path, source", _MULTI_LINE_TAG_SOURCES)
def test_a_multi_line_tag_never_names_a_symbol_with_filler_or_whitespace(path, source):
    for s in parse_file(source, path, "erb"):
        for segment in re.split(r"[.:#]", s.qualified_name):
            assert not re.fullmatch(r"X{1,4}", segment), s.qualified_name
            assert segment == segment.strip(), s.qualified_name
        assert "X<%= a" not in s.qualified_name


def test_docstrings_decorators_and_calls_show_the_tag():
    source = "@<%= deco %>\ndef f():\n    '''<%= doc %>'''\n    <%= callee %>()\n"
    f = _named(parse_file(source, "lib/a.py.erb", "erb"), "f", "python")
    assert f.decorators == ["@<%= deco %>"]
    assert "<%= doc %>" in f.docstring
    assert f.call_references == ["<%= callee %>"]


@pytest.mark.parametrize("path, source", _UNLINKED_OWNER_SOURCES)
def test_no_field_of_any_symbol_shows_the_filler(path, source):
    assert not _leaked(parse_file(source, path, "erb"))


def test_each_output_tag_fills_to_its_own_identifier():
    source = b"<%= a %>, <%= b %>, <%=%>\n<%= multi\n line %>"
    _, host = extractor._erb_masks(source)
    first, second, shortest = host[0:8], host[10:18], host[20:25]
    assert len({first, second}) == 2
    for token in (first, second, shortest):
        assert re.fullmatch(rb"X[0-9a-z]{4}X*", token)


def test_a_filler_code_written_in_the_file_is_never_used_for_a_tag():
    # The first code is `0000`; the file already spells `X0000`, so the tag
    # takes another and the literal name is left alone.
    source = "var n = <%= abcd %>;\nfunction X0000XXXXXXReady() { return n; }\n"
    symbols = parse_file(source, "app/views/a/show.js.erb", "erb")
    assert _named(symbols, "X0000XXXXXXReady", "javascript").qualified_name == "show.X0000XXXXXXReady"


def test_a_code_spelled_after_a_tag_is_never_used_for_another_tag():
    # `<%= a %>` fills to `X0000XXX`, and the text after it reads `X0001…`
    # in the filled copy, so the next tag must not take `0001`.
    _, host = extractor._erb_masks(b"<%= a %>0001XXXXX <%= bb %>")
    assert host[:8] == b"X0000XXX"
    assert host[18:27] != b"X0001XXXX" and re.fullmatch(rb"X[0-9a-z]{4}XXXX", host[18:27])


def test_a_token_cut_short_is_left_as_written_never_read_past():
    from jcodemunch_mcp.parser.symbols import Symbol

    def sym(name):
        return Symbol(id=name, file="f", name=name, qualified_name=name, kind="function",
                      language="javascript", signature=name, byte_length=1)

    tokens = {"0000": ("X0000XXX", "<%= a %>")}
    adopted = extractor._erb_adopt([sym("X0000XXab"), sym("X0000XXXab")], "f.js.erb", sym("v"), b"x", tokens)
    assert [s.name for s in adopted] == ["X0000XXab", "<%= a %>ab"]


def test_a_child_is_qualified_by_its_owner_never_by_an_equal_length_sibling():
    source = "<%= env %>:\n  <%= key %>: x\n  adapter: pg\n"
    symbols = parse_file(source, "config/a.yml.erb", "erb")
    assert _named(symbols, "adapter", "yaml").qualified_name == "a.<%= env %>.adapter"
    source = "<%= env %>:\n  <%= key %>:\n    deep: 1\n  adapter: pg\n"
    symbols = parse_file(source, "config/a.yml.erb", "erb")
    assert _named(symbols, "adapter", "yaml").qualified_name == "a.<%= env %>.adapter"
    assert _named(symbols, "deep", "yaml").qualified_name == "a.<%= env %>.<%= key %>.deep"


def test_equal_length_yaml_keys_are_two_keys():
    symbols = parse_file("en:\n  <%= k1 %>: one\n  <%= k2 %>: two\n", "config/a.yml.erb", "erb")
    assert "'one'" in _named(symbols, "<%= k1 %>", "yaml").signature
    assert "'two'" in _named(symbols, "<%= k2 %>", "yaml").signature
    symbols = parse_file("<%= aa %>:\n  x: 1\n<%= bb %>:\n  y: 1\n", "config/a.yml.erb", "erb")
    assert _named(symbols, "x", "yaml").qualified_name == "a.<%= aa %>.x"
    assert _named(symbols, "y", "yaml").qualified_name == "a.<%= bb %>.y"


def test_a_rewritten_signature_shows_adjacent_tags_and_literal_text_as_written():
    def database(source):
        return _named(parse_file(source, "config/a.yml.erb", "erb"), "database", "yaml").signature

    assert "<%= pre %><%= env %>" in database("production:\n  database: <%= pre %><%= env %>\n")
    assert "X<%= env %>" in database("production:\n  database: X<%= env %>\n")
    literal = "app_<%= Rails.env %> XXXXXXXXXXXXXXXX"
    assert literal in database(f"production:\n  database: {literal}\n")


def test_a_merge_key_copy_shows_the_anchor_s_tag():
    source = "defaults: &d\n  adapter: <%= aa %>x\ndev:\n  <<: *d\n  db: <%= bb %>y\n"
    symbols = parse_file(source, "config/a.yml.erb", "erb")
    adapters = [s for s in symbols if s.name == "adapter"]
    assert adapters and all("<%= aa %>x" in s.signature for s in adapters)


def test_a_name_is_its_own_tag_when_the_span_starts_before_it():
    source = "class A {\n  @Named(<%= aa %>) void <%= bb %>() {}\n}\n"
    symbols = parse_file(source, "lib/t/m.java.erb", "erb")
    method = _named(symbols, "<%= bb %>", "java")
    assert method.qualified_name == "m.A.<%= bb %>"


def test_a_child_of_a_tag_built_yaml_key_is_qualified_by_the_tag():
    symbols = parse_file("<%= Rails.env %>:\n  adapter: pg\n", "config/x.yml.erb", "erb")
    assert _named(symbols, "adapter", "yaml").qualified_name == "x.<%= Rails.env %>.adapter"


def test_nested_tag_built_yaml_keys_each_get_their_own_tag():
    # `<%= k1 %>` fills to 9 X and `<%= env %>` to 10: the shorter must not be
    # found inside the longer.
    symbols = parse_file("<%= env %>:\n  <%= k1 %>: <%= v %>\n", "config/database.yml.erb", "erb")
    assert _named(symbols, "<%= k1 %>", "yaml").qualified_name == "database.<%= env %>.<%= k1 %>"


def test_an_owner_is_never_found_inside_a_longer_sibling_key():
    # Between `child` and its 9-X owner sits the 10-X sibling `<%= env %>`.
    source = "<%= k1 %>:\n  <%= env %>: 1\n  child: 2\n"
    symbols = parse_file(source, "config/database.yml.erb", "erb")
    assert _named(symbols, "child", "yaml").qualified_name == "database.<%= k1 %>.child"


def test_a_signature_the_yaml_parser_rewrote_shows_the_tag():
    source = "production:\n  database: app_<%= Rails.env %>\n"
    symbols = parse_file(source, "config/database.yml.erb", "erb")
    assert "app_<%= Rails.env %>" in _named(symbols, "database", "yaml").signature


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_no_fixture_symbol_shows_the_filler(name):
    source, symbols = _parse(name)
    assert not _FILLER.search(source.decode("utf-8", errors="replace"))
    assert not _leaked(symbols)


def test_a_signature_read_from_the_file_shows_the_tag_never_the_filler():
    _, symbols = _parse("handwritten_widget.js.erb")
    assert _named(symbols, "count", "javascript").signature == "var count = <%= @items.size %>;"
    _, symbols = _parse("solidus_orders_form.html.erb")
    assert _named(symbols, "order_number", "javascript").signature == "var order_number = '<%= @order.number %>';"


def test_a_rb_erb_class_owns_its_methods_and_a_tag_named_class_owns_its_own():
    _, symbols = _parse("handwritten_model.rb.erb", "lib/templates/model.rb.erb")
    view = _view(symbols)
    archive = _named(symbols, "Archive", "ruby")
    assert archive.kind == "class" and archive.parent == view.id
    assert archive.qualified_name == "model.Archive"
    for method in ("table_label", "summary"):
        assert _named(symbols, method, "ruby").parent == archive.id
    report = _named(symbols, "<%= class_name %>Report", "ruby")
    generate = _named(symbols, "generate", "ruby")
    assert generate.parent == report.id
    assert generate.qualified_name == "model.<%= class_name %>Report.generate"
    assert not [s for s in symbols if "XXX" in s.name or "XXX" in s.qualified_name]


def test_a_yaml_host_is_parsed_as_yaml():
    _, symbols = _parse("handwritten_database.yml.erb", "config/database.yml.erb")
    view = _view(symbols)
    pool = _named(symbols, "pool", "yaml")
    assert pool.parent == view.id
    assert _named(symbols, "development", "yaml").parent == view.id


@pytest.mark.parametrize(
    "name",
    [
        "rails_rescues_trace.text.erb",
        "rails_messages_show.json.erb",
        "rails_comments_show.js.erb",
        "rails_customer.xml.erb",
        "solidus_option_values_create.turbo_stream.erb",
        "rails_fragment_cached_with_variant.html+phone.erb",
    ],
)
def test_small_hosts_of_every_kind_yield_a_view(name):
    _, symbols = _parse(name)
    view = _view(symbols)
    assert view.name == name.split(".", 1)[0]


# ---------------------------------------------------------------------------
# Tag Ruby: statement boundaries and blocks across tags
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "source, names",
    [
        # Each of these loses a `def` when a tag's close is not a statement end.
        ("<% if a %><%= b %><% end %><% def after; end %>", ["after"]),
        ("<% def one %><% end %><% def after; end %>", ["one", "after"]),
        ("<% def before %><% end %><% def after %><% end %>", ["before", "after"]),
        ("<% def a %>1<% end %>2<% def after %>3<% end %>", ["a", "after"]),
    ],
)
def test_adjacent_tags_on_one_line_stay_separate_statements(source, names):
    symbols = parse_file(source, "app/views/a/one_line.html.erb", "erb")
    assert [s.name for s in symbols if s.language == "ruby"] == names


def test_each_tag_close_is_a_statement_end_in_the_ruby_copy():
    ruby, _ = extractor._erb_masks(b"<% a %><% b %>")
    assert ruby == b"   a ;    b ; "
    ruby, _ = extractor._erb_masks(b"<%- a -%>")
    assert ruby == b"    a ;  "


def test_an_id_written_inside_a_tag_is_ruby_not_markup():
    source = """<%= raw('<p id="ruby-made">') %><div id="static"></div>"""
    symbols = parse_file(source, "app/views/a/ids.html.erb", "erb")
    assert [s.name for s in symbols if s.kind == "constant"] == ["static"]


def test_an_attribute_ending_in_id_is_not_an_id():
    source = """<div data-id="5" aria:id="6" id="real"></div>"""
    symbols = parse_file(source, "app/views/a/ids.html.erb", "erb")
    assert [s.name for s in symbols if s.kind == "constant"] == ["real"]


def test_a_block_spanning_tags_does_not_hide_what_follows():
    source = (
        "<%= form_with model: @post do |f| %>\n"
        "  <%= f.text_field :title %>\n"
        "  <% if f.object.persisted? %><p>saved</p><% end %>\n"
        "<% end %>\n"
        "<% def after_the_block; end %>\n"
    )
    symbols = parse_file(source, "app/views/a/form.html.erb", "erb")
    after = _named(symbols, "after_the_block", "ruby")
    assert after.line == 5


@pytest.mark.parametrize(
    "source",
    [
        "",
        "<p>no tags at all</p>\n",
        "<% unterminated statement\n<p>",
        "<%= unterminated output",
        "<%# unterminated comment",
        "%> a closer with no opener <%",
        "<%%",
        "<p>a</p>\r\n<% def crlf; end %>\r\n<div id=\"x\"></div>\r\n",
        "<script>never closed <%= x %>",
        "<script></script><script>",
        "<% content_for :",
        "<%= yield( :side ) %><% provide(:t) %>",
    ],
)
def test_awkward_inputs_parse_without_raising(source):
    data = source.encode("utf-8")
    symbols = parse_file(source, "app/views/a/edge.html.erb", "erb")
    view = _view(symbols)
    assert view.byte_length == len(data)
    for s in symbols:
        assert 0 <= s.byte_offset <= s.byte_offset + s.byte_length <= len(data)
        if s is not view:
            assert s.line == _line_of(data, s.byte_offset)


def test_crlf_line_endings_keep_lines():
    source = "<p>a</p>\r\n<% def crlf; end %>\r\n<div id=\"x\"></div>\r\n"
    symbols = parse_file(source, "app/views/a/crlf.html.erb", "erb")
    assert _named(symbols, "crlf", "ruby").line == 2
    assert _named(symbols, "x", "erb").line == 3


# ---------------------------------------------------------------------------
# Cost: linear in file size on hostile input
# ---------------------------------------------------------------------------

def _checkpoints(monkeypatch, content: str) -> int:
    count = 0
    real = parse_budget.checkpoint

    def counting():
        nonlocal count
        count += 1
        real()

    monkeypatch.setattr(parse_budget, "checkpoint", counting)
    try:
        parse_file(content, "app/views/a/hostile.html.erb", "erb")
    finally:
        monkeypatch.setattr(parse_budget, "checkpoint", real)
    return count


@pytest.mark.parametrize(
    "build",
    [
        lambda n: "<% a\n" * n,
        lambda n: "<%= a %>" * n,
        lambda n: "<script>" * n,
        lambda n: "<script><%= a %></script>\n" * n,
        lambda n: '<p id="a' + "b" * n,
        lambda n: "<% provide" + " " * (50 * n) + "x %>",
        lambda n: "<% content_for :a %>\n" * n,
    ],
    ids=["unclosed-tags", "output-tags", "unclosed-scripts", "scripts-with-tags", "unclosed-id", "slot-then-spaces", "slots"],
)
def test_twice_the_file_is_about_twice_the_work(monkeypatch, build):
    small = _checkpoints(monkeypatch, build(200))
    big = _checkpoints(monkeypatch, build(400))
    assert small > 0, "no checkpoint passed: the ERB path is not on the parse budget"
    assert big / small < 2.6, f"{small} checkpoints at 200, {big} at 400"


@pytest.mark.parametrize(
    "source",
    [
        "<script>" * 40_000,
        "<% provide" + " " * 300_000 + "x %>",
        "<% yield" + "\t" * 300_000,
        '<div id="' + "a" * 300_000,
    ],
    ids=["unclosed-scripts", "provide-then-spaces", "yield-then-tabs", "unclosed-id"],
)
def test_hostile_input_of_a_few_hundred_kilobytes_parses_quickly(source):
    import time

    started = time.perf_counter()
    symbols = parse_file(source, "app/views/a/hostile.html.erb", "erb")
    assert time.perf_counter() - started < 5.0
    _view(symbols)  # it was parsed as ERB, not skipped


# ---------------------------------------------------------------------------
# Through the indexer
# ---------------------------------------------------------------------------

def test_an_index_built_before_erb_picks_up_the_views_on_the_next_run(tmp_path, monkeypatch):
    # No PARSER_GENERATION bump came with `erb`: the next walk finds the views
    # as new files. This fails if a fast path ever skips that walk.
    from jcodemunch_mcp.parser import languages

    root = tmp_path / "app"
    (root / "views").mkdir(parents=True)
    (root / "a.rb").write_text("class A\n  def x; end\nend\n", encoding="utf-8")
    (root / "views" / "show.html.erb").write_text("<div id='hero'></div>\n", encoding="utf-8")
    store = tmp_path / "store"

    monkeypatch.delitem(languages.LANGUAGE_EXTENSIONS, ".erb")
    before = index_folder(str(root), use_ai_summaries=False, storage_path=str(store))
    assert before["languages"] == {"ruby": 1}
    monkeypatch.undo()

    after = index_folder(str(root), use_ai_summaries=False, storage_path=str(store))
    assert after["new"] == 1
    found = search_symbols(repo=after["repo"], query="hero", language="erb", storage_path=str(store))
    assert [r["name"] for r in found["results"]] == ["hero"]


def test_index_folder_indexes_views_and_search_finds_each_language(tmp_path):
    root = tmp_path / "shop"
    views = root / "app" / "views" / "widgets"
    views.mkdir(parents=True)
    shutil.copy(FIXTURES / "handwritten_widget.js.erb", views / "show.js.erb")
    shutil.copy(FIXTURES / "handwritten_slots.html.erb", views / "_slots.html.erb")
    shutil.copy(FIXTURES / "rails_rescues_trace.text.erb", views / "trace.text.erb")
    store = tmp_path / "store"

    result = index_folder(str(root), use_ai_summaries=False, storage_path=str(store))

    assert result["success"] is True
    assert result["languages"]["erb"] == 3
    assert "app/views/widgets/show.js.erb" in result["files"]

    found = search_symbols(repo=result["repo"], query="renderWidget", language="javascript", storage_path=str(store))
    assert [r["name"] for r in found["results"]][:1] == ["renderWidget"]
    found = search_symbols(repo=result["repo"], query="helper_inside_a_view", language="ruby", storage_path=str(store))
    assert [r["name"] for r in found["results"]][:1] == ["helper_inside_a_view"]
    found = search_symbols(repo=result["repo"], query="side-nav", language="erb", storage_path=str(store))
    assert "side-nav" in [r["name"] for r in found["results"]]
