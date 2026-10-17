"""audit_agent_config — find wasted tokens in agent configuration files.

Scans CLAUDE.md, .cursorrules, copilot-instructions.md, and other agent
config files for token cost, stale symbol/file references, redundancy,
and bloat.  Cross-references against the jcodemunch index to catch dead
paths and renamed symbols that no other linter can detect.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

from ..storage import IndexStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------

def _estimate_tokens(text: str) -> int:
    """Estimate token count.  Uses tiktoken if available, else word heuristic."""
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        # Rough heuristic: ~0.75 tokens per word for English prose/code mix
        return max(1, int(len(text.split()) * 1.3))


# ---------------------------------------------------------------------------
# Config file discovery
# ---------------------------------------------------------------------------

# (relative_to_home, relative_to_project, description)
_GLOBAL_CONFIGS: list[tuple[str, str]] = [
    (os.path.join(".claude", "CLAUDE.md"), "global CLAUDE.md"),
    (os.path.join(".claude", "settings.json"), "Claude Code settings"),
]

_PROJECT_CONFIGS: list[tuple[str, str]] = [
    ("CLAUDE.md", "project CLAUDE.md"),
    (".cursorrules", "Cursor rules"),
    (os.path.join(".cursor", "rules"), "Cursor rules (new)"),
    (os.path.join(".github", "copilot-instructions.md"), "GitHub Copilot instructions"),
    (".windsurfrules", "Windsurf rules"),
    (os.path.join(".antigravity", "rules"), "Google Antigravity rules"),
    (os.path.join(".continue", "config.json"), "Continue config"),
]


def _discover_files(project_path: Optional[str] = None) -> list[dict[str, Any]]:
    """Find all agent config files and return metadata."""
    home = Path.home()
    project = Path(project_path) if project_path else None
    found: list[dict[str, Any]] = []

    for rel, desc in _GLOBAL_CONFIGS:
        p = home / rel
        if p.is_file():
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
                found.append({
                    "path": str(p),
                    "description": desc,
                    "scope": "global",
                    "content": text,
                    "tokens": _estimate_tokens(text),
                    "lines": text.count("\n") + 1,
                })
            except OSError:
                pass

    if project:
        for rel, desc in _PROJECT_CONFIGS:
            p = project / rel
            if p.is_file():
                try:
                    text = p.read_text(encoding="utf-8", errors="replace")
                    found.append({
                        "path": str(p),
                        "description": desc,
                        "scope": "project",
                        "content": text,
                        "tokens": _estimate_tokens(text),
                        "lines": text.count("\n") + 1,
                    })
                except OSError:
                    pass

    return found


# ---------------------------------------------------------------------------
# Finding extractors
# ---------------------------------------------------------------------------

# Matches things that look like symbol names: camelCase, snake_case, PascalCase
# in contexts like "use X", "call X", "function X", backtick-quoted `X`
_BACKTICK_REF = re.compile(r"`([a-zA-Z_]\w{2,}(?:\.\w+)*)`")
_SYMBOL_LIKE = re.compile(
    r"(?:^|\s)(?:function|method|class|use|call|check|run|invoke|symbol)\s+"
    r"[`'\"]?([a-zA-Z_]\w{2,})[`'\"]?",
    re.IGNORECASE | re.MULTILINE,
)

# Matches file paths: src/foo/bar.py, ./utils/helpers.js, etc.
_FILE_PATH_REF = re.compile(
    r"(?:^|\s|[`'\"])((?:\./|src/|lib/|app/|test|pkg/|internal/|cmd/)"
    r"[a-zA-Z0-9_./-]+\.[a-zA-Z]{1,10})(?:\s|[`'\"]|$|:|\))",
    re.MULTILINE,
)


def _extract_symbol_refs(text: str) -> list[str]:
    """Extract potential symbol name references from config text."""
    refs: set[str] = set()
    for m in _BACKTICK_REF.finditer(text):
        candidate = m.group(1)
        # Filter out common non-symbol backtick content
        if not candidate.startswith(("http", "pip ", "npm ", "git ", "--")):
            refs.add(candidate)
    for m in _SYMBOL_LIKE.finditer(text):
        refs.add(m.group(1))
    return sorted(refs)


def _extract_file_refs(text: str) -> list[str]:
    """Extract potential file path references from config text."""
    refs: set[str] = set()
    for m in _FILE_PATH_REF.finditer(text):
        refs.add(m.group(1))
    return sorted(refs)


def _find_line(text: str, needle: str) -> Optional[int]:
    """Find the 1-based line number of needle in text."""
    for i, line in enumerate(text.splitlines(), 1):
        if needle in line:
            return i
    return None


# ---------------------------------------------------------------------------
# Checkers
# ---------------------------------------------------------------------------

def _check_stale_symbols(
    file_info: dict, index, all_symbol_names: set[str],
) -> list[dict[str, Any]]:
    """Find references to symbols that don't exist in the index.

    Absence from the index alone is not staleness evidence. A missing name
    earns a ``stale_reference`` warning only when corroborated: a fuzzy
    near-match in the index (a renamed or deleted repo symbol) on a
    project-scoped config. Everything else -- an external tool, harness
    facility, or command named in prose (e.g. the harness tool
    ``AskUserQuestion`` in a global ``~/.claude/CLAUDE.md``); a repo symbol
    deleted outright with no near-match left; any reference in a global
    config, which no single repository's index can speak for -- is reported
    as an informational ``unresolved_reference``. The distinction is
    evidence, not a hardcoded list of external names.
    """
    findings: list[dict[str, Any]] = []
    refs = _extract_symbol_refs(file_info["content"])
    for ref in refs:
        # Check against symbol names (plain name, not full ID)
        parts = ref.split(".")
        name = parts[-1]  # Use the last segment (e.g., "bar" from "foo.bar")
        if name in all_symbol_names or len(name) <= 3:
            continue
        # Try fuzzy match for corroboration / suggestions
        suggestion = _fuzzy_suggest(name, all_symbol_names)
        hint = f". Did you mean '{suggestion}'?" if suggestion else ""
        if suggestion and file_info.get("scope", "project") != "global":
            # Corroborated staleness: a near-identical repo symbol exists
            # and this config governs this repository.
            msg = f"References symbol '{ref}' which was not found in the index{hint}"
            category, severity = "stale_reference", "warning"
        else:
            # Absence only. The name may belong to an external tool or
            # harness facility that is not a repository symbol, and a
            # global config spans repositories no single index covers.
            why = (
                "a global config applies across repositories, which this "
                "index does not cover"
                if file_info.get("scope") == "global"
                else "it may name an external tool, harness facility, or "
                     "command that is not a repository symbol"
            )
            msg = (
                f"References '{ref}' which was not found in the index. "
                f"Not proof of a stale reference: {why}. Verify before "
                f"editing{hint}."
            )
            category, severity = "unresolved_reference", "info"
        findings.append({
            "file": file_info["path"],
            "severity": severity,
            "category": category,
            "message": msg,
            "line": _find_line(file_info["content"], ref),
        })
    return findings


def _check_dead_paths(
    file_info: dict, source_files: set[str], source_root: str,
) -> list[dict[str, Any]]:
    """Find references to files that don't exist in the index."""
    findings: list[dict[str, Any]] = []
    refs = _extract_file_refs(file_info["content"])
    for ref in refs:
        # Normalize: strip leading ./
        normalized = ref.lstrip("./")
        # Check if any source file ends with this path
        matched = any(sf.endswith(normalized) or sf == normalized for sf in source_files)
        if not matched:
            # Also check absolute path from source_root
            if source_root:
                abs_path = os.path.join(source_root, normalized)
                if os.path.exists(abs_path):
                    continue
            findings.append({
                "file": file_info["path"],
                "severity": "warning",
                "category": "dead_path",
                "message": f"References '{ref}' which does not exist in the indexed file tree",
                "line": _find_line(file_info["content"], ref),
            })
    return findings


