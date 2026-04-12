"""plan_refactoring — edit-ready refactoring plans."""
from __future__ import annotations

import logging
import re
from pathlib import PurePosixPath
from typing import Optional, Tuple

from ..storage import IndexStore

# Reused from existing tools (no duplication)
from .get_blast_radius import (
    _build_reverse_adjacency,
    _bfs_importers,
    _name_in_content,
)
from ._call_graph import _symbol_body
from ..storage import record_savings

logger = logging.getLogger(__name__)

# Fallback: match any line containing the symbol name (conservative, catches edge cases)
_DEFAULT_IMPORT_PATTERN = re.compile(r".")

# Language-specific import line patterns
_IMPORT_PATTERNS = {
    # -- Tier 1: full import extraction in imports.py --
    "python": re.compile(r"^\s*(from\s+\S+\s+import\s|import\s)"),
    "typescript": re.compile(r"^\s*(import\s|const\s+\w+\s*=\s*require\()"),
    "javascript": re.compile(r"^\s*(import\s|const\s+\w+\s*=\s*require\()"),
    "tsx": re.compile(r"^\s*(import\s|const\s+\w+\s*=\s*require\()"),
    "jsx": re.compile(r"^\s*(import\s|const\s+\w+\s*=\s*require\()"),
    "vue": re.compile(r"^\s*(import\s|const\s+\w+\s*=\s*require\()"),
    "rust": re.compile(r"^\s*use\s"),
    "go": re.compile(r"^\s*import\s"),
    "java": re.compile(r"^\s*import\s+(static\s+)?[\w.]+\s*;?$"),
    "kotlin": re.compile(r"^\s*import\s+[\w.]+"),
    "csharp": re.compile(r"^\s*using\s+(static\s+)?([\w.]+\s*=\s*)?[\w.]+\s*;"),
    "php": re.compile(r"^\s*(use\s+[\w\\]+|(?:require|include)(?:_once)?\s+['\"])"),
    "ruby": re.compile(r"^\s*(require|require_relative)\s+['\"]"),
    "c": re.compile(r"^\s*#\s*include\s+[<\"]"),
    "cpp": re.compile(r"^\s*#\s*include\s+[<\"]"),
    "objc": re.compile(r"^\s*#\s*(include|import)\s+[<\"]"),
    "swift": re.compile(r"^\s*import\s+\w+"),
    "scala": re.compile(r"^\s*import\s+[\w.{]"),
    "haskell": re.compile(r"^\s*import\s+(qualified\s+)?"),
    "dart": re.compile(r"^\s*(import|export)\s+['\"]"),
    "asm": re.compile(r"^\s*[.%]include\s+", re.IGNORECASE),
    "sql": re.compile(r"\{\{[\s-]*ref\s*\("),  # dbt {{ ref('model') }}
    # -- Tier 2: no import extractor, but recognizable import syntax --
    "elixir": re.compile(r"^\s*(import|alias|require|use)\s+[A-Z]"),
    "perl": re.compile(r"^\s*(use|require)\s+[\w:]+"),
    "lua": re.compile(r"^\s*(require|local\s+\w+\s*=\s*require)\s*[\('\"]"),
    "luau": re.compile(r"^\s*(require|local\s+\w+\s*=\s*require)\s*[\('\"]"),
    "groovy": re.compile(r"^\s*import\s+[\w.]+"),
    "proto": re.compile(r"^\s*import\s+['\"]"),
    "julia": re.compile(r"^\s*(using|import)\s+[\w.]"),
    "r": re.compile(r"^\s*(library|require)\s*\("),
    "gdscript": re.compile(r"^\s*(preload|load)\s*\("),
    "gleam": re.compile(r"^\s*import\s+[\w/]"),
    "fortran": re.compile(r"^\s*use\s+\w+", re.IGNORECASE),
    "graphql": re.compile(r"^\s*#\s*import\s+"),  # graphql-import convention
}

# Definition patterns per language
_DEF_PATTERNS = {
    # -- Tier 1: languages with import extractors --
    "python": re.compile(r"^\s*(class|def|async\s+def)\s+{name}\b"),
    "typescript": re.compile(r"^\s*(export\s+)?(class|function|const|let|var|interface|type|enum)\s+{name}\b"),
    "javascript": re.compile(r"^\s*(export\s+)?(class|function|const|let|var)\s+{name}\b"),
    "tsx": re.compile(r"^\s*(export\s+)?(class|function|const|let|var|interface|type|enum)\s+{name}\b"),
    "jsx": re.compile(r"^\s*(export\s+)?(class|function|const|let|var)\s+{name}\b"),
    "rust": re.compile(r"^\s*(pub(\s*\([^)]*\))?\s+)?(fn|struct|enum|trait|type|const|static|mod)\s+{name}\b"),
    "go": re.compile(r"^\s*(func|type|var|const)\s+{name}\b"),
    "java": re.compile(r"^\s*(public|private|protected)?\s*(static\s+)?(abstract\s+)?(final\s+)?(class|interface|enum|record|@interface)\s+{name}\b"),
    "kotlin": re.compile(r"^\s*(public|private|protected|internal)?\s*(data\s+|sealed\s+|abstract\s+|open\s+)?(class|interface|object|enum\s+class|fun)\s+{name}\b"),
    "csharp": re.compile(r"^\s*(public|private|protected|internal)?\s*(static\s+)?(partial\s+)?(class|struct|interface|enum|record|delegate)\s+{name}\b"),
    "php": re.compile(r"^\s*(abstract\s+)?(final\s+)?(class|interface|trait|enum|function)\s+{name}\b"),
    "ruby": re.compile(r"^\s*(class|module|def)\s+{name}\b"),
    "c": re.compile(r"^\s*(struct|enum|union|typedef)\s+{name}\b"),
    "cpp": re.compile(r"^\s*(class|struct|enum|union|namespace|template)\s+{name}\b"),
    "objc": re.compile(r"^\s*@(interface|implementation|protocol)\s+{name}\b"),
    "swift": re.compile(r"^\s*(public\s+|private\s+|internal\s+|open\s+|fileprivate\s+)?(class|struct|enum|protocol|func|extension|typealias|actor)\s+{name}\b"),
    "scala": re.compile(r"^\s*(private\s+|protected\s+)?(abstract\s+|sealed\s+|case\s+)?(class|object|trait|def|val|var|type|enum)\s+{name}\b"),
    "haskell": re.compile(r"^\s*(data|type|newtype|class)\s+{name}\b"),
    "dart": re.compile(r"^\s*(abstract\s+)?(class|mixin|enum|extension|typedef)\s+{name}\b"),
    # -- Tier 2: languages with tree-sitter but no import extractors --
    "elixir": re.compile(r"^\s*(defmodule|def|defp|defmacro|defmacrop|defstruct|defguard|defdelegate)\s+{name}\b"),
    "perl": re.compile(r"^\s*(sub|package)\s+{name}\b"),
    "lua": re.compile(r"^\s*(local\s+)?function\s+(\w+[.:])?\s*{name}\b"),
    "luau": re.compile(r"^\s*(local\s+)?function\s+(\w+[.:])?\s*{name}\b"),
    "groovy": re.compile(r"^\s*(public|private|protected)?\s*(static\s+)?(class|interface|enum|def)\s+{name}\b"),
    "gleam": re.compile(r"^\s*(pub\s+)?(fn|type|const)\s+{name}\b"),
    "fortran": re.compile(r"^\s*(subroutine|function|module|program|type)\s+{name}\b"),
    "erlang": re.compile(r"^{name}\s*\("),  # Erlang: function_name(args) ->
    "julia": re.compile(r"^\s*(function|struct|abstract\s+type|mutable\s+struct|module|macro)\s+{name}\b"),
    "r": re.compile(r"^\s*{name}\s*(<-|=)\s*function\s*\("),
    "gdscript": re.compile(r"^\s*(func|class|signal|enum)\s+{name}\b"),
    "bash": re.compile(r"^\s*(function\s+{name}|{name}\s*\(\s*\))"),
    "proto": re.compile(r"^\s*(message|service|enum|rpc)\s+{name}\b"),
    "hcl": re.compile(r"^\s*(resource|data|module|variable|output)\s+\"[^\"]*\"\s+\"{name}\""),
    "graphql": re.compile(r"^\s*(type|query|mutation|subscription|interface|enum|scalar|union|input|fragment|directive)\s+{name}\b"),
    "autohotkey": re.compile(r"^\s*(class\s+{name}|{name}\s*\()"),
}

# Non-code file extensions for warning scans
_NON_CODE_EXTENSIONS = {".yaml", ".yml", ".json", ".toml", ".env", ".md", ".txt", ".cfg", ".ini", ".xml"}

# Common TypeScript/JavaScript path aliases (Fix A)
_PATH_ALIAS_PATTERNS = {
    "@": re.compile(r"['\"]@/"),       # @/ alias (common in Vue, Next.js)
    "$lib": re.compile(r"['\"]\$lib/"), # $lib alias (SvelteKit)
    "~": re.compile(r"['\"]~/"),       # ~ alias (common in webpack configs)
    "#": re.compile(r"['\"]#/"),       # # alias (some configs)
}

# TypeScript overload signature pattern (Fix D)
_TS_OVERLOAD_PATTERN = re.compile(r"^\s*(export\s+)?function\s+\w+\s*\(.*\)\s*:")


def _detect_line_sep(content: str) -> str:
    """Return the line separator used in content: \\r\\n or \\n."""
    return "\r\n" if "\r\n" in content else "\n"


def _file_to_module(file_path: str) -> str:
    """Convert a Python file path to a dot-separated module path.

    Handles __init__.py: pkg/__init__.py -> pkg (not pkg.__init__).
    """
    path = PurePosixPath(file_path)
    if path.name == "__init__.py":
        return path.parent.as_posix().replace("/", ".")
    return path.with_suffix("").as_posix().replace("/", ".")


