"""#490: the lexical cache must never advertise readiness before it is ready.

The BM25 corpus cache is built behind a check-then-build guarded on ``idf``.
Three call sites published FOUR keys behind that one sentinel: the tuple unpack
that wrote the first three is three separate ``__setitem__`` calls, followed by
a fourth statement for ``centrality``. A caller arriving after ``idf`` appeared
and before ``centrality`` did passed the readiness check and raised
``KeyError: 'centrality'``.

The window is the entire runtime of ``_compute_centrality`` over the corpus,
which on a large repository is not narrow.
"""

import importlib
import pathlib
import re
import threading
import time

import pytest

from jcodemunch_mcp.tools import search_symbols as ss
from jcodemunch_mcp.tools.index_folder import index_folder
from jcodemunch_mcp.tools.search_symbols import search_symbols

# The keys a reader of this cache goes on to read once it believes it is ready.
CORPUS_KEYS = ("idf", "avgdl", "inverted", "centrality")

# Every module that built the corpus inline before #490.
CALL_SITES = (
    "jcodemunch_mcp.tools.search_symbols",
    "jcodemunch_mcp.tools.get_ranked_context",
    "jcodemunch_mcp.tools.plan_turn",
)


@pytest.fixture
def indexed(tmp_path):
    """A tiny real repo plus its store. Returns (repo_id, store_path)."""
    src = tmp_path / "src"
    src.mkdir()
    store = tmp_path / "store"
    store.mkdir()
    (src / "observer.py").write_text(
        "class _NativeObserverOwner:\n    pass\n\n\n"
        "class _ObserverLaunchContainment:\n    pass\n\n\n"
        "def collect_async_io_observation():\n    return None\n"
    )
    (src / "other.py").write_text("import observer\n\n\ndef use():\n    return 1\n")
    result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert result["success"] is True
    return result["repo"], str(store)


def _load(repo_id, store):
    from jcodemunch_mcp.tools._utils import load_repo_index_or_error

    index, error, _status = load_repo_index_or_error(repo_id, store)
    assert error is None, error
    return index


class TestReadinessIsNeverAdvertisedEarly:
    """The defect, stated as an invariant rather than as a stack trace."""

    def test_no_sentinel_is_visible_before_the_keys_a_reader_will_need(
        self, indexed, monkeypatch
    ):
        """Observe the cache from INSIDE the build.

        No threads needed: the pre-fix code had already published ``idf`` by the
        time ``_compute_centrality`` ran, so a single-threaded probe at that
        moment sees exactly the partial state a second caller would.
        """
        repo_id, store = indexed
        index = _load(repo_id, store)
        observations = []
        real = ss._compute_centrality

        def probing(*args, **kwargs):
            observations.append({k: k in index._bm25_cache for k in CORPUS_KEYS})
            return real(*args, **kwargs)

        monkeypatch.setattr(ss, "_compute_centrality", probing)
        ss.ensure_bm25_cache(index)

        assert observations, "the build never ran; the probe proves nothing"
        for seen in observations:
            if not seen["idf"]:
                continue
            missing = [k for k in CORPUS_KEYS if not seen[k]]
            assert not missing, (
                f"cache advertised ready (idf present) while {missing} were "
                "absent - a second caller reads those and raises KeyError"
            )

    def test_the_sentinel_is_written_last(self, indexed):
        """Pin the write ORDER, not just the end state.

        The end state is identical in the fixed and the broken version; only the
        order differs, so an end-state assertion would pass on both sides.
        """
        repo_id, store = indexed
        index = _load(repo_id, store)
        written = []

        class RecordingDict(dict):
            def __setitem__(self, key, value):
                written.append(key)
                super().__setitem__(key, value)

        index._bm25_cache = RecordingDict()
        ss.ensure_bm25_cache(index)

        corpus_writes = [k for k in written if k in CORPUS_KEYS]
        assert set(corpus_writes) == set(CORPUS_KEYS), corpus_writes
        assert corpus_writes[-1] == "idf", (
            f"idf must be published last; write order was {corpus_writes}"
        )