def _check_redundancy(files: list[dict]) -> list[dict[str, Any]]:
    """Find duplicate content between global and project configs."""
    findings: list[dict[str, Any]] = []
    global_files = [f for f in files if f["scope"] == "global"]
    project_files = [f for f in files if f["scope"] == "project"]

    for gf in global_files:
        g_lines = [
            ln.strip() for ln in gf["content"].splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
        for pf in project_files:
            p_lines = [
                ln.strip() for ln in pf["content"].splitlines()
                if ln.strip() and not ln.strip().startswith("#")
            ]
            # Find runs of 3+ consecutive matching lines
            duplicates = _find_duplicate_runs(g_lines, p_lines, min_run=3)
            for dup in duplicates:
                findings.append({
                    "file": pf["path"],
                    "severity": "info",
                    "category": "redundancy",
                    "message": (
                        f"{dup['count']} lines duplicate content already present "
                        f"in {gf['path']} (starting with: '{dup['preview']}')"
                    ),
                    "line": None,
                })
    return findings


def _find_duplicate_runs(
    a_lines: list[str], b_lines: list[str], min_run: int = 3,
) -> list[dict]:
    """Find runs of min_run+ consecutive matching lines between two line lists."""
    if not a_lines or not b_lines:
        return []
    a_set = set(a_lines)
    runs: list[dict] = []
    current_run = 0
    first_line = ""
    for line in b_lines:
        if line in a_set:
            if current_run == 0:
                first_line = line
            current_run += 1
        else:
            if current_run >= min_run:
                preview = first_line[:80] + ("..." if len(first_line) > 80 else "")
                runs.append({"count": current_run, "preview": preview})
            current_run = 0
    if current_run >= min_run:
        preview = first_line[:80] + ("..." if len(first_line) > 80 else "")
        runs.append({"count": current_run, "preview": preview})
    return runs


def _check_bloat(file_info: dict) -> list[dict[str, Any]]:
    """Detect common bloat patterns."""
    findings: list[dict[str, Any]] = []
    content = file_info["content"]
    tokens = file_info["tokens"]

    # Large file warning
    if tokens > 2000:
        findings.append({
            "file": file_info["path"],
            "severity": "warning",
            "category": "bloat",
            "message": (
                f"This file is {tokens:,} tokens. Agent config files over 2,000 tokens "
                f"add significant per-turn cost. Consider trimming verbose examples or "
                f"moving reference material to external docs."
            ),
            "line": None,
        })

    # Excessive code blocks (examples that bloat context)
    code_blocks = re.findall(r"```[\s\S]*?```", content)
    if code_blocks:
        block_chars = sum(len(b) for b in code_blocks)
        block_pct = block_chars / max(len(content), 1)
        if block_pct > 0.5 and len(code_blocks) > 2:
            findings.append({
                "file": file_info["path"],
                "severity": "info",
                "category": "bloat",
                "message": (
                    f"{len(code_blocks)} code blocks make up {block_pct:.0%} of the file. "
                    f"Consider moving examples to a separate reference doc and linking to it."
                ),
                "line": None,
            })

    return findings


def _check_scope_leaks(file_info: dict) -> list[dict[str, Any]]:
    """Detect project-specific rules that leaked into global config."""
    if file_info["scope"] != "global":
        return []
    findings: list[dict[str, Any]] = []
    content = file_info["content"]

    # Patterns that suggest project-specific rules
    project_signals = [
        (r"(?:pytest|jest|mocha|vitest)\b.*(?:--\w+)", "test runner configuration"),
        (r"(?:django|flask|fastapi|express|rails)\b", "framework-specific rules"),
        (r"(?:src/|lib/|app/|pkg/)\w+/\w+\.\w+", "project-specific file paths"),
        (r"(?:table|migration|schema)\s+\w+", "database-specific references"),
    ]
    for pattern, desc in project_signals:
        matches = re.findall(pattern, content, re.IGNORECASE)
        if matches:
            sample = matches[0][:60]
            findings.append({
                "file": file_info["path"],
                "severity": "info",
                "category": "scope_leak",
                "message": (
                    f"Possible project-specific content in global config ({desc}: "
                    f"'{sample}'). Global rules cost tokens in every project."
                ),
                "line": _find_line(content, sample),
            })
            break  # One finding per file is enough
    return findings


# ---------------------------------------------------------------------------
# Skill-candidate advisory
# ---------------------------------------------------------------------------
#
# "A skill might outperform a doc here." Same shape as agent_selector's "a
# lesser model might handle this": cheap signals, named thresholds, an advisory
# annotation, and OFF until asked for.
#
# ⚠ The signal is reference CONCENTRATION, not size. A size threshold fires on
# every large config, tells the user what they can already see, and gets muted.
# What is actually claimed here is narrower: this section's references resolve
# into one subtree of the repo, while the section itself is resident in every
# turn regardless of what the turn is about.
#
# ⚠ STATED LIMIT: nothing records which config section was RELEVANT to a turn.
# Resident cost and concentration are measured; need is not. Every finding says
# so in its own text — do not remove that sentence to make the message shorter.

# Tuned 2026-08-06 against the corpus named in tests/test_skill_candidates.py
# (_TUNING_SET). ⚠ Both numbers moved on measurement and the direction is worth
# keeping: an 0.8 floor with a 0.6 share cap found NOTHING real, because raising
# the floor makes the selected prefix SHALLOWER (a narrow subtree fails the
# floor, so its permissive parent wins) — the two knobs pull against each other.
# The share cap is what actually discriminates: real subtrees measured 0.12-0.13
# of their trees, package roots 0.33-0.50. Tightening the cap to 0.25 rejects
# package roots outright and lets the floor come DOWN to where real sections sit.
DEFAULT_SKILL_THRESHOLDS: dict[str, float] = {
    # A section has to be worth moving before it is worth mentioning.
    "minSectionTokens": 600,
    # Fraction of a section's resolved references that must land in one subtree.
    # Real candidates in the tuning corpus concentrated at 0.67-0.74; nothing
    # cleared 0.8, so 0.8 is not "strict", it is "off".
    "concentrationFloor": 0.65,
    # Below this, concentration is an artifact of a tiny sample.
    "minResolvedRefs": 5,
    # The subtree must be a minority of the repo, or "concentrated in
    # src/<package>" is trivially true and means nothing.
    "subtreeShareCap": 0.25,
}

# A bare name in this many indexed files is vocabulary, not a reference.
_MAX_SYMBOL_FANOUT = 3

_H2 = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


def _split_sections(text: str) -> list[dict[str, Any]]:
    """Split markdown into level-2 sections with 1-based line spans.

    Content before the first ``##`` is deliberately skipped: a preamble is
    orientation for the whole file, which is the one thing that genuinely
    belongs in an always-resident doc.
    """
    matches = list(_H2.finditer(text))
    out: list[dict[str, Any]] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end]
        line = text.count("\n", 0, start) + 1
        # ⚠ rstrip before counting: the slice runs to the NEXT heading's start,
        # so the raw newline count lands end_line ON that heading. Report the
        # last line with content, not the blank run before the next section.
        out.append({
            "title": m.group(1).strip(),
            "text": body,
            "line": line,
            "end_line": line + body.rstrip("\n").count("\n"),
        })
    return out


