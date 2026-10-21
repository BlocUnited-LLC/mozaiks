from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from factory_app.refinement_harness.tools import app_validation
from mozaiksai.control_plane import app_validation as core_validation
from mozaiksai.control_plane.contracts import ControlPlaneToolContext


class _ValidationResult:
    def model_dump(self, *, mode: str = "python") -> dict[str, Any]:
        return {
            "schema_version": "mozaiks.app_source_validation.v1",
            "validation_status": "passed",
            "mode": mode,
        }


@pytest.mark.asyncio
async def test_run_app_source_validation_tool_calls_current_context_runner(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_runner(**kwargs: Any) -> _ValidationResult:
        captured.update(kwargs)
        return _ValidationResult()

    monkeypatch.setattr(app_validation, "run_current_app_source_validation", fake_runner)

    result = await app_validation.run_app_source_validation(
        allowed_kinds=["test"],
        confirm_execution=True,
        context=ControlPlaneToolContext(checkpoint="coding_requested", app_id="app_1"),
    )

    assert result["present"] is True
    assert result["validation"]["validation_status"] == "passed"
    assert captured["app_id"] == "app_1"
    assert captured["allowed_kinds"] == ["test"]
    assert captured["confirm_execution"] is True


@pytest.mark.asyncio
async def test_run_app_source_validation_tool_requires_app_id() -> None:
    result = await app_validation.run_app_source_validation(context=ControlPlaneToolContext())

    assert result["present"] is False
    assert result["validation_status"] == "skipped"
    assert result["reason"] == "missing_app_id"


@pytest.mark.asyncio
async def test_refinement_tool_cannot_start_host_commands_with_auth_enabled(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_PROVIDER", "jwt")
    monkeypatch.setenv("AUTH_AUDIENCE", "source-validation-test")
    workspace = tmp_path / "imported-repository"
    workspace.mkdir()
    (workspace / "pyproject.toml").write_text("[project]\nname = 'audit'\n", encoding="utf-8")
    (workspace / "service.py").write_text("VALUE = 1\n", encoding="utf-8")

    async def latest_job(**kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(workspace_root=str(workspace))

    async def framework_detection(**kwargs: Any) -> dict[str, Any]:
        return {
            "validation_commands": [
                {"kind": "test", "command": "python -m pytest", "working_directory": "."}
            ],
        }

    def forbidden_host_runner(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Repository command reached the host subprocess runner")

    monkeypatch.setattr(core_validation, "get_latest_app_intelligence_index_job", latest_job)
    monkeypatch.setattr(core_validation, "_current_framework_detection", framework_detection)
    monkeypatch.setattr(core_validation, "_run_subprocess_command", forbidden_host_runner)

    result = await app_validation.run_app_source_validation(
        confirm_execution=True,
        context=ControlPlaneToolContext(checkpoint="coding_requested", app_id="app_1"),
    )

    assert result["validation"]["validation_status"] == "warning"
    assert result["validation"]["command_results"] == []
    assert result["validation"]["planned_commands"][0]["skip_reason"] == (
        "host_command_execution_requires_isolation"
    )
