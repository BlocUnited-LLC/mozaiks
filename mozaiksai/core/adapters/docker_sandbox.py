"""Docker-based SandboxPort implementation for local development.

Uses the Docker CLI (must be installed and running) to spin up short-lived
containers. Provides preview URLs via host-port mapping so Studio can embed
the generated app in an iframe without requiring E2B credentials.

Resolution order (see app_validation_strategy.py):
  e2b    -> E2BSandboxAdapter  (requires E2B_API_KEY)
  docker -> DockerSandboxAdapter (requires Docker daemon)
  local  -> subprocess on host (npm must be available, no preview URL)
  skip   -> no validation
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import tempfile
from pathlib import Path, PurePosixPath

_ENV_KEY_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$", re.IGNORECASE)
from typing import Any

from logs.logging_config import get_core_logger
from mozaiksai.core.ports.sandbox import SandboxRunResult, SandboxSessionInfo

logger = get_core_logger("docker_sandbox")

# Default image — Node 20 LTS on Alpine, mirrors the E2B template capabilities.
_DEFAULT_IMAGE = os.getenv("DOCKER_SANDBOX_IMAGE", "node:20-alpine")
_DEFAULT_WORKDIR = "/workspace"
_DEFAULT_TIMEOUT_SECONDS = int(os.getenv("DOCKER_SANDBOX_TIMEOUT", "300"))


def _preview_ports() -> list[int]:
    """Container ports published at create time for preview URLs.

    The configured preview port (SANDBOX_PREVIEW_PORT, default 3000 for node
    dev servers) plus 8000 for python backends, deduplicated.
    """
    preview_port = int(os.getenv("SANDBOX_PREVIEW_PORT", "3000"))
    ports = [preview_port]
    if 8000 not in ports:
        ports.append(8000)
    return ports


def docker_available() -> bool:
    """Return True if the Docker CLI is installed and the daemon is reachable."""
    if not shutil.which("docker"):
        return False
    try:
        import subprocess
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


class DockerSandboxAdapter:
    """Async SandboxPort implementation backed by the local Docker daemon."""

    def __init__(
        self,
        *,
        image: str | None = None,
        default_timeout_seconds: int | None = None,
    ) -> None:
        self._image = image or _DEFAULT_IMAGE
        self._timeout = default_timeout_seconds or _DEFAULT_TIMEOUT_SECONDS

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def _run(args: list[str], *, timeout: float = 30.0) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError as exc:
            proc.kill()
            await proc.communicate()
            raise RuntimeError(f"Docker command timed out after {timeout}s: {' '.join(args)}") from exc
        rc = int(proc.returncode or 0)
        return rc, stdout_b.decode("utf-8", errors="replace"), stderr_b.decode("utf-8", errors="replace")

    # ------------------------------------------------------------------
    # SandboxPort implementation
    # ------------------------------------------------------------------

    async def create_session(
        self,
        *,
        template: str | None = None,
        timeout_seconds: int | None = None,
        metadata: dict[str, str] | None = None,
        envs: dict[str, str] | None = None,
    ) -> SandboxSessionInfo:
        image = template or self._image
        env_args: list[str] = []
        for key, val in (envs or {}).items():
            env_args += ["-e", f"{key}={val}"]

        # Publish the preview ports with random host bindings so
        # get_preview_url's `docker port` lookup can resolve a URL. Without
        # -p at create time no binding ever exists and docker previews are
        # structurally dead.
        port_args: list[str] = []
        for container_port in _preview_ports():
            port_args += ["-p", f"127.0.0.1:0:{container_port}"]

        # Run a long-lived idle container so we can exec into it
        rc, stdout, stderr = await self._run([
            "docker", "run", "-d", "--rm",
            "-w", _DEFAULT_WORKDIR,
            *env_args,
            *port_args,
            image,
            "sh", "-c", f"sleep {timeout_seconds or self._timeout}",
        ])
        if rc != 0:
            raise RuntimeError(f"docker run failed: {stderr.strip()}")
        session_id = stdout.strip()
        logger.info("docker_sandbox.created container_id=%s image=%s", session_id[:12], image)
        return SandboxSessionInfo(
            session_id=session_id,
            provider="docker",
            metadata={**(metadata or {}), "image": image},
        )

    async def connect(
        self,
        *,
        session_id: str,
        timeout_seconds: int | None = None,
    ) -> SandboxSessionInfo:
        rc, stdout, _ = await self._run(
            ["docker", "inspect", "--format", "{{.Config.Image}}", session_id]
        )
        image = stdout.strip() if rc == 0 else self._image
        return SandboxSessionInfo(
            session_id=session_id,
            provider="docker",
            metadata={"image": image},
        )

    async def write_files(
        self,
        *,
        session_id: str,
        files: dict[str, str | bytes],
        cwd: str | None = None,
    ) -> dict[str, Any]:
        base = cwd or _DEFAULT_WORKDIR
        written: list[str] = []

        with tempfile.TemporaryDirectory(prefix="mozaiks-docker-staging-") as staging:
            staging_root = Path(staging)
            for rel_path, content in files.items():
                safe = _safe_relpath(rel_path)
                if not safe:
                    continue
                dest = staging_root / safe
                dest.parent.mkdir(parents=True, exist_ok=True)
                if isinstance(content, bytes):
                    dest.write_bytes(content)
                else:
                    dest.write_text(str(content), encoding="utf-8")
                written.append(safe)

            if written:
                # Ensure workdir exists inside container
                await self._run(
                    ["docker", "exec", session_id, "mkdir", "-p", base],
                    timeout=10.0,
                )
                # Copy the staging dir tree into the container
                rc, _, stderr = await self._run(
                    ["docker", "cp", f"{staging}/.", f"{session_id}:{base}"],
                    timeout=30.0,
                )
                if rc != 0:
                    raise RuntimeError(f"docker cp failed: {stderr.strip()}")

        return {"written": written, "count": len(written)}

    async def read_file(
        self,
        *,
        session_id: str,
        path: str,
        as_bytes: bool = False,
    ) -> str | bytes:
        container_path = path if path.startswith("/") else f"{_DEFAULT_WORKDIR}/{path}"
        rc, stdout, stderr = await self._run(
            ["docker", "exec", session_id, "cat", container_path],
            timeout=15.0,
        )
        if rc != 0:
            raise FileNotFoundError(f"docker exec cat {container_path}: {stderr.strip()}")
        return stdout.encode("utf-8") if as_bytes else stdout

    async def run_command(
        self,
        *,
        session_id: str,
        command: str,
        cwd: str | None = None,
        envs: dict[str, str] | None = None,
        background: bool = False,
        timeout_seconds: float | None = 60.0,
    ) -> SandboxRunResult:
        workdir = cwd or _DEFAULT_WORKDIR
        env_args: list[str] = []
        for key, val in (envs or {}).items():
            if not _ENV_KEY_RE.match(key):
                logger.warning("Skipping env var with invalid key name: %r", key)
                continue
            env_args += ["-e", f"{key}={val}"]

        if background:
            # Fire-and-forget — wrap in sh -c with nohup
            proc = await asyncio.create_subprocess_exec(
                "docker", "exec", "-d",
                *env_args,
                "-w", workdir,
                session_id,
                "sh", "-c", command,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            return SandboxRunResult(success=True)

        try:
            rc, stdout, stderr = await self._run(
                [
                    "docker", "exec",
                    *env_args,
                    "-w", workdir,
                    session_id,
                    "sh", "-c", command,
                ],
                timeout=float(timeout_seconds or 60.0),
            )
            return SandboxRunResult(
                success=rc == 0,
                exit_code=rc,
                stdout=stdout,
                stderr=stderr,
            )
        except Exception as exc:
            logger.error("Docker sandbox run_command failed session=%s: %s", session_id, exc, exc_info=True)
            return SandboxRunResult(
                success=False,
                exit_code=1,
                error="sandbox_error",
            )

    async def get_preview_url(
        self,
        *,
        session_id: str,
        port: int,
    ) -> str | None:
        """Return the mapped host URL for the given container port.

        The container must have been started with a port binding.  If no
        binding exists the method returns None — callers should warn rather
        than error.
        """
        rc, stdout, _ = await self._run(
            ["docker", "port", session_id, str(port)],
            timeout=10.0,
        )
        if rc != 0 or not stdout.strip():
            return None
        # stdout is "0.0.0.0:HOST_PORT" or ":::HOST_PORT"
        host_port = stdout.strip().split(":")[-1]
        return f"http://localhost:{host_port}"

    async def extend_session(
        self,
        *,
        session_id: str,
        timeout_seconds: int,
    ) -> SandboxSessionInfo:
        # Docker containers have a fixed sleep duration; restart is not feasible
        # without recreating the container.  We return current info and warn.
        logger.warning(
            "docker_sandbox: extend_session not supported for container %s", session_id[:12]
        )
        return SandboxSessionInfo(session_id=session_id, provider="docker")

    async def terminate_session(self, *, session_id: str) -> bool:
        rc, _, _ = await self._run(
            ["docker", "stop", session_id],
            timeout=15.0,
        )
        return rc == 0

    def capabilities(self) -> dict[str, Any]:
        return {
            "provider": "docker",
            "supports_preview": True,
            "supports_file_io": True,
            "supports_command_execution": True,
            "supports_timeout_extension": False,
        }


def _safe_relpath(raw: str) -> str | None:
    path = raw.replace("\\", "/").strip()
    if not path or path.startswith("/"):
        return None
    p = PurePosixPath(path)
    if any(part == ".." for part in p.parts):
        return None
    return str(p)


_docker_adapter: DockerSandboxAdapter | None = None


def get_docker_sandbox() -> DockerSandboxAdapter:
    global _docker_adapter
    if _docker_adapter is None:
        _docker_adapter = DockerSandboxAdapter()
    return _docker_adapter


__all__ = ["DockerSandboxAdapter", "docker_available", "get_docker_sandbox"]
