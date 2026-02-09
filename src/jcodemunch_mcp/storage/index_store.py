"""Index storage with save/load and byte-offset content retrieval."""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..parser.symbols import Symbol


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

    def get_symbol(self, symbol_id: str) -> Optional[dict]:
        """Find a symbol by ID."""
        for sym in self.symbols:
            if sym.get("id") == symbol_id:
                return sym
        return None

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

    def _index_path(self, owner: str, name: str) -> Path:
        """Path to index JSON file."""
        return self.base_path / f"{owner}-{name}.json"

    def _content_dir(self, owner: str, name: str) -> Path:
        """Path to raw content directory."""
        return self.base_path / f"{owner}-{name}"

    def save_index(
        self,
        owner: str,
        name: str,
        source_files: list[str],
        symbols: list[Symbol],
        raw_files: dict[str, str],
        languages: dict[str, int]
    ) -> CodeIndex:
        """Save index and raw files to storage.
        
        Args:
            owner: Repository owner
            name: Repository name
            source_files: List of indexed file paths
            symbols: List of Symbol objects
            raw_files: Dict mapping file path to raw content
            languages: Dict mapping language to file count
        
        Returns:
            CodeIndex object
        """
        from datetime import datetime
        
        # Create index
        index = CodeIndex(
            repo=f"{owner}/{name}",
            owner=owner,
            name=name,
            indexed_at=datetime.now().isoformat(),
            source_files=source_files,
            languages=languages,
            symbols=[self._symbol_to_dict(s) for s in symbols]
        )
        
        # Save index JSON
        index_path = self._index_path(owner, name)
        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(self._index_to_dict(index), f, indent=2)
        
        # Save raw files
        content_dir = self._content_dir(owner, name)
        content_dir.mkdir(parents=True, exist_ok=True)
        
        for file_path, content in raw_files.items():
            file_dest = content_dir / file_path
            file_dest.parent.mkdir(parents=True, exist_ok=True)
            with open(file_dest, "w", encoding="utf-8") as f:
                f.write(content)
        
        return index

    def load_index(self, owner: str, name: str) -> Optional[CodeIndex]:
        """Load index from storage."""
        index_path = self._index_path(owner, name)
        
        if not index_path.exists():
            return None
        
        with open(index_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        return CodeIndex(
            repo=data["repo"],
            owner=data["owner"],
            name=data["name"],
            indexed_at=data["indexed_at"],
            source_files=data["source_files"],
            languages=data["languages"],
            symbols=data["symbols"]
        )

    def get_symbol_content(self, owner: str, name: str, symbol_id: str) -> Optional[str]:
        """Read symbol source using stored byte offsets.
        
        This is O(1) - no re-parsing, just seek + read.
        """
        index = self.load_index(owner, name)
        if not index:
            return None
        
        symbol = index.get_symbol(symbol_id)
        if not symbol:
            return None
        
        file_path = self._content_dir(owner, name) / symbol["file"]
        
        if not file_path.exists():
            return None
        
        with open(file_path, "rb") as f:
            f.seek(symbol["byte_offset"])
            source_bytes = f.read(symbol["byte_length"])
        
        return source_bytes.decode("utf-8")

    def list_repos(self) -> list[dict]:
        """List all indexed repositories."""
        repos = []
        
        for index_file in self.base_path.glob("*.json"):
            try:
                with open(index_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                
                repos.append({
                    "repo": data["repo"],
                    "indexed_at": data["indexed_at"],
                    "symbol_count": len(data["symbols"]),
                    "file_count": len(data["source_files"]),
                    "languages": data["languages"]
                })
            except Exception:
                continue
        
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
            import shutil
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
        }
