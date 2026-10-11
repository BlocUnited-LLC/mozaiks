"""Opt-in real-Docker gates for the live repository ACP transport.

These cover ADR 0015 acceptance gates 1-3 against the real transport, real
container and network settings, and images derived from the pinned live
worker and model gateway images. They never contact a model provider: the
worker is the scripted ``infra/docker/acp_live_gates/gate_worker.py`` and the
gateway runs the real gateway code against an in-container fake upstream
(``fake_gateway.py``). The upstream credential is a random canary.

Never run in CI or by default. To run locally on a Linux Docker engine:

    MOZAIKS_RUN_REPOSITORY_LIVE_GATES=1 \
    MOZAIKS_REPOSITORY_LIVE_WORKER_IMAGE_ID=sha256:... \
    MOZAIKS_REPOSITORY_LIVE_GATEWAY_IMAGE_ID=sha256:... \
    python -m pytest -q --no-cov --reruns 0 -s tests/test_repository_live_docker_gates.py
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import subprocess
import uuid
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

import mozaiksai
from mozaiksai.control_plane import (
    ApprovedExecutionContext,
    CodingWorkerRequest,
    RepositoryDockerExecutionError,
    RepositoryLiveACPProfile,
    RepositorySnapshotEvidence,
    execute_repository_docker_turn,
)
from mozaiksai.control_plane import repository_live_docker as live
from mozaiksai.control_plane.repository_patch import (
    _EMPTY_REPOSITORY_ARCHIVE,
    finalize_repository_patch,
    repository_patch_digest,
    stage_repository_workspace_archive,
)

pytestmark = pytest.mark.skipif(
    os.getenv("MOZAIKS_RUN_REPOSITORY_LIVE_GATES") != "1",
    reason="opt-in real Docker live ACP gates",
)

_ROOT = Path(__file__).resolve().parents[1]
_GATES = _ROOT / "infra" / "docker" / "acp_live_gates"
_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
_EDIT = "app/gate/Probe.md"
_BEFORE = "# Probe\n"
_NEW = "app/gate/New.md"
_READ_ONLY = "docs/gate/Readme.md"
_READ_ONLY_CONTENT = "# Read only\n"
_MODEL = "claude-gate-model"
_NO_ROUTE = {"dns_failed", "unreachable", "timeout"}
_WORKER_LABEL = "label=mozaiks.refinement.repository_turn.worker=live"
_GATEWAY_LABEL = "label=mozaiks.refinement.repository_turn.gateway=live"


def _docker(*args: str, timeout: int = 600, check: bool = True) -> str:
    completed = subprocess.run(
        ["docker", *args], capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=timeout, check=False,
    )
    if check and completed.returncode != 0:
        raise AssertionError(f"docker {args[0]} failed: {completed.stderr[-2000:]}")
    return completed.stdout


def _leftovers() -> dict[str, list[str]]:
    return {
        "worker_containers": _docker("ps", "-aq", "--filter", _WORKER_LABEL).split(),
        "gateway_containers": _docker("ps", "-aq", "--filter", _GATEWAY_LABEL).split(),
        "networks": _docker("network", "ls", "-q", "--filter", "name=mozaiks-acp-").split(),
    }


@pytest.fixture(scope="module")
def gate_images(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, tuple[str, str]]]:
    """Derive the scripted worker and fake gateway from the pinned images."""
    assert Path(mozaiksai.__file__).resolve().is_relative_to(_ROOT), "mozaiksai resolves outside this checkout"
    assert Path(live.__file__).resolve().is_relative_to(_ROOT)
    bases = {
        "worker": os.environ.get("MOZAIKS_REPOSITORY_LIVE_WORKER_IMAGE_ID", ""),
        "gateway": os.environ.get("MOZAIKS_REPOSITORY_LIVE_GATEWAY_IMAGE_ID", ""),
    }
    for role, image_id in bases.items():
        if not _IMAGE_ID.fullmatch(image_id):
            pytest.fail(f"set the {role} image ID as sha256:<64 hex>")
    recipes = {
        "worker": ("gate_worker.py", "/opt/mozaiks/acp_live_gates/gate_worker.py", ["python"]),
        "gateway": ("fake_gateway.py", "/app/fake_gateway.py", ["python", "-I"]),
    }
    suffix = uuid.uuid4().hex[:12]
    tags: list[str] = []
    images: dict[str, tuple[str, str]] = {}
    try:
        for role, (script, target, command) in recipes.items():
            base_tag = f"mozaiks-acp-gate-base-{role}:{suffix}"
            _docker("tag", bases[role], base_tag)
            tags.append(base_tag)
            context = tmp_path_factory.mktemp(f"gate-{role}")
            shutil.copyfile(_GATES / script, context / script)
            entrypoint = json.dumps([*command, target])
            (context / "Dockerfile").write_text(
                f"FROM {base_tag}\nCOPY {script} {target}\nENTRYPOINT {entrypoint}\n", encoding="utf-8",
            )
            derived = f"mozaiks-acp-gate-{role}:{suffix}"
            _docker("build", "-q", "-t", derived, str(context))
            tags.append(derived)
            images[role] = (derived, _docker("image", "inspect", "--format", "{{.Id}}", derived).strip())
        images["bases"] = (bases["worker"], bases["gateway"])
        yield images
    finally:
        for tag in reversed(tags):
            _docker("image", "rm", tag, check=False)


@pytest.fixture(autouse=True)
def no_transport_leftovers() -> Iterator[None]:
    assert _leftovers() == {"worker_containers": [], "gateway_containers": [], "networks": []}
    yield
    assert _leftovers() == {"worker_containers": [], "gateway_containers": [], "networks": []}


def _sha(content: str) -> str:
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


class _Turn:
    """One approved gate turn: request, approval, snapshot and host path policy."""

    def __init__(
        self, mode: str, *, probe: bool = False, hold_seconds: float = 0,
        create: bool = False, delete: bool = False,
    ) -> None:
        self.script = json.dumps(
            {"gate_mode": mode, "probe": probe, "hold_seconds": hold_seconds}, sort_keys=True,
        )
        self.files = {} if create else {_EDIT: _BEFORE}
        self.read_only = {_READ_ONLY: _READ_ONLY_CONTENT}
        self.create_paths = [_NEW] if create else []
        self.delete_paths = [_EDIT] if delete else []
        self.approved = set(self.files) | set(self.read_only) | set(self.create_paths)
        self.context = ApprovedExecutionContext(
            handoff_id="gate-handoff", request_id="gate-request", plan_id="gate-plan",
            app_id="gate", build_registry_id="gate-registry",
            repository_full_name="throwaway/gate", baseline_commit_sha="c" * 40,
            raw_request=self.script, request_type="patch",
            approved_plan_digest="d" * 64, snapshot_digest="gate-snapshot",
            graph_identity_digest="gate-graph", impact_report_digest="gate-impact",
            execution_strategy_id="gate", rationale="Opt-in Docker gate",
            allowed_paths=["app/gate/", "docs/gate/"], read_only_paths=["docs/gate/"],
            create_paths=self.create_paths, delete_paths=self.delete_paths,
        )
        self.snapshot = RepositorySnapshotEvidence(
            plan_id="gate-plan", request_id="gate-request", app_id="gate",
            repository_full_name="throwaway/gate", baseline_commit_sha="c" * 40,
            snapshot_digest="gate-snapshot",
            file_manifest={path: _sha(content) for path, content in {**self.files, **self.read_only}.items()},
        )
        self.request = CodingWorkerRequest(
            app_id="gate", target_app_id="gate", build_family="app_bundle", build_key="gate-registry",
            build_record_id="gate-record", change_class="patch", raw_user_request=self.script,
            files=self.files, read_only_files=self.read_only,
        )

    def validate_path(self, path: str) -> None:
        if path not in self.approved:
            raise ValueError("gate path outside the approved set")

    def prove_absence(self, path: str) -> None:
        if path not in self.create_paths:
            raise ValueError("gate create path not granted")

    async def run(
        self, images: dict[str, tuple[str, str]], *, credential: str, max_wall_seconds: int = 90,
    ) -> Any:
        worker_tag, worker_id = images["worker"]
        gateway_tag, gateway_id = images["gateway"]
        return await execute_repository_docker_turn(
            self.request, image=worker_tag, expected_image_id=worker_id,
            approved_context=self.context, snapshot=self.snapshot,
            validate_path=self.validate_path,
            validate_create_absence=self.prove_absence if self.create_paths else None,
            live_profile=RepositoryLiveACPProfile(
                adapter="claude_code", model=_MODEL, gateway_image=gateway_tag,
                gateway_expected_image_id=gateway_id, upstream_api_key=SecretStr(credential),
            ),
            max_wall_seconds=max_wall_seconds, max_archive_bytes=2_000_000,
        )

    def finalize(self, turn: Any, root: Path) -> Any:
        workspace = stage_repository_workspace_archive(
            self.context, snapshot=self.snapshot, selected_paths=list(self.files),
            baseline_files=self.files, archive_bytes=turn.workspace_archive,
            workspace_root=root, validate_path=self.validate_path,
            validate_create_absence=self.prove_absence if self.create_paths else None,
            max_files=3, max_archive_bytes=2_000_000,
        )
        try:
            return finalize_repository_patch(
                self.context, snapshot=self.snapshot, selected_paths=list(self.files),
                baseline_files=self.files, workspace=workspace, proposal=turn.proposal,
                validate_path=self.validate_path,
                validate_create_absence=self.prove_absence if self.create_paths else None,
            )
        finally:
            workspace.cleanup()


def _canary() -> str:
    return "mozaiks-gate-canary-" + secrets.token_hex(16)


def _archive_text(archive: bytes | None) -> str:
    if archive is None or archive == _EMPTY_REPOSITORY_ARCHIVE:
        return ""
    with zipfile.ZipFile(io.BytesIO(archive)) as opened:
        return "".join(opened.read(name).decode("utf-8") for name in opened.namelist())


async def _watch_running_turn(captured: dict[str, str], stop: asyncio.Event) -> None:
    """Inspect both live containers and both networks while the worker runs."""
    while not stop.is_set():
        workers = (await asyncio.to_thread(
            _docker, "ps", "-q", "--filter", _WORKER_LABEL, "--filter", "status=running",
        )).split()
        gateways = (await asyncio.to_thread(
            _docker, "ps", "-q", "--filter", _GATEWAY_LABEL, "--filter", "status=running",
        )).split()
        networks = (await asyncio.to_thread(
            _docker, "network", "ls", "-q", "--filter", "name=mozaiks-acp-",
        )).split()
        if len(workers) == 1 and len(gateways) == 1 and len(networks) == 2:
            captured["containers"] = await asyncio.to_thread(_docker, "inspect", workers[0], gateways[0])
            captured["networks"] = await asyncio.to_thread(_docker, "network", "inspect", *networks)
            captured["worker_top"] = await asyncio.to_thread(_docker, "top", workers[0], "-eo", "pid,args")
            captured["gateway_top"] = await asyncio.to_thread(_docker, "top", gateways[0], "-eo", "pid,args")
            return
        await asyncio.sleep(0.2)


@pytest.mark.asyncio
async def test_gate_1_and_2_network_reach_and_canary_containment(
    gate_images: dict[str, tuple[str, str]], monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    canary = _canary()
    observed: dict[str, Any] = {}
    start_gateway = live._start_gateway
    parse_output = live._parse_turn_output

    async def capture_token(container_id: str, *, config_dir: str, config: dict[str, str]) -> Any:
        observed["job_token"] = config["job_token"]
        return await start_gateway(container_id, config_dir=config_dir, config=config)

    def capture_output(raw: bytes, **kwargs: Any) -> Any:
        observed["raw_output"] = raw
        return parse_output(raw, **kwargs)

    monkeypatch.setattr(live, "_start_gateway", capture_token)
    monkeypatch.setattr(live, "_parse_turn_output", capture_output)
    turn_spec = _Turn("update", probe=True, hold_seconds=8)
    inspected: dict[str, str] = {}
    stop = asyncio.Event()
    watcher = asyncio.create_task(_watch_running_turn(inspected, stop))
    try:
        turn = await turn_spec.run(gate_images, credential=canary)
    finally:
        stop.set()
        await watcher

    assert turn.proposal.status == "completed"
    report = json.loads(turn.proposal.changed_files[0].content)
    print("\nGATE1_FORBIDDEN", json.dumps(report["forbidden"], sort_keys=True))
    print("GATE1_TARGETS", json.dumps(report["forbidden_targets"], sort_keys=True))
    print("GATE1_GATEWAY", json.dumps(report["gateway"], sort_keys=True), report["gateway_tcp"])
    print("GATE1_ROUTES", json.dumps(report["routes"], sort_keys=True))

    # Gate 1: only the gateway alias is reachable; the gateway enforces its route.
    assert report["gateway_tcp"] == "connected"
    assert report["targets"]["egress_gateway_ip"] and report["targets"]["gateway_egress_ip"]
    assert {"egress_bridge_gateway_http", "gateway_egress_address", "metadata_http",
            "host_docker_internal_http", "internet_ip_https"} <= set(report["forbidden"])
    reachable = {name: outcome for name, outcome in report["forbidden"].items() if outcome not in _NO_ROUTE}
    assert reachable == {}
    assert report["routes"]["default_route"] is False
    assert report["gateway"] == {
        "approved_call": 200, "wrong_model": 403, "wrong_token": 401, "no_token": 401,
        "unknown_route": 404, "get_method": 404, "targets_without_token": 401, "targets_status": 200,
    }
    assert report["targets"]["credential_at_upstream"] is True  # canary reached only the fake upstream

    # The running networks: an isolated internal bridge with no host-side
    # gateway address holding exactly both containers, and a separate egress
    # bridge holding only the gateway.
    assert inspected.keys() == {"containers", "networks", "worker_top", "gateway_top"}
    networks = {item["Internal"]: item for item in json.loads(inspected["networks"])}
    private, egress = networks[True], networks[False]
    assert private["Options"].get("com.docker.network.bridge.gateway_mode_ipv4") == "isolated"
    assert all(not config.get("Gateway") for config in private["IPAM"]["Config"])
    assert len(private["Containers"]) == 2 and len(egress["Containers"]) == 1
    assert set(egress["Containers"]) < set(private["Containers"])
    print("GATE1_NETWORKS", json.dumps({
        "private_ipam": private["IPAM"]["Config"], "egress_ipam": egress["IPAM"]["Config"],
    }, sort_keys=True))

    # Gate 2: the canary credential and the job token stay out of every surface.
    images = _docker("image", "inspect", *gate_images["worker"][1:], *gate_images["gateway"][1:],
                     *gate_images["bases"])
    job_token = observed["job_token"]
    surfaces = {
        "container_inspect": inspected["containers"],
        "network_inspect": inspected["networks"],
        "process_args": inspected["worker_top"] + inspected["gateway_top"],
        "image_inspect": images,
        "worker_environment": json.dumps(report["environment"]),
        "worker_process": json.dumps(report["process"]),
        "raw_output": observed["raw_output"].decode("utf-8"),
        "proposal": turn.proposal.model_dump_json(),
        "archive": _archive_text(turn.workspace_archive),
    }
    assert [name for name, text in surfaces.items() if canary in text] == []
    assert [name for name in ("container_inspect", "network_inspect", "process_args", "image_inspect")
            if job_token in surfaces[name]] == []
    credential_names = {"OPENAI_API_KEY", "CODEX_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                        "CLAUDE_CODE_OAUTH_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"}
    assert credential_names.isdisjoint(report["environment"])
    assert report["process"]["uid"] == 10001

    candidate = turn_spec.finalize(turn, tmp_path / "staged")
    assert [(change.path, change.op) for change in candidate.changed_files] == [(_EDIT, "update")]
    assert repository_patch_digest(candidate).startswith("sha256:")
    assert turn.usage is not None and turn.usage.total_tokens == 5


@pytest.mark.asyncio
@pytest.mark.parametrize(("mode", "operation"), [
    ("update", "update"), ("create", "create"), ("delete", "delete"),
])
async def test_gate_3_exact_operations_round_trip_through_host_staging(
    gate_images: dict[str, tuple[str, str]], tmp_path: Path, mode: str, operation: str,
) -> None:
    turn_spec = _Turn(mode, create=mode == "create", delete=mode == "delete")
    turn = await turn_spec.run(gate_images, credential=_canary())
    candidate = turn_spec.finalize(turn, tmp_path / "staged")
    expected_path = _NEW if mode == "create" else _EDIT
    assert [(change.path, change.op) for change in candidate.changed_files] == [(expected_path, operation)]
    assert repository_patch_digest(candidate).startswith("sha256:")


@pytest.mark.asyncio
@pytest.mark.parametrize(("mode", "code"), [
    ("read_only_mutation", "REPOSITORY_DOCKER_INVALID_OUTPUT"),
    ("out_of_scope", "REPOSITORY_DOCKER_INVALID_OUTPUT"),
    ("malformed", "REPOSITORY_DOCKER_INVALID_OUTPUT"),
    ("token_copy", "REPOSITORY_LIVE_TOKEN_IN_OUTPUT"),
    ("crash", "REPOSITORY_(DOCKER_COMMAND|LIVE_WORKER)_FAILED"),
])
async def test_gate_3_invalid_or_failed_turns_fail_closed_and_clean_up(
    gate_images: dict[str, tuple[str, str]], mode: str, code: str,
) -> None:
    with pytest.raises(RepositoryDockerExecutionError, match=code):
        await _Turn(mode).run(gate_images, credential=_canary())


@pytest.mark.asyncio
async def test_gate_3_timeout_fails_closed_and_clean_up(gate_images: dict[str, tuple[str, str]]) -> None:
    with pytest.raises(RepositoryDockerExecutionError, match="REPOSITORY_DOCKER_TIMEOUT"):
        await _Turn("hang").run(gate_images, credential=_canary(), max_wall_seconds=5)
