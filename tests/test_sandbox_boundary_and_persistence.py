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

from factory_app.workflows.AppGenerator.tools.app_validation import (
    _run_sandbox_validation,
)
from mozaiksai.control_plane.contracts import CodingWorkerPlan
from mozaiksai.control_plane.implementations.coding_worker import _VALIDATION_STRATEGIES
from mozaiksai.core.adapters.docker_sandbox import DockerSandboxAdapter, _preview_ports
from mozaiksai.core.artifacts.models import BuildRecord
from mozaiksai.core.ports.sandbox import SandboxRunResult, SandboxSessionInfo

# ---------------------------------------------------------------------------
# Docker adapter publishes preview ports
# ---------------------------------------------------------------------------

def test_preview_ports_default_and_env(monkeypatch):
    monkeypatch.delenv("SANDBOX_PREVIEW_PORT", raising=False)
    assert _preview_ports() == [3000, 8000]
    monkeypatch.setenv("SANDBOX_PREVIEW_PORT", "5173")
    assert _preview_ports() == [5173, 8000]
    monkeypatch.setenv("SANDBOX_PREVIEW_PORT", "8000")
    assert _preview_ports() == [8000]


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

    async def terminate_session(self, *, session_id: str) -> None:
        self.terminated.append(session_id)


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

def test_coding_worker_strategies_exclude_unimplemented_e2b():
    assert _VALIDATION_STRATEGIES == {"skip", "local"}


def test_coding_worker_plan_rejects_e2b():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CodingWorkerPlan(
            summary="s",
            owned_paths=[],
            updated_files=[],
            validation_strategy="e2b",
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
    from mozaiksai.core.sandbox.preview_sessions import ArtifactPreviewSessionManager

    created: dict[str, Any] = {}

    class _FakeAdapter:
        async def create_session(self, **kwargs):
            created.update(kwargs)
            from mozaiksai.core.ports.sandbox import SandboxSessionInfo

            return SandboxSessionInfo(session_id="fake-session", provider="e2b")

    monkeypatch.setenv("SANDBOX_TTL_MINUTES", "15")
    manager = ArtifactPreviewSessionManager(
        provider_resolver=lambda: ("e2b", _FakeAdapter())
    )
    manager._broadcast = AsyncMock()

    state = await manager.create_or_reuse("artifact-123")

    assert state.session_id == "fake-session"
    assert created["timeout_seconds"] == 15 * 60
    metadata = created["metadata"]
    assert metadata["purpose"] == "artifact_preview"
    assert metadata["artifact_id"] == "artifact-123"
    assert metadata["manager_sandbox_id"] == state.sandbox_id
