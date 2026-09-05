"""The container sandbox every measured tool runs in (docs/competitive/DESIGN.md D2).

purpose:  build a pinned image with the network ON, then run the tool with
          the network OFF, a read-only corpus, one writable /out, no
          capabilities, no new privileges, an unprivileged user, a memory
          and pid ceiling and a wall-clock timeout; record the image digest
          so a rebuild that produces a different image is a finding
invokes:  the docker CLI (`docker build`, `docker run`, `docker image
          inspect`); nothing else
produces: BuildResult(digest, seconds), RunResult(rc, stdout, stderr,
          seconds, timed_out)
refuses:  to run with the network on; to mount anything but the corpus
          (read-only) and /out; to pass any environment variable through
          from the host (the container sees HOME=/out and PATH only)
pinned:   the Dockerfile under sandbox/<tool>.Dockerfile names the base
          image by digest and the tool by version and checksum
fairness: identical flags for every tool including jcodemunch, so the
          sandbox's cost is paid on every row (DESIGN D2)
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

RUN_FLAGS = [
    "--network", "none",
    "--read-only",
    "--cap-drop", "ALL",
    "--security-opt", "no-new-privileges",
    "--user", "65534:65534",
    "--memory", "8g",
    "--pids-limit", "512",
    "--tmpfs", "/tmp:rw,size=512m",
]


@dataclass
class BuildResult:
    tag: str
    digest: str
    seconds: float
    dockerfile_sha256: str


@dataclass
class RunResult:
    rc: int
    stdout: str
    stderr: str
    seconds: float
    timed_out: bool = False


def docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        p = subprocess.run(["docker", "info", "--format", "{{.OSType}}"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        return p.returncode == 0 and p.stdout.strip() == "linux"
    except (OSError, subprocess.TimeoutExpired):
        return False


def _mount_path(p: Path) -> str:
    """Docker on Windows wants forward slashes for bind sources."""
    return str(p.resolve()).replace("\\", "/")


def build(tag: str, dockerfile: Path, context: Path, timeout: int = 600) -> BuildResult:
    t0 = time.perf_counter()
    proc = subprocess.run(
        ["docker", "build", "-q", "-t", tag, "-f", str(dockerfile), str(context)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )
    secs = time.perf_counter() - t0
    if proc.returncode != 0:
        raise RuntimeError(f"docker build {tag} failed (rc {proc.returncode}):\n{proc.stderr[-3000:]}")
    ins = subprocess.run(["docker", "image", "inspect", tag, "--format", "{{.Id}}"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    digest = ins.stdout.strip()
    return BuildResult(tag=tag, digest=digest, seconds=round(secs, 1), dockerfile_sha256=hashlib.sha256(dockerfile.read_bytes()).hexdigest())


def run(tag: str, args: list[str], corpus: Path, out: Path, timeout: int, workdir: str = "/corpus",
        extra_env: Optional[dict[str, str]] = None) -> RunResult:
    """One container. `args` follow the image's ENTRYPOINT. Only HOME and PATH
    reach the tool, plus `extra_env` (which an adapter may use for its own
    documented knobs; never a host variable)."""
    out.mkdir(parents=True, exist_ok=True)
    cmd = ["docker", "run", "--rm", *RUN_FLAGS,
           "-v", f"{_mount_path(corpus)}:/corpus:ro",
           "-v", f"{_mount_path(out)}:/out:rw",
           "-w", workdir, "-e", "HOME=/out"]
    for k, v in (extra_env or {}).items():
        cmd += ["-e", f"{k}={v}"]
    cmd += [tag, *args]
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired as e:
        return RunResult(rc=124, stdout=(e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or ""),
                         stderr="timeout", seconds=round(time.perf_counter() - t0, 1), timed_out=True)
    return RunResult(rc=proc.returncode, stdout=proc.stdout, stderr=proc.stderr, seconds=round(time.perf_counter() - t0, 1))
