from __future__ import annotations

from typing import Any

import pytest

from factory_app.refinement_harness.tools import app_validation
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
