from __future__ import annotations

from importlib import import_module
from unittest.mock import AsyncMock

import pytest

from mozaiksai.core.validation import validate_generated_app_candidate


@pytest.fixture
def gates(monkeypatch):
    app_validation = import_module("factory_app.workflows.AppGenerator.tools.app_validation")
    monkeypatch.setenv("MOZAIKS_APP_VALIDATION_STRATEGY", "docker")
    acceptance = AsyncMock(return_value={"status": "passed", "passed": True})
    build = AsyncMock(return_value={"validation_status": "passed", "validation_strategy": "docker"})
    monkeypatch.setattr(app_validation, "run_app_bundle_acceptance_gate", acceptance)
    monkeypatch.setattr(app_validation, "validate_app_build", build)
    return acceptance, build


@pytest.mark.asyncio
async def test_candidate_uses_same_complete_snapshot_and_operator_strategy(gates):
    acceptance, build = gates
    files = {"app.json": "{}", "brand/theme_config.json": '{"accent":"coral"}'}
    result = await validate_generated_app_candidate(
        files=files, app_id="owned_app", validation_strategy="local", timeout_seconds=45,
    )
    assert result["validation_status"] == "passed"
    for gate in gates:
        assert gate.await_args.kwargs["files"] == files
        assert gate.await_args.kwargs["files"] is not files
        assert gate.await_args.kwargs["context_variables"] == {"app_id": "owned_app"}
    assert acceptance.await_count == build.await_count == 1
    assert build.await_args.kwargs["validation_strategy"] == "docker"
    assert build.await_args.kwargs["start_dev_server"] is False
    assert build.await_args.kwargs["timeout_seconds"] == 45


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["failed", "pending", "skipped"])
async def test_acceptance_must_finish_before_build(gates, status):
    acceptance, build = gates
    acceptance.return_value = {"status": status, "passed": False, "failed_tests": [{"error": "Broken route"}]}
    result = await validate_generated_app_candidate(files={"app.json": "{}"}, app_id="app")
    assert result["validation_status"] != "passed"
    assert result["app_validation_result"]["validation_status"] == "pending"
    assert result["errors"] == ["Broken route"]
    build.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["failed", "pending", "skipped", None])
async def test_acceptance_alone_cannot_validate_candidate(gates, status):
    _, build = gates
    build.return_value = {"validation_status": status, "errors": ["Build did not pass"]}
    result = await validate_generated_app_candidate(files={"app.json": "{}"}, app_id="app")
    assert result["app_bundle_acceptance_result"]["passed"] is True
    assert result["validation_status"] != "passed"
    assert result["errors"] == ["Build did not pass"]


@pytest.mark.asyncio
async def test_skip_does_not_execute_generated_code(gates, monkeypatch):
    monkeypatch.setenv("MOZAIKS_APP_VALIDATION_STRATEGY", "skip")
    result = await validate_generated_app_candidate(files={"app.json": "{}"}, app_id="app")
    assert result["validation_status"] == "skipped"
    for gate in gates:
        gate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("files", [
    {"ui/pages/partial.jsx": "partial"},
    {"app.json": "{}", "../escape.py": "bad"},
    {"app.json": "{}", "ui//duplicate.jsx": "bad"},
    {"app.json": "{}", "ui/stream:payload": "bad"},
    {"app.json": "{}", "ui/invalid\x00": "bad"},
    {"app.json": "{}", "ui/page.jsx": None},
])
async def test_invalid_candidate_cannot_reach_execution(gates, files):
    result = await validate_generated_app_candidate(files=files, app_id="app")
    assert result["validation_status"] == "failed"
    for gate in gates:
        gate.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_build_files_keep_session_owner_metadata(monkeypatch):
    app_validation = import_module("factory_app.workflows.AppGenerator.tools.app_validation")
    monkeypatch.setenv("MOZAIKS_APP_VALIDATION_STRATEGY", "docker")
    sandbox = AsyncMock(return_value={"validation_status": "passed"})
    monkeypatch.setattr(app_validation, "_run_sandbox_validation", sandbox)
    await app_validation.validate_app_build(
        files={"app.json": "{}"}, start_dev_server=False,
        context_variables={"app_id": "owned_app", "chat_id": "owned_chat"},
    )
    assert sandbox.await_args.kwargs["session_metadata"] == {
        "purpose": "app_validation", "app_id": "owned_app", "chat_id": "owned_chat",
    }