def _norm(path: str) -> str:
    return path.replace("\\", "/")


def _resolve_section_refs(
    section_text: str, symbol_files: dict[str, set[str]], source_files: set[str],
) -> dict[str, Any]:
    """Resolve a section's symbol and path references to indexed file paths.

    Only UNAMBIGUOUS references count. A symbol name that lives in many files
    tells us nothing about where the section points, and a path fragment that
    suffix-matches several files is the same problem.
    """
    paths: list[str] = []
    symbols_hit = 0
    files_hit = 0

    for ref in _extract_symbol_refs(section_text):
        name = ref.split(".")[-1]
        owners = symbol_files.get(name)
        if owners and len(owners) <= _MAX_SYMBOL_FANOUT:
            paths.extend(owners)
            symbols_hit += 1

    for ref in _extract_file_refs(section_text):
        normalized = _norm(ref).lstrip("./")
        matched = [
            sf for sf in source_files
            if _norm(sf) == normalized or _norm(sf).endswith("/" + normalized)
        ]
        if len(matched) == 1:
            paths.append(matched[0])
            files_hit += 1

    return {"paths": paths, "symbols_hit": symbols_hit, "files_hit": files_hit}


def _best_subtree(
    paths: list[str], all_files: set[str], floor: float, share_cap: float,
) -> Optional[dict[str, Any]]:
    """Deepest directory prefix covering >= floor of paths without covering the repo.

    Deepest-qualifying rather than highest-scoring: ``src`` covers everything in
    a src-layout project, so the most specific prefix that still clears the bar
    is the informative one. ``share_cap`` is what actually rejects ``src`` — a
    prefix containing most of the repo describes the repo, not the section.
    """
    total = len(paths)
    if not total or not all_files:
        return None

    counts: dict[str, int] = {}
    for p in paths:
        parts = _norm(p).split("/")[:-1]
        for depth in range(1, len(parts) + 1):
            prefix = "/".join(parts[:depth])
            counts[prefix] = counts.get(prefix, 0) + 1

    normalized_files = [_norm(f) for f in all_files]
    repo_total = len(normalized_files)
    best: Optional[dict[str, Any]] = None

    for prefix, count in counts.items():
        concentration = count / total
        if concentration < floor:
            continue
        share = sum(1 for f in normalized_files if f.startswith(prefix + "/")) / repo_total
        if share > share_cap:
            continue
        depth = prefix.count("/") + 1
        if best is None or depth > best["depth"]:
            best = {
                "prefix": prefix,
                "count": count,
                "concentration": concentration,
                "repo_share": share,
                "depth": depth,
            }
    return best


