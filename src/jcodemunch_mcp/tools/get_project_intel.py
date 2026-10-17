"""Auto-discover and parse non-code knowledge files, cross-reference to indexed code.

Scans a project for structured knowledge (Dockerfiles, CI configs, compose files,
K8s manifests, env templates, Makefiles, package scripts) and returns intelligence
grouped by category with cross-references to code symbols in the index.
"""

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Optional

from ..storage.git_root import is_linked_worktree

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_INTEL_FILES = 200
_MAX_FILE_SIZE = 256 * 1024  # 256 KB
_MAX_ITEMS_PER_CATEGORY = 50
_MAX_CROSS_REFS = 50

_VALID_CATEGORIES = frozenset({"all", "infra", "ci", "config", "deps", "api", "data"})

# Directories to skip during discovery walk (subset of security.py defaults)
_SKIP_DIRS = frozenset({
    "node_modules", "vendor", "venv", ".venv", "__pycache__",
    "dist", "build", ".git", ".tox", ".mypy_cache", "target",
    ".gradle", ".build", "DerivedData", ".next", ".nuxt",
})

# Safe .env template basenames (never read actual .env)
_ENV_TEMPLATE_NAMES = frozenset({
    ".env.example", ".env.template", ".env.sample",
    ".env.development", ".env.test", ".env.local.example",
})

# ---------------------------------------------------------------------------
# YAML helper (graceful fallback)
# ---------------------------------------------------------------------------

def _load_yaml(content: str):
    """Parse YAML, returning None if pyyaml is unavailable or parse fails."""
    try:
        import yaml  # noqa: PLC0415
        return yaml.safe_load(content)
    except ImportError:
        return None
    except Exception:
        logger.debug("YAML parse failed", exc_info=True)
        return None


def _load_yaml_all(content: str) -> list:
    """Parse multi-document YAML, returning [] on failure."""
    try:
        import yaml  # noqa: PLC0415
        return list(yaml.safe_load_all(content))
    except ImportError:
        return []
    except Exception:
        logger.debug("YAML multi-doc parse failed", exc_info=True)
        return []


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------


def _scope_crosses_checkout_boundary(source_root: str, scope: str) -> bool:
    """True when a relative scope enters a linked worktree.

    Ask the same rule `index_folder` uses so old-form submodules stay walkable.
    The target's ancestors must be checked before os.walk starts: otherwise
    setting the walk root directly to a linked checkout bypasses pruning below.
    """
    root = Path(source_root).resolve()
    target = (root / scope).resolve()
    try:
        relative = target.relative_to(root)
    except ValueError:
        return True
    current = root
    for part in relative.parts:
        current = current / part
        if is_linked_worktree(current):
            return True
    return False


def _discover_intel_files(source_root: str, scope: Optional[str] = None) -> dict[str, list[str]]:
    """Walk source_root once and categorize intel-bearing files.

    When ``scope`` is provided (a relative subpath under ``source_root``), the
    walk is restricted to that subtree and rel-dir calculations are taken
    relative to ``scope``. This is what powers per-package intel queries on a
    monorepo: ``get_project_intel(scope_path="packages/api")`` reports the
    package's own Dockerfile / package.json / CI files even when the
    repo-level Dockerfile lives elsewhere.

    Returns {category: [absolute_path, ...]}.
    """
    found: dict[str, list[str]] = {
        "infra": [], "ci": [], "config": [], "deps": [],
    }
    total = 0

    walk_root = source_root
    rel_anchor = source_root
    if scope:
        scoped = os.path.normpath(os.path.join(source_root, scope))
        if os.path.isdir(scoped):
            if _scope_crosses_checkout_boundary(source_root, scope):
                return found
            walk_root = scoped
            rel_anchor = scoped

    for dirpath, dirnames, filenames in os.walk(walk_root, followlinks=False):
        # Prune skipped directories, then stop at nested linked worktrees —
        # exactly the boundary `index_folder` uses. Old-form submodules stay
        # walkable under both .git-file and .git-directory layouts.
        _dpath = Path(dirpath)
        dirnames[:] = [
            d for d in dirnames
            if d not in _SKIP_DIRS
            and not is_linked_worktree(_dpath / d)
        ]

        rel_dir = os.path.relpath(dirpath, rel_anchor).replace("\\", "/")
        if rel_dir == ".":
            rel_dir = ""

        for fname in filenames:
            if total >= _MAX_INTEL_FILES:
                return found

            fpath = os.path.join(dirpath, fname)
            fname_lower = fname.lower()

            # --- infra ---
            if fname_lower == "dockerfile" or fname_lower.startswith("dockerfile.") or fname_lower.endswith(".dockerfile"):
                found["infra"].append(fpath)
                total += 1
                continue
            if fname_lower in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
                found["infra"].append(fpath)
                total += 1
                continue
            # K8s manifests: YAML in k8s/, kubernetes/, manifests/, deploy/, helm/ dirs
            if fname_lower.endswith((".yml", ".yaml")):
                k8s_dirs = ("k8s", "kubernetes", "manifests", "deploy", "helm", "charts")
                parts = rel_dir.split("/") if rel_dir else []
                if any(p.lower() in k8s_dirs for p in parts):
                    found["infra"].append(fpath)
                    total += 1
                    continue

            # --- ci ---
            if rel_dir == ".github/workflows" and fname_lower.endswith((".yml", ".yaml")):
                found["ci"].append(fpath)
                total += 1
                continue
            if fname_lower in (".gitlab-ci.yml", ".gitlab-ci.yaml"):
                found["ci"].append(fpath)
                total += 1
                continue
            if rel_dir == ".circleci" and fname_lower in ("config.yml", "config.yaml"):
                found["ci"].append(fpath)
                total += 1
                continue

            # --- config ---
            if fname in _ENV_TEMPLATE_NAMES:
                found["config"].append(fpath)
                total += 1
                continue

            # --- deps ---
            # When scope is set, "rel_dir == ''" means the package root rather
            # than the repo root — exactly the behaviour we want for
            # per-package intel (each workspace member has its own
            # package.json / pyproject.toml).
            if fname == "package.json" and rel_dir in ("", "."):
                found["deps"].append(fpath)
                total += 1
                continue
            if fname_lower in ("makefile", "gnumakefile"):
                found["deps"].append(fpath)
                total += 1
                continue
            if fname == "pyproject.toml" and rel_dir in ("", "."):
                found["deps"].append(fpath)
                total += 1
                continue

    return found


