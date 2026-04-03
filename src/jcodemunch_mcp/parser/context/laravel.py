"""Laravel context provider — detects Laravel projects and enriches symbols with framework metadata.

When a Laravel project is detected (via artisan + composer.json containing laravel/framework),
this provider:
1. Parses routes/*.php for HTTP method, URI, controller, and route name
2. Parses app/Models/*.php for Eloquent relationships, fillable, casts, and scopes
3. Parses database/migrations/*.php for table column definitions (exposed via search_columns)
4. Parses app/Providers/EventServiceProvider.php for event→listener mappings
5. Improves Blade parsing: <x-component> tags and dot-notation view resolution
"""

import json
import logging
import re
from pathlib import Path
from typing import Optional

from .base import ContextProvider, FileContext, register_provider

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Route definitions: Route::get('/uri', [Controller::class, 'method'])
# Pattern 1: [ClassName::class, 'method']  Pattern 2: 'ClassName@method'
_ROUTE_ARRAY = re.compile(
    r"""Route\s*::\s*(?P<verb>get|post|put|patch|delete|any|resource|apiResource)\s*"""
    r"""\(\s*['"](?P<uri>[^'"]+)['"]\s*,\s*"""
    r"""\[\s*(?P<class>[\w\\]+)::class\s*,\s*['"](?P<action>\w+)['"]\s*\]""",
    re.MULTILINE,
)
_ROUTE_OLDSTYLE = re.compile(
    r"""Route\s*::\s*(?P<verb>get|post|put|patch|delete|any|resource|apiResource)\s*"""
    r"""\(\s*['"](?P<uri>[^'"]+)['"]\s*,\s*['"](?P<oldstyle>[\w\\@]+)['"]""",
    re.MULTILINE,
)

# Route name chaining: ->name('route.name')
_ROUTE_NAME = re.compile(r"""->name\s*\(\s*['"](?P<name>[^'"]+)['"]\s*\)""")

# Eloquent relationship methods
_ELOQUENT_RELATION = re.compile(
    r"""\$this\s*->\s*(?P<type>hasMany|hasOne|belongsTo|belongsToMany|hasManyThrough|"""
    r"""hasOneThrough|morphMany|morphOne|morphTo|morphToMany|morphedByMany)\s*"""
    r"""\(\s*(?P<model>[\w\\]+)::class""",
    re.MULTILINE,
)

# $fillable, $guarded, $casts, $table
_PHP_PROPERTY_ARRAY = re.compile(
    r"""(?:protected|public|private)\s+(?:static\s+)?\$(?P<prop>fillable|guarded|casts|table|with|hidden)\s*=\s*"""
    r"""(?:\[(?P<arr>[^\]]*)\]|['"'](?P<str>[^'"]+)['"'])\s*;""",
    re.MULTILINE | re.DOTALL,
)

# Local scope methods: public function scopeXxx($query)
_SCOPE_METHOD = re.compile(r"""function\s+scope(?P<name>[A-Z]\w*)\s*\(""", re.MULTILINE)

# Migration Schema::create / Schema::table
_SCHEMA_CREATE = re.compile(
    r"""Schema\s*::\s*(?:create|table)\s*\(\s*['"](?P<table>[^'"]+)['"]""",
    re.MULTILINE,
)

# Column definitions inside Blueprint callback: $table->type('name')
_COLUMN_DEF = re.compile(
    r"""\$table\s*->\s*(?P<type>\w+)\s*\(\s*['"](?P<name>[^'"]+)['"]""",
    re.MULTILINE,
)

# Event::listen or $listen array entries
_EVENT_LISTEN_ARRAY = re.compile(
    r"""['"'](?P<event>[\w\\]+)['"']\s*=>\s*\[([^\]]+)\]""",
    re.MULTILINE | re.DOTALL,
)
_LISTENER_ENTRY = re.compile(r"""['"'](?P<listener>[\w\\]+::class|[\w\\]+)['"']""")

# Blade: <x-component> tag syntax
_BLADE_X_COMPONENT = re.compile(r"""<x-(?P<name>[\w\-.]+)""", re.MULTILINE)

