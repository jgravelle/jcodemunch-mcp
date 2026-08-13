"""Framework profile detection for zero-config indexing.

A FrameworkProfile captures conventions auto-detected from marker files.
Profiles affect **indexing behavior** (what to ignore, what counts as an
entry point, what architectural layers exist) — separate from Context
Providers which affect symbol enrichment.

Detection is cheap (file existence checks only) and runs before directory
discovery so detected ignore patterns are applied during the initial scan.
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Layer:
    name: str
    paths: list[str]


@dataclass
class FrameworkProfile:
    name: str
    ignore_patterns: list[str] = field(default_factory=list)
    entry_point_patterns: list[str] = field(default_factory=list)
    layer_definitions: list[Layer] = field(default_factory=list)
    high_value_paths: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Built-in profiles
# ---------------------------------------------------------------------------

_LARAVEL = FrameworkProfile(
    name="laravel",
    ignore_patterns=[
        "vendor/", "node_modules/", "storage/logs/", "storage/framework/",
        "bootstrap/cache/", ".phpunit.cache/", "*.log", "*.cache",
    ],
    entry_point_patterns=[
        "routes/*.php",
        "app/Console/Commands/*.php",
        "app/Providers/*.php",
        "database/seeders/*.php",
    ],
    layer_definitions=[
        Layer("routes",      ["routes/"]),
        Layer("controllers", ["app/Http/Controllers/"]),
        Layer("requests",    ["app/Http/Requests/"]),
        Layer("services",    ["app/Services/"]),
        Layer("models",      ["app/Models/"]),
        Layer("migrations",  ["database/migrations/"]),
    ],
    high_value_paths=[
        "app/Models/", "app/Http/Controllers/", "routes/", "config/",
    ],
)

# Extension sets, named once so a profile lists WHERE its entry points live and
# not how to spell a glob.
_JS_EXTS = ("ts", "js", "mjs")
_NEXT_PAGE_EXTS = ("tsx", "jsx", "ts", "js")
_NEXT_ROUTE_EXTS = ("ts", "js")


def _entry_globs(directory: str, *exts: str) -> list[str]:
    """Entry-point globs for every file of each extension under ``directory``.

    v1.108.273 (#445). Emits TWO patterns per extension, and both are load-bearing,
    because the consumer is ``fnmatch`` (``find_dead_code._matches_any_pattern``)
    rather than a shell glob. ``fnmatch`` differs from shell globbing in two ways
    that each silently produce a pattern matching nothing:

    ⚠⚠ **No brace expansion.** ``plugins/**/*.{ts,js}`` matches a file whose name
    literally contains ``{ts,js}`` and nothing else — ``fnmatch.translate`` yields
    ``\\.\\{ts,js\\}``. v1.108.271 spelled the #435 fix that way, which did not add
    JS coverage; it **removed** the TS coverage that worked. One pattern per
    extension is the only spelling that does anything.

    ⚠⚠ **``**/`` requires a slash.** It translates to ``(?>.*?/)``, so
    ``plugins/**/*.ts`` matches ``plugins/a/b.ts`` but NOT ``plugins/auth.ts``. The
    flat form is not redundant with the nested one — without it the commonest
    layout in every one of these frameworks misses.

    Neither failure raises. A missing reachability seed just reports a genuine
    framework root as unreachable, so it has to be impossible to spell wrong rather
    than merely documented.
    """
    globs: list[str] = []
    for ext in exts:
        globs.append(f"{directory}/*.{ext}")
        globs.append(f"{directory}/**/*.{ext}")
    return globs


def _entry_named(directory: str, stem: str, *exts: str) -> list[str]:
    """Globs for a specific FILENAME under ``directory``, flat and nested.

    The framework-convention counterpart of :func:`_entry_globs` — Next.js keys on
    the file's name (``page``, ``route``, ``layout``) rather than on every file in
    the directory. Same two fnmatch constraints apply; see that docstring.
    """
    globs: list[str] = []
    for ext in exts:
        globs.append(f"{directory}/{stem}.{ext}")
        globs.append(f"{directory}/**/{stem}.{ext}")
    return globs


_NUXT = FrameworkProfile(
    name="nuxt",
    ignore_patterns=[
        "node_modules/", ".nuxt/", ".output/", "dist/", ".nitro/",
    ],
    # Both layouts are listed because both are defaults of a current major
    # version: Nuxt 3 keeps these at the root, Nuxt 4 moves them under `app/`
    # (see NuxtContextProvider._resolve_src_dir). `server/` stays at the root in
    # both, so it is deliberately NOT mirrored. A profile cannot read srcDir, so
    # naming both is the honest static approximation.
    entry_point_patterns=[
        *_entry_globs("pages", "vue"),
        *_entry_globs("plugins", *_JS_EXTS),
        *_entry_globs("middleware", *_JS_EXTS),
        *_entry_globs("app/pages", "vue"),
        *_entry_globs("app/plugins", *_JS_EXTS),
        *_entry_globs("app/middleware", *_JS_EXTS),
        *_entry_globs("server/api", *_JS_EXTS),
    ],
    layer_definitions=[
        Layer("pages",       ["pages/", "app/pages/"]),
        Layer("components",  ["components/", "app/components/"]),
        Layer("composables", ["composables/", "app/composables/"]),
        Layer("stores",      ["stores/", "app/stores/"]),
        Layer("server",      ["server/"]),
        Layer("plugins",     ["plugins/", "app/plugins/"]),
    ],
    high_value_paths=[
        "pages/", "composables/", "app/pages/", "app/composables/", "server/api/",
    ],
)

_NEXT = FrameworkProfile(
    name="next",
    ignore_patterns=[
        "node_modules/", ".next/", "out/", "dist/",
    ],
    # v1.108.273 closes the #435 remainder. The JS/JSX counterparts were deferred
    # behind PR #433 (which added the src/app layout below) and that merged
    # 2026-08-11, retiring the sequencing note that stood here.
    # Extensions are Next.js's own: pages take js/jsx/ts/tsx, route handlers and
    # middleware take js/ts only.
    entry_point_patterns=[
        *_entry_named("app", "page", *_NEXT_PAGE_EXTS),
        *_entry_named("app", "route", *_NEXT_ROUTE_EXTS),
        *_entry_named("app", "layout", *_NEXT_PAGE_EXTS),
        *_entry_named("src/app", "page", *_NEXT_PAGE_EXTS),
        *_entry_named("src/app", "route", *_NEXT_ROUTE_EXTS),
        *_entry_named("src/app", "layout", *_NEXT_PAGE_EXTS),
        *(f"middleware.{ext}" for ext in _NEXT_ROUTE_EXTS),
        *(f"src/middleware.{ext}" for ext in _NEXT_ROUTE_EXTS),
    ],
    layer_definitions=[
        Layer("pages",      ["app/", "src/app/"]),
        Layer("components", ["components/", "src/components/"]),
        Layer("lib",        ["lib/", "src/lib/"]),
        Layer("api",        ["app/api/", "src/app/api/"]),
    ],
    high_value_paths=[
        "app/", "src/app/", "lib/", "src/lib/", "components/", "src/components/",
    ],
)

_VUE_SPA = FrameworkProfile(
    name="vue-spa",
    ignore_patterns=["node_modules/", "dist/"],
    entry_point_patterns=["src/main.ts", "src/main.js", "src/App.vue"],
    layer_definitions=[
        Layer("views",      ["src/views/"]),
        Layer("components", ["src/components/"]),
        Layer("stores",     ["src/stores/", "src/store/"]),
    ],
    high_value_paths=["src/"],
)

_REACT_SPA = FrameworkProfile(
    name="react-spa",
    ignore_patterns=["node_modules/", "dist/", "build/"],
    entry_point_patterns=["src/index.tsx", "src/index.jsx", "src/App.tsx", "src/App.jsx"],
    layer_definitions=[
        Layer("components", ["src/components/"]),
        Layer("pages",      ["src/pages/"]),
        Layer("hooks",      ["src/hooks/"]),
    ],
    high_value_paths=["src/"],
)

# Flask profile
_FLASK = FrameworkProfile(
    name="flask",
    ignore_patterns=["venv/", ".venv/", "env/", "__pycache__/", "*.pyc", ".git/"],
    entry_point_patterns=["app.py", "main.py", "run.py", "*.py"],
    layer_definitions=[
        Layer("routes",     ["routes/", "*.py"]),
        Layer("models",    ["models/"]),
        Layer("templates", ["templates/"]),
        Layer("static",    ["static/"]),
    ],
    high_value_paths=["app.py", "routes/", "models/"],
)

# FastAPI profile
_FASTAPI = FrameworkProfile(
    name="fastapi",
    ignore_patterns=["venv/", ".venv/", "env/", "__pycache__/", "*.pyc", ".git/"],
    entry_point_patterns=["main.py", "app.py", "*.py"],
    layer_definitions=[
        Layer("routers",   ["routers/", "api/"]),
        Layer("models",   ["models/", "schemas/"]),
        Layer("schemas",  ["schemas/"]),
    ],
    high_value_paths=["main.py", "app.py", "routers/", "models/"],
)

# Django profile
_DJANGO = FrameworkProfile(
    name="django",
    ignore_patterns=["venv/", ".venv/", "env/", "__pycache__/", "*.pyc", ".git/", "migrations/"],
    entry_point_patterns=["manage.py", "settings.py", "urls.py", "wsgi.py", "asgi.py"],
    layer_definitions=[
        Layer("views",     ["*/views.py"]),
        Layer("models",    ["*/models.py"]),
        Layer("urls",      ["*/urls.py"]),
        Layer("migrations",["*/migrations/"]),
        Layer("templates", ["templates/"]),
        Layer("static",    ["static/"]),
    ],
    high_value_paths=["views.py", "models.py", "urls.py", "settings.py"],
)

# Express/Fastify profile
_EXPRESS = FrameworkProfile(
    name="express",
    ignore_patterns=["node_modules/", "dist/", "build/", ".next/", ".nuxt/", "coverage/"],
    entry_point_patterns=["app.js", "server.js", "index.js", "src/index.ts", "src/index.js"],
    layer_definitions=[
        Layer("routes",    ["routes/", "src/routes/"]),
        Layer("middleware", ["middleware/", "src/middleware/"]),
        Layer("controllers",["controllers/", "src/controllers/"]),
        Layer("models",     ["models/", "src/models/"]),
    ],
    high_value_paths=["app.js", "routes/", "middleware/", "controllers/"],
)

# Spring Boot profile
_SPRING_BOOT = FrameworkProfile(
    name="spring-boot",
    ignore_patterns=["target/", "build/", ".gradle/", ".git/", "src/test/"],
    entry_point_patterns=["src/main/java/**/*.java", "src/main/kotlin/**/*.kt"],
    layer_definitions=[
        Layer("controllers", ["src/main/java/**/controller/"]),
        Layer("services",    ["src/main/java/**/service/"]),
        Layer("repos",      ["src/main/java/**/repository/"]),
        Layer("models",     ["src/main/java/**/model/", "src/main/java/**/entity/"]),
    ],
    high_value_paths=["src/main/java/", "src/main/resources/"],
)

# NestJS profile
_NESTJS = FrameworkProfile(
    name="nestjs",
    ignore_patterns=["node_modules/", "dist/", ".next/", ".nuxt/", "coverage/"],
    # Exact paths, not globs — NestJS fixes both filenames by convention. Listed
    # per extension because fnmatch expands no braces; see `_entry_globs`.
    entry_point_patterns=[
        "src/main.ts", "src/main.js",
        "src/app.module.ts", "src/app.module.js",
    ],
    layer_definitions=[
        # ⚠ No in-tree consumer reads these — `profile_to_meta` publishes them to
        # callers. Spelled per extension anyway: braces are wrong under a glob
        # matcher and meaningless under a prefix one, so they cannot be right for
        # whatever a caller uses. Flat + nested for the same reason as entry globs.
        Layer("controllers", ["src/*controller.ts", "src/**/*controller.ts",
                              "src/*controller.js", "src/**/*controller.js"]),
        Layer("services",    ["src/*service.ts", "src/**/*service.ts",
                              "src/*service.js", "src/**/*service.js"]),
        Layer("modules",     ["src/*.module.ts", "src/**/*.module.ts",
                              "src/*.module.js", "src/**/*.module.js"]),
        Layer("guards",      ["src/*guard.ts", "src/**/*guard.ts",
                              "src/*guard.js", "src/**/*guard.js"]),
    ],
    high_value_paths=["src/", "modules/", "controllers/", "services/"],
)

# Gin (Go) profile
_GIN = FrameworkProfile(
    name="gin",
    ignore_patterns=["vendor/", "testdata/", ".git/", "bin/"],
    entry_point_patterns=["cmd/", "main.go", "internal/"],
    layer_definitions=[
        Layer("handlers",  ["internal/handler/", "internal/controllers/"]),
        Layer("services",   ["internal/service/"]),
        Layer("repos",     ["internal/repository/", "internal/models/"]),
        Layer("middleware", ["internal/middleware/"]),
    ],
    high_value_paths=["cmd/", "internal/", "main.go"],
)

# Rails profile
_RAILS = FrameworkProfile(
    name="rails",
    ignore_patterns=["vendor/", "node_modules/", "tmp/", "log/", "storage/", ".git/"],
    entry_point_patterns=["config/routes.rb", "config/application.rb", "config/environment.rb"],
    layer_definitions=[
        Layer("controllers", ["app/controllers/"]),
        Layer("models",      ["app/models/"]),
        Layer("views",      ["app/views/"]),
        Layer("helpers",    ["app/helpers/"]),
        Layer("channels",   ["app/channels/"]),
        Layer("jobs",       ["app/jobs/"]),
    ],
    high_value_paths=["app/controllers/", "app/models/", "config/routes.rb"],
)


# ---------------------------------------------------------------------------
# Detection logic
# ---------------------------------------------------------------------------

def _has_file(folder: Path, *parts: str) -> bool:
    return (folder / Path(*parts)).exists()


def _composer_requires(folder: Path) -> str:
    try:
        data = json.loads((folder / "composer.json").read_text("utf-8", errors="replace"))
        return " ".join(list(data.get("require", {}).keys()) + list(data.get("require-dev", {}).keys()))
    except Exception:
        return ""


def _has_requirements(folder: Path) -> str:
    """Return the content of requirements.txt if it exists."""
    req = folder / "requirements.txt"
    if req.exists():
        try:
            return req.read_text("utf-8", errors="replace")
        except Exception:
            pass
    return ""


def _has_pyproject_deps(folder: Path) -> str:
    """Return content of pyproject.toml project dependencies section."""
    pyproject = folder / "pyproject.toml"
    if pyproject.exists():
        try:
            content = pyproject.read_text("utf-8", errors="replace")
            # Simple check for django/fastapi/flask
            return content
        except Exception:
            pass
    return ""


def _has_go_mod(folder: Path) -> str:
    """Return content of go.mod if it exists."""
    go_mod = folder / "go.mod"
    if go_mod.exists():
        try:
            return go_mod.read_text("utf-8", errors="replace")
        except Exception:
            pass
    return ""


def _has_package_json(folder: Path) -> dict:
    """Return parsed package.json if it exists."""
    pkg = folder / "package.json"
    if pkg.exists():
        try:
            return json.loads(pkg.read_text("utf-8", errors="replace"))
        except Exception:
            pass
    return {}


def detect_framework(folder_path: Path) -> Optional[FrameworkProfile]:
    """Detect the primary framework in *folder_path* and return a FrameworkProfile.

    Returns ``None`` if no known framework is detected.  Detection is done via
    cheap file-existence checks in priority order — only the first match is
    returned.
    """
    # 1. Laravel (must precede generic PHP)
    if _has_file(folder_path, "artisan"):
        requires = _composer_requires(folder_path)
        if "laravel/framework" in requires:
            logger.info("Framework profile detected: laravel")
            return _LARAVEL

    # 2. Django (manage.py + django dependency)
    if _has_file(folder_path, "manage.py"):
        requires = _has_requirements(folder_path)
        pyproject = _has_pyproject_deps(folder_path)
        if "django" in requires.lower() or "django" in pyproject.lower():
            logger.info("Framework profile detected: django")
            return _DJANGO

    # 3. Spring Boot (build.gradle + spring-boot OR pom.xml + spring-boot)
    if (_has_file(folder_path, "build.gradle") or _has_file(folder_path, "pom.xml")):
        gradle = folder_path / "build.gradle"
        if gradle.exists():
            try:
                content = gradle.read_text("utf-8", errors="replace")
                if "org.springframework.boot" in content:
                    logger.info("Framework profile detected: spring-boot")
                    return _SPRING_BOOT
            except Exception:
                pass
        pom = folder_path / "pom.xml"
        if pom.exists():
            try:
                content = pom.read_text("utf-8", errors="replace")
                if "spring-boot" in content.lower():
                    logger.info("Framework profile detected: spring-boot")
                    return _SPRING_BOOT
            except Exception:
                pass

    # 4. Rails (Gemfile + rails gem AND config/routes.rb)
    if _has_file(folder_path, "config", "routes.rb"):
        gemfile = folder_path / "Gemfile"
        if gemfile.exists():
            try:
                content = gemfile.read_text("utf-8", errors="replace")
                if "gem 'rails'" in content or 'gem "rails"' in content:
                    logger.info("Framework profile detected: rails")
                    return _RAILS
            except Exception:
                pass

    # 5. NestJS (package.json + @nestjs/core)
    pkg = _has_package_json(folder_path)
    if pkg:
        deps = {}
        deps.update(pkg.get("dependencies", {}))
        deps.update(pkg.get("devDependencies", {}))
        if "@nestjs/core" in deps:
            logger.info("Framework profile detected: nestjs")
            return _NESTJS

    # 6. Nuxt.js
    if _has_file(folder_path, "nuxt.config.ts") or _has_file(folder_path, "nuxt.config.js"):
        logger.info("Framework profile detected: nuxt")
        return _NUXT

    # 7. Next.js
    if (
        _has_file(folder_path, "next.config.js")
        or _has_file(folder_path, "next.config.ts")
        or _has_file(folder_path, "next.config.mjs")
    ):
        logger.info("Framework profile detected: next")
        return _NEXT

    # 8. Express/Fastify/Hono/Koa (package.json + express/fastify/hono/koa)
    if pkg:
        deps = {}
        deps.update(pkg.get("dependencies", {}))
        deps.update(pkg.get("devDependencies", {}))
        for fw in ("express", "fastify", "hono"):
            if fw in deps:
                logger.info("Framework profile detected: %s", fw)
                return _EXPRESS
        # Koa with koa-router
        if "koa" in deps and ("@koa/router" in deps or "koa-router" in deps):
            logger.info("Framework profile detected: express (koa)")
            return _EXPRESS

    # 9. Flask (requirements.txt/pyproject.toml + flask, but NOT fastapi)
    requires = _has_requirements(folder_path)
    pyproject = _has_pyproject_deps(folder_path)
    if ("flask" in requires.lower() or "flask" in pyproject.lower()) and \
       not ("fastapi" in requires.lower() or "fastapi" in pyproject.lower()):
        logger.info("Framework profile detected: flask")
        return _FLASK

    # 10. FastAPI (requirements.txt/pyproject.toml + fastapi)
    if "fastapi" in requires.lower() or "fastapi" in pyproject.lower():
        logger.info("Framework profile detected: fastapi")
        return _FASTAPI

    # 11. Gin/Chi/Echo/Fiber (go.mod)
    go_mod = _has_go_mod(folder_path)
    if go_mod:
        for fw, module in [("gin", "github.com/gin-gonic/gin"),
                           ("chi", "github.com/go-chi/chi"),
                           ("echo", "github.com/labstack/echo"),
                           ("fiber", "github.com/gofiber/fiber")]:
            if module in go_mod:
                logger.info("Framework profile detected: %s", fw)
                return _GIN  # Use Gin profile for all Go frameworks

    # 12. Vue SPA (vite + App.vue, no Nuxt marker)
    if _has_file(folder_path, "vite.config.ts") or _has_file(folder_path, "vite.config.js"):
        if _has_file(folder_path, "src", "App.vue"):
            logger.info("Framework profile detected: vue-spa")
            return _VUE_SPA
        if _has_file(folder_path, "src", "App.tsx") or _has_file(folder_path, "src", "App.jsx"):
            logger.info("Framework profile detected: react-spa")
            return _REACT_SPA

    return None


def profile_to_meta(profile: FrameworkProfile) -> dict:
    """Serialize a FrameworkProfile for storage in context_metadata."""
    return {
        "framework_profile": {
            "name": profile.name,
            "entry_point_patterns": profile.entry_point_patterns,
            "layer_definitions": [{"name": l.name, "paths": l.paths} for l in profile.layer_definitions],
            "high_value_paths": profile.high_value_paths,
        }
    }
