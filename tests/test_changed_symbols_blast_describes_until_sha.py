"""`get_changed_symbols`' embedded blast describes `until_sha`, not the index's revision (#878).

Found in review of #718's fix. The importer graph is the INDEX's. #718 refused an
EMPTY blast from a graph at another revision (`graph_not_at_until_sha`) and left
a non-empty one alone, on the premise that a found importer is positive
evidence. That premise holds only for a graph at `until_sha`: an index built at
a LATER commit lists importers that did not exist yet, and one built at an
EARLIER commit lists importers the diff deleted.

⚠ Two spellings of the same gap need two remedies. An importer ABSENT at
`until_sha` is provably wrong and is dropped, with its transitive importers
reached only through it. An importer that exists at `until_sha` but gained its
import later cannot be told apart by existence, so the list carries
`blast_graph_sha`, naming the revision it describes.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from jcodemunch_mcp.tools.get_changed_symbols import get_changed_symbols
from jcodemunch_mcp.tools.index_folder import index_folder

ENGINE = "def run():\n    return 0\n"
ENGINE_CHANGED = "def run():\n    return 1\n"
CALLER = "from app.engine import run\n\ndef call():\n    return run()\n"
TOP = "from app.caller import call\n\ndef top():\n    return call()\n"


def _git(args: list[str], cwd: Path) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        stdin=subprocess.DEVNULL,
    ).stdout.strip()


def _write(root: Path, files: dict[str, str | None]) -> None:
    for rel, text in files.items():
        p = root / rel
        if text is None:
            p.unlink()
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


def _commit(root: Path, files: dict[str, str | None]) -> str:
    _write(root, files)
    _git(["add", "-A"], root)
    _git(["commit", "-qm", "c"], root)
    return _git(["rev-parse", "HEAD"], root)


def _init(root: Path, files: dict[str, str]) -> str:
    root.mkdir(parents=True, exist_ok=True)
    _git(["init", "-q"], root)
    _git(["config", "user.email", "t@t"], root)
    _git(["config", "user.name", "t"], root)
    return _commit(root, files)


def _index(root: Path) -> tuple[str, str]:
    # Outside the repo: a store inside it is committed by `git add -A` and
    # turns a deleted importer into a rename.
    storage = str(root.parent / "store")
    res = index_folder(
        str(root), use_ai_summaries=False, storage_path=storage, identity_mode="local"
    )
    return res["repo"], storage


def _run_entry(result: dict) -> dict:
    assert "error" not in result, result
    entries = {
        e["name"]: e
        for e in result["added_symbols"]
        + result["removed_symbols"]
        + result["changed_symbols"]
    }
    return entries["run"]


def test_the_reported_shape_an_importer_added_after_until_sha_is_not_listed(tmp_path):
    """Index at c2, since c0, until c1: `app/caller.py` exists only from c2."""
    root = tmp_path / "r"
    c0 = _init(root, {"app/__init__.py": "", "app/engine.py": ENGINE})
    c1 = _commit(root, {"app/engine.py": ENGINE_CHANGED})
    c2 = _commit(root, {"app/caller.py": CALLER})
    repo, storage = _index(root)

    result = get_changed_symbols(
        repo,
        since_sha=c0,
        until_sha=c1,
        include_blast_radius=True,
        storage_path=storage,
    )
    run = _run_entry(result)
    assert "app/caller.py" not in run["blast_radius"], (
        "an importer that does not exist at until_sha was listed as downstream impact"
    )
    assert result["blast_dropped_absent_at_until"] == {
        "app/engine.py": ["app/caller.py"]
    }
    # With the only importer gone, the blast is empty and the graph is from
    # another revision: the #718 refusal applies, never `absent`.
    assert run["blast_radius"] == []
    assert run["blast_verdict"] == {
        "state": "degraded",
        "absence_refused": True,
        "reason": "graph_not_at_until_sha",
    }
    assert c2  # the index's revision


def test_a_transitive_importer_reached_only_through_a_dropped_one_is_dropped_too(
    tmp_path,
):
    root = tmp_path / "r"
    c0 = _init(root, {"app/__init__.py": "", "app/engine.py": ENGINE})
    c1 = _commit(root, {"app/engine.py": ENGINE_CHANGED})
    _commit(root, {"app/caller.py": CALLER, "app/top.py": TOP})
    repo, storage = _index(root)

    result = get_changed_symbols(
        repo,
        since_sha=c0,
        until_sha=c1,
        include_blast_radius=True,
        max_blast_depth=3,
        storage_path=storage,
    )
    run = _run_entry(result)
    assert run["blast_radius"] == []
    assert sorted(result["blast_dropped_absent_at_until"]["app/engine.py"]) == [
        "app/caller.py",
        "app/top.py",
    ]


def test_an_importer_that_exists_at_until_sha_names_the_graph_it_came_from(tmp_path):
    """Existence cannot catch an import added later, so the list says whose it is."""
    root = tmp_path / "r"
    c0 = _init(
        root,
        {
            "app/__init__.py": "",
            "app/engine.py": ENGINE,
            "app/caller.py": "def call():\n    return 0\n",
        },
    )
    c1 = _commit(root, {"app/engine.py": ENGINE_CHANGED})
    c2 = _commit(root, {"app/caller.py": CALLER})
    repo, storage = _index(root)

    result = get_changed_symbols(
        repo,
        since_sha=c0,
        until_sha=c1,
        include_blast_radius=True,
        storage_path=storage,
    )
    run = _run_entry(result)
    assert run["blast_radius"] == ["app/caller.py"], (
        "precondition: it exists at until_sha, so it is kept"
    )
    assert run.get("blast_graph_sha") == c2[:12], (
        "a blast from a graph at another revision does not say which revision it describes"
    )
    assert "blast_dropped_absent_at_until" not in result


def test_an_older_graph_does_not_list_an_importer_the_diff_deleted(tmp_path):
    """The same gap from the other side: the default mode's graph predates until_sha."""
    root = tmp_path / "r"
    c0 = _init(
        root, {"app/__init__.py": "", "app/engine.py": ENGINE, "app/caller.py": CALLER}
    )
    repo, storage = _index(root)
    _commit(root, {"app/engine.py": ENGINE_CHANGED, "app/caller.py": None})

    result = get_changed_symbols(
        repo, since_sha=c0, include_blast_radius=True, storage_path=storage
    )
    run = _run_entry(result)
    assert "app/caller.py" not in run["blast_radius"], (
        "an importer deleted in the diff was listed"
    )
    assert result["blast_dropped_absent_at_until"] == {
        "app/engine.py": ["app/caller.py"]
    }


