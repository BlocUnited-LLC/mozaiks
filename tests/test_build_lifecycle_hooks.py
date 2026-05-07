from __future__ import annotations

import pytest

from tests.import_utils import import_module_directly

_schema = import_module_directly("mozaiksai.core.workflow.pack.schema")
_build_lifecycle = import_module_directly(
    "factory_app.workflows.AppGenerator.tools.platform.build_lifecycle"
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
    payload = events[0]["payload"]
    assert payload["buildId"] == "journey_1"
    assert payload["buildRegistryId"] == "build_reg_1"
    assert payload["artifacts"]["exportDownloadUrl"] == "/api/apps/app_1/builds/build_reg_1/export"