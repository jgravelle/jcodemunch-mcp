"""#489: two of three provider-advice sites omitted the bundled ONNX encoder.

Three places tell a caller how to obtain an embedding provider. `embed_repo`'s
error led with `pip install 'jcodemunch-mcp[local-embed]'` and marked it
recommended. The `semantic` parameter description and `search_symbols`'
`no_embedding_provider` error named only the three env-var providers — two of
which bill per call — and omitted the free, local, already-bundled one that
outranks all three in `_detect_provider`.

⚠ The parameter description is the expensive one. It is not documentation a
human browses; it is the tool schema, and it is the only information an agent
has when deciding whether to set `semantic: true`. An agent reading "requires
one of three env vars" against an environment with none of them set correctly
concludes semantic search is unavailable and never attempts it, on a machine
where it works for free. No error, no warning, no degraded result — the
capability simply goes unused.

The drift is the point: site 3 was updated when ONNX landed at priority 0 and
the other two were not, because each carried its own copy.
"""

import re

import pytest

from jcodemunch_mcp.embeddings.advice import (
    NO_PROVIDER_MESSAGE,
    PROVIDER_HINT,
    _LOCAL_FIRST,
)

LOCAL_EXTRA = "jcodemunch-mcp[local-embed]"


def _semantic_description():
    """The `semantic` param description as an agent receives it."""
    from jcodemunch_mcp import config as config_module
    from jcodemunch_mcp.server import _build_tools_list

    cfg = config_module._GLOBAL_CONFIG
    original = {k: cfg.get(k) for k in ("tool_profile", "compact_schemas")}
    try:
        cfg["tool_profile"] = "full"
        cfg["compact_schemas"] = False
        for tool in _build_tools_list():
            if tool.name == "search_symbols":
                props = tool.inputSchema.get("properties", {}) or {}
                return (props.get("semantic") or {}).get("description", "")
    finally:
        for k, v in original.items():
            if v is None:
                cfg.pop(k, None)
            else:
                cfg[k] = v
    return ""


class TestEverySiteNamesTheBundledEncoder:
    """One assertion per site, so a failure names which one drifted."""

    def test_the_semantic_parameter_description(self):
        description = _semantic_description()
        assert description, "search_symbols has no `semantic` param on the full surface"
        assert LOCAL_EXTRA in description, (
            "the tool schema an agent reads to decide whether semantic search is "
            f"available does not mention the bundled encoder: {description!r}"
        )

    def test_the_search_symbols_runtime_error(self):
        """⚠ Asserts on search_symbols' SOURCE, not on the constant.

        The first version of this test imported `NO_PROVIDER_MESSAGE` and
        asserted the extra was in it — which is true the moment the constant
        exists, whether or not `search_symbols` uses it. It checked the fix
        instead of the site, and would have passed against a tree where this
        site still carried its stale copy.
        """
        import inspect

        from jcodemunch_mcp.tools import search_symbols

        source = inspect.getsource(search_symbols)
        assert "NO_PROVIDER_MESSAGE" in source
        assert "No embedding provider is configured. Set one of:" not in source, (
            "search_symbols still carries its own copy of the advice"
        )

    def test_the_embed_repo_runtime_error(self):
        """Site 3 was already correct; it is now the shared source rather than a
        third independent copy."""
        import inspect

        from jcodemunch_mcp.tools import embed_repo

        source = inspect.getsource(embed_repo)
        assert "NO_PROVIDER_MESSAGE" in source
        assert "zero-config ONNX, recommended" not in source, (
            "embed_repo still carries its own copy of the advice"
        )


