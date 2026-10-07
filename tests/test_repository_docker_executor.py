"""Host transport checks for one offline, disposable repository coding turn."""

from __future__ import annotations

import asyncio
import base64
import json
import os
from typing import Any

import pytest

from mozaiksai.control_plane import (
    CodingWorkerRequest,
    RepositoryDockerExecutionError,
    execute_repository_docker_turn,
)
from mozaiksai.control_plane import repository_docker_executor as executor
from mozaiksai.core.semantics.archive import ArchiveEntry, build_deterministic_archive

_PATH = "app/ui/pages/Dashboard.jsx"
_BEFORE = "export default function Dashboard() {}\n"
_AFTER = "export default function Dashboard() { return 1; }\n"
_IMAGE = "mozaiks-acp-proof:local"
_IMAGE_ID = "sha256:" + "a" * 64
_CONTAINER_ID = "b" * 64


def _request(**changes: Any) -> CodingWorkerRequest:
    values = {
        "app_id": "proof", "build_family": "app_bundle", "build_record_id": "av_parent",
        "change_class": "patch", "raw_user_request": "Make the dashboard return 1",
        "files": {_PATH: _BEFORE},
        "baseline_files": {_PATH: _BEFORE, "private.txt": "host-baseline-sentinel"},
        "metadata": {"credential": "host-metadata-sentinel"},
        "context_seed": {"private": "host-context-sentinel"},
        "user_id": "host-user-sentinel",
    }
    values.update(changes)
    return CodingWorkerRequest(**values)


def _worker_output(*, archive: bytes | None = None, status: str = "completed") -> bytes:
    if archive is None and status == "completed":
        archive = build_deterministic_archive([ArchiveEntry(path=_PATH, content=_AFTER.encode())])
    return json.dumps({
        "proposal": {
            "proposal_id": "raw-provider-id", "provider_id": "raw-provider-name",
            "status": status,
            "summary": "read-only-source-sentinel",
            "rationale": "read-only-source-sentinel",
            "provider_events": [{"kind": "plan", "summary": "read-only-source-sentinel"}],
            "error": "read-only-source-sentinel" if status != "completed" else None,
            "owned_paths": [_PATH] if status == "completed" else [],
            "changed_files": (
                [{"path": _PATH, "op": "update", "content": _AFTER}]
                if status == "completed" else []
            ),
        },
        "workspace_archive_base64": base64.b64encode(archive).decode() if archive else None,
    }).encode()


def _inspect(*, network: str = "none", image_env: list[str] | None = None) -> bytes:
    return json.dumps([{
        "HostConfig": {
            "NetworkMode": network, "ReadonlyRootfs": True, "Binds": None,
            "Privileged": False, "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges"], "PidsLimit": 64,
            "Memory": 768 * 1024 * 1024, "NanoCpus": 1_000_000_000,
            "LogConfig": {"Type": "none"},
            "Tmpfs": {"/tmp": "", "/workspace": "", "/home/sandbox": ""},
        },
        "Config": {
            "User": "10001:10001", "Env": image_env or [],
            "Labels": {"mozaiks.refinement.repository_turn": "offline"},
        },
        "Mounts": [
            {"Type": "tmpfs", "Destination": "/tmp"},
            {"Type": "tmpfs", "Destination": "/workspace"},
            {"Type": "tmpfs", "Destination": "/home/sandbox"},
        ],
    }]).encode()


class FakeDocker:
    def __init__(self, *, output: bytes | None = None, inspect: bytes | None = None) -> None:
        self.output = output if output is not None else _worker_output()
        self.inspect = inspect if inspect is not None else _inspect()
        self.calls: list[tuple[list[str], bytes | None, int]] = []
        self.removed: list[str] = []

    def remove(self, container_name: str, config_dir: str) -> None:
        assert config_dir
        assert container_name.startswith("mozaiks-acp-")
        self.removed.append(container_name)

    async def __call__(
        self, args: list[str], *, config_dir: str, stdin_bytes: bytes | None,
        timeout_seconds: int, stdout_limit: int,
    ) -> bytes:
        assert config_dir
        self.calls.append((args, stdin_bytes, stdout_limit))
        if args[:2] == ["image", "inspect"]:
            return (_IMAGE_ID + "\n").encode()
        if args[0] == "create":
            return (_CONTAINER_ID + "\n").encode()
        if args == ["inspect", _CONTAINER_ID]:
            return self.inspect
        if args[:3] == ["start", "--attach", "--interactive"]:
            return self.output
        if args[:2] == ["inspect", "--format"]:
            return b"0\n"
        raise AssertionError(args)


