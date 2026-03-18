"""Shared indexing pipeline used by index_folder, index_file, and index_repo."""

import logging
from collections import defaultdict
from typing import Optional

from ..parser import parse_file, get_language_for_path
from ..parser.context import ContextProvider, enrich_symbols
from ..parser.imports import extract_imports
from ..parser.symbols import Symbol
from ..summarizer import summarize_symbols, generate_file_summaries

logger = logging.getLogger(__name__)


def file_languages_for_paths(
    file_paths: list[str],
    symbols_by_file: dict[str, list],
) -> dict[str, str]:
    """Resolve file languages using parsed symbols first, then extension fallback."""
    file_languages: dict[str, str] = {}
    for file_path in file_paths:
        file_symbols = symbols_by_file.get(file_path, [])
        language = file_symbols[0].language if file_symbols else ""
        if not language:
            language = get_language_for_path(file_path) or ""
        if language:
            file_languages[file_path] = language
    return file_languages


def language_counts(file_languages: dict[str, str]) -> dict[str, int]:
    """Count files by language."""
    counts: dict[str, int] = {}
    for language in file_languages.values():
        counts[language] = counts.get(language, 0) + 1
    return counts


def complete_file_summaries(
    file_paths: list[str],
    symbols_by_file: dict[str, list],
    context_providers: Optional[list[ContextProvider]] = None,
) -> dict[str, str]:
    """Generate file summaries and include empty entries for no-symbol files."""
    providers = context_providers or []
    generated = generate_file_summaries(dict(symbols_by_file), context_providers=providers)

    # For files with no symbols but with provider metadata, generate context-only summary
    if providers:
        for file_path in file_paths:
            if file_path not in generated or not generated.get(file_path):
                for provider in providers:
                    ctx = provider.get_file_context(file_path)
                    if ctx is not None:
                        summary = ctx.file_summary()
                        if summary:
                            generated[file_path] = summary
                            break

    return {file_path: generated.get(file_path, "") for file_path in file_paths}


def parse_and_prepare_incremental(
    files_to_parse: set[str],
    file_contents: dict[str, str],
    active_providers: Optional[list[ContextProvider]] = None,
    use_ai_summaries: bool = True,
    warnings: Optional[list[str]] = None,
) -> tuple[list[Symbol], dict[str, str], dict[str, str], dict[str, list[dict]], list[str]]:
    """Shared incremental pipeline: parse, enrich, summarize, extract metadata.

    Args:
        files_to_parse: Set of rel_paths to process (changed + new).
        file_contents: rel_path -> content for files to parse.
        active_providers: Context providers for enrichment (empty/None for remote repos).
        use_ai_summaries: Whether to use AI summaries.
        warnings: Mutable list to append warnings to.

    Returns:
        (symbols, file_summaries, file_languages, file_imports, no_symbols_files)
    """
    if warnings is None:
        warnings = []
    providers = active_providers or []

    # 1. Parse each file
    new_symbols: list[Symbol] = []
    no_symbols_files: list[str] = []

    for rel_path in sorted(files_to_parse):
        content = file_contents.get(rel_path)
        if content is None:
            continue
        language = get_language_for_path(rel_path)
        if not language:
            no_symbols_files.append(rel_path)
            continue
        try:
            symbols = parse_file(content, rel_path, language)
            if symbols:
                new_symbols.extend(symbols)
            else:
                no_symbols_files.append(rel_path)
                logger.debug("NO SYMBOLS (incremental): %s", rel_path)
        except Exception as e:
            warnings.append(f"Failed to parse {rel_path}: {e}")
            logger.debug("PARSE ERROR (incremental): %s — %s", rel_path, e)

    logger.info(
        "Incremental parsing — with symbols: %d, no symbols: %d",
        len(new_symbols),
        len(no_symbols_files),
    )

    # 2. Enrich with context providers
    if providers and new_symbols:
        enrich_symbols(new_symbols, providers)

    # 3. Summarize
    new_symbols = summarize_symbols(new_symbols, use_ai=use_ai_summaries)

    # 4. Build symbols-by-file map, file summaries, file languages
    symbols_map: dict[str, list] = defaultdict(list)
    for s in new_symbols:
        symbols_map[s.file].append(s)

    sorted_files = sorted(files_to_parse)
    file_summaries = complete_file_summaries(sorted_files, symbols_map, context_providers=providers or None)
    file_langs = file_languages_for_paths(sorted_files, symbols_map)

    # 5. Extract imports
    file_imports: dict[str, list[dict]] = {}
    for rel_path in files_to_parse:
        content = file_contents.get(rel_path)
        if content is None:
            continue
        language = get_language_for_path(rel_path)
        if language:
            imps = extract_imports(content, rel_path, language)
            if imps:
                file_imports[rel_path] = imps

    return new_symbols, file_summaries, file_langs, file_imports, no_symbols_files


def parse_and_prepare_full(
    file_contents: dict[str, str],
    active_providers: Optional[list[ContextProvider]] = None,
    use_ai_summaries: bool = True,
    warnings: Optional[list[str]] = None,
) -> tuple[list[Symbol], dict[str, str], dict[str, int], dict[str, str], dict[str, list[dict]], list[str]]:
    """Shared full-index pipeline: parse all files, enrich, summarize.

    Args:
        file_contents: rel_path -> content for all files.
        active_providers: Context providers for enrichment.
        use_ai_summaries: Whether to use AI summaries.
        warnings: Mutable list to append warnings to.

    Returns:
        (symbols, file_summaries, languages, file_languages, file_imports, no_symbols_files)
    """
    if warnings is None:
        warnings = []
    providers = active_providers or []

    source_file_list = sorted(file_contents)

    # 1. Parse all files
    all_symbols: list[Symbol] = []
    symbols_by_file: dict[str, list] = defaultdict(list)
    no_symbols_files: list[str] = []

    for path in source_file_list:
        content = file_contents[path]
        language = get_language_for_path(path)
        if not language:
            no_symbols_files.append(path)
            continue
        try:
            symbols = parse_file(content, path, language)
            if symbols:
                all_symbols.extend(symbols)
                symbols_by_file[path].extend(symbols)
            else:
                no_symbols_files.append(path)
                logger.debug("NO SYMBOLS: %s", path)
        except Exception as e:
            warnings.append(f"Failed to parse {path}: {e}")
            logger.debug("PARSE ERROR: %s — %s", path, e)

    logger.info(
        "Parsing complete — with symbols: %d, no symbols: %d",
        len(symbols_by_file),
        len(no_symbols_files),
    )

    # 2. Enrich with context providers
    if providers and all_symbols:
        enrich_symbols(all_symbols, providers)

    # 3. Summarize
    if all_symbols:
        all_symbols = summarize_symbols(all_symbols, use_ai=use_ai_summaries)

    # 4. Rebuild symbols_by_file after summarization (summaries may update fields)
    file_symbols_map: dict[str, list] = defaultdict(list)
    for s in all_symbols:
        file_symbols_map[s.file].append(s)

    file_langs = file_languages_for_paths(source_file_list, file_symbols_map)
    languages = language_counts(file_langs)
    file_summaries = complete_file_summaries(source_file_list, file_symbols_map, context_providers=providers or None)

    # 5. Extract imports
    file_imports: dict[str, list[dict]] = {}
    for path, content in file_contents.items():
        language = get_language_for_path(path)
        if language:
            imps = extract_imports(content, path, language)
            if imps:
                file_imports[path] = imps

    return all_symbols, file_summaries, languages, file_langs, file_imports, no_symbols_files