# Blade dot-notation: @extends('layouts.app'), @include('partials.header'), view('users.index')
_BLADE_DOTREF = re.compile(
    r"""(?:@extends|@include|@component|view)\s*\(\s*['"](?P<ref>[^'"]+)['"]""",
    re.MULTILINE,
)

# @includeWhen($cond, 'view'), @includeUnless($cond, 'view')
_BLADE_INCLUDE_CONDITIONAL = re.compile(
    r"""@include(?:When|Unless)\s*\([^,]+,\s*['"](?P<ref>[^'"]+)['"]""",
    re.MULTILINE,
)

# @includeFirst(['primary.view', 'fallback.view']) — captures only the first (highest-priority) candidate
_BLADE_INCLUDE_FIRST = re.compile(
    r"""@includeFirst\s*\(\s*\[\s*['"](?P<ref>[^'"]+)['"]""",
    re.MULTILINE,
)

# Inertia.js render calls: Inertia::render('Users/Index'), inertia('Dashboard')
_INERTIA_RENDER = re.compile(
    r"""(?:Inertia\s*::\s*render|inertia)\s*\(\s*['"](?P<page>[^'"]+)['"]""",
    re.MULTILINE,
)

# Frontend API calls: fetch('/api/...'), axios.get('/api/...'), useFetch('/api/...'), $fetch('/api/...')
_API_CALL = re.compile(
    r"""(?:fetch|axios\s*\.\s*\w+|\$fetch|useFetch|useAsyncData)\s*\(\s*"""
    r"""['"`](?P<url>/api/[^'"`$]+)['"`]""",
    re.MULTILINE,
)

# Static facade calls: Cache::get(), DB::table(), etc.
# Built dynamically after _LARAVEL_FACADES is defined (see below).

# Laravel built-in facade → underlying service class mapping
_LARAVEL_FACADES: dict[str, str] = {
    "App": "Illuminate\\Foundation\\Application",
    "Artisan": "Illuminate\\Contracts\\Console\\Kernel",
    "Auth": "Illuminate\\Auth\\AuthManager",
    "Blade": "Illuminate\\View\\Compilers\\BladeCompiler",
    "Broadcast": "Illuminate\\Contracts\\Broadcasting\\Factory",
    "Bus": "Illuminate\\Contracts\\Bus\\Dispatcher",
    "Cache": "Illuminate\\Cache\\CacheManager",
    "Config": "Illuminate\\Config\\Repository",
    "Context": "Illuminate\\Log\\Context\\Repository",
    "Cookie": "Illuminate\\Cookie\\CookieJar",
    "Crypt": "Illuminate\\Encryption\\Encrypter",
    "Date": "Illuminate\\Support\\DateFactory",
    "DB": "Illuminate\\Database\\DatabaseManager",
    "Event": "Illuminate\\Events\\Dispatcher",
    "File": "Illuminate\\Filesystem\\Filesystem",
    "Gate": "Illuminate\\Contracts\\Auth\\Access\\Gate",
    "Hash": "Illuminate\\Hashing\\HashManager",
    "Http": "Illuminate\\Http\\Client\\Factory",
    "Lang": "Illuminate\\Translation\\Translator",
    "Log": "Illuminate\\Log\\LogManager",
    "Mail": "Illuminate\\Mail\\Mailer",
    "Notification": "Illuminate\\Notifications\\ChannelManager",
    "Password": "Illuminate\\Auth\\Passwords\\PasswordBrokerManager",
    "Pipeline": "Illuminate\\Pipeline\\Pipeline",
    "Process": "Illuminate\\Process\\Factory",
    "Queue": "Illuminate\\Queue\\QueueManager",
    "RateLimiter": "Illuminate\\Cache\\RateLimiter",
    "Redirect": "Illuminate\\Routing\\Redirector",
    "Redis": "Illuminate\\Redis\\RedisManager",
    "Request": "Illuminate\\Http\\Request",
    "Response": "Illuminate\\Routing\\ResponseFactory",
    "Route": "Illuminate\\Routing\\Router",
    "Schedule": "Illuminate\\Console\\Scheduling\\Schedule",
    "Schema": "Illuminate\\Database\\Schema\\Builder",
    "Session": "Illuminate\\Session\\SessionManager",
    "Storage": "Illuminate\\Filesystem\\FilesystemManager",
    "URL": "Illuminate\\Routing\\UrlGenerator",
    "Validator": "Illuminate\\Validation\\Factory",
    "View": "Illuminate\\View\\Factory",
    "Vite": "Illuminate\\Foundation\\Vite",
}

