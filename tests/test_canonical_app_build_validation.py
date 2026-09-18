"""Canonical app compilation uses the shared shell, never invented npm scripts."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.app_validation import _run_sandbox_validation
from mozaiksai.resources import resolve_factory_app_root


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["e2b", "docker"])
@pytest.mark.parametrize("failed_step", [None, 0, 1])
async def test_canonical_build_stages_workspace_and_always_terminates(monkeypatch, provider, failed_step):
    from mozaiksai.core import adapters

    async def run(**kwargs):
        return SimpleNamespace(success=adapter.run_command.await_count - 1 != failed_step, stdout="output", stderr="")

    adapter = SimpleNamespace(
        create_session=AsyncMock(return_value=SimpleNamespace(session_id="build", provider=provider)),
        write_files=AsyncMock(), run_command=AsyncMock(side_effect=run),
        terminate_session=AsyncMock(return_value=True), read_file=AsyncMock(), get_preview_url=AsyncMock(),
    )
    monkeypatch.setattr(adapters, "get_sandbox_adapter", lambda _: adapter)
    monkeypatch.delenv("SANDBOX_WORKDIR", raising=False)
    monkeypatch.setenv("E2B_TIMEOUT", "120")
    monkeypatch.setenv("OPENAI_API_KEY", "host-secret-not-forwarded")
    result = await _run_sandbox_validation(
        strategy=provider, resolved_files={"app.json": '{"appId": null}', "modules/reports/backend/handler.py": "x = 1\n"},
        commands=["echo bypass"], start_dev_server=True, timeout_seconds=600,
    )
    root = "/workspace" if provider == "docker" else "/home/user/app"
    staged = adapter.write_files.await_args.kwargs
    assert staged["cwd"] == root
    assert staged["files"]["app/app.json"] == '{"appId": null}'
    assert "app/modules/reports/backend/handler.py" in staged["files"]
    envs = adapter.create_session.await_args.kwargs["envs"]
    assert envs["PLATFORM_PATH"] == root + "/app"
    assert envs["MOZAIKS_WEB_SHELL_PATH"] == "/opt/mozaiks/web_shell"
    assert "OPENAI_API_KEY" not in envs
    calls = adapter.run_command.await_args_list
    assert calls[0].kwargs["command"] == "python -m compileall -q ."
    assert calls[0].kwargs["cwd"] == root
    if failed_step != 0:
        assert "vite/bin/vite.js build" in calls[1].kwargs["command"]
        assert calls[1].kwargs["cwd"] == "/opt/mozaiks/web_shell"
        assert calls[1].kwargs["envs"]["PLATFORM_PATH"] == root + "/app"
    assert all("echo bypass" not in call.kwargs["command"] for call in calls)
    assert result["validation_status"] == ("passed" if failed_step is None else "failed")
    assert result["sandbox_terminated"] and result["preview_url"] is None
    adapter.terminate_session.assert_awaited_once_with(session_id="build")
    adapter.read_file.assert_not_awaited()
    adapter.get_preview_url.assert_not_awaited()


@pytest.mark.asyncio
async def test_conflicting_canonical_paths_fail_before_allocation(monkeypatch):
    from mozaiksai.core import adapters

    adapter = SimpleNamespace(create_session=AsyncMock())
    monkeypatch.setattr(adapters, "get_sandbox_adapter", lambda _: adapter)
    result = await _run_sandbox_validation(
        strategy="e2b", resolved_files={"app.json": "{}", "app/app.json": "{}"},
        commands=[], start_dev_server=False, timeout_seconds=120,
    )
    assert result["validation_status"] == "failed"
    adapter.create_session.assert_not_awaited()


def test_validation_models_match_runtime_provider_taxonomy():
    from mozaiksai.core.workflow.generator_support.app_validation_strategy import (
        APP_VALIDATION_STRATEGIES,
    )

    root = resolve_factory_app_root()
    schema = yaml.safe_load((root / "workflows/AppGenerator/structured_outputs.yaml").read_text(encoding="utf-8"))
    models = schema["models"]
    for name in ("AppValidation", "AppValidationRequest"):
        assert set(models[name]["fields"]["validation_strategy"]["values"]) == set(APP_VALIDATION_STRATEGIES)


@pytest.mark.asyncio
async def test_local_canonical_validation_uses_same_workspace_and_build_steps(monkeypatch, tmp_path):
    from factory_app.workflows.AppGenerator.tools import app_validation
    from mozaiksai import resources

    shell = tmp_path / "shell"
    vite = shell / "node_modules/vite/bin/vite.js"
    vite.parent.mkdir(parents=True)
    vite.touch()
    monkeypatch.setattr(resources, "resolve_web_shell_root", lambda: shell)
    monkeypatch.setattr(app_validation, "_local_validation_available", lambda: True)

    async def run(**kwargs):
        root = kwargs["env"]["MOZAIKS_APP_WORKSPACE_PATH"]
        from pathlib import Path

        assert (Path(root) / "app/app.json").is_file()
        assert kwargs["env"]["PLATFORM_PATH"] == root + "/app"
        return 0, "compiled", ""

    runner = AsyncMock(side_effect=run)
    monkeypatch.setattr(app_validation, "_run_local_command", runner)
    result = await app_validation._run_local_validation(
        resolved_files={"app.json": "{}"}, commands=["echo bypass"],
        start_dev_server=False, timeout_seconds=120,
    )
    assert result["validation_status"] == "passed"
    assert runner.await_count == 2
    assert "compileall" in runner.await_args_list[0].kwargs["command"]
    assert "vite.js build" in runner.await_args_list[1].kwargs["command"]
    assert runner.await_args_list[1].kwargs["cwd"] == shell
