"""The one place that tells a caller how to obtain an embedding provider.

⚠⚠ Three sites carried this advice independently and two of them omitted the
bundled zero-config ONNX encoder entirely, naming only the three env-var
providers — two of which bill per call (#489). `embed_repo`'s copy was updated
when ONNX landed at priority 0; the `semantic` parameter description and
`search_symbols`' `no_embedding_provider` error were not.

That is expensive in one direction specifically. The parameter description is
not documentation a human browses — it is the tool schema, and it is the ONLY
information an agent has when deciding whether to set `semantic: true`. An agent
reading "requires one of three env vars" against an environment with none of
them set correctly concludes semantic search is unavailable, and never attempts
it, on a machine where it works for free. There is no error, no warning and no
degraded result: the capability simply goes unused.

Keep `_LOCAL_FIRST` first. The ordering mirrors `_detect_provider`'s priority,
so the advice and the resolver cannot disagree about which one wins.
"""

# The bundled encoder. Priority 0 in `_detect_provider`, free, local, and the
# project's recommended setup — so it leads every list.
_LOCAL_FIRST = "pip install 'jcodemunch-mcp[local-embed]' (zero-config ONNX, recommended)"

_ENV_PROVIDERS = (
    "JCODEMUNCH_EMBED_MODEL (sentence-transformers, free/local), "
    "GOOGLE_API_KEY + GOOGLE_EMBED_MODEL (Gemini), or "
    "OPENAI_API_KEY + OPENAI_EMBED_MODEL (OpenAI)"
)

#: Full sentence for a runtime error, where the caller has already failed and
#: needs the remedy.
NO_PROVIDER_MESSAGE = (
    f"No embedding provider is configured. Options: {_LOCAL_FIRST}, "
    f"{_ENV_PROVIDERS}."
)

#: Clause for a tool schema, where the caller is deciding whether to try at all.
#: Shorter than the error on purpose — schema text is paid on every request that
#: carries it, and `search_symbols` is a core-tier tool.
#:
#: ⚠ Semicolon, not "or", before the env list: `_ENV_PROVIDERS` already ends in
#: "or OPENAI…", so joining with "or" yields two of them in one sentence.
PROVIDER_HINT = (
    f"Requires an embedding provider: {_LOCAL_FIRST}; otherwise {_ENV_PROVIDERS}."
)