# Build regex from known facade names: matches Cache::get(), DB::table(), etc.
# Excludes Foo::class references (class constant, not a call).
_FACADE_NAMES_PATTERN = "|".join(re.escape(f) for f in sorted(_LARAVEL_FACADES, key=len, reverse=True))
_FACADE_STATIC_CALL = re.compile(
    rf"""(?<![>\w\\])(?P<facade>{_FACADE_NAMES_PATTERN})\s*::\s*(?!class\b)\w+\s*\(""",
    re.MULTILINE,
)

# Non-column Blueprint calls to skip (modifiers, not column definitions)
_COLUMN_SKIP_TYPES = frozenset({
    "index", "unique", "primary", "foreign", "foreignId", "dropColumn",
    "dropIndex", "dropPrimary", "dropForeign", "renameColumn", "timestamps",
    "softDeletes", "rememberToken", "engine", "charset", "collation",
    "comment", "after", "constrained", "cascadeOnDelete", "nullOnDelete",
    "restrictOnDelete", "references", "on",
})


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _read_composer_require(folder_path: Path) -> str:
    """Return the raw content of composer.json require section for framework detection."""
    try:
        data = json.loads((folder_path / "composer.json").read_text("utf-8", errors="replace"))
        requires = list(data.get("require", {}).keys()) + list(data.get("require-dev", {}).keys())
        return " ".join(requires)
    except Exception:
        return ""


def _read_php(path: Path) -> str:
    try:
        return path.read_text("utf-8", errors="replace")
    except Exception:
        return ""


def _parse_routes(routes_dir: Path) -> list[dict]:
    """Parse all route files and return a list of route dicts."""
    routes: list[dict] = []
    if not routes_dir.is_dir():
        return routes

    for route_file in sorted(routes_dir.glob("*.php")):
        content = _read_php(route_file)

        for m in _ROUTE_ARRAY.finditer(content):
            remainder = content[m.end():m.end() + 200]
            name_m = _ROUTE_NAME.search(remainder)
            routes.append({
                "verb": m.group("verb").upper(),
                "uri": m.group("uri"),
                "controller": m.group("class").rsplit("\\", 1)[-1],
                "controller_fqn": m.group("class"),
                "action": m.group("action"),
                "name": name_m.group("name") if name_m else "",
                "file": route_file.name,
            })

        for m in _ROUTE_OLDSTYLE.finditer(content):
            old_style = m.group("oldstyle")
            if "@" in old_style:
                parts = old_style.split("@", 1)
                controller, action = parts[0], parts[1]
            else:
                controller, action = old_style, ""
            remainder = content[m.end():m.end() + 200]
            name_m = _ROUTE_NAME.search(remainder)
            routes.append({
                "verb": m.group("verb").upper(),
                "uri": m.group("uri"),
                "controller": controller.rsplit("\\", 1)[-1],
                "controller_fqn": controller,
                "action": action,
                "name": name_m.group("name") if name_m else "",
                "file": route_file.name,
            })

    return routes


def _parse_model(content: str) -> dict:
    """Extract Eloquent model metadata from PHP source."""
    relationships: list[str] = []
    related_models: list[str] = []
    for m in _ELOQUENT_RELATION.finditer(content):
        raw_model = m.group("model")
        model_name = raw_model.rsplit("\\", 1)[-1]
        relationships.append(f"{m.group('type')}({model_name})")
        related_models.append(raw_model)

    scopes: list[str] = [m.group("name") for m in _SCOPE_METHOD.finditer(content)]

    props: dict[str, str] = {}
    for m in _PHP_PROPERTY_ARRAY.finditer(content):
        prop = m.group("prop")
        arr_content = m.group("arr") or ""
        str_val = m.group("str") or ""
        if str_val:
            props[prop] = str_val
        elif arr_content:
            items = re.findall(r"""['"]([\w]+)['"]""", arr_content)
            props[prop] = ", ".join(items[:10])

    return {
        "relationships": relationships,
        "related_models": related_models,
        "scopes": scopes,
        "fillable": props.get("fillable", ""),
        "table": props.get("table", ""),
        "casts": props.get("casts", ""),
    }


