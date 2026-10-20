"""Decorator-based routes provider for Flask, FastAPI, Spring Boot, NestJS, and ASP.NET.

This provider detects routes declared via decorators/annotations in frameworks where
routes are explicitly marked with decorator syntax.
"""

from __future__ import annotations

import bisect
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ._route_utils import has_dependency, make_route_file_context
from .base import ContextProvider, FileContext, register_provider

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Framework configurations
# ---------------------------------------------------------------------------

@dataclass
class FrameworkConfig:
    """Configuration for a single framework's route detection."""
    name: str
    manifest_files: list[str]
    key_dependency: str
    file_globs: list[str]
    route_pattern: re.Pattern
    method_to_verb: dict[str, str] = field(default_factory=lambda: {
        "get": "GET", "post": "POST", "put": "PUT", "patch": "PATCH",
        "delete": "DELETE", "head": "HEAD", "options": "OPTIONS",
        "any": "ANY",
    })


# Flask: matches @app.route(...) and @bp.route(...)
_FLASK_ROUTE = re.compile(
    r"@(?:app|bp|blueprint|router|flask_app)\s*\.\s*"
    r"(?:route|get|post|put|delete|patch|head|options)\s*\(\s*"
    r"[\"\'](?P<path>[^\"\']+)[\"\']",
    re.IGNORECASE,
)

def _flask_route_methods_by_line(content: str) -> dict[int, list[str]] | None:
    """Verbs each `@x.route(...)` decorator serves, keyed by its `@` line (#1177).

    Read with Python's own parser, never by scanning text: a bracket counter
    took a `)` inside a string or a comment for the end of the call, and read a
    `methods=` inside a string as the argument (review of #1177). Flask serves
    GET only when `methods=` is absent. A list, tuple or set of string literals
    names the verbs; any other value (a variable, a call, `['GET'] + EXTRA`)
    names verbs this reader cannot see, so the route is `ANY` rather than a
    guessed GET. None when the file does not parse: the caller cannot read any
    `methods=` and says so.
    """
    import ast
    import warnings

    # A BOM is not Python; `read_text("utf-8")` keeps it (review round 2).
    source = content[1:] if content.startswith("﻿") else content
    try:
        # SyntaxWarnings about the USER's code (an invalid escape) are not ours to
        # print, and under `-W error` they would turn the verb into ANY: the
        # result must not depend on the host's warning filter.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            tree = ast.parse(source)
    except Exception:  # noqa: BLE001
        # SyntaxError, and RecursionError/MemoryError from a generated file:
        # one unreadable file must not take the provider down for every file.
        logger.debug("decorator routes: could not parse a Flask file for methods=", exc_info=True)
        return None
    out: dict[int, list[str]] = {}
    for node in ast.walk(tree):
        for dec in getattr(node, "decorator_list", ()):
            if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and dec.func.attr == "route"):
                continue
            kw = next((k for k in dec.keywords if k.arg == "methods"), None)
            if kw is None:
                verbs = ["GET"]
            elif isinstance(kw.value, (ast.List, ast.Tuple, ast.Set)) and kw.value.elts and all(
                isinstance(e, ast.Constant) and isinstance(e.value, str) for e in kw.value.elts
            ):
                verbs = list(dict.fromkeys(str(e.value).upper() for e in kw.value.elts))  # type: ignore[union-attr]
            else:
                verbs = ["ANY"]
            # `dec.lineno` is the line of the expression after `@`, which is the
            # line the caller's regex match starts on.
            out[dec.lineno] = verbs
    return out


# FastAPI: matches @app.get(...), @router.post(...), etc.
_FASTAPI_ROUTE = re.compile(
    r"@(?:app|router)\s*\.\s*"
    r"(?:get|post|put|patch|delete|head|options)\s*\(\s*"
    r"[\"\'](?P<path>[^\"\']+)[\"\']",
    re.IGNORECASE,
)

# Spring Boot: matches @GetMapping, @PostMapping, etc.
_SPRING_ROUTE = re.compile(
    r"@(?:Get|Post|Put|Delete|Patch|Request)Mapping"
    r"(?:\s*\(\s*(?:value\s*=\s*)?[\"\'](?P<path>[^\"\']*)[\"\'])?",
    re.IGNORECASE,
)

