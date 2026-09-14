"""get_tectonic_map — discover the logical module topology of a codebase.

Tectonic Analysis fuses three independent coupling signals — structural
(import edges), behavioral (shared symbol references), and temporal
(git co-churn) — into a single weighted file graph, then partitions it
via label propagation to reveal the *actual* module boundaries hiding
inside the codebase.

Every discovered plate includes:
  - An **anchor** file (highest weighted degree within the plate).
  - A **cohesion** score (intra-plate edge density).
  - A **coupling map** to other plates (inter-plate edge weight).
  - **Drifters**: files whose directory doesn't match their plate majority,
    indicating the physical layout has diverged from the logical structure.
  - **Nexus alerts**: plates coupled to ≥4 others (god-module risk).

Requires locally indexed repo (source_root) for temporal signal.
Falls back gracefully to structural+behavioral when git is unavailable.

No external dependencies — runs on the existing index data with pure
Python graph algorithms.
"""

from __future__ import annotations

import logging
import os
import random
import subprocess
import time
from collections import defaultdict
from typing import Optional

from ..storage import IndexStore
from ._git_history import churn_is_measurable, history_coverage
from ._utils import resolve_repo
from .get_dependency_graph import _build_adjacency

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Signal weights (normalised to sum to 1.0)
# ---------------------------------------------------------------------------
W_STRUCTURAL = 0.40  # import edges
W_BEHAVIORAL = 0.30  # shared symbol references
W_TEMPORAL = 0.30    # git co-churn


# ---------------------------------------------------------------------------
# Signal extraction
# ---------------------------------------------------------------------------

def _structural_edges(fwd: dict[str, list[str]]) -> dict[tuple[str, str], float]:
    """Weight = number of import edges between the two files (bidirectional max)."""
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for src, targets in fwd.items():
        for tgt in targets:
            key = (min(src, tgt), max(src, tgt))
            counts[key] += 1
    if not counts:
        return {}
    max_val = max(counts.values())
    return {k: v / max_val for k, v in counts.items()} if max_val else {}


def _behavioral_edges(index) -> dict[tuple[str, str], float]:
    """Files that reference the same symbols are behaviorally coupled.

    For each symbol, find all files that import it (via the import name index).
    Every pair of those files gets a +1 for that shared reference.
    """
    # Build: symbol_name → set of files that import it
    symbol_importers: dict[str, set[str]] = defaultdict(set)
    if not index.imports:
        return {}

    for src_file, file_imports in index.imports.items():
        for imp in file_imports:
            for name in imp.get("names", []):
                symbol_importers[name.lower()].add(src_file)
            # Also count the specifier stem
            spec = imp.get("specifier", "")
            if spec:
                stem = os.path.splitext(os.path.basename(spec.replace("\\", "/")))[0].lower()
                if stem:
                    symbol_importers[stem].add(src_file)

    # For each shared reference, weight the pair
    pair_counts: dict[tuple[str, str], int] = defaultdict(int)
    for _sym, files in symbol_importers.items():
        file_list = sorted(files)
        # Cap pairwise expansion: skip symbols imported by >30 files (ubiquitous utils)
        if len(file_list) > 30:
            continue
        for i in range(len(file_list)):
            for j in range(i + 1, len(file_list)):
                key = (file_list[i], file_list[j])
                pair_counts[key] += 1

    if not pair_counts:
        return {}
    max_val = max(pair_counts.values())
    return {k: v / max_val for k, v in pair_counts.items()} if max_val else {}


