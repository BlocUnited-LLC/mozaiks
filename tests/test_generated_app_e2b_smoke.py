"""Opt-in canonical runtime smokes, not a live-LLM generation journey."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import urllib.request
from pathlib import Path

import pytest

from mozaiksai.core.adapters.docker_sandbox import DockerSandboxAdapter
from mozaiksai.core.adapters.e2b_sandbox import E2BSandboxAdapter
from mozaiksai.core.sandbox.preview_sessions import ArtifactPreviewSessionManager
from tests.test_continuous_deterministic_materialization import _load_models, _typed_task_outputs
from tests.test_materialized_bundle_production_runtime import _assemble_from_payload


def _request(url, payload=None):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        assert response.status == 200
        return json.load(response)


async def _assert_absent(provider, session_id):
    if provider == "e2b":
        from e2b import Sandbox
        from e2b.exceptions import NotFoundException

        with pytest.raises(NotFoundException):
            await asyncio.to_thread(Sandbox.get_info, session_id)
    else:
        result = await asyncio.to_thread(subprocess.run, ["docker", "inspect", session_id], capture_output=True, text=True)
        assert result.returncode != 0 and "No such" in result.stderr


@pytest.mark.asyncio
@pytest.mark.flaky(reruns=0)
@pytest.mark.parametrize("provider", ["e2b", "docker"])
async def test_factory_materialized_app_builds_runs_and_is_terminated(monkeypatch, provider):
    if os.getenv(f"MOZAIKS_RUN_GENERATED_APP_{provider.upper()}_SMOKE") != "1":
        pytest.skip(f"set MOZAIKS_RUN_GENERATED_APP_{provider.upper()}_SMOKE=1 to authorize this smoke")
    if provider == "e2b":
        assert os.getenv("E2B_API_KEY") and os.getenv("E2B_TEMPLATE")
    monkeypatch.setenv("SANDBOX_TTL_MINUTES", "5")
    monkeypatch.setenv("SANDBOX_MAX_SESSIONS", "1")
    monkeypatch.delenv("SANDBOX_TEMPLATE", raising=False)
    outputs = _typed_task_outputs(_load_models())
    outputs["reports.page"]["pages"][0]["sections"][0]["config"]["data_key"] = "reports"
    files, _ = await _assemble_from_payload(
        task_outputs=outputs,
        captured_theme_config={
            "assets": {},
            "colors": {"primary": {"main": "#0f766e"}, "secondary": {"main": "#475569"}},
        },
    )
    # The export boundary binds appId; this fixture stops before export.
    manifest = json.loads(files["app.json"])
    manifest["appId"] = "deterministic-reports"
    files["app.json"] = json.dumps(manifest)
    from factory_app.workflows.AppGenerator.tools.app_validation import validate_app_build
    from mozaiksai.core import adapters

    adapter = (
        E2BSandboxAdapter() if provider == "e2b"
        else DockerSandboxAdapter(image=os.environ["MOZAIKS_SMOKE_DOCKER_IMAGE"])
    )
    monkeypatch.setattr(adapters, "get_sandbox_adapter", lambda _: adapter)
    monkeypatch.setenv("MOZAIKS_APP_VALIDATION_STRATEGY", provider)
    validation = await validate_app_build(files, start_dev_server=False)
    assert validation["validation_status"] == "passed", validation
    assert validation["sandbox_terminated"] and validation["preview_url"] is None
    await _assert_absent(provider, validation["sandbox_session_id"])
    print(f"{provider} Factory build validation passed and terminated session={validation['sandbox_session_id']}", flush=True)
    manager = ArtifactPreviewSessionManager(provider_resolver=lambda: (provider, adapter))
    state = await manager.create_or_reuse(
        "runtime-smoke", app_id="factory", user_id="smoke", target_app_id="deterministic-reports",
        build_registry_id="runtime-smoke",
    )
    session_id = state.session_id
    print(f"{provider} smoke allocated session={session_id}", flush=True)
    try:
        await manager.sync(state.sandbox_id, [{"path": path, "content": text} for path, text in files.items()], [])
        await manager.start(state.sandbox_id)
        assert state.status == "running", state.last_error
        print(f"{provider} smoke preview={state.preview_url}", flush=True)
        health = await asyncio.to_thread(_request, f"{state.preview_url}/api/health")
        assert health["status"] == "healthy"
        page = await asyncio.to_thread(_request, f"{state.preview_url}/api/pages/reports")
        assert page["sections"][0]["config"]["api_endpoint"] == "/api/modules/reports/list_reports"
        action = await asyncio.to_thread(
            _request, f"{state.preview_url}/api/modules/reports/list_reports", {"params": {}},
        )
        assert action == {"reports": [{"id": "report-1", "title": "Readiness", "status": "ready"}]}
        playwright_module = os.getenv("MOZAIKS_E2B_SMOKE_PLAYWRIGHT_MODULE")
        if playwright_module:
            await asyncio.to_thread(
                subprocess.run,
                ["node", str(Path(__file__).with_name("e2b_preview_browser.cjs")),
                 playwright_module, state.preview_url, os.environ["MOZAIKS_E2B_SMOKE_SCREENSHOT_DIR"]],
                check=True, timeout=90,
            )
    finally:
        await manager.stop(state.sandbox_id)
        assert not manager._sessions
        await _assert_absent(provider, session_id)
        print(f"{provider} smoke termination confirmed session={session_id}", flush=True)
