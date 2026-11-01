"""One-shot diagnostic: list every file with instability > 0.7.

Shows the raw graph view (every unstable file regardless of dir) and the
production-filtered view (what the coupling axis actually scores against
since v1.91.0). Use this when a repo's coupling score looks suspicious
to confirm whether it's dominated by tests/scripts/etc. or by real
production-code instability.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

from jcodemunch_mcp.storage import IndexStore
from jcodemunch_mcp.tools.get_dependency_graph import _build_adjacency
from jcodemunch_mcp.tools.get_repo_health import _is_production_path
from jcodemunch_mcp.tools._utils import resolve_repo


def main(repo: str = "jgravelle/jcodemunch-mcp") -> int:
    owner, name = resolve_repo(repo)
    index = IndexStore().load_index(owner, name)
    if index is None:
        print(f"no index for {repo}", file=sys.stderr)
        return 2

    source_files = frozenset(index.source_files)
    fwd = _build_adjacency(
        index.imports,
        source_files,
        getattr(index, "alias_map", None),
        getattr(index, "psr4_map", None),
    )
    rev: dict[str, list] = {}
    for src, targets in fwd.items():
        for tgt in targets:
            rev.setdefault(tgt, []).append(src)

    unstable: list[tuple[str, int, int, float]] = []
    for f in index.source_files:
        ca = len(rev.get(f, []))
        ce = len(fwd.get(f, []))
        total = ca + ce
        if total > 0 and (ce / total) > 0.7:
            unstable.append((f, ca, ce, ce / total))

    unstable.sort(key=lambda r: (-r[3], r[0]))

    prod_unstable = [r for r in unstable if _is_production_path(r[0])]
    prod_total = sum(1 for f in index.source_files if _is_production_path(f))

    print(
        f"raw unstable:        {len(unstable)} / {len(index.source_files)} "
        f"= {len(unstable) / len(index.source_files) * 100:.1f}%"
    )
    print(
        f"production unstable: {len(prod_unstable)} / {prod_total} "
        f"= {(len(prod_unstable) / prod_total * 100 if prod_total else 0):.1f}%  "
        f"<-- coupling axis denominator (v1.91.0+)"
    )
    print()
    print(f"{'instab':>7}  {'Ca':>4}  {'Ce':>4}  {'prod':>4}  path")
    print("-" * 76)
    for f, ca, ce, instab in unstable:
        marker = "yes" if _is_production_path(f) else "no"
        print(f"{instab:7.2f}  {ca:>4}  {ce:>4}  {marker:>4}  {f}")

    print()
    print("=== top-level directory breakdown ===")
    by_dir: Counter[str] = Counter()
    for f, *_ in unstable:
        parts = Path(f).parts
        if len(parts) >= 3:
            top = "/".join(parts[:3])  # src/jcodemunch_mcp/<dir>
        else:
            top = "/".join(parts)
        by_dir[top] += 1
    for d, count in by_dir.most_common():
        print(f"  {count:>4}  {d}")
    return 0


if __name__ == "__main__":
    # A Windows console or redirect is cp1252; repo text is not (L-145).
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "jgravelle/jcodemunch-mcp"))