def _parse_migration(content: str) -> Optional[tuple[str, dict[str, str]]]:
    """Extract table name and column definitions from a migration file.

    Returns (table_name, {col_name: col_description}) or None.
    """
    m = _SCHEMA_CREATE.search(content)
    if not m:
        return None
    table_name = m.group("table")

    columns: dict[str, str] = {}
    for cm in _COLUMN_DEF.finditer(content):
        col_type = cm.group("type")
        col_name = cm.group("name")
        if col_type in _COLUMN_SKIP_TYPES:
            continue
        # Check for common modifiers in the short trailing text
        trailing = content[cm.end():cm.end() + 120]
        mods: list[str] = []
        if "->nullable()" in trailing:
            mods.append("nullable")
        if "->unique()" in trailing:
            mods.append("unique")
        if "->unsigned()" in trailing:
            mods.append("unsigned")
        desc = col_type
        if mods:
            desc += ", " + ", ".join(mods)
        columns[col_name] = desc

    return table_name, columns


def _parse_events(content: str) -> list[str]:
    """Extract event→listener mappings from EventServiceProvider."""
    mappings: list[str] = []
    for m in _EVENT_LISTEN_ARRAY.finditer(content):
        event = m.group("event").rsplit("\\", 1)[-1]
        listeners_block = m.group(2)
        listeners = [lm.group("listener").rsplit("\\", 1)[-1].replace("::class", "")
                     for lm in _LISTENER_ENTRY.finditer(listeners_block)]
        if listeners:
            mappings.append(f"{event} → {', '.join(listeners)}")
    return mappings


# ---------------------------------------------------------------------------
# Context Provider
# ---------------------------------------------------------------------------