def _safe_read(fpath: str) -> Optional[str]:
    """Read a file if it's under the size cap."""
    try:
        size = os.path.getsize(fpath)
        if size > _MAX_FILE_SIZE:
            logger.debug("Skipping oversized intel file: %s (%d bytes)", fpath, size)
            return None
        with open(fpath, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception:
        logger.debug("Failed to read intel file: %s", fpath, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Parsers — Dockerfile
# ---------------------------------------------------------------------------

_RE_FROM = re.compile(r"^FROM\s+(\S+)(?:\s+[Aa][Ss]\s+(\S+))?", re.MULTILINE)
_RE_EXPOSE = re.compile(r"^EXPOSE\s+(.+)", re.MULTILINE)
_RE_CMD = re.compile(r"^(?:CMD|ENTRYPOINT)\s+(.+)", re.MULTILINE)
_RE_COPY_SRC = re.compile(r"^COPY\s+(?:--from=\S+\s+)?(?:--\S+\s+)*(\S+)\s+", re.MULTILINE)
_RE_ENV = re.compile(r"^ENV\s+(\S+?)(?:=|\s)", re.MULTILINE)
_RE_ARG = re.compile(r"^ARG\s+(\S+?)(?:=|$)", re.MULTILINE)
_RE_WORKDIR = re.compile(r"^WORKDIR\s+(\S+)", re.MULTILINE)


def _parse_dockerfile(content: str, rel_path: str) -> dict:
    stages = []
    for m in _RE_FROM.finditer(content):
        stages.append({"image": m.group(1), "alias": m.group(2) or ""})

    ports = []
    for m in _RE_EXPOSE.finditer(content):
        ports.extend(m.group(1).split())

    entrypoint = None
    for m in _RE_CMD.finditer(content):
        raw = m.group(1).strip()
        # Parse JSON array form: ["node", "server.js"] -> "node server.js"
        if raw.startswith("["):
            try:
                parts = json.loads(raw)
                entrypoint = " ".join(parts)
            except Exception:
                entrypoint = raw
        else:
            entrypoint = raw

    copy_sources = [m.group(1) for m in _RE_COPY_SRC.finditer(content)
                    if m.group(1) != "."]
    env_vars = [m.group(1) for m in _RE_ENV.finditer(content)]
    args = [m.group(1) for m in _RE_ARG.finditer(content)]
    workdirs = [m.group(1) for m in _RE_WORKDIR.finditer(content)]

    return {
        "file": rel_path,
        "stages": stages,
        "ports": ports,
        "entrypoint": entrypoint,
        "copy_sources": list(dict.fromkeys(copy_sources)),  # dedupe, preserve order
        "env_vars": env_vars,
        "args": args,
        "workdir": workdirs[-1] if workdirs else None,
    }


# ---------------------------------------------------------------------------
# Parsers — docker-compose
# ---------------------------------------------------------------------------

_RE_COMPOSE_SERVICE = re.compile(r"^  (\w[\w.-]+):\s*$", re.MULTILINE)


def _parse_compose(content: str, rel_path: str) -> list[dict]:
    data = _load_yaml(content)
    if isinstance(data, dict) and "services" in data:
        return _parse_compose_from_dict(data, rel_path)
    # Regex fallback
    return _parse_compose_regex(content, rel_path)


def _parse_compose_from_dict(data: dict, rel_path: str) -> list[dict]:
    services = []
    raw_services = data.get("services", {})
    if not isinstance(raw_services, dict):
        return []
    for name, svc in raw_services.items():
        if not isinstance(svc, dict):
            continue
        build = svc.get("build")
        build_ctx = None
        if isinstance(build, str):
            build_ctx = build
        elif isinstance(build, dict):
            build_ctx = build.get("context")

        ports_raw = svc.get("ports", [])
        ports = [str(p) for p in ports_raw] if isinstance(ports_raw, list) else []

        env_raw = svc.get("environment", {})
        env_vars = []
        if isinstance(env_raw, dict):
            env_vars = list(env_raw.keys())
        elif isinstance(env_raw, list):
            env_vars = [e.split("=")[0] for e in env_raw if isinstance(e, str)]

        depends = svc.get("depends_on", [])
        if isinstance(depends, dict):
            depends = list(depends.keys())
        elif not isinstance(depends, list):
            depends = []

        services.append({
            "name": name,
            "image": svc.get("image"),
            "build_context": build_ctx,
            "ports": ports[:10],
            "env_vars": env_vars[:20],
            "depends_on": depends,
        })
    return services[:_MAX_ITEMS_PER_CATEGORY]


def _parse_compose_regex(content: str, rel_path: str) -> list[dict]:
    """Minimal regex fallback for docker-compose without pyyaml."""
    services = []
    in_services = False
    current_name = None

    for line in content.splitlines():
        stripped = line.rstrip()
        if stripped == "services:":
            in_services = True
            continue
        if in_services and not stripped.startswith(" ") and stripped and not stripped.startswith("#"):
            break  # Left services block

        if in_services:
            m = _RE_COMPOSE_SERVICE.match(line)
            if m:
                current_name = m.group(1)
                services.append({
                    "name": current_name,
                    "image": None, "build_context": None,
                    "ports": [], "env_vars": [], "depends_on": [],
                })
                continue

            if current_name and services:
                s = stripped.lstrip()
                if s.startswith("image:"):
                    services[-1]["image"] = s.split(":", 1)[1].strip()
                elif s.startswith("build:"):
                    val = s.split(":", 1)[1].strip()
                    if val:
                        services[-1]["build_context"] = val

    return services[:_MAX_ITEMS_PER_CATEGORY]


# ---------------------------------------------------------------------------
# Parsers — K8s manifests
# ---------------------------------------------------------------------------

def _parse_k8s_manifest(content: str, rel_path: str) -> list[dict]:
    docs = _load_yaml_all(content)
    if docs:
        return _parse_k8s_from_docs(docs, rel_path)
    return _parse_k8s_regex(content, rel_path)


def _parse_k8s_from_docs(docs: list, rel_path: str) -> list[dict]:
    resources = []
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        kind = doc.get("kind")
        if not kind:
            continue
        meta = doc.get("metadata", {}) or {}
        name = meta.get("name", "")
        namespace = meta.get("namespace")

        images = []
        ports = []
        replicas = None

        spec = doc.get("spec", {}) or {}
        replicas = spec.get("replicas")

        # Walk through template -> spec -> containers
        template = spec.get("template", {}) or {}
        pod_spec = template.get("spec", spec) or {}
        containers = pod_spec.get("containers", [])
        if isinstance(containers, list):
            for c in containers:
                if isinstance(c, dict):
                    img = c.get("image")
                    if img:
                        images.append(img)
                    for p in c.get("ports", []):
                        if isinstance(p, dict) and "containerPort" in p:
                            ports.append(p["containerPort"])

        # Kind-specific exposure fields (keys present only when non-empty).
        extra: dict = {}
        if kind == "Service":
            svc_ports = [p["port"] for p in (spec.get("ports") or [])
                         if isinstance(p, dict) and "port" in p]
            if svc_ports:
                ports = svc_ports
            selector = spec.get("selector")
            if isinstance(selector, dict) and selector:
                extra["selector"] = {str(k): str(v) for k, v in list(selector.items())[:10]}
        elif kind == "Ingress":
            rules = []
            for rule in spec.get("rules") or []:
                if not isinstance(rule, dict):
                    continue
                host = rule.get("host")
                for p in (rule.get("http") or {}).get("paths") or []:
                    if not isinstance(p, dict):
                        continue
                    backend = p.get("backend") or {}
                    backend_svc = backend.get("service")
                    svc_name = backend_svc.get("name") if isinstance(backend_svc, dict) else None
                    if not svc_name:
                        svc_name = backend.get("serviceName")  # extensions/v1beta1
                    rules.append({"host": host, "path": p.get("path"), "service": svc_name})
            if rules:
                extra["ingress_rules"] = rules[:20]
        else:
            labels = (template.get("metadata") or {}).get("labels")
            if not (isinstance(labels, dict) and labels):
                labels = meta.get("labels")  # bare Pod
            if isinstance(labels, dict) and labels:
                extra["labels"] = {str(k): str(v) for k, v in list(labels.items())[:10]}

        resources.append({
            "file": rel_path,
            "kind": kind,
            "name": name,
            "namespace": namespace,
            "images": images,
            "ports": ports,
            "replicas": replicas,
            **extra,
        })
    return resources[:_MAX_ITEMS_PER_CATEGORY]


_RE_K8S_KIND = re.compile(r"^kind:\s*(\S+)", re.MULTILINE)
_RE_K8S_NAME = re.compile(r"^\s+name:\s*(\S+)", re.MULTILINE)
_RE_K8S_IMAGE = re.compile(r"^\s+image:\s*(\S+)", re.MULTILINE)


def _parse_k8s_regex(content: str, rel_path: str) -> list[dict]:
    """Minimal regex fallback for K8s manifests."""
    kinds = _RE_K8S_KIND.findall(content)
    names = _RE_K8S_NAME.findall(content)
    images = _RE_K8S_IMAGE.findall(content)
    if not kinds:
        return []
    return [{
        "file": rel_path,
        "kind": kinds[0],
        "name": names[0] if names else "",
        "namespace": None,
        "images": images[:5],
        "ports": [],
        "replicas": None,
    }]


# ---------------------------------------------------------------------------
# Parsers — GitHub Actions
# ---------------------------------------------------------------------------

def _parse_github_actions(content: str, rel_path: str) -> dict:
    data = _load_yaml(content)
    if isinstance(data, dict):
        return _parse_gha_from_dict(data, rel_path)
    return _parse_gha_regex(content, rel_path)


def _parse_gha_from_dict(data: dict, rel_path: str) -> dict:
    name = data.get("name", "")

    triggers_raw = data.get(True, data.get("on", []))  # YAML parses `on:` as True key
    triggers = []
    if isinstance(triggers_raw, str):
        triggers = [triggers_raw]
    elif isinstance(triggers_raw, list):
        triggers = [str(t) for t in triggers_raw]
    elif isinstance(triggers_raw, dict):
        triggers = list(triggers_raw.keys())

    jobs = []
    for job_id, job_def in (data.get("jobs", {}) or {}).items():
        if not isinstance(job_def, dict):
            continue
        runner = job_def.get("runs-on", "")
        if isinstance(runner, list):
            runner = ", ".join(str(r) for r in runner)

        run_cmds = []
        for step in job_def.get("steps", []):
            if isinstance(step, dict) and "run" in step:
                cmd = str(step["run"]).strip()
                # Take first line of multi-line commands
                first_line = cmd.splitlines()[0] if cmd else ""
                if first_line:
                    run_cmds.append(first_line)

        jobs.append({
            "name": job_def.get("name", job_id),
            "id": job_id,
            "runner": str(runner),
            "run_commands": run_cmds[:15],
        })

    return {
        "file": rel_path,
        "name": name,
        "triggers": triggers,
        "jobs": jobs[:_MAX_ITEMS_PER_CATEGORY],
    }


_RE_GHA_NAME = re.compile(r"^name:\s*(.+)", re.MULTILINE)
_RE_GHA_ON = re.compile(r"^on:\s*(.+)", re.MULTILINE)
_RE_GHA_RUN = re.compile(r"^\s+-?\s*run:\s*(.+)", re.MULTILINE)


def _parse_gha_regex(content: str, rel_path: str) -> dict:
    name_m = _RE_GHA_NAME.search(content)
    on_m = _RE_GHA_ON.search(content)
    runs = _RE_GHA_RUN.findall(content)
    jobs = []
    if runs:
        jobs = [{"name": "unknown", "id": "unknown", "runner": "",
                 "run_commands": [r.strip() for r in runs[:15]]}]
    return {
        "file": rel_path,
        "name": name_m.group(1).strip() if name_m else "",
        "triggers": [on_m.group(1).strip()] if on_m else [],
        "jobs": jobs,
    }


# ---------------------------------------------------------------------------
# Parsers — GitLab CI
# ---------------------------------------------------------------------------

_GITLAB_RESERVED = frozenset({
    "stages", "variables", "image", "services", "before_script",
    "after_script", "cache", "default", "include", "workflow",
})


def _parse_gitlab_ci(content: str, rel_path: str) -> dict:
    data = _load_yaml(content)
    if isinstance(data, dict):
        return _parse_gitlab_from_dict(data, rel_path)
    return _parse_gitlab_regex(content, rel_path)


def _parse_gitlab_from_dict(data: dict, rel_path: str) -> dict:
    stages = data.get("stages", [])
    if not isinstance(stages, list):
        stages = []

    jobs = []
    for key, val in data.items():
        if key.startswith(".") or key in _GITLAB_RESERVED or not isinstance(val, dict):
            continue
        scripts = val.get("script", [])
        if isinstance(scripts, str):
            scripts = [scripts]
        elif not isinstance(scripts, list):
            scripts = []
        jobs.append({
            "name": key,
            "stage": val.get("stage"),
            "image": val.get("image"),
            "scripts": [str(s) for s in scripts[:10]],
        })

    return {"file": rel_path, "stages": stages, "jobs": jobs[:_MAX_ITEMS_PER_CATEGORY]}


def _parse_gitlab_regex(content: str, rel_path: str) -> dict:
    stages = []
    jobs = []
    for line in content.splitlines():
        s = line.strip()
        if s.startswith("- ") and "stages:" in content[:content.index(line) if line in content else 0]:
            stages.append(s[2:].strip())
    return {"file": rel_path, "stages": stages, "jobs": jobs}


# ---------------------------------------------------------------------------
# Parsers — CircleCI
# ---------------------------------------------------------------------------

def _parse_circleci(content: str, rel_path: str) -> dict:
    data = _load_yaml(content)
    if isinstance(data, dict):
        jobs = []
        for jname, jdef in (data.get("jobs", {}) or {}).items():
            if not isinstance(jdef, dict):
                continue
            steps = jdef.get("steps", [])
            cmds = []
            for step in steps:
                if isinstance(step, dict) and "run" in step:
                    run_val = step["run"]
                    if isinstance(run_val, str):
                        cmds.append(run_val.splitlines()[0])
                    elif isinstance(run_val, dict):
                        cmd = run_val.get("command", "")
                        if cmd:
                            cmds.append(str(cmd).splitlines()[0])
            jobs.append({
                "name": jname,
                "stage": None,
                "image": None,
                "scripts": cmds[:10],
            })
        return {"file": rel_path, "stages": [], "jobs": jobs[:_MAX_ITEMS_PER_CATEGORY]}
    return {"file": rel_path, "stages": [], "jobs": []}


# ---------------------------------------------------------------------------
# Parsers — .env templates
# ---------------------------------------------------------------------------

_RE_ENV_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)")


def _parse_env_template(content: str, rel_path: str) -> list[dict]:
    vars_list = []
    pending_comment = None

    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            pending_comment = None
            continue
        if stripped.startswith("#"):
            pending_comment = stripped.lstrip("#").strip()
            continue

        m = _RE_ENV_LINE.match(stripped)
        if m:
            name = m.group(1)
            default_val = m.group(2).strip()
            # Strip inline comments
            if " #" in default_val:
                default_val = default_val[:default_val.index(" #")].strip()
            vars_list.append({
                "name": name,
                "default": default_val if default_val else None,
                "comment": pending_comment,
            })
            pending_comment = None

    return vars_list[:_MAX_ITEMS_PER_CATEGORY]


# ---------------------------------------------------------------------------
# Parsers — package.json scripts
# ---------------------------------------------------------------------------

def _parse_package_scripts(content: str, rel_path: str) -> list[dict]:
    try:
        data = json.loads(content)
    except Exception:
        return []
    scripts = data.get("scripts", {})
    if not isinstance(scripts, dict):
        return []
    return [{"name": k, "command": v, "source": rel_path}
            for k, v in scripts.items()
            if isinstance(v, str)][:_MAX_ITEMS_PER_CATEGORY]


# ---------------------------------------------------------------------------
# Parsers — Makefile
# ---------------------------------------------------------------------------

_RE_MAKE_TARGET = re.compile(r"^([a-zA-Z_][\w.-]*)\s*:(.*?)$")
_MAKE_SPECIAL = frozenset({
    ".PHONY", ".DEFAULT", ".SUFFIXES", ".PRECIOUS", ".INTERMEDIATE",
    ".SECONDARY", ".DELETE_ON_ERROR", ".IGNORE", ".SILENT", ".NOTPARALLEL",
})


def _parse_makefile(content: str, rel_path: str) -> list[dict]:
    targets = []
    lines = content.splitlines()
    for i, line in enumerate(lines):
        m = _RE_MAKE_TARGET.match(line)
        if not m:
            continue
        name = m.group(1)
        if name in _MAKE_SPECIAL:
            continue
        prereqs_raw = m.group(2).strip()
        prereqs = [p.strip() for p in prereqs_raw.split() if p.strip()] if prereqs_raw else []

        # Grab first recipe line as hint
        recipe_hint = None
        if i + 1 < len(lines) and lines[i + 1].startswith("\t"):
            recipe_hint = lines[i + 1].strip()

        targets.append({
            "target": name,
            "prerequisites": prereqs[:10],
            "recipe_hint": recipe_hint,
            "source": rel_path,
        })

    return targets[:_MAX_ITEMS_PER_CATEGORY]


# ---------------------------------------------------------------------------
# Parsers — pyproject.toml scripts
# ---------------------------------------------------------------------------

def _parse_pyproject_scripts(content: str, rel_path: str) -> list[dict]:
    # Try tomllib (stdlib 3.11+)
    data = None
    try:
        import tomllib  # noqa: PLC0415
        data = tomllib.loads(content)
    except ImportError:
        try:
            import tomli as tomllib  # noqa: PLC0415
            data = tomllib.loads(content)
        except ImportError:
            pass
    except Exception:
        pass

    if isinstance(data, dict):
        scripts = {}
        # PEP 621
        proj = data.get("project", {})
        if isinstance(proj, dict):
            scripts.update(proj.get("scripts", {}))
        # Poetry
        tool = data.get("tool", {})
        if isinstance(tool, dict):
            poetry = tool.get("poetry", {})
            if isinstance(poetry, dict):
                scripts.update(poetry.get("scripts", {}))
        if scripts:
            return [{"name": k, "entry_point": v, "source": rel_path}
                    for k, v in scripts.items()
                    if isinstance(v, str)][:_MAX_ITEMS_PER_CATEGORY]

    # Regex fallback
    return _parse_pyproject_scripts_regex(content, rel_path)


_RE_TOML_SECTION = re.compile(r"^\[([^\]]+)\]")
_RE_TOML_KV = re.compile(r'^(\w[\w-]*)\s*=\s*"([^"]*)"')


def _parse_pyproject_scripts_regex(content: str, rel_path: str) -> list[dict]:
    scripts = []
    in_scripts = False
    for line in content.splitlines():
        sec_m = _RE_TOML_SECTION.match(line)
        if sec_m:
            section = sec_m.group(1).strip()
            in_scripts = section in ("project.scripts", "tool.poetry.scripts")
            continue
        if in_scripts:
            kv_m = _RE_TOML_KV.match(line.strip())
            if kv_m:
                scripts.append({
                    "name": kv_m.group(1),
                    "entry_point": kv_m.group(2),
                    "source": rel_path,
                })
    return scripts[:_MAX_ITEMS_PER_CATEGORY]


# ---------------------------------------------------------------------------
# Index-backed collectors
# ---------------------------------------------------------------------------

def _collect_api_intel(index) -> dict:
    """Pull API surface data from indexed symbols."""
    endpoints = []
    graphql_types = []
    proto_services = []

    for sym in index.symbols:
        lang = sym.get("language", "")
        if lang == "openapi":
            endpoints.append({
                "name": sym.get("name", ""),
                "kind": sym.get("kind", ""),
                "signature": sym.get("signature", ""),
                "file": sym.get("file", ""),
            })
        elif lang == "graphql":
            graphql_types.append({
                "name": sym.get("name", ""),
                "kind": sym.get("kind", ""),
                "file": sym.get("file", ""),
            })
        elif lang == "proto":
            proto_services.append({
                "name": sym.get("name", ""),
                "kind": sym.get("kind", ""),
                "file": sym.get("file", ""),
            })

    return {
        "endpoints": endpoints[:_MAX_ITEMS_PER_CATEGORY],
        "graphql_types": graphql_types[:_MAX_ITEMS_PER_CATEGORY],
        "proto_services": proto_services[:_MAX_ITEMS_PER_CATEGORY],
    }


def _collect_data_intel(index) -> dict:
    """Pull data layer intelligence from index."""
    # Column metadata from context providers (dbt, SQLMesh, Laravel, etc.)
    columns_count = 0
    models = []
    ctx_meta = getattr(index, "context_metadata", {}) or {}
    for key, val in ctx_meta.items():
        if key.endswith("_columns") and isinstance(val, dict):
            source = key.replace("_columns", "")
            for model_name, cols in val.items():
                col_count = len(cols) if isinstance(cols, dict) else 0
                columns_count += col_count
                models.append({
                    "name": model_name,
                    "source": source,
                    "columns": col_count,
                })

    # Count migration files: a file a migration tool named, in a `migrations/` or
    # `migrate/` directory. A path test alone counted a framework's own
    # `migrations` package (django's autodetector, executor) once it was indexed.
    from ..security import is_generated_migration_name

    def _is_migration_file(path: str) -> bool:
        parts = path.replace("\\", "/").split("/")
        return (
            len(parts) >= 2
            and parts[-2] in ("migrations", "migrate")
            and is_generated_migration_name(parts[-1])
        )

    migration_files = sum(1 for f in index.source_files if _is_migration_file(f))

    return {
        "models": models[:_MAX_ITEMS_PER_CATEGORY],
        "columns_count": columns_count,
        "migration_files": migration_files,
    }


def _collect_infra_from_index(index) -> dict:
    """Pull Terraform/HCL data from indexed symbols."""
    resources = []
    for sym in index.symbols:
        if sym.get("language") == "hcl":
            resources.append({
                "name": sym.get("name", ""),
                "kind": sym.get("kind", ""),
                "file": sym.get("file", ""),
            })
    return {"terraform": resources[:_MAX_ITEMS_PER_CATEGORY]}


# ---------------------------------------------------------------------------
# Cross-referencing
# ---------------------------------------------------------------------------

def _extract_file_tokens(cmd: str) -> list[str]:
    """Extract tokens from a command string that look like file paths."""
    tokens = []
    for token in cmd.split():
        # Strip quotes
        token = token.strip("'\"")
        # Looks like a file path if it has a slash or known extension
        if "/" in token or any(token.endswith(ext) for ext in (
            ".py", ".js", ".ts", ".go", ".rs", ".java", ".rb", ".php",
            ".sh", ".bash", ".yaml", ".yml", ".json", ".toml",
        )):
            tokens.append(token)
    return tokens


def _fuzzy_path_match(token: str, source_file_set: set[str]) -> Optional[str]:
    """Try to find a source file matching the token by suffix."""
    token_norm = token.replace("\\", "/").lstrip("./")
    # Exact match first
    if token_norm in source_file_set:
        return token_norm
    # Suffix match
    for sf in source_file_set:
        if sf.endswith("/" + token_norm) or sf == token_norm:
            return sf
    return None


def _build_cross_references(discoveries: dict, index) -> list[dict]:
    """Link discovered intel to indexed source files."""
    cross_refs: list[dict] = []
    source_file_set = set(index.source_files)

    # 1. Dockerfile ENTRYPOINT/CMD -> source files
    for df in discoveries.get("infra", {}).get("dockerfiles", []):
        ep = df.get("entrypoint", "") or ""
        for token in _extract_file_tokens(ep):
            match = _fuzzy_path_match(token, source_file_set)
            if match:
                cross_refs.append({
                    "source": f"{df['file']}:ENTRYPOINT",
                    "target_file": match,
                    "type": "entrypoint",
                })

        # COPY sources -> source dirs
        for src in df.get("copy_sources", []):
            src_norm = src.rstrip("/").replace("\\", "/")
            matched = sum(1 for f in source_file_set
                         if f.startswith(src_norm + "/") or f == src_norm)
            if matched:
                cross_refs.append({
                    "source": f"{df['file']}:COPY",
                    "target_file": src_norm,
                    "type": "copy_source",
                    "matched_files": matched,
                })

    # 2. docker-compose build contexts -> source dirs
    for svc in discoveries.get("infra", {}).get("compose_services", []):
        ctx = svc.get("build_context")
        if not ctx or ctx == ".":
            continue
        ctx_norm = ctx.rstrip("/").replace("\\", "/").lstrip("./")
        matched = sum(1 for f in source_file_set if f.startswith(ctx_norm + "/"))
        if matched:
            cross_refs.append({
                "source": f"compose:{svc['name']}",
                "target_file": ctx_norm,
                "type": "build_context",
                "matched_files": matched,
            })

    # 3. .env var names -> symbol keywords
    env_var_names = set()
    for ev in discoveries.get("config", {}).get("env_vars", []):
        env_var_names.add(ev["name"])

    if env_var_names:
        seen_pairs: set[tuple[str, str]] = set()
        for sym in index.symbols:
            kw_set = set(sym.get("keywords", []))
            overlap = env_var_names & kw_set
            for var in overlap:
                pair = (var, sym["file"])
                if pair not in seen_pairs:
                    seen_pairs.add(pair)
                    cross_refs.append({
                        "source": f"env:{var}",
                        "target_file": sym["file"],
                        "type": "env_usage",
                    })
                    if len(cross_refs) >= _MAX_CROSS_REFS:
                        return cross_refs

    # 4. CI run commands -> source files
    for pipeline in discoveries.get("ci", {}).get("pipelines", []):
        for job in pipeline.get("jobs", []):
            for cmd in job.get("run_commands", job.get("scripts", [])):
                for token in _extract_file_tokens(str(cmd)):
                    match = _fuzzy_path_match(token, source_file_set)
                    if match:
                        cross_refs.append({
                            "source": f"ci:{pipeline['file']}:{job.get('name', job.get('id', ''))}",
                            "target_file": match,
                            "type": "ci_target",
                        })

    # 5. Makefile/script targets -> referenced files
    for script in discoveries.get("deps", {}).get("scripts", []):
        hint = script.get("recipe_hint") or script.get("command", "")
        for token in _extract_file_tokens(str(hint)):
            match = _fuzzy_path_match(token, source_file_set)
            if match:
                cross_refs.append({
                    "source": f"script:{script.get('target', script.get('name', ''))}",
                    "target_file": match,
                    "type": "script_target",
                })

    return cross_refs[:_MAX_CROSS_REFS]


# ---------------------------------------------------------------------------
# Internal entry point (already-loaded index)
# ---------------------------------------------------------------------------

def collect_project_intel(
    index,
    source_root: str,
    *,
    cats: Optional[list] = None,
    scope_path: Optional[str] = None,
) -> dict:
    """Discovery + parse + cross-reference over an ALREADY-LOADED index.

    Pure over ``(index, source_root, cats, scope_path)``; no repo resolution,
    no IndexStore load. Callers that already hold an index (get_endpoint_impact)
    reuse it instead of paying a second load. Returns::

        {categories, discoveries, cross_references, discovered_files,
         file_count, category_count}

    Defensive: a missing/non-existent source_root yields empty results
    (empty categories, [] cross_references) rather than raising, so an
    infra-fusion caller degrades to honest-empty.
    """
    if cats is None:
        cats = list(_VALID_CATEGORIES - {"all"})

    if not source_root or not os.path.isdir(source_root):
        return {
            "categories": {}, "discoveries": {}, "cross_references": [],
            "discovered_files": {}, "file_count": 0, "category_count": 0,
        }

    # Phase 1: Discover intel files from filesystem
    fs_cats = {"infra", "ci", "config", "deps"}
    need_fs = bool(fs_cats & set(cats))
    discovered_files = (
        _discover_intel_files(source_root, scope=scope_path) if need_fs else {}
    )

    # Phase 2: Parse discovered files
    result_categories: dict = {}

    if "infra" in cats:
        dockerfiles = []
        compose_services = []
        k8s_resources = []

        for fpath in discovered_files.get("infra", []):
            content = _safe_read(fpath)
            if content is None:
                continue
            rel = os.path.relpath(fpath, source_root).replace("\\", "/")
            fname = os.path.basename(fpath).lower()

            if fname == "dockerfile" or fname.startswith("dockerfile.") or fname.endswith(".dockerfile"):
                dockerfiles.append(_parse_dockerfile(content, rel))
            elif fname in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
                compose_services.extend(_parse_compose(content, rel))
            else:
                # Potential K8s manifest
                resources = _parse_k8s_manifest(content, rel)
                k8s_resources.extend(resources)

        # Also pull Terraform from index
        tf_data = _collect_infra_from_index(index)

        result_categories["infra"] = {
            "dockerfiles": dockerfiles,
            "compose_services": compose_services,
            "k8s_resources": k8s_resources[:_MAX_ITEMS_PER_CATEGORY],
            "terraform": tf_data.get("terraform", []),
        }

    if "ci" in cats:
        pipelines = []
        for fpath in discovered_files.get("ci", []):
            content = _safe_read(fpath)
            if content is None:
                continue
            rel = os.path.relpath(fpath, source_root).replace("\\", "/")

            if ".github/workflows" in rel.replace("\\", "/"):
                pipelines.append(_parse_github_actions(content, rel))
            elif ".gitlab-ci" in os.path.basename(fpath).lower():
                pipelines.append(_parse_gitlab_ci(content, rel))
            elif ".circleci" in rel.replace("\\", "/"):
                pipelines.append(_parse_circleci(content, rel))

        result_categories["ci"] = {"pipelines": pipelines}

    if "config" in cats:
        env_vars: list = []
        for fpath in discovered_files.get("config", []):
            content = _safe_read(fpath)
            if content is None:
                continue
            rel = os.path.relpath(fpath, source_root).replace("\\", "/")
            parsed = _parse_env_template(content, rel)
            for v in parsed:
                v["source"] = rel
            env_vars.extend(parsed)

        result_categories["config"] = {"env_vars": env_vars[:_MAX_ITEMS_PER_CATEGORY]}

    if "deps" in cats:
        scripts: list = []
        for fpath in discovered_files.get("deps", []):
            content = _safe_read(fpath)
            if content is None:
                continue
            rel = os.path.relpath(fpath, source_root).replace("\\", "/")
            fname = os.path.basename(fpath).lower()

            if fname == "package.json":
                scripts.extend(_parse_package_scripts(content, rel))
            elif fname in ("makefile", "gnumakefile"):
                scripts.extend(_parse_makefile(content, rel))
            elif fname == "pyproject.toml":
                scripts.extend(_parse_pyproject_scripts(content, rel))

        result_categories["deps"] = {"scripts": scripts[:_MAX_ITEMS_PER_CATEGORY]}

    if "api" in cats:
        result_categories["api"] = _collect_api_intel(index)

    if "data" in cats:
        result_categories["data"] = _collect_data_intel(index)

    # Phase 3: Cross-reference
    # Build a unified discoveries dict for cross-ref builder
    discoveries = {}
    if "infra" in result_categories:
        discoveries["infra"] = result_categories["infra"]
    if "ci" in result_categories:
        discoveries["ci"] = result_categories["ci"]
    if "config" in result_categories:
        discoveries["config"] = result_categories["config"]
    if "deps" in result_categories:
        discoveries["deps"] = result_categories["deps"]

    cross_refs = _build_cross_references(discoveries, index) if discoveries else []

    # Count categories with actual data
    nonempty = 0
    for cat_data in result_categories.values():
        if isinstance(cat_data, dict):
            for v in cat_data.values():
                if isinstance(v, list) and v:
                    nonempty += 1
                    break
                if isinstance(v, int) and v > 0:
                    nonempty += 1
                    break

    total_files = sum(len(files) for files in discovered_files.values())

    return {
        "categories": result_categories,
        "discoveries": discoveries,
        "cross_references": cross_refs,
        "discovered_files": discovered_files,
        "file_count": total_files,
        "category_count": nonempty,
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def get_project_intel(
    repo: str,
    category: str = "all",
    scope_path: Optional[str] = None,
    storage_path: Optional[str] = None,
) -> dict:
    """Auto-discover and parse non-code knowledge files, cross-reference to code.

    ``scope_path`` (optional) restricts file discovery to a subpath of
    ``source_root`` — typically a workspace member like ``packages/api``.
    Use ``list_workspaces`` to enumerate the available members. Repo-wide
    discovery (the default) reads every Dockerfile / CI workflow / .env
    template in the tree; scoped discovery reads only those inside the
    specified subtree. The cross-reference pass still consults the global
    index so a package's container still resolves against repo-level code.
    """
    t0 = time.monotonic()

    if category not in _VALID_CATEGORIES:
        return {"error": f"Invalid category '{category}'. Valid: {', '.join(sorted(_VALID_CATEGORIES))}"}

    # Resolve repo and load index
    from ._utils import resolve_repo as _resolve  # noqa: PLC0415
    from ..storage import IndexStore  # noqa: PLC0415

    try:
        owner, name = _resolve(repo, storage_path)
    except ValueError as e:
        return {"error": str(e)}

    store = IndexStore(base_path=storage_path)
    index = store.load_index(owner, name)
    if index is None:
        return {"error": f"No index found for {repo}"}

    source_root = getattr(index, "source_root", "")
    if not source_root or not os.path.isdir(source_root):
        return {
            "error": "get_project_intel requires a local index with source_root. "
                     "Use index_folder (not index_repo) to create one."
        }

    # Validate scope_path: must be a relative path under source_root.
    if scope_path:
        # Reject absolute paths and traversal up out of source_root
        if os.path.isabs(scope_path):
            try:
                scope_path = os.path.relpath(scope_path, source_root)
            except ValueError:
                return {"error": f"scope_path {scope_path!r} is not under source_root"}
        normalised = os.path.normpath(scope_path).replace("\\", "/").strip("/")
        if normalised.startswith("..") or "/.." in normalised:
            return {"error": f"scope_path {scope_path!r} escapes source_root"}
        scoped_abs = os.path.normpath(os.path.join(source_root, normalised))
        if not os.path.isdir(scoped_abs):
            return {"error": f"scope_path {scope_path!r} is not a directory under source_root"}
        if _scope_crosses_checkout_boundary(source_root, normalised):
            return {
                "error": f"scope_path {scope_path!r} crosses a linked worktree boundary"
            }
        scope_path = normalised
    else:
        scope_path = None

    # Determine which categories to process, then delegate to the internal
    # entry point over the already-loaded index.
    cats = list(_VALID_CATEGORIES - {"all"}) if category == "all" else [category]
    collected = collect_project_intel(index, source_root, cats=cats, scope_path=scope_path)

    elapsed_ms = int((time.monotonic() - t0) * 1000)

    response: dict = {
        "repo": f"{owner}/{name}",
        "categories": collected["categories"],
        "cross_references": collected["cross_references"],
        "file_count": collected["file_count"],
        "category_count": collected["category_count"],
        "_meta": {
            "timing_ms": elapsed_ms,
            "source_root": source_root,
            "categories_requested": category,
        },
    }
    if scope_path:
        response["scope_path"] = scope_path
        response["_meta"]["scope_path"] = scope_path
    return response
