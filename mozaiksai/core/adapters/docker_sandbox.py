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
import io
import os
import re
import shutil
import tarfile
import tempfile
from pathlib import PurePosixPath

_ENV_KEY_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$", re.IGNORECASE)
from typing import Any

from logs.logging_config import get_core_logger
from mozaiksai.core.ports.sandbox import SandboxRunResult, SandboxSessionInfo

from .local_docker_cli import _docker_cli_env, _docker_prefix

logger = get_core_logger("docker_sandbox")

_DEFAULT_IMAGE = os.getenv("DOCKER_SANDBOX_IMAGE", "mozaiks-sandbox:local")
_DEFAULT_WORKDIR = "/workspace"
_DEFAULT_TIMEOUT_SECONDS = int(os.getenv("DOCKER_SANDBOX_TIMEOUT") or "300")
_IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def _preview_ports() -> list[int]:
    """Container ports published at create time for preview URLs.

    The canonical frontend/backend ports plus an optional custom preview port.
    """
    preview_port = int(os.getenv("SANDBOX_PREVIEW_PORT", "3000"))
    if not 1 <= preview_port <= 65535:
        raise ValueError("SANDBOX_PREVIEW_PORT must be a valid TCP port")
    return list(dict.fromkeys([preview_port, 3000, 8000]))