@register_provider
class LaravelContextProvider(ContextProvider):
    """Context provider for Laravel projects.

    Detects artisan + laravel/framework in composer.json, then parses:
    - routes/*.php  → HTTP method, URI, controller, route name
    - app/Models/*  → Eloquent relationships, fillable, scopes
    - database/migrations/* → table column definitions (for search_columns)
    - app/Providers/EventServiceProvider.php → event→listener mappings
    """

    def __init__(self) -> None:
        self._folder: Optional[Path] = None
        # file stem → FileContext
        self._file_contexts: dict[str, FileContext] = {}
        # route lookup: controller class name → list of route summaries
        self._controller_routes: dict[str, list[str]] = {}
        # migration columns: table_name → {col_name: col_description}
        self._table_columns: dict[str, dict[str, str]] = {}
        # blade view resolution: relative folder path
        self._views_path: Optional[Path] = None
        # Extra import edges injected into the dependency graph
        self._extra_imports: dict[str, list[dict]] = {}
        # API route map: normalized URI → controller file (for fetch/axios matching)
        self._api_route_map: dict[str, str] = {}

    @property
    def name(self) -> str:
        return "laravel"

    def detect(self, folder_path: Path) -> bool:
        if not (folder_path / "artisan").exists():
            return False
        requires = _read_composer_require(folder_path)
        if "laravel/framework" not in requires:
            return False
        self._folder = folder_path
        return True

    def load(self, folder_path: Path) -> None:
        if self._folder is None:
            self._folder = folder_path

        routes = _parse_routes(folder_path / "routes")
        logger.info("Laravel: parsed %d routes", len(routes))

        # Build controller → routes lookup
        for route in routes:
            ctrl = route["controller"]
            if ctrl:
                summary = f"{route['verb']} {route['uri']}"
                if route["name"]:
                    summary += f" ({route['name']})"
                self._controller_routes.setdefault(ctrl, []).append(summary)

        # Build API route map: URI → controller file path (for fetch/axios matching)
        self._build_api_route_map(routes, folder_path)

        # Parse Eloquent models
        models_dir = folder_path / "app" / "Models"
        model_count = 0
        if models_dir.is_dir():
            for php_file in sorted(models_dir.glob("*.php")):
                content = _read_php(php_file)
                meta = _parse_model(content)
                stem = php_file.stem

                # Determine table name: explicit $table or pluralized stem (simple heuristic)
                table_name = meta["table"] or _guess_table(stem)

                props: dict[str, str] = {"table": table_name}
                if meta["relationships"]:
                    props["relationships"] = ", ".join(meta["relationships"][:8])
                if meta["scopes"]:
                    props["scopes"] = ", ".join(meta["scopes"][:6])
                if meta["fillable"]:
                    props["fillable"] = meta["fillable"]

                route_strs = self._controller_routes.get(stem + "Controller", [])
                if route_strs:
                    props["routes"] = "; ".join(route_strs[:4])

                self._file_contexts[stem] = FileContext(
                    description=f"Eloquent model for `{table_name}` table",
                    tags=["eloquent-model", f"{table_name}-table"],
                    properties=props,
                )
                model_count += 1

                # Eloquent relationship → import edges
                if meta["related_models"]:
                    rel_path = str(php_file.relative_to(folder_path)).replace("\\", "/")
                    rel_imports: list[dict] = []
                    seen_models: set[str] = set()
                    for raw_model in meta["related_models"]:
                        if raw_model not in seen_models:
                            short = raw_model.rsplit("\\", 1)[-1]
                            rel_imports.append({"specifier": raw_model, "names": [short]})
                            seen_models.add(raw_model)
                    if rel_imports:
                        self._extra_imports.setdefault(rel_path, []).extend(rel_imports)

        logger.info("Laravel: parsed %d models", model_count)

        # Parse migrations for column metadata
        migrations_dir = folder_path / "database" / "migrations"
        migration_count = 0
        if migrations_dir.is_dir():
            for php_file in sorted(migrations_dir.glob("*.php")):
                content = _read_php(php_file)
                result = _parse_migration(content)
                if result:
                    table_name, columns = result
                    if columns:
                        self._table_columns[table_name] = columns
                        migration_count += 1

        logger.info("Laravel: parsed %d migration tables", migration_count)

        # Parse EventServiceProvider
        esp_path = folder_path / "app" / "Providers" / "EventServiceProvider.php"
        if esp_path.exists():
            content = _read_php(esp_path)
            events = _parse_events(content)
            if events:
                self._file_contexts["EventServiceProvider"] = FileContext(
                    description="Laravel event→listener registry",
                    tags=["event-provider", "events"],
                    properties={"event_mappings": "; ".join(events[:10])},
                )
                logger.info("Laravel: parsed %d event mappings", len(events))

        # Parse controllers: enrich with route info
        controllers_dir = folder_path / "app" / "Http" / "Controllers"
        if controllers_dir.is_dir():
            for php_file in sorted(controllers_dir.rglob("*.php")):
                stem = php_file.stem
                route_strs = self._controller_routes.get(stem, [])
                if route_strs:
                    self._file_contexts[stem] = FileContext(
                        description=f"Laravel controller: handles {', '.join(route_strs[:3])}",
                        tags=["controller", "http"],
                        properties={"routes": "; ".join(route_strs[:6])},
                    )

        # Store views path for Blade resolution
        self._views_path = folder_path / "resources" / "views"

        # --- Extra imports: Route files → Controllers ---
        self._build_route_imports(routes, folder_path)

        # --- Extra imports: Blade templates → referenced views ---
        self._build_blade_imports(folder_path)

        # --- Extra imports: Facade static calls → underlying classes ---
        self._build_facade_imports(folder_path)

        # --- Extra imports: Inertia.js renders → Vue/React page components ---
        self._build_inertia_imports(folder_path)

        # --- Extra imports: fetch/axios API calls → controller files ---
        self._build_api_call_imports(folder_path)

    def _build_api_route_map(self, routes: list[dict], folder_path: Path) -> None:
        """Build a URI → controller file path mapping for API call matching."""
        controllers_dir = folder_path / "app" / "Http" / "Controllers"
        for route in routes:
            uri = route.get("uri", "")
            ctrl = route.get("controller", "")
            if not uri or not ctrl:
                continue
            # Normalize: strip leading slash, replace {param} with *
            normalized = "/" + uri.lstrip("/")
            normalized = re.sub(r"\{[^}]+\}", "*", normalized)
            # Try to find the controller file
            ctrl_file = _find_controller_file(ctrl, controllers_dir, folder_path)
            if ctrl_file:
                self._api_route_map[normalized] = ctrl_file

    def _build_route_imports(self, routes: list[dict], folder_path: Path) -> None:
        """Create import edges from route files to their controller files."""
        for route in routes:
            fqn = route.get("controller_fqn", "")
            route_file = route.get("file", "")
            if not fqn or not route_file:
                continue

            route_rel = f"routes/{route_file}"
            imp = {"specifier": fqn, "names": [route["controller"]]}

            if route_rel in self._extra_imports:
                # Avoid duplicate specifiers
                existing = {i["specifier"] for i in self._extra_imports[route_rel]}
                if fqn not in existing:
                    self._extra_imports[route_rel].append(imp)
            else:
                self._extra_imports[route_rel] = [imp]

    def _build_blade_imports(self, folder_path: Path) -> None:
        """Parse Blade templates for @extends, @include, @component, <x-*> references."""
        views_dir = folder_path / "resources" / "views"
        if not views_dir.is_dir():
            return

        blade_count = 0
        for blade_file in sorted(views_dir.rglob("*.blade.php")):
            content = _read_php(blade_file)
            if not content:
                continue

            rel_path = str(blade_file.relative_to(folder_path)).replace("\\", "/")
            imports: list[dict] = []
            seen: set[str] = set()

            # @extends, @include, @component, view()
            for m in _BLADE_DOTREF.finditer(content):
                ref = m.group("ref")
                resolved = _blade_dot_to_path(ref)
                if resolved and resolved not in seen:
                    imports.append({"specifier": resolved, "names": []})
                    seen.add(resolved)

            # @includeWhen($cond, 'view'), @includeUnless($cond, 'view')
            for m in _BLADE_INCLUDE_CONDITIONAL.finditer(content):
                ref = m.group("ref")
                resolved = _blade_dot_to_path(ref)
                if resolved and resolved not in seen:
                    imports.append({"specifier": resolved, "names": []})
                    seen.add(resolved)

            # @includeFirst(['primary.view', 'fallback.view']) — first candidate only
            for m in _BLADE_INCLUDE_FIRST.finditer(content):
                ref = m.group("ref")
                resolved = _blade_dot_to_path(ref)
                if resolved and resolved not in seen:
                    imports.append({"specifier": resolved, "names": []})
                    seen.add(resolved)

            # <x-component> → resources/views/components/component.blade.php
            for m in _BLADE_X_COMPONENT.finditer(content):
                name = m.group("name")
                resolved = _blade_component_to_path(name)
                if resolved and resolved not in seen:
                    imports.append({"specifier": resolved, "names": []})
                    seen.add(resolved)

            if imports:
                self._extra_imports[rel_path] = imports
                blade_count += 1

        if blade_count:
            logger.info("Laravel: extracted Blade imports from %d templates", blade_count)

    def _build_facade_imports(self, folder_path: Path) -> None:
        """Scan PHP files for facade static calls and create import edges to underlying classes."""
        facade_count = 0
        app_dir = folder_path / "app"
        if not app_dir.is_dir():
            return

        for php_file in sorted(app_dir.rglob("*.php")):
            content = _read_php(php_file)
            if not content:
                continue

            facades_used: dict[str, str] = {}
            for m in _FACADE_STATIC_CALL.finditer(content):
                facade_name = m.group("facade")
                if facade_name in _LARAVEL_FACADES and facade_name not in facades_used:
                    facades_used[facade_name] = _LARAVEL_FACADES[facade_name]

            if facades_used:
                rel_path = str(php_file.relative_to(folder_path)).replace("\\", "/")
                imports = [
                    {"specifier": fqn, "names": [name]}
                    for name, fqn in facades_used.items()
                ]
                self._extra_imports.setdefault(rel_path, []).extend(imports)
                facade_count += 1

        if facade_count:
            logger.info("Laravel: extracted facade imports from %d files", facade_count)

    def _build_inertia_imports(self, folder_path: Path) -> None:
        """Create import edges from PHP controllers to Inertia.js Vue/React page components."""
        # Detect Inertia: composer require OR middleware file
        requires = _read_composer_require(folder_path)
        has_middleware = (folder_path / "app" / "Http" / "Middleware" / "HandleInertiaRequests.php").exists()
        if "inertiajs/inertia-laravel" not in requires and not has_middleware:
            return

        # Find pages directory
        pages_dir: Optional[str] = None
        for candidate in ("resources/js/Pages", "resources/js/pages", "resources/ts/Pages"):
            if (folder_path / candidate).is_dir():
                pages_dir = candidate
                break
        if pages_dir is None:
            return

        # Scan PHP files for Inertia::render / inertia() calls
        app_dir = folder_path / "app"
        if not app_dir.is_dir():
            return

        inertia_count = 0
        for php_file in sorted(app_dir.rglob("*.php")):
            content = _read_php(php_file)
            if not content:
                continue

            pages_found: dict[str, str] = {}
            for m in _INERTIA_RENDER.finditer(content):
                page = m.group("page")
                if page in pages_found:
                    continue
                # Strip extension if already present (e.g. 'Users/Index.vue')
                page_clean = re.sub(r"\.(vue|tsx|jsx)$", "", page)
                # Resolve page path: try .vue, .tsx, .jsx
                resolved = None
                for ext in (".vue", ".tsx", ".jsx"):
                    candidate_path = f"{pages_dir}/{page_clean}{ext}"
                    if (folder_path / candidate_path).exists():
                        resolved = candidate_path
                        break
                if resolved is None:
                    # Default to .vue even if file doesn't exist yet
                    resolved = f"{pages_dir}/{page_clean}.vue"
                pages_found[page] = resolved

            if pages_found:
                rel_path = str(php_file.relative_to(folder_path)).replace("\\", "/")
                imports = [
                    {"specifier": target, "names": []}
                    for target in pages_found.values()
                ]
                self._extra_imports.setdefault(rel_path, []).extend(imports)
                inertia_count += 1

        if inertia_count:
            logger.info("Laravel: extracted Inertia imports from %d files", inertia_count)

    def _build_api_call_imports(self, folder_path: Path) -> None:
        """Scan JS/TS/Vue files for fetch/axios API calls and match to Laravel routes."""
        if not self._api_route_map:
            return

        api_count = 0
        for ext_pattern in ("**/*.js", "**/*.ts", "**/*.vue", "**/*.tsx", "**/*.jsx"):
            for js_file in sorted(folder_path.glob(ext_pattern)):
                # Skip vendor/node_modules
                rel = str(js_file.relative_to(folder_path))
                if "node_modules" in rel or "vendor" in rel:
                    continue

                try:
                    content = js_file.read_text("utf-8", errors="replace")
                except Exception:
                    continue

                matched_controllers: dict[str, str] = {}
                for m in _API_CALL.finditer(content):
                    url = m.group("url")
                    ctrl_file = self._match_api_url(url)
                    if ctrl_file and ctrl_file not in matched_controllers:
                        matched_controllers[ctrl_file] = url

                if matched_controllers:
                    rel_path = str(js_file.relative_to(folder_path)).replace("\\", "/")
                    imports = [
                        {"specifier": ctrl, "names": []}
                        for ctrl in matched_controllers
                    ]
                    self._extra_imports.setdefault(rel_path, []).extend(imports)
                    api_count += 1

        if api_count:
            logger.info("Laravel: matched API calls to routes in %d files", api_count)

    def _match_api_url(self, url: str) -> Optional[str]:
        """Match a frontend API URL to a Laravel route's controller file."""
        normalized = "/" + url.lstrip("/")
        # Exact match first
        if normalized in self._api_route_map:
            return self._api_route_map[normalized]
        # Try matching with wildcard routes (replace path segments with *)
        segments = normalized.rstrip("/").split("/")
        for route_uri, ctrl_file in self._api_route_map.items():
            route_segments = route_uri.rstrip("/").split("/")
            if len(segments) != len(route_segments):
                continue
            if all(
                rs == "*" or rs == seg
                for rs, seg in zip(route_segments, segments)
            ):
                return ctrl_file
        return None

    def get_file_context(self, file_path: str) -> Optional[FileContext]:
        stem = Path(file_path).stem
        ctx = self._file_contexts.get(stem)
        if ctx:
            return ctx

        # Blade: enrich with component usage info
        if file_path.endswith(".blade.php") or ".blade." in file_path:
            return self._blade_context(file_path)

        return None

    def _blade_context(self, file_path: str) -> Optional[FileContext]:
        """Produce context for Blade template files."""
        stem = Path(file_path).stem.replace(".blade", "")
        return FileContext(
            description=f"Blade template: {stem}",
            tags=["blade", "template"],
        )

    def get_metadata(self) -> dict:
        """Expose migration column metadata for search_columns."""
        if not self._table_columns:
            return {}
        return {"laravel_columns": self._table_columns}

    def get_extra_imports(self) -> dict[str, list[dict]]:
        """Return Blade and route import edges for the dependency graph."""
        return self._extra_imports

    def stats(self) -> dict:
        return {
            "models": sum(1 for ctx in self._file_contexts.values()
                         if "eloquent-model" in ctx.tags),
            "controllers_with_routes": len(self._controller_routes),
            "migration_tables": len(self._table_columns),
            "blade_files_with_imports": sum(
                1 for k in self._extra_imports
                if k.startswith("resources/views/")
            ),
            "route_files_with_imports": sum(
                1 for k in self._extra_imports
                if k.startswith("routes/")
            ),
            "files_with_facade_imports": sum(
                1 for k, v in self._extra_imports.items()
                if any(imp["specifier"].startswith("Illuminate\\") for imp in v)
            ),
            "files_with_inertia_imports": sum(
                1 for k, v in self._extra_imports.items()
                if any("Pages/" in imp["specifier"] or "pages/" in imp["specifier"] for imp in v)
            ),
            "api_routes_mapped": len(self._api_route_map),
        }


