"""Security boundaries for the opt-in local repository ACP transport."""

from __future__ import annotations

import base64
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
from mozaiksai.core.semantics.archive import ArchiveEntry, build_deterministic_archive

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
async def test_live_turn_rejects_combined_file_budget_before_docker() -> None:
    request = _request().model_copy(update={
        "files": {
            f"app/ui/pages/Page{index}.jsx": _BEFORE for index in range(50)
        },
        "read_only_files": {"README.md": "# Read only\n"},
    })
    with pytest.raises(ValueError, match="REPOSITORY_LIVE_FILE_BUDGET"):
        await execute_repository_docker_turn(
            request, image=_WORKER_IMAGE, expected_image_id=_WORKER_IMAGE_ID,
            approved_context=_context(), snapshot=_snapshot(),
            validate_path=lambda _path: None, live_profile=_profile(),
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


class _FakeGatewayProcess:
    returncode: int | None = None

    async def wait(self) -> int:
        self.returncode = 0
        return 0

    def kill(self) -> None:
        self.returncode = -9


def _install_successful_live_docker(
    monkeypatch: pytest.MonkeyPatch, *, worker_output: Any, removed: list[str],
    seen: list[dict[str, Any]] | None = None,
) -> None:
    """Fake every Docker step of one live turn; ``worker_output`` sees the stdin payload."""
    container_ids = iter(["c" * 64, "d" * 64, "e" * 64, "f" * 64])

    async def fake_docker(
        args: list[str], *, config_dir: str, stdin_bytes: bytes | None,
        timeout_seconds: int, stdout_limit: int,
    ) -> bytes:
        if args[:2] in (["network", "create"], ["network", "connect"]):
            if seen is not None and args[:2] == ["network", "create"]:
                seen.append({"network": args[-1]})
            return b"ok\n"
        if args[0] == "start":
            return b""
        if args[:3] == ["inspect", "--format", "{{.State.Running}}"]:
            return b"true\n"
        if args[:3] == ["inspect", "--format", "{{.State.ExitCode}}"]:
            return b"0\n"
        if args[0] == "attach":
            assert stdin_bytes is not None
            payload = json.loads(stdin_bytes)
            if seen is not None:
                seen.append({"job_token": payload["live"]["job_token"]})
            return worker_output(payload)
        raise AssertionError(f"unexpected Docker command: {args}")

    async def no_op(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def fake_create(*_args: Any, **_kwargs: Any) -> str:
        return next(container_ids)

    async def fake_gateway(_container_id: str, *, config_dir: str, config: dict[str, str]) -> Any:
        return _FakeGatewayProcess()

    monkeypatch.setattr(live, "_docker", fake_docker)
    monkeypatch.setattr(live, "_inspect_image", no_op)
    monkeypatch.setattr(live, "_inspect_network", no_op)
    monkeypatch.setattr(live, "_inspect_container", no_op)
    monkeypatch.setattr(live, "_create_container", fake_create)
    monkeypatch.setattr(live, "_start_gateway", fake_gateway)
    monkeypatch.setattr(live, "_remove_container", lambda name, _config_dir: removed.append(name))
    monkeypatch.setattr(live, "_remove_network", lambda name, _config_dir: removed.append(name))


def _update_output(content: str, *, summary: str = "updated") -> bytes:
    archive = build_deterministic_archive([ArchiveEntry(path=_PATH, content=content.encode("utf-8"))])
    return json.dumps({
        "proposal": {
            "proposal_id": "p-1", "provider_id": "acp", "status": "completed",
            "summary": summary, "owned_paths": [_PATH],
            "changed_files": [{"path": _PATH, "op": "update", "content": content}],
        },
        "workspace_archive_base64": base64.b64encode(archive).decode("ascii"),
    }).encode("utf-8")


async def _live_turn() -> RepositoryDockerTurn:
    return await execute_repository_docker_turn(
        _request(), image=_WORKER_IMAGE, expected_image_id=_WORKER_IMAGE_ID,
        approved_context=_context(), snapshot=_snapshot(),
        validate_path=lambda _path: None, live_profile=_profile(),
    )


@pytest.mark.asyncio
async def test_live_turn_returns_verified_update_and_removes_every_resource(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    removed: list[str] = []
    _install_successful_live_docker(
        monkeypatch, worker_output=lambda _payload: _update_output(_BEFORE + "// edited\n"), removed=removed,
    )
    turn = await _live_turn()
    assert turn.proposal.status == "completed"
    assert [(change.path, change.op) for change in turn.proposal.changed_files] == [(_PATH, "update")]
    assert len(removed) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("where", ["source", "proposal_text"])
async def test_live_turn_rejects_job_token_copied_into_returned_bytes(
    monkeypatch: pytest.MonkeyPatch, where: str,
) -> None:
    removed: list[str] = []

    def copy_token(payload: dict[str, Any]) -> bytes:
        token = payload["live"]["job_token"]
        if where == "source":
            return _update_output(_BEFORE + f"// {token}\n")
        return _update_output(_BEFORE + "// edited\n", summary=f"token {token}")

    _install_successful_live_docker(monkeypatch, worker_output=copy_token, removed=removed)
    with pytest.raises(RepositoryDockerExecutionError, match="REPOSITORY_LIVE_TOKEN_IN_OUTPUT"):
        await _live_turn()
    assert len(removed) == 4


@pytest.mark.asyncio
async def test_live_turn_cleanup_failure_discards_an_otherwise_valid_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    removed: list[str] = []
    _install_successful_live_docker(
        monkeypatch, worker_output=lambda _payload: _update_output(_BEFORE + "// edited\n"), removed=removed,
    )

    def failing_network_cleanup(name: str, _config_dir: str) -> None:
        removed.append(name)
        raise RepositoryDockerExecutionError("REPOSITORY_LIVE_NETWORK_CLEANUP_FAILED")

    monkeypatch.setattr(live, "_remove_network", failing_network_cleanup)
    with pytest.raises(RepositoryDockerExecutionError, match="REPOSITORY_LIVE_NETWORK_CLEANUP_FAILED"):
        await _live_turn()
    assert len(removed) == 4  # the second network is still attempted


@pytest.mark.asyncio
async def test_repeated_live_attempts_never_share_a_token_or_resource(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    removed: list[str] = []
    seen: list[dict[str, Any]] = []
    _install_successful_live_docker(
        monkeypatch, worker_output=lambda _payload: _update_output(_BEFORE + "// edited\n"),
        removed=removed, seen=seen,
    )
    await _live_turn()
    await _live_turn()
    tokens = [item["job_token"] for item in seen if "job_token" in item]
    networks = [item["network"] for item in seen if "network" in item]
    assert len(tokens) == 2 and len(set(tokens)) == 2
    assert len(networks) == 4 and len(set(networks)) == 4
    assert len(removed) == 8 and len(set(removed)) == 8


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["changed_source", "create_without_absence_proof", "unapproved_inspection"])
async def test_live_turn_rejects_drift_or_missing_proof_before_docker(
    monkeypatch: pytest.MonkeyPatch, drift: str,
) -> None:
    from mozaiksai.control_plane import repository_docker_executor as executor

    async def never_docker(*_args: Any, **_kwargs: Any) -> bytes:
        raise AssertionError("Docker must not run")

    monkeypatch.setattr(executor, "_docker", never_docker)
    monkeypatch.setattr(live, "_docker", never_docker)
    request, context = _request(), _context()
    if drift == "changed_source":
        request = request.model_copy(update={"files": {_PATH: _BEFORE + "// drifted\n"}})
    elif drift == "create_without_absence_proof":
        context = context.model_copy(update={"create_paths": ["app/ui/pages/New.jsx"]})
    else:
        request = request.model_copy(update={"read_only_files": {"README.md": "# not approved\n"}})
    with pytest.raises(ValueError, match="REPOSITORY_PATCH_"):
        await execute_repository_docker_turn(
            request, image=_WORKER_IMAGE, expected_image_id=_WORKER_IMAGE_ID,
            approved_context=context, snapshot=_snapshot(),
            validate_path=lambda _path: None, live_profile=_profile(),
        )