def _install_fake(monkeypatch: pytest.MonkeyPatch, fake: FakeDocker) -> None:
    monkeypatch.setattr(executor, "_docker", fake)
    monkeypatch.setattr(executor, "_remove_container", fake.remove)


@pytest.mark.asyncio
async def test_docker_turn_transmits_only_scoped_input_and_scrubs_provider_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeDocker()
    _install_fake(monkeypatch, fake)
    result = await execute_repository_docker_turn(_request(), image=_IMAGE)

    assert result.proposal.status == "completed"
    assert result.proposal.changed_files[0].content == _AFTER
    assert result.proposal.provider_id == "acp_docker"
    assert result.proposal.proposal_id != "raw-provider-id"
    assert result.proposal.provider_events == []
    assert "read-only-source-sentinel" not in result.proposal.model_dump_json()
    assert result.workspace_archive is not None
    assert len(fake.removed) == 1

    create = fake.calls[1][0]
    assert create[0] == "create" and create[-1] == _IMAGE_ID
    assert create[create.index("--name") + 1] == fake.removed[0]
    assert create[create.index("--label") + 1] == "mozaiks.refinement.repository_turn=offline"
    for expected in (
        ["--network", "none"], ["--read-only"], ["--cap-drop", "ALL"],
        ["--security-opt", "no-new-privileges"], ["--user", "10001:10001"],
        ["--pids-limit", "64"], ["--memory", "768m"], ["--cpus", "1"],
        ["--log-driver", "none"],
    ):
        assert create[create.index(expected[0]):][:len(expected)] == expected
    sent = fake.calls[3][1]
    assert sent is not None
    assert set(json.loads(sent)) == {
        "app_id", "build_family", "build_record_id", "change_class",
        "raw_user_request", "files", "read_only_files",
    }
    assert b"host-baseline-sentinel" not in sent
    assert b"host-metadata-sentinel" not in sent
    assert b"host-context-sentinel" not in sent
    assert b"host-user-sentinel" not in sent


@pytest.mark.asyncio
async def test_isolation_mismatch_stops_before_start_and_removes_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeDocker(inspect=_inspect(network="bridge"))
    _install_fake(monkeypatch, fake)
    with pytest.raises(RepositoryDockerExecutionError, match="REPOSITORY_DOCKER_ISOLATION_MISMATCH"):
        await execute_repository_docker_turn(_request(), image=_IMAGE)
    assert not any(call[0][0] == "start" for call in fake.calls)
    assert len(fake.removed) == 1


@pytest.mark.asyncio
async def test_image_baked_model_credential_is_rejected_before_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeDocker(inspect=_inspect(image_env=["ANTHROPIC_API_KEY=private"] ))
    _install_fake(monkeypatch, fake)
    with pytest.raises(RepositoryDockerExecutionError, match="REPOSITORY_DOCKER_ISOLATION_MISMATCH"):
        await execute_repository_docker_turn(_request(), image=_IMAGE)
    assert not any(call[0][0] == "start" for call in fake.calls)
    assert len(fake.removed) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("output", [
    b'{"proposal":{},"proposal":{},"workspace_archive_base64":null}',
    b'{"proposal":{},"workspace_archive_base64":null,"extra":1}',
    b'{"proposal":{},"workspace_archive_base64":"not base64"}',
    _worker_output(archive=b"not a canonical archive"),
    _worker_output(archive=build_deterministic_archive([
        ArchiveEntry(path="app/other.txt", content=b"wrong set")
    ])),
])
async def test_invalid_worker_output_fails_closed_and_removes_container(
    monkeypatch: pytest.MonkeyPatch, output: bytes,
) -> None:
    fake = FakeDocker(output=output)
    _install_fake(monkeypatch, fake)
    with pytest.raises(RepositoryDockerExecutionError, match="REPOSITORY_DOCKER_INVALID_OUTPUT"):
        await execute_repository_docker_turn(_request(), image=_IMAGE)
    assert len(fake.removed) == 1


