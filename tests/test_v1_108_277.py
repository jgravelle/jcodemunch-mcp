"""Render-edge reachability in ``find_dead_code`` (jcm#461).

A template is never IMPORTED. It is reached by a render edge — a string argument
(``render(request, "page.html")``) that ``flow_edges`` resolves to a file. Before
this change, ``find_dead_code`` classified purely from the import graph, so an
actively-rendered template came back ``zero_importers`` at **confidence 1.0** —
the value reserved for "no importers and not a test file" — while
``flow_edges._resolve_template`` resolved that same file from the same index in
the same process.

⚠ The load-bearing test here is ``test_unrendered_template_is_still_dead``. It is
what distinguishes this fix from a blanket ``.html`` exemption, which would pass
every other test in this file while suppressing true positives. `.html` is not
special; having an inbound render edge is.
"""
from __future__ import annotations

from pathlib import Path

from jcodemunch_mcp.storage import IndexStore
from jcodemunch_mcp.tools.find_dead_code import find_dead_code
from jcodemunch_mcp.tools.flow_edges import _resolve_template
from jcodemunch_mcp.tools.index_folder import index_folder


_TEMPLATE = """<!doctype html>
<html><body><h1>A template</h1></body></html>
"""


def _build(tmp_path: Path, *, render: bool, extra_template: bool = False):
    """Index a Django-shaped repo. ``render`` controls whether the view
    actually renders the template, which is the only thing under test."""
    src = tmp_path / "src"
    (src / "templates").mkdir(parents=True)
    body = (
        '    return render(request, "page.html")\n'
        if render
        else "    return None\n"
    )
    (src / "app.py").write_text(
        "from django.shortcuts import render\n\n"
        "def home(request):\n" + body,
        encoding="utf-8",
    )
    (src / "templates" / "page.html").write_text(_TEMPLATE, encoding="utf-8")
    if extra_template:
        # Never referenced by any render call anywhere in the repo.
        (src / "templates" / "orphan.html").write_text(_TEMPLATE, encoding="utf-8")

    store_dir = tmp_path / "store"
    store_dir.mkdir()
    result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store_dir))
    assert result["success"], result
    return result["repo"], str(store_dir)


def _dead_files(repo: str, storage: str) -> dict[str, dict]:
    out = find_dead_code(repo, granularity="file", storage_path=storage)
    assert "error" not in out, out
    return {d["file"]: d for d in out.get("dead_files", [])}, out


def test_rendered_template_is_not_dead(tmp_path):
    """THE regression: a template an indexed view renders is reachable."""
    repo, storage = _build(tmp_path, render=True)
    dead, out = _dead_files(repo, storage)
    assert "templates/page.html" not in dead, (
        "an actively-rendered template was reported dead; dead_files=%r" % list(dead)
    )
    assert out["render_reachable_count"] == 1


def test_unrendered_template_is_still_dead(tmp_path):
    """The guard against an extension exemption.

    ⚠ A ``.html``-is-exempt implementation passes every other test in this file
    and fails this one. A template nothing renders IS dead and must still be
    reported — otherwise the fix trades a false positive for a false negative,
    which is the worse direction because silence reads as 'nothing found'.
    """
    repo, storage = _build(tmp_path, render=False)
    dead, out = _dead_files(repo, storage)
    assert "templates/page.html" in dead
    assert dead["templates/page.html"]["reason"] == "zero_importers"
    assert out["render_reachable_count"] == 0


def test_only_the_rendered_template_is_rescued(tmp_path):
    """Two templates, one rendered: the rescue is edge-driven, not per-extension."""
    repo, storage = _build(tmp_path, render=True, extra_template=True)
    dead, out = _dead_files(repo, storage)
    assert "templates/page.html" not in dead
    assert "templates/orphan.html" in dead, (
        "an unrendered sibling was rescued, which means the rule keyed on the "
        "file class rather than on the render edge"
    )
    assert out["render_reachable_count"] == 1


def test_render_edge_resolves_for_the_same_index(tmp_path):
    """Pins the premise the whole fix rests on: flow_edges CAN resolve this.

    If this fails, the disagreement #461 describes no longer exists and the
    reachability pass above is answering a question nobody is asking.
    """
    repo, storage = _build(tmp_path, render=True)
    owner, name = repo.split("/", 1)
    index = IndexStore(base_path=storage).load_index(owner, name)
    assert _resolve_template(index, "page.html") == "templates/page.html"


def test_analysis_note_names_render_reachability(tmp_path):
    """The two reachability kinds must stay tellable apart in output.

    ⚠ Asserts the COUNT and the fact a note exists, not its exact wording —
    binding a test to prose makes editing the message expensive and pins
    spelling rather than effect.
    """
    repo, storage = _build(tmp_path, render=True)
    out = find_dead_code(repo, granularity="file", storage_path=storage)
    assert out["render_reachable_count"] == 1
    assert any("render" in note.lower() for note in out["analysis_notes"])


def test_repo_without_templates_is_unaffected(tmp_path):
    """Control: the pass must not perturb a repo it has nothing to say about."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text("def home():\n    return 1\n", encoding="utf-8")
    (src / "orphan.py").write_text("def unused():\n    return 2\n", encoding="utf-8")
    store_dir = tmp_path / "store"
    store_dir.mkdir()
    r = index_folder(str(src), use_ai_summaries=False, storage_path=str(store_dir))
    assert r["success"], r
    out = find_dead_code(r["repo"], granularity="file", storage_path=str(store_dir))
    assert out["render_reachable_count"] == 0
    # orphan.py is genuinely dead and must still be reported.
    assert any(d["file"] == "orphan.py" for d in out["dead_files"])
