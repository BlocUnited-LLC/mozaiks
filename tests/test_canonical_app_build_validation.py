"""Canonical app compilation uses the shared shell, never invented npm scripts."""

import os
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
    from factory_app.workflows.AppGenerator.tools import app_runtime_smoke
    from mozaiksai.core import adapters

    async def run(**kwargs):
        return SimpleNamespace(success=adapter.run_command.await_count - 1 != failed_step, stdout="output", stderr="")

    adapter = SimpleNamespace(
        create_session=AsyncMock(return_value=SimpleNamespace(session_id="build", provider=provider)),
        write_files=AsyncMock(), run_command=AsyncMock(side_effect=run),
        terminate_session=AsyncMock(return_value=True), read_file=AsyncMock(), get_preview_url=AsyncMock(),
    )
    monkeypatch.setattr(adapters, "get_sandbox_adapter", lambda _: adapter)
    monkeypatch.setattr(adapters, "DockerSandboxAdapter", lambda *, image: adapter)
    monkeypatch.setattr(app_runtime_smoke, "_preflight_generated_image", lambda: "sha256:" + "a" * 64)
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
    assert envs["MOZAIKS_REQUIRE_TAILWIND_SOURCE_LINKS"] == "1"
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
    if provider == "docker":
        assert result["sandbox_image_id"] == "sha256:" + "a" * 64
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


@pytest.mark.asyncio
async def test_oversized_docker_build_timeout_is_capped_before_allocation(monkeypatch):
    from factory_app.workflows.AppGenerator.tools import app_runtime_smoke
    from mozaiksai.core import adapters

    adapter = SimpleNamespace(
        create_session=AsyncMock(return_value=SimpleNamespace(session_id="build", provider="docker")),
        write_files=AsyncMock(),
        run_command=AsyncMock(return_value=SimpleNamespace(success=True, stdout="", stderr="")),
        terminate_session=AsyncMock(return_value=True),
    )
    monkeypatch.setattr(adapters, "DockerSandboxAdapter", lambda *, image: adapter)
    monkeypatch.setattr(app_runtime_smoke, "_preflight_generated_image", lambda: "sha256:" + "a" * 64)
    result = await _run_sandbox_validation(
        strategy="docker", resolved_files={"app.json": "{}"}, commands=[],
        start_dev_server=False, timeout_seconds=9999,
    )
    assert result["validation_status"] == "passed"
    assert adapter.create_session.await_args.kwargs["timeout_seconds"] == 120
    assert [call.kwargs["timeout_seconds"] for call in adapter.run_command.await_args_list] == [120.0, 120.0]
    adapter.terminate_session.assert_awaited_once_with(session_id="build")


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [0, -1, True, "9999"])
async def test_invalid_build_timeout_fails_before_file_resolution(monkeypatch, timeout):
    from factory_app.workflows.AppGenerator.tools import app_validation

    resolve_files = AsyncMock(side_effect=AssertionError("Resolved files before timeout validation"))
    monkeypatch.setattr(app_validation, "_resolve_files", resolve_files)
    result = await app_validation.validate_app_build(
        files={"app.json": "{}"}, validation_strategy="docker", timeout_seconds=timeout,
    )
    assert result["validation_status"] == "failed"
    assert result["errors"] == ["App build timeout must be a positive integer"]
    resolve_files.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("stream_name", ["stdout", "stderr"])
async def test_docker_cli_output_is_bounded_and_process_reaped(monkeypatch, stream_name):
    import asyncio
    import sys

    from mozaiksai.core.adapters.docker_sandbox import DockerSandboxAdapter

    create = asyncio.create_subprocess_exec
    processes = []

    async def emit_large_output(*args, **kwargs):
        process = await create(
            sys.executable, "-c", f"import sys; sys.{stream_name}.write('X' * 5000000)",
            **kwargs,
        )
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", emit_large_output)
    with pytest.raises(RuntimeError, match="output exceeded 1 MiB"):
        await DockerSandboxAdapter._run(["docker", "exec", "container", "echo", "irrelevant"])
    result = await DockerSandboxAdapter().run_command(session_id="container", command="echo irrelevant")
    assert result.success is False
    assert result.error == "sandbox_error"
    assert len(processes) == 2
    assert all(process.returncode is not None for process in processes)


def test_validation_models_match_runtime_provider_taxonomy():
    from mozaiksai.core.workflow.generator_support.app_validation_strategy import (
        APP_VALIDATION_STRATEGIES,
    )

    root = resolve_factory_app_root()
    schema = yaml.safe_load((root / "workflows/AppGenerator/structured_outputs.yaml").read_text(encoding="utf-8"))
    models = schema["models"]
    assert set(models["AppValidationStrategy"]["values"]) == set(APP_VALIDATION_STRATEGIES)
    assert models["AppValidation"]["fields"]["validation_strategy"]["type"] == "AppValidationStrategy"
    assert models["AppValidationRequest"]["fields"]["validation_strategy"]["variants"] == [
        "AppValidationStrategy", "null",
    ]


