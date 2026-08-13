"""Nuxt 4 srcDir support (#434) and the JS-extension gap in profiles (#435).

Nuxt 4 changed the DEFAULT srcDir to `app/`, so a stock Nuxt 4 project kept
pages at `app/pages/` while the provider probed only root `pages/`. Framework
detected, zero routes, no warning.

⚠ A fixture that only ever builds the Nuxt 3 root layout cannot fail on this
class, which is why every pre-existing nuxt test passed throughout.
"""
from pathlib import Path

import pytest


def _write(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _nuxt_project(root: Path, config: str = "export default defineNuxtConfig({})") -> None:
    _write(root / "nuxt.config.ts", config)


def _provider():
    from jcodemunch_mcp.parser.context.nuxt import NuxtContextProvider
    return NuxtContextProvider()


# ---------------------------------------------------------------------------
# JS-variant sweep (#435)
# ---------------------------------------------------------------------------

# Profiles allowed to carry TS-only globs, named with the reason and the issue
# that retires the exemption.
#
# ⚠ Exemptions live HERE, not in a parametrize list. `next` was previously
# exempt by being absent from one, which is indistinguishable from nobody
# having thought about it, and is the shape tests/test_constant_extraction_guard.py
# was built to avoid for #428: "an exemption outliving its defect is how a
# ratchet rots into a permanent allowance."
# ⚠ v1.108.273: `next` retired from this dict. PR #433 merged 2026-08-11, and the
# JS/JSX counterparts landed in the same release that fixed #445. The ratchet below
# is what forced the two to happen together — an empty dict is the intended end
# state, not a sign the mechanism was abandoned.
_JS_VARIANT_EXEMPT: dict[str, str] = {}


def _profile_by_name() -> dict:
    """Every FrameworkProfile defined in the module, keyed by its own name.

    Enumerated rather than listed, so a profile added later is swept without
    anyone remembering to add it. #435 exists because three profiles got the
    JS variants right by attention and three did not.
    """
    from jcodemunch_mcp.parser.context import framework_profiles as fp
    return {
        obj.name: obj
        for obj in vars(fp).values()
        if isinstance(obj, fp.FrameworkProfile)
    }


# A TS extension and the JS extensions that count as covering it.
_TS_TO_JS = {
    ".tsx": (".jsx", ".js"),
    ".ts":  (".js", ".mjs"),
}


def _ts_only_globs(profile) -> list[str]:
    """TS-pinned globs with no JS counterpart.

    ⚠ Two spellings both count as covered, and an earlier version of this
    helper only understood one. `nuxt` and `nestjs` use brace alternation
    (`plugins/**/*.{ts,js,mjs}`); `vue-spa`, `react-spa` and `express` list
    the extensions as separate sibling entries (`src/main.ts` alongside
    `src/main.js`). Flagging the second group would have been a false
    failure against three profiles that are already correct.
    """
    globs = list(profile.entry_point_patterns)
    for layer in profile.layer_definitions:
        globs += layer.paths
    globs += list(profile.high_value_paths)

    present = set(globs)
    offenders = []
    for glob in globs:
        # ⚠⚠ v1.108.273 (#445). This used to `continue` on a brace pattern, i.e.
        # count `{ts,js}` as JS coverage. fnmatch expands no braces, so it was
        # counting a pattern that matches NOTHING as the fix — which is how
        # v1.108.271 shipped nuxt and nestjs with zero working entry points and a
        # green sweep. Brace patterns are now an offender, never an exemption; the
        # dedicated check lives in test_v1_108_273.py.
        for ts_ext, js_exts in _TS_TO_JS.items():
            if not glob.endswith(ts_ext):
                continue
            stem = glob[: -len(ts_ext)]
            if not any(stem + js_ext in present for js_ext in js_exts):
                offenders.append(glob)
            break
    return offenders


def pytest_generate_tests(metafunc):
    if "profile_name" in metafunc.fixturenames:
        names = sorted(set(_profile_by_name()) - set(_JS_VARIANT_EXEMPT))
        metafunc.parametrize("profile_name", names)
    if "exempt_name" in metafunc.fixturenames:
        metafunc.parametrize("exempt_name", sorted(_JS_VARIANT_EXEMPT))


# ---------------------------------------------------------------------------
# srcDir resolution
# ---------------------------------------------------------------------------

class TestResolveSrcDir:
    def test_nuxt3_root_layout_resolves_to_root(self, tmp_path):
        _nuxt_project(tmp_path)
        _write(tmp_path / "pages" / "index.vue", "<template/>")
        assert _provider()._resolve_src_dir(tmp_path) == ""

    def test_nuxt4_app_layout_resolves_to_app(self, tmp_path):
        _nuxt_project(tmp_path)
        _write(tmp_path / "app" / "pages" / "index.vue", "<template/>")
        assert _provider()._resolve_src_dir(tmp_path) == "app"

    def test_explicit_srcdir_in_config_wins(self, tmp_path):
        """Config is the actual answer; the probe is only a fallback."""
        _nuxt_project(tmp_path, "export default defineNuxtConfig({ srcDir: 'src/' })")
        _write(tmp_path / "app" / "pages" / "index.vue", "<template/>")
        _write(tmp_path / "src" / "pages" / "index.vue", "<template/>")
        assert _provider()._resolve_src_dir(tmp_path) == "src"

    @pytest.mark.parametrize("raw,expected", [
        ("'app'", "app"), ('"app/"', "app"), ("'./src'", "src"), ("'src/'", "src"),
    ])
    def test_srcdir_value_is_normalised(self, tmp_path, raw, expected):
        _nuxt_project(tmp_path, f"export default defineNuxtConfig({{ srcDir: {raw} }})")
        assert _provider()._resolve_src_dir(tmp_path) == expected

    def test_unrelated_app_dir_does_not_hijack_the_layout(self, tmp_path):
        """An `app/` with no Nuxt-shaped child must not relocate the parse.

        Guessing wrong here moves the whole scan, so the probe requires a
        recognised child rather than merely the directory existing.
        """
        _nuxt_project(tmp_path)
        _write(tmp_path / "app" / "notes.txt", "unrelated")
        _write(tmp_path / "pages" / "index.vue", "<template/>")
        assert _provider()._resolve_src_dir(tmp_path) == ""


# ---------------------------------------------------------------------------
# The defect: stock Nuxt 4 indexed zero routes
# ---------------------------------------------------------------------------

class TestNuxt4DefaultLayout:
    def test_app_pages_produce_routes(self, tmp_path):
        _nuxt_project(tmp_path)
        _write(tmp_path / "app" / "pages" / "index.vue", "<template/>")
        _write(tmp_path / "app" / "pages" / "users" / "[id].vue", "<template/>")

        p = _provider()
        assert p.detect(tmp_path)
        p.load(tmp_path)

        assert p.stats()["page_routes"] == 2
        ctx = p.get_file_context("app/pages/index.vue")
        assert ctx is not None and ctx.properties["route"] == "/"
        assert p.get_file_context("app/pages/users/[id].vue").properties["route"] == "/users/:id"

    def test_app_composables_produce_auto_imports(self, tmp_path):
        """The knock-on half: an empty map returns early and kills every edge."""
        _nuxt_project(tmp_path)
        _write(tmp_path / "app" / "pages" / "index.vue", "<template/>")
        _write(tmp_path / "app" / "composables" / "useAuth.ts", "export function useAuth() {}")
        _write(tmp_path / "app" / "utils" / "fmt.ts", "export function fmt() {}")

        p = _provider()
        assert p.detect(tmp_path)
        p.load(tmp_path)
        assert set(p._auto_import_symbols) == {"useAuth", "fmt"}

    def test_server_api_stays_at_root_under_app_layout(self, tmp_path):
        """`server/` does NOT move under app/ in Nuxt 4. Root must still win."""
        _nuxt_project(tmp_path)
        _write(tmp_path / "app" / "pages" / "index.vue", "<template/>")
        _write(tmp_path / "server" / "api" / "health.ts", "export default defineEventHandler(() => {})")

        p = _provider()
        assert p.detect(tmp_path)
        p.load(tmp_path)
        assert p.stats()["api_routes"] == 1
        assert p.get_file_context("server/api/health.ts") is not None

    def test_nested_server_api_is_an_additive_fallback(self, tmp_path):
        """Only consulted when root server/api is absent, so Nuxt 4 is unaffected."""
        _nuxt_project(tmp_path, "export default defineNuxtConfig({ srcDir: 'src' })")
        _write(tmp_path / "src" / "pages" / "index.vue", "<template/>")
        _write(tmp_path / "src" / "server" / "api" / "ping.ts",
               "export default defineEventHandler(() => {})")

        p = _provider()
        assert p.detect(tmp_path)
        p.load(tmp_path)
        assert p.stats()["api_routes"] == 1


class TestNuxt3StillWorks:
    """Controls. These pass before AND after; the root layout must not move."""

    def test_root_pages_unchanged(self, tmp_path):
        _nuxt_project(tmp_path)
        _write(tmp_path / "pages" / "index.vue", "<template/>")
        _write(tmp_path / "pages" / "blog" / "[slug].vue", "<template/>")

        p = _provider()
        assert p.detect(tmp_path)
        p.load(tmp_path)
        assert p.stats()["page_routes"] == 2
        assert p.get_file_context("pages/index.vue").properties["route"] == "/"
        assert p.get_file_context("pages/blog/[slug].vue").properties["route"] == "/blog/:slug"

    def test_root_composables_unchanged(self, tmp_path):
        _nuxt_project(tmp_path)
        _write(tmp_path / "composables" / "useThing.ts", "export function useThing() {}")
        p = _provider()
        assert p.detect(tmp_path)
        p.load(tmp_path)
        assert "useThing" in p._auto_import_symbols


# ---------------------------------------------------------------------------
# #435: profile patterns
# ---------------------------------------------------------------------------

class TestProfilePatterns:
    def test_nuxt_profile_covers_both_layouts(self, tmp_path):
        from jcodemunch_mcp.parser.context.framework_profiles import _NUXT
        pats = _NUXT.entry_point_patterns
        assert any(p.startswith("app/pages/") for p in pats)
        assert any(p.startswith("pages/") for p in pats)
        # server/ stays at the root in both layouts, so it must NOT be mirrored.
        assert not any(p.startswith("app/server/") for p in pats)

    def test_nuxt_layers_cover_both_layouts(self):
        from jcodemunch_mcp.parser.context.framework_profiles import _NUXT
        by_name = {layer.name: layer.paths for layer in _NUXT.layer_definitions}
        for name in ("pages", "components", "composables", "stores", "plugins"):
            assert any(p.startswith("app/") for p in by_name[name]), name
        assert by_name["server"] == ["server/"]

    def test_ts_patterns_carry_js_variants(self, profile_name):
        """A TS-only entry-point list gives a JS project no reachability seed.

        Every profile is swept, so a profile added later is covered by
        construction rather than by whoever writes it remembering. Non-JS
        profiles carry no `.ts` globs and pass trivially.
        """
        profile = _profile_by_name()[profile_name]
        offenders = _ts_only_globs(profile)
        assert not offenders, f"{profile_name} has TS-only globs: {offenders}"

    def test_exempt_profiles_still_warrant_their_exemption(self, exempt_name):
        """The ratchet. An exemption must expire the moment its defect does.

        ⚠ Without this, `next` was exempt by ABSENCE from a parametrize list,
        with only a docstring saying to remove it when #433 lands. Nothing
        failed if nobody read the docstring, so the deferral could outlive
        the reason for it and #435 could be closed on the two thirds that
        shipped. Modelled on tests/test_constant_extraction_guard.py, which
        got this right for #428 on the same day this got it wrong.

        Fixing an exempt profile makes THIS test fail. That is intended: the
        fix and the deletion of its exemption land together or not at all.

        It also doubles as the non-vacuity proof for the sweep above. That
        sweep asserts `_ts_only_globs` returns EMPTY for 12 profiles, which a
        helper broken to always return empty would also satisfy. This one
        asserts it returns NON-empty for `next`, so the two together show the
        helper discriminates rather than just agreeing.
        """
        profile = _profile_by_name()[exempt_name]
        assert _ts_only_globs(profile), (
            f"{exempt_name} no longer has TS-only globs, so its entry in "
            f"_JS_VARIANT_EXEMPT is spent. Delete it: "
            f"{_JS_VARIANT_EXEMPT[exempt_name]}"
        )
