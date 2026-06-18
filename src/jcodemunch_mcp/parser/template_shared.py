"""Shared templating-engine helpers (Jinja2, Twig).

A *template file* wraps an underlying source language with a templating engine's
constructs — the user's case is TypeScript-under-Jinja (``foo.ts.j2``). To index
the real underlying symbols we:

1. (optionally) extract the engine's own named definitions — Jinja/Twig
   ``{% macro %}`` / ``{% block %}`` — as symbols, and
2. **mask** the engine constructs while preserving byte offsets and line numbers
   (newline → newline, every other char → space), then re-parse the masked text
   as the underlying language.

Because the mask is offset-preserving, the underlying-language symbols come back
with positions that already point at the real lines of the template file — no
block-offset rewrapping is required (this is what makes templates simpler to
handle than Astro/Razor, which extract embedded sub-blocks).

This module depends only on :mod:`.symbols` and :mod:`.sql_preprocessor` (both
leaves), so :mod:`.languages`, :mod:`.extractor`, and :mod:`.imports` can all
import from it without a cycle.

The first cut ships Jinja2 and Twig — the engines whose ``name.<lang>.<engine>``
double-extension convention this feature targets. The :class:`TemplateEngine`
registry is pluggable: adding an engine is a small addition — register a
:class:`TemplateEngine` in :data:`TEMPLATE_ENGINES` and map its extensions in
:data:`TEMPLATE_EXTENSIONS`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Optional

from .sql_preprocessor import extract_dbt_directives
from .symbols import Symbol, compute_content_hash, make_symbol_id


# --------------------------------------------------------------------------- #
# Directive extraction (Jinja / Twig)                                          #
# --------------------------------------------------------------------------- #
# Reuses sql_preprocessor.extract_dbt_directives — the same Jinja-block scan dbt
# uses for ``{% macro %}`` — passing the ``{% macro %}`` / ``{% block %}`` keyword
# set instead of dbt's. Jinja and Twig share these delimiters exactly, so the
# only template-specific step is mapping the returned DbtDirective rows to
# Symbols (mirroring _parse_sql_symbols' DbtDirective → Symbol conversion).

# The Jinja/Twig directive keywords we surface as symbols.
_TEMPLATE_DIRECTIVES = ("macro", "block")

# Directive kind → Symbol kind. A macro is a callable (function); a block is a
# named, overridable region (constant-like named anchor).
_DIRECTIVE_KIND = {"macro": "function", "block": "constant"}


def extract_jinja_directives(
    text: str, filename: str, language: str = "jinja"
) -> list[Symbol]:
    """Extract ``{% macro %}`` / ``{% block %}`` definitions as Symbols.

    Delegates the scan to :func:`sql_preprocessor.extract_dbt_directives` (the
    dbt directive path, with the ``macro``/``block`` keyword set) and maps each
    :class:`~sql_preprocessor.DbtDirective` to a :class:`Symbol`, mirroring the
    DbtDirective → Symbol conversion in ``extractor._parse_sql_symbols``.
    Offsets/line numbers refer to the *original* template text (the mask
    preserves them, so they stay valid).
    """
    symbols: list[Symbol] = []
    for d in extract_dbt_directives(
        text.encode("utf-8"), directive_keywords=_TEMPLATE_DIRECTIVES
    ):
        kind = _DIRECTIVE_KIND[d.directive]
        signature = (
            f"{{% {d.directive} {d.name}({d.params}) %}}"
            if d.params
            else f"{{% {d.directive} {d.name} %}}"
        )
        symbols.append(
            Symbol(
                id=make_symbol_id(filename, d.name, kind),
                file=filename,
                name=d.name,
                qualified_name=d.name,
                kind=kind,
                language=language,
                signature=signature,
                docstring=d.docstring,
                line=d.line,
                end_line=d.end_line,
                byte_offset=d.byte_offset,
                byte_length=d.byte_length,
                content_hash=compute_content_hash(
                    text.encode("utf-8")[d.byte_offset:d.byte_offset + d.byte_length]
                ),
                ecosystem_context=f"{language}-template",
            )
        )
    return symbols


# --------------------------------------------------------------------------- #
# Engine registry                                                              #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class TemplateEngine:
    """A templating engine jCodeMunch can index over an underlying language."""

    name: str                          # engine language name, e.g. "jinja"
    extensions: tuple[str, ...]        # e.g. (".j2", ".jinja", ".jinja2")
    mask_pattern: re.Pattern           # spans of engine syntax to blank out
    # Optional: extract the engine's own named definitions (macros/blocks).
    directive_extractor: Optional[Callable[[str, str, str], list[Symbol]]] = None


# Jinja2 and Twig share delimiters exactly: {{ expr }}, {% tag %}, {# comment #}.
_JINJA_MASK = re.compile(
    r"\{\{.*?\}\}"      # {{ expression }}
    r"|\{%-?.*?-?%\}"   # {% tag %} / {%- tag -%}
    r"|\{#.*?#\}",      # {# comment #}
    re.DOTALL,
)


# First cut: Jinja2 + Twig (the `name.<lang>.<engine>` double-extension engines).
# Single-extension HTML-bodied engines (Handlebars/Liquid/Mustache — page.hbs,
# index.liquid) don't carry an underlying-language extension for this feature to
# resolve, and each adds its own delimiter set; the registry is pluggable, so
# they can be added on demand.
TEMPLATE_ENGINES: dict[str, TemplateEngine] = {
    "jinja": TemplateEngine(
        name="jinja",
        extensions=(".j2", ".jinja", ".jinja2"),
        mask_pattern=_JINJA_MASK,
        directive_extractor=extract_jinja_directives,
    ),
    "twig": TemplateEngine(
        name="twig",
        extensions=(".twig",),
        mask_pattern=_JINJA_MASK,
        directive_extractor=extract_jinja_directives,
    ),
}

# extension → engine-language name, e.g. ".j2" → "jinja". Built from the registry
# so a new engine only has to declare its extensions above.
TEMPLATE_EXTENSIONS: dict[str, str] = {
    ext: engine.name
    for engine in TEMPLATE_ENGINES.values()
    for ext in engine.extensions
}

# The set of engine-language names, used by the parser/imports dispatch.
TEMPLATE_ENGINE_LANGUAGES: frozenset[str] = frozenset(TEMPLATE_ENGINES)


def mask_template_keep_offsets(text: str, engine: str) -> str:
    """Blank out an engine's constructs, preserving byte offsets and line counts.

    Like :func:`astro_shared.mask_html_comments_keep_offsets`, newlines are
    preserved so line numbers stay aligned. Unlike that helper (which blanks HTML
    *comments*), template holes sit at code positions, so each non-newline char
    is replaced with ``_`` rather than a space — an offset-preserving identifier
    filler. This keeps value-position holes syntactically valid for stricter
    grammars (``DEBUG = {{ x }}`` → ``DEBUG = _______``, which Python/TS still
    parse; a space fill would leave an empty RHS and drop the surrounding
    symbol). It is the offset-preserving form of dbt's ``__jinja__`` placeholder.

    Best-effort caveats (same contract as dbt SQL parsing): a hole at a *name*
    position (``function {{name}}()``) erases that symbol's name, and free
    template text emitted inside a block body (e.g. a ``{% macro %}`` that
    renders prose) can disrupt the declaration immediately following it.
    """
    eng = TEMPLATE_ENGINES.get(engine)
    if eng is None:
        return text

    def _repl(match: "re.Match[str]") -> str:
        return "".join("\n" if ch == "\n" else "_" for ch in match.group(0))

    return eng.mask_pattern.sub(_repl, text)