def test_compiled_validation_request_allows_runtime_default_and_only_known_strategies():
    from pydantic import ValidationError

    from mozaiksai.core.workflow.outputs.structured import load_workflow_structured_outputs

    models, _ = load_workflow_structured_outputs("AppGenerator")
    request_model = models["AppValidationRequest"]
    base = {"start_dev_server": False, "timeout_seconds": 120, "commands": None}
    assert request_model.model_validate(base).validation_strategy is None
    for strategy in (None, "e2b", "docker", "skip"):
        request = request_model.model_validate({**base, "validation_strategy": strategy})
        assert request.model_dump(mode="json")["validation_strategy"] == strategy
    with pytest.raises(ValidationError):
        request_model.model_validate({**base, "validation_strategy": "auto"})


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["request", "environment"])
async def test_generated_build_rejects_local_script_before_host_execution(monkeypatch, source):
    import asyncio

    from factory_app.workflows.AppGenerator.tools.app_validation import validate_app_build

    async def no_host_shell(*args, **kwargs):
        raise AssertionError("Candidate package script reached the host shell")

    monkeypatch.setattr(asyncio, "create_subprocess_shell", no_host_shell)
    if source == "environment":
        monkeypatch.setenv("MOZAIKS_APP_VALIDATION_STRATEGY", "local")
    malicious_package = '{"scripts":{"build":"node -e \\"require(\'fs\').writeFileSync(\'host-marker\',\'ran\')\\""}}'
    result = await validate_app_build(
        files={"package.json": malicious_package}, commands=["npm run build"],
        validation_strategy="local" if source == "request" else None, start_dev_server=False,
    )
    assert result["validation_status"] == "failed"
    assert "Unsupported app validation strategy 'local'" in result["errors"][0]


@pytest.mark.asyncio
async def test_generated_build_without_sandbox_skips_malicious_script(monkeypatch):
    import asyncio

    from factory_app.workflows.AppGenerator.tools.app_validation import validate_app_build
    from mozaiksai.core.workflow.generator_support import app_validation_strategy

    async def no_host_shell(*args, **kwargs):
        raise AssertionError("Candidate package script reached the host shell")

    monkeypatch.setattr(asyncio, "create_subprocess_shell", no_host_shell)
    monkeypatch.setattr(app_validation_strategy, "docker_app_validation_available", lambda: False)
    result = await validate_app_build(
        files={"package.json": '{"scripts":{"build":"echo host-marker"}}'},
        commands=["npm run build"], start_dev_server=False,
    )
    assert result["validation_status"] == "skipped"
    assert result["validation_strategy"] == "skip"
    assert result["success"] is False


@pytest.mark.asyncio
async def test_generated_docker_build_fails_closed_without_pinned_image(monkeypatch):
    import asyncio

    from factory_app.workflows.AppGenerator.tools import app_runtime_smoke
    from factory_app.workflows.AppGenerator.tools.app_validation import validate_app_build
    from mozaiksai.core import adapters

    async def no_host_shell(*args, **kwargs):
        raise AssertionError("Candidate package script reached the host shell")

    monkeypatch.setattr(asyncio, "create_subprocess_shell", no_host_shell)
    monkeypatch.setattr(app_runtime_smoke, "_preflight_generated_image", lambda: (_ for _ in ()).throw(RuntimeError("no pinned image")))
    monkeypatch.setattr(adapters, "DockerSandboxAdapter", lambda **kwargs: (_ for _ in ()).throw(AssertionError("allocated before image preflight")))
    result = await validate_app_build(
        files={"package.json": '{"scripts":{"build":"echo host-marker"}}'},
        commands=["npm run build"], validation_strategy="docker", start_dev_server=False,
    )
    assert result["validation_status"] == "failed"
    assert result["infrastructure_failure"] is True
    assert result["errors"] == ["Validation infrastructure unavailable."]


