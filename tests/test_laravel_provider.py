"""Tests for the Laravel context provider."""

import json
from pathlib import Path

import pytest

from jcodemunch_mcp.parser.context.laravel import (
    LaravelContextProvider,
    _blade_component_to_path,
    _blade_dot_to_path,
    _guess_table,
    _parse_migration,
    _parse_model,
    _parse_routes,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_laravel_project(tmp_path: Path, laravel_version: str = "^11.0") -> None:
    """Create a minimal Laravel project skeleton."""
    _write(tmp_path / "artisan", "#!/usr/bin/env php\n<?php // artisan\n")
    _write(tmp_path / "composer.json", json.dumps({
        "require": {"laravel/framework": laravel_version},
        "autoload": {"psr-4": {"App\\": "app/"}},
    }))


# ---------------------------------------------------------------------------
# _guess_table
# ---------------------------------------------------------------------------

class TestGuessTable:
    def test_regular_noun(self):
        assert _guess_table("User") == "users"

    def test_y_suffix(self):
        assert _guess_table("Category") == "categories"

    def test_already_plural_pattern(self):
        assert _guess_table("Status") == "statuses"

    def test_camel_case(self):
        assert _guess_table("BlogPost") == "blog_posts"


# ---------------------------------------------------------------------------
# _parse_migration
# ---------------------------------------------------------------------------

MIGRATION_PHP = """<?php
use Illuminate\\Database\\Migrations\\Migration;
use Illuminate\\Database\\Schema\\Blueprint;
use Illuminate\\Support\\Facades\\Schema;

return new class extends Migration {
    public function up(): void
    {
        Schema::create('users', function (Blueprint $table) {
            $table->id();
            $table->string('name');
            $table->string('email')->unique();
            $table->foreignId('team_id')->constrained();
            $table->timestamp('email_verified_at')->nullable();
            $table->timestamps();
        });
    }
};
"""


class TestParseMigration:
    def test_parses_table_name(self):
        result = _parse_migration(MIGRATION_PHP)
        assert result is not None
        table_name, columns = result
        assert table_name == "users"

    def test_parses_column_names(self):
        _, columns = _parse_migration(MIGRATION_PHP)
        assert "name" in columns
        assert "email" in columns

    def test_unique_modifier(self):
        _, columns = _parse_migration(MIGRATION_PHP)
        assert "unique" in columns["email"]

    def test_nullable_modifier(self):
        _, columns = _parse_migration(MIGRATION_PHP)
        assert "nullable" in columns["email_verified_at"]

    def test_skips_timestamps_helper(self):
        _, columns = _parse_migration(MIGRATION_PHP)
        # timestamps() is a helper, not a column name
        assert "timestamps" not in columns

    def test_no_schema_returns_none(self):
        assert _parse_migration("<?php echo 'hello';") is None


# ---------------------------------------------------------------------------
# _parse_model
# ---------------------------------------------------------------------------

MODEL_PHP = """<?php
namespace App\\Models;

class User extends Model
{
    protected $fillable = ['name', 'email', 'password'];
    protected $casts = ['email_verified_at' => 'datetime'];

    public function posts()
    {
        return $this->hasMany(Post::class);
    }

    public function team()
    {
        return $this->belongsTo(Team::class);
    }

    public function scopeActive($query)
    {
        return $query->where('active', true);
    }
}
"""


class TestParseModel:
    def test_relationships(self):
        meta = _parse_model(MODEL_PHP)
        assert any("hasMany" in r for r in meta["relationships"])
        assert any("belongsTo" in r for r in meta["relationships"])

    def test_fillable(self):
        meta = _parse_model(MODEL_PHP)
        assert "name" in meta["fillable"]
        assert "email" in meta["fillable"]

    def test_scopes(self):
        meta = _parse_model(MODEL_PHP)
        assert "Active" in meta["scopes"]

    def test_no_relationships(self):
        meta = _parse_model("<?php class Foo extends Model {}")
        assert meta["relationships"] == []


# ---------------------------------------------------------------------------
# _parse_routes
# ---------------------------------------------------------------------------

ROUTES_PHP = """<?php
use App\\Http\\Controllers\\UserController;
use Illuminate\\Support\\Facades\\Route;

Route::get('/users', [UserController::class, 'index'])->name('users.index');
Route::post('/users', [UserController::class, 'store'])->name('users.store');
Route::get('/users/{user}', [UserController::class, 'show'])->name('users.show');
"""


class TestParseRoutes:
    def test_parses_array_style_routes(self, tmp_path):
        routes_dir = tmp_path / "routes"
        routes_dir.mkdir()
        (routes_dir / "api.php").write_text(ROUTES_PHP)
        routes = _parse_routes(routes_dir)
        assert len(routes) == 3
        verbs = [r["verb"] for r in routes]
        assert "GET" in verbs
        assert "POST" in verbs

    def test_route_names_extracted(self, tmp_path):
        routes_dir = tmp_path / "routes"
        routes_dir.mkdir()
        (routes_dir / "api.php").write_text(ROUTES_PHP)
        routes = _parse_routes(routes_dir)
        names = [r["name"] for r in routes]
        assert "users.index" in names
        assert "users.store" in names

    def test_controller_extracted(self, tmp_path):
        routes_dir = tmp_path / "routes"
        routes_dir.mkdir()
        (routes_dir / "api.php").write_text(ROUTES_PHP)
        routes = _parse_routes(routes_dir)
        assert all(r["controller"] == "UserController" for r in routes)

    def test_missing_routes_dir_returns_empty(self, tmp_path):
        routes = _parse_routes(tmp_path / "routes")
        assert routes == []


# ---------------------------------------------------------------------------
# LaravelContextProvider
# ---------------------------------------------------------------------------

class TestLaravelContextProvider:
    def test_detect_laravel_project(self, tmp_path):
        _make_laravel_project(tmp_path)
        provider = LaravelContextProvider()
        assert provider.detect(tmp_path) is True

    def test_detect_no_artisan(self, tmp_path):
        # No artisan file
        (tmp_path / "composer.json").write_text(json.dumps({
            "require": {"laravel/framework": "^11.0"}
        }))
        provider = LaravelContextProvider()
        assert provider.detect(tmp_path) is False

    def test_detect_wrong_framework(self, tmp_path):
        _write(tmp_path / "artisan", "#!/usr/bin/env php\n")
        (tmp_path / "composer.json").write_text(json.dumps({
            "require": {"symfony/symfony": "^7.0"}
        }))
        provider = LaravelContextProvider()
        assert provider.detect(tmp_path) is False

    def test_load_model_enrichment(self, tmp_path):
        _make_laravel_project(tmp_path)
        _write(tmp_path / "app" / "Models" / "User.php", MODEL_PHP)

        provider = LaravelContextProvider()
        assert provider.detect(tmp_path)
        provider.load(tmp_path)

        ctx = provider.get_file_context("app/Models/User.php")
        assert ctx is not None
        assert "users" in ctx.description or "User" in ctx.description
        assert "eloquent-model" in ctx.tags

    def test_load_migration_columns(self, tmp_path):
        _make_laravel_project(tmp_path)
        _write(tmp_path / "database" / "migrations" / "2024_01_01_create_users_table.php",
               MIGRATION_PHP)

        provider = LaravelContextProvider()
        assert provider.detect(tmp_path)
        provider.load(tmp_path)

        meta = provider.get_metadata()
        assert "laravel_columns" in meta
        assert "users" in meta["laravel_columns"]
        assert "email" in meta["laravel_columns"]["users"]

    def test_load_route_enriches_controller(self, tmp_path):
        _make_laravel_project(tmp_path)
        _write(tmp_path / "routes" / "api.php", ROUTES_PHP)
        _write(tmp_path / "app" / "Http" / "Controllers" / "UserController.php",
               "<?php namespace App\\Http\\Controllers; class UserController {}")

        provider = LaravelContextProvider()
        assert provider.detect(tmp_path)
        provider.load(tmp_path)

        ctx = provider.get_file_context("app/Http/Controllers/UserController.php")
        assert ctx is not None
        assert "controller" in ctx.tags

    def test_stats(self, tmp_path):
        _make_laravel_project(tmp_path)
        _write(tmp_path / "app" / "Models" / "User.php", MODEL_PHP)
        _write(tmp_path / "database" / "migrations" / "2024_01_01_create_users_table.php",
               MIGRATION_PHP)

        provider = LaravelContextProvider()
        assert provider.detect(tmp_path)
        provider.load(tmp_path)

        stats = provider.stats()
        assert stats["models"] >= 1
        assert stats["migration_tables"] >= 1

    def test_blade_context_returned(self, tmp_path):
        _make_laravel_project(tmp_path)
        provider = LaravelContextProvider()
        assert provider.detect(tmp_path)
        provider.load(tmp_path)

        ctx = provider.get_file_context("resources/views/users/index.blade.php")
        assert ctx is not None
        assert "blade" in ctx.tags

    def test_non_laravel_file_returns_none(self, tmp_path):
        _make_laravel_project(tmp_path)
        provider = LaravelContextProvider()
        assert provider.detect(tmp_path)
        provider.load(tmp_path)

        ctx = provider.get_file_context("src/something.go")
        assert ctx is None


# ---------------------------------------------------------------------------
# Blade dot-notation helpers
# ---------------------------------------------------------------------------

class TestBladeDotToPath:
    def test_simple_view(self):
        assert _blade_dot_to_path("welcome") == "resources/views/welcome.blade.php"

    def test_nested_view(self):
        assert _blade_dot_to_path("layouts.app") == "resources/views/layouts/app.blade.php"

    def test_deeply_nested(self):
        assert _blade_dot_to_path("admin.users.index") == "resources/views/admin/users/index.blade.php"


class TestBladeComponentToPath:
    def test_simple_component(self):
        assert _blade_component_to_path("alert") == "resources/views/components/alert.blade.php"

    def test_nested_component(self):
        assert _blade_component_to_path("forms.input") == "resources/views/components/forms/input.blade.php"

    def test_hyphenated_component(self):
        assert _blade_component_to_path("nav-link") == "resources/views/components/nav-link.blade.php"


# ---------------------------------------------------------------------------
# Blade import extraction
# ---------------------------------------------------------------------------

BLADE_LAYOUT = """<!DOCTYPE html>
<html>
<body>@yield('content')</body>
</html>
"""

BLADE_WITH_REFS = """
@extends('layouts.app')

@section('content')
    @include('partials.header')
    @includeWhen($showSidebar, 'partials.sidebar')
    @includeFirst(['partials.primary', 'partials.fallback'])

    <x-alert />
    <x-forms.input type="text" />

    {{ view('components.footer') }}
@endsection
"""


class TestBladeImportExtraction:
    def test_blade_imports_extracted(self, tmp_path):
        _make_laravel_project(tmp_path)
        _write(tmp_path / "resources" / "views" / "layouts" / "app.blade.php", BLADE_LAYOUT)
        _write(tmp_path / "resources" / "views" / "pages" / "home.blade.php", BLADE_WITH_REFS)

        provider = LaravelContextProvider()
        assert provider.detect(tmp_path)
        provider.load(tmp_path)

        extras = provider.get_extra_imports()
        home_imports = extras.get("resources/views/pages/home.blade.php", [])
        specifiers = {imp["specifier"] for imp in home_imports}

        # @extends('layouts.app')
        assert "resources/views/layouts/app.blade.php" in specifiers
        # @include('partials.header')
        assert "resources/views/partials/header.blade.php" in specifiers
        # @includeWhen(..., 'partials.sidebar')
        assert "resources/views/partials/sidebar.blade.php" in specifiers
        # <x-alert>
        assert "resources/views/components/alert.blade.php" in specifiers
        # <x-forms.input>
        assert "resources/views/components/forms/input.blade.php" in specifiers
        # @includeFirst(['partials.primary', 'partials.fallback']) — first candidate only
        assert "resources/views/partials/primary.blade.php" in specifiers
        assert "resources/views/partials/fallback.blade.php" not in specifiers

    def test_layout_has_no_blade_imports(self, tmp_path):
        _make_laravel_project(tmp_path)
        _write(tmp_path / "resources" / "views" / "layouts" / "app.blade.php", BLADE_LAYOUT)

        provider = LaravelContextProvider()
        assert provider.detect(tmp_path)
        provider.load(tmp_path)

        extras = provider.get_extra_imports()
        # Layout doesn't reference other views
        assert "resources/views/layouts/app.blade.php" not in extras

    def test_no_views_dir_is_safe(self, tmp_path):
        _make_laravel_project(tmp_path)
        provider = LaravelContextProvider()
        assert provider.detect(tmp_path)
        provider.load(tmp_path)

        extras = provider.get_extra_imports()
        assert not any(k.startswith("resources/views/") for k in extras)


# ---------------------------------------------------------------------------
# Route → Controller import extraction
# ---------------------------------------------------------------------------

class TestRouteControllerImports:
    def test_route_imports_controller(self, tmp_path):
        _make_laravel_project(tmp_path)
        _write(tmp_path / "routes" / "api.php", ROUTES_PHP)

        provider = LaravelContextProvider()
        assert provider.detect(tmp_path)
        provider.load(tmp_path)

        extras = provider.get_extra_imports()
        route_imports = extras.get("routes/api.php", [])
        specifiers = {imp["specifier"] for imp in route_imports}

        # Short name when use-imported; FQN when inlined
        assert "UserController" in specifiers

    def test_route_imports_deduplicated(self, tmp_path):
        """Multiple routes to same controller should produce a single import edge."""
        _make_laravel_project(tmp_path)
        _write(tmp_path / "routes" / "api.php", ROUTES_PHP)

        provider = LaravelContextProvider()
        assert provider.detect(tmp_path)
        provider.load(tmp_path)

        extras = provider.get_extra_imports()
        route_imports = extras.get("routes/api.php", [])
        # 3 routes all to UserController, but only 1 import edge
        assert len(route_imports) == 1
        assert route_imports[0]["names"] == ["UserController"]

    def test_inline_fqn_controller(self, tmp_path):
        """When controller FQN is inline (no use statement), full namespace is captured."""
        _make_laravel_project(tmp_path)
        routes_content = r"""<?php
Route::get('/users', [\App\Http\Controllers\UserController::class, 'index']);
"""
        _write(tmp_path / "routes" / "web.php", routes_content)

        provider = LaravelContextProvider()
        assert provider.detect(tmp_path)
        provider.load(tmp_path)

        extras = provider.get_extra_imports()
        route_imports = extras.get("routes/web.php", [])
        specifiers = {imp["specifier"] for imp in route_imports}
        assert r"\App\Http\Controllers\UserController" in specifiers

    def test_multiple_controllers(self, tmp_path):
        _make_laravel_project(tmp_path)
        routes_content = """<?php
use App\\Http\\Controllers\\UserController;
use App\\Http\\Controllers\\PostController;

Route::get('/users', [UserController::class, 'index']);
Route::get('/posts', [PostController::class, 'index']);
"""
        _write(tmp_path / "routes" / "web.php", routes_content)

        provider = LaravelContextProvider()
        assert provider.detect(tmp_path)
        provider.load(tmp_path)

        extras = provider.get_extra_imports()
        route_imports = extras.get("routes/web.php", [])
        specifiers = {imp["specifier"] for imp in route_imports}

        assert "UserController" in specifiers
        assert "PostController" in specifiers


# ---------------------------------------------------------------------------
# Eloquent relationship → import edges
# ---------------------------------------------------------------------------

class TestEloquentRelationshipImports:
    def test_relationship_creates_import_edge(self, tmp_path):
        _make_laravel_project(tmp_path)
        _write(tmp_path / "app" / "Models" / "User.php", MODEL_PHP)

        provider = LaravelContextProvider()
        assert provider.detect(tmp_path)
        provider.load(tmp_path)

        extras = provider.get_extra_imports()
        model_imports = extras.get("app/Models/User.php", [])
        specifiers = {imp["specifier"] for imp in model_imports}

        assert "Post" in specifiers
        assert "Team" in specifiers

    def test_relationship_with_fqn(self, tmp_path):
        _make_laravel_project(tmp_path)
        model_with_fqn = r"""<?php
namespace App\Models;

class Order extends Model
{
    public function items()
    {
        return $this->hasMany(\App\Models\OrderItem::class);
    }
}
"""
        _write(tmp_path / "app" / "Models" / "Order.php", model_with_fqn)

        provider = LaravelContextProvider()
        assert provider.detect(tmp_path)
        provider.load(tmp_path)

        extras = provider.get_extra_imports()
        model_imports = extras.get("app/Models/Order.php", [])
        specifiers = {imp["specifier"] for imp in model_imports}
        assert r"\App\Models\OrderItem" in specifiers

    def test_no_relationships_no_imports(self, tmp_path):
        _make_laravel_project(tmp_path)
        _write(tmp_path / "app" / "Models" / "Setting.php",
               "<?php namespace App\\Models; class Setting extends Model {}")

        provider = LaravelContextProvider()
        assert provider.detect(tmp_path)
        provider.load(tmp_path)

        extras = provider.get_extra_imports()
        assert "app/Models/Setting.php" not in extras

    def test_deduplicated_relationships(self, tmp_path):
        """Same model referenced in multiple relationships should produce one import."""
        _make_laravel_project(tmp_path)
        model = r"""<?php
namespace App\Models;

class User extends Model
{
    public function posts() { return $this->hasMany(Post::class); }
    public function latestPost() { return $this->hasOne(Post::class); }
}
"""
        _write(tmp_path / "app" / "Models" / "User.php", model)

        provider = LaravelContextProvider()
        assert provider.detect(tmp_path)
        provider.load(tmp_path)

        extras = provider.get_extra_imports()
        model_imports = extras.get("app/Models/User.php", [])
        post_imports = [i for i in model_imports if i["specifier"] == "Post"]
        assert len(post_imports) == 1



# ---------------------------------------------------------------------------
# Facade resolution → import edges
# ---------------------------------------------------------------------------

class TestFacadeImports:
    def test_facade_call_creates_import(self, tmp_path):
        _make_laravel_project(tmp_path)
        controller = r"""<?php
namespace App\Http\Controllers;

class OrderController extends Controller
{
    public function index()
    {
        $orders = Cache::get('orders');
        DB::table('orders')->get();
        Log::info('Orders fetched');
    }
}
"""
        _write(tmp_path / "app" / "Http" / "Controllers" / "OrderController.php", controller)

        provider = LaravelContextProvider()
        assert provider.detect(tmp_path)
        provider.load(tmp_path)

        extras = provider.get_extra_imports()
        imports = extras.get("app/Http/Controllers/OrderController.php", [])
        specifiers = {imp["specifier"] for imp in imports}

        assert "Illuminate\\Cache\\CacheManager" in specifiers
        assert "Illuminate\\Database\\DatabaseManager" in specifiers
        assert "Illuminate\\Log\\LogManager" in specifiers

    def test_facade_class_reference_not_matched(self, tmp_path):
        """Cache::class should NOT create a facade import (it's a class reference)."""
        _make_laravel_project(tmp_path)
        _write(tmp_path / "app" / "Services" / "Foo.php", r"""<?php
namespace App\Services;
class Foo {
    public function bar() { return Cache::class; }
}
""")

        provider = LaravelContextProvider()
        assert provider.detect(tmp_path)
        provider.load(tmp_path)

        extras = provider.get_extra_imports()
        imports = extras.get("app/Services/Foo.php", [])
        specifiers = {imp["specifier"] for imp in imports}
        # Cache::class is NOT a facade call
        assert "Illuminate\\Cache\\CacheManager" not in specifiers

    def test_non_facade_static_call_ignored(self, tmp_path):
        _make_laravel_project(tmp_path)
        _write(tmp_path / "app" / "Services" / "Bar.php", r"""<?php
namespace App\Services;
class Bar {
    public function baz() { return MyCustomClass::doSomething(); }
}
""")

        provider = LaravelContextProvider()
        assert provider.detect(tmp_path)
        provider.load(tmp_path)

        extras = provider.get_extra_imports()
        imports = extras.get("app/Services/Bar.php", [])
        # MyCustomClass is not in the facade registry
        assert len(imports) == 0

    def test_facade_deduplicated_in_same_file(self, tmp_path):
        _make_laravel_project(tmp_path)
        _write(tmp_path / "app" / "Services" / "Dup.php", r"""<?php
namespace App\Services;
class Dup {
    public function a() { Cache::get('x'); }
    public function b() { Cache::put('y', 1); }
}
""")

        provider = LaravelContextProvider()
        assert provider.detect(tmp_path)
        provider.load(tmp_path)

        extras = provider.get_extra_imports()
        imports = extras.get("app/Services/Dup.php", [])
        cache_imports = [i for i in imports if "Cache" in i.get("names", [])]
        assert len(cache_imports) == 1


    def test_stats_include_import_counts(self, tmp_path):
        _make_laravel_project(tmp_path)
        _write(tmp_path / "routes" / "api.php", ROUTES_PHP)
        _write(tmp_path / "resources" / "views" / "home.blade.php", BLADE_WITH_REFS)

        provider = LaravelContextProvider()
        assert provider.detect(tmp_path)
        provider.load(tmp_path)

        stats = provider.stats()
        assert stats["route_files_with_imports"] >= 1
        assert stats["blade_files_with_imports"] >= 1


# ---------------------------------------------------------------------------
# Inertia.js cross-language bridge
# ---------------------------------------------------------------------------

def _make_inertia_project(tmp_path, via_composer=True):
    """Create a minimal Laravel + Inertia.js project skeleton."""
    _make_laravel_project(tmp_path)
    if via_composer:
        import json
        composer = json.loads((tmp_path / "composer.json").read_text())
        composer["require"]["inertiajs/inertia-laravel"] = "^1.0"
        (tmp_path / "composer.json").write_text(json.dumps(composer))
    else:
        _write(tmp_path / "app" / "Http" / "Middleware" / "HandleInertiaRequests.php",
               "<?php\nnamespace App\\Http\\Middleware;\nclass HandleInertiaRequests {}\n")
    (tmp_path / "resources" / "js" / "Pages").mkdir(parents=True, exist_ok=True)


class TestInertiaImports:
    def test_inertia_render_creates_import(self, tmp_path):
        _make_inertia_project(tmp_path)
        _write(tmp_path / "app" / "Http" / "Controllers" / "UserController.php", r"""<?php
namespace App\Http\Controllers;
use Inertia\Inertia;

class UserController extends Controller
{
    public function index()
    {
        return Inertia::render('Users/Index', ['users' => []]);
    }
}
""")
        _write(tmp_path / "resources" / "js" / "Pages" / "Users" / "Index.vue",
               "<template><div>Users</div></template>")

        provider = LaravelContextProvider()
        assert provider.detect(tmp_path)
        provider.load(tmp_path)

        extras = provider.get_extra_imports()
        ctrl_imports = extras.get("app/Http/Controllers/UserController.php", [])
        specifiers = {imp["specifier"] for imp in ctrl_imports}
        assert "resources/js/Pages/Users/Index.vue" in specifiers

    def test_inertia_helper_function(self, tmp_path):
        _make_inertia_project(tmp_path)
        _write(tmp_path / "app" / "Http" / "Controllers" / "DashCtrl.php", r"""<?php
namespace App\Http\Controllers;
class DashCtrl extends Controller
{
    public function show() { return inertia('Dashboard'); }
}
""")
        _write(tmp_path / "resources" / "js" / "Pages" / "Dashboard.vue", "<template/>")

        provider = LaravelContextProvider()
        assert provider.detect(tmp_path)
        provider.load(tmp_path)

        extras = provider.get_extra_imports()
        imports = extras.get("app/Http/Controllers/DashCtrl.php", [])
        specifiers = {imp["specifier"] for imp in imports}
        assert "resources/js/Pages/Dashboard.vue" in specifiers

    def test_inertia_tsx_fallback(self, tmp_path):
        _make_inertia_project(tmp_path)
        _write(tmp_path / "app" / "Http" / "Controllers" / "Ctrl.php", r"""<?php
class Ctrl { public function x() { return Inertia::render('Settings'); } }
""")
        _write(tmp_path / "resources" / "js" / "Pages" / "Settings.tsx", "export default () => <div/>;")

        provider = LaravelContextProvider()
        assert provider.detect(tmp_path)
        provider.load(tmp_path)

        extras = provider.get_extra_imports()
        imports = extras.get("app/Http/Controllers/Ctrl.php", [])
        specifiers = {imp["specifier"] for imp in imports}
        assert "resources/js/Pages/Settings.tsx" in specifiers

    def test_no_inertia_no_imports(self, tmp_path):
        _make_laravel_project(tmp_path)
        _write(tmp_path / "app" / "Http" / "Controllers" / "Ctrl.php", r"""<?php
class Ctrl { public function x() { return Inertia::render('Foo'); } }
""")

        provider = LaravelContextProvider()
        assert provider.detect(tmp_path)
        provider.load(tmp_path)

        extras = provider.get_extra_imports()
        imports = extras.get("app/Http/Controllers/Ctrl.php", [])
        # No inertia dependency — no inertia imports
        inertia_imports = [i for i in imports if "Pages/" in i.get("specifier", "")]
        assert len(inertia_imports) == 0

    def test_inertia_detected_via_middleware(self, tmp_path):
        _make_inertia_project(tmp_path, via_composer=False)
        _write(tmp_path / "app" / "Http" / "Controllers" / "Ctrl.php", r"""<?php
class Ctrl { public function x() { return Inertia::render('Home'); } }
""")
        _write(tmp_path / "resources" / "js" / "Pages" / "Home.vue", "<template/>")

        provider = LaravelContextProvider()
        assert provider.detect(tmp_path)
        provider.load(tmp_path)

        extras = provider.get_extra_imports()
        imports = extras.get("app/Http/Controllers/Ctrl.php", [])
        specifiers = {imp["specifier"] for imp in imports}
        assert "resources/js/Pages/Home.vue" in specifiers

    def test_inertia_multiple_renders_deduplicated(self, tmp_path):
        """Multiple renders to same page should produce one import edge."""
        _make_inertia_project(tmp_path)
        _write(tmp_path / "app" / "Http" / "Controllers" / "Ctrl.php", r"""<?php
class Ctrl {
    public function index() { return Inertia::render('Users/Index'); }
    public function create() { return Inertia::render('Users/Index'); }
}
""")
        _write(tmp_path / "resources" / "js" / "Pages" / "Users" / "Index.vue", "<template/>")

        provider = LaravelContextProvider()
        assert provider.detect(tmp_path)
        provider.load(tmp_path)

        extras = provider.get_extra_imports()
        imports = extras.get("app/Http/Controllers/Ctrl.php", [])
        vue_imports = [i for i in imports if i["specifier"].endswith(".vue")]
        assert len(vue_imports) == 1

    def test_inertia_page_with_extension_not_duplicated(self, tmp_path):
        """Inertia::render('Users/Index.vue') should not produce .vue.vue path."""
        _make_inertia_project(tmp_path)
        _write(tmp_path / "app" / "Http" / "Controllers" / "Ctrl.php", r"""<?php
class Ctrl { public function x() { return Inertia::render('Home.vue'); } }
""")
        _write(tmp_path / "resources" / "js" / "Pages" / "Home.vue", "<template/>")

        provider = LaravelContextProvider()
        assert provider.detect(tmp_path)
        provider.load(tmp_path)

        extras = provider.get_extra_imports()
        imports = extras.get("app/Http/Controllers/Ctrl.php", [])
        specifiers = {imp["specifier"] for imp in imports}
        assert "resources/js/Pages/Home.vue" in specifiers
        # Must NOT have double extension
        assert "resources/js/Pages/Home.vue.vue" not in specifiers


# ---------------------------------------------------------------------------
# fetch/axios API call → route matching
# ---------------------------------------------------------------------------

ROUTES_WITH_API = """<?php
use App\\Http\\Controllers\\Api\\UserController;
use App\\Http\\Controllers\\Api\\OrderController;

Route::get('/api/users', [UserController::class, 'index']);
Route::get('/api/users/{user}', [UserController::class, 'show']);
Route::post('/api/orders', [OrderController::class, 'store']);
"""


class TestApiCallImports:
    def test_fetch_matches_route(self, tmp_path):
        _make_laravel_project(tmp_path)
        _write(tmp_path / "routes" / "api.php", ROUTES_WITH_API)
        _write(tmp_path / "app" / "Http" / "Controllers" / "Api" / "UserController.php",
               "<?php namespace App\\Http\\Controllers\\Api; class UserController {}")
        _write(tmp_path / "resources" / "js" / "api.js",
               "fetch('/api/users').then(r => r.json())")

        provider = LaravelContextProvider()
        assert provider.detect(tmp_path)
        provider.load(tmp_path)

        extras = provider.get_extra_imports()
        js_imports = extras.get("resources/js/api.js", [])
        specifiers = {imp["specifier"] for imp in js_imports}
        assert "app/Http/Controllers/Api/UserController.php" in specifiers

    def test_axios_matches_route(self, tmp_path):
        _make_laravel_project(tmp_path)
        _write(tmp_path / "routes" / "api.php", ROUTES_WITH_API)
        _write(tmp_path / "app" / "Http" / "Controllers" / "Api" / "OrderController.php",
               "<?php namespace App\\Http\\Controllers\\Api; class OrderController {}")
        _write(tmp_path / "resources" / "js" / "orders.ts",
               "axios.post('/api/orders', data)")

        provider = LaravelContextProvider()
        assert provider.detect(tmp_path)
        provider.load(tmp_path)

        extras = provider.get_extra_imports()
        imports = extras.get("resources/js/orders.ts", [])
        specifiers = {imp["specifier"] for imp in imports}
        assert "app/Http/Controllers/Api/OrderController.php" in specifiers

    def test_dynamic_segment_matched(self, tmp_path):
        """fetch('/api/users/123') should match route /api/users/{user}."""
        _make_laravel_project(tmp_path)
        _write(tmp_path / "routes" / "api.php", ROUTES_WITH_API)
        _write(tmp_path / "app" / "Http" / "Controllers" / "Api" / "UserController.php",
               "<?php namespace App\\Http\\Controllers\\Api; class UserController {}")
        _write(tmp_path / "resources" / "js" / "user.vue",
               "<script>fetch('/api/users/42')</script>")

        provider = LaravelContextProvider()
        assert provider.detect(tmp_path)
        provider.load(tmp_path)

        extras = provider.get_extra_imports()
        imports = extras.get("resources/js/user.vue", [])
        specifiers = {imp["specifier"] for imp in imports}
        assert "app/Http/Controllers/Api/UserController.php" in specifiers

    def test_no_routes_no_api_imports(self, tmp_path):
        _make_laravel_project(tmp_path)
        _write(tmp_path / "resources" / "js" / "app.js",
               "fetch('/api/something')")

        provider = LaravelContextProvider()
        assert provider.detect(tmp_path)
        provider.load(tmp_path)

        extras = provider.get_extra_imports()
        imports = extras.get("resources/js/app.js", [])
        assert len(imports) == 0

    def test_non_api_fetch_ignored(self, tmp_path):
        _make_laravel_project(tmp_path)
        _write(tmp_path / "routes" / "api.php", ROUTES_WITH_API)
        _write(tmp_path / "app" / "Http" / "Controllers" / "Api" / "UserController.php",
               "<?php class UserController {}")
        _write(tmp_path / "resources" / "js" / "ext.js",
               "fetch('https://external.api.com/data')")

        provider = LaravelContextProvider()
        assert provider.detect(tmp_path)
        provider.load(tmp_path)

        extras = provider.get_extra_imports()
        imports = extras.get("resources/js/ext.js", [])
        assert len(imports) == 0