def _check_skill_candidates(
    file_info: dict,
    symbol_files: dict[str, set[str]],
    source_files: set[str],
    thresholds: Optional[dict[str, float]] = None,
) -> list[dict[str, Any]]:
    """Find always-resident config sections whose references sit in one subtree.

    Returns [] when the index is unavailable — a concentration claim needs the
    index to resolve against, and file size alone must never trigger this.
    """
    if not symbol_files and not source_files:
        return []

    t = {**DEFAULT_SKILL_THRESHOLDS, **(thresholds or {})}
    min_tokens = t["minSectionTokens"]
    floor = t["concentrationFloor"]
    min_refs = t["minResolvedRefs"]
    share_cap = t["subtreeShareCap"]

    residency = (
        "every session in every project" if file_info["scope"] == "global"
        else "every session in this project"
    )

    findings: list[dict[str, Any]] = []
    for section in _split_sections(file_info["content"]):
        tokens = _estimate_tokens(section["text"])
        if tokens < min_tokens:
            continue

        resolved = _resolve_section_refs(section["text"], symbol_files, source_files)
        paths = resolved["paths"]
        if len(paths) < min_refs:
            continue

        subtree = _best_subtree(paths, source_files, floor, share_cap)
        if subtree is None:
            continue

        findings.append({
            "file": file_info["path"],
            "severity": "info",
            "category": "skill_candidate",
            "message": (
                f"Section '{section['title']}' (lines {section['line']}-{section['end_line']}) "
                f"is ~{tokens:,} tokens, resident {residency}. "
                f"{subtree['count']} of {len(paths)} resolved references "
                f"({subtree['concentration']:.0%}) land under '{subtree['prefix']}', "
                f"which is {subtree['repo_share']:.0%} of the indexed tree. "
                f"Sections with this profile are skill candidates: move the prose to a "
                f"skill and leave a pointer, so it loads when the topic comes up. "
                f"Relevance was NOT measured — nothing records which section a turn "
                f"actually needed, only what it costs and where it points."
            ),
            "line": section["line"],
            "section": section["title"],
            "end_line": section["end_line"],
            "tokens": tokens,
            "subtree": subtree["prefix"],
            "concentration": round(subtree["concentration"], 3),
            "repo_share": round(subtree["repo_share"], 3),
            "resolved_refs": len(paths),
            "symbols_resolved": resolved["symbols_hit"],
            "paths_resolved": resolved["files_hit"],
            "relevance_measured": False,
        })
    return findings


