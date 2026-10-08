"""Opt-in local ACP repository turn with a separate credential-holding gateway.

The trusted caller supplies the approved source context and a fixed image ID
through ``execute_repository_docker_turn``. This module owns only the disposable
Docker transport. It does not approve source, accept a patch, or publish a PR.
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
import subprocess
import tempfile
import uuid
from typing import Any

from mozaiksai.core.adapters.local_docker_cli import (
    _docker_cli_env,
    _docker_prefix,
)

from .repository_docker_executor import (
    _ALLOWED_IMAGE_ENV_NAMES,
    _CONTAINER_ID,
    _IMAGE_ID,
    MAX_REPOSITORY_DOCKER_OUTPUT_BYTES,
    MAX_REPOSITORY_DOCKER_REQUEST_BYTES,
    RepositoryDockerExecutionError,
    RepositoryDockerTurn,
    RepositoryLiveACPProfile,
    _docker,
    _parse_turn_output,
    _remove_container,
    _strict_json,
)

_NETWORK_NAME = re.compile(r"mozaiks-acp-(?:private|egress)-[0-9a-f]{24}\Z")
_GATEWAY_ALIAS = "model-gateway"
_GATEWAY_URL = f"http://{_GATEWAY_ALIAS}:8765"
_GATEWAY_READY = b"READY\n"
_LIVE_WORKER_ENV_NAMES = _ALLOWED_IMAGE_ENV_NAMES | frozenset({
    "CODEX_HOME", "CLAUDE_CONFIG_DIR", "MOZAIKS_ACP_LIVE_WORKER",
})
_GATEWAY_ENV_NAMES = _ALLOWED_IMAGE_ENV_NAMES


def _remove_network(network_name: str, config_dir: str) -> None:
    """Remove only the unique network allocated by this turn."""
    if not _NETWORK_NAME.fullmatch(network_name):
        raise RepositoryDockerExecutionError("REPOSITORY_LIVE_NETWORK_NAME")
    prefix = _docker_prefix(config_dir)
    try:
        removed = subprocess.run(
            [*prefix, "network", "rm", network_name], stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=_docker_cli_env(),
            timeout=30, check=False,
        )
        if removed.returncode == 0:
            return
        probe = subprocess.run(
            [*prefix, "network", "inspect", network_name], stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=_docker_cli_env(),
            timeout=20, check=False,
        )
        if probe.returncode != 0 and b"not found" in probe.stderr.lower():
            return
    except (OSError, subprocess.TimeoutExpired):
        raise RepositoryDockerExecutionError("REPOSITORY_LIVE_NETWORK_CLEANUP_FAILED") from None
    raise RepositoryDockerExecutionError("REPOSITORY_LIVE_NETWORK_CLEANUP_FAILED")


def _one_inspect(raw: bytes, *, kind: str) -> dict[str, Any]:
    try:
        value = _strict_json(raw)
    except (ValueError, UnicodeError, TypeError):
        raise RepositoryDockerExecutionError(f"REPOSITORY_LIVE_{kind}_INSPECT_FORMAT") from None
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        raise RepositoryDockerExecutionError(f"REPOSITORY_LIVE_{kind}_INSPECT_FORMAT")
    return value[0]


def _verify_network(
    value: dict[str, Any], *, name: str, internal: bool, containers: set[str],
) -> None:
    options = value.get("Options") or {}
    peers = value.get("Containers") or {}
    if (
        value.get("Name") != name
        or value.get("Driver") != "bridge"
        or value.get("Internal") is not internal
        or value.get("EnableIPv6") is not False
        or value.get("Attachable") is not False
        or not isinstance(options, dict)
        or not isinstance(peers, dict)
        or set(peers) != containers
        or (internal and options.get("com.docker.network.bridge.gateway_mode_ipv4") != "isolated")
    ):
        raise RepositoryDockerExecutionError("REPOSITORY_LIVE_NETWORK_MISMATCH")


def _verify_container(
    value: dict[str, Any], *, image_id: str, networks: set[str],
    primary_network: str, role: str, gateway_alias_network: str | None = None,
) -> None:
    host = value.get("HostConfig")
    config = value.get("Config")
    settings = value.get("NetworkSettings")
    mounts = value.get("Mounts")
    if not isinstance(host, dict) or not isinstance(config, dict) or not isinstance(settings, dict):
        raise RepositoryDockerExecutionError("REPOSITORY_LIVE_CONTAINER_INSPECT_FORMAT")
    actual_networks = settings.get("Networks")
    image_env = config.get("Env") or []
    labels = config.get("Labels") or {}
    allowed_env = _LIVE_WORKER_ENV_NAMES if role == "worker" else _GATEWAY_ENV_NAMES
    expected_tmpfs = {
        "/tmp": "rw,nosuid,nodev,size=64m,mode=1777" if role == "worker" else "rw,nosuid,nodev,size=16m,mode=1777",
        "/home/sandbox": "rw,nosuid,nodev,size=128m,mode=1777" if role == "worker" else "rw,nosuid,nodev,size=16m,mode=1777",
    }
    if role == "worker":
        expected_tmpfs["/workspace"] = "rw,nosuid,nodev,size=128m,mode=1777"
    expected_label = f"mozaiks.refinement.repository_turn.{role}"
    expected_memory = 1536 * 1024 * 1024 if role == "worker" else 256 * 1024 * 1024
    expected_pids = 128 if role == "worker" else 32
    expected_cpus = 2_000_000_000 if role == "worker" else 1_000_000_000
    if (
        value.get("Image") != image_id
        or not isinstance(actual_networks, dict)
        or set(actual_networks) != networks
        or any(not isinstance(endpoint, dict) for endpoint in actual_networks.values())
        or not isinstance(mounts, list)
        or any(not isinstance(mount, dict) or mount.get("Type") != "tmpfs" for mount in mounts)
        or host.get("Binds") not in (None, [])
        or host.get("Privileged") is not False
        or host.get("ReadonlyRootfs") is not True
        or host.get("CapDrop") != ["ALL"]
        or "no-new-privileges" not in (host.get("SecurityOpt") or [])
        or host.get("NetworkMode") != primary_network
        or host.get("Memory") != expected_memory
        or host.get("MemorySwap") != expected_memory
        or host.get("PidsLimit") != expected_pids
        or host.get("NanoCpus") != expected_cpus
        or host.get("Init") is not True
        or host.get("Devices") not in (None, [])
        or host.get("DeviceRequests") not in (None, [])
        or host.get("ExtraHosts") not in (None, [])
        or host.get("PidMode") not in (None, "")
        or host.get("IpcMode") not in (None, "", "private")
        or host.get("PortBindings") not in (None, {})
        or host.get("PublishAllPorts") is not False
        or not isinstance(host.get("LogConfig"), dict)
        or host["LogConfig"].get("Type") != "none"
        or not isinstance(host.get("Tmpfs"), dict)
        or host["Tmpfs"] != expected_tmpfs
        or config.get("User") != "10001:10001"
        or not isinstance(labels, dict)
        or labels.get(expected_label) != "live"
        or (role == "gateway" and (
            gateway_alias_network not in actual_networks
            or not isinstance(actual_networks[gateway_alias_network], dict)
            or _GATEWAY_ALIAS not in (actual_networks[gateway_alias_network].get("Aliases") or [])
        ))
        or not isinstance(image_env, list)
        or any(
            not isinstance(entry, str)
            or "=" not in entry
            or entry.partition("=")[0] not in allowed_env
            for entry in image_env
        )
    ):
        raise RepositoryDockerExecutionError("REPOSITORY_LIVE_CONTAINER_ISOLATION_MISMATCH")


async def _inspect_network(
    name: str, *, config_dir: str, internal: bool, containers: set[str],
) -> None:
    raw = await _docker(
        ["network", "inspect", name], config_dir=config_dir, stdin_bytes=None,
        timeout_seconds=20, stdout_limit=1_048_576,
    )
    _verify_network(_one_inspect(raw, kind="NETWORK"), name=name, internal=internal, containers=containers)


async def _inspect_container(
    container_id: str, *, config_dir: str, image_id: str, networks: set[str],
    primary_network: str, role: str, gateway_alias_network: str | None = None,
) -> None:
    raw = await _docker(
        ["inspect", container_id], config_dir=config_dir, stdin_bytes=None,
        timeout_seconds=20, stdout_limit=1_048_576,
    )
    _verify_container(
        _one_inspect(raw, kind="CONTAINER"), image_id=image_id, networks=networks,
        primary_network=primary_network, role=role, gateway_alias_network=gateway_alias_network,
    )


async def _inspect_image(image: str, expected_id: str, *, config_dir: str) -> None:
    raw = await _docker(
        ["image", "inspect", "--format", "{{.Id}}", image], config_dir=config_dir,
        stdin_bytes=None, timeout_seconds=20, stdout_limit=128,
    )
    try:
        actual = raw.decode("ascii", errors="strict").strip()
    except UnicodeDecodeError:
        raise RepositoryDockerExecutionError("REPOSITORY_LIVE_IMAGE_ID") from None
    if not _IMAGE_ID.fullmatch(actual) or actual != expected_id:
        raise RepositoryDockerExecutionError("REPOSITORY_LIVE_IMAGE_ID_MISMATCH")


async def _create_container(args: list[str], *, config_dir: str) -> str:
    raw = await _docker(
        ["create", *args], config_dir=config_dir, stdin_bytes=None,
        timeout_seconds=30, stdout_limit=128,
    )
    container_id = raw.decode("ascii", errors="ignore").strip()
    if not _CONTAINER_ID.fullmatch(container_id):
        raise RepositoryDockerExecutionError("REPOSITORY_LIVE_CONTAINER_ID")
    return container_id


async def _start_gateway(container_id: str, *, config_dir: str, config: dict[str, str]) -> asyncio.subprocess.Process:
    process: asyncio.subprocess.Process | None = None
    ready_confirmed = False
    try:
        process = await asyncio.create_subprocess_exec(
            *_docker_prefix(config_dir), "start", "--attach", "--interactive", container_id,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, env=_docker_cli_env(),
        )
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(json.dumps(config, separators=(",", ":")).encode("utf-8") + b"\n")
        await asyncio.wait_for(process.stdin.drain(), timeout=10)
        process.stdin.close()
        ready = await asyncio.wait_for(process.stdout.readline(), timeout=20)
        if ready != _GATEWAY_READY or process.returncode is not None:
            raise RepositoryDockerExecutionError("REPOSITORY_LIVE_GATEWAY_NOT_READY")
        ready_confirmed = True
        return process
    except (OSError, TimeoutError, BrokenPipeError, ConnectionResetError):
        raise RepositoryDockerExecutionError("REPOSITORY_LIVE_GATEWAY_NOT_READY") from None
    finally:
        if process is not None and not ready_confirmed:
            if process.returncode is None:
                process.kill()
            await process.wait()


async def execute_live_repository_docker_turn(
    *, scoped: dict[str, object], profile: RepositoryLiveACPProfile,
    image: str, expected_image_id: str, selected_paths: set[str],
    create_paths: set[str], delete_paths: set[str], max_wall_seconds: int,
    max_archive_bytes: int,
) -> RepositoryDockerTurn:
    """Run one bounded ACP turn after the caller has verified approval/snapshot."""
    job_token = secrets.token_urlsafe(32)
    payload = {
        **scoped,
        "live": {
            "adapter": profile.adapter,
            "model": profile.model,
            "gateway_url": _GATEWAY_URL,
            "job_token": job_token,
            "max_wall_seconds": max_wall_seconds,
        },
    }
    input_bytes = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(input_bytes) > MAX_REPOSITORY_DOCKER_REQUEST_BYTES:
        raise ValueError("REPOSITORY_DOCKER_REQUEST_BUDGET")

    token = uuid.uuid4().hex[:24]
    private_network = f"mozaiks-acp-private-{token}"
    egress_network = f"mozaiks-acp-egress-{token}"
    worker_name = f"mozaiks-acp-live-worker-{token}"
    gateway_name = f"mozaiks-acp-live-gateway-{token}"
    with tempfile.TemporaryDirectory(prefix="mozaiks-live-docker-cli-") as config_dir:
        await _inspect_image(image, expected_image_id, config_dir=config_dir)
        await _inspect_image(profile.gateway_image, profile.gateway_expected_image_id, config_dir=config_dir)
        private_attempted = False
        egress_attempted = False
        worker_attempted = False
        gateway_attempted = False
        gateway_process: asyncio.subprocess.Process | None = None
        worker_id: str | None = None
        gateway_id: str | None = None
        try:
            private_attempted = True
            await _docker(
                [
                    "network", "create", "--driver", "bridge", "--internal",
                    "--opt", "com.docker.network.bridge.gateway_mode_ipv4=isolated",
                    private_network,
                ],
                config_dir=config_dir, stdin_bytes=None, timeout_seconds=30, stdout_limit=128,
            )
            await _inspect_network(private_network, config_dir=config_dir, internal=True, containers=set())
            egress_attempted = True
            await _docker(
                ["network", "create", "--driver", "bridge", egress_network],
                config_dir=config_dir, stdin_bytes=None, timeout_seconds=30, stdout_limit=128,
            )
            await _inspect_network(egress_network, config_dir=config_dir, internal=False, containers=set())

            gateway_attempted = True
            gateway_id = await _create_container(
                [
                    "--name", gateway_name,
                    "--label", "mozaiks.refinement.repository_turn.gateway=live",
                    "--interactive", "--init", "--network", egress_network,
                    "--read-only", "--log-driver", "none", "--cap-drop", "ALL",
                    "--security-opt", "no-new-privileges", "--pids-limit", "32",
                    "--memory", "256m", "--memory-swap", "256m", "--cpus", "1",
                    "--tmpfs", "/tmp:rw,nosuid,nodev,size=16m,mode=1777",
                    "--tmpfs", "/home/sandbox:rw,nosuid,nodev,size=16m,mode=1777",
                    "--user", "10001:10001", profile.gateway_expected_image_id,
                ],
                config_dir=config_dir,
            )
            await _docker(
                ["network", "connect", "--alias", _GATEWAY_ALIAS, private_network, gateway_id],
                config_dir=config_dir, stdin_bytes=None, timeout_seconds=20, stdout_limit=128,
            )

            worker_attempted = True
            worker_id = await _create_container(
                [
                    "--name", worker_name,
                    "--label", "mozaiks.refinement.repository_turn.worker=live",
                    "--interactive", "--init", "--network", private_network,
                    "--read-only", "--log-driver", "none", "--cap-drop", "ALL",
                    "--security-opt", "no-new-privileges", "--pids-limit", "128",
                    "--memory", "1536m", "--memory-swap", "1536m", "--cpus", "2",
                    "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m,mode=1777",
                    "--tmpfs", "/workspace:rw,nosuid,nodev,size=128m,mode=1777",
                    "--tmpfs", "/home/sandbox:rw,nosuid,nodev,size=128m,mode=1777",
                    "--user", "10001:10001", expected_image_id,
                ],
                config_dir=config_dir,
            )
            await _inspect_network(
                private_network, config_dir=config_dir, internal=True,
                containers=set(),
            )
            await _inspect_network(
                egress_network, config_dir=config_dir, internal=False,
                containers=set(),
            )
            await _inspect_container(
                worker_id, config_dir=config_dir, image_id=expected_image_id,
                networks={private_network}, primary_network=private_network, role="worker",
            )
            await _inspect_container(
                gateway_id, config_dir=config_dir, image_id=profile.gateway_expected_image_id,
                networks={egress_network, private_network}, primary_network=egress_network,
                role="gateway", gateway_alias_network=private_network,
            )

            gateway_config = {
                "adapter": profile.adapter,
                "upstream_api_key": profile.upstream_api_key.get_secret_value(),
                "job_token": job_token,
            }
            try:
                gateway_process = await _start_gateway(gateway_id, config_dir=config_dir, config=gateway_config)
            finally:
                gateway_config.clear()
            await _inspect_network(
                private_network, config_dir=config_dir, internal=True,
                containers={gateway_id},
            )
            await _inspect_network(
                egress_network, config_dir=config_dir, internal=False,
                containers={gateway_id},
            )
            await _docker(
                ["start", worker_id], config_dir=config_dir, stdin_bytes=None,
                timeout_seconds=20, stdout_limit=128,
            )
            running = await _docker(
                ["inspect", "--format", "{{.State.Running}}", worker_id],
                config_dir=config_dir, stdin_bytes=None, timeout_seconds=20, stdout_limit=32,
            )
            if running.strip() != b"true":
                raise RepositoryDockerExecutionError("REPOSITORY_LIVE_WORKER_FAILED")
            await _inspect_network(
                private_network, config_dir=config_dir, internal=True,
                containers={worker_id, gateway_id},
            )
            raw_output = await _docker(
                ["attach", worker_id],
                config_dir=config_dir, stdin_bytes=input_bytes,
                timeout_seconds=max_wall_seconds + 15,
                stdout_limit=MAX_REPOSITORY_DOCKER_OUTPUT_BYTES,
            )
            if gateway_process.returncode is not None:
                raise RepositoryDockerExecutionError("REPOSITORY_LIVE_GATEWAY_STOPPED")
            running = await _docker(
                ["inspect", "--format", "{{.State.Running}}", gateway_id],
                config_dir=config_dir, stdin_bytes=None, timeout_seconds=20, stdout_limit=32,
            )
            if running.strip() != b"true":
                raise RepositoryDockerExecutionError("REPOSITORY_LIVE_GATEWAY_STOPPED")
            exit_code = await _docker(
                ["inspect", "--format", "{{.State.ExitCode}}", worker_id],
                config_dir=config_dir, stdin_bytes=None, timeout_seconds=20, stdout_limit=32,
            )
            if exit_code.strip() != b"0":
                raise RepositoryDockerExecutionError("REPOSITORY_LIVE_WORKER_FAILED")
            return _parse_turn_output(
                raw_output, selected_paths=selected_paths,
                create_paths=create_paths, delete_paths=delete_paths,
                max_archive_bytes=max_archive_bytes,
            )
        finally:
            cleanup_errors: list[RepositoryDockerExecutionError] = []
            for attempted, name in ((worker_attempted, worker_name), (gateway_attempted, gateway_name)):
                if attempted:
                    try:
                        _remove_container(name, config_dir)
                    except RepositoryDockerExecutionError as exc:
                        cleanup_errors.append(exc)
            if gateway_process is not None:
                try:
                    await asyncio.wait_for(gateway_process.wait(), timeout=10)
                except TimeoutError:
                    gateway_process.kill()
                    await gateway_process.wait()
                    cleanup_errors.append(RepositoryDockerExecutionError("REPOSITORY_LIVE_GATEWAY_CLI_CLEANUP_FAILED"))
            for attempted, name in ((private_attempted, private_network), (egress_attempted, egress_network)):
                if attempted:
                    try:
                        _remove_network(name, config_dir)
                    except RepositoryDockerExecutionError as exc:
                        cleanup_errors.append(exc)
            if cleanup_errors:
                raise cleanup_errors[0]


__all__ = ["execute_live_repository_docker_turn"]