def _capture_multiline_sig(
    lines: list[str], def_line_idx: int, content: str,
) -> tuple[list[str], str, int]:
    """Capture a multi-line signature via paren balancing.

    Returns (sig_lines, line_sep, sig_end_idx).
    """
    first_line = lines[def_line_idx]
    sig_lines = [first_line.rstrip()]
    paren_depth = first_line.count("(") - first_line.count(")")
    current_idx = def_line_idx + 1

    while paren_depth > 0 and current_idx < len(lines):
        next_line = lines[current_idx]
        sig_lines.append(next_line.rstrip())
        paren_depth += next_line.count("(") - next_line.count(")")
        current_idx += 1

    line_sep = _detect_line_sep(content)
    sig_end_idx = def_line_idx + len(sig_lines) - 1
    return sig_lines, line_sep, sig_end_idx


def plan_refactoring(
    repo: str,
    symbol: str,
    refactor_type: str,
    new_name: Optional[str] = None,
    new_file: Optional[str] = None,
    new_signature: Optional[str] = None,
    depth: int = 2,
    storage_path: Optional[str] = None,
) -> dict:
    """Generate an edit-ready refactoring plan."""
    store = IndexStore(storage_path)
    owner, name = repo.split("/", 1)
    index = store.load_index(owner, name)
    if index is None:
        return {"error": f"No index found for {repo}"}

    depth = max(1, min(3, depth))

    # Resolve symbol(s)
    if refactor_type == "extract":
        sym_names = [s.strip() for s in symbol.split(",")]
        syms = []
        for sn in sym_names:
            resolved = _resolve_symbol(index, sn)
            if isinstance(resolved, dict) and "error" in resolved:
                return resolved
            syms.append(resolved)
    else:
        sym = _resolve_symbol(index, symbol)
        if isinstance(sym, dict) and "error" in sym:
            return sym

    # Dispatch by type
    if refactor_type == "rename":
        if not new_name:
            return {"error": "new_name required for rename"}
        return _plan_rename(index, store, owner, name, sym, new_name, depth)

    elif refactor_type == "move":
        if not new_file:
            return {"error": "new_file required for move"}
        return _plan_move(index, store, owner, name, sym, new_file, depth)

    elif refactor_type == "extract":
        if not new_file:
            return {"error": "new_file required for extract"}
        return _plan_extract(index, store, owner, name, syms, new_file, depth)

    elif refactor_type == "signature":
        if not new_signature:
            return {"error": "new_signature required for signature"}
        return _plan_signature_change(index, store, owner, name, sym, new_signature, depth)

    else:
        return {"error": f"Unknown refactor_type: {refactor_type}. Use: rename, move, extract, signature"}


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------

def _resolve_symbol(index, symbol_id_or_name: str) -> dict:
    """Resolve a symbol name or ID to its dict. Returns {"error": ...} on failure."""
    # Exact ID match first (O(1))
    sym = index.get_symbol(symbol_id_or_name)
    if sym:
        return sym
    # Bare name fallback (linear scan)
    matches = [s for s in index.symbols if s.get("name") == symbol_id_or_name]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        ids = [m["id"] for m in matches[:5]]
        return {"error": f"Ambiguous symbol '{symbol_id_or_name}'. Matches: {ids}"}
    return {"error": f"Symbol not found: {symbol_id_or_name}"}


def _find_affected_files(index, store, owner, name, sym_file, sym_name, depth):
    """Find files that import sym_file AND reference sym_name."""
    source_files = frozenset(index.source_files)
    rev = _build_reverse_adjacency(index.imports, source_files, index.alias_map, getattr(index, "psr4_map", None))
    importer_files, _ = _bfs_importers(sym_file, rev, depth)

    confirmed = []
    for imp_file in importer_files:
        content, read_error = _get_file_content_safe(store, owner, name, imp_file)
        # Fix F: Don't skip files with read errors - just don't confirm them
        if read_error:
            logger.debug(f"Could not read importer file {imp_file}: {read_error}")
            continue
        if content and _name_in_content(content, sym_name):
            confirmed.append(imp_file)
    return confirmed


def _apply_word_replacement(text: str, old_name: str, new_name: str) -> str:
    """Replace old_name with new_name at word boundaries."""
    return re.sub(r"\b" + re.escape(old_name) + r"\b", new_name, text)


def _classify_line(line_text: str, old_name: str, language: str) -> str:
    """Classify a matching line as import/definition/usage/string."""
    stripped = line_text.strip()
    
    # Fix E: Check if inside f-string or template literal interpolation FIRST
    if _is_inside_interpolation(line_text, old_name, language):
        return "usage"  # Interpolations are usages, not strings
    
    # Check if symbol appears outside string context - if so, it's a usage/definition/import
    # We need to find all string boundaries and check if old_name appears outside them
    string_boundaries = []  # List of (start, end) positions of string contents
    
    # Find f-strings first (Python) - they can contain nested quotes in interpolations
    # Bug 15: Handle rf/fr/Fr/fR/RF/FR prefixes (raw f-strings)
    if language == "python":
        fstring_start = re.compile(r"[rR]?[fF]('''|\"\"\"|\"|')|[fF][rR]?('''|\"\"\"|\"|')")
        start = 0
        while True:
            match = fstring_start.search(stripped, start)
            if not match:
                break
            # Bug 15: Handle both regex groups (rf prefix vs fr prefix)
            quote = match.group(1) or match.group(2)
            quote_len = len(quote)
            quote_start = match.start()
            # Find the closing quote, but skip over {..} interpolation content
            search_start = match.end()
            brace_level = 0
            i = search_start
            while i < len(stripped):
                # Fix 5: For triple quotes, compare the full quote substring; for single quotes, compare char
                if quote_len == 1:
                    c = stripped[i]
                    if c == '{':
                        brace_level += 1
                    elif c == '}':
                        if brace_level > 0:
                            brace_level -= 1
                    elif c == quote and brace_level == 0:
                        # Found closing quote at same brace level
                        string_boundaries.append((quote_start, i + 1))
                        start = i + 1
                        break
                else:
                    # Triple quote - compare substring
                    if stripped[i:i+quote_len] == quote and brace_level == 0:
                        string_boundaries.append((quote_start, i + quote_len))
                        start = i + quote_len
                        break
                    c = stripped[i]
                    if c == '{':
                        brace_level += 1
                    elif c == '}':
                        if brace_level > 0:
                            brace_level -= 1
                i += 1
            else:
                # No closing quote found - unclosed f-string
                break
    
    # Find triple-quoted strings first
    for triple_q in ('"""', "'''"):
        start = 0
        while True:
            idx = stripped.find(triple_q, start)
            if idx < 0:
                break
            # Fix 3: Skip if already inside an f-string boundary
            in_fstring = any(start <= idx < end for start, end in string_boundaries)
            if in_fstring:
                start = idx + 1
                continue
            end_idx = stripped.find(triple_q, idx + 3)
            if end_idx >= 0:
                string_boundaries.append((idx, end_idx + 3))
                start = end_idx + 3
            else:
                break
    
    # Find single-quoted strings
    # Bug 1: Fix escaped closing quote handling - use inner loop to find non-escaped closing quote
    for q in ('"', "'", '`'):
        start = 0
        while True:
            idx = stripped.find(q, start)
            if idx < 0:
                break
            # Skip if inside a triple-quoted string or f-string (already processed)
            in_existing = any(s <= idx < e for s, e in string_boundaries)
            if in_existing:
                start = idx + 1
                continue
            
            # Bug 3: Check for escaped opening quotes - count preceding backslashes
            if idx > 0:
                backslash_count = 0
                k = idx - 1
                while k >= 0 and stripped[k] == '\\':
                    backslash_count += 1
                    k -= 1
                # If odd number of backslashes, quote is escaped - skip it
                if backslash_count % 2 == 1:
                    start = idx + 1
                    continue
            
            # Bug 1/B-5: Inner loop to find non-escaped closing quote for the same string
            search_pos = idx + 1
            while True:
                end_idx = stripped.find(q, search_pos)
                if end_idx < 0:
                    # No closing quote found - advance past opening quote to avoid infinite loop
                    start = idx + 1
                    break
                # Check if closing quote is escaped
                if end_idx > 0:
                    backslash_count = 0
                    k = end_idx - 1
                    while k >= 0 and stripped[k] == '\\':
                        backslash_count += 1
                        k -= 1
                    # If odd number of backslashes, closing quote is escaped - continue searching
                    if backslash_count % 2 == 1:
                        search_pos = end_idx + 1
                        continue
                # Found non-escaped closing quote
                string_boundaries.append((idx, end_idx + 1))
                start = end_idx + 1
                break  # Exit inner loop, continue outer loop
            else:
                # Inner loop exhausted without finding closing quote
                break  # Exit outer loop
    
    # Check if old_name appears outside all string boundaries
    # Use word boundary matching
    pattern = re.compile(r"\b" + re.escape(old_name) + r"\b")
    for match in pattern.finditer(stripped):
        match_start, match_end = match.start(), match.end()
        # Check if this occurrence is outside ALL string boundaries
        outside_strings = True
        for str_start, str_end in string_boundaries:
            if str_start <= match_start < str_end:
                outside_strings = False
                break
        if outside_strings:
            # Symbol appears outside string context - check if it's definition/import/usage
            pat = _IMPORT_PATTERNS.get(language)
            if pat and pat.match(stripped):
                return "import"
            def_pat = _DEF_PATTERNS.get(language)
            if def_pat:
                concrete = re.compile(def_pat.pattern.format(name=re.escape(old_name)))
                if concrete.match(stripped):
                    return "definition"
            return "usage"
    
    # Check import/definition patterns before classifying as "string"
    # (Ruby require, Go import, PHP require have identifiers inside quotes)
    pat = _IMPORT_PATTERNS.get(language)
    if pat and pat.match(stripped):
        return "import"

    def_pat = _DEF_PATTERNS.get(language)
    if def_pat:
        concrete = re.compile(def_pat.pattern.format(name=re.escape(old_name)))
        if concrete.match(stripped):
            return "definition"

    # Symbol only appears inside string boundaries → "string"
    for str_start, str_end in string_boundaries:
        between = stripped[str_start:str_end]
        if old_name in between:
            return "string"

    return "usage"


