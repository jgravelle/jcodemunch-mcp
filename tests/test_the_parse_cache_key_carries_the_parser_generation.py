"""The shared parse cache never serves what an older parser emitted.

The cache (opt-in, `JCODEMUNCH_PARSE_CACHE`) is keyed by content, path and
language, so a hit is only correct while the parser that wrote the entry is the
parser running now. The key carried `INDEX_VERSION` and, for Racket only, the
reader generation. It did not carry `PARSER_GENERATION`, the number a release
bumps when the extractor's output changes for unchanged bytes. So on a host
with the cache set, the re-index a generation bump forces read every file back
from the cache, kept the old extractor's symbols, and stamped the index with
the new generation, which marks the index as already upgraded. A grammar
package upgrade inside its allowed range changes output the same way and was
not in the key either.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

PY = "def alpha(x):\n    return x + 1\n"
MARK = " # new-extractor"


def _bump_generation(monkeypatch) -> int:
    """Raise PARSER_GENERATION for every reader of it, the way a release does."""
    from jcodemunch_mcp.storage import index_store, sqlite_store
    from jcodemunch_mcp.tools import index_folder as mod

    sqlite_store._ensure_index_store_deps()
    bumped = int(index_store.PARSER_GENERATION) + 1
    monkeypatch.setattr(index_store, "PARSER_GENERATION", bumped)
    monkeypatch.setattr(sqlite_store, "_PARSER_GENERATION", bumped)
    monkeypatch.setattr(mod, "PARSER_GENERATION", bumped)
    return bumped


def _new_extractor(monkeypatch) -> None:
    """The release's extractor: same bytes in, different symbols out."""
    import jcodemunch_mcp.parser.extractor as extractor

    real = extractor.parse_file

    def changed(*a, **k):
        out = real(*a, **k)
        return [dataclasses.replace(s, signature=(s.signature or "") + MARK) for s in out]

    monkeypatch.setattr(extractor, "parse_file", changed)


def test_a_generation_bump_misses_the_cache(tmp_path, monkeypatch):
    from jcodemunch_mcp.parser.parse_cache import cached_parse_file

    monkeypatch.setenv("JCODEMUNCH_PARSE_CACHE", str(tmp_path / "pc"))
    old = cached_parse_file(PY, "m.py", "python")
    assert old and not any(MARK in (s.signature or "") for s in old)

    _bump_generation(monkeypatch)
    _new_extractor(monkeypatch)
    new = cached_parse_file(PY, "m.py", "python")
    assert new and all(MARK in (s.signature or "") for s in new), (
        "the cache served the old extractor's symbols after a generation bump"
    )


@pytest.mark.parametrize(
    "dist", ["tree-sitter", "tree-sitter-language-pack", "tree-sitter-fsharp"]
)
def test_a_grammar_upgrade_misses_the_cache(tmp_path, monkeypatch, dist):
    """A grammar package can change output for identical bytes inside its
    allowed range, so its installed version is part of what wrote the entry."""
    import jcodemunch_mcp.parser.parse_cache as pc

    monkeypatch.setenv("JCODEMUNCH_PARSE_CACHE", str(tmp_path / "pc"))
    before = pc._key(PY, "m.py", "python")

    real = pc._installed_version

    def upgraded(name):
        v = real(name)
        return (v or "0") + ".post1" if name == dist else v

    monkeypatch.setattr(pc, "_installed_version", upgraded)
    pc._grammar_identity.cache_clear()
    try:
        assert pc._key(PY, "m.py", "python") != before
    finally:
        pc._grammar_identity.cache_clear()


def test_an_upgrade_reindex_stores_the_new_extractors_symbols(tmp_path, monkeypatch):
    """The user's entry point: the re-index a generation bump forces."""
    from jcodemunch_mcp.storage import IndexStore
    from jcodemunch_mcp.tools import index_folder as mod

    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path / ".idx"))
    monkeypatch.setenv("JCODEMUNCH_USE_AI_SUMMARIES", "false")
    monkeypatch.setenv("JCODEMUNCH_PARSE_CACHE", str(tmp_path / "pc"))
    root = tmp_path / "repo"
    root.mkdir()
    (root / "m.py").write_text(PY, encoding="utf-8")

    first = mod.index_folder(str(root), use_ai_summaries=False)
    owner, name = first["repo"].split("/", 1)

    bumped = _bump_generation(monkeypatch)
    _new_extractor(monkeypatch)
    res = mod.index_folder(str(root), use_ai_summaries=False)
    assert mod.PARSER_UPGRADE_WARNING in res.get("warnings", []), (
        "precondition: the bump forced the upgrade re-index"
    )

    index = IndexStore().load_index(owner, name)
    assert getattr(index, "parser_generation", None) == bumped
    sigs = [s.get("signature") or "" for s in index.symbols]
    assert sigs and all(MARK in s for s in sigs), (
        "the upgrade re-index stored the old extractor's symbols from the "
        f"parse cache and stamped generation {bumped} over them: {sigs}"
    )


def test_every_generation_constant_is_in_the_key():
    """A module-level `*_GENERATION` under `parser/` or `storage/index_store.py`
    names a change in what the parser emits. Each must reach the cache key, or
    the next one repeats this defect."""
    import ast

    import jcodemunch_mcp

    src = Path(jcodemunch_mcp.__file__).parent
    files = sorted((src / "parser").glob("*.py")) + [src / "storage" / "index_store.py"]
    names = set()
    for f in files:
        for node in ast.parse(f.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id.endswith("_GENERATION"):
                        names.add(t.id)
    assert names, "found no generation constants; the scan is broken"
    # Names the code uses, never its text: the module's own comment named
    # PARSER_GENERATION while the key left it out, so a substring scan passed
    # against the defect.
    used = set()
    for node in ast.walk(ast.parse((src / "parser" / "parse_cache.py").read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            used.update(a.name for a in node.names)
        elif isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            used.add(node.attr)
    missing = sorted(n for n in names if n not in used)
    assert missing == [], f"generation constants missing from the parse-cache key: {missing}"