@pytest.mark.asyncio
async def test_generated_package_script_is_staged_and_run_only_in_pinned_sandbox(monkeypatch):
    import asyncio
    import json

    from factory_app.workflows.AppGenerator.tools import app_runtime_smoke
    from factory_app.workflows.AppGenerator.tools.app_validation import validate_app_build
    from mozaiksai.core import adapters

    async def no_host_shell(*args, **kwargs):
        raise AssertionError("Candidate package script reached the host shell")

    package = json.dumps({"scripts": {"build": "node -e 'write-host-marker'"}})
    image_id = "sha256:" + "a" * 64
    sandbox = SimpleNamespace(
        create_session=AsyncMock(return_value=SimpleNamespace(session_id="pinned-build", provider="docker")),
        write_files=AsyncMock(),
        run_command=AsyncMock(return_value=SimpleNamespace(success=True, stdout="built", stderr="")),
        read_file=AsyncMock(return_value=package),
        terminate_session=AsyncMock(return_value=True),
    )
    monkeypatch.setattr(asyncio, "create_subprocess_shell", no_host_shell)
    monkeypatch.setattr(app_runtime_smoke, "_preflight_generated_image", lambda: image_id)
    monkeypatch.setattr(adapters, "DockerSandboxAdapter", lambda *, image: sandbox)
    result = await validate_app_build(
        files={"package.json": package}, commands=["npm run build"],
        validation_strategy="docker", start_dev_server=False,
    )
    assert result["validation_status"] == "passed"
    assert result["sandbox_image_id"] == image_id
    assert sandbox.write_files.await_args.kwargs["files"]["package.json"] == package
    sandbox.run_command.assert_awaited_once()
    assert sandbox.run_command.await_args.kwargs["command"] == "npm run build"
    sandbox.terminate_session.assert_awaited_once_with(session_id="pinned-build")


@pytest.mark.asyncio
async def test_docker_build_session_uses_bounded_read_only_offline_container(monkeypatch):
    from mozaiksai.core.adapters.docker_sandbox import DockerSandboxAdapter

    calls = []

    async def run(args, **kwargs):
        calls.append(args)
        return 0, "container-id", ""

    monkeypatch.setattr(DockerSandboxAdapter, "_run", staticmethod(run))
    image_id = "sha256:" + "a" * 64
    session = await DockerSandboxAdapter(image=image_id).create_session(
        timeout_seconds=120, metadata={"purpose": "app_validation"},
    )
    command = calls[0]
    assert session.metadata["image"] == image_id
    assert image_id in command
    assert "--read-only" in command
    assert command[command.index("--user") + 1] == "10001:10001"
    assert command[command.index("--network") + 1] == "none"
    assert "--log-driver=none" in command
    assert any("/workspace:rw,nosuid,nodev,size=512m" in arg for arg in command)
    assert any("/opt/mozaiks/web_shell/node_modules/.vite-temp:rw,nosuid,nodev,size=16m" in arg for arg in command)
    assert any("/opt/mozaiks/web_shell/.mozaiks-tailwind-sources:rw,nosuid,nodev,size=16m" in arg for arg in command)
    assert "-p" not in command


@pytest.mark.skipif(
    not os.getenv("MOZAIKS_TEST_CONTAINED_SMOKE_IMAGE"),
    reason="set MOZAIKS_TEST_CONTAINED_SMOKE_IMAGE to a freshly built local preview image",
)
@pytest.mark.asyncio
async def test_real_docker_build_includes_generated_tailwind_class(monkeypatch):
    import subprocess

    from factory_app.workflows.AppGenerator.tools.app_validation import validate_app_build
    from mozaiksai.core import adapters
    from tests.test_generated_app_functional_acceptance import _basic_crud_files

    image = os.environ["MOZAIKS_TEST_CONTAINED_SMOKE_IMAGE"]
    image_id = subprocess.check_output(
        ["docker", "image", "inspect", "--format", "{{.Id}}", image],
        text=True, timeout=5,
    ).strip()
    monkeypatch.setenv("MOZAIKS_APP_RUNTIME_IMAGE_ID", image_id)
    captured = {}
    real_adapter = adapters.DockerSandboxAdapter

    class InspectingAdapter(real_adapter):
        async def terminate_session(self, *, session_id):
            captured["source_link"] = await self.run_command(
                session_id=session_id,
                command="test -L /opt/mozaiks/web_shell/.mozaiks-tailwind-sources/platform-ui",
            )
            captured["css"] = await self.run_command(
                session_id=session_id,
                command="grep -R -l '#123abc' /workspace/build --include='*.css'",
            )
            return await super().terminate_session(session_id=session_id)

    monkeypatch.setattr(adapters, "DockerSandboxAdapter", InspectingAdapter)
    files = _basic_crud_files()
    files["ui/index.js"] = "export const cssProof = 'bg-[#123abc]';\n"
    result = await validate_app_build(
        files=files, validation_strategy="docker", start_dev_server=False,
        timeout_seconds=120,
    )
    assert result["validation_status"] == "passed", result["errors"]
    assert result["sandbox_image_id"] == image_id
    assert result["sandbox_terminated"] is True
    assert captured["source_link"].success is True
    assert captured["css"].success is True, captured["css"].stderr
    assert "Failed to prepare Tailwind source links" not in result["build_output"]
