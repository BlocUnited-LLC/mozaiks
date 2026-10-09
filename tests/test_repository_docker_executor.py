"""Host transport checks for one offline, disposable repository coding turn."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
from typing import Any

import pytest

from infra.docker.acp_proof.worker import _ProofWorkerRequest, _verify_isolation_probe
from mozaiksai.control_plane import (
    CodingWorkerRequest,
    RepositoryDockerExecutionError,
    execute_repository_docker_turn,
)
from mozaiksai.control_plane import repository_docker_executor as executor
from mozaiksai.control_plane.execution_context import ApprovedExecutionContext
from mozaiksai.control_plane.repository_patch import (
    _EMPTY_REPOSITORY_ARCHIVE,
    RepositorySnapshotEvidence,
    finalize_repository_patch,
    stage_repository_workspace_archive,
)
from mozaiksai.core.adapters.local_docker_cli import _local_docker_endpoint
from mozaiksai.core.semantics.archive import ArchiveEntry, build_deterministic_archive

_PATH = "app/ui/pages/Dashboard.jsx"
_BEFORE = "export default function Dashboard() {}\n"
_AFTER = "export default function Dashboard() { return 1; }\n"
_NEW_PATH = "app/ui/pages/New.jsx"
_NEW_CONTENT = "export default function New() {}\n"
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


_SAFE_ISOLATION_PROBE = {
    "adapter_host_secret_visible": False,
    "adapter_outbound_reachable": False,
    "baseline_file_visible": False,
    "read_only_test_visible": False,
    "host_sentinel_visible": False,
    "terminal": {"host_secret_visible": False, "outbound_reachable": False},
}


@pytest.mark.parametrize(
    "path",
    [
        ("adapter_host_secret_visible",),
        ("adapter_outbound_reachable",),
        ("baseline_file_visible",),
        ("read_only_test_visible",),
        ("host_sentinel_visible",),
        ("terminal", "host_secret_visible"),
        ("terminal", "outbound_reachable"),
    ],
)
def test_synthetic_agent_probe_cannot_report_exposure_as_success(path: tuple[str, ...]) -> None:
    request = _ProofWorkerRequest.model_validate(_request().model_dump())
    _verify_isolation_probe(json.dumps(_SAFE_ISOLATION_PROBE), request)
    exposed = json.loads(json.dumps(_SAFE_ISOLATION_PROBE))
    current = exposed
    for key in path[:-1]:
        current = current[key]
    current[path[-1]] = True
    with pytest.raises(RuntimeError, match="ACP_PROOF_ISOLATION_FAILED"):
        _verify_isolation_probe(json.dumps(exposed), request)


def test_synthetic_agent_probe_requires_exact_report_for_selected_inspection() -> None:
    request = _ProofWorkerRequest.model_validate(
        _request(read_only_files={"tests/test_dashboard.py": "assert True\n"}).model_dump()
    )
    visible = {**_SAFE_ISOLATION_PROBE, "read_only_test_visible": True}
    _verify_isolation_probe(json.dumps(visible), request)
    with pytest.raises(RuntimeError, match="ACP_PROOF_ISOLATION_FAILED"):
        _verify_isolation_probe("{}", request)


def _context(*, create_paths: list[str], delete_paths: list[str]) -> ApprovedExecutionContext:
    return ApprovedExecutionContext(
        handoff_id="handoff-1", request_id="request-1", plan_id="plan-1",
        app_id="proof", build_registry_id="registry-1",
        repository_full_name="org/repo", baseline_commit_sha="a" * 40,
        raw_request="Make the dashboard return 1", request_type="patch",
        approved_plan_digest="b" * 64, snapshot_digest="snapshot-1",
        graph_identity_digest="graph-1", impact_report_digest="impact-1",
        execution_strategy_id="bounded", rationale="Approved test edit",
        allowed_paths=["app/ui/pages/"], create_paths=create_paths,
        delete_paths=delete_paths,
    )


def _snapshot(files: dict[str, str]) -> RepositorySnapshotEvidence:
    return RepositorySnapshotEvidence(
        plan_id="plan-1", request_id="request-1", app_id="proof",
        repository_full_name="org/repo", baseline_commit_sha="a" * 40,
        snapshot_digest="snapshot-1",
        file_manifest={
            path: "sha256:" + hashlib.sha256(content.encode()).hexdigest()
            for path, content in files.items()
        },
    )


def _operation_output(*, path: str, op: str, content: str | None, archive: bytes) -> bytes:
    return json.dumps({
        "proposal": {
            "proposal_id": "raw-provider-id", "provider_id": "raw-provider-name",
            "status": "completed", "summary": "untrusted source quote",
            "rationale": "untrusted source quote", "owned_paths": [path],
            "changed_files": [{"path": path, "op": op, "content": content}],
        },
        "workspace_archive_base64": base64.b64encode(archive).decode(),
    }).encode()


def _worker_output(
    *, archive: bytes | None = None, status: str = "completed", usage: object | None = None,
) -> bytes:
    if archive is None and status == "completed":
        archive = build_deterministic_archive([ArchiveEntry(path=_PATH, content=_AFTER.encode())])
    return json.dumps({
        "proposal": {
            "proposal_id": "raw-provider-id", "provider_id": "raw-provider-name",
            "provider_model": "read-only-source-sentinel",
            "status": status,
            "usage": usage,
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


def _inspect(
    *, network: str = "none", image_env: list[str] | None = None,
    image_id: str = _IMAGE_ID,
) -> bytes:
    return json.dumps([{
        "Image": image_id,
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
    def __init__(
        self, *, output: bytes | None = None, inspect: bytes | None = None,
        image_id: str = _IMAGE_ID,
    ) -> None:
        self.output = output if output is not None else _worker_output()
        self.inspect = inspect if inspect is not None else _inspect()
        self.image_id = image_id
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
            return (self.image_id + "\n").encode()
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
    fake = FakeDocker(output=_worker_output(usage={
        "prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120,
    }))
    _install_fake(monkeypatch, fake)
    result = await execute_repository_docker_turn(_request(), image=_IMAGE, expected_image_id=_IMAGE_ID)

    assert result.proposal.status == "completed"
    assert result.proposal.changed_files[0].content == _AFTER
    assert result.proposal.provider_id == "acp_docker"
    assert result.proposal.proposal_id != "raw-provider-id"
    assert result.proposal.provider_events == []
    assert result.proposal.provider_model is None
    assert result.proposal.usage is None
    assert result.usage is not None
    assert result.usage.model_dump() == {
        "prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120,
    }
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
async def test_expected_image_id_is_required_before_docker_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeDocker()
    _install_fake(monkeypatch, fake)
    with pytest.raises(ValueError, match="REPOSITORY_DOCKER_EXPECTED_IMAGE_ID"):
        await execute_repository_docker_turn(_request(), image=_IMAGE, expected_image_id="latest")
    assert fake.calls == []


@pytest.mark.asyncio
async def test_changed_image_tag_is_rejected_before_create_or_source_transfer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeDocker(image_id="sha256:" + "c" * 64)
    _install_fake(monkeypatch, fake)
    with pytest.raises(RepositoryDockerExecutionError, match="REPOSITORY_DOCKER_IMAGE_ID_MISMATCH"):
        await execute_repository_docker_turn(
            _request(), image=_IMAGE, expected_image_id=_IMAGE_ID,
        )
    assert [args[:2] for args, _, _ in fake.calls] == [["image", "inspect"]]
    assert all(stdin is None for _, stdin, _ in fake.calls)
    assert fake.removed == []


@pytest.mark.asyncio
async def test_created_container_must_use_expected_image_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeDocker(inspect=_inspect(image_id="sha256:" + "c" * 64))
    _install_fake(monkeypatch, fake)
    with pytest.raises(RepositoryDockerExecutionError, match="REPOSITORY_DOCKER_ISOLATION_MISMATCH"):
        await execute_repository_docker_turn(
            _request(), image=_IMAGE, expected_image_id=_IMAGE_ID,
        )
    assert not any(call[0][0] == "start" for call in fake.calls)
    assert len(fake.removed) == 1


@pytest.mark.asyncio
async def test_exact_create_grant_round_trips_through_verified_host_staging(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    archive = build_deterministic_archive([
        ArchiveEntry(path=_NEW_PATH, content=_NEW_CONTENT.encode())
    ])
    fake = FakeDocker(output=_operation_output(
        path=_NEW_PATH, op="create", content=_NEW_CONTENT, archive=archive,
    ))
    _install_fake(monkeypatch, fake)
    context = _context(create_paths=[_NEW_PATH], delete_paths=[])
    snapshot = _snapshot({})
    request = _request(
        files={}, baseline_files={}, build_key="registry-1", target_app_id="proof",
    )
    proven_paths: list[str] = []

    def prove_absence(path: str) -> None:
        proven_paths.append(path)

    turn = await execute_repository_docker_turn(
        request, image=_IMAGE, expected_image_id=_IMAGE_ID, approved_context=context, snapshot=snapshot,
        validate_path=lambda _path: None, validate_create_absence=prove_absence,
    )
    assert proven_paths == [_NEW_PATH]
    assert turn.workspace_archive == archive
    sent = json.loads(fake.calls[3][1])
    assert sent["files"] == {}
    assert sent["create_paths"] == [_NEW_PATH]
    assert sent["delete_paths"] == []
    assert "execution_context" not in sent

    workspace = stage_repository_workspace_archive(
        context, snapshot=snapshot, selected_paths=[], baseline_files={},
        archive_bytes=turn.workspace_archive, workspace_root=tmp_path / "staged",
        validate_path=lambda _path: None, validate_create_absence=prove_absence,
        max_files=50, max_archive_bytes=16_777_216,
    )
    try:
        candidate = finalize_repository_patch(
            context, snapshot=snapshot, selected_paths=[], baseline_files={},
            workspace=workspace, proposal=turn.proposal,
            validate_path=lambda _path: None, validate_create_absence=prove_absence,
        )
    finally:
        workspace.cleanup()
    assert [(file.path, file.op, file.content) for file in candidate.changed_files] == [
        (_NEW_PATH, "create", _NEW_CONTENT)
    ]


@pytest.mark.asyncio
async def test_exact_delete_grant_round_trips_empty_transport(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    fake = FakeDocker(output=_operation_output(
        path=_PATH, op="delete", content=None, archive=_EMPTY_REPOSITORY_ARCHIVE,
    ))
    _install_fake(monkeypatch, fake)
    context = _context(create_paths=[], delete_paths=[_PATH])
    snapshot = _snapshot({_PATH: _BEFORE})
    request = _request(
        files={_PATH: _BEFORE}, baseline_files={_PATH: _BEFORE},
        build_key="registry-1", target_app_id="proof",
    )
    turn = await execute_repository_docker_turn(
        request, image=_IMAGE, expected_image_id=_IMAGE_ID, approved_context=context, snapshot=snapshot,
        validate_path=lambda _path: None,
    )
    assert turn.workspace_archive == _EMPTY_REPOSITORY_ARCHIVE
    sent = json.loads(fake.calls[3][1])
    assert sent["create_paths"] == []
    assert sent["delete_paths"] == [_PATH]

    workspace = stage_repository_workspace_archive(
        context, snapshot=snapshot, selected_paths=[_PATH], baseline_files=request.files,
        archive_bytes=turn.workspace_archive, workspace_root=tmp_path / "staged",
        validate_path=lambda _path: None, max_files=50, max_archive_bytes=16_777_216,
    )
    try:
        candidate = finalize_repository_patch(
            context, snapshot=snapshot, selected_paths=[_PATH], baseline_files=request.files,
            workspace=workspace, proposal=turn.proposal, validate_path=lambda _path: None,
        )
    finally:
        workspace.cleanup()
    assert [(file.path, file.op, file.content) for file in candidate.changed_files] == [
        (_PATH, "delete", None)
    ]


@pytest.mark.asyncio
async def test_create_without_complete_baseline_proof_never_starts_docker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeDocker()
    _install_fake(monkeypatch, fake)
    context = _context(create_paths=[_NEW_PATH], delete_paths=[])
    request = _request(files={}, baseline_files={}, build_key="registry-1")
    with pytest.raises(ValueError, match="REPOSITORY_PATCH_CREATE_PROOF"):
        await execute_repository_docker_turn(
            request, image=_IMAGE, expected_image_id=_IMAGE_ID, approved_context=context, snapshot=_snapshot({}),
            validate_path=lambda _path: None,
        )
    assert fake.calls == []


@pytest.mark.asyncio
async def test_changed_baseline_or_unapproved_inspection_never_reaches_docker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeDocker()
    _install_fake(monkeypatch, fake)
    context = _context(create_paths=[], delete_paths=[])
    snapshot = _snapshot({_PATH: _BEFORE})
    wrong_baseline = _request(
        files={_PATH: _AFTER}, build_key="registry-1", target_app_id="proof",
    )
    with pytest.raises(ValueError, match="REPOSITORY_PATCH_BASELINE_HASH"):
        await execute_repository_docker_turn(
            wrong_baseline, image=_IMAGE, expected_image_id=_IMAGE_ID, approved_context=context, snapshot=snapshot,
            validate_path=lambda _path: None,
        )
    unapproved_inspection = _request(
        files={_PATH: _BEFORE}, read_only_files={"docs/private.md": "private"},
        build_key="registry-1", target_app_id="proof",
    )
    with pytest.raises(ValueError, match="REPOSITORY_PATCH_INSPECTION_SCOPE"):
        await execute_repository_docker_turn(
            unapproved_inspection, image=_IMAGE, expected_image_id=_IMAGE_ID, approved_context=context, snapshot=snapshot,
            validate_path=lambda _path: None,
        )
    assert fake.calls == []


@pytest.mark.asyncio
async def test_ungranted_create_output_rejected_after_container_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = build_deterministic_archive([
        ArchiveEntry(path=_PATH, content=_BEFORE.encode()),
        ArchiveEntry(path=_NEW_PATH, content=_NEW_CONTENT.encode()),
    ])
    fake = FakeDocker(output=_operation_output(
        path=_NEW_PATH, op="create", content=_NEW_CONTENT, archive=archive,
    ))
    _install_fake(monkeypatch, fake)
    with pytest.raises(RepositoryDockerExecutionError, match="REPOSITORY_DOCKER_INVALID_OUTPUT"):
        await execute_repository_docker_turn(_request(), image=_IMAGE, expected_image_id=_IMAGE_ID)
    assert len(fake.removed) == 1


@pytest.mark.asyncio
async def test_isolation_mismatch_stops_before_start_and_removes_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeDocker(inspect=_inspect(network="bridge"))
    _install_fake(monkeypatch, fake)
    with pytest.raises(RepositoryDockerExecutionError, match="REPOSITORY_DOCKER_ISOLATION_MISMATCH"):
        await execute_repository_docker_turn(_request(), image=_IMAGE, expected_image_id=_IMAGE_ID)
    assert not any(call[0][0] == "start" for call in fake.calls)
    assert len(fake.removed) == 1


@pytest.mark.asyncio
async def test_image_baked_model_credential_is_rejected_before_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeDocker(inspect=_inspect(image_env=["ANTHROPIC_API_KEY=private"] ))
    _install_fake(monkeypatch, fake)
    with pytest.raises(RepositoryDockerExecutionError, match="REPOSITORY_DOCKER_ISOLATION_MISMATCH"):
        await execute_repository_docker_turn(_request(), image=_IMAGE, expected_image_id=_IMAGE_ID)
    assert not any(call[0][0] == "start" for call in fake.calls)
    assert len(fake.removed) == 1


@pytest.mark.asyncio
async def test_unknown_image_environment_key_is_rejected_before_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeDocker(inspect=_inspect(image_env=["CUSTOM_PROVIDER_TOKEN=private"]))
    _install_fake(monkeypatch, fake)
    with pytest.raises(RepositoryDockerExecutionError, match="REPOSITORY_DOCKER_ISOLATION_MISMATCH"):
        await execute_repository_docker_turn(
            _request(), image=_IMAGE, expected_image_id=_IMAGE_ID,
        )
    assert not any(call[0][0] == "start" for call in fake.calls)
    assert len(fake.removed) == 1


@pytest.mark.asyncio
async def test_known_offline_image_environment_keys_are_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeDocker(inspect=_inspect(image_env=[
        "PATH=/usr/bin", "NODE_VERSION=24", "PYTHONPATH=/opt/mozaiks",
        "LANG=C.UTF-8", "GPG_KEY=" + "a" * 40,
        "MOZAIKS_FACTORY_APP_PATH=/opt/mozaiks/factory_app",
    ]))
    _install_fake(monkeypatch, fake)
    turn = await execute_repository_docker_turn(
        _request(), image=_IMAGE, expected_image_id=_IMAGE_ID,
    )
    assert turn.proposal.status == "completed"


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
        await execute_repository_docker_turn(_request(), image=_IMAGE, expected_image_id=_IMAGE_ID)
    assert len(fake.removed) == 1


@pytest.mark.asyncio
async def test_failed_turn_has_no_archive_or_source_quoting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeDocker(output=_worker_output(status="rejected_scope", usage={
        "prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120,
    }))
    _install_fake(monkeypatch, fake)
    result = await execute_repository_docker_turn(_request(), image=_IMAGE, expected_image_id=_IMAGE_ID)
    assert result.workspace_archive is None
    assert result.proposal.status == "rejected_scope"
    assert result.proposal.error == "Isolated coding turn reported rejected_scope."
    assert result.usage is not None and result.usage.total_tokens == 120
    assert "read-only-source-sentinel" not in result.proposal.model_dump_json()


@pytest.mark.asyncio
async def test_malformed_container_usage_does_not_invalidate_patch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeDocker(output=_worker_output(usage={"prompt_tokens": "source-secret"}))
    _install_fake(monkeypatch, fake)
    result = await execute_repository_docker_turn(_request(), image=_IMAGE, expected_image_id=_IMAGE_ID)
    assert result.proposal.status == "completed"
    assert result.usage is None
    assert "source-secret" not in result.proposal.model_dump_json()


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
        await execute_repository_docker_turn(_request(), image=_IMAGE, expected_image_id=_IMAGE_ID)
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
        await execute_repository_docker_turn(_request(), image=_IMAGE, expected_image_id=_IMAGE_ID)
    assert len(fake.removed) == 1
    assert fake.calls[1][0][fake.calls[1][0].index("--name") + 1] == fake.removed[0]


@pytest.mark.asyncio
async def test_input_budget_rejects_before_container_creation(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeDocker()
    _install_fake(monkeypatch, fake)
    monkeypatch.setattr(executor, "MAX_REPOSITORY_DOCKER_REQUEST_BYTES", 100)
    with pytest.raises(ValueError, match="REPOSITORY_DOCKER_REQUEST_BUDGET"):
        await execute_repository_docker_turn(_request(), image=_IMAGE, expected_image_id=_IMAGE_ID)
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
    assert _local_docker_endpoint().startswith("npipe:" if os.name == "nt" else "unix:")


@pytest.mark.skipif(
    os.getenv("MOZAIKS_RUN_REPOSITORY_DOCKER_PROOF") != "1",
    reason="opt-in offline Docker ACP proof",
)
@pytest.mark.asyncio
async def test_real_offline_proof_image_uses_host_executor() -> None:
    result = await execute_repository_docker_turn(
        _request(baseline_files={_PATH: _BEFORE}, metadata={}, context_seed={}, user_id=None),
        image=_IMAGE,
        expected_image_id=os.environ["MOZAIKS_REPOSITORY_DOCKER_PROOF_IMAGE_ID"],
        max_wall_seconds=75, max_archive_bytes=700_000,
    )
    assert result.proposal.status == "completed"
    assert result.proposal.changed_files[0].content == _AFTER
    assert result.workspace_archive is not None


@pytest.mark.skipif(
    os.getenv("MOZAIKS_RUN_REPOSITORY_DOCKER_PROOF") != "1",
    reason="opt-in offline Docker ACP proof",
)
@pytest.mark.parametrize("operation", ["create", "delete"])
@pytest.mark.asyncio
async def test_real_offline_proof_image_executes_exact_operation_grant(tmp_path, operation: str) -> None:
    create_paths = [_NEW_PATH] if operation == "create" else []
    delete_paths = [_PATH] if operation == "delete" else []
    selected_files = {_PATH: _BEFORE} if operation == "delete" else {}
    context = _context(create_paths=create_paths, delete_paths=delete_paths)
    snapshot = _snapshot(selected_files)
    request = _request(
        files=selected_files, baseline_files=selected_files,
        build_key="registry-1", target_app_id="proof",
    )
    def prove_absence(_path: str) -> None:
        return None

    turn = await execute_repository_docker_turn(
        request, image=_IMAGE,
        expected_image_id=os.environ["MOZAIKS_REPOSITORY_DOCKER_PROOF_IMAGE_ID"],
        approved_context=context, snapshot=snapshot,
        validate_path=lambda _path: None,
        validate_create_absence=prove_absence if create_paths else None,
        max_wall_seconds=75, max_archive_bytes=700_000,
    )
    assert turn.proposal.status == "completed"
    assert [(change.path, change.op, change.content) for change in turn.proposal.changed_files] == [
        (_NEW_PATH, "create", _NEW_CONTENT) if operation == "create" else (_PATH, "delete", None),
    ]

    workspace = stage_repository_workspace_archive(
        context, snapshot=snapshot, selected_paths=list(selected_files),
        baseline_files=selected_files, archive_bytes=turn.workspace_archive,
        workspace_root=tmp_path / "staged", validate_path=lambda _path: None,
        validate_create_absence=prove_absence if create_paths else None,
        max_files=3, max_archive_bytes=700_000,
    )
    try:
        candidate = finalize_repository_patch(
            context, snapshot=snapshot, selected_paths=list(selected_files),
            baseline_files=selected_files, workspace=workspace, proposal=turn.proposal,
            validate_path=lambda _path: None,
            validate_create_absence=prove_absence if create_paths else None,
        )
    finally:
        workspace.cleanup()
    assert [(change.path, change.op) for change in candidate.changed_files] == [
        (_NEW_PATH, "create") if operation == "create" else (_PATH, "delete"),
    ]
