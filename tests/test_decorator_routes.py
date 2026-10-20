"""Tests for decorator_routes provider (Flask, FastAPI, Spring Boot, NestJS, ASP.NET)."""

from pathlib import Path

import pytest

from jcodemunch_mcp.parser.context.decorator_routes import DecoratorRoutesProvider


class TestDecoratorRoutesFlask:
    """Tests for Flask detection and route extraction."""

    def test_flask_detect(self, tmp_path: Path):
        (tmp_path / "requirements.txt").write_text("flask==2.0.0\n")
        (tmp_path / "app.py").write_text("from flask import Flask\napp = Flask(__name__)\n")
        provider = DecoratorRoutesProvider()
        assert provider.detect(tmp_path) is True

    def test_flask_not_detected_without_flask_dep(self, tmp_path: Path):
        (tmp_path / "requirements.txt").write_text("django==4.0.0\n")
        provider = DecoratorRoutesProvider()
        assert provider.detect(tmp_path) is False

    def test_flask_route_extraction(self, tmp_path: Path):
        (tmp_path / "requirements.txt").write_text("flask\n")
        app_py = """
from flask import Flask, jsonify
app = Flask(__name__)

@app.route('/')
def index():
    return jsonify({'status': 'ok'})

@app.route('/users')
def users():
    return jsonify([])

@app.get('/api/users')
def get_users():
    return jsonify([])

@app.post('/api/users')
def create_user():
    return jsonify({'id': 1}), 201
"""
        (tmp_path / "app.py").write_text(app_py)
        provider = DecoratorRoutesProvider()
        assert provider.detect(tmp_path) is True
        provider.load(tmp_path)

        ctx = provider.get_file_context("app.py")
        assert ctx is not None
        assert "flask-route" in ctx.tags
        assert ctx.properties["framework"] == "flask"
        assert "GET /" in ctx.properties["routes"]
        assert "GET /users" in ctx.properties["routes"]
        assert "GET /api/users" in ctx.properties["routes"]
        assert "POST /api/users" in ctx.properties["routes"]


    def test_flask_route_reads_methods(self, tmp_path: Path):
        """#1177 (@skibadubskiybadubs): `methods=` names the verbs; GET only when it is absent."""
        (tmp_path / "requirements.txt").write_text("flask\n")
        (tmp_path / "tokens.py").write_text(
            "from flask import Blueprint\n"
            "bp = Blueprint('t', __name__)\n\n"
            "@bp.route('/tokens', methods=['POST'])\n"
            "def make():\n    pass\n\n"
            "@bp.route('/tokens', methods=['DELETE'])\n"
            "def revoke():\n    pass\n\n"
            "@bp.route(\n    '/items',\n    methods=(\"GET\", 'put'),\n    strict_slashes=False,\n)\n"
            "def items():\n    pass\n\n"
            "@bp.route('/ping')\n"
            "def ping():\n    pass\n\n"
            "VERBS = ['PATCH']\n"
            "@bp.route('/dyn', methods=VERBS)\n"
            "def dyn():\n    pass\n"
        )
        provider = DecoratorRoutesProvider()
        assert provider.detect(tmp_path) is True
        provider.load(tmp_path)
        routes = provider.get_file_context("tokens.py").properties["routes"]
        assert "POST /tokens" in routes and "DELETE /tokens" in routes
        assert "GET /tokens" not in routes
        assert "GET /items" in routes and "PUT /items" in routes
        assert "GET /ping" in routes
        # A non-literal `methods=` names verbs nobody can read here: never claim GET.
        assert "ANY /dyn" in routes and "GET /dyn" not in routes


