from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from tests.import_utils import import_module_directly

_schema = import_module_directly("mozaiksai.core.workflow.pack.schema")
_build_lifecycle = import_module_directly(
    "factory_app.workflows._shared.platform.build_lifecycle"
)

parse_global_pack_graph = _schema.parse_global_pack_graph


def _make_build_pack():
    return parse_global_pack_graph(
        {
            "version": 3,
            "workflows": [{"id": "ValueEngine"}, {"id": "AppGenerator"}],
            "transitions": [],
            "workflow_sequences": [
                {
                    "id": "build",
                    "steps": [
                        {"workflows": ["ValueEngine"]},
                        {"workflows": ["AppGenerator"]},
                    ],
                }
            ],
        }
    )


@pytest.mark.asyncio
async def test_emit_build_started_accepts_runtime_hook_kwargs_and_emits_for_first_journey_workflow(monkeypatch):
    events = []

    async def fake_upsert_outbox_event(**kwargs):  # noqa: ANN003
        events.append(kwargs)
        return "outbox_1"

    async def fake_context(**kwargs):  # noqa: ANN003
        return {
            "app_id": kwargs["app_id"],
            "build_id": "journey_1",
            "build_registry_id": None,
            "journey_instance_id": "journey_1",
            "journey_key": "build",
            "journey_position": 0,
            "chat_id": kwargs["chat_id"],
            "execution_id": kwargs["execution_id"],
        }

    monkeypatch.setattr(_build_lifecycle, "load_global_pack_graph", lambda: _make_build_pack())
    monkeypatch.setattr(_build_lifecycle, "_resolve_build_event_context", fake_context)
    monkeypatch.setattr(_build_lifecycle, "upsert_outbox_event", fake_upsert_outbox_event)
    monkeypatch.setattr(_build_lifecycle, "_spawn_delivery", lambda *args, **kwargs: None)

    await _build_lifecycle.emit_build_started(
        app_id="app_1",
        execution_id="exec_1",
        chat_id="chat_1",
        user_id="user_1",
        workflow_name="ValueEngine",
    )

    assert len(events) == 1
    assert events[0]["event_type"] == "build.started"
    assert events[0]["status"] == "started"
    assert events[0]["user_id"] == "user_1"
    assert events[0]["workflow_name"] == "ValueEngine"
    payload = events[0]["payload"]
    assert payload["buildId"] == "journey_1"
    assert payload["journeyId"] == "build"
    assert payload["journeyInstanceId"] == "journey_1"
    assert payload["executionId"] == "exec_1"


@pytest.mark.asyncio
async def test_emit_build_completed_skips_non_terminal_journey_workflow(monkeypatch):
    events = []

    async def fake_upsert_outbox_event(**kwargs):  # noqa: ANN003
        events.append(kwargs)
        return "outbox_1"

    async def fake_context(**kwargs):  # noqa: ANN003
        return {
            "app_id": kwargs["app_id"],
            "build_id": "journey_1",
            "build_registry_id": None,
            "journey_instance_id": "journey_1",
            "journey_key": "build",
            "journey_position": 0,
            "chat_id": kwargs["chat_id"],
            "execution_id": kwargs["execution_id"],
        }

    monkeypatch.setattr(_build_lifecycle, "load_global_pack_graph", lambda: _make_build_pack())
    monkeypatch.setattr(_build_lifecycle, "_resolve_build_event_context", fake_context)
    monkeypatch.setattr(_build_lifecycle, "upsert_outbox_event", fake_upsert_outbox_event)
    monkeypatch.setattr(_build_lifecycle, "_spawn_delivery", lambda *args, **kwargs: None)

    await _build_lifecycle.emit_build_completed(
        app_id="app_1",
        execution_id="exec_1",
        chat_id="chat_1",
        user_id="user_1",
        workflow_name="ValueEngine",
    )

    assert events == []