def _fuzzy_suggest(name: str, candidates: set[str], max_dist: int = 3) -> Optional[str]:
    """Find the closest candidate by edit distance."""
    name_lower = name.lower()
    best: Optional[str] = None
    best_dist = max_dist + 1
    for c in candidates:
        if abs(len(c) - len(name)) > max_dist:
            continue
        d = _edit_distance(name_lower, c.lower(), max_dist)
        if d < best_dist:
            best_dist = d
            best = c
    return best if best_dist <= max_dist else None


def _edit_distance(a: str, b: str, max_d: int) -> int:
    """Levenshtein distance with early termination."""
    if abs(len(a) - len(b)) > max_d:
        return max_d + 1
    if a == b:
        return 0
    la, lb = len(a), len(b)
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        curr = [i] + [0] * lb
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            curr[j] = min(curr[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost)
        if min(curr) > max_d:
            return max_d + 1
        prev = curr
    return prev[lb]


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def _skill_advisor_mode() -> str:
    """Resolve skill_advisor_mode; anything unrecognised means off."""
    try:
        from .. import config as config_module
        mode = config_module.get("skill_advisor_mode", "off")
    except Exception:
        logger.debug("Could not read skill_advisor_mode", exc_info=True)
        return "off"
    return mode if mode in ("off", "advise") else "off"


def audit_agent_config(
    *,
    repo: Optional[str] = None,
    project_path: Optional[str] = None,
    storage_path: Optional[str] = None,
    skill_candidates: Optional[bool] = None,
) -> dict[str, Any]:
    """Audit agent configuration files for token waste and stale references.

    Args:
        repo: Repository identifier for cross-referencing symbols/files.
              If omitted, skips stale-reference and dead-path checks.
        project_path: Project directory to scan for config files.
                     Defaults to cwd.
        storage_path: Override index storage path.
        skill_candidates: Force the skill-candidate advisory on or off for this
              call. ``None`` (default) defers to ``skill_advisor_mode``, which
              is ``off`` unless the user turned it on.

    Returns:
        Structured audit result with files_scanned, total_tokens,
        per_turn_cost, findings, and token_breakdown.
    """
    project = project_path or os.getcwd()
    files = _discover_files(project)

    if not files:
        return {
            "files_scanned": 0,
            "total_tokens": 0,
            "per_turn_cost": "No agent config files found",
            "findings": [],
            "token_breakdown": [],
        }

    # Load index if repo is provided
    index = None
    all_symbol_names: set[str] = set()
    symbol_files: dict[str, set[str]] = {}
    source_files: set[str] = set()
    source_root = ""

    if repo:
        try:
            from ._utils import resolve_repo as _resolve
            owner, name = _resolve(repo, storage_path)
            store = IndexStore(base_path=storage_path)
            index = store.load_index(owner, name)
            if index:
                all_symbol_names = {s.get("name", "") for s in index.symbols if s.get("name")}
                for s in index.symbols:
                    sname, sfile = s.get("name"), s.get("file")
                    if sname and sfile:
                        symbol_files.setdefault(sname, set()).add(sfile)
                source_files = set(index.source_files)
                source_root = index.source_root or ""
        except Exception as e:
            logger.debug("Could not load index for %s: %s", repo, e)

    want_skills = (
        _skill_advisor_mode() == "advise" if skill_candidates is None
        else bool(skill_candidates)
    )

    # Run all checks
    findings: list[dict[str, Any]] = []
    for f in files:
        findings.extend(_check_bloat(f))
        findings.extend(_check_scope_leaks(f))
        if index:
            findings.extend(_check_stale_symbols(f, index, all_symbol_names))
            findings.extend(_check_dead_paths(f, source_files, source_root))
            if want_skills:
                findings.extend(_check_skill_candidates(f, symbol_files, source_files))
    findings.extend(_check_redundancy(files))

    # Sort: warnings first, then info
    severity_order = {"error": 0, "warning": 1, "info": 2}
    findings.sort(key=lambda f: severity_order.get(f["severity"], 9))

    total_tokens = sum(f["tokens"] for f in files)
    global_tokens = sum(f["tokens"] for f in files if f["scope"] == "global")

    return {
        "files_scanned": len(files),
        "total_tokens": total_tokens,
        "global_tokens": global_tokens,
        "per_turn_cost": (
            f"~{total_tokens:,} tokens injected into every agent turn"
            + (f" ({global_tokens:,} from global config, applied to all projects)" if global_tokens else "")
        ),
        "findings": findings,
        "finding_counts": {
            "warning": sum(1 for f in findings if f["severity"] == "warning"),
            "info": sum(1 for f in findings if f["severity"] == "info"),
        },
        "skill_advisor": {
            "enabled": want_skills,
            "index_available": bool(index),
            "hint": None if want_skills else (
                "Skill-candidate advisory is off. Enable with "
                "`jcodemunch-mcp config set skill_advisor_mode advise` to flag "
                "always-resident config sections whose references sit in one subtree."
            ),
        },
        "token_breakdown": [
            {
                "file": f["path"],
                "scope": f["scope"],
                "tokens": f["tokens"],
                "lines": f["lines"],
                "description": f["description"],
            }
            for f in sorted(files, key=lambda x: x["tokens"], reverse=True)
        ],
    }