class TestDecoratorRoutesFastAPI:
    """Tests for FastAPI detection and route extraction."""

    def test_fastapi_detect(self, tmp_path: Path):
        (tmp_path / "requirements.txt").write_text("fastapi==0.100.0\n")
        (tmp_path / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n")
        provider = DecoratorRoutesProvider()
        assert provider.detect(tmp_path) is True

    def test_fastapi_not_detected_when_only_flask(self, tmp_path: Path):
        # When only Flask is present (no FastAPI), Flask is detected but FastAPI isn't
        (tmp_path / "requirements.txt").write_text("flask==2.0.0\n")
        provider = DecoratorRoutesProvider()
        # Flask IS detected (returns True), but it's Flask, not FastAPI
        assert provider.detect(tmp_path) is True
        assert provider._active_config.name == "flask"

    def test_fastapi_route_extraction(self, tmp_path: Path):
        (tmp_path / "requirements.txt").write_text("fastapi\n")
        main_py = """
from fastapi import FastAPI
app = FastAPI()

@app.get('/')
async def read_root():
    return {'status': 'ok'}

@app.get('/users/{user_id}')
async def read_user(user_id: int):
    return {'user_id': user_id}

@app.post('/users')
async def create_user():
    return {'id': 1}
"""
        (tmp_path / "main.py").write_text(main_py)
        provider = DecoratorRoutesProvider()
        assert provider.detect(tmp_path) is True
        provider.load(tmp_path)

        ctx = provider.get_file_context("main.py")
        assert ctx is not None
        assert "fastapi-route" in ctx.tags
        assert ctx.properties["framework"] == "fastapi"
        assert "GET /" in ctx.properties["routes"]
        assert "GET /users/{user_id}" in ctx.properties["routes"]
        assert "POST /users" in ctx.properties["routes"]


class TestDecoratorRoutesSpringBoot:
    """Tests for Spring Boot detection and route extraction."""

    def test_spring_boot_detect(self, tmp_path: Path):
        (tmp_path / "build.gradle").write_text("""
plugins {
    id 'org.springframework.boot' version '3.0.0'
}
""")
        provider = DecoratorRoutesProvider()
        assert provider.detect(tmp_path) is True

    def test_spring_boot_not_detected_without_spring(self, tmp_path: Path):
        (tmp_path / "build.gradle").write_text("plugins { id 'java' }\n")
        provider = DecoratorRoutesProvider()
        assert provider.detect(tmp_path) is False

    def test_spring_boot_route_extraction(self, tmp_path: Path):
        (tmp_path / "build.gradle").write_text("""
    plugins {
        id 'org.springframework.boot' version '3.0.0'
    }
    """)
        controller_java = """
    package com.example;
    
    import org.springframework.web.bind.annotation.*;
    
    @RestController
    @RequestMapping("/api/users")
    public class UserController {
    
        @GetMapping
        public String getUsers() {
            return "users";
        }
    
        @GetMapping("/{id}")
        public String getUser(@PathVariable Long id) {
            return "user:" + id;
        }
    
        @PostMapping
        public String createUser(@RequestBody String data) {
            return "created";
        }
    }
    """
        (tmp_path / "src" / "main" / "java" / "com" / "example" / "UserController.java").parent.mkdir(parents=True)
        (tmp_path / "src" / "main" / "java" / "com" / "example" / "UserController.java").write_text(controller_java)
        provider = DecoratorRoutesProvider()
        assert provider.detect(tmp_path) is True
        provider.load(tmp_path)

        ctx = provider.get_file_context("src/main/java/com/example/UserController.java")
        assert ctx is not None
        assert "spring-boot-route" in ctx.tags
        assert ctx.properties["framework"] == "spring-boot"
        assert "GET" in ctx.properties["http_methods"]
        assert "POST" in ctx.properties["http_methods"]


class TestDecoratorRoutesNestJS:
    """Tests for NestJS detection and route extraction."""

    def test_nestjs_detect(self, tmp_path: Path):
        pkg = {"dependencies": {"@nestjs/core": "^10.0.0"}}
        import json
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        provider = DecoratorRoutesProvider()
        assert provider.detect(tmp_path) is True

    def test_nestjs_not_detected_without_nestjs(self, tmp_path: Path):
        pkg = {"dependencies": {"express": "^4.18.0"}}
        import json
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        provider = DecoratorRoutesProvider()
        assert provider.detect(tmp_path) is False

    def test_nestjs_route_extraction(self, tmp_path: Path):
        pkg = {"dependencies": {"@nestjs/core": "^10.0.0"}}
        import json
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        controller_ts = """
    import { Controller, Get, Post } from '@nestjs/common';
    
    @Controller('users')
    export class UsersController {
        @Get()
        findAll() {
            return 'users';
        }
    
        @Get(':id')
        findOne(@Param('id') id: string) {
            return 'user:' + id;
        }
    
        @Post()
        create() {
            return 'created';
        }
    }
    """
        (tmp_path / "src" / "users" / "users.controller.ts").parent.mkdir(parents=True)
        (tmp_path / "src" / "users" / "users.controller.ts").write_text(controller_ts)
        provider = DecoratorRoutesProvider()
        assert provider.detect(tmp_path) is True
        provider.load(tmp_path)

        ctx = provider.get_file_context("src/users/users.controller.ts")
        assert ctx is not None
        assert "nestjs-route" in ctx.tags
        assert ctx.properties["framework"] == "nestjs"
        assert "GET users" in ctx.properties["routes"]
        assert "GET users/:id" in ctx.properties["routes"]
        assert "POST users" in ctx.properties["routes"]


class TestDecoratorRoutesASPNET:
    """Tests for ASP.NET detection and route extraction."""

    def test_aspnet_detect(self, tmp_path: Path):
        csproj = """<Project Sdk="Microsoft.NET.Sdk.Web">
  <PropertyGroup>
    <TargetFramework>net7.0</TargetFramework>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Microsoft.AspNetCore" Version="7.0.0" />
  </ItemGroup>
</Project>"""
        (tmp_path / "WebApp.csproj").write_text(csproj)
        provider = DecoratorRoutesProvider()
        assert provider.detect(tmp_path) is True

    def test_aspnet_not_detected_without_aspnetcore(self, tmp_path: Path):
        csproj = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net7.0</TargetFramework>
  </PropertyGroup>
</Project>"""
        (tmp_path / "ConsoleApp.csproj").write_text(csproj)
        provider = DecoratorRoutesProvider()
        assert provider.detect(tmp_path) is False

    def test_aspnet_route_extraction(self, tmp_path: Path):
        csproj = """<Project Sdk="Microsoft.NET.Sdk.Web">
      <PropertyGroup>
        <TargetFramework>net7.0</TargetFramework>
      </PropertyGroup>
    </Project>"""
        (tmp_path / "WebApp.csproj").write_text(csproj)
        controller_cs = """
    using Microsoft.AspNetCore.Mvc;
    
    namespace WebApp.Controllers;
    
    [ApiController]
    [Route("api/[controller]")]
    public class UsersController : ControllerBase {
        [HttpGet]
        public IActionResult GetUsers() {
            return Ok(new[] { "user1", "user2" });
        }
    
        [HttpGet("{id}")]
        public IActionResult GetUser(int id) {
            return Ok(new { id });
        }
    
        [HttpPost]
        public IActionResult CreateUser([FromBody] object data) {
            return Created("", new { id = 1 });
        }
    }
    """
        (tmp_path / "Controllers" / "UsersController.cs").parent.mkdir(parents=True)
        (tmp_path / "Controllers" / "UsersController.cs").write_text(controller_cs)
        provider = DecoratorRoutesProvider()
        assert provider.detect(tmp_path) is True
        provider.load(tmp_path)

        ctx = provider.get_file_context("Controllers/UsersController.cs")
        assert ctx is not None
        assert "aspnet-route" in ctx.tags
        assert ctx.properties["framework"] == "aspnet"
        # Note: [controller] placeholder is kept literal; full resolution would need controller name
        assert "GET api/[controller]" in ctx.properties["routes"]
        assert "POST api/[controller]" in ctx.properties["routes"]


class TestDecoratorRoutesDetection:
    """Tests for the detect() method across all frameworks."""

    def test_priority_flask_before_django(self, tmp_path: Path):
        # If both Flask and FastAPI in requirements, first matching config wins
        (tmp_path / "requirements.txt").write_text("flask\nfastapi\n")
        provider = DecoratorRoutesProvider()
        assert provider.detect(tmp_path) is True
        provider.load(tmp_path)
        # Should detect as Flask (first in order)
        ctx = provider.get_file_context("app.py")
        # Flask is first in the config list, so it should be detected

    def test_no_match(self, tmp_path: Path):
        (tmp_path / "requirements.txt").write_text("requests\nurllib3\n")
        provider = DecoratorRoutesProvider()
        assert provider.detect(tmp_path) is False