def _find_controller_file(
    controller_name: str, controllers_dir: Path, folder_path: Path
) -> Optional[str]:
    """Find a controller's repo-relative file path by class name."""
    if not controllers_dir.is_dir():
        return None
    for php_file in controllers_dir.rglob("*.php"):
        if php_file.stem == controller_name:
            return str(php_file.relative_to(folder_path)).replace("\\", "/")
    return None


def _blade_dot_to_path(dot_ref: str) -> Optional[str]:
    """Convert Blade dot-notation to a relative file path.

    Example: 'layouts.app' → 'resources/views/layouts/app.blade.php'
    Returns None for empty or invalid references.
    """
    if not dot_ref or dot_ref == ".":
        return None
    parts = dot_ref.replace(".", "/")
    return f"resources/views/{parts}.blade.php"


def _blade_component_to_path(component_name: str) -> Optional[str]:
    """Convert <x-component> name to a relative file path.

    Example: 'alert' → 'resources/views/components/alert.blade.php'
             'forms.input' → 'resources/views/components/forms/input.blade.php'
    Returns None for empty names.
    """
    if not component_name:
        return None
    parts = component_name.replace(".", "/")
    return f"resources/views/components/{parts}.blade.php"


def _guess_table(model_stem: str) -> str:
    """Simple pluralization heuristic for model class name → table name."""
    name = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", model_stem).lower()
    if name.endswith("y") and not name.endswith(("ay", "ey", "iy", "oy", "uy")):
        return name[:-1] + "ies"
    if name.endswith(("s", "x", "z", "ch", "sh")):
        return name + "es"
    return name + "s"