# NestJS: matches @Get('/path'), @Post('path'), etc.
# Also handles @Get() with no path (empty path)
_NESTJS_ROUTE = re.compile(
    r"@(?:Get|Post|Put|Patch|Delete|Head|Options|All)\s*\(\s*"
    r"(?:[\"\'](?P<path>[^\"\']*)[\"\'])?\s*(?:,|\))",
    re.IGNORECASE,
)

# ASP.NET: matches [HttpGet], [HttpGet("path")], [Route("path")]
_ASPNET_ROUTE = re.compile(
    r"\[Http(?P<method>Get|Post|Put|Patch|Delete|Head|Options)(?:\s*\(\s*\"(?P<path>[^\"]+)\"\s*\))?\]",
    re.IGNORECASE,
)
_ASPNET_ROUTE_CLASS = re.compile(
    r"\[Route\s*\(\s*\"(?P<path>[^\"]+)\"\s*\)\]",
    re.IGNORECASE,
)

_FRAMEWORK_CONFIGS = [
    # FastAPI (Python) - check BEFORE Flask since FastAPI depends on Starlette and uses same decorator syntax
    FrameworkConfig(
        name="fastapi",
        manifest_files=["requirements.txt", "pyproject.toml"],
        key_dependency="fastapi",
        file_globs=["**/*.py"],
        route_pattern=_FASTAPI_ROUTE,
    ),
    # Flask (Python)
    FrameworkConfig(
        name="flask",
        manifest_files=["requirements.txt", "pyproject.toml"],
        key_dependency="flask",
        file_globs=["**/*.py"],
        route_pattern=_FLASK_ROUTE,
    ),
    # Spring Boot (Java)
    FrameworkConfig(
        name="spring-boot",
        manifest_files=["build.gradle", "pom.xml"],
        key_dependency="spring-boot-starter-web",
        file_globs=["**/*.java"],
        route_pattern=_SPRING_ROUTE,
    ),
    # NestJS (TypeScript)
    FrameworkConfig(
        name="nestjs",
        manifest_files=["package.json"],
        key_dependency="@nestjs/core",
        file_globs=["**/*.ts"],
        route_pattern=_NESTJS_ROUTE,
    ),
    # ASP.NET (C#)
    FrameworkConfig(
        name="aspnet",
        manifest_files=["*.csproj"],
        key_dependency="Microsoft.AspNetCore",
        file_globs=["**/*.cs"],
        route_pattern=_ASPNET_ROUTE,
    ),
]


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