def test_controls_a_graph_at_until_sha_carries_no_revision_note(tmp_path):
    root = tmp_path / "r"
    c0 = _init(
        root, {"app/__init__.py": "", "app/engine.py": ENGINE, "app/caller.py": CALLER}
    )
    _commit(root, {"app/engine.py": ENGINE_CHANGED, "app/other.py": "X = 1\n"})
    repo, storage = _index(root)

    result = get_changed_symbols(
        repo, since_sha=c0, include_blast_radius=True, storage_path=storage
    )
    run = _run_entry(result)
    assert run["blast_radius"] == ["app/caller.py"]
    assert "blast_graph_sha" not in run
    assert "blast_dropped_absent_at_until" not in result


def test_an_older_graph_does_not_list_an_importer_the_diff_renamed(tmp_path):
    """A rename is a deletion at the old path, which `git diff --name-only` does not name."""
    root = tmp_path / "r"
    c0 = _init(
        root, {"app/__init__.py": "", "app/engine.py": ENGINE, "app/caller.py": CALLER}
    )
    repo, storage = _index(root)
    _commit(
        root,
        {
            "app/engine.py": ENGINE_CHANGED,
            "app/caller.py": None,
            "app/renamed_caller.py": CALLER,
        },
    )

    result = get_changed_symbols(
        repo, since_sha=c0, include_blast_radius=True, storage_path=storage
    )
    run = _run_entry(result)
    assert "app/caller.py" not in run["blast_radius"]
    assert result["blast_dropped_absent_at_until"] == {
        "app/engine.py": ["app/caller.py"]
    }


def test_an_importer_with_a_non_ascii_path_that_exists_is_kept(tmp_path):
    """Review: `core.quotePath` C-quotes `café.py` unless the listing is `-z`."""
    root = tmp_path / "r"
    c0 = _init(root, {"app/__init__.py": "", "app/engine.py": ENGINE, "app/café.py": CALLER})
    repo, storage = _index(root)
    _commit(root, {"app/engine.py": ENGINE_CHANGED, "app/other.py": "X = 1\n"})

    result = get_changed_symbols(repo, since_sha=c0, include_blast_radius=True, storage_path=storage)
    run = _run_entry(result)
    assert run["blast_radius"] == ["app/café.py"], "a real importer was published as absent at until_sha"
    assert "blast_dropped_absent_at_until" not in result


def test_an_existence_check_that_could_not_run_says_so(tmp_path, monkeypatch):
    """UNKNOWN is not 'nothing dropped': an unlisted tree is disclosed."""
    from jcodemunch_mcp.tools import get_changed_symbols as mod

    root = tmp_path / "r"
    c0 = _init(root, {"app/__init__.py": "", "app/engine.py": ENGINE, "app/caller.py": CALLER})
    repo, storage = _index(root)
    _commit(root, {"app/engine.py": ENGINE_CHANGED, "app/other.py": "X = 1\n"})

    real = mod._run_git

    def _no_tree(args, cwd, timeout=10):
        if args and args[0] == "ls-tree":
            return 128, "", "fatal: not a tree object"
        return real(args, cwd, timeout)

    monkeypatch.setattr(mod, "_run_git", _no_tree)
    result = get_changed_symbols(repo, since_sha=c0, include_blast_radius=True, storage_path=storage)
    run = _run_entry(result)
    assert run["blast_radius"] == ["app/caller.py"]
    assert result["blast_existence_unchecked"] == ["app/engine.py"]
    assert "blast_dropped_absent_at_until" not in result


def test_an_index_rooted_below_the_git_top_level_drops_by_index_paths(tmp_path):
    """#685's spelling: the existence check reads index-root-relative paths."""
    top = tmp_path / "r"
    c0 = _init(
        top, {"README": "x\n", "svc/app/__init__.py": "", "svc/app/engine.py": ENGINE}
    )
    c1 = _commit(top, {"svc/app/engine.py": ENGINE_CHANGED})
    _commit(top, {"svc/app/caller.py": CALLER})
    repo, storage = _index(top / "svc")

    result = get_changed_symbols(
        repo,
        since_sha=c0,
        until_sha=c1,
        include_blast_radius=True,
        storage_path=storage,
    )
    run = _run_entry(result)
    assert run["blast_radius"] == []
    assert result["blast_dropped_absent_at_until"] == {
        "app/engine.py": ["app/caller.py"]
    }