@pytest.mark.asyncio
async def test_emit_build_completed_emits_for_terminal_journey_workflow(monkeypatch):
    events = []

    async def fake_upsert_outbox_event(**kwargs):  # noqa: ANN003
        events.append(kwargs)
        return "outbox_1"

    async def fake_context(**kwargs):  # noqa: ANN003
        return {
            "app_id": kwargs["app_id"],
            "build_id": "journey_1",
            "build_registry_id": "build_reg_1",
            "journey_instance_id": "journey_1",
            "journey_key": "build",
            "journey_position": 1,
            "chat_id": kwargs["chat_id"],
            "execution_id": kwargs["execution_id"],
        }

    async def fake_get_build_artifacts(**kwargs):  # noqa: ANN003
        assert kwargs["export_build_id"] == "build_reg_1"
        return {
            "previewUrl": None,
            "exportDownloadUrl": "/api/apps/app_1/builds/build_reg_1/export",
        }

    monkeypatch.setattr(_build_lifecycle, "load_global_pack_graph", lambda: _make_build_pack())
    monkeypatch.setattr(_build_lifecycle, "_resolve_build_event_context", fake_context)
    monkeypatch.setattr(_build_lifecycle, "get_build_artifacts", fake_get_build_artifacts)
    monkeypatch.setattr(_build_lifecycle, "upsert_outbox_event", fake_upsert_outbox_event)
    monkeypatch.setattr(_build_lifecycle, "_spawn_delivery", lambda *args, **kwargs: None)

    await _build_lifecycle.emit_build_completed(
        app_id="app_1",
        execution_id="exec_2",
        chat_id="chat_2",
        user_id="user_1",
        workflow_name="AppGenerator",
    )

    assert len(events) == 1
    assert events[0]["event_type"] == "build.completed"
    assert events[0]["status"] == "completed"
    assert events[0]["user_id"] == "user_1"
    assert events[0]["workflow_name"] == "AppGenerator"
    payload = events[0]["payload"]
    assert payload["buildId"] == "journey_1"
    assert payload["buildRegistryId"] == "build_reg_1"
    assert payload["artifacts"]["exportDownloadUrl"] == "/api/apps/app_1/builds/build_reg_1/export"


@pytest.mark.asyncio
async def test_emit_build_completed_schedules_anonymized_telemetry_for_terminal_journey_workflow(monkeypatch):
    from mozaiksai.core import telemetry as telemetry_mod

    events = []
    telemetry_events = []

    async def fake_upsert_outbox_event(**kwargs):  # noqa: ANN003
        events.append(kwargs)
        return "outbox_1"

    async def fake_context(**kwargs):  # noqa: ANN003
        return {
            "app_id": kwargs["app_id"],
            "build_id": "journey_1",
            "build_registry_id": "build_reg_1",
            "journey_instance_id": "journey_1",
            "journey_key": "build",
            "journey_position": 1,
            "chat_id": kwargs["chat_id"],
            "execution_id": kwargs["execution_id"],
        }

    async def fake_get_build_artifacts(**kwargs):  # noqa: ANN003
        return {
            "previewUrl": None,
            "exportDownloadUrl": "/api/apps/app_1/builds/build_reg_1/export",
        }

    async def fake_emit_build_telemetry(payload):  # noqa: ANN001
        telemetry_events.append(payload)

    monkeypatch.setattr(_build_lifecycle, "load_global_pack_graph", lambda: _make_build_pack())
    monkeypatch.setattr(_build_lifecycle, "_resolve_build_event_context", fake_context)
    monkeypatch.setattr(_build_lifecycle, "get_build_artifacts", fake_get_build_artifacts)
    monkeypatch.setattr(_build_lifecycle, "upsert_outbox_event", fake_upsert_outbox_event)
    monkeypatch.setattr(_build_lifecycle, "_spawn_delivery", lambda *args, **kwargs: None)
    monkeypatch.setattr(telemetry_mod, "emit_build_telemetry", fake_emit_build_telemetry)

    await _build_lifecycle.emit_build_completed(
        app_id="app_1",
        execution_id="exec_2",
        chat_id="chat_2",
        user_id="user_1",
        workflow_name="AppGenerator",
    )
    await asyncio.sleep(0)

    assert len(events) == 1
    assert len(telemetry_events) == 1
    telemetry_payload = telemetry_events[0]
    assert telemetry_payload["workflow_name"] == "AppGenerator"
    assert telemetry_payload["final_status"] == "completed"
    assert telemetry_payload["build_id_hash"]
    assert "build_reg_1" not in json.dumps(telemetry_payload)