@register_provider
class DecoratorRoutesProvider(ContextProvider):
    """Context provider for decorator-based frameworks.

    Detects Flask, FastAPI, Spring Boot, NestJS, and ASP.NET projects via
    manifest files and extracts routes from decorator/annotation patterns.
    """

    def __init__(self) -> None:
        self._folder: Optional[Path] = None
        self._active_config: Optional[FrameworkConfig] = None
        self._file_contexts: dict[str, FileContext] = {}
        self._extra_imports: dict[str, list[dict]] = {}
        self._stats: dict = {}

    @property
    def name(self) -> str:
        return "decorator-routes"

    def detect(self, folder: Path) -> bool:
        """Detect if any decorator-based framework is present."""
        for config in _FRAMEWORK_CONFIGS:
            if self._check_framework(folder, config):
                self._active_config = config
                logger.info("Decorator routes: detected framework %s", config.name)
                return True
        return False

    def _check_framework(self, folder: Path, config: FrameworkConfig) -> bool:
        """Check if a specific framework is present in the folder."""
        # Special case: Spring Boot uses plugins, not direct dependencies
        if config.name == "spring-boot":
            return self._detect_spring_boot(folder)
        # Special case: ASP.NET uses SDK attribute in csproj
        if config.name == "aspnet":
            return self._detect_aspnet(folder)
        return has_dependency(folder, config.key_dependency, config.manifest_files)

    def _detect_spring_boot(self, folder: Path) -> bool:
        """Detect Spring Boot via build.gradle plugins or pom.xml."""
        build_gradle = folder / "build.gradle"
        if build_gradle.exists():
            try:
                content = build_gradle.read_text("utf-8", errors="replace")
                if "org.springframework.boot" in content:
                    return True
            except Exception:
                pass
        pom_xml = folder / "pom.xml"
        if pom_xml.exists():
            try:
                content = pom_xml.read_text("utf-8", errors="replace")
                if "spring-boot" in content.lower():
                    return True
            except Exception:
                pass
        return False

    def _detect_aspnet(self, folder: Path) -> bool:
        """Detect ASP.NET via .csproj with Web SDK."""
        for csproj_file in folder.glob("*.csproj"):
            try:
                content = csproj_file.read_text("utf-8", errors="replace")
                if 'Sdk="Microsoft.NET.Sdk.Web"' in content or "Microsoft.AspNetCore" in content:
                    return True
            except Exception:
                continue
        return False

    def load(self, folder: Path) -> None:
        """Scan source files for route patterns."""
        if self._active_config is None:
            return
        self._folder = folder

        config = self._active_config
        route_count = 0
        file_count = 0

        for pattern in config.file_globs:
            for file_path in folder.glob(pattern):
                # Skip common non-source directories
                rel_path = str(file_path.relative_to(folder)).replace("\\", "/")
                if any(
                    skip in rel_path
                    for skip in ("node_modules/", ".venv/", "venv/", "env/",
                                 "__pycache__/", ".git/", "vendor/", "dist/",
                                 "build/", "target/", "bin/", "obj/")
                ):
                    continue

                try:
                    content = file_path.read_text("utf-8", errors="replace")
                except Exception:
                    continue

                routes = self._extract_routes(content, config)
                if routes:
                    ctx = make_route_file_context(config.name, routes)
                    self._file_contexts[rel_path] = ctx
                    route_count += len(routes)
                    file_count += 1

                    # Extra imports for NestJS module→controller edges
                    if config.name == "nestjs":
                        self._extract_nestjs_module_edges(content, rel_path)

        self._stats = {
            "framework": config.name,
            "route_files": file_count,
            "total_routes": route_count,
        }
        logger.info(
            "Decorator routes: found %d routes in %d files (%s)",
            route_count, file_count, config.name,
        )

    def _extract_routes(
        self, content: str, config: FrameworkConfig
    ) -> list[dict]:
        """Extract route dicts from source content."""
        routes = []
        method_map = config.method_to_verb

        if config.name == "aspnet":
            # ASP.NET needs class-level route prefix extraction
            class_route = ""
            for m in _ASPNET_ROUTE_CLASS.finditer(content):
                class_route = m.group("path") or ""
                break
            for m in config.route_pattern.finditer(content):
                method = m.group("method").upper()
                path = m.group("path") or ""
                full_path = self._combine_aspnet_path(class_route, path)
                routes.append({"verb": method, "path": full_path})

        elif config.name == "nestjs":
            # For NestJS, extract class-level @Controller('prefix')
            class_prefix = ""
            ctrl_match = re.search(
                r"@Controller\s*\(\s*[\"'](?P<prefix>[^\"']*)[\"']\s*\)",
                content,
            )
            if ctrl_match:
                class_prefix = ctrl_match.group("prefix") or ""
            for m in config.route_pattern.finditer(content):
                # Extract decorator name: "@Get(':id')" -> "GET"
                decorator_text = m.group(0)
                verb = decorator_text.split("(")[0].lstrip("@").upper()
                path = m.group("path") or ""
                full_path = class_prefix + ("/" if class_prefix and path else "") + path
                routes.append({"verb": verb, "path": full_path})

        elif config.name == "spring-boot":
            # Spring Boot: need class-level @RequestMapping for prefix
            class_prefix = ""
            req_match = re.search(
                r"@RequestMapping\s*\(\s*(?:value\s*=\s*)?[\"'](?P<prefix>[^\"']*)[\"']\s*\)",
                content,
            )
            if req_match:
                class_prefix = req_match.group("prefix") or ""
            for m in config.route_pattern.finditer(content):
                verb = m.group(0).split("@")[1].split("Mapping")[0].upper()
                path = m.group("path") or ""
                full_path = class_prefix + ("/" if class_prefix and path else "") + path
                routes.append({"verb": verb, "path": full_path})

        else:
            # Flask/FastAPI
            route_methods: dict[int, list[str]] | None = None
            parsed = False
            newlines: list[int] = []
            for m in config.route_pattern.finditer(content):
                # Extract HTTP method from decorator name
                decorator = m.group(0)
                # Pattern: @app.route or @app.get
                parts = re.split(r"\s*\.\s*", decorator)
                method_part = parts[-1].split("(")[0].lower()
                verb = method_map.get(method_part, method_part.upper())
                path = m.group("path") or "/"
                if verb == "ROUTE":
                    # #1177: `methods=` names the verbs; Flask's GET default
                    # applies only when it is absent. Parsed once per file.
                    if not parsed:
                        route_methods = _flask_route_methods_by_line(content)
                        newlines = [i for i, c in enumerate(content) if c == "\n"]
                        parsed = True
                    line = bisect.bisect_left(newlines, m.start()) + 1
                    verbs = (route_methods or {}).get(line) or ["ANY"]
                    for v in verbs:
                        routes.append({"verb": v, "path": path})
                    continue
                routes.append({"verb": verb, "path": path})

        return routes

    def _combine_aspnet_path(self, prefix: str, path: str) -> str:
        """Combine class-level route prefix with method-level path."""
        if not prefix and not path:
            return "/"
        if not path:
            return prefix
        if not prefix:
            return path
        # If path starts with /, it's absolute
        if path.startswith("/"):
            return path
        return f"{prefix}/{path}"

    def _extract_nestjs_module_edges(
        self, content: str, file_path: str
    ) -> None:
        """Extract NestJS module→controller edges from @Module decorator."""
        module_match = re.search(
            r"@Module\s*\(\s*\{([^}]+)\}\s*\)",
            content,
            re.DOTALL,
        )
        if not module_match:
            return

        module_body = module_match.group(1)
        # Find controllers array
        ctrl_match = re.search(
            r"controllers\s*:\s*\[([^\]]+)\]",
            module_body,
        )
        if not ctrl_match:
            return

        ctrl_content = ctrl_match.group(1)
        # Extract controller class names
        for ctrl_ref in re.findall(r"(\w+)Controller", ctrl_content):
            # Create import edge from this module file to the controller file
            ctrl_file = self._find_nestjs_controller(ctrl_ref, file_path)
            if ctrl_file:
                self._extra_imports.setdefault(file_path, []).append({
                    "specifier": ctrl_file,
                    "names": [f"{ctrl_ref}Controller"],
                })

    def _find_nestjs_controller(
        self, controller_name: str, from_file: str
    ) -> Optional[str]:
        """Find the file path for a NestJS controller class."""
        if self._folder is None:
            return None
        # Search for the controller file in the same or nearby directories
        from_dir = (self._folder / from_file).parent
        for pattern in (f"**/{controller_name}.controller.ts", f"**/{controller_name}.controller.js"):
            for ctrl_file in self._folder.glob(pattern):
                if "node_modules" not in str(ctrl_file):
                    return str(ctrl_file.relative_to(self._folder)).replace("\\", "/")
        return None

    def get_file_context(self, file_path: str) -> Optional[FileContext]:
        """Look up FileContext for a file."""
        # Try exact match first
        if file_path in self._file_contexts:
            return self._file_contexts[file_path]
        # Try stem match
        stem = Path(file_path).stem
        for key, ctx in self._file_contexts.items():
            if Path(key).stem == stem:
                return ctx
        return None

    def get_extra_imports(self) -> dict[str, list[dict]]:
        """Return extra import edges (NestJS module→controller)."""
        return self._extra_imports

    def get_metadata(self) -> dict:
        """Return route summary metadata."""
        if not self._stats:
            return {}
        return {f"{self.name}_routes": self._stats}

    def stats(self) -> dict:
        """Return provider stats."""
        return self._stats
