"""Nuxt.js context provider — detects Nuxt projects and enriches symbols with framework metadata.

When a Nuxt project is detected (via nuxt.config.ts/js), this provider:
1. Parses pages/ for file-based routing → enriches page components with route metadata
2. Parses server/api/ for server API routes → enriches handlers with endpoint metadata
3. Scans composables/ and utils/ for auto-import candidates → injects synthetic import edges
4. Exposes route metadata via get_metadata() for search_columns
"""

import logging
import re
from pathlib import Path
from typing import Optional

from ...word_rule import word_left
from .base import ContextProvider, FileContext, register_provider

logger = logging.getLogger(__name__)


def _nuxt_route_from_path(file_path: str, pages_dir: str = "pages") -> str:
    """Convert a Nuxt pages/ file path to a route string.

    Examples:
        'pages/index.vue'          → '/'
        'pages/users/index.vue'    → '/users'
        'pages/users/[id].vue'     → '/users/:id'
        'pages/posts/[...slug].vue' → '/posts/*'
    """
    # Strip pages dir prefix and extension
    rel = file_path
    if rel.startswith(pages_dir + "/"):
        rel = rel[len(pages_dir) + 1:]
    rel = re.sub(r"\.(vue|tsx|jsx)$", "", rel)

    # Convert path segments
    segments = []
    for part in rel.split("/"):
        if part == "index":
            continue
        if part.startswith("[..."):
            segments.append("*")
        elif part.startswith("[") and part.endswith("]"):
            param = part[1:-1]
            segments.append(f":{param}")
        else:
            segments.append(part)

    return "/" + "/".join(segments) if segments else "/"


def _nuxt_api_route_from_path(file_path: str, api_dir: str = "server/api") -> Optional[dict]:
    """Convert a Nuxt server/api/ file path to an endpoint dict.

    Examples:
        'server/api/users.get.ts'      → {'method': 'GET', 'endpoint': '/api/users'}
        'server/api/users.post.ts'     → {'method': 'POST', 'endpoint': '/api/users'}
        'server/api/users/[id].get.ts' → {'method': 'GET', 'endpoint': '/api/users/:id'}
        'server/api/health.ts'         → {'method': 'ALL', 'endpoint': '/api/health'}
    """
    rel = file_path
    if rel.startswith(api_dir + "/"):
        rel = rel[len(api_dir) + 1:]
    rel = re.sub(r"\.(ts|js|mjs)$", "", rel)

    # Extract HTTP method from filename suffix (e.g., users.get → GET)
    method = "ALL"
    for m in ("get", "post", "put", "patch", "delete", "head", "options"):
        if rel.endswith(f".{m}"):
            method = m.upper()
            rel = rel[: -(len(m) + 1)]
            break

    # Convert segments
    segments = []
    for part in rel.split("/"):
        if part == "index":
            continue
        if part.startswith("[..."):
            segments.append("*")
        elif part.startswith("[") and part.endswith("]"):
            segments.append(f":{part[1:-1]}")
        else:
            segments.append(part)

    endpoint = "/api/" + "/".join(segments) if segments else "/api"
    return {"method": method, "endpoint": endpoint}