def _scan_non_code_files(store, owner, name, index, old_name):
    """Scan non-code files for word-boundary matches -> warnings."""
    warnings = []
    pattern = re.compile(r"\b" + re.escape(old_name) + r"\b")
    for fpath in index.source_files:
        ext = PurePosixPath(fpath).suffix.lower()
        if ext not in _NON_CODE_EXTENSIONS:
            continue
        content, read_error = _get_file_content_safe(store, owner, name, fpath)
        if read_error:
            warnings.append({"file": fpath, "reason": "file_read_error", "error": read_error})
            continue
        if not content:
            continue
        for i, line in enumerate(content.splitlines()):
            if pattern.search(line):
                warnings.append({"file": fpath, "line": i + 1, "text": line.rstrip(), "reason": "non-code file"})
    return warnings


# ---------------------------------------------------------------------------
# Fix A: Path alias detection and warning
# ---------------------------------------------------------------------------

def _detect_path_alias(import_line: str) -> Optional[str]:
    """Detect if an import line uses a path alias like @/, $lib/, ~/."""
    for alias_name, pattern in _PATH_ALIAS_PATTERNS.items():
        if pattern.search(import_line):
            return alias_name
    return None


def _resolve_path_alias(alias: str, import_line: str, old_file: str) -> Optional[str]:
    """Attempt to resolve a path alias to a file path.
    
    Returns the resolved path if resolution is unambiguous, or None if it requires
    tsconfig.json analysis (e.g., @ alias could mean src/, app/, or root/).
    
    Note: This function is conservative - it only returns a resolved path when
    the mapping is standardized across most projects. For @/, which varies by
    project configuration, we return None and let the caller warn about manual rewrite.
    """
    # Extract the import specifier from the line
    match = re.search(r"['\"]([^'\"]+)['\"]", import_line)
    if not match:
        return None
    
    specifier = match.group(1)
    
    # Only resolve aliases with standardized mappings
    # $lib in SvelteKit consistently maps to src/lib
    if alias == "$lib" and specifier.startswith("$lib/"):
        resolved = specifier.replace("$lib/", "src/lib/")
        return resolved
    
    # ~ is ambiguous (could be src/, root/, or project root/) - don't guess
    # @ is highly ambiguous (@ → src/, @ → app/, @ → root/) - don't guess
    # These require tsconfig.json analysis which we don't do
    
    return None


