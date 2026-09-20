from __future__ import annotations

import pytest
from pydantic import ValidationError

from factory_app.workflows.AppGenerator.tools.platform import build_events_client
from mozaiksai.core.studio.build_events import BuildLifecycleEvent


def event_payload(**changes):
    return {
        "eventType": "build.started", "status": "started",
        "idempotencyKey": "build:factory:build_1:build.started",
        "appId": "factory", "targetAppId": "tracker", "buildId": "build_1",
        "buildRegistryId": "registry_1", "phase": "genesis", "userId": "alice",
        "chatId": "chat_1", "workflowName": "ValueEngine", "timestamp": "2026-09-11T12:00:00Z",
        **changes,
    }


def test_event_preserves_distinct_host_target_and_registry():
    event = BuildLifecycleEvent.model_validate(event_payload())
    assert event.app_id == "factory"
    assert event.target_app_id == "tracker"
    assert event.build_registry_id == "registry_1"
    assert event.model_dump(by_alias=True)["eventType"] == "build.started"


@pytest.mark.parametrize("changes", [
    {"eventType": "build.unknown"}, {"status": "completed"},
    {"targetAppId": "factory"}, {"idempotencyKey": "someone-else"},
    {"targetAppId": "../factory"}, {"timestamp": "2026-09-11T12:00:00"},
    {"build_registry_id": "another-registry"}, {"phase": "arbitrary"},
])
def test_invalid_or_ambiguous_events_fail_closed(changes):
    with pytest.raises(ValidationError):
        BuildLifecycleEvent.model_validate(event_payload(**changes))


@pytest.mark.asyncio
@pytest.mark.parametrize("status,ok", [(202, True), (409, False), (403, False)])
async def test_delivery_does_not_swallow_conflicts(monkeypatch, status, ok):
    class Response:
        async def __aenter__(self):
            self.status = status
            return self

        async def __aexit__(self, *args):
            pass

        async def text(self):
            return "conflict"

    calls = []

    class Session:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return Response()

    monkeypatch.setattr(build_events_client.aiohttp, "ClientSession", Session)
    client = build_events_client.BuildEventsClient(
        base_url="http://localhost:8000", api_key="internal-test-key", enabled=True,
    )
    result = await client.post_build_event(app_id="factory", payload=event_payload())
    assert result.ok is ok
    assert result.status_code == status
    assert len(calls) == 1
    assert calls[0][1]["headers"]["X-Idempotency-Key"] == event_payload()["idempotencyKey"]