@register_provider
class NuxtContextProvider(ContextProvider):
    """Context provider for Nuxt.js projects.

    Detects nuxt.config.ts/js, then parses:
    - pages/     → file-based routing with route metadata
    - server/api/ → server API route handlers
    - composables/, utils/ → auto-import synthetic edges
    """

    def __init__(self) -> None:
        self._folder: Optional[Path] = None
        self._file_contexts: dict[str, FileContext] = {}
        self._extra_imports: dict[str, list[dict]] = {}
        self._route_metadata: dict[str, dict] = {}
        self._api_metadata: dict[str, dict] = {}
        self._auto_import_symbols: dict[str, str] = {}  # name → file path

    @property
    def name(self) -> str:
        return "nuxt"

    def detect(self, folder_path: Path) -> bool:
        for config_name in ("nuxt.config.ts", "nuxt.config.js", "nuxt.config.mjs"):
            if (folder_path / config_name).exists():
                self._folder = folder_path
                return True
        return False

    def _resolve_src_dir(self, folder_path: Path) -> str:
        """Return the srcDir prefix, relative and slash-normalised ("" = root).

        Nuxt 4 changed the DEFAULT srcDir to `app/`, so a stock Nuxt 4 project
        keeps pages at `app/pages/` while Nuxt 3 keeps them at `pages/`. Both
        are defaults of a current major version, and `srcDir` is configurable on
        top of that, so neither a fixed path nor a two-way probe is sufficient.

        Config wins when it says something, because it is the actual answer.
        Probing is the fallback and is an APPROXIMATION: it infers the layout
        from which directories exist. `server/` is deliberately not consulted,
        because it stays at the project root in the Nuxt 4 default.
        """
        for config_name in ("nuxt.config.ts", "nuxt.config.js", "nuxt.config.mjs"):
            cfg = folder_path / config_name
            if not cfg.exists():
                continue
            try:
                content = cfg.read_text("utf-8", errors="replace")
            except OSError:
                continue
            m = re.search(r"""\bsrcDir\s*:\s*['"]([^'"]+)['"]""", content)
            if m:
                return m.group(1).strip().strip("/").removeprefix("./")

        # Fallback probe. Require a Nuxt-shaped child rather than merely an
        # `app/` directory: plenty of projects have an unrelated `app/`, and
        # guessing wrong here relocates the whole parse.
        app = folder_path / "app"
        if app.is_dir() and any(
            (app / child).exists()
            for child in ("pages", "app.vue", "components", "composables", "layouts")
        ):
            return "app"
        return ""

    def load(self, folder_path: Path) -> None:
        if self._folder is None:
            self._folder = folder_path

        src_dir = self._resolve_src_dir(folder_path)
        self._parse_pages(folder_path, src_dir)
        self._parse_server_api(folder_path, src_dir)
        self._build_auto_import_map(folder_path, src_dir)
        self._build_auto_import_edges(folder_path)

    def _parse_pages(self, folder_path: Path, src_dir: str = "") -> None:
        """Parse the pages/ directory (under srcDir) for file-based routing."""
        prefix = f"{src_dir}/pages" if src_dir else "pages"
        pages_dir = folder_path / prefix
        if not pages_dir.is_dir():
            return

        for vue_file in sorted(pages_dir.rglob("*.vue")):
            rel_path = str(vue_file.relative_to(folder_path)).replace("\\", "/")
            route = _nuxt_route_from_path(rel_path, prefix)

            # Extract dynamic params
            params = re.findall(r":(\w+)", route)
            is_dynamic = bool(params) or "*" in route

            props: dict[str, str] = {"route": route}
            if params:
                props["params"] = ", ".join(params)
            desc = f"Nuxt page component. Route: {route}"
            if is_dynamic:
                desc += " (dynamic)"

            stem = vue_file.stem.replace(".vue", "")
            self._file_contexts[rel_path] = FileContext(
                description=desc,
                tags=["nuxt-page", "route"],
                properties=props,
            )
            self._route_metadata[route] = {"page": rel_path, "method": "GET"}

        logger.info("Nuxt: parsed %d page routes", len(self._route_metadata))

    def _parse_server_api(self, folder_path: Path, src_dir: str = "") -> None:
        """Parse server/api/ for API route handlers.

        Root-first, and that is not an oversight: in the Nuxt 4 default layout
        `server/` stays at the project ROOT while everything else moves under
        `app/`. The srcDir-relative probe is a strictly additive fallback for
        layouts that do nest it (Nuxt 3 with a custom srcDir), tried only when
        the root directory is absent, so it cannot change the Nuxt 4 result.
        """
        api_dir = folder_path / "server" / "api"
        if not api_dir.is_dir() and src_dir:
            api_dir = folder_path / src_dir / "server" / "api"
        if not api_dir.is_dir():
            return

        for api_file in sorted(api_dir.rglob("*")):
            if api_file.suffix not in (".ts", ".js", ".mjs"):
                continue
            rel_path = str(api_file.relative_to(folder_path)).replace("\\", "/")
            route_info = _nuxt_api_route_from_path(rel_path)
            if not route_info:
                continue

            key = f"{route_info['method']} {route_info['endpoint']}"
            self._file_contexts[rel_path] = FileContext(
                description=f"Nuxt server route: {key}",
                tags=["nuxt-api", "endpoint"],
                properties={"method": route_info["method"], "endpoint": route_info["endpoint"]},
            )
            self._api_metadata[key] = {"handler": rel_path}

        logger.info("Nuxt: parsed %d server API routes", len(self._api_metadata))

    def _build_auto_import_map(self, folder_path: Path, src_dir: str = "") -> None:
        """Scan composables/ and utils/ (under srcDir) for exported names.

        Both directories move under `app/` in the Nuxt 4 default layout. When
        this map comes back empty `_build_auto_import_edges` returns early, so
        missing it costs every synthetic edge that makes Nuxt's implicit imports
        visible, not just the symbol names.
        """
        for auto_dir in ("composables", "utils"):
            dir_path = folder_path / src_dir / auto_dir if src_dir else folder_path / auto_dir
            if not dir_path.is_dir():
                continue
            for src_file in sorted(dir_path.rglob("*")):
                if src_file.suffix not in (".ts", ".js", ".mjs", ".vue"):
                    continue
                rel_path = str(src_file.relative_to(folder_path)).replace("\\", "/")
                # Use filename stem as the auto-import name (Nuxt convention)
                name = src_file.stem
                # For useXxx.ts → useXxx is auto-imported
                self._auto_import_symbols[name] = rel_path

        logger.info("Nuxt: found %d auto-import candidates", len(self._auto_import_symbols))

    def _build_auto_import_edges(self, folder_path: Path) -> None:
        """Scan .vue files for usage of auto-imported symbols and create synthetic edges."""
        if not self._auto_import_symbols:
            return

        # Build regex from auto-import names
        names = sorted(self._auto_import_symbols.keys(), key=len, reverse=True)
        pattern = re.compile("(" + "|".join(word_left(n) + re.escape(n) for n in names) + r")\s*\(")

        edge_count = 0
        for vue_file in sorted(folder_path.rglob("*.vue")):
            rel = str(vue_file.relative_to(folder_path))
            if "node_modules" in rel or ".nuxt" in rel:
                continue
            try:
                content = vue_file.read_text("utf-8", errors="replace")
            except Exception:
                continue

            rel_path = rel.replace("\\", "/")
            seen: dict[str, set[str]] = {}
            for m in pattern.finditer(content):
                name = m.group(1)
                target = self._auto_import_symbols[name]
                if target != rel_path:
                    seen.setdefault(target, set()).add(name)

            if seen:
                imports = [
                    {"specifier": t, "names": sorted(seen[t])} for t in sorted(seen)
                ]
                self._extra_imports.setdefault(rel_path, []).extend(imports)
                edge_count += 1

        if edge_count:
            logger.info("Nuxt: injected auto-import edges for %d files", edge_count)

    def get_file_context(self, file_path: str) -> Optional[FileContext]:
        return self._file_contexts.get(file_path)

    def get_extra_imports(self) -> dict[str, list[dict]]:
        return self._extra_imports

    def get_metadata(self) -> dict:
        meta: dict = {}
        if self._route_metadata:
            meta["nuxt_routes"] = self._route_metadata
        if self._api_metadata:
            meta["nuxt_api"] = self._api_metadata
        return meta

    def stats(self) -> dict:
        return {
            "page_routes": len(self._route_metadata),
            "api_routes": len(self._api_metadata),
            "auto_import_symbols": len(self._auto_import_symbols),
            "files_with_auto_imports": len(self._extra_imports),
        }