def _compute_new_import(old_import_line, old_file, new_file, sym_name, language) -> Tuple[str, Optional[str]]:
    """Rewrite an import line from old_file to new_file.
    
    Returns (new_line, warning) tuple:
    - (new_line, None) if rewrite succeeded
    - (old_import_line, warning_message) if rewrite not possible
    
    Fix A: Detects path aliases and warns when they can't be rewritten.
    """
    warning = None
    
    if language == "python":
        old_module = _file_to_module(old_file)
        new_module = _file_to_module(new_file)
        if old_module in old_import_line:
            return old_import_line.replace(old_module, new_module), None
        return old_import_line, f"Python module path '{old_module}' not found in import line"
    
    elif language in ("typescript", "javascript"):
        # Fix A: Check for path aliases first
        alias = _detect_path_alias(old_import_line)
        if alias:
            # Attempt to resolve the alias
            resolved = _resolve_path_alias(alias, old_import_line, old_file)
            if resolved:
                # Check if resolved path matches old_file (without extension)
                old_spec = PurePosixPath(old_file).with_suffix("").as_posix()
                # Handle cases where resolved might or might not include extension
                resolved_no_ext = resolved.removesuffix(".ts").removesuffix(".js").removesuffix(".tsx").removesuffix(".jsx")
                if resolved == old_spec or resolved_no_ext == old_spec:
                    # We can rewrite: replace the alias specifier with new path (keeping alias prefix)
                    match = re.search(r"['\"]([^'\"]+)['\"]", old_import_line)
                    if match:
                        old_specifier = match.group(1)
                        # Extract the path part after the alias prefix
                        # @/models/user -> models/user (the part that maps to src/models/user)
                        # We need to replace this with the new path part
                        alias_prefix = f"{alias}/"
                        if old_specifier.startswith(alias_prefix):
                            # Get the path after the alias prefix
                            alias_path = old_specifier[len(alias_prefix):]
                            # Build new specifier: alias_prefix + new_path
                            # The new path should be relative to the alias root (e.g., src/)
                            new_spec = PurePosixPath(new_file).with_suffix("").as_posix()
                            # If new file starts with src/, convert to alias path
                            if new_spec.startswith("src/"):
                                new_alias_path = new_spec[4:]  # Remove src/ prefix
                            else:
                                new_alias_path = new_spec
                            new_alias_spec = f"{alias_prefix}{new_alias_path}"
                            return old_import_line.replace(old_specifier, new_alias_spec), None
            # Can't resolve alias - return warning
            warning = f"Path alias '{alias}' detected in import. Manual rewrite may be required: '{old_import_line}'"
            return old_import_line, warning
        
        # Standard path rewrite (no alias)
        old_spec = PurePosixPath(old_file).with_suffix("").as_posix()
        new_spec = PurePosixPath(new_file).with_suffix("").as_posix()
        if old_spec in old_import_line:
            return old_import_line.replace(old_spec, new_spec), None
        # Path didn't match - check for relative imports
        warning = f"Import specifier doesn't match file path '{old_spec}'. May be relative import or alias."
        return old_import_line, warning
    
    elif language == "rust":
        # Rust: use crate::old_mod::Symbol; → use crate::new_mod::Symbol;
        # Path separator is :: — convert file paths to :: notation
        def _file_to_rust_path(file_path: str) -> str:
            p = PurePosixPath(file_path).with_suffix("")
            # src/models/user.rs → models::user; lib.rs → crate root
            parts = list(p.parts)
            if parts and parts[0] == "src":
                parts = parts[1:]
            return "::".join(parts)

        old_mod = _file_to_rust_path(old_file)
        new_mod = _file_to_rust_path(new_file)
        if old_mod in old_import_line:
            return old_import_line.replace(old_mod, new_mod), None
        # Try with crate:: prefix stripped from both
        for prefix in ("crate::", "super::", "self::"):
            prefixed_old = f"{prefix}{old_mod}"
            if prefixed_old in old_import_line:
                return old_import_line.replace(prefixed_old, f"{prefix}{new_mod}"), None
        return old_import_line, f"Rust module path '{old_mod}' not found in use statement"

    elif language == "go":
        # Go: import "old/pkg/path" → import "new/pkg/path"
        old_spec = PurePosixPath(old_file).parent.as_posix()
        new_spec = PurePosixPath(new_file).parent.as_posix()
        if old_spec in old_import_line:
            return old_import_line.replace(old_spec, new_spec), None
        # Try with just the file stem (package name = directory name)
        old_dir = PurePosixPath(old_file).parent.name
        new_dir = PurePosixPath(new_file).parent.name
        if old_dir and old_dir in old_import_line:
            return old_import_line.replace(old_dir, new_dir), None
        return old_import_line, f"Go package path '{old_spec}' not found in import"

    elif language == "java" or language == "kotlin":
        # Java/Kotlin: import com.example.old_pkg.Symbol; → com.example.new_pkg.Symbol;
        # Convert file path to dot-separated package: src/main/java/com/example/User.java → com.example.User
        def _file_to_java_pkg(file_path: str) -> str:
            p = PurePosixPath(file_path).with_suffix("")
            parts = list(p.parts)
            # Strip common source roots: src/main/java/, src/main/kotlin/, src/
            for root in (("src", "main", "java"), ("src", "main", "kotlin"), ("src",)):
                if tuple(parts[:len(root)]) == root:
                    parts = parts[len(root):]
                    break
            return ".".join(parts)

        old_pkg = _file_to_java_pkg(old_file)
        new_pkg = _file_to_java_pkg(new_file)
        if old_pkg in old_import_line:
            return old_import_line.replace(old_pkg, new_pkg), None
        return old_import_line, f"Java package path '{old_pkg}' not found in import"

    elif language == "csharp":
        # C#: using OldNamespace.Sub; → using NewNamespace.Sub;
        # Convert file path to dot-separated namespace
        def _file_to_csharp_ns(file_path: str) -> str:
            p = PurePosixPath(file_path).with_suffix("")
            parts = list(p.parts)
            if parts and parts[0] == "src":
                parts = parts[1:]
            return ".".join(parts)

        old_ns = _file_to_csharp_ns(old_file)
        new_ns = _file_to_csharp_ns(new_file)
        if old_ns in old_import_line:
            return old_import_line.replace(old_ns, new_ns), None
        return old_import_line, f"C# namespace '{old_ns}' not found in using statement"

    elif language == "php":
        # PHP: use App\Old\Models\User; → use App\New\Models\User;
        # Convert file path to backslash-separated namespace
        def _file_to_php_ns(file_path: str) -> str:
            p = PurePosixPath(file_path).with_suffix("")
            parts = list(p.parts)
            if parts and parts[0] == "src":
                parts = parts[1:]
            return "\\".join(parts)

        old_ns = _file_to_php_ns(old_file)
        new_ns = _file_to_php_ns(new_file)
        if old_ns in old_import_line:
            return old_import_line.replace(old_ns, new_ns), None
        return old_import_line, f"PHP namespace '{old_ns}' not found in use statement"

    elif language == "ruby":
        # Ruby: require 'old/path' → require 'new/path' (file path based)
        old_spec = PurePosixPath(old_file).with_suffix("").as_posix()
        new_spec = PurePosixPath(new_file).with_suffix("").as_posix()
        if old_spec in old_import_line:
            return old_import_line.replace(old_spec, new_spec), None
        return old_import_line, f"Ruby require path '{old_spec}' not found in require statement"

    elif language in ("c", "cpp", "objc"):
        # C/C++/ObjC: #include "old/path.h" → #include "new/path.h"
        # Only rewrite quoted includes (not angle-bracket system includes)
        old_spec = PurePosixPath(old_file).as_posix()
        new_spec = PurePosixPath(new_file).as_posix()
        if old_spec in old_import_line:
            return old_import_line.replace(old_spec, new_spec), None
        # Try without extension
        old_stem = PurePosixPath(old_file).with_suffix("").as_posix()
        new_stem = PurePosixPath(new_file).with_suffix("").as_posix()
        if old_stem in old_import_line:
            return old_import_line.replace(old_stem, new_stem), None
        return old_import_line, f"Include path '{old_spec}' not found in #include"

    elif language == "swift":
        # Swift: import OldModule → import NewModule
        # Module name = directory name or project name
        old_mod = PurePosixPath(old_file).parent.name or PurePosixPath(old_file).stem
        new_mod = PurePosixPath(new_file).parent.name or PurePosixPath(new_file).stem
        if old_mod in old_import_line:
            return old_import_line.replace(old_mod, new_mod), None
        return old_import_line, f"Swift module '{old_mod}' not found in import"

    elif language == "scala":
        # Scala: import com.example.old.Symbol → import com.example.new.Symbol
        # Same dot-separated convention as Java
        def _file_to_scala_pkg(file_path: str) -> str:
            p = PurePosixPath(file_path).with_suffix("")
            parts = list(p.parts)
            for root in (("src", "main", "scala"), ("src",)):
                if tuple(parts[:len(root)]) == root:
                    parts = parts[len(root):]
                    break
            return ".".join(parts)

        old_pkg = _file_to_scala_pkg(old_file)
        new_pkg = _file_to_scala_pkg(new_file)
        if old_pkg in old_import_line:
            return old_import_line.replace(old_pkg, new_pkg), None
        return old_import_line, f"Scala package path '{old_pkg}' not found in import"

    elif language == "haskell":
        # Haskell: import Data.Old.Module → import Data.New.Module
        # Module path = dot-separated from file path
        def _file_to_haskell_mod(file_path: str) -> str:
            p = PurePosixPath(file_path).with_suffix("")
            parts = list(p.parts)
            if parts and parts[0] == "src":
                parts = parts[1:]
            return ".".join(parts)

        old_mod = _file_to_haskell_mod(old_file)
        new_mod = _file_to_haskell_mod(new_file)
        if old_mod in old_import_line:
            return old_import_line.replace(old_mod, new_mod), None
        return old_import_line, f"Haskell module '{old_mod}' not found in import"

    elif language == "dart":
        # Dart: import 'package:pkg/old/path.dart' → import 'package:pkg/new/path.dart'
        old_spec = PurePosixPath(old_file).as_posix()
        new_spec = PurePosixPath(new_file).as_posix()
        if old_spec in old_import_line:
            return old_import_line.replace(old_spec, new_spec), None
        # Try lib-relative: lib/old/file.dart → old/file.dart in import
        old_lib = PurePosixPath(old_file).as_posix().removeprefix("lib/")
        new_lib = PurePosixPath(new_file).as_posix().removeprefix("lib/")
        if old_lib in old_import_line:
            return old_import_line.replace(old_lib, new_lib), None
        return old_import_line, f"Dart path '{old_spec}' not found in import"

    elif language == "asm":
        # ASM: .include "old/path.asm" → .include "new/path.asm"
        old_spec = PurePosixPath(old_file).as_posix()
        new_spec = PurePosixPath(new_file).as_posix()
        if old_spec in old_import_line:
            return old_import_line.replace(old_spec, new_spec), None
        return old_import_line, f"ASM include path '{old_spec}' not found"

    elif language in ("elixir", "gleam"):
        # Elixir: alias OldModule.Name → alias NewModule.Name
        # Gleam: import old/module → import new/module
        old_spec = PurePosixPath(old_file).with_suffix("").as_posix()
        new_spec = PurePosixPath(new_file).with_suffix("").as_posix()
        if language == "elixir":
            # Elixir modules are dot-separated, capitalized
            old_mod = ".".join(p.capitalize() for p in PurePosixPath(old_file).with_suffix("").parts if p != "lib")
            new_mod = ".".join(p.capitalize() for p in PurePosixPath(new_file).with_suffix("").parts if p != "lib")
        else:
            old_mod = old_spec
            new_mod = new_spec
        if old_mod in old_import_line:
            return old_import_line.replace(old_mod, new_mod), None
        return old_import_line, f"{language.capitalize()} module '{old_mod}' not found in import"

    elif language in ("perl", "groovy"):
        # Perl: use Old::Module; → use New::Module;  (:: separator)
        # Groovy: import old.pkg.Class → import new.pkg.Class (same as Java)
        sep = "::" if language == "perl" else "."
        def _file_to_mod(file_path: str) -> str:
            p = PurePosixPath(file_path).with_suffix("")
            parts = list(p.parts)
            if parts and parts[0] in ("lib", "src"):
                parts = parts[1:]
            return sep.join(parts)

        old_mod = _file_to_mod(old_file)
        new_mod = _file_to_mod(new_file)
        if old_mod in old_import_line:
            return old_import_line.replace(old_mod, new_mod), None
        return old_import_line, f"{language.capitalize()} module '{old_mod}' not found in import"

    elif language in ("lua", "luau"):
        # Lua: require("old.module") or require 'old/module'
        # Try dot-separated first, then slash-separated
        old_dot = PurePosixPath(old_file).with_suffix("").as_posix().replace("/", ".")
        new_dot = PurePosixPath(new_file).with_suffix("").as_posix().replace("/", ".")
        if old_dot in old_import_line:
            return old_import_line.replace(old_dot, new_dot), None
        old_slash = PurePosixPath(old_file).with_suffix("").as_posix()
        new_slash = PurePosixPath(new_file).with_suffix("").as_posix()
        if old_slash in old_import_line:
            return old_import_line.replace(old_slash, new_slash), None
        return old_import_line, f"Lua module path not found in require"

    elif language == "julia":
        # Julia: using OldModule.Sub → using NewModule.Sub
        old_mod = ".".join(PurePosixPath(old_file).with_suffix("").parts)
        new_mod = ".".join(PurePosixPath(new_file).with_suffix("").parts)
        if parts := [p for p in PurePosixPath(old_file).with_suffix("").parts if p != "src"]:
            old_mod = ".".join(parts)
        if parts := [p for p in PurePosixPath(new_file).with_suffix("").parts if p != "src"]:
            new_mod = ".".join(parts)
        if old_mod in old_import_line:
            return old_import_line.replace(old_mod, new_mod), None
        return old_import_line, f"Julia module '{old_mod}' not found in import"

    elif language == "proto":
        # Proto: import "old/path.proto" → import "new/path.proto"
        old_spec = PurePosixPath(old_file).as_posix()
        new_spec = PurePosixPath(new_file).as_posix()
        if old_spec in old_import_line:
            return old_import_line.replace(old_spec, new_spec), None
        return old_import_line, f"Proto import path '{old_spec}' not found"

    elif language == "fortran":
        # Fortran: use old_module → use new_module
        old_mod = PurePosixPath(old_file).stem
        new_mod = PurePosixPath(new_file).stem
        if old_mod in old_import_line:
            return old_import_line.replace(old_mod, new_mod), None
        return old_import_line, f"Fortran module '{old_mod}' not found in use"

    return old_import_line, f"Unsupported language '{language}' for import rewrite"


def _format_import_line(imp_dict, language):
    """Reconstruct an import line from a parsed import dict."""
    spec = imp_dict["specifier"]
    names = imp_dict.get("names", [])
    if language == "python":
        if names:
            return f"from {spec} import {', '.join(names)}"
        return f"import {spec}"
    elif language in ("typescript", "javascript", "tsx", "jsx", "vue"):
        if names:
            return f"import {{ {', '.join(names)} }} from '{spec}';"
        return f"import '{spec}';"
    elif language == "rust":
        rust_spec = spec.replace("/", "::")
        if names:
            return f"use {rust_spec}::{{{', '.join(names)}}};"
        return f"use {rust_spec};"
    elif language == "go":
        return f'import "{spec}"'
    elif language in ("java", "kotlin", "groovy"):
        suffix = ";" if language in ("java", "groovy") else ""
        if names:
            return f"import {spec}.{names[0]}{suffix}"
        return f"import {spec}{suffix}"
    elif language == "scala":
        if names and len(names) > 1:
            return f"import {spec}.{{{', '.join(names)}}}"
        elif names:
            return f"import {spec}.{names[0]}"
        return f"import {spec}"
    elif language == "csharp":
        return f"using {spec};"
    elif language == "php":
        return f"use {spec};"
    elif language == "ruby":
        return f"require '{spec}'"
    elif language in ("c", "cpp", "objc"):
        return f'#include "{spec}"'
    elif language == "swift":
        return f"import {spec}"
    elif language == "haskell":
        if names:
            return f"import {spec} ({', '.join(names)})"
        return f"import {spec}"
    elif language == "dart":
        return f"import '{spec}';"
    elif language == "elixir":
        if names:
            return f"alias {spec}.{{{', '.join(names)}}}"
        return f"alias {spec}"
    elif language == "perl":
        perl_spec = spec.replace("/", "::")
        return f"use {perl_spec};"
    elif language in ("lua", "luau"):
        lua_spec = spec.replace("/", ".")
        return f'require("{lua_spec}")'
    elif language == "julia":
        return f"using {spec}"
    elif language == "proto":
        return f'import "{spec}";'
    elif language == "fortran":
        return f"use {spec}"
    elif language == "asm":
        return f'.include "{spec}"'
    elif language == "gleam":
        return f"import {spec}"
    elif language == "r":
        return f"library({spec})"
    elif language == "gdscript":
        return f'preload("{spec}")'
    elif language == "graphql":
        return f'# import {spec}'
    return f"import {spec}"


