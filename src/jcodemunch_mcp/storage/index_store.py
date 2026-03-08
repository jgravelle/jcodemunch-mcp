"""Index storage with save/load, byte-offset content retrieval, and incremental indexing."""

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..parser.symbols import Symbol

# Bump this when the index schema changes in an incompatible way.
INDEX_VERSION = 4


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
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
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
        return not self.source_files or file_path in self._source_file_set

    def search(self, query: str, kind: Optional[str] = None, file_pattern: Optional[str] = None) -> list[dict]:
        """Search symbols with weighted scoring."""
        query_lower = query.lower()
        query_words = set(query_lower.split())

        scored = []
        for sym in self.symbols:
            # Apply filters
            if kind and sym.get("kind") != kind:
                continue
            if file_pattern and not self._match_pattern(sym.get("file", ""), file_pattern):
                continue

            # Score symbol
            score = self._score_symbol(sym, query_lower, query_words)
            if score > 0:
                scored.append((score, sym))

        # Sort by score descending
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
    ) -> "CodeIndex":
        """Save index and raw files to storage."""
        normalized_source_files = sorted(dict.fromkeys(source_files or list(raw_files.keys())))
        serialized_symbols = [self._symbol_to_dict(s) for s in symbols]
        merged_file_languages = self._file_languages_for_paths(
            normalized_source_files,
            serialized_symbols,
            existing=file_languages,
        )
        resolved_languages = languages or self._languages_from_file_languages(merged_file_languages)

        # Compute file hashes if not provided
        if file_hashes is None:
            file_hashes = {fp: _file_hash(content) for fp, content in raw_files.items()}

        # Create index
        index = CodeIndex(
            repo=f"{owner}/{name}",
            owner=owner,
            name=name,
            indexed_at=datetime.now().isoformat(),
            source_files=normalized_source_files,
            languages=resolved_languages,
            symbols=serialized_symbols,
            index_version=INDEX_VERSION,
            file_hashes=file_hashes,
            git_head=git_head,
            file_summaries=file_summaries or {},
            source_root=source_root,
            file_languages=merged_file_languages,
            display_name=display_name or name,
        )

        # Save index JSON atomically: write to temp then rename
        index_path = self._index_path(owner, name)
        tmp_path = index_path.with_suffix(".json.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(self._index_to_dict(index), f, indent=2)
        tmp_path.replace(index_path)

        # Save raw files
        content_dir = self._content_dir(owner, name)
        content_dir.mkdir(parents=True, exist_ok=True)

        for file_path, content in raw_files.items():
            file_dest = self._safe_content_path(content_dir, file_path)
            if not file_dest:
                raise ValueError(f"Unsafe file path in raw_files: {file_path}")
            file_dest.parent.mkdir(parents=True, exist_ok=True)
            self._write_cached_text(file_dest, content)

        return index

    def load_index(self, owner: str, name: str) -> Optional[CodeIndex]:
        """Load index from storage. Rejects incompatible versions."""
        index_path = self._index_path(owner, name)

        if not index_path.exists():
            return None

        with open(index_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Version check
        stored_version = data.get("index_version", 1)
        if stored_version > INDEX_VERSION:
            return None  # Future version we can't read

        repo_id, stored_owner, stored_name = self._repo_metadata_from_data(data, owner, name)
        source_files = data.get("source_files", [])
        symbols = data.get("symbols", [])
        file_languages = self._file_languages_for_paths(
            source_files,
            symbols,
            existing=data.get("file_languages"),
        )
        languages = self._languages_from_file_languages(file_languages)
        if not languages:
            languages = data.get("languages", {})

        return CodeIndex(
            repo=repo_id,
            owner=stored_owner,
            name=stored_name,
            indexed_at=data["indexed_at"],
            source_files=source_files,
            languages=languages,
            symbols=symbols,
            index_version=stored_version,
            file_hashes=data.get("file_hashes", {}),
            git_head=data.get("git_head", ""),
            file_summaries=data.get("file_summaries", {}),
            source_root=data.get("source_root", ""),
            file_languages=file_languages,
            display_name=data.get("display_name", stored_name),
        )

    def get_symbol_content(self, owner: str, name: str, symbol_id: str, _index: Optional["CodeIndex"] = None) -> Optional[str]:
        """Read symbol source using stored byte offsets.

        Pass _index to avoid a redundant load_index() call when the caller
        already holds a loaded index.
        """
        index = _index or self.load_index(owner, name)
        if not index:
            return None

        symbol = index.get_symbol(symbol_id)
        if not symbol:
            return None

        file_path = self._safe_content_path(self._content_dir(owner, name), symbol["file"])
        if not file_path:
            return None

        if not file_path.exists():
            return None

        with open(file_path, "rb") as f:
            f.seek(symbol["byte_offset"])
            source_bytes = f.read(symbol["byte_length"])

        return source_bytes.decode("utf-8", errors="replace")

    def get_file_content(
        self,
        owner: str,
        name: str,
        file_path: str,
        _index: Optional["CodeIndex"] = None,
    ) -> Optional[str]:
        """Read a cached file's full content."""
        index = _index or self.load_index(owner, name)
        if not index or not index.has_source_file(file_path):
            return None

        content_path = self._safe_content_path(self._content_dir(owner, name), file_path)
        if not content_path or not content_path.exists():
            return None

        return self._read_cached_text(content_path)

    def detect_changes(
        self,
        owner: str,
        name: str,
        current_files: dict[str, str],
    ) -> tuple[list[str], list[str], list[str]]:
        """Detect changed, new, and deleted files by comparing hashes."""
        index = self.load_index(owner, name)
        if not index:
            return [], list(current_files.keys()), []

        old_hashes = index.file_hashes
        current_hashes = {fp: _file_hash(content) for fp, content in current_files.items()}

        old_set = set(old_hashes.keys())
        new_set = set(current_hashes.keys())

        new_files = list(new_set - old_set)
        deleted_files = list(old_set - new_set)
        changed_files = [
            fp for fp in (old_set & new_set)
            if old_hashes[fp] != current_hashes[fp]
        ]

        return changed_files, new_files, deleted_files

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
    ) -> Optional[CodeIndex]:
        """Incrementally update an existing index.

        Removes symbols for deleted/changed files, adds new symbols,
        updates raw content, and saves atomically.
        """
        index = self.load_index(owner, name)
        if not index:
            return None

        # Remove symbols for deleted and changed files
        files_to_remove = set(deleted_files) | set(changed_files)
        kept_symbols = [s for s in index.symbols if s.get("file") not in files_to_remove]

        # Add new symbols
        new_symbol_dicts = [self._symbol_to_dict(s) for s in new_symbols]
        all_symbols_dicts = kept_symbols + new_symbol_dicts

        changed_or_new_files = sorted(set(changed_files) | set(new_files))
        merged_file_languages = dict(index.file_languages)
        for file_path in deleted_files:
            merged_file_languages.pop(file_path, None)
        merged_file_languages.update(
            self._file_languages_for_paths(
                changed_or_new_files,
                new_symbol_dicts,
                existing={**index.file_languages, **(file_languages or {})},
            )
        )

        recomputed_languages = self._languages_from_file_languages(merged_file_languages)
        if not recomputed_languages and languages:
            recomputed_languages = languages

        # Update source files list
        old_files = set(index.source_files)
        for f in deleted_files:
            old_files.discard(f)
        for f in new_files:
            old_files.add(f)
        for f in changed_files:
            old_files.add(f)

        # Update file hashes
        file_hashes = dict(index.file_hashes)
        for f in deleted_files:
            file_hashes.pop(f, None)
        for fp, content in raw_files.items():
            file_hashes[fp] = _file_hash(content)

        # Merge file summaries: keep old, remove deleted, update changed/new
        merged_summaries = dict(index.file_summaries)
        for f in deleted_files:
            merged_summaries.pop(f, None)
        for f in changed_or_new_files:
            merged_summaries.pop(f, None)
        if file_summaries:
            merged_summaries.update(file_summaries)

        # Build updated index
        updated_source_files = sorted(old_files)
        updated = CodeIndex(
            repo=f"{owner}/{name}",
            owner=owner,
            name=name,
            indexed_at=datetime.now().isoformat(),
            source_files=updated_source_files,
            languages=recomputed_languages,
            symbols=all_symbols_dicts,
            index_version=INDEX_VERSION,
            file_hashes=file_hashes,
            git_head=git_head,
            file_summaries=merged_summaries,
            source_root=index.source_root,
            file_languages={fp: merged_file_languages[fp] for fp in updated_source_files if fp in merged_file_languages},
            display_name=index.display_name,
        )

        # Save atomically
        index_path = self._index_path(owner, name)
        tmp_path = index_path.with_suffix(".json.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(self._index_to_dict(updated), f, indent=2)
        tmp_path.replace(index_path)

        # Update raw files
        content_dir = self._content_dir(owner, name)
        content_dir.mkdir(parents=True, exist_ok=True)

        # Remove deleted files from content dir
        for fp in deleted_files:
            dead = self._safe_content_path(content_dir, fp)
            if not dead:
                continue
            if dead.exists():
                dead.unlink()

        # Write changed + new files
        for fp, content in raw_files.items():
            dest = self._safe_content_path(content_dir, fp)
            if not dest:
                raise ValueError(f"Unsafe file path in raw_files: {fp}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            self._write_cached_text(dest, content)

        return updated

    def _languages_from_symbols(self, symbols: list[dict]) -> dict[str, int]:
        """Compute language->file_count from serialized symbols."""
        return self._languages_from_file_languages(self._file_languages_from_symbols(symbols))

    def list_repos(self) -> list[dict]:
        """List all indexed repositories."""
        repos = []

        for index_file in self.base_path.glob("*.json"):
            try:
                with open(index_file, "r", encoding="utf-8") as f:
                    data = json.load(f)

                repo_id = data.get("repo")
                if not repo_id:
                    continue
                repo_entry = {
                    "repo": data["repo"],
                    "indexed_at": data["indexed_at"],
                    "symbol_count": len(data.get("symbols", [])),
                    "file_count": len(data.get("source_files", [])),
                    "languages": data.get("languages", {}),
                    "index_version": data.get("index_version", 1),
                }
                if data.get("display_name"):
                    repo_entry["display_name"] = data["display_name"]
                if data.get("source_root"):
                    repo_entry["source_root"] = data["source_root"]
                repos.append(repo_entry)
            except Exception:
                continue

        repos.sort(key=lambda repo: repo["repo"])
        return repos

    def delete_index(self, owner: str, name: str) -> bool:
        """Delete an index and its raw files."""
        index_path = self._index_path(owner, name)
        content_dir = self._content_dir(owner, name)

        deleted = False

        if index_path.exists():
            index_path.unlink()
            deleted = True

        if content_dir.exists():
            shutil.rmtree(content_dir)
            deleted = True

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
        }
