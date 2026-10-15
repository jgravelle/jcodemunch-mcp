"""Express.js / JS Router provider for Express, Fastify, Hono, and Koa.

This provider detects JavaScript/TypeScript frameworks that use call-expression routing
(e.g., app.get('/path', handler)) and extracts routes and middleware.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from ._route_utils import iter_source_files, read_package_json, make_route_file_context
from .base import ContextProvider, FileContext, register_provider

logger = logging.getLogger(__name__)

_JS_SUFFIXES = frozenset({".js", ".ts", ".mjs", ".jsx", ".tsx"})


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

_JS_FRAMEWORKS = {
    "express": "express",
    "fastify": "fastify",
    "hono": "hono",
    "koa": "koa",
}

# Pattern: app.get("/path", handler) or router.post("/path", mw, handler)
# ⚠ Do NOT reintroduce a leading ``(?:\w+)`` before the ``\.``. It is uncaptured
# and asserts nothing the ``.`` does not already imply, but it makes the scan
# QUADRATIC: at every offset inside an unbroken word run the engine matches the
# run greedily, fails on the ``.``, then backtracks a character at a time. One
# minified chunk or base64 blob in a single source file is enough. Measured on a
# lone ``\w`` run: 8k chars 0.45s, 32k 7.5s, 128k over 120s. That is jcm#375,
# where provider discovery burned 4 minutes at 100% CPU before file indexing even
# began. Without the prefix the same scans are ~0.00006s at 128k.
_JS_ROUTE = re.compile(
    r"\.\s*(?P<method>get|post|put|patch|delete|all|head|options)\s*\(\s*"
    r"[\"'](?P<path>[^\"']+)[\"']",
    re.IGNORECASE,
)

# Pattern: app.use(mw) or app.use("/prefix", mw)
_JS_MIDDLEWARE = re.compile(
    r"\.\s*use\s*\(\s*(?:[\"'](?P<path>[^\"']+)[\"'])?\s*,?\s*(?P<handler>\w+)",
)

# Pattern: app.use("/api", usersRouter)
_JS_MOUNT = re.compile(
    r"\.\s*use\s*\(\s*[\"'](?P<prefix>[^\"']+)[\"']\s*,\s*(?P<router>\w+)\s*\)",
)


@register_provider
class ExpressProvider(ContextProvider):
    """Context provider for Express, Fastify, Hono, and Koa frameworks.

    Detects via package.json and extracts routes/middleware from JS/TS source files.
    """

    def __init__(self) -> None:
        self._folder: Optional[Path] = None
        self._framework: Optional[str] = None
        self._file_contexts: dict[str, FileContext] = {}
        self._extra_imports: dict[str, list[dict]] = {}

    @property
    def name(self) -> str:
        return "express"

    def detect(self, folder: Path) -> bool:
        """Detect Express/Fastify/Hono/Koa via package.json."""
        pkg = read_package_json(folder)
        deps = {}
        deps.update(pkg.get("dependencies", {}))
        deps.update(pkg.get("devDependencies", {}))

        for fw_name, pkg_name in _JS_FRAMEWORKS.items():
            if pkg_name in deps:
                self._framework = fw_name
                logger.info("Express provider: detected %s", fw_name)
                return True

        # Also check for Koa with koa-router
        if "koa" in deps and ("@koa/router" in deps or "koa-router" in deps):
            self._framework = "koa"
            logger.info("Express provider: detected koa")
            return True

        return False

    def load(self, folder: Path) -> None:
        """Scan JS/TS files for routes and middleware."""
        self._folder = folder
        framework = self._framework or "express"

        route_count = 0
        file_count = 0
        scanned = 0
        abandoned = False

        # One pruned walk, not five recursive globs. The previous form ran
        # ``folder.glob("**/*.js")`` (and four siblings) and dropped
        # node_modules by substring AFTER the walk had already enumerated it —
        # five full descents through the dependency tree to index none of it.
        for file_path, rel_path in iter_source_files(folder, _JS_SUFFIXES):
            if self.budget_expired():
                abandoned = True
                break
            try:
                content = file_path.read_text("utf-8", errors="replace")
            except Exception:
                continue
            scanned += 1

            routes = self._extract_routes(content)
            middleware = self._extract_middleware(content)
            mounts = self._extract_mounts(content, rel_path)

            if routes or middleware:
                if routes:
                    ctx = make_route_file_context(framework, routes, kind="route")
                    self._file_contexts[rel_path] = ctx
                    route_count += len(routes)
                elif middleware:
                    ctx = make_route_file_context(framework, middleware, kind="middleware")
                    self._file_contexts[rel_path] = ctx

                file_count += 1

            # Add mount edges
            for mount_info in mounts:
                target_file = self._find_router_file(mount_info["router"], rel_path)
                if target_file:
                    self._extra_imports.setdefault(rel_path, []).append({
                        "specifier": target_file,
                        "names": [mount_info["router"]],
                    })

        if abandoned:
            logger.warning(
                "Express provider: budget expired after %d files; the provider is "
                "skipped for this pass and re-runs on the next index (L-190)", scanned,
            )
        logger.info(
            "Express provider: found %d routes in %d files (%s)",
            route_count, file_count, framework,
        )

    def _extract_routes(self, content: str) -> list[dict]:
        """Extract route dicts from content."""
        routes = []
        for m in _JS_ROUTE.finditer(content):
            verb = m.group("method").upper()
            path = m.group("path") or "/"
            routes.append({"verb": verb, "path": path})
        return routes

    def _extract_middleware(self, content: str) -> list[dict]:
        """Extract middleware 'routes' from app.use() calls."""
        middleware = []
        for m in _JS_MIDDLEWARE.finditer(content):
            path = m.group("path")
            handler = m.group("handler")
            if handler:
                middleware.append({
                    "verb": "USE",
                    "path": path or "/",
                    "handler": handler,
                })
        return middleware

    def _extract_mounts(self, content: str, from_file: str) -> list[dict]:
        """Extract router mount points (app.use('/prefix', router))."""
        mounts = []
        for m in _JS_MOUNT.finditer(content):
            mounts.append({
                "prefix": m.group("prefix"),
                "router": m.group("router"),
            })
        return mounts

    def _find_router_file(self, router_name: str, from_file: str) -> Optional[str]:
        """Find the file that exports a router/middleware."""
        if self._folder is None:
            return None

        # Try to find a file with the same name as the router
        from_dir = (self._folder / from_file).parent

        # Common patterns: routes/users.js, ./users, users/index.js
        candidates = [
            f"{router_name}.js",
            f"{router_name}.ts",
            f"{router_name}/index.js",
            f"{router_name}/index.ts",
            f"./{router_name}.js",
            f"./{router_name}.ts",
            f"./{router_name}/index.js",
            f"./{router_name}/index.ts",
        ]

        for candidate in candidates:
            candidate_path = from_dir / candidate
            if candidate_path.exists():
                return str(candidate_path.relative_to(self._folder)).replace("\\", "/")

        return None

    def get_file_context(self, file_path: str) -> Optional[FileContext]:
        """Look up FileContext for a file."""
        if file_path in self._file_contexts:
            return self._file_contexts[file_path]
        stem = Path(file_path).stem
        for key, ctx in self._file_contexts.items():
            if Path(key).stem == stem:
                return ctx
        return None

    def get_extra_imports(self) -> dict[str, list[dict]]:
        """Return router mount edges."""
        return self._extra_imports

    def get_metadata(self) -> dict:
        """Return route summary metadata."""
        return {"express_framework": self._framework or "express"}

    def stats(self) -> dict:
        """Return provider stats."""
        return {
            "framework": self._framework or "express",
            "route_files": len(self._file_contexts),
        }