# ---------------------------------------------------------------------------
# Fix B: Qualified import detection (check all parts)
# ---------------------------------------------------------------------------

def _check_qualified_import_used(body: str, specifier: str) -> bool:
    """Check if a qualified import specifier is actually used in the body.
    
    Fix B: For qualified imports like 'os.path', we need to verify the qualified
    access pattern is used, not just that one of the parts appears as a word.
    E.g., 'os.path.join' should return True only if the qualified access pattern
    appears in the body, not just if 'path' appears as a word in variable names.
    
    For single-part imports (no dots), we check if that part appears as a word.
    """
    # Check full qualified name first
    if re.search(r"\b" + re.escape(specifier) + r"\b", body):
        return True
    
    # For qualified imports (with dots), check if used in qualified access pattern
    parts = specifier.split(".")
    if len(parts) >= 2:
        # Build regex that checks the first part is used followed by the rest
        # e.g., for os.path: check if 'os.path' or 'os\n.path' (cross-line) appears
        # or if os appears followed by . and then path
        qualified_pattern = re.escape(parts[0]) + r"(?:\s*\.\s*)" + re.escape(".".join(parts[1:]))
        # Fix 3: Add word boundary at BOTH ends of the full qualified pattern
        if re.search(r"\b" + qualified_pattern + r"\b", body, re.DOTALL):
            return True
    elif len(parts) == 1:
        # Single-part import (e.g., `import os`): check if it appears as a word
        if re.search(r"\b" + re.escape(specifier) + r"\b", body):
            return True
    
    return False


# ---------------------------------------------------------------------------
# Fix C: Smarter unique context expansion (track symbol occurrences)
# ---------------------------------------------------------------------------

def _count_symbol_occurrences(content: str, symbol_name: str) -> int:
    """Count how many times a symbol name appears at word boundaries in content."""
    pattern = re.compile(r"\b" + re.escape(symbol_name) + r"\b")
    return len(pattern.findall(content))


def _ensure_unique_context_smart(
    content: str, 
    lines: list[str], 
    match_line_idx: int, 
    old_text: str, 
    new_text: str,
    symbol_name: str,
    new_name: str
) -> Tuple[str, str]:
    """Expand old_text/new_text only when necessary for uniqueness.
    
    Fix C: This is smarter than the original - it tracks symbol occurrences.
    - If symbol name appears only once, no expansion needed (symbol is unique)
    - If old_text is already unique, no expansion needed
    - Otherwise, expand to make old_text unique
    """
    # Fix C: If symbol name is unique in the file, no expansion needed
    # (even if the line text is duplicated, the edit will still work correctly)
    symbol_count = _count_symbol_occurrences(content, symbol_name)
    if symbol_count <= 1:
        return old_text, new_text
    
    # If old_text is already unique, no expansion needed
    if content.count(old_text) == 1:
        return old_text, new_text
    
    # Need to expand - do it smartly by including context that makes it unique
    line_sep = _detect_line_sep(content)
    above, below = 0, 0
    max_expand = 5
    while content.count(old_text) > 1 and (above + below) < max_expand:
        # Fix 4: Prefer direction with more available content to avoid wasting budget
        above_room = match_line_idx - above - 1
        below_room = len(lines) - (match_line_idx + below + 1)

        if above_room >= 0 and (below_room <= 0 or above_room >= below_room):
            above += 1
        elif below_room > 0:
            below += 1
        else:
            break

        start = match_line_idx - above
        end = match_line_idx + below + 1
        expanded_lines = lines[start:end]
        # LE-1: Use content's line separator instead of hardcoded "\n"
        old_text = line_sep.join(expanded_lines)
        # Build new_text: replace the original match line within the expanded block
        new_lines = list(expanded_lines)
        # The original match line is at index `above` within the expanded slice
        # Apply word replacement to the line containing the symbol
        new_lines[above] = _apply_word_replacement(expanded_lines[above], symbol_name, new_name)
        new_text = line_sep.join(new_lines)

    return old_text, new_text


# ---------------------------------------------------------------------------
# Fix D: TypeScript method overload signature extraction
# ---------------------------------------------------------------------------

def _extract_ts_overload_signatures(lines: list[str], def_line_idx: int, sym_name: str, line_sep: str = "\n") -> Tuple[str, int]:
    """Extract TypeScript overload signatures (consecutive function signatures).
    
    Fix D: Returns (old_text, end_line_idx) - the combined signatures and the last line index.
    LE-5: Uses line_sep parameter instead of hardcoded "\n".
    """
    # Check if this is an overload signature (function with type annotation but no body)
    first_line = lines[def_line_idx]
    if not _TS_OVERLOAD_PATTERN.match(first_line.strip()):
        return first_line.rstrip(), def_line_idx
    
    # Collect consecutive overload signatures
    signature_lines = [first_line.rstrip()]
    current_idx = def_line_idx + 1
    
    while current_idx < len(lines):
        next_line = lines[current_idx].strip()
        # Check if next line is also an overload signature for the same function
        if re.match(r"^\s*(export\s+)?function\s+" + re.escape(sym_name) + r"\s*\(", next_line):
            signature_lines.append(lines[current_idx].rstrip())
            current_idx += 1
        else:
            break
    
    # LE-5: Use line_sep instead of hardcoded "\n"
    return line_sep.join(signature_lines), current_idx - 1


# ---------------------------------------------------------------------------
# Fix E: F-string and template literal detection
# ---------------------------------------------------------------------------

def _is_inside_interpolation(line_text: str, symbol_name: str, language: str) -> bool:
    """Check if symbol name appears inside f-string interpolation or template literal.
    
    Fix E: 
    Python: f"...{symbol_name}..."
    JS/TS: `...${symbol_name}...`
    """
    stripped = line_text.strip()
    
    # Python f-string detection
    if language == "python":
        # Find f-string markers (including triple-quoted f-strings)
        # Bug 15: Handle rf/fr/Fr/fR/RF/FR prefixes (raw f-strings)
        fstring_pattern = re.compile(r"[rR]?[fF]('''[\s\S]*?'''|\"\"\"[\s\S]*?\"\"\"|\"[\s\S]*?\"|'[\s\S]*?')|[fF][rR]?('''[\s\S]*?'''|\"\"\"[\s\S]*?\"\"\"|\"[\s\S]*?\"|'[\s\S]*?')")
        for match in fstring_pattern.finditer(stripped):
            fstring_content = match.group(1) or match.group(2)
            # Bug 6: {{ }} are escaped literal braces in Python, not interpolation
            # Replace {{ and }} with placeholders before checking for interpolation
            escaped_content = fstring_content.replace("{{", "⟪ESCAPED_OPEN⟫").replace("}}", "⟪ESCAPED_CLOSE⟫")
            # Check for {symbol_name} inside the f-string (after removing escaped braces)
            interp_pattern = re.compile(r"\{[^}]*\b" + re.escape(symbol_name) + r"\b[^}]*\}")
            if interp_pattern.search(escaped_content):
                return True
        # Also check for unclosed f-strings (opening quote found but no closing) - line is still in string
        # This handles the case where we start in an f-string but haven't found the end yet
        fstring_start = re.compile(r"[rR]?[fF]('''|\"\"\"|\"|')|[fF][rR]?('''|\"\"\"|\"|')")
        for match in fstring_start.finditer(stripped):
            quote = match.group(1) or match.group(2)
            # Find content after opening quote
            start = match.end()
            # For triple quotes, closing is the same triple quote
            if quote in ('"""', "'''"):
                end_quote = stripped.find(quote, start)
            else:
                end_quote = stripped.find(quote, start)
            if end_quote == -1:
                # Unclosed f-string - check if symbol is after opening
                content_after = stripped[start:]
                # Bug 6: Also handle escaped braces in unclosed f-strings
                escaped_content = content_after.replace("{{", "⟪ESCAPED_OPEN⟫").replace("}}", "⟪ESCAPED_CLOSE⟫")
                interp_pattern = re.compile(r"\{[^}]*\b" + re.escape(symbol_name) + r"\b[^}]*\}")
                if interp_pattern.search(escaped_content):
                    return True
    
    # JS/TS template literal detection
    if language in ("typescript", "javascript"):
        # Find template literal markers (single-line: `...`)
        template_pattern = re.compile(r"`[^`]*`")
        for match in template_pattern.finditer(stripped):
            template_content = match.group()
            # Bug 12: Handle nested braces by using brace counting
            if _check_symbol_in_template_interp(template_content, symbol_name):
                return True
        
        # Also check for ${symbol_name} interpolation directly on the line
        # This catches multiline template literals where the interpolation line
        # has no backticks but is still inside a template literal
        if _check_symbol_in_template_interp(stripped, symbol_name):
            return True
    
    return False


def _check_symbol_in_template_interp(content: str, symbol_name: str) -> bool:
    """Check if symbol appears inside ${...} interpolation, handling nested braces.
    
    Bug 12: ${obj.method({key: value})} has nested braces that [^}]* can't handle.
    """
    i = 0
    while i < len(content):
        # Find ${
        if content[i:i+2] == '${':
            start = i + 2
            brace_depth = 1
            j = start
            while j < len(content) and brace_depth > 0:
                if content[j] == '{':
                    brace_depth += 1
                elif content[j] == '}':
                    brace_depth -= 1
                j += 1
            # Extract interpolation content (from ${ to matching })
            interp_content = content[start:j-1] if brace_depth == 0 else content[start:]
            # Check for symbol name at word boundary
            if re.search(r"\b" + re.escape(symbol_name) + r"\b", interp_content):
                return True
            i = j
        else:
            i += 1
    return False


# ---------------------------------------------------------------------------
# Fix F: Safe file content retrieval with error detection
# ---------------------------------------------------------------------------

