"""``get_file_risk`` — per-symbol composite risk for a single file.

Powers the v1.89.0 VS Code risk-density gutter. For each function or
method in the file, returns a 0–100 composite risk score (higher = healthier;
lower = riskier) plus per-axis sub-scores. Same scoring posture as
``health_radar``: linear penalties, conservative calibration, simple
formulas.

Four axes per symbol:

| Axis        | Source                                             |
|-------------|----------------------------------------------------|
| complexity  | cyclomatic from the index                          |
| exposure    | file-level fan-in (importers of the file)          |
| churn       | file-level commit count, last 30 days              |
| test_gap    | file-level: does any test file import this module? |

The first axis is per-symbol; the other three are file-level (shared
across every symbol in the file). That's an honest reflection of what
data the index provides at file granularity — caller-count *per symbol*
needs `find_references` per call, which is too slow to drive a save-time
gutter refresh.
"""

from __future__ import annotations

import logging
import subprocess
import time
from typing import Optional

from ..storage import IndexStore
from ._utils import index_status_to_tool_error, resolve_repo
from ._test_paths import is_test_file

logger = logging.getLogger(__name__)


_RISK_LEVEL_THRESHOLDS = (
    (85.0, "green"),
    (70.0, "yellow"),
    (55.0, "orange"),
    (0.0, "red"),
)


def _level_for(composite: float) -> str:
    for threshold, level in _RISK_LEVEL_THRESHOLDS:
        if composite >= threshold:
            return level
    return "red"


def _score_complexity(cyclomatic: int) -> float:
    """Same posture as health_radar: cy <= 3 -> 100; linear penalty after."""
    if cyclomatic <= 3:
        return 100.0
    return max(0.0, min(100.0, 100.0 - 6.0 * (cyclomatic - 3)))


def _score_exposure(incoming_count: int) -> float:
    """High fan-in = riskier to change. 0 importers -> 100 (private code,
    safe to refactor); 5 importers -> 75; 20+ -> 0."""
    if incoming_count <= 0:
        return 100.0
    return max(0.0, min(100.0, 100.0 - 5.0 * incoming_count))


def _score_churn(commits_30d: int) -> float:
    """0 commits -> 100; 5 -> 75; 10 -> 50; 20+ -> 0."""
    return max(0.0, min(100.0, 100.0 - 5.0 * commits_30d))


def _score_test_gap(has_tests: bool) -> float:
    """File-level binary: any test file imports this module? Yes -> 100, No -> 0.
    Coarse, but the gutter's job is "draw attention to risky things," and
    'untested' is the load-bearing signal at file level."""
    return 100.0 if has_tests else 0.0


def _count_incoming(index, file_path: str) -> int:
    """How many other source files import this one?"""
    if not index.imports:
        return 0
    # Reverse the import graph: for each (src, [target, ...]), bump target's count.
    # But we only need ONE target's count, so a single pass is fine.
    count = 0
    try:
        from ..parser.imports import resolve_specifier
    except ImportError:
        return 0
    source_files = frozenset(index.source_files)
    alias_map = getattr(index, "alias_map", None)
    psr4_map = getattr(index, "psr4_map", None)
    for src, file_imports in index.imports.items():
        if src == file_path:
            continue
        for imp in file_imports:
            target = resolve_specifier(
                imp.get("specifier", ""), src, source_files, alias_map, psr4_map
            )
            if target == file_path:
                count += 1
                break  # one count per importing file, regardless of import count
    return count


def _churn_for_file(file_path: str, source_root: str, days: int = 30) -> int:
    """git log --since=Ndays -- <file> | wc -l. Returns 0 on any failure."""
    try:
        r = subprocess.run(
            ["git", "log", f"--since={days} days ago", "--oneline", "--", file_path],
            cwd=source_root,
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=5,
            stdin=subprocess.DEVNULL,
        )
        if r.returncode != 0:
            return 0
        return sum(1 for line in r.stdout.splitlines() if line.strip())
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
        return 0


def _file_has_tests(index, file_path: str) -> bool:
    """Does any test file in the index import this file?"""
    if not index.imports:
        return False
    try:
        from ..parser.imports import resolve_specifier
    except ImportError:
        return False
    source_files = frozenset(index.source_files)
    alias_map = getattr(index, "alias_map", None)
    psr4_map = getattr(index, "psr4_map", None)
    for src, file_imports in index.imports.items():
        if not is_test_file(src):
            continue
        for imp in file_imports:
            target = resolve_specifier(
                imp.get("specifier", ""), src, source_files, alias_map, psr4_map
            )
            if target == file_path:
                return True
    return False


def get_file_risk(
    repo: str,
    file_path: str,
    storage_path: Optional[str] = None,
) -> dict:
    """Return per-symbol composite risk for one file.

    Args:
        repo: Repository identifier (owner/repo, full id, or bare display name).
        file_path: Path to the file within the indexed repo.
        storage_path: Override index storage location.

    Returns:
        Dict with file, language, file_metrics, symbols (each with
        per-axis sub-scores + composite + level), _meta.
    """
    start = time.perf_counter()

    try:
        owner, name = resolve_repo(repo, storage_path)
    except ValueError as e:
        return {"error": str(e)}

    store = IndexStore(base_path=storage_path)
    index = store.load_index(owner, name)
    if not index:
        return index_status_to_tool_error(store.inspect_index(owner, name))

    # Normalize file path.
    if file_path not in index.source_files:
        # Try a few common normalizations before giving up.
        normalized = file_path.replace("\\", "/")
        if normalized not in index.source_files:
            return {"error": f"File not in index: {file_path}"}
        file_path = normalized

    # File-level metrics — computed once, shared across every symbol.
    incoming = _count_incoming(index, file_path)
    has_tests = _file_has_tests(index, file_path)
    churn = (
        _churn_for_file(file_path, index.source_root, days=30)
        if index.source_root else 0
    )

    exposure_score = _score_exposure(incoming)
    churn_score = _score_churn(churn)
    test_gap_score = _score_test_gap(has_tests)

    file_lang = ""
    symbols_out: list[dict] = []
    for sym in index.symbols:
        if sym.get("file") != file_path:
            continue
        kind = sym.get("kind")
        if kind not in ("function", "method"):
            continue
        cy = int(sym.get("cyclomatic") or 0)
        complexity_score = _score_complexity(cy)

        composite = round(
            (complexity_score + exposure_score + churn_score + test_gap_score) / 4.0,
            1,
        )
        level = _level_for(composite)

        symbols_out.append({
            "symbol_id": sym.get("id", ""),
            "name": sym.get("name", ""),
            "kind": kind,
            "line": sym.get("line", 0),
            "end_line": sym.get("end_line", 0),
            "cyclomatic": cy,
            "risk": {
                "composite": composite,
                "level": level,
                "axes": {
                    "complexity": round(complexity_score, 1),
                    "exposure": round(exposure_score, 1),
                    "churn": round(churn_score, 1),
                    "test_gap": round(test_gap_score, 1),
                },
            },
        })
        if not file_lang:
            file_lang = sym.get("language", "")

    elapsed = (time.perf_counter() - start) * 1000.0
    return {
        "file": file_path,
        "language": file_lang,
        "file_metrics": {
            "incoming_files": incoming,
            "churn_30d": churn,
            "has_tests": has_tests,
        },
        "symbols": symbols_out,
        "_meta": {"timing_ms": round(elapsed, 1)},
    }
