"""Validation readiness must reflect the required work that actually completed."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from factory_app.workflows.AppGenerator.tools import app_validation as factory_validation
from mozaiksai.control_plane import app_validation as source_validation
from mozaiksai.control_plane.app_validation import run_app_source_validation


@pytest.mark.parametrize("checks", [{}, {"runtime": {"status": "skipped", "passed": None}}])
def test_empty_or_all_skipped_acceptance_is_pending(checks):
    status, evidence = factory_validation._acceptance_readiness(checks)
    assert status == "pending"
    assert evidence["completed"] == []
    assert evidence["failed"] == []


def test_required_skip_blocks_other_passes_without_requesting_code_repair():
    status, evidence = factory_validation._acceptance_readiness({
        "contracts": {"passed": True},
        "runtime": {"status": "skipped", "passed": None},
    })
    assert status == "pending"
    assert evidence == {"completed": ["contracts"], "failed": [], "skipped": ["runtime"]}


def test_applicable_checks_and_successful_not_applicable_check_can_pass():
    status, evidence = factory_validation._acceptance_readiness({
        "runtime": {"passed": True},
        "agent_backend": {"passed": True, "checks": [{"id": "agent_backend_integration_not_required"}]},
    })
    assert status == "passed"
    assert evidence["skipped"] == []


@pytest.mark.parametrize("build_status", ["passed", "pending", "skipped", "failed"])
async def test_request_readiness_requires_build_validation_pass(monkeypatch, build_status):
    names = (
        "bundle_scan", "agent_backend", "module_wiring", "module_implementation",
        "module_runtime_quality", "functional_completeness", "workflow_integration",
        "app_runtime_load", "app_runtime_smoke",
    )

    async def accepted(**kwargs):
        return {"passed": True, "status": "passed", "skipped_checks": [],
                **{name: {"passed": True} for name in names}}

    async def validated(**kwargs):
        return factory_validation._base_result(strategy="local", status=build_status)

    monkeypatch.setattr(factory_validation, "run_app_bundle_acceptance_gate", accepted)
    monkeypatch.setattr(factory_validation, "validate_app_build", validated)
    context = {"generated_files": {"README.md": "candidate"}}
    result = await factory_validation.validate_app_bundle_from_request({}, context_variables=context)
    assert result["integration_tests_passed"] is (build_status == "passed")
    assert context["integration_tests_passed"] is (build_status == "passed")
    assert result["app_validation_result"]["validation_status"] == build_status


@pytest.mark.parametrize("strategy_input", [None, "omitted"])
@pytest.mark.parametrize("available,expected", [("docker", "docker"), ("local", "local"), (None, "skip")])
async def test_compiled_request_defers_to_runtime_default(monkeypatch, strategy_input, available, expected):
    from mozaiksai.core.workflow.context.adapter import create_context_container
    from mozaiksai.core.workflow.generator_support import app_validation_strategy
    from mozaiksai.core.workflow.outputs.structured import load_workflow_structured_outputs

    monkeypatch.delenv("MOZAIKS_APP_VALIDATION_STRATEGY", raising=False)
    monkeypatch.setattr(app_validation_strategy, "docker_app_validation_available", lambda: available == "docker")
    monkeypatch.setattr(app_validation_strategy, "local_app_validation_available", lambda: available == "local")
    sandbox = AsyncMock(return_value=factory_validation._base_result(strategy="docker", status="passed"))
    local = AsyncMock(return_value=factory_validation._base_result(strategy="local", status="passed"))
    monkeypatch.setattr(factory_validation, "_run_sandbox_validation", sandbox)
    monkeypatch.setattr(factory_validation, "_run_local_validation", local)
    models, _ = load_workflow_structured_outputs("AppGenerator")
    raw = {"start_dev_server": False, "timeout_seconds": 120, "commands": None}
    if strategy_input != "omitted":
        raw["validation_strategy"] = strategy_input
    request = models["AppValidationRequest"].model_validate(raw).model_dump(mode="json")
    context = create_context_container(initial={})
    result = await factory_validation.validate_app_build(
        files={"README.md": "synthetic candidate"}, context_variables=context, **request,
    )
    assert result["validation_strategy"] == expected
    assert context.get("app_validation_strategy_used") == expected
    assert result["validation_status"] == ("skipped" if expected == "skip" else "passed")
    assert sandbox.await_count == (expected == "docker")
    assert local.await_count == (expected == "local")
    if expected == "skip":
        assert "no sandbox or local npm" in result["warnings"][0]
        assert "explicitly" not in result["warnings"][0]


@pytest.mark.parametrize("source", ["context", "environment", "request"])
async def test_explicit_skip_is_preserved_even_when_docker_is_available(monkeypatch, source):
    from mozaiksai.core.workflow.generator_support import app_validation_strategy

    monkeypatch.delenv("MOZAIKS_APP_VALIDATION_STRATEGY", raising=False)
    if source == "environment":
        monkeypatch.setenv("MOZAIKS_APP_VALIDATION_STRATEGY", "skip")
    monkeypatch.setattr(app_validation_strategy, "docker_app_validation_available", lambda: True)
    sandbox = AsyncMock()
    monkeypatch.setattr(factory_validation, "_run_sandbox_validation", sandbox)
    context = {"app_validation_strategy": "skip"} if source == "context" else {}
    result = await factory_validation.validate_app_build(
        files={"README.md": "synthetic candidate"}, context_variables=context,
        validation_strategy="skip" if source == "request" else None,
    )
    assert result["validation_strategy"] == "skip"
    assert result["validation_status"] == "skipped"
    assert result["success"] is False
    sandbox.assert_not_awaited()


def _detection(*commands):
    return {"validation_commands": [{"kind": kind, "command": command} for kind, command in commands]}


def test_source_validation_runs_real_app_test_before_passing_with_injected_runner(tmp_path: Path):
    (tmp_path / "service.py").write_text("def total(values):\n    return sum(values)\n", encoding="utf-8")
    (tmp_path / "test_service.py").write_text(
        "from service import total\ndef test_total():\n    assert total([2, 3]) == 5\n", encoding="utf-8",
    )
    result = run_app_source_validation(
        app_id="reference-app", workspace_root=tmp_path,
        framework_detection=_detection(("test", "python -m pytest -q --no-cov")),
        confirm_execution=True, command_runner=source_validation._run_subprocess_command,
    )
    assert result.validation_status == "passed", result.model_dump()
    assert result.command_results[0].exit_code == 0
    assert "1 passed" in result.command_results[0].stdout_tail


def test_static_checks_preserve_evidence_without_certifying_readiness(tmp_path: Path):
    (tmp_path / "service.py").write_text("def total(values):\n    return sum(values)\n", encoding="utf-8")
    result = run_app_source_validation(app_id="reference-app", workspace_root=tmp_path)
    assert result.validation_status == "warning"
    assert any(check.name == "python_syntax" and check.status == "passed" for check in result.fallback_checks)
    assert result.command_results == []


def test_empty_source_workspace_is_skipped(tmp_path: Path):
    result = run_app_source_validation(app_id="reference-app", workspace_root=tmp_path)
    assert result.validation_status == "skipped"


def test_unavailable_test_executable_cannot_be_replaced_by_json_parse(tmp_path: Path, monkeypatch):
    (tmp_path / "package.json").write_text('{"scripts":{"test":"vitest run"}}', encoding="utf-8")
    monkeypatch.setattr(source_validation, "_resolve_command_argv", lambda argv: (argv, "executable_unavailable"))

    def never_run(*args, **kwargs):
        raise AssertionError("Unavailable commands must not reach the runner")

    result = run_app_source_validation(
        app_id="reference-app", workspace_root=tmp_path,
        framework_detection=_detection(("test", "npm run test")), confirm_execution=True,
        command_runner=never_run,
    )
    assert result.command_results[0].status == "skipped"
    assert result.validation_status == "warning"
    assert any(check.name == "json_manifest_parse" and check.status == "passed" for check in result.fallback_checks)


def test_command_limit_cannot_silently_drop_an_applicable_check(tmp_path: Path):
    def runner(argv, cwd, timeout, env):
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    result = run_app_source_validation(
        app_id="reference-app", workspace_root=tmp_path,
        framework_detection=_detection(("lint", "python -m ruff check ."), ("test", "python -m pytest")),
        max_commands=1, confirm_execution=True, command_runner=runner,
    )
    assert result.command_results[0].status == "passed"
    assert result.planned_commands[1].skip_reason == "max_command_count_reached"
    assert result.validation_status == "warning"


def test_unselected_commands_do_not_block_selected_checks(tmp_path: Path):
    def runner(argv, cwd, timeout, env):
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    result = run_app_source_validation(
        app_id="reference-app", workspace_root=tmp_path,
        framework_detection=_detection(("lint", "python -m ruff check ."), ("test", "python -m pytest")),
        allowed_kinds=["test"], confirm_execution=True, command_runner=runner,
    )
    assert result.validation_status == "passed"
    assert result.planned_commands[0].skip_reason == "command_kind_not_selected"