def _get_file_content_safe(store, owner: str, name: str, fpath: str) -> Tuple[str, Optional[str]]:
    """Get file content with error detection.
    
    Fix F: Returns (content, error) tuple:
    - (content, None) if file was read successfully (content may be empty string)
    - ("", error_message) if file could not be read
    """
    try:
        content = store.get_file_content(owner, name, fpath)
        if content is None:
            return "", f"File not found or not indexed: {fpath}"
        return content, None
    except Exception as e:
        return "", f"Error reading file {fpath}: {str(e)}"


# ---------------------------------------------------------------------------
# Rename
# ---------------------------------------------------------------------------

def _plan_rename(index, store, owner, name, sym, new_name, depth):
    """Generate rename edit plan."""
    sym_name = sym["name"]
    sym_file = sym["file"]

    # Collision check
    collision = _check_collision(index, new_name, sym_file, store, owner, name, depth)

    # Find affected files (importers that reference the name)
    affected = _find_affected_files(index, store, owner, name, sym_file, sym_name, depth)

    # Always include the definition file
    all_files = [sym_file] + [f for f in affected if f != sym_file]

    edits = []
    file_read_warnings = []
    for fpath in all_files:
        content, read_error = _get_file_content_safe(store, owner, name, fpath)
        if read_error:
            file_read_warnings.append({"file": fpath, "reason": "file_read_error", "error": read_error})
            continue
        if not content:
            continue
        lang = index.file_languages.get(fpath, "python")
        blocks = _generate_rename_blocks(content, sym_name, new_name, lang)
        if blocks:
            edits.append({"file": fpath, "blocks": blocks})

    warnings = _scan_non_code_files(store, owner, name, index, sym_name)
    
    # Fix F: Combine all warnings
    all_warnings = warnings + file_read_warnings

    total_blocks = sum(len(e["blocks"]) for e in edits)
    result = {
        "type": "rename",
        "edits": edits,
        "warnings": all_warnings,
        "collision_check": collision,
        "summary": {"files": len(edits), "edit_blocks": total_blocks, "warnings": len(all_warnings)},
    }

    # Token savings
    _record_savings(len(all_files), result)
    return result


def _generate_rename_blocks(content, old_name, new_name, language):
    """Generate {old_text, new_text, category} blocks for all matches in a file."""
    lines = content.splitlines()
    pattern = re.compile(r"\b" + re.escape(old_name) + r"\b")
    blocks = []

    for i, line in enumerate(lines):
        if not pattern.search(line):
            continue
        category = _classify_line(line, old_name, language)
        if category == "string":
            continue  # strings go to warnings, not edits

        new_line = _apply_word_replacement(line, old_name, new_name)
        old_text = line.rstrip()
        new_text = new_line.rstrip()

        # Fix C: Use smart unique context that tracks symbol occurrences
        old_text, new_text = _ensure_unique_context_smart(content, lines, i, old_text, new_text, old_name, new_name)

        blocks.append({"old_text": old_text, "new_text": new_text, "category": category})

    return blocks


def _check_collision(index, new_name, sym_file, store, owner, name, depth):
    """Check if new_name collides with existing symbols in affected files."""
    source_files = frozenset(index.source_files)
    rev = _build_reverse_adjacency(index.imports, source_files, index.alias_map, getattr(index, "psr4_map", None))
    importer_files, _ = _bfs_importers(sym_file, rev, depth)
    files_to_check = {sym_file} | set(importer_files)

    conflicts = []
    for s in index.symbols:
        if s.get("file") in files_to_check and s.get("name", "").lower() == new_name.lower():
            conflicts.append({"file": s["file"], "symbol_id": s["id"], "kind": s.get("kind")})

    return {"safe": len(conflicts) == 0, "conflicts": conflicts}


# ---------------------------------------------------------------------------
# Move
# ---------------------------------------------------------------------------

def _extract_symbol_with_deps(store, owner, name, index, sym):
    """Get symbol source + determine which imports from its file it needs."""
    content, read_error = _get_file_content_safe(store, owner, name, sym["file"])
    if read_error:
        logger.warning(f"Could not read symbol source file: {read_error}")
        return "", []

    lines = content.splitlines()
    body = _symbol_body(lines, sym)

    # Find which imports from the file are used by the symbol
    file_imports = index.imports.get(sym["file"], [])
    needed_imports = []
    for imp in file_imports:
        specifier = imp.get("specifier", "")
        imp_names = imp.get("names", [])
        # Fix B: Check if any imported name appears in the symbol body
        if imp_names:
            for n in imp_names:
                if re.search(r"\b" + re.escape(n) + r"\b", body):
                    needed_imports.append(imp)
                    break
        elif specifier:
            # Fix B: Check ALL parts of qualified imports (os.path -> check os AND path)
            if _check_qualified_import_used(body, specifier):
                needed_imports.append(imp)

    return body, needed_imports


def _plan_move(index, store, owner, name, sym, new_file, depth):
    """Generate move edit plan."""
    sym_name = sym["name"]
    sym_file = sym["file"]
    lang = index.file_languages.get(sym_file, "python")

    # Check if destination already has a symbol with this name (collision)
    dest_collision = None
    for s in index.symbols:
        if s.get("file") == new_file and s.get("name", "").lower() == sym_name.lower():
            dest_collision = {"file": new_file, "symbol_id": s["id"], "kind": s.get("kind")}
            break

    # Get symbol source + its import dependencies
    body, needed_imports = _extract_symbol_with_deps(store, owner, name, index, sym)

    # Bug 1: Check same-file dependencies (symbols in source file that reference this symbol)
    dep_warnings = _find_inter_symbol_deps(index, store, owner, name, [sym], sym_file)

    # Source removal block
    content, read_error = _get_file_content_safe(store, owner, name, sym_file)
    if read_error:
        return {"error": f"Could not read source file: {read_error}"}
    lines = content.splitlines()
    line_sep = _detect_line_sep(content)
    start = sym.get("line", 1) - 1
    end = sym.get("end_line", start + 1)
    # LE-2: Use content's line separator instead of hardcoded "\n"
    old_text = line_sep.join(lines[start:end])

    source_removal = {
        "file": sym_file,
        "old_text": old_text,
        "new_text": "",
        "note": f"Remove {sym_name} from source file",
    }

    # Destination content
    needed_import_lines = [_format_import_line(imp, lang) for imp in needed_imports]
    destination = {
        "file": new_file,
        "symbol_source": body,
        "needed_imports": needed_import_lines,
    }

    # Bug 1: Add import of moved symbol back to source file (for same-file internal references)
    # Bug 5: Only add import if staying symbols actually reference the moved symbol
    new_module = PurePosixPath(new_file).with_suffix("").as_posix()
    
    # Check if any staying symbol references the moved symbol
    needs_source_import = any(
        w.get("direction") == "staying_calls_extracted" 
        for w in dep_warnings
    )
    
    if lang in ("typescript", "javascript"):
        add_import_line = f"import {{ {sym_name} }} from '{new_module}';"
    elif lang == "rust":
        add_import_line = "use " + new_module.replace("/", "::") + ";"
    elif lang == "go":
        add_import_line = f'import "{new_module}"'
    else:
        add_import_line = f"from {new_module.replace('/', '.')} import {sym_name}"

    # Import rewrites for all importers
    affected = _find_affected_files(index, store, owner, name, sym_file, sym_name, depth)
    import_rewrites, rewrite_warnings = _generate_import_rewrites(
        index, store, owner, name, affected, sym_name, sym_file, new_file, lang
    )

    result = {
        "type": "move",
        "source_removal": source_removal,
        "destination": destination,
        "import_rewrites": import_rewrites,
        "collision_check": {"safe": dest_collision is None, "conflict": dest_collision},
        "summary": {"importers_rewritten": len(import_rewrites)},
    }
    
    # Bug 5: Only include add_import if staying symbols reference the moved symbol
    if needs_source_import:
        result["add_import"] = {"file": sym_file, "import_line": add_import_line}
    
    # Include warnings for failed rewrites and same-file dependencies
    if rewrite_warnings:
        result["warnings"] = rewrite_warnings
    if dep_warnings:
        result["dep_warnings"] = dep_warnings

    # Token savings
    _record_savings(len(affected) + 1, result)
    return result


def _generate_import_rewrites(index, store, owner, name, affected_files, sym_name, old_file, new_file, language):
    """Generate import rewrite blocks for each affected file.
    
    Returns list of rewrites and collects warnings for unrewritable imports.
    """
    rewrites = []
    warnings = []
    old_module = _file_to_module(old_file)
    new_module = _file_to_module(new_file)

    for fpath in affected_files:
        content, read_error = _get_file_content_safe(store, owner, name, fpath)
        if read_error:
            warnings.append({"file": fpath, "reason": "file_read_error", "error": read_error})
            continue
        if not content:
            continue

        for i, line in enumerate(content.splitlines()):
            if not (_IMPORT_PATTERNS.get(language, _DEFAULT_IMPORT_PATTERN)).match(line.strip()):
                continue
            if not re.search(r"\b" + re.escape(sym_name) + r"\b", line):
                continue

            # Handle multi-import lines: "from X import a, b" when only "a" moves
            if language == "python" and "," in line and sym_name in line:
                new_line = _split_python_import(line, sym_name, old_module, new_module)
                if new_line != line:
                    rewrites.append({"file": fpath, "old_text": line.rstrip(), "new_text": new_line.rstrip()})
            else:
                # Fix A: Handle tuple return with warning
                new_line, rewrite_warning = _compute_new_import(line, old_file, new_file, sym_name, language)
                if new_line != line:
                    rewrites.append({"file": fpath, "old_text": line.rstrip(), "new_text": new_line.rstrip()})
                elif rewrite_warning:
                    warnings.append({"file": fpath, "line": i + 1, "reason": "import_rewrite_failed", "warning": rewrite_warning})

    return rewrites, warnings


