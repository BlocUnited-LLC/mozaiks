"""Run one scoped repository ACP turn in a disposable, offline Docker container.

The authenticated host supplies approved files and a fixed local image. A
separate trusted worker with local Docker socket access runs this transport;
the App Zero web host and the agent container must not receive that socket. This
transport grants no scope, validation, source-control, or promotion authority.
The returned archive must still pass ``stage_repository_workspace_archive`` and
the proposal must pass ``finalize_repository_patch`` against host-owned evidence.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import re
import subprocess
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from mozaiksai.core.adapters.local_docker_cli import _docker_cli_env, _docker_prefix
from mozaiksai.core.semantics.archive import ArchiveError, read_archive_manifest

from .contracts import (
    MAX_READ_ONLY_INSPECTION_BYTES,
    MAX_READ_ONLY_INSPECTION_FILES,
    CodingWorkerRequest,
    StagedPatchProposal,
)
from .execution_context import ApprovedExecutionContext
from .repository_acp_usage import IsolatedACPUsage, parse_isolated_acp_usage
from .repository_patch import (
    _EMPTY_REPOSITORY_ARCHIVE,
    RepositorySnapshotEvidence,
    _preflight_archive_directory,
    _verify_selected_baseline,
    select_repository_read_only_files,
)

MAX_REPOSITORY_DOCKER_REQUEST_BYTES = 20_971_520
MAX_REPOSITORY_DOCKER_OUTPUT_BYTES = 40_000_000
MAX_REPOSITORY_DOCKER_ARCHIVE_BYTES = 16_777_216
MAX_REPOSITORY_DOCKER_FILES = 50

_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
_CONTAINER_ID = re.compile(r"[0-9a-f]{64}\Z")
_IMAGE_REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./:@-]{0,255}\Z")
_CLI_STDERR_BYTES = 65_536
_CLI_STDOUT_BYTES = 1_048_576
_ALLOWED_IMAGE_ENV_NAMES = frozenset({
    "PATH", "HOME", "LANG", "NODE_VERSION", "YARN_VERSION",
    "GPG_KEY", "PYTHON_VERSION", "PYTHON_SHA256",
    "PYTHONDONTWRITEBYTECODE", "PYTHONUNBUFFERED", "PYTHONPATH",
    "PIP_DISABLE_PIP_VERSION_CHECK", "MOZAIKS_WEB_SHELL_PATH",
    "MOZAIKS_CHAT_UI_PATH", "MOZAIKS_FACTORY_APP_PATH", "LOGS_BASE_DIR",
})


class RepositoryDockerExecutionError(RuntimeError):
    """A container turn failed closed; never includes provider output or source."""


@dataclass(frozen=True, slots=True)
class RepositoryDockerTurn:
    proposal: StagedPatchProposal
    workspace_archive: bytes | None
    usage: IsolatedACPUsage | None = None


def _remove_container(container_name: str, config_dir: str) -> None:
    """Remove only this random name, including after a lost create response."""
    prefix = _docker_prefix(config_dir)
    try:
        removed = subprocess.run(
            [*prefix, "rm", "--force", container_name],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            env=_docker_cli_env(), timeout=30, check=False,
        )
        if removed.returncode == 0:
            return
        # A failed create may never have made a container. Distinguish that
        # from a daemon failure without surfacing Docker stderr to callers.
        probe = subprocess.run(
            [*prefix, "container", "inspect", container_name],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            env=_docker_cli_env(), timeout=20, check=False,
        )
        if probe.returncode != 0 and (
            b"No such object: " + container_name.encode("ascii") in probe.stderr
            or b"No such container: " + container_name.encode("ascii") in probe.stderr
        ):
            return
    except (OSError, subprocess.TimeoutExpired):
        raise RepositoryDockerExecutionError("REPOSITORY_DOCKER_CLEANUP_FAILED") from None
    raise RepositoryDockerExecutionError("REPOSITORY_DOCKER_CLEANUP_FAILED")


async def _read_limited(reader: asyncio.StreamReader, limit: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while chunk := await reader.read(min(65_536, limit - size + 1)):
        size += len(chunk)
        if size > limit:
            raise RepositoryDockerExecutionError("REPOSITORY_DOCKER_OUTPUT_LIMIT")
        chunks.append(chunk)
    return b"".join(chunks)


async def _docker(
    args: list[str], *, config_dir: str, stdin_bytes: bytes | None,
    timeout_seconds: int, stdout_limit: int,
) -> bytes:
    command = [*_docker_prefix(config_dir), *args]
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE if stdin_bytes is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_docker_cli_env(),
        )
    except OSError as exc:
        raise RepositoryDockerExecutionError("REPOSITORY_DOCKER_UNAVAILABLE") from exc

    async def send_input() -> None:
        if stdin_bytes is None or process.stdin is None:
            return
        try:
            process.stdin.write(stdin_bytes)
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            process.stdin.close()

    assert process.stdout is not None and process.stderr is not None
    input_task = asyncio.create_task(send_input())
    stdout_task = asyncio.create_task(_read_limited(process.stdout, stdout_limit))
    stderr_task = asyncio.create_task(_read_limited(process.stderr, _CLI_STDERR_BYTES))
    tasks = [input_task, stdout_task, stderr_task]
    try:
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=timeout_seconds)
        await asyncio.wait_for(process.wait(), timeout=5)
        if process.returncode != 0:
            raise RepositoryDockerExecutionError("REPOSITORY_DOCKER_COMMAND_FAILED")
        return stdout_task.result()
    except TimeoutError as exc:
        raise RepositoryDockerExecutionError("REPOSITORY_DOCKER_TIMEOUT") from exc
    finally:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_nonfinite(_value: str) -> Any:
    raise ValueError("nonfinite JSON number")


def _strict_json(raw: bytes) -> Any:
    return json.loads(
        raw.decode("utf-8", errors="strict"),
        object_pairs_hook=_strict_object,
        parse_constant=_reject_nonfinite,
    )


def _verify_container(config: Any, *, expected_image_id: str) -> None:
    if not isinstance(config, list) or len(config) != 1 or not isinstance(config[0], dict):
        raise RepositoryDockerExecutionError("REPOSITORY_DOCKER_INSPECT_FORMAT")
    container = config[0]
    host = container.get("HostConfig")
    image_config = container.get("Config")
    if not isinstance(host, dict) or not isinstance(image_config, dict):
        raise RepositoryDockerExecutionError("REPOSITORY_DOCKER_INSPECT_FORMAT")
    mounts = container.get("Mounts")
    tmpfs = host.get("Tmpfs")
    image_env = image_config.get("Env") or []
    labels = image_config.get("Labels") or {}
    if (
        container.get("Image") != expected_image_id
        or host.get("NetworkMode") != "none"
        or host.get("ReadonlyRootfs") is not True
        or host.get("Binds") not in (None, [])
        or not isinstance(mounts, list)
        or any(not isinstance(mount, dict) or mount.get("Type") != "tmpfs" for mount in mounts)
        or host.get("Privileged") is not False
        or host.get("CapDrop") != ["ALL"]
        or "no-new-privileges" not in (host.get("SecurityOpt") or [])
        or host.get("PidsLimit") != 64
        or host.get("Memory") != 768 * 1024 * 1024
        or host.get("NanoCpus") != 1_000_000_000
        or not isinstance(host.get("LogConfig"), dict)
        or host["LogConfig"].get("Type") != "none"
        or image_config.get("User") != "10001:10001"
        or not isinstance(labels, dict)
        or labels.get("mozaiks.refinement.repository_turn") != "offline"
        or not isinstance(image_env, list)
        or any(
            not isinstance(entry, str)
            or entry.partition("=")[1] != "="
            or entry.partition("=")[0] not in _ALLOWED_IMAGE_ENV_NAMES
            for entry in image_env
        )
        or not isinstance(tmpfs, dict)
        or set(tmpfs) != {"/tmp", "/workspace", "/home/sandbox"}
    ):
        raise RepositoryDockerExecutionError("REPOSITORY_DOCKER_ISOLATION_MISMATCH")


def _parse_turn_output(
    raw: bytes, *, selected_paths: set[str], create_paths: set[str],
    delete_paths: set[str], max_archive_bytes: int,
) -> RepositoryDockerTurn:
    try:
        output = _strict_json(raw)
        if not isinstance(output, dict) or set(output) != {"proposal", "workspace_archive_base64"}:
            raise ValueError("unexpected worker output shape")
        raw_proposal = output["proposal"]
        if not isinstance(raw_proposal, dict):
            raise ValueError("invalid proposal")
        usage = parse_isolated_acp_usage(raw_proposal.get("usage"))
        # Usage is advisory. Its absence or malformed shape cannot invalidate
        # an otherwise valid patch, and it never enters the safe proposal.
        proposal = StagedPatchProposal.model_validate({**raw_proposal, "usage": None})
        encoded = output["workspace_archive_base64"]
        if encoded is not None and not isinstance(encoded, str):
            raise ValueError("invalid archive encoding")
        if encoded is not None and len(encoded) > 4 * ((max_archive_bytes + 2) // 3):
            raise ValueError("encoded archive exceeds limit")
        archive = base64.b64decode(encoded, validate=True) if encoded is not None else None
        if proposal.status == "completed":
            if archive is None or not proposal.changed_files or len(archive) > max_archive_bytes:
                raise ValueError("completed turn lacks bounded archive and changes")
            if archive == _EMPTY_REPOSITORY_ARCHIVE:
                entry_paths: set[str] = set()
            else:
                _preflight_archive_directory(
                    archive,
                    expected_files=(len(selected_paths) if not create_paths and not delete_paths else None),
                    max_files=len(selected_paths) + len(create_paths),
                    max_bytes=max_archive_bytes,
                )
                manifest = read_archive_manifest(archive)
                entry_paths = {entry.path for entry in manifest.entries}
            if (
                selected_paths - entry_paths - delete_paths
                or entry_paths - selected_paths - create_paths
                or (archive == _EMPTY_REPOSITORY_ARCHIVE and (create_paths or selected_paths != delete_paths))
            ):
                raise ValueError("archive paths differ from selected files")
            for change in proposal.changed_files:
                if (
                    (change.op == "update" and change.path not in selected_paths)
                    or (change.op == "create" and change.path not in create_paths)
                    or (change.op == "delete" and change.path not in delete_paths)
                ):
                    raise ValueError("proposal operation differs from approved grants")
        elif archive is not None or proposal.changed_files:
            raise ValueError("failed turn must not return patch bytes")
    except (UnicodeError, ValueError, TypeError, RecursionError, ValidationError, ArchiveError, binascii.Error):
        # Pydantic and archive errors can contain file content or paths.
        raise RepositoryDockerExecutionError("REPOSITORY_DOCKER_INVALID_OUTPUT") from None

    # Model-authored text and operational events can quote read-only source.
    # The host constructs review text from verified file bytes after staging.
    safe_proposal = StagedPatchProposal(
        proposal_id=uuid.uuid4().hex,
        provider_id="acp_docker",
        status=proposal.status,
        summary="Isolated repository coding turn.",
        rationale="Pending host verification.",
        changed_files=proposal.changed_files,
        owned_paths=proposal.owned_paths,
        error=(f"Isolated coding turn reported {proposal.status}." if proposal.status != "completed" else None),
    )
    return RepositoryDockerTurn(proposal=safe_proposal, workspace_archive=archive, usage=usage)


async def execute_repository_docker_turn(
    request: CodingWorkerRequest, *, image: str, expected_image_id: str,
    max_wall_seconds: int = 90,
    max_archive_bytes: int = MAX_REPOSITORY_DOCKER_ARCHIVE_BYTES,
    approved_context: ApprovedExecutionContext | None = None,
    snapshot: RepositorySnapshotEvidence | None = None,
    validate_path: Callable[[str], object] | None = None,
    validate_create_absence: Callable[[str], object] | None = None,
) -> RepositoryDockerTurn:
    """Execute one already-approved file set; only a local, prebuilt image runs.

    ``image`` and ``expected_image_id`` are trusted host configuration, never
    request fields. The expected ID must come from an operator-approved image
    build or equivalent trusted image manifest, not this request or a fresh
    resolution of the tag. The host must verify the approved snapshot and path
    policy before calling this, then stage/finalize the returned archive
    against those same immutable inputs.
    """
    if not _IMAGE_REFERENCE.fullmatch(image) or (":" not in image and "@" not in image):
        raise ValueError("REPOSITORY_DOCKER_IMAGE: invalid fixed image reference")
    if not _IMAGE_ID.fullmatch(expected_image_id):
        raise ValueError("REPOSITORY_DOCKER_EXPECTED_IMAGE_ID")
    if not 1 <= max_wall_seconds <= 3600:
        raise ValueError("REPOSITORY_DOCKER_WALL_BUDGET")
    if not 1 <= max_archive_bytes <= MAX_REPOSITORY_DOCKER_ARCHIVE_BYTES:
        raise ValueError("REPOSITORY_DOCKER_ARCHIVE_BUDGET")
    selected_files = dict(request.files)
    inspection_files = dict(request.read_only_files)
    selected_paths = set(selected_files)
    create_paths = set(approved_context.create_paths) if approved_context is not None else set()
    delete_paths = set(approved_context.delete_paths) if approved_context is not None else set()
    if (
        not 1 <= len(selected_files) + len(create_paths) <= MAX_REPOSITORY_DOCKER_FILES
        or not delete_paths <= selected_paths
    ):
        raise ValueError("REPOSITORY_DOCKER_FILE_BUDGET")
    if approved_context is None:
        if snapshot is not None or validate_path is not None or validate_create_absence is not None or (
            request.metadata.get("approved_create_paths") or request.metadata.get("approved_delete_paths")
        ):
            raise ValueError("REPOSITORY_DOCKER_CONTEXT_REQUIRED")
    else:
        if snapshot is None or validate_path is None:
            raise ValueError("REPOSITORY_DOCKER_CONTEXT_REQUIRED")
        if (
            approved_context.schema_version != "managed_refinement.execution_context.v1"
            or request.app_id != approved_context.app_id
            or request.target_app_id not in (None, approved_context.app_id)
            or request.build_family != "app_bundle"
            or request.build_key != approved_context.build_registry_id
            or request.change_class != "patch"
            or request.raw_user_request != approved_context.raw_request
        ):
            raise ValueError("REPOSITORY_DOCKER_CONTEXT_MISMATCH")
        _verify_selected_baseline(
            approved_context,
            snapshot=snapshot,
            selected_paths=list(selected_files),
            baseline_files=selected_files,
            validate_path=validate_path,
            validate_create_absence=validate_create_absence,
        )
        if inspection_files:
            select_repository_read_only_files(
                approved_context,
                snapshot=snapshot,
                selected_paths=list(inspection_files),
                baseline_files=inspection_files,
                validate_path=validate_path,
                max_files=MAX_READ_ONLY_INSPECTION_FILES,
                max_bytes=MAX_READ_ONLY_INSPECTION_BYTES,
            )

    # Do not send baseline, user/tenant identity, host context, or metadata.
    scoped: dict[str, object] = {
        "app_id": request.app_id,
        "build_family": request.build_family,
        "build_record_id": request.build_record_id,
        "change_class": request.change_class,
        "raw_user_request": request.raw_user_request,
        "files": selected_files,
        "read_only_files": inspection_files,
    }
    if create_paths or delete_paths:
        scoped["create_paths"] = sorted(create_paths)
        scoped["delete_paths"] = sorted(delete_paths)
    input_bytes = json.dumps(scoped, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(input_bytes) > MAX_REPOSITORY_DOCKER_REQUEST_BYTES:
        raise ValueError("REPOSITORY_DOCKER_REQUEST_BUDGET")

    with tempfile.TemporaryDirectory(prefix="mozaiks-docker-cli-") as config_dir:
        image_id_raw = await _docker(
            ["image", "inspect", "--format", "{{.Id}}", image],
            config_dir=config_dir, stdin_bytes=None, timeout_seconds=20, stdout_limit=128,
        )
        try:
            image_id = image_id_raw.decode("ascii", errors="strict").strip()
        except UnicodeDecodeError:
            raise RepositoryDockerExecutionError("REPOSITORY_DOCKER_IMAGE_ID") from None
        if not _IMAGE_ID.fullmatch(image_id):
            raise RepositoryDockerExecutionError("REPOSITORY_DOCKER_IMAGE_ID")
        if image_id != expected_image_id:
            raise RepositoryDockerExecutionError("REPOSITORY_DOCKER_IMAGE_ID_MISMATCH")

        # A generated name lets us remove the container even if Docker created
        # it but the create response timed out or was malformed.
        container_name = f"mozaiks-acp-{uuid.uuid4().hex}"
        try:
            created = await _docker(
                [
                    "create", "--name", container_name,
                    "--label", "mozaiks.refinement.repository_turn=offline",
                    "--interactive", "--init",
                    "--network", "none", "--read-only", "--log-driver", "none",
                    "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                    "--pids-limit", "64", "--memory", "768m", "--cpus", "1",
                    "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m,mode=1777",
                    "--tmpfs", "/workspace:rw,nosuid,nodev,size=64m,mode=1777",
                    "--tmpfs", "/home/sandbox:rw,nosuid,nodev,size=16m,mode=1777",
                    "--user", "10001:10001", image_id,
                ],
                config_dir=config_dir, stdin_bytes=None, timeout_seconds=30,
                stdout_limit=128,
            )
            container_id = created.decode("ascii", errors="ignore").strip()
            if not _CONTAINER_ID.fullmatch(container_id):
                raise RepositoryDockerExecutionError("REPOSITORY_DOCKER_CONTAINER_ID")
            inspected = await _docker(
                ["inspect", container_id], config_dir=config_dir, stdin_bytes=None,
                timeout_seconds=20, stdout_limit=_CLI_STDOUT_BYTES,
            )
            try:
                _verify_container(_strict_json(inspected), expected_image_id=expected_image_id)
            except (UnicodeError, ValueError, TypeError):
                raise RepositoryDockerExecutionError("REPOSITORY_DOCKER_INSPECT_FORMAT") from None
            raw_output = await _docker(
                ["start", "--attach", "--interactive", container_id],
                config_dir=config_dir, stdin_bytes=input_bytes,
                timeout_seconds=max_wall_seconds + 10,
                stdout_limit=MAX_REPOSITORY_DOCKER_OUTPUT_BYTES,
            )
            exit_code = await _docker(
                ["inspect", "--format", "{{.State.ExitCode}}", container_id],
                config_dir=config_dir, stdin_bytes=None, timeout_seconds=20,
                stdout_limit=32,
            )
            if exit_code.strip() != b"0":
                raise RepositoryDockerExecutionError("REPOSITORY_DOCKER_WORKER_FAILED")
            return _parse_turn_output(
                raw_output, selected_paths=selected_paths,
                create_paths=create_paths, delete_paths=delete_paths,
                max_archive_bytes=max_archive_bytes,
            )
        finally:
            _remove_container(container_name, config_dir)


__all__ = [
    "RepositoryDockerExecutionError", "RepositoryDockerTurn", "execute_repository_docker_turn",
]
