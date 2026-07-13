from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from factory_app.workflows.AppGenerator.tools import update_app_record


@pytest.mark.asyncio
async def test_update_build_status_posts_artifact_aware_payload(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _AsyncClient:
        def __init__(self, *, timeout: float) -> None:
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def put(self, url: str, *, json: dict):
            captured["url"] = url
            captured["json"] = json
            return SimpleNamespace(status_code=200)

    monkeypatch.setitem(
        sys.modules,
        "httpx",
        SimpleNamespace(AsyncClient=_AsyncClient),
    )

    await update_app_record.update_build_status(
        build_registry_id="appreg_1",
        status="review",
        bundle_path="generated/apps/app_1/build_1/app",
        artifact_version_id="av_bundle_1",
        workflow_sequence="build",
        active_chat_id="chat_1",
        active_workflow_id="AppGenerator",
        current_build_run={
            "build_id": "build_1",
            "workflow_sequence": "build",
            "status": "review",
            "artifact_version_id": "av_bundle_1",
        },
    )

    assert captured["url"] == "http://localhost:8000/api/studio/apps/appreg_1/status"
    assert captured["json"] == {
        "build_registry_id": "appreg_1",
        "status": "review",
        "bundle_path": "generated/apps/app_1/build_1/app",
        "artifact_version_id": "av_bundle_1",
        "workflow_sequence": "build",
        "active_chat_id": "chat_1",
        "active_workflow_id": "AppGenerator",
        "current_build_run": {
            "build_id": "build_1",
            "workflow_sequence": "build",
            "status": "review",
            "artifact_version_id": "av_bundle_1",
        },
    }