@pytest.mark.asyncio
async def test_emit_build_failed_schedules_anonymized_telemetry_for_journey_workflow(monkeypatch):
    from mozaiksai.core import telemetry as telemetry_mod

    events = []
    telemetry_events = []

    async def fake_upsert_outbox_event(**kwargs):  # noqa: ANN003
        events.append(kwargs)
        return "outbox_1"

    async def fake_context(**kwargs):  # noqa: ANN003
        return {
            "app_id": kwargs["app_id"],
            "build_id": "journey_1",
            "build_registry_id": "build_reg_1",
            "journey_instance_id": "journey_1",
            "journey_key": "build",
            "journey_position": 0,
            "chat_id": kwargs["chat_id"],
            "execution_id": kwargs["execution_id"],
        }

    async def fake_emit_build_telemetry(payload):  # noqa: ANN001
        telemetry_events.append(payload)

    monkeypatch.setattr(_build_lifecycle, "load_global_pack_graph", lambda: _make_build_pack())
    monkeypatch.setattr(_build_lifecycle, "_resolve_build_event_context", fake_context)
    monkeypatch.setattr(_build_lifecycle, "upsert_outbox_event", fake_upsert_outbox_event)
    monkeypatch.setattr(_build_lifecycle, "_spawn_delivery", lambda *args, **kwargs: None)
    monkeypatch.setattr(telemetry_mod, "emit_build_telemetry", fake_emit_build_telemetry)

    await _build_lifecycle.emit_build_failed(
        app_id="app_1",
        execution_id="exec_3",
        chat_id="chat_3",
        user_id="user_1",
        workflow_name="ValueEngine",
        error="boom",
    )
    await asyncio.sleep(0)

    assert len(events) == 1
    assert events[0]["event_type"] == "build.failed"
    assert len(telemetry_events) == 1
    telemetry_payload = telemetry_events[0]
    assert telemetry_payload["workflow_name"] == "ValueEngine"
    assert telemetry_payload["final_status"] == "failed"
    assert telemetry_payload["build_id_hash"]
    assert "build_reg_1" not in json.dumps(telemetry_payload)


@pytest.mark.asyncio
async def test_deliver_outbox_event_posts_payload_and_marks_attempt(monkeypatch):
    attempts = []
    posted = []

    async def fake_get_outbox_event(**kwargs):  # noqa: ANN003
        assert kwargs == {"outbox_id": "outbox_1"}
        return {
            "_id": "outbox_1",
            "app_id": "app_1",
            "payload": {"eventType": "build.started"},
        }

    async def fake_mark_attempt(**kwargs):  # noqa: ANN003
        attempts.append(kwargs)

    class _FakeClient:
        async def post_build_event(self, **kwargs):  # noqa: ANN003
            posted.append(kwargs)
            return SimpleNamespace(ok=True, status_code=202, error=None)

    monkeypatch.setattr(_build_lifecycle, "get_outbox_event", fake_get_outbox_event)
    monkeypatch.setattr(_build_lifecycle, "mark_attempt", fake_mark_attempt)
    monkeypatch.setattr(_build_lifecycle, "_build_events_client", lambda: _FakeClient())

    await _build_lifecycle._deliver_outbox_event_once(outbox_event_id="outbox_1")

    assert posted == [{"app_id": "app_1", "payload": {"eventType": "build.started"}}]
    assert attempts == [
        {
            "outbox_id": "outbox_1",
            "ok": True,
            "status_code": 202,
            "error": None,
        }
    ]

