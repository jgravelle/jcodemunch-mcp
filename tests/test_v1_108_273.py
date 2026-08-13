"""#445 — framework entry-point patterns must MATCH FILES, not merely name extensions.

v1.108.271 fixed #435 for `nuxt` and `nestjs` by rewriting their entry patterns as
brace alternation (`{ts,js}`). `fnmatch` expands no braces, so the patterns matched
nothing at all: the change did not add JS coverage, it removed the TS coverage that
worked. A stock NestJS project went from two entry points to zero.

⚠⚠ **The guard did not fail, and that is the more important half.** The #435 sweep
asks which extensions appear in a pattern STRING. A brace pattern names both `ts`
and `js`, so it satisfied the sweep while matching no file on disk. Every test here
is therefore about EFFECT — patterns are run against realistic paths through the
same matcher the tools use — because a spelling check cannot distinguish a fix from
a plausible-looking non-fix.

Also closes the #435 remainder: `next` gained its JS/JSX counterparts once PR #433
merged, and its exemption is retired from `_JS_VARIANT_EXEMPT`.
"""

import fnmatch

import pytest

from jcodemunch_mcp.parser.context import framework_profiles as fp
from jcodemunch_mcp.parser.context.framework_profiles import _entry_globs, _entry_named
from jcodemunch_mcp.tools.find_dead_code import _matches_any_pattern


def _profiles() -> dict:
    """Every FrameworkProfile in the module, keyed by its own name."""
    return {
        obj.name: obj
        for obj in vars(fp).values()
        if isinstance(obj, fp.FrameworkProfile)
    }


# Realistic entry-point files per profile: the paths a stock scaffold produces.
# Both a FLAT file and a NESTED one per framework, because `**/` requires a slash
# under fnmatch and the flat case is the commonest layout.
_REAL_ENTRY_FILES = {
    "nestjs": ["src/main.ts", "src/app.module.ts", "src/main.js", "src/app.module.js"],
    "nuxt": [
        "pages/index.vue", "pages/blog/[slug].vue",
        "plugins/auth.ts", "plugins/nested/thing.ts", "plugins/auth.js",
        "middleware/guard.ts", "server/api/users.ts",
        "app/pages/index.vue", "app/plugins/auth.ts",
    ],
    "next": [
        "app/page.tsx", "app/blog/page.tsx", "app/page.jsx", "app/page.js",
        "app/route.ts", "app/api/route.ts", "app/api/route.js",
        "app/layout.tsx", "app/layout.jsx",
        "src/app/page.tsx", "src/app/blog/page.jsx", "src/app/layout.tsx",
        "middleware.ts", "middleware.js", "src/middleware.ts",
    ],
}


@pytest.mark.parametrize("profile_name", sorted(_REAL_ENTRY_FILES))
def test_entry_patterns_match_real_scaffold_files(profile_name):
    """The load-bearing test: every listed file is recognised as an entry point.

    Run through `_matches_any_pattern`, the real matcher both dead-code tools use,
    rather than a reimplementation — the defect lived in the gap between what the
    patterns looked like and what that function does with them.
    """
    profile = _profiles()[profile_name]
    missed = [
        f for f in _REAL_ENTRY_FILES[profile_name]
        if not _matches_any_pattern(f, profile.entry_point_patterns)
    ]
    assert not missed, f"{profile_name} entry patterns match no file for: {missed}"


def test_the_regression_itself_nestjs_typescript_roots():
    """The exact v1.108.271 regression, pinned as its own case.

    Both were True at v1.108.270 and False at v1.108.271. NestJS is TypeScript-first
    by convention, so this was the common path, not an edge one.
    """
    nestjs = _profiles()["nestjs"]
    for f in ("src/main.ts", "src/app.module.ts"):
        assert _matches_any_pattern(f, nestjs.entry_point_patterns), f


def test_no_profile_ships_a_brace_pattern():
    """Braces are never correct here: fnmatch treats them as literal characters.

    A pattern-wide ban rather than a per-profile fix, because the defect is a
    spelling that LOOKS like it covers two extensions.
    """
    offenders = []
    for name, profile in _profiles().items():
        globs = list(profile.entry_point_patterns) + list(profile.high_value_paths)
        for layer in profile.layer_definitions:
            globs += layer.paths
        offenders += [f"{name}: {g}" for g in globs if "{" in g or "}" in g]
    assert not offenders, f"brace patterns match nothing under fnmatch: {offenders}"


def test_fnmatch_really_does_not_expand_braces():
    """Pins the premise, so the ban above cannot be dismissed as superstition.

    If a future Python grows brace expansion this fails and the rule is revisited
    deliberately rather than silently kept.
    """
    assert not fnmatch.fnmatch("src/main.ts", "src/main.{ts,js}")
    assert not fnmatch.fnmatch("src/main.js", "src/main.{ts,js}")
    assert r"\{ts,js\}" in fnmatch.translate("src/main.{ts,js}")


def test_double_star_requires_a_slash_so_flat_globs_are_not_redundant():
    """The second fnmatch surprise, and why `_entry_globs` emits two patterns.

    `**/` translates to `(?>.*?/)`. Without the flat form, `plugins/auth.ts` — the
    commonest shape there is — misses.
    """
    assert not fnmatch.fnmatch("plugins/auth.ts", "plugins/**/*.ts")
    assert fnmatch.fnmatch("plugins/a/b.ts", "plugins/**/*.ts")
    globs = _entry_globs("plugins", "ts")
    assert _matches_any_pattern("plugins/auth.ts", globs)
    assert _matches_any_pattern("plugins/a/b.ts", globs)


def test_entry_globs_emits_both_shapes_per_extension():
    assert _entry_globs("plugins", "ts", "js") == [
        "plugins/*.ts", "plugins/**/*.ts",
        "plugins/*.js", "plugins/**/*.js",
    ]


def test_entry_named_emits_both_shapes_per_extension():
    assert _entry_named("app", "page", "tsx") == [
        "app/page.tsx", "app/**/page.tsx",
    ]


def test_next_js_variants_landed_and_its_exemption_is_retired():
    """#435's remainder. The two must happen together or the ratchet fails."""
    from tests.test_nuxt_srcdir import _JS_VARIANT_EXEMPT

    assert "next" not in _JS_VARIANT_EXEMPT, (
        "next is fixed; its exemption must be deleted in the same change"
    )
    nxt = _profiles()["next"]
    for f in ("app/page.jsx", "app/page.js", "app/route.js", "middleware.js"):
        assert _matches_any_pattern(f, nxt.entry_point_patterns), f


def test_a_non_entry_file_is_still_not_an_entry_point():
    """The patterns got broader; they must not have become a wildcard.

    Without this, replacing every pattern with `*` would pass every test above.
    """
    nxt = _profiles()["next"]
    for f in ("components/Button.tsx", "lib/util.ts", "app/helpers.ts"):
        assert not _matches_any_pattern(f, nxt.entry_point_patterns), f
    nestjs = _profiles()["nestjs"]
    assert not _matches_any_pattern("src/user.service.ts", nestjs.entry_point_patterns)
