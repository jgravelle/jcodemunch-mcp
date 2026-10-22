"""Every `index_folder` knob the watcher names must reach `index_folder`.

⚠⚠ `context_providers` existed on `index_folder` and had **no route from the
watcher at any layer** — no CLI flag, and not a parameter of `watch_folders`,
`WatcherManager`, `_watch_single` or `_initial_index`. Its three neighbours
(`use_ai_summaries`, `follow_symlinks`, `extra_ignore_patterns`) were threaded
end to end, so nothing looked wrong at any single site (#558, surfaced by
@Ticki84 in #557 when they tried to hold it fixed across a comparison and
could not).

⚠ These are PROPERTIES over the signatures, deliberately not a list of four
names. A hand-written roster would have had the same hole the next time a knob
is added, which is the whole reason the gap survived — see
[[a-set-cannot-count]] for the sibling shape.
"""

from __future__ import annotations

import ast
import inspect
import pathlib

import pytest

from jcodemunch_mcp import watch_all as watch_all_mod
from jcodemunch_mcp import watcher as w
from jcodemunch_mcp.tools.index_folder import index_folder

# The chain a knob must survive, outermost first.
_CHAIN = {
    "watch_folders": w.watch_folders,
    "sync_folders": w.sync_folders,
    "watch_claude_worktrees": w.watch_claude_worktrees,
    "WatcherManager.__init__": w.WatcherManager.__init__,
    "_watch_single": w._watch_single,
    "_initial_index": w._initial_index,
    "watch_all": watch_all_mod.watch_all,
}

# ⚠⚠ `paths` is a NAME COLLISION, not a knob. `index_folder(paths=...)` is an
# explicit file list; `watch_folders(paths=...)` is the folders to watch. They
# share a spelling and nothing else, and left in, the parity check fails for
# every layer forever — a permanently red guard is a deleted guard.
_NOT_A_KNOB = {"paths"}
_INDEX_FOLDER_PARAMS = set(inspect.signature(index_folder).parameters) - _NOT_A_KNOB


def _knobs(fn) -> set[str]:
    """The parameters this layer shares with `index_folder`."""
    return set(inspect.signature(fn).parameters) & _INDEX_FOLDER_PARAMS


def test_every_behaviour_knob_reaches_the_watcher():
    """⚠⚠ THE DEFECT ITSELF, and the layer-parity test below cannot see it.

    Parity across layers only catches a knob that stops PART WAY. `context_providers`
    was missing from every layer at once, so the shared set was simply smaller
    and nothing looked uneven — six of the seven tests in this file passed
    against the broken tree. The property has to be anchored to what
    `index_folder` OFFERS, not to what the watcher happens to name.

    ⚠ The exclusions are per-parameter and each states why, so adding one is a
    decision someone has to write down rather than a set that quietly grows.
    """
    offered = set(inspect.signature(index_folder).parameters)
    not_the_watchers_to_set = {
        "path": "the watcher supplies the folder it is watching",
        "paths": "explicit file list; the watcher passes changed_paths instead",
        "changed_paths": "the watcher COMPUTES this from filesystem events",
        "incremental": "a watcher reindex is incremental by definition",
        "progress_cb": "an in-process callback, not a user-facing setting",
        "force_reparse": "belongs to `refresh`, which is a different command",
        "identity_mode": "fixed at index time; changing it mid-watch would "
                         "repoint the watcher at a different repo id",
        "max_size": "resolved from config/env per repo, with no per-run meaning",
        "paths_honor_gitignore": "applies only to `paths=`, which the watcher "
                                 "never passes; index_dependency alone sets it, "
                                 "for its own pre-filtered snapshot",
    }
    expected = offered - set(not_the_watchers_to_set)
    reachable = _knobs(_CHAIN["watch_folders"]) | {"paths"}
    missing = sorted(expected - reachable)
    assert not missing, (
        f"`index_folder` offers {missing} and the watcher cannot set them. "
        f"Either thread the knob through (see `use_ai_summaries` for the "
        f"shape) or add it to `not_the_watchers_to_set` with a reason."
    )