def docker_available() -> bool:
    """Return True only for the local Docker daemon used by this adapter."""
    if not shutil.which("docker"):
        return False
    try:
        import subprocess
        with tempfile.TemporaryDirectory(prefix="mozaiks-docker-cli-") as config_dir:
            result = subprocess.run(
                [*_docker_prefix(config_dir), "info"],
                capture_output=True,
                timeout=5,
                env=_docker_cli_env(),
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
    async def _run(args: list[str], *, timeout: float = 30.0, input_data: bytes | None = None) -> tuple[int, str, str]:
        if not args or args[0] != "docker":
            raise ValueError("Docker adapter accepts Docker CLI commands only")
        with tempfile.TemporaryDirectory(prefix="mozaiks-docker-cli-") as config_dir:
            proc = await asyncio.create_subprocess_exec(
                *_docker_prefix(config_dir), *args[1:],
                stdin=asyncio.subprocess.PIPE if input_data is not None else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_docker_cli_env(),
            )
            try:
                stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(input_data), timeout=timeout)
            except TimeoutError as exc:
                proc.kill()
                await proc.communicate()
                raise RuntimeError(f"Docker command timed out after {timeout}s") from exc
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
            if not _ENV_KEY_RE.fullmatch(key):
                raise ValueError("Invalid sandbox environment variable name")
            env_args += ["-e", f"{key}={val}"]
        label_args: list[str] = ["--label", "mozaiks.sandbox=true"]
        if metadata and metadata.get("manager_sandbox_id"):
            label_args += ["--label", f"mozaiks.preview={metadata['manager_sandbox_id']}"]

        # Publish the preview ports with random host bindings so
        # get_preview_url's `docker port` lookup can resolve a URL. Without
        # -p at create time no binding ever exists and docker previews are
        # structurally dead.
        sealed_candidate = bool(metadata and metadata.get("purpose") == "sealed_candidate_preview")
        offline_validation = bool(metadata and metadata.get("purpose") == "app_validation") or sealed_candidate
        if sealed_candidate:
            if not _IMAGE_ID_RE.fullmatch(image) or envs:
                raise ValueError("Sealed preview requires a pinned local image ID and no environment overrides")
            inspected, _, _ = await self._run(["docker", "image", "inspect", image], timeout=10)
            if inspected != 0:
                raise RuntimeError("Pinned sealed preview image is unavailable locally")
            label_args += ["--label", "mozaiks.preview.sealed=true"]
        port_args: list[str] = []
        if not offline_validation:
            for container_port in _preview_ports():
                port_args += ["-p", f"127.0.0.1:0:{container_port}"]

        # Run a long-lived idle container so we can exec into it
        rc, stdout, stderr = await self._run([
            "docker", "run", "-d", "--rm",
            "--init", "--cap-drop=ALL", "--security-opt=no-new-privileges",
            "--pids-limit=512", "--memory=2g", "--cpus=2",
            *(["--pull=never", "--read-only", "--user=10001:10001",
               "--memory-swap=2g", "--log-driver=none",
               "--tmpfs", "/workspace:rw,nosuid,nodev,size=128m,uid=0,gid=10001,mode=0750",
               "--tmpfs", "/workspace/logs:rw,nosuid,nodev,size=16m,uid=10001,gid=10001,mode=0700",
               "--tmpfs", "/tmp:rw,nosuid,nodev,size=256m,uid=10001,gid=10001,mode=0700",
               "--tmpfs", "/opt/mozaiks/web_shell/node_modules/.vite-temp:rw,nosuid,nodev,size=16m,uid=10001,gid=10001,mode=0700",
               "--tmpfs", "/opt/mozaiks/web_shell/node_modules/.vite:rw,nosuid,nodev,size=64m,uid=10001,gid=10001,mode=0700"]
              if sealed_candidate else []),
            *(["--network", "none"] if offline_validation else []),
            "-w", _DEFAULT_WORKDIR,
            *label_args,
            *env_args,
            *port_args,
            image,
            "sh", "-c", f"sleep {timeout_seconds or self._timeout}",
        ])
        if rc != 0:
            raise RuntimeError("Docker sandbox creation failed; check Docker and build the configured preview image")
        session_id = stdout.strip()
        logger.info("docker_sandbox.created container_id=%s image=%s", session_id[:12], image)
        return SandboxSessionInfo(
            session_id=session_id,
            provider="docker",
            metadata={**(metadata or {}), "image": image},
        )

    async def stage_sealed_files(
        self, *, session_id: str, files: dict[str, bytes],
    ) -> None:
        """Stage verified files as root; the preview app UID can read but not edit them."""
        rc, marker, _ = await self._run(
            ["docker", "inspect", "--format", "{{index .Config.Labels \"mozaiks.preview.sealed\"}}", session_id],
            timeout=10,
        )
        if rc != 0 or marker.strip() != "true":
            raise RuntimeError("Target is not a sealed preview session")
        archive_bytes = io.BytesIO()
        with tarfile.open(fileobj=archive_bytes, mode="w") as archive:
            for rel_path, content in files.items():
                if _safe_relpath(rel_path) != rel_path or (
                    rel_path != "requirements.txt" and not rel_path.startswith(("app/", "workflows/"))
                ):
                    raise ValueError("Invalid sealed preview file path")
                member = tarfile.TarInfo(rel_path)
                member.size = len(content)
                member.mode = 0o444
                archive.addfile(member, io.BytesIO(content))
        chmod_args = ["docker", "exec", "-u", "0", session_id, "chmod", "-R", "a-w", "/workspace/app", "/workspace/workflows"]
        if "requirements.txt" in files:
            chmod_args.append("/workspace/requirements.txt")
        for args, input_data in (
            (["docker", "exec", "-u", "0", session_id, "mkdir", "-p", "/workspace/app", "/workspace/workflows"], None),
            (["docker", "exec", "-u", "0", "-i", session_id, "tar", "--no-same-owner", "-xf", "-", "-C", "/workspace"], archive_bytes.getvalue()),
            (chmod_args, None),
        ):
            rc, _, _ = await self._run(args, input_data=input_data, timeout=30)
            if rc != 0:
                raise RuntimeError("Sealed preview source staging failed")

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

        archive_bytes = io.BytesIO()
        with tarfile.open(fileobj=archive_bytes, mode="w") as archive:
            for rel_path, content in files.items():
                safe = _safe_relpath(rel_path)
                if not safe or safe in written:
                    raise ValueError("Invalid or duplicate sandbox file path")
                data = content if isinstance(content, bytes) else str(content).encode("utf-8")
                member = tarfile.TarInfo(safe)
                member.size = len(data)
                member.mode = 0o644
                archive.addfile(member, io.BytesIO(data))
                written.append(safe)

        if written:
            rc, _, _ = await self._run(
                ["docker", "exec", session_id, "mkdir", "-p", base],
                timeout=10.0,
            )
            if rc != 0:
                raise RuntimeError("Docker sandbox workspace is unavailable")
            # Extract as the container user so subsequent edits and deletes retain access.
            rc, _, _ = await self._run(
                ["docker", "exec", "-i", session_id, "tar", "--no-same-owner", "--no-same-permissions", "-xf", "-", "-C", base],
                input_data=archive_bytes.getvalue(), timeout=30.0,
            )
            if rc != 0:
                raise RuntimeError("Docker sandbox file extraction failed")

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
            # Docker detaches the process; its own launch result still matters.
            with tempfile.TemporaryDirectory(prefix="mozaiks-docker-cli-") as config_dir:
                proc = await asyncio.create_subprocess_exec(
                    *_docker_prefix(config_dir), "exec", "-d",
                    *env_args,
                    "-w", workdir,
                    session_id,
                    "sh", "-c", command,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    env=_docker_cli_env(),
                )
                try:
                    returncode = await asyncio.wait_for(proc.wait(), timeout=float(timeout_seconds or 60))
                except TimeoutError:
                    proc.kill()
                    await proc.wait()
                    return SandboxRunResult(success=False, error="sandbox_timeout")
                return SandboxRunResult(success=returncode == 0, exit_code=returncode)

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
            logger.error("Docker sandbox run_command failed session=%s exception=%s", session_id, type(exc).__name__)
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
        stop_rc, _, _ = await self._run(
            ["docker", "stop", session_id],
            timeout=15.0,
        )
        attempts = 3 if stop_rc == 0 else 1
        for attempt in range(attempts):
            rc, stdout, _ = await self._run(
                ["docker", "ps", "--all", "--quiet", "--no-trunc", "--filter", f"id={session_id}"],
                timeout=15.0,
            )
            if rc != 0:
                return False
            if not stdout.strip():
                return True
            if attempt < attempts - 1:
                await asyncio.sleep(0.1)
        await self._run(["docker", "rm", "-f", session_id], timeout=15.0)
        rc, stdout, _ = await self._run(
            ["docker", "ps", "--all", "--quiet", "--no-trunc", "--filter", f"id={session_id}"],
            timeout=15.0,
        )
        return rc == 0 and not stdout.strip()

    def capabilities(self) -> dict[str, Any]:
        return {
            "provider": "docker",
            "supports_preview": True,
            "supports_file_io": True,
            "supports_command_execution": True,
            "supports_timeout_extension": False,
        }


def _safe_relpath(raw: str) -> str | None:
    path = raw.replace("\\", "/")
    if not path or path != path.strip() or path.startswith("/") or ":" in path or "\x00" in path:
        return None
    p = PurePosixPath(path)
    if str(p) == "." or any(part == ".." for part in p.parts):
        return None
    return str(p)


_docker_adapter: DockerSandboxAdapter | None = None


def get_docker_sandbox() -> DockerSandboxAdapter:
    global _docker_adapter
    if _docker_adapter is None:
        _docker_adapter = DockerSandboxAdapter()
    return _docker_adapter


__all__ = ["DockerSandboxAdapter", "docker_available", "get_docker_sandbox"]
