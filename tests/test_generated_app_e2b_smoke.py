"""Opt-in paid runtime smoke, not a live-LLM generation journey."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import urllib.request
from pathlib import Path

import pytest

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


@pytest.mark.asyncio
@pytest.mark.flaky(reruns=0)
async def test_factory_materialized_app_runs_in_e2b_and_is_terminated(monkeypatch):
    if os.getenv("MOZAIKS_RUN_GENERATED_APP_E2B_SMOKE") != "1":
        pytest.skip("set MOZAIKS_RUN_GENERATED_APP_E2B_SMOKE=1 to authorize the paid smoke")
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
    adapter = E2BSandboxAdapter()
    manager = ArtifactPreviewSessionManager(provider_resolver=lambda: ("e2b", adapter))
    state = await manager.create_or_reuse(
        "runtime-smoke", app_id="factory", user_id="smoke", target_app_id="deterministic-reports",
        build_registry_id="runtime-smoke",
    )
    session_id = state.session_id
    print(f"E2B smoke allocated session={session_id}", flush=True)
    try:
        await manager.sync(state.sandbox_id, [{"path": path, "content": text} for path, text in files.items()], [])
        await manager.start(state.sandbox_id)
        assert state.status == "running", state.last_error
        print(f"E2B smoke preview={state.preview_url}", flush=True)
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
        assert not manager._sessions and not adapter._sessions
        from e2b import Sandbox
        from e2b.exceptions import NotFoundException

        with pytest.raises(NotFoundException):
            await asyncio.to_thread(Sandbox.get_info, session_id)
        print(f"E2B smoke termination confirmed session={session_id}", flush=True)
