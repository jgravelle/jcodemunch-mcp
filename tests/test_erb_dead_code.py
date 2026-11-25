"""Dead-code tools on a Rails app: an `.erb` view is never proven unused.

A view is rendered by a name the index does not resolve (`render "shared/nav"`
names `shared/_nav.html.erb`) or by controller convention with no call at all,
so "no importer" proves nothing about it. The tools cap a view, as #1012 does
for a Web Forms control, rather than root it: a view may still be dead.

Each capped case has a twin that must still be reported, so a fix that marks
everything live fails here too.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jcodemunch_mcp.tools.index_folder import index_folder

_CONTROLLER = """class UsersController < ApplicationController
  def show
    @user = User.find(params[:id])
  end
end
"""

_SHOW = """<h1><%= @user.name %></h1>
<%= render "shared/nav" %>
"""

_NAV = """<nav id="main-nav">
  <%= link_to "Home", root_path %>
</nav>
<script>
function toggleNav() { document.body.classList.toggle("nav-open"); }
</script>
"""

_ORPHAN = """class Orphan
  def unused
    42
  end
end
"""

# One import edge, so get_dead_code_v2 runs its three-signal path: with no
# import graph at all it falls back to call-graph-only mode, which has no
# file-level signal for a cap to act on.
_APPLICATION = """require_relative "../lib/version"
"""

_VERSION = """module App
  VERSION = "1.0"
end
"""

_SHOW_FILE = "app/views/users/show.html.erb"
_NAV_FILE = "app/views/shared/_nav.html.erb"
_ORPHAN_FILE = "lib/orphan.rb"


@pytest.fixture(scope="module")
def rails_repo(tmp_path_factory) -> tuple[str, str]:
    root: Path = tmp_path_factory.mktemp("rails_app")
    files = {
        "app/controllers/users_controller.rb": _CONTROLLER,
        _SHOW_FILE: _SHOW,
        _NAV_FILE: _NAV,
        _ORPHAN_FILE: _ORPHAN,
        "config/application.rb": _APPLICATION,
        "lib/version.rb": _VERSION,
    }
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    storage = str(root / ".index")
    result = index_folder(str(root), use_ai_summaries=False, storage_path=storage)
    return result["repo"], storage


def _index(repo_id, storage):
    from jcodemunch_mcp.storage import IndexStore
    from jcodemunch_mcp.tools._utils import resolve_repo

    owner, name = resolve_repo(repo_id, storage)
    return IndexStore(base_path=storage).load_index(owner, name)


def _dead_files(out: dict) -> dict[str, dict]:
    return {d["file"]: d for d in out.get("dead_files", [])}


def _symbol_id(repo_id, storage, file, name):
    index = _index(repo_id, storage)
    ids = [s["id"] for s in index.symbols if s.get("file") == file and s.get("name") == name]
    assert ids, f"no {name} in {file}: {[s.get('name') for s in index.symbols if s.get('file') == file]}"
    return ids[0]


def test_the_fixture_is_indexed_with_its_views_as_erb(rails_repo):
    """Non-vacuity: without the views every capped check passes trivially."""
    index = _index(*rails_repo)
    assert index.imports, "no import edge: v2 would take its call-graph-only path"
    files = set(index.source_files)
    for f in (_SHOW_FILE, _NAV_FILE, _ORPHAN_FILE, "app/controllers/users_controller.rb"):
        assert f in files, f"{f} was not indexed: {sorted(files)}"
    names = {(s["file"], s["name"]) for s in index.symbols}
    for name in ("_nav", "main-nav", "toggleNav"):
        assert (_NAV_FILE, name) in names, sorted(names)


@pytest.mark.parametrize(
    "path",
    ["app/views/users/show.html.erb", "app/views/users/create.js.erb",
     "app/views/layouts/mailer.text.erb", "templates/page.erb", "APP/VIEWS/X.HTML.ERB"],
)
def test_every_erb_view_has_an_unseen_consumer(path):
    from jcodemunch_mcp.tools._entry_points import unseen_consumer

    assert unseen_consumer(path, dotnet=False) == "template_rendered"
    assert unseen_consumer(path, dotnet=True) == "template_rendered"


@pytest.mark.parametrize("path", ["app/models/user.rb", "lib/erb_helper.rb", "app/views/x.html"])
def test_a_file_that_is_not_a_view_has_no_unseen_consumer(path):
    from jcodemunch_mcp.tools._entry_points import unseen_consumer

    assert unseen_consumer(path, dotnet=False) is None


def test_a_rails_app_does_not_read_as_dotnet(rails_repo):
    """A view's cap must not change the advice a schema gets in the same repository."""
    from jcodemunch_mcp.tools._entry_points import has_dotnet_evidence

    assert not has_dotnet_evidence(_index(*rails_repo).source_files)


