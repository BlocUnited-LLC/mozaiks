from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

from mozaiksai.control_plane.app_validation import (
    plan_app_source_validation_commands,
    run_app_source_validation,
)


def _framework_detection(commands: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": "mozaiks.framework_detection.v1",
        "primary_framework_id": "python",
        "primary_framework_label": "Python",
        "validation_commands": commands,
    }


def test_app_source_validation_runs_safe_detected_commands_in_copy(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")

    calls: list[dict[str, Any]] = []

    def fake_runner(argv: list[str], cwd: Path, timeout: int, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        calls.append({"argv": argv, "cwd": cwd, "timeout": timeout, "env": env})
        assert cwd != workspace
        assert (cwd / "pyproject.toml").exists()
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    result = run_app_source_validation(
        app_id="app_1",
        workspace_root=workspace,
        framework_detection=_framework_detection(
            [
                {"kind": "install", "command": "npm install", "working_directory": ".", "confidence": 0.9},
                {"kind": "test", "command": "python -m pytest", "working_directory": ".", "confidence": 0.8},
            ]
        ),
        confirm_execution=True,
        command_runner=fake_runner,
    )

    assert result.validation_status == "passed"
    assert result.execution_mode == "isolated_workspace_copy"
    assert len(calls) == 1
    assert calls[0]["argv"][:3] == [sys.executable, "-m", "pytest"]
    assert calls[0]["env"]["MOZAIKS_APP_VALIDATION"] == "1"
    assert result.planned_commands[0].kind == "install"
    assert result.planned_commands[0].status == "skipped"
    assert result.planned_commands[0].skip_reason == "install_commands_require_include_install"
    assert result.command_results[0].status == "passed"
    assert result.workspace_root_present is True


def test_app_source_validation_rejects_unsafe_commands_and_uses_fallback(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "package.json").write_text('{"scripts":{"test":"echo ok"}}', encoding="utf-8")

    def fail_runner(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise AssertionError("unsafe commands must not execute")

    result = run_app_source_validation(
        app_id="app_1",
        workspace_root=workspace,
        framework_detection=_framework_detection(
            [{"kind": "test", "command": "npm run test && echo pwned", "working_directory": "."}]
        ),
        confirm_execution=True,
        command_runner=fail_runner,
    )

    assert result.command_results == []
    assert result.planned_commands[0].status == "skipped"
    assert result.planned_commands[0].skip_reason == "unsafe_command_rejected"
    assert any(check.name == "json_manifest_parse" and check.status == "passed" for check in result.fallback_checks)


def test_app_source_validation_fallback_applies_staged_overlay(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "service.py").write_text("def ok():\n    return True\n", encoding="utf-8")

    result = run_app_source_validation(
        app_id="app_1",
        workspace_root=workspace,
        framework_detection={},
        overlay_files={"service.py": "def broken(:\n    return True\n"},
    )

    assert result.validation_status == "failed"
    assert result.overlay_file_count == 1
    python_check = next(check for check in result.fallback_checks if check.name == "python_syntax")
    assert python_check.status == "failed"
    assert "service.py" in python_check.reason
    assert (workspace / "service.py").read_text(encoding="utf-8").startswith("def ok")


def test_plan_app_source_validation_rejects_out_of_workspace_workdir(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    plan = plan_app_source_validation_commands(
        workspace_root=workspace,
        framework_detection=_framework_detection(
            [{"kind": "test", "command": "python -m pytest", "working_directory": "../outside"}]
        ),
    )

    assert plan[0].status == "skipped"
    assert plan[0].skip_reason == "working_directory_outside_workspace"


def test_plan_app_source_validation_requires_include_install_even_when_selected(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    plan = plan_app_source_validation_commands(
        workspace_root=workspace,
        allowed_kinds=["install"],
        include_install=False,
        framework_detection=_framework_detection(
            [{"kind": "install", "command": "npm install", "working_directory": "."}]
        ),
    )

    assert plan[0].status == "skipped"
    assert plan[0].skip_reason == "install_commands_require_include_install"