class TestShippedPath:
    """A unit invariant is not a user-visible outcome. This is the outcome."""

    def test_concurrent_cold_searches_both_return_results(self, indexed, monkeypatch):
        """⚠ The hold below is load-bearing, and the first version of this test
        did not have it: signalling from inside the build and letting the second
        caller race was not enough on a two-file corpus, so it passed against
        the BROKEN source too. A is held open until B has entered its call and
        had time to reach the cache. Pre-fix B reads a missing ``centrality``
        in that window; post-fix B waits on the lock and then succeeds.
        """
        repo_id, store = indexed
        builder_inside = threading.Event()
        second_caller_started = threading.Event()
        real = ss._compute_centrality

        def slow(*args, **kwargs):
            builder_inside.set()
            # Keep the window open until the second caller is inside its call,
            # plus enough slack for it to perform two dict lookups.
            second_caller_started.wait(timeout=30)
            time.sleep(0.5)
            return real(*args, **kwargs)

        monkeypatch.setattr(ss, "_compute_centrality", slow)
        out = {}

        def call(tag, query, wait_first):
            if wait_first:
                assert builder_inside.wait(timeout=30)
                second_caller_started.set()
            try:
                response = search_symbols(
                    repo=repo_id,
                    query=query,
                    detail_level="compact",
                    max_results=10,
                    storage_path=store,
                )
                rows = response.get("symbols") or response.get("results") or []
                out[tag] = ("ok", len(rows))
            except BaseException as exc:  # noqa: BLE001 - the failure IS the finding
                out[tag] = ("raised", f"{type(exc).__name__}: {exc}")

        a = threading.Thread(
            target=call, args=("A", "_ObserverLaunchContainment", False), name="A"
        )
        b = threading.Thread(
            target=call, args=("B", "_NativeObserverOwner", True), name="B"
        )
        a.start()
        b.start()
        a.join(timeout=60)
        b.join(timeout=60)

        assert out.get("A", ("missing",))[0] == "ok", out
        assert out.get("B", ("missing",))[0] == "ok", out

    def test_single_flight_is_preserved(self, indexed, monkeypatch):
        """#370's guarantee must survive #490's fix: the corpus builds ONCE.

        Control for the obvious wrong fix. Deleting the check-then-build and
        letting every caller rebuild would satisfy every assertion above.
        """
        repo_id, store = indexed
        index = _load(repo_id, store)
        calls = []
        real = ss._compute_bm25
        gate = threading.Barrier(4, timeout=30)

        def counting(*args, **kwargs):
            calls.append(1)
            return real(*args, **kwargs)

        monkeypatch.setattr(ss, "_compute_bm25", counting)

        def worker():
            gate.wait()
            ss.ensure_bm25_cache(index)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        assert len(calls) == 1, f"corpus built {len(calls)} times, expected once"


class TestEveryCallSiteShares:
    """The same four-key publish lived in THREE modules, so a fix in one is a
    fix in one. This is the ratchet that stops a fourth appearing."""

    def test_no_module_publishes_the_sentinel_outside_the_helper(self):
        root = pathlib.Path(ss.__file__).resolve().parent.parent
        helper_file = pathlib.Path(ss.__file__).resolve()
        # A WRITE to the sentinel: `cache["idf"] =` or `cache["idf"], ... =`.
        write = re.compile(r'cache\["idf"\]\s*(,|=[^=])')
        offenders = []
        for path in sorted(root.rglob("*.py")):
            if path.resolve() == helper_file:
                continue  # the helper itself is the one legitimate writer
            text = path.read_text(encoding="utf-8", errors="replace")
            for lineno, line in enumerate(text.splitlines(), 1):
                if write.search(line):
                    offenders.append(f"{path.relative_to(root)}:{lineno}")
        assert not offenders, (
            "these publish the readiness sentinel outside ensure_bm25_cache: "
            + ", ".join(offenders)
        )

    @pytest.mark.parametrize("module_name", CALL_SITES)
    def test_the_known_call_sites_reach_the_helper(self, module_name):
        mod = importlib.import_module(module_name)
        assert hasattr(mod, "ensure_bm25_cache"), (
            f"{module_name} built the corpus inline before #490; it must now go "
            "through the shared helper"
        )
