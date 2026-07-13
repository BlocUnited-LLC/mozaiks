from __future__ import annotations

import pytest

from factory_app.workflows.ValueEngine.tools import create_app_record as module


class _Context(dict):
    def set(self, key, value):  # noqa: ANN001
        self[key] = value


@pytest.mark.asyncio
async def test_value_engine_create_app_record_skips_without_user_intent(monkeypatch) -> None:
    called = False

    async def fake_create(payload):  # noqa: ANN001
        nonlocal called
        called = True
        return {"success": True, "app": {"build_registry_id": "appreg_1", "app_id": "app_1"}}

    async def no_user_intent(**_kwargs):  # noqa: ANN003
        return False

    monkeypatch.setattr(module, "_create_studio_app", fake_create)
    monkeypatch.setattr(module, "_has_persisted_user_intent", no_user_intent)

    result = await module.create_app_record(
        _Context(
            {
                "app_id": "factory-app",
                "chat_id": "chat_1",
                "workflow_name": "ValueEngine",
                "user_id": "user_1",
            }
        )
    )

    assert result["success"] is False
    assert result["skipped"] is True
    assert called is False


@pytest.mark.asyncio
async def test_value_engine_create_app_record_runs_after_user_intent(monkeypatch) -> None:
    async def fake_create(payload):  # noqa: ANN001
        assert payload["name"] is None
        assert payload["name_source"] == "provisional"
        assert payload["chat_app_id"] == "factory-app"
        assert payload["active_chat_id"] == "chat_1"
        assert payload["build_context_profile"]["workflow_sequence"] == "build"
        assert payload["current_build_run"]["build_id"] == "chat_1"
        assert payload["current_build_run"]["active_workflow_id"] == "ValueEngine"
        return {"success": True, "app": {"build_registry_id": "appreg_1", "app_id": "build-app"}}

    async def has_user_intent(**_kwargs):  # noqa: ANN003
        return True

    monkeypatch.setattr(module, "_create_studio_app", fake_create)
    monkeypatch.setattr(module, "_has_persisted_user_intent", has_user_intent)

    ctx = _Context(
        {
            "app_id": "factory-app",
            "chat_id": "chat_1",
            "workflow_name": "ValueEngine",
            "user_id": "user_1",
        }
    )
    result = await module.create_app_record(ctx)

    assert result["success"] is True
    assert result["build_registry_id"] == "appreg_1"
    assert result["app_id"] == "build-app"
    assert ctx["build_registry_id"] == "appreg_1"
    assert ctx["app_id"] == "build-app"