def _split_python_import(line, moving_name, old_module, new_module):
    """Handle 'from X import a, b' when only one name is moving.
    
    Bug 4: Handle aliased imports like 'from X import User as U, Admin'
    where 'User as U' should match 'User' (strip alias when comparing).
    """
    match = re.match(r"^(\s*)(from\s+\S+\s+import\s+)(.+)$", line)
    if not match:
        return line
    indent, from_keyword, names_str = match.group(1), match.group(2), match.group(3)
    prefix = indent + from_keyword
    names = [n.strip() for n in names_str.split(",")]

    # Bug 4: Strip alias part when matching (e.g., "User as U" -> "User")
    def get_base_name(name: str) -> str:
        # Split on " as " and take first part
        if " as " in name:
            return name.split(" as ")[0].strip()
        return name

    # Find the moving import (may have alias)
    moving_import = None
    remaining = []
    for n in names:
        base_name = get_base_name(n)
        if base_name == moving_name:
            moving_import = n  # Keep original form (with alias if present)
        else:
            remaining.append(n)

    if moving_import and remaining:
        # Keep remaining imports + add new import on next line
        kept = prefix + ", ".join(remaining)
        # B-3: Use moving_import to preserve alias (e.g., "User as U")
        added = f"{indent}from {new_module} import {moving_import}"
        return kept + "\n" + added
    elif moving_import:
        # All names moved — just rewrite the module
        # B-3: Use moving_import to preserve alias (e.g., "User as U")
        return f"{indent}from {new_module} import {moving_import}"
    else:
        # Moving name not found in import - return unchanged
        return line


# ---------------------------------------------------------------------------
# Extract
# ---------------------------------------------------------------------------

def _plan_extract(index, store, owner, name, syms, new_file, depth):
    """Generate extract edit plan for one or more symbols."""
    # All symbols must come from the same file
    files = {s["file"] for s in syms}
    if len(files) > 1:
        return {"error": "All symbols must be from the same file for extract"}
    source_file = syms[0]["file"]
    lang = index.file_languages.get(source_file, "python")

    # Check if destination already has a symbol with same name (collision)
    dest_collision = None
    for sym in syms:
        sym_name = sym["name"]
        for s in index.symbols:
            if s.get("file") == new_file and s.get("name", "").lower() == sym_name.lower():
                dest_collision = {"file": new_file, "symbol_id": s["id"], "kind": s.get("kind"), "name": sym_name}
                break
        if dest_collision:
            break

    # Collect sources and dependencies
    all_bodies = []
    all_imports = []
    sym_names = []
    for sym in sorted(syms, key=lambda s: s.get("line", 0)):
        body, needed = _extract_symbol_with_deps(store, owner, name, index, sym)
        all_bodies.append(body)
        all_imports.extend(needed)
        sym_names.append(sym["name"])

    # Deduplicate imports
    seen_specs = set()
    unique_imports = []
    for imp in all_imports:
        key = (imp["specifier"], tuple(imp.get("names", [])))
        if key not in seen_specs:
            seen_specs.add(key)
            unique_imports.append(imp)

    # Check inter-symbol dependencies
    dep_warnings = _find_inter_symbol_deps(index, store, owner, name, syms, source_file)

    # Build new file content
    import_lines = [_format_import_line(imp, lang) for imp in unique_imports]
    new_file_content = _build_new_file_content(all_bodies, import_lines, lang)

    # Source removals
    content, read_error = _get_file_content_safe(store, owner, name, source_file)
    if read_error:
        return {"error": f"Could not read source file: {read_error}"}
    lines = content.splitlines()
    line_sep = _detect_line_sep(content)
    source_removals = []
    for sym in sorted(syms, key=lambda s: s.get("line", 0), reverse=True):
        # Reverse order so line numbers stay valid during removal
        start = sym.get("line", 1) - 1
        end = sym.get("end_line", start + 1)
        # LE-3: Use content's line separator instead of hardcoded "\n"
        old_text = line_sep.join(lines[start:end])
        source_removals.append({
            "file": source_file,
            "old_text": old_text,
            "new_text": "",
        })

    # Add import of extracted symbols to source file
    new_module = PurePosixPath(new_file).with_suffix("").as_posix()
    if lang in ("typescript", "javascript"):
        add_import = f"import {{ {', '.join(sym_names)} }} from '{new_module}';"
    elif lang == "rust":
        add_import = "use " + new_module.replace("/", "::") + ";"
    elif lang == "go":
        add_import = f'import "{new_module}"'
    else:
        add_import = f"from {new_module.replace('/', '.')} import {', '.join(sym_names)}"

    # Import rewrites for external importers
    all_affected = set()
    for sym in syms:
        affected = _find_affected_files(index, store, owner, name, source_file, sym["name"], depth)
        all_affected.update(affected)

    import_rewrites = []
    rewrite_warnings = []
    for sn in sym_names:
        rw, warns = _generate_import_rewrites(
            index, store, owner, name, list(all_affected), sn, source_file, new_file, lang
        )
        import_rewrites.extend(rw)
        rewrite_warnings.extend(warns)

    # Only add import to source file if staying symbols reference extracted symbols
    needs_source_import = any(
        w.get("direction") == "staying_calls_extracted"
        for w in dep_warnings
    )

    result = {
        "type": "extract",
        "new_file": {
            "file": new_file,
            "content": new_file_content,
            "note": "Create with Write tool",
        },
        "source_removals": source_removals,
        "import_rewrites": import_rewrites,
        "collision_check": {"safe": dest_collision is None, "conflict": dest_collision},
        "summary": {
            "symbols_extracted": len(syms),
            "importers_rewritten": len(import_rewrites),
            "new_file": new_file,
        },
    }
    if needs_source_import:
        result["add_import"] = {"file": source_file, "import_line": add_import}
    if dep_warnings:
        result["dep_warnings"] = dep_warnings
    if rewrite_warnings:
        result["warnings"] = rewrite_warnings

    # Token savings
    _record_savings(len(all_affected) + len(syms), result)
    return result


def _build_new_file_content(bodies, import_lines, language):
    """Assemble a well-formatted new file."""
    # Fix 4: Build result directly to avoid double trailing newline
    if import_lines:
        imports = "\n".join(sorted(set(import_lines)))
        return imports + "\n\n" + "\n\n".join(bodies) + "\n"
    return "\n\n".join(bodies) + "\n"


def _find_inter_symbol_deps(index, store, owner, name, syms, source_file):
    """Check if extracting these symbols breaks references to symbols staying behind.
    
    Bug 1: Check BOTH directions:
    - Extracted symbol references staying symbol (needs import in new file)
    - Staying symbol references extracted symbol (needs import in source file)
    """
    extracting_names = {s["name"] for s in syms}
    content, read_error = _get_file_content_safe(store, owner, name, source_file)
    if read_error:
        return [{"reason": "file_read_error", "error": read_error, "file": source_file}]
    if not content:
        return []

    warnings = []
    lines = content.splitlines()
    file_symbols = [s for s in index.symbols if s["file"] == source_file]
    staying = [s for s in file_symbols if s["name"] not in extracting_names]

    # Direction 1: Extracted symbol references staying symbol
    for sym in syms:
        body = _symbol_body(lines, sym)
        for stay_sym in staying:
            if re.search(r"\b" + re.escape(stay_sym["name"]) + r"\b", body):
                warnings.append({
                    "extracted": sym["name"],
                    "references": stay_sym["name"],
                    "direction": "extracted_calls_staying",
                    "note": f"{sym['name']} calls {stay_sym['name']} which stays in {source_file}. Add import or extract together.",
                })

    # Bug 1: Direction 2: Staying symbol references extracted symbol
    for stay_sym in staying:
        stay_body = _symbol_body(lines, stay_sym)
        for sym in syms:
            if re.search(r"\b" + re.escape(sym["name"]) + r"\b", stay_body):
                warnings.append({
                    "extracted": sym["name"],
                    "references": stay_sym["name"],
                    "direction": "staying_calls_extracted",
                    "note": f"{stay_sym['name']} calls {sym['name']} which is being extracted. Add import to {source_file}.",
                })

    return warnings


# ---------------------------------------------------------------------------
# Signature Change
# ---------------------------------------------------------------------------