@pytest.mark.asyncio
async def test_failed_turn_has_no_archive_or_source_quoting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeDocker(output=_worker_output(status="rejected_scope"))
    _install_fake(monkeypatch, fake)
    result = await execute_repository_docker_turn(_request(), image=_IMAGE)
    assert result.workspace_archive is None
    assert result.proposal.status == "rejected_scope"
    assert result.proposal.error == "Isolated coding turn reported rejected_scope."
    assert "read-only-source-sentinel" not in result.proposal.model_dump_json()


@pytest.mark.asyncio
async def test_container_is_removed_when_start_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeDocker()

    async def failing_docker(*args: Any, **kwargs: Any) -> bytes:
        if args[0][0] == "start":
            raise RepositoryDockerExecutionError("REPOSITORY_DOCKER_TIMEOUT")
        return await fake(*args, **kwargs)

    monkeypatch.setattr(executor, "_docker", failing_docker)
    monkeypatch.setattr(executor, "_remove_container", fake.remove)
    with pytest.raises(RepositoryDockerExecutionError, match="REPOSITORY_DOCKER_TIMEOUT"):
        await execute_repository_docker_turn(_request(), image=_IMAGE)
    assert len(fake.removed) == 1


@pytest.mark.asyncio
async def test_random_container_name_is_removed_when_create_response_is_lost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeDocker()

    async def lost_create(*args: Any, **kwargs: Any) -> bytes:
        if args[0][0] == "create":
            fake.calls.append((args[0], None, kwargs["stdout_limit"]))
            raise RepositoryDockerExecutionError("REPOSITORY_DOCKER_TIMEOUT")
        return await fake(*args, **kwargs)

    monkeypatch.setattr(executor, "_docker", lost_create)
    monkeypatch.setattr(executor, "_remove_container", fake.remove)
    with pytest.raises(RepositoryDockerExecutionError, match="REPOSITORY_DOCKER_TIMEOUT"):
        await execute_repository_docker_turn(_request(), image=_IMAGE)
    assert len(fake.removed) == 1
    assert fake.calls[1][0][fake.calls[1][0].index("--name") + 1] == fake.removed[0]


@pytest.mark.asyncio
async def test_input_budget_rejects_before_container_creation(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeDocker()
    _install_fake(monkeypatch, fake)
    monkeypatch.setattr(executor, "MAX_REPOSITORY_DOCKER_REQUEST_BYTES", 100)
    with pytest.raises(ValueError, match="REPOSITORY_DOCKER_REQUEST_BUDGET"):
        await execute_repository_docker_turn(_request(), image=_IMAGE)
    assert fake.calls == []


@pytest.mark.asyncio
async def test_output_stream_limit_is_applied_before_json_decode() -> None:
    reader = asyncio.StreamReader()
    reader.feed_data(b"a" * 11)
    reader.feed_eof()
    with pytest.raises(RepositoryDockerExecutionError, match="REPOSITORY_DOCKER_OUTPUT_LIMIT"):
        await executor._read_limited(reader, 10)


def test_docker_cli_does_not_inherit_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "host-secret")
    monkeypatch.setenv("DOCKER_HOST", "tcp://attacker.example:2375")
    monkeypatch.setenv("DOCKER_CONFIG", "host-profile")
    environment = executor._docker_cli_env()
    assert "ANTHROPIC_API_KEY" not in environment
    assert "DOCKER_HOST" not in environment
    assert "DOCKER_CONFIG" not in environment
    assert executor._local_docker_endpoint().startswith("npipe:" if os.name == "nt" else "unix:")


@pytest.mark.skipif(
    os.getenv("MOZAIKS_RUN_REPOSITORY_DOCKER_PROOF") != "1",
    reason="opt-in offline Docker ACP proof",
)
@pytest.mark.asyncio
async def test_real_offline_proof_image_uses_host_executor() -> None:
    result = await execute_repository_docker_turn(
        _request(baseline_files={_PATH: _BEFORE}, metadata={}, context_seed={}, user_id=None),
        image=_IMAGE, max_wall_seconds=75, max_archive_bytes=700_000,
    )
    assert result.proposal.status == "completed"
    assert result.proposal.changed_files[0].content == _AFTER
    assert result.workspace_archive is not None
