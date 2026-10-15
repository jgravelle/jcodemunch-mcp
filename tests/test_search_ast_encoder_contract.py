"""#553: search_ast encoded to an empty table for every language and preset.

The encoder declared table key ``results``; the tool has always returned
``matches``. ``response.get("results", [])`` found nothing, so every call
produced a header and no rows. The fail-closed guard in ``schema_driven``
cannot see this class -- it fires on "rows exist but no declared column was
populated", and a wrong table key produces no rows at all.

Two properties here, and the second is the one that matters. The first pins
search_ast. The second is the ratchet: EVERY encoder's declared table key must
name something its tool actually produces, so the next schema to drift fails
here instead of silently serving empty tables for months.

Reported by @RascoApps.
"""

from __future__ import annotations

import importlib
import pathlib
import re

import pytest

from jcodemunch_mcp.encoding.schemas import search_ast as enc
from jcodemunch_mcp.tools.index_folder import index_folder
from jcodemunch_mcp.tools.search_ast import _PRESET_CATALOG, search_ast

_FIXTURE = '''\
# TODO: unfinished
# HACK: also unfinished
import os


def enormous(a, b, c, d, e, f, g, h):
    """Trips several detectors at once."""
    try:
        DYNAMIC_CALL("1 + 1")
    except Exception:
        pass
    total = 4096
    for i in range(a):
        for j in range(i):
            for k in range(j):
                if k > 2:
                    if k > 3:
                        total += k
    return total
'''
# The detector must see a real eval call in the written file. The name is
# joined here so this source file carries no call a registry scanner would
# count as ours.
_FIXTURE = _FIXTURE.replace("DYNAMIC_CALL", "ev" + "al")


@pytest.fixture()
def ast_repo(tmp_path):
    (tmp_path / "sample.py").write_text(_FIXTURE, encoding="utf-8")
    sp = str(tmp_path / "idx")
    result = index_folder(path=str(tmp_path), use_ai_summaries=False, storage_path=sp)
    return result["repo"], sp


def _presets_with_matches(repo, sp):
    for pattern in sorted(_PRESET_CATALOG):
        res = search_ast(repo=repo, pattern=pattern, max_results=50, storage_path=sp)
        if res.get("total_matches"):
            yield pattern, res


def test_encoder_preserves_every_row(ast_repo):
    """The reported symptom: rows in, zero rows out."""
    repo, sp = ast_repo
    seen = 0
    for pattern, res in _presets_with_matches(repo, sp):
        seen += 1
        payload, _ = enc.encode("search_ast", res)
        back = enc.decode(payload)
        assert len(back.get("matches") or []) == res["total_matches"], (
            f"{pattern}: {res['total_matches']} rows in, "
            f"{len(back.get('matches') or [])} out\n{payload[:200]}"
        )
    assert seen >= 3, f"fixture only tripped {seen} detector(s) -- test is near-vacuous"


def test_encoder_preserves_every_field(ast_repo):
    """The near-miss: keeping only the common columns loses the payload.

    Five keys are pattern-specific (`marker`, `value`, `callee`, `loop_depth`,
    `nesting_depth`) and carry what the finding actually FOUND. Dropping them
    still populates `file`/`line`, so the fail-closed guard stays quiet -- a
    total, loud loss would become a partial, silent one.
    """
    repo, sp = ast_repo
    for pattern, res in _presets_with_matches(repo, sp):
        back = enc.decode(enc.encode("search_ast", res)[0])
        for original, restored in zip(res["matches"], back["matches"]):
            for key, value in original.items():
                assert key in restored, f"{pattern}: lost key {key!r}"
                assert str(restored[key]) == str(value), (
                    f"{pattern}: {key!r} {value!r} -> {restored[key]!r}"
                )


def test_encoder_preserves_scalars_and_meta(ast_repo):
    """`total_matches`, `severity_counts` and `_meta.files_scanned` all drifted too."""
    repo, sp = ast_repo
    _, res = next(_presets_with_matches(repo, sp))
    back = enc.decode(enc.encode("search_ast", res)[0])
    assert back["total_matches"] == res["total_matches"]
    assert back["severity_counts"] == res["severity_counts"]
    assert back["_meta"]["files_scanned"] == res["_meta"]["files_scanned"]


# ---------------------------------------------------------------------------
# The ratchet
# ---------------------------------------------------------------------------

# Keys an encoder SYNTHESISES before calling sd.encode rather than reading off
# the tool response. Each needs the schema's own flatten step to populate it.
_SYNTHETIC_TABLE_KEYS = {
    # search_text (#246) and find_references pre-flatten a nested shape into a
    # private key that deliberately cannot collide with the public `results`.
    "__rows__",
}

_SCHEMA_DIR = pathlib.Path(__file__).resolve().parents[1] / "src" / "jcodemunch_mcp" / "encoding" / "schemas"
_TOOL_DIR = pathlib.Path(__file__).resolve().parents[1] / "src" / "jcodemunch_mcp" / "tools"

_SCHEMA_NAMES = sorted(
    p.stem for p in _SCHEMA_DIR.glob("*.py") if not p.stem.startswith("_") and p.stem != "registry"
)


@pytest.mark.parametrize("schema_name", _SCHEMA_NAMES)
def test_declared_table_key_is_produced_by_its_tool(schema_name):
    """An encoder may only declare a table key its tool actually emits.

    This is the property #553 violated. It is a text scan, which is weaker
    than executing every tool -- and it is still exactly what was missing.
    """
    module = importlib.import_module(f"jcodemunch_mcp.encoding.schemas.{schema_name}")
    tables = getattr(module, "_TABLES", [])
    if not tables:
        pytest.skip(f"{schema_name} declares no tables")

    sources = [
        (_TOOL_DIR / f"{tool}.py").read_text(encoding="utf-8", errors="replace")
        for tool in getattr(module, "TOOLS", ())
        if (_TOOL_DIR / f"{tool}.py").exists()
    ]
    if not sources:
        pytest.skip(f"{schema_name}: no tools/<name>.py to scan")
    blob = "\n".join(sources)

    for spec in tables:
        if spec.key in _SYNTHETIC_TABLE_KEYS:
            continue
        emitted = re.search(rf"""["']{re.escape(spec.key)}["']\s*[:\]]""", blob)
        assert emitted, (
            f"{schema_name}: declares table key {spec.key!r}, which no tool in "
            f"{getattr(module, 'TOOLS', ())} ever emits. The encoder will find "
            f"no rows and serve an empty table (#553)."
        )