@pytest.mark.parametrize("path", [_SHOW_FILE, _NAV_FILE])
def test_an_unreferenced_view_is_capped_not_proven_dead(rails_repo, path):
    from jcodemunch_mcp.tools._corpus_adequacy import UNPROVEN_CEILING
    from jcodemunch_mcp.tools.find_dead_code import find_dead_code

    repo_id, storage = rails_repo
    dead = _dead_files(find_dead_code(repo_id, min_confidence=0.0, storage_path=storage))
    row = dead.get(path)
    assert row is not None, f"{path} not listed at min_confidence=0: {sorted(dead)}"
    assert row["confidence"] <= UNPROVEN_CEILING, row
    assert row["uncapped_confidence"] == 1.0, row
    assert "template_rendered" in row["confidence_capped_by"], row


def test_views_are_withheld_at_the_default_threshold_and_counted(rails_repo):
    from jcodemunch_mcp.tools.find_dead_code import find_dead_code

    repo_id, storage = rails_repo
    out = find_dead_code(repo_id, storage_path=storage)
    dead = _dead_files(out)
    assert _SHOW_FILE not in dead and _NAV_FILE not in dead, sorted(dead)
    assert out.get("template_rendered_withheld", 0) >= 2, out.get("analysis_notes")
    assert any("render" in note for note in out.get("analysis_notes", [])), out.get("analysis_notes")


def test_a_symbol_in_a_view_carries_the_view_s_cap(rails_repo):
    from jcodemunch_mcp.tools._corpus_adequacy import UNPROVEN_CEILING
    from jcodemunch_mcp.tools.find_dead_code import find_dead_code

    repo_id, storage = rails_repo
    out = find_dead_code(repo_id, min_confidence=0.0, granularity="symbol", storage_path=storage)
    in_views = [s for s in out["dead_symbols"] if s["file"] in (_SHOW_FILE, _NAV_FILE)]
    assert in_views, out["dead_symbols"]
    assert all(s["confidence"] <= UNPROVEN_CEILING for s in in_views), in_views


def test_an_unreferenced_ruby_file_is_still_reported_at_full_confidence(rails_repo):
    from jcodemunch_mcp.tools.find_dead_code import find_dead_code

    repo_id, storage = rails_repo
    row = _dead_files(find_dead_code(repo_id, min_confidence=0.0, storage_path=storage)).get(_ORPHAN_FILE)
    assert row is not None and row["reason"] == "zero_importers", row
    assert not row.get("confidence_capped_by"), row


@pytest.mark.parametrize(
    "file,name,verdict",
    [
        (_NAV_FILE, "_nav", "dynamic_import_boundary"),
        (_NAV_FILE, "main-nav", "name_not_searchable"),
        (_NAV_FILE, "toggleNav", "dynamic_import_boundary"),
        (_SHOW_FILE, "show", "dynamic_import_boundary"),
    ],
)
def test_delete_preflight_does_not_certify_a_view_or_a_symbol_in_one(rails_repo, file, name, verdict):
    """The rendered partial of the report: `render "shared/nav"` reaches it unseen.

    `main-nav` is not a name a call site writes, so it keeps its narrower
    `name_not_searchable` verdict and gains the view's blocker beside it.
    """
    from jcodemunch_mcp.tools._corpus_adequacy import UNPROVEN_CEILING
    from jcodemunch_mcp.tools.check_delete_safe import check_delete_safe

    repo_id, storage = rails_repo
    sid = _symbol_id(repo_id, storage, file, name)
    out = check_delete_safe(repo_id, sid, cross_repo=False, include_runtime=False, storage_path=storage)
    assert out["verdict"] == verdict, out
    assert out["confidence"] <= UNPROVEN_CEILING, out
    assert any(b.get("kind") == "template_rendered" for b in out.get("blockers", [])), out
    if verdict == "dynamic_import_boundary":
        action = out["recommended_action"]
        assert "render" in action and "LoadControl" not in action, action


def test_delete_preflight_still_decides_a_ruby_file(rails_repo):
    from jcodemunch_mcp.tools.check_delete_safe import check_delete_safe

    repo_id, storage = rails_repo
    sid = _symbol_id(repo_id, storage, _ORPHAN_FILE, "Orphan")
    out = check_delete_safe(repo_id, sid, cross_repo=False, include_runtime=False, storage_path=storage)
    assert not any(b.get("kind") == "template_rendered" for b in out.get("blockers", [])), out
    assert out["verdict"] != "dynamic_import_boundary", out


def test_v2_leaves_unreachable_file_undecided_for_views(rails_repo):
    """v2 scores functions and methods only; `toggleNav` is the partial's one."""
    from jcodemunch_mcp.tools.get_dead_code_v2 import get_dead_code_v2

    repo_id, storage = rails_repo
    out = get_dead_code_v2(repo_id, min_confidence=0.0, storage_path=storage)
    block = out.get("unseen_consumer_boundary", {})
    assert block.get("files_by_kind", {}).get("template_rendered", 0) >= 1, block
    assert "template_rendered" in block.get("note", ""), block
    for row in out.get("dead_symbols", []):
        if ".erb" in row["id"]:
            assert "unreachable_file" not in row.get("signals", []), row