class TestTheBundledEncoderLeads:
    """Naming it is not enough — `_detect_provider` returns it at priority 0, so
    advice that buries it still describes the design backwards."""

    @pytest.mark.parametrize(
        "text", [NO_PROVIDER_MESSAGE, PROVIDER_HINT],
        ids=["runtime_error", "schema_hint"],
    )
    def test_it_is_named_before_any_key_requiring_provider(self, text):
        local_at = text.index(LOCAL_EXTRA)
        for env_var in ("JCODEMUNCH_EMBED_MODEL", "GOOGLE_API_KEY", "OPENAI_API_KEY"):
            assert local_at < text.index(env_var), (
                f"{env_var} is named before the bundled encoder"
            )

    @pytest.mark.parametrize(
        "text", [NO_PROVIDER_MESSAGE, PROVIDER_HINT],
        ids=["runtime_error", "schema_hint"],
    )
    def test_no_doubled_conjunction(self, text):
        """⚠ `_ENV_PROVIDERS` already ends in "or OPENAI…", so joining it with
        "or" produced two in one sentence. Caught by reading the rendered string,
        not the template."""
        assert not re.search(r"\bor\b[^.]*\bor\b[^.]*\bor\b", text), (
            f"more than two 'or's in one sentence: {text!r}"
        )


class TestTheSitesCannotDriftApartAgain:
    """The defect was three independent copies, one of which was maintained."""

    def test_no_module_hardcodes_the_provider_list(self):
        """A second copy of the advice is what created this issue."""
        import pathlib

        from jcodemunch_mcp import embeddings

        root = pathlib.Path(embeddings.__file__).resolve().parent.parent
        advice = (root / "embeddings" / "advice.py").resolve()
        offenders = []
        for path in sorted(root.rglob("*.py")):
            if path.resolve() == advice:
                continue  # the one legitimate home
            text = path.read_text(encoding="utf-8", errors="replace")
            for lineno, line in enumerate(text.splitlines(), 1):
                # An ENUMERATION is the thing that drifts. A line naming two of
                # the env providers is a copy of the list regardless of wording.
                if "GOOGLE_EMBED_MODEL" in line and "OPENAI_EMBED_MODEL" in line:
                    offenders.append(f"{path.relative_to(root)}:{lineno}")
                # ⚠ Phrasing-agnostic on purpose. The first version of this
                # matched the literal "No embedding provider is configured" and
                # missed `embed_drift.py`, which said "No embedding provider
                # configured" without the "is" — caught only by the clause
                # above, i.e. by luck. A ratchet keyed to one spelling of a
                # sentence guards that spelling, not the defect.
                elif re.search(r"No embedding provider (is )?configured\.", line):
                    offenders.append(f"{path.relative_to(root)}:{lineno}")
        assert not offenders, (
            "these carry their own copy of the provider advice instead of "
            "importing it, which is how two of three fell out of date: "
            + ", ".join(offenders)
        )

    def test_the_shared_constants_share_one_source(self):
        """Both strings must derive from `_LOCAL_FIRST`, or "lead with the
        bundled encoder" can be true of one and false of the other."""
        assert _LOCAL_FIRST in NO_PROVIDER_MESSAGE
        assert _LOCAL_FIRST in PROVIDER_HINT


class TestTheBudgetIsUnchanged:
    """⚠ `semantic` is in `_COMPACT_STRIP_PARAMS`, so this description never
    reaches the compact schema and costs the core budget nothing. That was
    measured, not assumed - the initial read of this issue assumed the opposite
    and would have trimmed a description for no reason."""

    def test_semantic_is_absent_from_the_compact_schema(self):
        from jcodemunch_mcp import config as config_module
        from jcodemunch_mcp.server import _build_tools_list

        cfg = config_module._GLOBAL_CONFIG
        original = {k: cfg.get(k) for k in ("tool_profile", "compact_schemas")}
        try:
            cfg["tool_profile"] = "core"
            cfg["compact_schemas"] = True
            props = {}
            for tool in _build_tools_list():
                if tool.name == "search_symbols":
                    props = tool.inputSchema.get("properties", {}) or {}
                    break
        finally:
            for k, v in original.items():
                if v is None:
                    cfg.pop(k, None)
                else:
                    cfg[k] = v
        assert "semantic" not in props, (
            "`semantic` now reaches the compact schema, so this description is "
            "charged against the hard 4000-token core_compact ceiling"
        )