def _temporal_edges(source_root: str, source_files: frozenset, days: int = 90) -> dict[tuple[str, str], float]:
    """Files that change in the same commit are temporally coupled (co-churn).

    Uses a single `git log --name-only` pass, then counts co-occurrence
    per commit for all file pairs.

    ⚠⚠ (#667) `--format=format:COMMIT_SEP`, never `--format=COMMIT_SEP`: git
    reads a bare `--format=` value as a pretty-format NAME unless it holds a
    `%` placeholder, answered `fatal: invalid --pretty format`, and this
    signal returned `{}` on every repository from the tool's first commit.
    ⚠ `--relative`: `--name-only` prints paths from the git TOP LEVEL, the
    index holds them from `source_root`, and an index rooted in a
    subdirectory would miss every name and return `{}` by a second route.
    ⚠ A non-zero git is a WARNING with git's stderr: a silent `{}` reads
    exactly like a repository with no co-changes.
    """
    try:
        r = subprocess.run(
            ["git", "log", f"--since={days} days ago", "--relative", "--name-only", "--format=format:COMMIT_SEP"],
            cwd=source_root, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60, stdin=subprocess.DEVNULL,
        )
        if r.returncode != 0:
            logger.warning(
                "tectonic temporal signal unavailable: git log exited %s in %s: %s",
                r.returncode, source_root, (r.stderr or "").strip()[:500],
            )
            return {}
        if not r.stdout.strip():
            return {}
    except Exception:
        logger.warning("tectonic temporal signal unavailable: git co-churn extraction failed", exc_info=True)
        return {}

    # Parse commits: split on COMMIT_SEP, extract file sets per commit
    pair_counts: dict[tuple[str, str], int] = defaultdict(int)
    for chunk in r.stdout.split("COMMIT_SEP"):
        files_in_commit = []
        for line in chunk.strip().splitlines():
            line = line.strip().replace("\\", "/")
            if line and line in source_files:
                files_in_commit.append(line)
        # Cap: skip huge commits (merges, bulk renames)
        if len(files_in_commit) > 20 or len(files_in_commit) < 2:
            continue
        files_in_commit.sort()
        for i in range(len(files_in_commit)):
            for j in range(i + 1, len(files_in_commit)):
                key = (files_in_commit[i], files_in_commit[j])
                pair_counts[key] += 1

    if not pair_counts:
        return {}
    max_val = max(pair_counts.values())
    return {k: v / max_val for k, v in pair_counts.items()} if max_val else {}


# ---------------------------------------------------------------------------
# Signal fusion → weighted graph
# ---------------------------------------------------------------------------

def _fuse_signals(
    structural: dict[tuple[str, str], float],
    behavioral: dict[tuple[str, str], float],
    temporal: dict[tuple[str, str], float],
) -> dict[tuple[str, str], float]:
    """Combine three normalised edge-weight dicts into one weighted graph."""
    all_edges: set[tuple[str, str]] = set()
    all_edges.update(structural)
    all_edges.update(behavioral)
    all_edges.update(temporal)

    fused: dict[tuple[str, str], float] = {}
    for edge in all_edges:
        w = (
            W_STRUCTURAL * structural.get(edge, 0.0)
            + W_BEHAVIORAL * behavioral.get(edge, 0.0)
            + W_TEMPORAL * temporal.get(edge, 0.0)
        )
        if w > 0.01:  # prune negligible edges
            fused[edge] = round(w, 6)
    return fused


# ---------------------------------------------------------------------------
# Label propagation (community detection)
# ---------------------------------------------------------------------------

def _label_propagation(
    nodes: list[str],
    edges: dict[tuple[str, str], float],
    max_iterations: int = 50,
    seed: int = 42,
) -> dict[str, int]:
    """Weighted label propagation. Returns {node: community_id}.

    Each node starts with its own label. On each iteration, every node
    adopts the label with the highest total edge weight among its neighbors.
    Ties broken randomly. Converges when no node changes label.
    """
    rng = random.Random(seed)

    # Build adjacency with weights
    adj: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for (a, b), w in edges.items():
        adj[a].append((b, w))
        adj[b].append((a, w))

    # Initialize: each node gets a unique label
    labels: dict[str, int] = {n: i for i, n in enumerate(nodes)}

    for _iteration in range(max_iterations):
        changed = False
        # Process nodes in random order for stability
        order = list(nodes)
        rng.shuffle(order)

        for node in order:
            neighbors = adj.get(node)
            if not neighbors:
                continue

            # Accumulate weight per label
            label_weights: dict[int, float] = defaultdict(float)
            for neighbor, weight in neighbors:
                label_weights[labels[neighbor]] += weight

            if not label_weights:
                continue

            max_weight = max(label_weights.values())
            # Collect all labels tied at max weight
            candidates = [lbl for lbl, w in label_weights.items() if w == max_weight]
            chosen = rng.choice(candidates)

            if chosen != labels[node]:
                labels[node] = chosen
                changed = True

        if not changed:
            break

    # Renumber labels to 0..N-1
    unique_labels = sorted(set(labels.values()))
    remap = {old: new for new, old in enumerate(unique_labels)}
    return {n: remap[lbl] for n, lbl in labels.items()}