def _plan_signature_change(index, store, owner, name, sym, new_signature, depth):
    """Generate signature change plan with call site discovery."""
    sym_name = sym["name"]
    sym_file = sym["file"]
    lang = index.file_languages.get(sym_file, "python")

    # Definition edit
    content, read_error = _get_file_content_safe(store, owner, name, sym_file)
    if read_error:
        return {"error": f"Could not read source file: {read_error}"}
    lines = content.splitlines()
    def_line_idx = sym.get("line", 1) - 1
    end_line_idx = def_line_idx  # Default, will be updated for TypeScript overloads
    
    # Fix D: Handle TypeScript method overloads (consecutive signatures)
    if lang == "typescript":
        line_sep = _detect_line_sep(content)
        old_def, end_line_idx = _extract_ts_overload_signatures(lines, def_line_idx, sym_name, line_sep)
        sig_end_idx = end_line_idx  # Align with the generic skip variable
        # Build new definition for all overload signatures
        indent = re.match(r"^(\s*)", lines[def_line_idx]).group(1)
        # For overloads, we replace all signatures with the new one
        new_def = f"{indent}function {new_signature}"
    else:
        def_line_idx = sym.get("line", 1) - 1
        first_line = lines[def_line_idx]
        indent = re.match(r"^(\s*)", first_line).group(1)

        # B-1: Check if this is an async def
        is_async = first_line.strip().startswith("async ")

        if lang == "python":
            # Python: signature ends with ":" — custom loop for colon check
            sig_lines = [first_line.rstrip()]
            paren_depth = first_line.count("(") - first_line.count(")")
            current_idx = def_line_idx + 1

            while paren_depth > 0 and current_idx < len(lines):
                next_line = lines[current_idx]
                sig_lines.append(next_line.rstrip())
                paren_depth += next_line.count("(") - next_line.count(")")
                if paren_depth == 0 and next_line.rstrip().endswith(":"):
                    break
                current_idx += 1

            line_sep = _detect_line_sep(content)
            old_def = line_sep.join(sig_lines)
            new_def = f"{indent}{'async ' if is_async else ''}def {new_signature}:"
            sig_end_idx = def_line_idx + len(sig_lines) - 1

        elif lang == "rust":
            sig_lines, line_sep, sig_end_idx = _capture_multiline_sig(lines, def_line_idx, content)
            old_def = line_sep.join(sig_lines)
            vis_match = re.match(r"^(\s*(?:pub\s*(?:\([^)]*\)\s*)?)?)", first_line)
            vis_prefix = vis_match.group(1) if vis_match else indent
            new_def = f"{vis_prefix}fn {new_signature}"

        elif lang == "go":
            sig_lines, line_sep, sig_end_idx = _capture_multiline_sig(lines, def_line_idx, content)
            old_def = line_sep.join(sig_lines)
            recv_match = re.match(r"^(\s*func\s+\([^)]+\)\s+)", first_line)
            if recv_match:
                new_def = f"{recv_match.group(1)}{new_signature}"
            else:
                new_def = f"{indent}func {new_signature}"

        elif lang in ("java", "csharp", "kotlin"):
            sig_lines, line_sep, sig_end_idx = _capture_multiline_sig(lines, def_line_idx, content)
            old_def = line_sep.join(sig_lines)
            mod_match = re.match(
                r"^(\s*(?:(?:public|private|protected|internal|static|abstract|override|virtual|sealed|final|open|suspend|inline)\s+)*)",
                first_line,
            )
            mod_prefix = mod_match.group(1) if mod_match else indent
            if lang == "kotlin":
                new_def = f"{mod_prefix}fun {new_signature}"
            else:
                new_def = f"{mod_prefix}{new_signature}"

        elif lang == "swift":
            sig_lines, line_sep, sig_end_idx = _capture_multiline_sig(lines, def_line_idx, content)
            old_def = line_sep.join(sig_lines)
            mod_match = re.match(
                r"^(\s*(?:(?:public|private|internal|open|fileprivate|static|class|mutating|nonmutating|@\w+)\s+)*)",
                first_line,
            )
            mod_prefix = mod_match.group(1) if mod_match else indent
            new_def = f"{mod_prefix}func {new_signature}"

        elif lang == "scala":
            sig_lines, line_sep, sig_end_idx = _capture_multiline_sig(lines, def_line_idx, content)
            old_def = line_sep.join(sig_lines)
            mod_match = re.match(r"^(\s*(?:(?:private|protected|override|implicit|lazy|final|sealed|abstract)\s+)*)", first_line)
            mod_prefix = mod_match.group(1) if mod_match else indent
            new_def = f"{mod_prefix}def {new_signature}"

        elif lang == "dart":
            sig_lines, line_sep, sig_end_idx = _capture_multiline_sig(lines, def_line_idx, content)
            old_def = line_sep.join(sig_lines)
            mod_match = re.match(r"^(\s*(?:(?:static|abstract|external|factory)\s+)*)", first_line)
            mod_prefix = mod_match.group(1) if mod_match else indent
            new_def = f"{mod_prefix}{new_signature}"

        elif lang == "php":
            sig_lines, line_sep, sig_end_idx = _capture_multiline_sig(lines, def_line_idx, content)
            old_def = line_sep.join(sig_lines)
            mod_match = re.match(r"^(\s*(?:(?:public|private|protected|static|abstract|final)\s+)*)", first_line)
            mod_prefix = mod_match.group(1) if mod_match else indent
            new_def = f"{mod_prefix}function {new_signature}"

        elif lang == "ruby":
            old_def = first_line.rstrip()
            new_def = f"{indent}def {new_signature}"
            sig_end_idx = def_line_idx

        elif lang == "elixir":
            sig_lines, line_sep, sig_end_idx = _capture_multiline_sig(lines, def_line_idx, content)
            old_def = line_sep.join(sig_lines)
            keyword = "defp" if first_line.strip().startswith("defp") else "def"
            new_def = f"{indent}{keyword} {new_signature}"

        elif lang in ("c", "cpp"):
            sig_lines, line_sep, sig_end_idx = _capture_multiline_sig(lines, def_line_idx, content)
            old_def = line_sep.join(sig_lines)
            new_def = f"{indent}{new_signature}"

        elif lang in ("lua", "luau"):
            sig_lines, line_sep, sig_end_idx = _capture_multiline_sig(lines, def_line_idx, content)
            old_def = line_sep.join(sig_lines)
            local_prefix = "local " if first_line.strip().startswith("local ") else ""
            new_def = f"{indent}{local_prefix}function {new_signature}"

        elif lang == "perl":
            old_def = first_line.rstrip()
            new_def = f"{indent}sub {new_signature}"
            sig_end_idx = def_line_idx

        elif lang == "julia":
            sig_lines, line_sep, sig_end_idx = _capture_multiline_sig(lines, def_line_idx, content)
            old_def = line_sep.join(sig_lines)
            new_def = f"{indent}function {new_signature}"

        elif lang == "gleam":
            sig_lines, line_sep, sig_end_idx = _capture_multiline_sig(lines, def_line_idx, content)
            old_def = line_sep.join(sig_lines)
            pub_prefix = "pub " if first_line.strip().startswith("pub ") else ""
            new_def = f"{indent}{pub_prefix}fn {new_signature}"

        else:
            old_def = first_line.rstrip()
            new_def = f"{indent}{new_signature}"
            sig_end_idx = def_line_idx

    definition_edit = {
        "file": sym_file,
        "old_text": old_def,
        "new_text": new_def,
    }

    # Find call sites
    affected = _find_affected_files(index, store, owner, name, sym_file, sym_name, depth)
    # Also scan definition file for internal calls
    all_files = [sym_file] + [f for f in affected if f != sym_file]

    call_sites = []
    file_read_warnings = []
    for fpath in all_files:
        file_content, read_error = _get_file_content_safe(store, owner, name, fpath)
        if read_error:
            file_read_warnings.append({"file": fpath, "reason": "file_read_error", "error": read_error})
            continue
        if not file_content:
            continue
        file_lines = file_content.splitlines()
        pattern = re.compile(r"\b" + re.escape(sym_name) + r"\s*\(")

        for i, line in enumerate(file_lines):
            # Skip all lines that are part of the definition signature
            # (handles multi-line signatures for all languages)
            if fpath == sym_file and def_line_idx <= i <= sig_end_idx:
                continue
            if not pattern.search(line):
                continue

            # Extract call expression (handle multi-line)
            call_expr = _extract_call_expression(file_lines, sym_name, i)

            # Context: 1 line above and below
            ctx_start = max(0, i - 1)
            ctx_end = min(len(file_lines), i + 2)
            context = "\n".join(file_lines[ctx_start:ctx_end])

            call_sites.append({
                "file": fpath,
                "line": i + 1,
                "current_call": call_expr,
                "context": context,
            })

    result = {
        "type": "signature",
        "definition_edit": definition_edit,
        "call_sites": call_sites,
        "summary": {"call_sites_found": len(call_sites)},
    }
    
    # Fix F: Include warnings for file read errors
    if file_read_warnings:
        result["warnings"] = file_read_warnings

    # Token savings
    _record_savings(len(all_files), result)
    return result


def _extract_call_expression(lines, func_name, start_line_idx):
    """Extract a full function call, handling multi-line with paren balancing."""
    line = lines[start_line_idx]
    # Find the function name and opening paren
    match = re.search(r"\b" + re.escape(func_name) + r"\s*\(", line)
    if not match:
        return line.strip()

    # Balance parentheses - start from match position, not beginning of line
    # Bug 5: Skip string literals when counting parens
    # B-2: Skip triple-quoted strings before single-char toggle
    depth = 0
    result_lines = []
    started = False
    in_string = None  # Track if we're inside a string: None, '"', "'", or '`'
    escape_next = False  # Track if next char is escaped
    in_triple = False  # Track if we're inside a triple-quoted string
    triple_char = None  # The triple-quote character(s): ''' or """
    
    for i in range(start_line_idx, min(start_line_idx + 10, len(lines))):
        line_content = lines[i]
        start_pos = match.start() if i == start_line_idx else 0
        j = start_pos
        while j < len(line_content):
            ch = line_content[j]
            
            if escape_next:
                escape_next = False
                j += 1
                continue
            
            if ch == '\\' and in_string and not in_triple:
                escape_next = True
                j += 1
                continue
            
            # B-2: Handle triple-quoted strings - check before single-char toggle
            if not in_string and not in_triple:
                # Check for triple quotes starting at current position
                if line_content[j:j+3] in ('"""', "'''"):
                    in_triple = True
                    triple_char = line_content[j:j+3]
                    j += 3
                    continue
            elif in_triple:
                # Inside triple-quoted string - look for closing triple
                if line_content[j:j+3] == triple_char:
                    in_triple = False
                    triple_char = None
                    j += 3
                    continue
                j += 1
                continue
            
            # Handle string boundaries (single-char strings only when not in triple)
            if ch in ('"', "'", '`') and in_string is None:
                in_string = ch
                j += 1
                continue
            elif ch == in_string and in_string is not None:
                in_string = None
                j += 1
                continue
            
            # Only count parens outside strings
            if in_string is None and not in_triple:
                if ch == "(":
                    depth += 1
                    started = True
                elif ch == ")":
                    depth -= 1
                # Bug 2: Break inner loop immediately when parens balanced
                if started and depth == 0:
                    j += 1  # Include the closing paren
                    break
            j += 1
        
        result_lines.append(line_content[:j].rstrip())
        if started and depth == 0:
            break

    return "\n".join(result_lines).strip()


# ---------------------------------------------------------------------------
# Token savings helper
# ---------------------------------------------------------------------------

def _record_savings(num_files: int, result: dict) -> None:
    """Estimate tokens saved and record + attach to result."""
    estimated_savings = num_files * 500
    record_savings(estimated_savings, tool_name="plan_refactoring")
    result["_meta"] = {"tokens_saved": estimated_savings}
