"""Index storage with save/load, byte-offset content retrieval, and incremental indexing."""

import functools
import hashlib
import json
import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from ..parser.symbols import Symbol
from .sqlite_store import SQLiteIndexStore

logger = logging.getLogger(__name__)

# Bump this when the index schema changes in an incompatible way.
INDEX_VERSION = 4


@functools.lru_cache(maxsize=16)
def _load_index_json_cached(index_path: str, mtime_ns: int) -> Optional[dict]:
    """Cache parsed JSON keyed on (path, mtime). mtime_ns ensures
    automatic invalidation when the file changes on disk."""
    try:
        with open(index_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        logger.debug("Failed to load index JSON: %s", index_path, exc_info=True)
        return None


def _invalidate_index_cache() -> None:
    """Clear the index JSON cache after writes."""
    _load_index_json_cached.cache_clear()


def _file_hash(content: str) -> str:
    """SHA-256 hash of file content string."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _get_git_head(repo_path: Path) -> Optional[str]:
    """Get current HEAD commit hash for a git repo, or None."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_path),
            capture_output=True, text=True, timeout=5,
            stdin=subprocess.DEVNULL,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        logger.debug("Failed to get git HEAD for %s", repo_path, exc_info=True)
    return None


@dataclass
class CodeIndex:
    """Index for a repository's source code."""
    repo: str                    # "owner/repo"
    owner: str
    name: str
    indexed_at: str              # ISO timestamp
    source_files: list[str]      # All indexed file paths
    languages: dict[str, int]    # Language -> file count
    symbols: list[dict]          # Serialized Symbol dicts (without source content)
    index_version: int = INDEX_VERSION
    file_hashes: dict[str, str] = field(default_factory=dict)  # file_path -> sha256
    git_head: str = ""           # HEAD commit hash at index time (for git repos)
    file_summaries: dict[str, str] = field(default_factory=dict)  # file_path -> summary
    source_root: str = ""        # Absolute source root for local indexes, empty for remote
    file_languages: dict[str, str] = field(default_factory=dict)  # file_path -> language
    display_name: str = ""       # User-facing name (for local hashed repo IDs)
    imports: Optional[dict[str, list[dict]]] = None  # file_path -> [{specifier, names}]; None = not indexed yet (pre-v1.3.0)
    context_metadata: dict = field(default_factory=dict)  # Provider metadata (e.g., dbt_columns)
    file_blob_shas: dict[str, str] = field(default_factory=dict)  # file_path -> GitHub blob SHA (remote repos only)
    file_mtimes: dict[str, int] = field(default_factory=dict)  # file_path -> os.stat().st_mtime_ns

    def __post_init__(self) -> None:
        if not self.display_name:
            self.display_name = self.name
        # Build O(1) lookup structures once at load time.
        self._symbol_index: dict[str, dict] = {s["id"]: s for s in self.symbols if "id" in s}
        self._source_file_set: set[str] = set(self.source_files)

    def get_symbol(self, symbol_id: str) -> Optional[dict]:
        """Find a symbol by ID (O(1))."""
        return self._symbol_index.get(symbol_id)

    def has_source_file(self, file_path: str) -> bool:
        """Check whether a file is present in the index."""
        return file_path in self._source_file_set

    def search(self, query: str, kind: Optional[str] = None, file_pattern: Optional[str] = None, limit: int = 0) -> list[dict]:
        """Search symbols with weighted scoring.

        Args:
            limit: When > 0, use a bounded heap to cap memory at ~limit items.
                   Results may slightly exceed limit to preserve ties at the boundary.
                   When 0, return all matches (legacy behavior).
        """
        import heapq

        query_lower = query.lower()
        query_words = set(query_lower.split())

        if limit > 0:
            # Bounded heap: keep only the top `limit` scores using a min-heap
            heap: list[tuple[int, int, dict]] = []  # (score, counter, sym)
            counter = 0
            for sym in self.symbols:
                if kind and sym.get("kind") != kind:
                    continue
                if file_pattern and not self._match_pattern(sym.get("file", ""), file_pattern):
                    continue
                score = self._score_symbol(sym, query_lower, query_words)
                if score > 0:
                    counter += 1
                    if len(heap) < limit:
                        heapq.heappush(heap, (score, counter, sym))
                    elif score > heap[0][0]:
                        heapq.heapreplace(heap, (score, counter, sym))
            return [sym for _, _, sym in sorted(heap, key=lambda x: x[0], reverse=True)]
        else:
            scored = []
            for sym in self.symbols:
                if kind and sym.get("kind") != kind:
                    continue
                if file_pattern and not self._match_pattern(sym.get("file", ""), file_pattern):
                    continue
                score = self._score_symbol(sym, query_lower, query_words)
                if score > 0:
                    scored.append((score, sym))
            scored.sort(key=lambda x: x[0], reverse=True)
            return [sym for _, sym in scored]

    def _match_pattern(self, file_path: str, pattern: str) -> bool:
        """Match file path against glob pattern."""
        import fnmatch
        return fnmatch.fnmatch(file_path, pattern) or fnmatch.fnmatch(file_path, f"*/{pattern}")

    def _score_symbol(self, sym: dict, query_lower: str, query_words: set) -> int:
        """Calculate search score for a symbol."""
        score = 0

        # 1. Exact name match (highest weight)
        name_lower = sym.get("name", "").lower()
        if query_lower == name_lower:
            score += 20
        elif query_lower in name_lower:
            score += 10

        # 2. Name word overlap
        for word in query_words:
            if word in name_lower:
                score += 5

        # 3. Signature match
        sig_lower = sym.get("signature", "").lower()
        if query_lower in sig_lower:
            score += 8
        for word in query_words:
            if word in sig_lower:
                score += 2

        # 4. Summary match
        summary_lower = sym.get("summary", "").lower()
        if query_lower in summary_lower:
            score += 5
        for word in query_words:
            if word in summary_lower:
                score += 1

        # 5. Keyword match
        keywords = set(sym.get("keywords", []))
        matching_keywords = query_words & keywords
        score += len(matching_keywords) * 3

        # 6. Docstring match
        doc_lower = sym.get("docstring", "").lower()
        for word in query_words:
            if word in doc_lower:
                score += 1

        return score


class IndexStore:
    """Storage for code indexes with byte-offset content retrieval."""

    def __init__(self, base_path: Optional[str] = None):
        """Initialize store.

        Args:
            base_path: Base directory for storage. Defaults to ~/.code-index/
        """
        if base_path:
            self.base_path = Path(base_path)
        else:
            self.base_path = Path.home() / ".code-index"

        self.base_path.mkdir(parents=True, exist_ok=True)
        self._sqlite = SQLiteIndexStore(base_path=base_path)

    def close(self) -> None:
        """Checkpoint and close all WAL files for every indexed repo.

        Call from server shutdown to compact WAL files before exit.
        Safe to call multiple times or when no repos are indexed.
        """
        if not self.base_path.exists():
            return
        for db_file in self.base_path.glob("*.db"):
            self._sqlite.checkpoint_db(db_file)

    def _safe_repo_component(self, value: str, field_name: str) -> str:
        """Validate and sanitize owner/name components used in on-disk cache paths.

        Characters outside [A-Za-z0-9._-] (e.g. spaces) are replaced with hyphens
        so that directories with special characters in their names can be indexed.
        Path separators are still rejected outright.
        """
        import re

        if not value or value in {".", ".."}:
            raise ValueError(f"Invalid {field_name}: {value!r}")
        if "/" in value or "\\" in value:
            raise ValueError(f"Invalid {field_name}: {value!r}")
        # Sanitize invalid characters to hyphens rather than raising
        value = re.sub(r"[^A-Za-z0-9._-]", "-", value)
        value = re.sub(r"-+", "-", value).strip("-")
        if not value:
            raise ValueError(f"Invalid {field_name}: sanitized to empty string")
        return value

    def _repo_slug(self, owner: str, name: str) -> str:
        """Stable and safe slug used for index/content file paths."""
        safe_owner = self._safe_repo_component(owner, "owner")
        safe_name = self._safe_repo_component(name, "name")
        return f"{safe_owner}-{safe_name}"

    def _index_path(self, owner: str, name: str) -> Path:
        """Path to index JSON file."""
        return self.base_path / f"{self._repo_slug(owner, name)}.json"

    def _lock_path(self, owner: str, name: str) -> Path:
        """Path to per-repo advisory lock file."""
        return self.base_path / f"{self._repo_slug(owner, name)}.json.lock"

    def _meta_path(self, owner: str, name: str) -> Path:
        """Path to lightweight metadata sidecar for list_repos."""
        return self.base_path / f"{self._repo_slug(owner, name)}.meta.json"

    def _checksum_path(self, index_path: Path) -> Path:
        """Path to SHA-256 integrity checksum sidecar."""
        return index_path.with_suffix(".json.sha256")

    def _write_checksum(self, index_path: Path, index_bytes: bytes) -> None:
        """Write SHA-256 checksum sidecar for index integrity verification."""
        sha = hashlib.sha256(index_bytes).hexdigest()
        try:
            self._checksum_path(index_path).write_text(sha, encoding="utf-8")
        except Exception:
            logger.debug("Failed to write checksum sidecar for %s", index_path, exc_info=True)

    def _verify_checksum(self, index_path: Path) -> bool:
        """Verify index file against its SHA-256 checksum sidecar.

        Returns True if no sidecar exists (backwards-compatible) or checksum matches.
        Logs a warning on mismatch but still returns True (non-blocking).
        """
        sha_path = self._checksum_path(index_path)
        if not sha_path.exists():
            return True  # No sidecar — old index, skip check
        try:
            expected = sha_path.read_text(encoding="utf-8").strip()
            actual = hashlib.sha256(index_path.read_bytes()).hexdigest()
            if actual != expected:
                logger.warning(
                    "Index integrity check failed for %s — expected %s, got %s. "
                    "The index may have been modified externally. Re-index to fix.",
                    index_path, expected[:12], actual[:12],
                )
            return True
        except Exception:
            logger.debug("Checksum verification error for %s", index_path, exc_info=True)
            return True  # Err on the side of loading

    def _write_meta_sidecar(self, index: "CodeIndex") -> None:
        """Write a small metadata sidecar alongside the full index."""
        meta = {
            "repo": index.repo,
            "indexed_at": index.indexed_at,
            "symbol_count": len(index.symbols),
            "file_count": len(index.source_files),
            "languages": index.languages,
            "index_version": index.index_version,
            "git_head": index.git_head,
            "display_name": index.display_name,
            "source_root": index.source_root,
        }
        meta_path = self._meta_path(index.owner, index.name)
        try:
            fd, tmp_name = tempfile.mkstemp(dir=meta_path.parent, suffix=".meta.tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(meta, f)
                Path(tmp_name).replace(meta_path)
            except Exception:
                Path(tmp_name).unlink(missing_ok=True)
        except Exception:
            logger.debug("Failed to write meta sidecar", exc_info=True)

    def _content_dir(self, owner: str, name: str) -> Path:
        """Path to raw content directory."""
        return self.base_path / self._repo_slug(owner, name)

    def _safe_content_path(self, content_dir: Path, relative_path: str) -> Optional[Path]:
        """Resolve a content path and ensure it stays within content_dir.

        Prevents path traversal when writing/reading cached raw files from
        untrusted repository paths.
        """
        try:
            base = content_dir.resolve()
            candidate = (content_dir / relative_path).resolve()
            if os.path.commonpath([str(base), str(candidate)]) != str(base):
                return None
            return candidate
        except (OSError, ValueError):
            return None

    def _write_cached_text(self, path: Path, content: str) -> None:
        """Write cached text without newline translation."""
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(content)

    def _read_cached_text(self, path: Path) -> Optional[str]:
        """Read cached text without newline normalization."""
        try:
            with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
                return f.read()
        except OSError:
            return None

    def _repo_metadata_from_data(self, data: dict, owner: str, name: str) -> tuple[str, str, str]:
        """Normalize repo/owner/name fields from stored JSON."""
        repo_id = data.get("repo", f"{owner}/{name}")
        if "/" in repo_id:
            repo_owner, repo_name = repo_id.split("/", 1)
        else:
            repo_owner, repo_name = owner, name
        return repo_id, data.get("owner", repo_owner), data.get("name", repo_name)

    def _file_languages_from_symbols(self, symbols: list[dict]) -> dict[str, str]:
        """Compute file -> language using symbol metadata."""
        file_languages: dict[str, str] = {}
        for sym in symbols:
            file_path = sym.get("file")
            language = sym.get("language")
            if file_path and language and file_path not in file_languages:
                file_languages[file_path] = language
        return file_languages

    def _file_languages_for_paths(
        self,
        paths: list[str],
        symbols: list[dict],
        existing: Optional[dict[str, str]] = None,
    ) -> dict[str, str]:
        """Fill file -> language for the given paths using symbols then extension fallback."""
        from ..parser.languages import get_language_for_path

        symbol_languages = self._file_languages_from_symbols(symbols)
        file_languages = dict(existing or {})

        for file_path in paths:
            language = (
                symbol_languages.get(file_path)
                or file_languages.get(file_path)
                or get_language_for_path(file_path)
                or ""
            )
            if language:
                file_languages[file_path] = language

        return {file_path: file_languages[file_path] for file_path in paths if file_path in file_languages}

    def _languages_from_file_languages(self, file_languages: dict[str, str]) -> dict[str, int]:
        """Compute language -> file count from stored file language metadata."""
        counts: dict[str, int] = {}
        for language in file_languages.values():
            counts[language] = counts.get(language, 0) + 1
        return counts

    def save_index(
        self,
        owner: str,
        name: str,
        source_files: list[str],
        symbols: list[Symbol],
        raw_files: dict[str, str],
        languages: Optional[dict[str, int]] = None,
        file_hashes: Optional[dict[str, str]] = None,
        git_head: str = "",
        file_summaries: Optional[dict[str, str]] = None,
        source_root: str = "",
        file_languages: Optional[dict[str, str]] = None,
        display_name: str = "",
        imports: Optional[dict[str, list[dict]]] = None,
        context_metadata: Optional[dict] = None,
        file_blob_shas: Optional[dict[str, str]] = None,
        file_mtimes: Optional[dict[str, int]] = None,
    ) -> "CodeIndex":
        """Save index via SQLite backend."""
        # Validate owner/name for path separators (before any slug computation)
        if "/" in owner or "\\" in owner:
            raise ValueError(f"Invalid owner: {owner!r}")
        if "/" in name or "\\" in name:
            raise ValueError(f"Path separator not allowed in name: {name!r}")
        # Preserve existing language computation logic
        normalized_source_files = sorted(dict.fromkeys(source_files or list(raw_files.keys())))
        serialized_symbols = [self._symbol_to_dict(s) for s in symbols]
        merged_file_languages = self._file_languages_for_paths(
            normalized_source_files,
            serialized_symbols,
            existing=file_languages,
        )
        resolved_languages = languages or self._languages_from_file_languages(merged_file_languages)

        if file_hashes is None:
            file_hashes = {fp: _file_hash(content) for fp, content in raw_files.items()}

        result = self._sqlite.save_index(
            owner=owner, name=name,
            source_files=normalized_source_files, symbols=symbols,
            raw_files=raw_files, languages=resolved_languages,
            file_hashes=file_hashes, git_head=git_head,
            file_summaries=file_summaries, source_root=source_root,
            file_languages=merged_file_languages, display_name=display_name,
            imports=imports, context_metadata=context_metadata,
            file_blob_shas=file_blob_shas, file_mtimes=file_mtimes,
        )

        # Clean up any legacy JSON now that data is safely in SQLite.
        index_path = self._index_path(owner, name)
        if index_path.exists():
            try:
                index_path.rename(index_path.with_suffix(".json.migrated"))
            except OSError:
                index_path.unlink(missing_ok=True)
        self._meta_path(owner, name).unlink(missing_ok=True)
        self._lock_path(owner, name).unlink(missing_ok=True)
        self._checksum_path(index_path).unlink(missing_ok=True)

        return result

    def has_index(self, owner: str, name: str) -> bool:
        """Return True if an index exists (SQLite or JSON)."""
        return self._sqlite.has_index(owner, name) or self._index_path(owner, name).exists()

    def load_index(self, owner: str, name: str) -> Optional[CodeIndex]:
        """Load index from storage. Prefers SQLite, auto-migrates from JSON."""
        # Try SQLite first
        result = self._sqlite.load_index(owner, name)
        if result is not None:
            return result

        # Try auto-migration from JSON
        index_path = self._index_path(owner, name)
        if index_path.exists():
            logger.info("Auto-migrating %s/%s from JSON to SQLite", owner, name)
            result = self._sqlite.migrate_from_json(index_path, owner, name)
            if result is not None:
                _invalidate_index_cache()
                return result

        return None

    def get_symbol_content(self, owner: str, name: str, symbol_id: str, _index: Optional["CodeIndex"] = None) -> Optional[str]:
        """Read symbol source using stored byte offsets.

        Delegates to the SQLite backend for a single-row lookup.
        Pass _index to avoid a redundant load_index() call when the caller
        already holds a loaded index.
        """
        return self._sqlite.get_symbol_content(owner, name, symbol_id, _index)

    def get_file_content(
        self,
        owner: str,
        name: str,
        file_path: str,
        _index: Optional["CodeIndex"] = None,
    ) -> Optional[str]:
        """Read a cached file's full content."""
        return self._sqlite.get_file_content(owner, name, file_path, _index)

    def detect_changes(
        self,
        owner: str,
        name: str,
        current_files: dict[str, str],
    ) -> tuple[list[str], list[str], list[str]]:
        """Detect changed, new, and deleted files by comparing hashes."""
        current_hashes = {fp: _file_hash(content) for fp, content in current_files.items()}
        return self.detect_changes_from_hashes(owner, name, current_hashes)

    def detect_changes_from_hashes(
        self,
        owner: str,
        name: str,
        current_hashes: dict[str, str],
    ) -> tuple[list[str], list[str], list[str]]:
        """Detect changed, new, and deleted files from precomputed hashes.

        Delegates to the SQLite backend for a single-row lookup.
        """
        return self._sqlite.detect_changes_from_hashes(owner, name, current_hashes)

    def detect_changes_with_mtimes(
        self,
        owner: str,
        name: str,
        current_mtimes: dict[str, int],
        hash_fn: Callable[[str], str],
    ) -> tuple[list[str], list[str], list[str], dict[str, str], dict[str, int]]:
        """Fast-path change detection using mtimes, falling back to hash on mismatch.

        Delegates to the SQLite backend for a single-row lookup.

        Args:
            owner: Repository owner.
            name: Repository name.
            current_mtimes: rel_path -> st_mtime_ns for all current files.
            hash_fn: Callable that takes a rel_path and returns its SHA-256 hash.

        Returns:
            Tuple of (changed_files, new_files, deleted_files,
                      hashes_for_changed_and_new, updated_mtimes).
        """
        return self._sqlite.detect_changes_with_mtimes(owner, name, current_mtimes, hash_fn)

    def incremental_save(
        self,
        owner: str,
        name: str,
        changed_files: list[str],
        new_files: list[str],
        deleted_files: list[str],
        new_symbols: list[Symbol],
        raw_files: dict[str, str],
        languages: Optional[dict[str, int]] = None,
        git_head: str = "",
        file_summaries: Optional[dict[str, str]] = None,
        file_languages: Optional[dict[str, str]] = None,
        imports: Optional[dict[str, list[dict]]] = None,
        context_metadata: Optional[dict] = None,
        file_blob_shas: Optional[dict[str, str]] = None,
        file_hashes: Optional[dict[str, str]] = None,
        file_mtimes: Optional[dict[str, int]] = None,
    ) -> Optional[CodeIndex]:
        """Incrementally update via SQLite backend."""
        # Compute file_languages for changed/new files using existing logic.
        # Query only the file_languages column — NOT the full index.
        changed_or_new = sorted(set(changed_files) | set(new_files))
        new_symbol_dicts = [self._symbol_to_dict(s) for s in new_symbols]
        existing_fl = self._sqlite.get_file_languages(owner, name)
        merged_file_languages = self._file_languages_for_paths(
            changed_or_new, new_symbol_dicts,
            existing={**existing_fl, **(file_languages or {})},
        )

        return self._sqlite.incremental_save(
            owner=owner, name=name,
            changed_files=changed_files, new_files=new_files,
            deleted_files=deleted_files, new_symbols=new_symbols,
            raw_files=raw_files, languages=languages, git_head=git_head,
            file_summaries=file_summaries, file_languages=merged_file_languages,
            imports=imports, context_metadata=context_metadata,
            file_blob_shas=file_blob_shas, file_hashes=file_hashes,
            file_mtimes=file_mtimes,
        )

    def _languages_from_symbols(self, symbols: list[dict]) -> dict[str, int]:
        """Compute language->file_count from serialized symbols."""
        return self._languages_from_file_languages(self._file_languages_from_symbols(symbols))

    def _repo_entry_from_data(self, data: dict) -> Optional[dict]:
        """Build a repo listing entry from index or sidecar data."""
        repo_id = data.get("repo")
        if not repo_id:
            return None
        # Sidecar has symbol_count/file_count directly; full index has lists
        symbol_count = data.get("symbol_count")
        if symbol_count is None:
            symbol_count = len(data.get("symbols", []))
        file_count = data.get("file_count")
        if file_count is None:
            file_count = len(data.get("source_files", []))
        repo_entry = {
            "repo": repo_id,
            "indexed_at": data.get("indexed_at", ""),
            "symbol_count": symbol_count,
            "file_count": file_count,
            "languages": data.get("languages", {}),
            "index_version": data.get("index_version", 1),
        }
        if data.get("git_head"):
            repo_entry["git_head"] = data["git_head"]
        if data.get("display_name"):
            repo_entry["display_name"] = data["display_name"]
        if data.get("source_root"):
            if os.environ.get("JCODEMUNCH_REDACT_SOURCE_ROOT", "") == "1":
                repo_entry["source_root"] = data.get("display_name", "") or ""
            else:
                repo_entry["source_root"] = data["source_root"]
        return repo_entry

    def list_repos(self) -> list[dict]:
        """List all indexed repositories (SQLite + legacy JSON)."""
        repos = []
        seen_slugs: set[str] = set()

        # Pass 1: SQLite databases
        for db_file in self.base_path.glob("*.db"):
            slug = db_file.stem
            seen_slugs.add(slug)
            try:
                entry = self._sqlite._list_repo_from_db(db_file)
                if entry:
                    repos.append(entry)
            except Exception:
                logger.debug("Skipping corrupted DB: %s", db_file, exc_info=True)

        # Pass 2: legacy JSON — eagerly migrate to SQLite so that every
        # repo is in the canonical format before any tool can interact with it.
        # This prevents data loss when invalidate_cache is called before
        # load_index has had a chance to trigger lazy migration.
        json_files_to_migrate: list[Path] = []

        for meta_file in self.base_path.glob("*.meta.json"):
            slug = meta_file.name.removesuffix(".meta.json")
            if slug in seen_slugs:
                continue
            # Check if a matching .json exists for migration
            json_path = self.base_path / f"{slug}.json"
            if json_path.exists():
                json_files_to_migrate.append(json_path)
            seen_slugs.add(slug)
            try:
                with open(meta_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                entry = self._repo_entry_from_data(data)
                if entry:
                    repos.append(entry)
            except Exception:
                continue

        for index_file in self.base_path.glob("*.json"):
            slug = index_file.name.removesuffix(".json")
            if slug in seen_slugs or slug.endswith(".meta"):
                continue
            json_files_to_migrate.append(index_file)
            try:
                with open(index_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                entry = self._repo_entry_from_data(data)
                if entry:
                    repos.append(entry)
            except Exception:
                continue

        # Eagerly migrate discovered JSON indexes to SQLite (fire-and-forget;
        # failures are logged but don't block the listing).
        for json_path in json_files_to_migrate:
            slug = json_path.stem
            # Extract owner/name from slug: "owner-name" → ("owner", "name")
            parts = slug.split("-", 1)
            if len(parts) != 2:
                continue
            owner, name = parts
            if self._sqlite.has_index(owner, name):
                continue  # already migrated
            try:
                logger.info("Eager-migrating %s from JSON to SQLite", json_path)
                self._sqlite.migrate_from_json(json_path, owner, name)
            except Exception:
                logger.warning(
                    "Failed to eager-migrate %s — will retry on next load_index",
                    json_path, exc_info=True,
                )

        repos.sort(key=lambda repo: repo["repo"])
        return repos

    def delete_index(self, owner: str, name: str) -> bool:
        """Delete an index (SQLite DB + sidecars + content dir).

        Legacy .json index files are only deleted when a SQLite .db already
        existed (meaning the data was safely migrated).  If the JSON is the
        *only* copy of the data, deleting it would silently discard user data
        with no backup.  In that case we preserve the JSON — it will be
        replaced automatically on the next index_folder / save_index call.
        """
        db_existed = self._sqlite.has_index(owner, name)
        deleted = self._sqlite.delete_index(owner, name)

        index_path = self._index_path(owner, name)
        meta_path = self._meta_path(owner, name)
        lock_path = self._lock_path(owner, name)

        if index_path.exists():
            if db_existed:
                # Data was already in SQLite; JSON is a redundant legacy file.
                index_path.unlink()
                deleted = True
            else:
                # JSON is the sole copy — do NOT delete it.
                # Still report deleted=True so invalidate_cache returns success
                # (the SQLite system has nothing to offer; the JSON will be
                # overwritten on the next index_folder run).
                logger.warning(
                    "delete_index: preserving unmigrated JSON for %s/%s — "
                    "it will be replaced on the next index_folder run.",
                    owner, name,
                )
                deleted = True

        meta_path.unlink(missing_ok=True)
        lock_path.unlink(missing_ok=True)
        self._checksum_path(index_path).unlink(missing_ok=True)

        _invalidate_index_cache()
        return deleted

    def _symbol_to_dict(self, symbol: Symbol) -> dict:
        """Convert Symbol to dict (without source content)."""
        return {
            "id": symbol.id,
            "file": symbol.file,
            "name": symbol.name,
            "qualified_name": symbol.qualified_name,
            "kind": symbol.kind,
            "language": symbol.language,
            "signature": symbol.signature,
            "docstring": symbol.docstring,
            "summary": symbol.summary,
            "decorators": symbol.decorators,
            "keywords": symbol.keywords,
            "parent": symbol.parent,
            "line": symbol.line,
            "end_line": symbol.end_line,
            "byte_offset": symbol.byte_offset,
            "byte_length": symbol.byte_length,
            "content_hash": symbol.content_hash,
        }

    def _index_to_dict(self, index: CodeIndex) -> dict:
        """Convert CodeIndex to dict."""
        return {
            "repo": index.repo,
            "owner": index.owner,
            "name": index.name,
            "indexed_at": index.indexed_at,
            "source_files": index.source_files,
            "languages": index.languages,
            "symbols": index.symbols,
            "index_version": index.index_version,
            "file_hashes": index.file_hashes,
            "git_head": index.git_head,
            "file_summaries": index.file_summaries,
            "source_root": index.source_root,
            "file_languages": index.file_languages,
            "display_name": index.display_name,
            **({} if index.imports is None else {"imports": index.imports}),
            **({"context_metadata": index.context_metadata} if index.context_metadata else {}),
            **({"file_blob_shas": index.file_blob_shas} if index.file_blob_shas else {}),
            **({"file_mtimes": index.file_mtimes} if index.file_mtimes else {}),
        }
