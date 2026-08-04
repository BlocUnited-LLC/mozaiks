from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _enable_client_console_bridge(monkeypatch):
    monkeypatch.setenv("CLIENT_CONSOLE_BRIDGE_ENABLED", "true")


@pytest.fixture
def runtime_client(monkeypatch):
    import mozaiksai.hosts.runtime as runtime

    async def _noop_startup():
        return None

    async def _noop_shutdown():
        return None

    monkeypatch.setattr(runtime, "_runtime_startup", _noop_startup)
    monkeypatch.setattr(runtime, "_runtime_shutdown", _noop_shutdown)
    with TestClient(runtime.app) as client:
        yield client


def test_client_console_bridge_ingests_logs(runtime_client, caplog):
    payload = {
        "source": "browser_console",
        "reason": "manual",
        "metadata": {
            "app_id": "demo-app",
            "chat_id": "chat-123",
            "workflow_name": "ExistingAppDiscovery",
            "pathname": "/chat",
        },
        "entries": [
            {
                "level": "info",
                "method": "info",
                "message": "artifact_loading_started",
                "args": ["artifact", {"component": "AppIntelligenceOverviewCard"}],
                "timestamp": "2026-08-02T11:15:00.000Z",
                "app_id": "demo-app",
                "chat_id": "chat-123",
                "workflow_name": "ExistingAppDiscovery",
                "pathname": "/chat",
            }
        ],
    }

    with caplog.at_level(logging.INFO):
        response = runtime_client.post("/api/debug/client-console", json=payload)

    assert response.status_code == 200
    assert response.json() == {"status": "accepted", "entries": 1}
    assert any(
        record.name == "mozaiks.workflow.frontend.browser_console"
        and "artifact_loading_started" in record.getMessage()
        for record in caplog.records
    )
