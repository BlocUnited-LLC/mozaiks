"""Sandbox boundary + persistence guards.

Covers the fixes that make sandbox sessions attributable, self-terminating,
and durable: docker preview-port publishing, validation-session identity on
results, BuildRecord's first-class validation fields, and the coding
worker's honest strategy vocabulary.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from factory_app.workflows.AppGenerator.tools import app_validation as app_validation_module
from factory_app.workflows.AppGenerator.tools.app_validation import (
    _run_sandbox_validation,
)
from mozaiksai.control_plane.contracts import CodingWorkerPlan
from mozaiksai.control_plane.implementations.coding_worker import resolve_coding_validation_strategy
from mozaiksai.core.adapters.docker_sandbox import (
    DockerSandboxAdapter,
    _preview_ports,
)
from mozaiksai.core.adapters.local_docker_cli import (
    _docker_cli_env,
    _docker_prefix,
    _local_docker_endpoint,
)
from mozaiksai.core.artifacts.models import BuildRecord
from mozaiksai.core.ports.sandbox import SandboxRunResult, SandboxSessionInfo
from mozaiksai.core.sandbox.preview_sessions import (
    preview_resource_environment,
    sandbox_resource_environment,
)
from mozaiksai.core.workflow.generator_support.app_validation_strategy import (
    APP_VALIDATION_STRATEGIES,
)

# ---------------------------------------------------------------------------
# Docker adapter publishes preview ports
# ---------------------------------------------------------------------------

def test_preview_ports_default_and_env(monkeypatch):
    monkeypatch.delenv("SANDBOX_PREVIEW_PORT", raising=False)
    assert _preview_ports() == [3000, 8000]
    monkeypatch.setenv("SANDBOX_PREVIEW_PORT", "5173")
    assert _preview_ports() == [5173, 3000, 8000]
    monkeypatch.setenv("SANDBOX_PREVIEW_PORT", "8000")
    assert _preview_ports() == [8000, 3000]


@pytest.mark.asyncio
async def test_docker_create_session_publishes_preview_ports(monkeypatch):
    monkeypatch.delenv("SANDBOX_PREVIEW_PORT", raising=False)
    adapter = DockerSandboxAdapter()
    captured: dict[str, list[str]] = {}

    async def fake_run(args, timeout: float = 60.0):
        captured["args"] = list(args)
        return 0, "container-id-123\n", ""

    with patch.object(adapter, "_run", side_effect=fake_run):
        session = await adapter.create_session()

    args = captured["args"]
    assert session.session_id == "container-id-123"
    assert "-p" in args
    port_bindings = [args[i + 1] for i, a in enumerate(args) if a == "-p"]
    assert "127.0.0.1:0:3000" in port_bindings
    assert "127.0.0.1:0:8000" in port_bindings


@pytest.mark.asyncio
async def test_docker_validation_session_has_no_network_or_published_ports(monkeypatch):
    monkeypatch.setenv("SANDBOX_PREVIEW_PORT", "invalid-preview-port")
    adapter = DockerSandboxAdapter()
    captured: dict[str, list[str]] = {}

    async def fake_run(args, timeout: float = 60.0):
        captured["args"] = list(args)
        return 0, "validation-container\n", ""

    with patch.object(adapter, "_run", side_effect=fake_run):
        session = await adapter.create_session(metadata={"purpose": "app_validation"})

    args = captured["args"]
    assert session.session_id == "validation-container"
    assert args[args.index("--network") + 1] == "none"
    assert "-p" not in args


@pytest.mark.asyncio
async def test_app_runtime_diagnostic_has_bounded_writable_space(monkeypatch):
    monkeypatch.setenv("SANDBOX_PREVIEW_PORT", "invalid-preview-port")
    adapter = DockerSandboxAdapter()
    captured: dict[str, list[str]] = {}

    async def fake_run(args, timeout: float = 60.0):
        captured["args"] = list(args)
        return 0, "diagnostic-container\n", ""

    with patch.object(adapter, "_run", side_effect=fake_run):
        session = await adapter.create_session(metadata={"purpose": "app_runtime_diagnostic"})

    args = captured["args"]
    assert session.session_id == "diagnostic-container"
    assert args[args.index("--network") + 1] == "none"
    assert "-p" not in args
    assert "--read-only" in args
    assert "--log-driver=none" in args
    assert args[args.index("--user") + 1] == "10001:10001"
    assert "--memory-swap=2g" in args
    tmpfs = [args[i + 1] for i, arg in enumerate(args) if arg == "--tmpfs"]
    assert any(mount.startswith("/workspace:") and "size=128m" in mount for mount in tmpfs)
    assert any(mount.startswith("/tmp:") and "size=64m" in mount for mount in tmpfs)
    assert any(mount.startswith("/home/sandbox:") and "size=16m" in mount for mount in tmpfs)


@pytest.mark.asyncio
async def test_docker_termination_requires_confirmed_absence():
    adapter = DockerSandboxAdapter()
    responses = [(0, "container-id\n", ""), (0, "container-id\n", ""),
                 (0, "container-id\n", ""), (0, "container-id\n", ""),
                 (0, "container-id\n", ""), (0, "container-id\n", "")]

    async def fake_run(args, timeout: float = 60.0):
        return responses.pop(0)

    with patch.object(adapter, "_run", side_effect=fake_run):
        assert await adapter.terminate_session(session_id="container-id") is False

    responses = [(0, "container-id\n", ""), (0, "container-id\n", ""), (0, "", "")]
    with patch.object(adapter, "_run", side_effect=fake_run):
        assert await adapter.terminate_session(session_id="container-id") is True

    responses = [(0, "container-id\n", ""), (0, "container-id\n", ""),
                 (0, "container-id\n", ""), (0, "container-id\n", ""),
                 (0, "container-id\n", ""), (0, "", "")]
    with patch.object(adapter, "_run", side_effect=fake_run):
        assert await adapter.terminate_session(session_id="container-id") is True


@pytest.mark.asyncio
async def test_docker_commands_ignore_remote_context_and_host_credentials(monkeypatch):
    monkeypatch.setenv("DOCKER_CONTEXT", "remote")
    monkeypatch.setenv("DOCKER_HOST", "tcp://remote.example:2375")
    monkeypatch.setenv("OPENAI_API_KEY", "host-secret")
    assert "DOCKER_CONTEXT" not in _docker_cli_env()
    assert "DOCKER_HOST" not in _docker_cli_env()
    assert "OPENAI_API_KEY" not in _docker_cli_env()
    assert _docker_prefix("isolated-config")[-2:] == ["--host", _local_docker_endpoint()]

    process = AsyncMock()
    process.returncode = 0
    process.stdout.read.side_effect = [b"ok", b""]
    process.stderr.read.return_value = b""
    process.wait.return_value = 0
    with patch("asyncio.create_subprocess_exec", return_value=process) as launch:
        rc, stdout, stderr = await DockerSandboxAdapter._run(["docker", "info"])
    assert (rc, stdout, stderr) == (0, "ok", "")
    args = launch.call_args.args
    assert args[:3] == ("docker", "--config", args[2])
    assert args[3:5] == ("--host", _local_docker_endpoint())
    assert args[-1] == "info"
    assert launch.call_args.kwargs["env"] == _docker_cli_env()

    process.wait.return_value = 0
    with patch("asyncio.create_subprocess_exec", return_value=process) as launch:
        result = await DockerSandboxAdapter().run_command(
            session_id="validation-container", command="true", background=True,
        )
    assert result.success
    assert launch.call_args.args[3:5] == ("--host", _local_docker_endpoint())
    assert launch.call_args.kwargs["env"] == _docker_cli_env()


# ---------------------------------------------------------------------------
# Validation results carry session identity; sessions carry metadata
# ---------------------------------------------------------------------------

class _FakeAdapter:
    def __init__(self) -> None:
        self.create_kwargs: dict[str, Any] = {}
        self.terminated: list[str] = []

    async def create_session(self, **kwargs) -> SandboxSessionInfo:
        self.create_kwargs = kwargs
        return SandboxSessionInfo(session_id="sess-1", provider="e2b")

    async def write_files(self, **kwargs) -> None:
        return None

    async def run_command(self, **kwargs) -> SandboxRunResult:
        return SandboxRunResult(success=True, exit_code=0, stdout="ok", stderr="")

    async def get_preview_url(self, **kwargs) -> str | None:
        return "https://sess-1.example.dev"

    async def terminate_session(self, *, session_id: str) -> bool:
        self.terminated.append(session_id)
        return True


@pytest.mark.asyncio
async def test_server_timeout_caps_e2b_validation_allocation(monkeypatch):
    monkeypatch.setenv("E2B_TIMEOUT", "120")
    fake = _FakeAdapter()
    with patch("mozaiksai.core.adapters.get_sandbox_adapter", return_value=fake):
        result = await _run_sandbox_validation(
            strategy="e2b", resolved_files={"app.json": "{}"}, commands=["true"],
            start_dev_server=False, timeout_seconds=9999,
        )
    assert fake.create_kwargs["timeout_seconds"] == 120
    assert result["sandbox_terminated"] is True


@pytest.mark.asyncio
async def test_validation_cleanup_failure_blocks_success_and_never_returns_dead_preview():
    fake = _FakeAdapter()
    fake.terminate_session = AsyncMock(side_effect=RuntimeError("provider credential must stay private"))
    with patch("mozaiksai.core.adapters.get_sandbox_adapter", return_value=fake):
        result = await _run_sandbox_validation(
            strategy="e2b", resolved_files={"app.json": "{}"}, commands=["npm install"],
            start_dev_server=False, timeout_seconds=60,
        )
    assert result["success"] is False
    assert result["sandbox_terminated"] is False
    assert result["preview_url"] is None
    assert result["sandbox_session_id"] == "sess-1"
    assert "credential" not in str(result["errors"])


@pytest.mark.asyncio
async def test_sandbox_validation_persists_session_identity_and_metadata():
    fake = _FakeAdapter()
    with patch(
        "mozaiksai.core.adapters.get_sandbox_adapter",
        return_value=fake,
    ):
        result = await _run_sandbox_validation(
            strategy="e2b",
            resolved_files={"app.json": "{}"},
            commands=["npm install"],
            start_dev_server=False,
            timeout_seconds=60,
            session_metadata={
                "purpose": "app_validation",
                "app_id": "app-1",
                "chat_id": "chat-1",
            },
        )

    assert result["sandbox_session_id"] == "sess-1"
    assert result["sandbox_provider"] == "e2b"
    assert fake.create_kwargs["metadata"] == {
        "purpose": "app_validation",
        "app_id": "app-1",
        "chat_id": "chat-1",
    }
    # Ephemeral by design: the session is still terminated after the run.
    assert fake.terminated == ["sess-1"]


@pytest.mark.asyncio
async def test_validation_caller_cannot_change_sandbox_purpose(monkeypatch):
    fake = _FakeAdapter()
    monkeypatch.setattr(app_validation_module.app_runtime_smoke, "_preflight_generated_image", lambda: "sha256:" + "a" * 64)
    monkeypatch.setattr("mozaiksai.core.adapters.DockerSandboxAdapter", lambda *, image: fake)
    await _run_sandbox_validation(
        strategy="docker", resolved_files={"app.json": "{}"}, commands=[],
        start_dev_server=False, timeout_seconds=60,
        session_metadata={"purpose": "artifact_preview", "app_id": "app-1"},
    )
    assert fake.create_kwargs["metadata"] == {"purpose": "app_validation", "app_id": "app-1"}


@pytest.mark.asyncio
async def test_sandbox_validation_defaults_purpose_metadata():
    fake = _FakeAdapter()
    with patch(
        "mozaiksai.core.adapters.get_sandbox_adapter",
        return_value=fake,
    ):
        await _run_sandbox_validation(
            strategy="e2b",
            resolved_files={"app.json": "{}"},
            commands=[],
            start_dev_server=False,
            timeout_seconds=60,
        )
    assert fake.create_kwargs["metadata"] == {"purpose": "app_validation"}


@pytest.mark.asyncio
async def test_validation_does_not_receive_preview_forwarded_environment(monkeypatch):
    monkeypatch.setenv("MOZAIKS_PREVIEW_ENV_OPENAI_API_KEY", "operator-preview-secret")
    assert "OPENAI_API_KEY" not in sandbox_resource_environment()
    assert preview_resource_environment()["OPENAI_API_KEY"] == "operator-preview-secret"
    fake = _FakeAdapter()
    monkeypatch.setattr(app_validation_module.app_runtime_smoke, "_preflight_generated_image", lambda: "sha256:" + "a" * 64)
    monkeypatch.setattr("mozaiksai.core.adapters.DockerSandboxAdapter", lambda *, image: fake)
    await _run_sandbox_validation(
        strategy="docker", resolved_files={"app.json": "{}"}, commands=[],
        start_dev_server=False, timeout_seconds=60,
    )
    assert "OPENAI_API_KEY" not in fake.create_kwargs["envs"]


# ---------------------------------------------------------------------------
# BuildRecord first-class validation fields
# ---------------------------------------------------------------------------

def test_build_record_carries_validation_fields():
    record = BuildRecord(
        _id="av_test",
        app_id="app-1",
        build_family="app_bundle",
        build_key="app_bundle",
        version_number=1,
        lineage_root_id="av_test",
        app_validation_status="passed",
        app_validation_strategy="docker",
        sandbox_session_id="sess-1",
        sandbox_provider="docker",
    )
    dumped = record.model_dump()
    assert dumped["app_validation_status"] == "passed"
    assert dumped["app_validation_strategy"] == "docker"
    assert dumped["sandbox_session_id"] == "sess-1"
    assert dumped["sandbox_provider"] == "docker"


def test_build_record_validation_fields_default_none():
    record = BuildRecord(
        _id="av_test2",
        app_id="app-1",
        build_family="app_bundle",
        build_key="app_bundle",
        version_number=1,
        lineage_root_id="av_test2",
    )
    assert record.app_validation_status is None
    assert record.sandbox_session_id is None


# ---------------------------------------------------------------------------
# Coding worker vocabulary is honest
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("strategy", APP_VALIDATION_STRATEGIES)
def test_coding_worker_resolves_canonical_validation_strategies(monkeypatch, strategy):
    monkeypatch.delenv("MOZAIKS_APP_VALIDATION_STRATEGY", raising=False)
    assert resolve_coding_validation_strategy(strategy) == strategy


@pytest.mark.parametrize("strategy", APP_VALIDATION_STRATEGIES)
def test_coding_worker_plan_accepts_canonical_validation_strategies(strategy):
    plan = CodingWorkerPlan(
        summary="s", owned_paths=[], updated_files=[], validation_strategy=strategy,
        validation_commands=[], start_preview=False, needs_human_review=False, rationale="r",
    )
    assert plan.validation_strategy == strategy


def test_coding_worker_plan_rejects_unknown_strategy():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CodingWorkerPlan(
            summary="s",
            owned_paths=[],
            updated_files=[],
            validation_strategy="unsupported",
            validation_commands=[],
            start_preview=False,
            needs_human_review=False,
            rationale="r",
        )


# ---------------------------------------------------------------------------
# Preview session manager creates provider sandboxes with deadline + identity
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_artifact_preview_manager_tags_and_bounds_sessions(monkeypatch):
    from tests.test_artifact_preview_sessions import _manager

    created: dict[str, Any] = {}

    class _FakeAdapter:
        async def create_session(self, **kwargs):
            created.update(kwargs)
            from mozaiksai.core.ports.sandbox import SandboxSessionInfo

            return SandboxSessionInfo(session_id="fake-session", provider="e2b")

    monkeypatch.setenv("SANDBOX_TTL_MINUTES", "15")
    manager = _manager(_FakeAdapter(), provider="e2b")

    state = await manager.create_or_reuse(
        "artifact-123", app_id="factory", user_id="user-a", target_app_id="generated-app", build_registry_id="appreg-a",
    )

    assert state.session_id == "fake-session"
    assert created["timeout_seconds"] == 15 * 60
    metadata = created["metadata"]
    assert metadata["purpose"] == "artifact_preview"
    assert metadata["artifact_id"] == "artifact-123"
    assert metadata["manager_sandbox_id"] == state.sandbox_id