# ---------------------------------------------------------------------------
# Plate analysis
# ---------------------------------------------------------------------------

def _majority_directory(files: list[str]) -> str:
    """Return the most common first directory segment among files."""
    dir_counts: dict[str, int] = defaultdict(int)
    for f in files:
        parts = f.replace("\\", "/").split("/")
        # Use the first two segments for better granularity, or just one if flat
        if len(parts) >= 3:
            dir_key = "/".join(parts[:2])
        elif len(parts) >= 2:
            dir_key = parts[0]
        else:
            dir_key = "."
        dir_counts[dir_key] += 1
    return max(dir_counts, key=dir_counts.get) if dir_counts else "."


def _analyze_plates(
    labels: dict[str, int],
    edges: dict[tuple[str, str], float],
    adj_fwd: dict[str, list[str]],
) -> list[dict]:
    """Analyze each plate: anchor, cohesion, coupling, drifters, nexus."""
    # Group files by plate
    plates: dict[int, list[str]] = defaultdict(list)
    for node, label in labels.items():
        plates[label].append(node)

    # Sort by plate size descending
    plate_ids = sorted(plates, key=lambda pid: -len(plates[pid]))

    # Build per-node weighted degree within plate
    node_plate = labels
    intra_degree: dict[str, float] = defaultdict(float)
    inter_plate_weights: dict[tuple[int, int], float] = defaultdict(float)

    for (a, b), w in edges.items():
        pa, pb = node_plate.get(a), node_plate.get(b)
        if pa is None or pb is None:
            continue
        if pa == pb:
            intra_degree[a] += w
            intra_degree[b] += w
        else:
            key = (min(pa, pb), max(pa, pb))
            inter_plate_weights[key] += w

    results = []
    for pid in plate_ids:
        members = sorted(plates[pid])
        if not members:
            continue

        # Anchor: highest intra-plate weighted degree
        anchor = max(members, key=lambda f: intra_degree.get(f, 0.0))

        # Cohesion: actual intra-edges / possible edges
        intra_edge_weight = sum(
            w for (a, b), w in edges.items()
            if node_plate.get(a) == pid and node_plate.get(b) == pid
        )
        possible_edges = len(members) * (len(members) - 1) / 2
        cohesion = round(intra_edge_weight / possible_edges, 4) if possible_edges > 0 else 1.0

        # Coupling to other plates
        coupling: dict[int, float] = {}
        for (pa, pb), w in inter_plate_weights.items():
            if pa == pid:
                coupling[pb] = round(w, 4)
            elif pb == pid:
                coupling[pa] = round(w, 4)

        # Drifters: files whose directory doesn't match plate majority
        majority_dir = _majority_directory(members)
        drifters = []
        for f in members:
            parts = f.replace("\\", "/").split("/")
            if len(parts) >= 3:
                file_dir = "/".join(parts[:2])
            elif len(parts) >= 2:
                file_dir = parts[0]
            else:
                file_dir = "."
            if file_dir != majority_dir:
                drifters.append(f)

        # Nexus alert
        is_nexus = len(coupling) >= 4

        plate_entry: dict = {
            "plate_id": pid,
            "anchor": anchor,
            "file_count": len(members),
            "cohesion": cohesion,
            "files": members,
            "majority_directory": majority_dir,
        }
        if drifters:
            plate_entry["drifters"] = sorted(drifters)
            plate_entry["drifter_count"] = len(drifters)
        if coupling:
            # Convert plate IDs to anchor names for readability
            plate_entry["coupled_to"] = {
                str(other_pid): weight
                for other_pid, weight in sorted(coupling.items(), key=lambda x: -x[1])
            }
        if is_nexus:
            plate_entry["nexus_alert"] = True
            plate_entry["nexus_coupling_count"] = len(coupling)

        results.append(plate_entry)

    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_tectonic_map(
    repo: str,
    days: int = 90,
    min_plate_size: int = 2,
    storage_path: Optional[str] = None,
) -> dict:
    """Discover the logical module topology of a codebase.

    Fuses three coupling signals — structural (imports), behavioral
    (shared symbol references), and temporal (git co-churn) — then
    partitions the weighted file graph via label propagation.

    Args:
        repo:            Repository identifier (owner/repo or bare name).
        days:            Git co-churn look-back window in days (default 90).
        min_plate_size:  Minimum files per plate to include (default 2).
                         Singletons are grouped under ``isolated_files``.
        storage_path:    Optional index storage path override.

    Returns:
        {
          "repo": str,
          "plate_count": int,
          "file_count": int,
          "plates": [{
            "plate_id": int,
            "anchor": str,             # highest-degree file (the plate's core)
            "file_count": int,
            "cohesion": float,         # 0.0–1.0 intra-plate density
            "files": [str],
            "majority_directory": str,
            "drifters": [str],         # files misplaced vs. majority dir
            "drifter_count": int,
            "coupled_to": {plate_id: weight},
            "nexus_alert": bool,       # True if coupled to ≥4 other plates
          }],
          "isolated_files": [str],     # files below min_plate_size
          "signals_used": [str],       # which signals contributed
          "signals_withheld": {name: {reason, ...}},  # only when a signal could not be trusted (#667)
          "drifter_summary": [{        # top misplaced files across all plates
            "file": str,
            "current_directory": str,
            "belongs_with": str,       # plate majority directory
            "plate_anchor": str,
          }],
          "_meta": {timing_ms, methodology, signal_weights}
        }
    """
    t0 = time.perf_counter()

    try:
        owner, name = resolve_repo(repo, storage_path)
    except ValueError as e:
        return {"error": str(e)}

    store = IndexStore(base_path=storage_path)
    index = store.load_index(owner, name)
    if index is None:
        return {"error": f"No index found for {repo!r}. Run index_folder first."}

    if not index.imports:
        return {
            "error": "No import data available. Re-index with jcodemunch-mcp >= 1.3.0 to enable tectonic analysis."
        }

    source_files = frozenset(index.source_files)
    alias_map = getattr(index, "alias_map", None)
    psr4_map = getattr(index, "psr4_map", None)

    # --- Build the three signals ---
    signals_used = []
    signals_withheld: dict[str, dict] = {}

    # 1. Structural (always available if imports exist)
    fwd = _build_adjacency(index.imports, source_files, alias_map, psr4_map)
    structural = _structural_edges(fwd)
    if structural:
        signals_used.append("structural")

    # 2. Behavioral
    behavioral = _behavioral_edges(index)
    if behavioral:
        signals_used.append("behavioral")

    # 3. Temporal (requires local git)
    temporal: dict[tuple[str, str], float] = {}
    if index.source_root:
        try:
            r = subprocess.run(
                ["git", "rev-parse", "--git-dir"],
                cwd=index.source_root, capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=5, stdin=subprocess.DEVNULL,
            )
            if r.returncode == 0:
                # ⚠⚠ Ask the authority whether the window is covered BEFORE
                # trusting it. Each signal is normalised against its own
                # maximum, so a shallow clone would not weaken co-churn, it
                # would RESCALE it: one co-change in three commits scores 1.0
                # like four hundred in full history. Withheld and NAMED in the
                # answer (never `_meta`, which a default install strips).
                if churn_is_measurable(index.source_root, days):
                    temporal = _temporal_edges(index.source_root, source_files, days)
                    if temporal:
                        signals_used.append("temporal")
                else:
                    signals_withheld["temporal"] = history_coverage(index.source_root, days)
        except Exception:
            logger.debug("git availability check failed for tectonic", exc_info=True)

    if not structural and not behavioral:
        return {
            "error": "Insufficient coupling data for tectonic analysis. "
                     "Ensure the repo has import relationships between files."
        }

    # --- Fuse signals ---
    fused = _fuse_signals(structural, behavioral, temporal)

    # Only include files that participate in at least one edge
    active_nodes = set()
    for a, b in fused:
        active_nodes.add(a)
        active_nodes.add(b)
    active_nodes = sorted(active_nodes)

    if len(active_nodes) < 2:
        return {
            "repo": f"{owner}/{name}",
            "plate_count": 0,
            "file_count": len(source_files),
            "plates": [],
            "isolated_files": sorted(source_files),
            "signals_used": signals_used,
            **({"signals_withheld": signals_withheld} if signals_withheld else {}),
            "drifter_summary": [],
            "_meta": {
                "timing_ms": round((time.perf_counter() - t0) * 1000, 1),
                "methodology": "tectonic_label_propagation",
            },
        }

    # --- Label propagation ---
    labels = _label_propagation(active_nodes, fused)

    # --- Analyze plates ---
    raw_plates = _analyze_plates(labels, fused, fwd)

    # Separate small plates into isolated_files
    plates = []
    isolated = []
    for p in raw_plates:
        if p["file_count"] >= min_plate_size:
            plates.append(p)
        else:
            isolated.extend(p["files"])

    # Add files with no edges at all
    all_plated = set()
    for p in raw_plates:
        all_plated.update(p["files"])
    for f in sorted(source_files):
        if f not in all_plated:
            isolated.append(f)
    isolated = sorted(set(isolated))

    # Build plate_id → anchor lookup for readable coupling references
    plate_anchor_map = {p["plate_id"]: p["anchor"] for p in plates}

    # Rewrite coupled_to keys from plate_id to anchor file name
    for p in plates:
        if "coupled_to" in p:
            readable_coupling = {}
            for pid_str, weight in p["coupled_to"].items():
                pid = int(pid_str)
                anchor_name = plate_anchor_map.get(pid, f"plate_{pid}")
                readable_coupling[anchor_name] = weight
            p["coupled_to"] = readable_coupling

    # Drifter summary: top misplaced files across all plates
    drifter_summary = []
    for p in plates:
        for drifter in p.get("drifters", []):
            parts = drifter.replace("\\", "/").split("/")
            if len(parts) >= 3:
                current_dir = "/".join(parts[:2])
            elif len(parts) >= 2:
                current_dir = parts[0]
            else:
                current_dir = "."
            drifter_summary.append({
                "file": drifter,
                "current_directory": current_dir,
                "belongs_with": p["majority_directory"],
                "plate_anchor": p["anchor"],
            })
    # Sort by how far the file drifted (different directory = interesting)
    drifter_summary.sort(key=lambda d: d["file"])

    elapsed = (time.perf_counter() - t0) * 1000
    return {
        "repo": f"{owner}/{name}",
        "plate_count": len(plates),
        "file_count": len(source_files),
        "plates": plates,
        "isolated_files": isolated,
        "signals_used": signals_used,
        **({"signals_withheld": signals_withheld} if signals_withheld else {}),
        "drifter_summary": drifter_summary[:30],  # cap for readability
        "_meta": {
            "timing_ms": round(elapsed, 1),
            "methodology": "tectonic_label_propagation",
            "signal_weights": {
                "structural": W_STRUCTURAL,
                "behavioral": W_BEHAVIORAL,
                "temporal": W_TEMPORAL,
            },
            "active_files": len(active_nodes),
            "edge_count": len(fused),
            "label_propagation_seed": 42,
        },
    }