def test_no_knob_stops_part_way():
    """The other half: a knob present on some layers and absent on others.

    A knob that reaches layer 2 and stops is worse than one that was never
    added — the CLI accepts it, nothing errors, and the value is dropped in
    silence.
    """
    per_layer = {name: _knobs(fn) for name, fn in _CHAIN.items()}
    union = set.union(*per_layer.values())
    gaps = {n: sorted(union - ks) for n, ks in per_layer.items() if ks != union}
    assert not gaps, f"knobs missing on some layers only: {gaps}"


def _forwarded_kwargs(fn, callee: str) -> set[str]:
    """Keyword names passed to `callee` anywhere in `fn`'s body (AST, not text)."""
    src = inspect.getsource(fn)
    tree = ast.parse(src.lstrip() if src.startswith((" ", "\t")) else src)
    out: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        target = getattr(f, "id", None) or getattr(f, "attr", None)
        args = list(node.args)
        # asyncio.to_thread(index_folder, ...) passes the callee positionally
        if target in {"to_thread", "run_in_executor"} and args:
            first = args[0]
            target = getattr(first, "id", None) or getattr(first, "attr", None)
        if target != callee:
            continue
        out |= {kw.arg for kw in node.keywords if kw.arg}
    return out


@pytest.mark.parametrize("layer", ["_watch_single", "_initial_index"])
def test_knobs_reach_index_folder(layer):
    """⚠ Declaring the parameter is half the job; the value has to arrive.

    Read off the call's keywords rather than searched for as text, so a knob
    that is accepted and then dropped fails here instead of passing on the
    strength of appearing somewhere in the file.
    """
    fn = _CHAIN[layer]
    declared = _knobs(fn)
    forwarded = _forwarded_kwargs(fn, "index_folder") | _forwarded_kwargs(fn, "_initial_index")
    missing = sorted(declared - forwarded)
    assert not missing, (
        f"{layer} accepts {missing} and never passes them on; the caller's "
        f"choice is discarded silently"
    )


def test_context_providers_is_one_of_them():
    """A non-vacuity anchor.

    ⚠ Without this, deleting the knob from every layer at once would make the
    parity test above pass by making the shared set smaller. The property is
    "no gaps", which an empty set satisfies.
    """
    assert "context_providers" in _INDEX_FOLDER_PARAMS
    for name, fn in _CHAIN.items():
        assert "context_providers" in _knobs(fn), f"{name} lost the knob"


_WATCH_COMMANDS = ("watch", "watch-all", "watch-claude")


@pytest.mark.parametrize("command", _WATCH_COMMANDS)
def test_watch_commands_expose_a_flag_for_each_boolean_knob(command):
    """The CLI half. A knob reachable only from a config file is not reachable
    for an A/B against the same repo, which is what #557 needed.

    ⚠ Flag names are DERIVED, but loosely: the existing flag for
    `use_ai_summaries` is `--no-ai-summaries`, not `--no-use-ai-summaries`, so
    any `--no-` + trailing segment of the knob counts. Pinning one spelling
    would have failed against a flag that has shipped for a year, which is the
    test being wrong rather than the code.
    """
    src = pathlib.Path(w.__file__).parent.joinpath("server.py").read_text(encoding="utf-8")
    sig = inspect.signature(_CHAIN["watch_folders"]).parameters
    booleans = [
        n for n in _knobs(_CHAIN["watch_folders"])
        if isinstance(sig[n].default, bool) and sig[n].default is True
    ]
    assert booleans, "expected at least one opt-out boolean knob"
    for knob in booleans:
        parts = knob.split("_")
        candidates = ["--no-" + "-".join(parts[i:]) for i in range(len(parts))]
        assert any(f'"{c}"' in src for c in candidates), (
            f"{command}: no opt-out flag for the `{knob}` knob (tried "
            f"{candidates}). A knob settable only in .jcodemunch.jsonc cannot "
            f"be varied per invocation, which is what #557 needed."
        )
