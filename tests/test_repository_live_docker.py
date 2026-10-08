"""Security boundaries for the opt-in local repository ACP transport."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

import pytest
from pydantic import SecretStr

from mozaiksai.control_plane import (
    ApprovedExecutionContext,
    CodingWorkerRequest,
    RepositoryDockerExecutionError,
    RepositoryDockerTurn,
    RepositoryLiveACPProfile,
    RepositorySnapshotEvidence,
    StagedPatchProposal,
    execute_repository_docker_turn,
)
from mozaiksai.control_plane import repository_live_docker as live

_PATH = "app/ui/pages/Dashboard.jsx"
_BEFORE = "export default function Dashboard() {}\n"
_WORKER_IMAGE = "mozaiks-acp-live:local"
_WORKER_IMAGE_ID = "sha256:" + "a" * 64
_GATEWAY_IMAGE_ID = "sha256:" + "b" * 64


def _profile() -> RepositoryLiveACPProfile:
    return RepositoryLiveACPProfile(
        adapter="codex", model="gpt-5.2-codex",
        gateway_image="mozaiks-acp-gateway:local",
        gateway_expected_image_id=_GATEWAY_IMAGE_ID,
        upstream_api_key=SecretStr("upstream-private-key"),
    )


def _request() -> CodingWorkerRequest:
    return CodingWorkerRequest(
        app_id="app-1", build_family="app_bundle", build_key="registry-1",
        build_record_id="record-1", change_class="patch",
        raw_user_request="Fix the dashboard", files={_PATH: _BEFORE},
        metadata={"host_only": "never-enter-container"},
    )


def _context() -> ApprovedExecutionContext:
    return ApprovedExecutionContext(
        handoff_id="handoff-1", request_id="request-1", plan_id="plan-1",
        app_id="app-1", build_registry_id="registry-1",
        repository_full_name="org/repo", baseline_commit_sha="a" * 40,
        raw_request="Fix the dashboard", request_type="patch",
        approved_plan_digest="b" * 64, snapshot_digest="snapshot-1",
        graph_identity_digest="graph-1", impact_report_digest="impact-1",
        execution_strategy_id="bounded", rationale="Approved owner edit",
        allowed_paths=["app/ui/pages/"],
    )


def _snapshot() -> RepositorySnapshotEvidence:
    return RepositorySnapshotEvidence(
        plan_id="plan-1", request_id="request-1", app_id="app-1",
        repository_full_name="org/repo", baseline_commit_sha="a" * 40,
        snapshot_digest="snapshot-1",
        file_manifest={_PATH: "sha256:" + hashlib.sha256(_BEFORE.encode()).hexdigest()},
    )


def _network(name: str, *, internal: bool, peers: set[str]) -> dict[str, Any]:
    return {
        "Name": name, "Driver": "bridge", "Internal": internal,
        "EnableIPv6": False, "Attachable": False,
        "Options": {"com.docker.network.bridge.gateway_mode_ipv4": "isolated"} if internal else {},
        "Containers": {peer: {} for peer in peers},
    }


def _container(*, role: str, networks: set[str], image_id: str) -> dict[str, Any]:
    worker = role == "worker"
    tmpfs = {
        "/tmp": "rw,nosuid,nodev,size=64m,mode=1777" if worker else "rw,nosuid,nodev,size=16m,mode=1777",
        "/home/sandbox": "rw,nosuid,nodev,size=128m,mode=1777" if worker else "rw,nosuid,nodev,size=16m,mode=1777",
    }
    if worker:
        tmpfs["/workspace"] = "rw,nosuid,nodev,size=128m,mode=1777"
    endpoints = {name: {"Aliases": ["model-gateway"] if not worker else []} for name in networks}
    return {
        "Image": image_id,
        "HostConfig": {
            "NetworkMode": next(iter(networks)) if worker else "egress",
            "ReadonlyRootfs": True, "Binds": None, "Privileged": False,
            "CapDrop": ["ALL"], "SecurityOpt": ["no-new-privileges"],
            "PidsLimit": 128 if worker else 32,
            "Memory": (1536 if worker else 256) * 1024 * 1024,
            "MemorySwap": (1536 if worker else 256) * 1024 * 1024,
            "NanoCpus": 2_000_000_000 if worker else 1_000_000_000,
            "Init": True,
            "Devices": [], "DeviceRequests": [], "ExtraHosts": [],
            "PidMode": "", "IpcMode": "private", "PortBindings": {},
            "PublishAllPorts": False, "LogConfig": {"Type": "none"},
            "Tmpfs": tmpfs,
        },
        "Config": {
            "User": "10001:10001", "Env": ["PATH=/usr/bin"],
            "Labels": {f"mozaiks.refinement.repository_turn.{role}": "live"},
        },
        "NetworkSettings": {"Networks": endpoints},
        "Mounts": [],
    }


def test_live_profile_keeps_credential_masked_and_rejects_bad_image() -> None:
    profile = _profile()
    assert "upstream-private-key" not in repr(profile)
    with pytest.raises(ValueError, match="REPOSITORY_LIVE_GATEWAY_IMAGE_ID"):
        RepositoryLiveACPProfile(
            adapter="codex", model="gpt-5.2-codex", gateway_image="gateway:local",
            gateway_expected_image_id="latest", upstream_api_key=SecretStr("key"),
        )


@pytest.mark.asyncio
async def test_live_turn_requires_approval_before_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    async def never_docker(*_args: Any, **_kwargs: Any) -> bytes:
        nonlocal called
        called = True
        raise AssertionError("Docker must not run")

    from mozaiksai.control_plane import repository_docker_executor as executor

    monkeypatch.setattr(executor, "_docker", never_docker)
    with pytest.raises(ValueError, match="REPOSITORY_LIVE_CONTEXT_REQUIRED"):
        await execute_repository_docker_turn(
            _request(), image=_WORKER_IMAGE, expected_image_id=_WORKER_IMAGE_ID,
            live_profile=_profile(),
        )
    assert not called


@pytest.mark.asyncio
async def test_live_turn_rejects_wall_budget_beyond_gateway_lifetime() -> None:
    with pytest.raises(ValueError, match="REPOSITORY_LIVE_WALL_BUDGET"):
        await execute_repository_docker_turn(
            _request(), image=_WORKER_IMAGE, expected_image_id=_WORKER_IMAGE_ID,
            approved_context=_context(), snapshot=_snapshot(),
            validate_path=lambda _path: None, live_profile=_profile(),
            max_wall_seconds=601,
        )


@pytest.mark.asyncio
async def test_live_turn_delegates_only_verified_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_live(**kwargs: Any) -> RepositoryDockerTurn:
        captured.update(kwargs)
        return RepositoryDockerTurn(
            proposal=StagedPatchProposal(
                proposal_id="p-1", provider_id="acp_docker", status="empty",
            ),
            workspace_archive=None,
        )

    monkeypatch.setattr(live, "execute_live_repository_docker_turn", fake_live)
    await execute_repository_docker_turn(
        _request(), image=_WORKER_IMAGE, expected_image_id=_WORKER_IMAGE_ID,
        approved_context=_context(), snapshot=_snapshot(),
        validate_path=lambda _path: None, live_profile=_profile(),
    )
    assert captured["scoped"] == {
        "app_id": "app-1", "build_family": "app_bundle", "build_record_id": "record-1",
        "change_class": "patch", "raw_user_request": "Fix the dashboard",
        "files": {_PATH: _BEFORE}, "read_only_files": {},
    }
    assert "upstream-private-key" not in json.dumps(captured["scoped"])


@pytest.mark.parametrize("change", [
    {"Internal": False},
    {"EnableIPv6": True},
    {"Options": {}},
    {"Containers": {"extra-container": {}}},
])
def test_private_network_rejects_egress_or_unexpected_peer(change: dict[str, Any]) -> None:
    inspected = _network("private", internal=True, peers=set())
    inspected.update(change)
    with pytest.raises(RepositoryDockerExecutionError, match="REPOSITORY_LIVE_NETWORK_MISMATCH"):
        live._verify_network(inspected, name="private", internal=True, containers=set())


@pytest.mark.parametrize("change", [
    {"Config": {"User": "10001:10001", "Env": ["OPENAI_API_KEY=leaked"],
                "Labels": {"mozaiks.refinement.repository_turn.worker": "live"}}},
    {"NetworkSettings": {"Networks": {"private": {}, "bridge": {}}}},
    {"HostConfig": {"NetworkMode": "private", "Privileged": True}},
])
def test_worker_inspect_rejects_secret_or_extra_authority(change: dict[str, Any]) -> None:
    inspected = _container(role="worker", networks={"private"}, image_id=_WORKER_IMAGE_ID)
    for key, fields in change.items():
        inspected[key].update(deepcopy(fields))
    with pytest.raises(RepositoryDockerExecutionError, match="REPOSITORY_LIVE_CONTAINER_ISOLATION_MISMATCH"):
        live._verify_container(
            inspected, image_id=_WORKER_IMAGE_ID, networks={"private"},
            primary_network="private", role="worker",
        )


def test_expected_worker_and_gateway_inspection_shapes() -> None:
    live._verify_container(
        _container(role="worker", networks={"private"}, image_id=_WORKER_IMAGE_ID),
        image_id=_WORKER_IMAGE_ID, networks={"private"}, primary_network="private", role="worker",
    )
    live._verify_container(
        _container(role="gateway", networks={"private", "egress"}, image_id=_GATEWAY_IMAGE_ID),
        image_id=_GATEWAY_IMAGE_ID, networks={"private", "egress"},
        primary_network="egress", role="gateway", gateway_alias_network="private",
    )


@pytest.mark.asyncio
async def test_gateway_failure_withholds_source_and_cleans_docker_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    docker_calls: list[tuple[list[str], bytes | None]] = []
    removed: list[str] = []
    gateway_config: dict[str, str] = {}
    container_ids = iter(["c" * 64, "d" * 64])

    async def fake_docker(
        args: list[str], *, config_dir: str, stdin_bytes: bytes | None,
        timeout_seconds: int, stdout_limit: int,
    ) -> bytes:
        assert config_dir and timeout_seconds and stdout_limit
        docker_calls.append((args, stdin_bytes))
        if args[:2] in (["network", "create"], ["network", "connect"]):
            return b"ok\n"
        raise AssertionError(f"unexpected Docker command before gateway readiness: {args}")

    async def fake_image(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def fake_inspect(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def fake_create(*_args: Any, **_kwargs: Any) -> str:
        return next(container_ids)

    async def failed_gateway(_container_id: str, *, config_dir: str, config: dict[str, str]) -> Any:
        assert config_dir
        gateway_config.update(config)
        raise RepositoryDockerExecutionError("REPOSITORY_LIVE_GATEWAY_NOT_READY")

    monkeypatch.setattr(live, "_docker", fake_docker)
    monkeypatch.setattr(live, "_inspect_image", fake_image)
    monkeypatch.setattr(live, "_inspect_network", fake_inspect)
    monkeypatch.setattr(live, "_inspect_container", fake_inspect)
    monkeypatch.setattr(live, "_create_container", fake_create)
    monkeypatch.setattr(live, "_start_gateway", failed_gateway)
    monkeypatch.setattr(live, "_remove_container", lambda name, _config_dir: removed.append(name))
    monkeypatch.setattr(live, "_remove_network", lambda name, _config_dir: removed.append(name))

    with pytest.raises(RepositoryDockerExecutionError, match="REPOSITORY_LIVE_GATEWAY_NOT_READY"):
        await live.execute_live_repository_docker_turn(
            scoped={"files": {_PATH: _BEFORE}}, profile=_profile(),
            image=_WORKER_IMAGE, expected_image_id=_WORKER_IMAGE_ID,
            selected_paths={_PATH}, create_paths=set(), delete_paths=set(),
            max_wall_seconds=90, max_archive_bytes=1024,
        )

    assert gateway_config["upstream_api_key"] == "upstream-private-key"
    assert gateway_config["model"] == "gpt-5.2-codex"
    assert all(stdin is None for _args, stdin in docker_calls)
    assert "upstream-private-key" not in repr(docker_calls)
    assert len(removed) == 4  # both containers and both unique networks
