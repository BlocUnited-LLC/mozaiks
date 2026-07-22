from __future__ import annotations

import pytest

from factory_app.workflows.ValueEngine.tools import create_app_record as module


class _Context(dict):
    def set(self, key, value):  # noqa: ANN001
        self[key] = value


@pytest.mark.asyncio
async def test_value_engine_create_app_record_creates_provisional_record_on_start(monkeypatch) -> None:
    captured_payload = None

    async def fake_create(payload):  # noqa: ANN001
        nonlocal captured_payload
        captured_payload = payload
        return {"success": True, "app": {"build_registry_id": "appreg_1", "app_id": payload["app_id"]}}

    monkeypatch.setattr(module, "_create_studio_app", fake_create)

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
    assert result["app_id"].startswith("draft-build-")
    assert captured_payload["name"] is None
    assert captured_payload["name_source"] == "provisional"
    assert captured_payload["app_id"] == result["app_id"]
    assert captured_payload["chat_app_id"] == "factory-app"
    assert captured_payload["active_chat_id"] == "chat_1"
    assert captured_payload["current_build_run"]["build_id"] == "chat_1"
    assert ctx["build_registry_id"] == "appreg_1"
    assert ctx["app_id"] == result["app_id"]
    assert ctx["chat_app_id"] == "factory-app"


def test_value_engine_create_app_record_uses_stable_provisional_app_id() -> None:
    first = module._provisional_build_app_id(  # noqa: SLF001
        chat_app_id="factory-app",
        chat_id="chat_1",
        build_id="chat_1",
    )
    second = module._provisional_build_app_id(  # noqa: SLF001
        chat_app_id="factory-app",
        chat_id="chat_1",
        build_id="chat_1",
    )
    other = module._provisional_build_app_id(  # noqa: SLF001
        chat_app_id="factory-app",
        chat_id="chat_2",
        build_id="chat_2",
    )

    assert first == second
    assert first != other
    assert first.startswith("draft-build-")


@pytest.mark.asyncio
async def test_value_engine_create_app_record_skips_without_chat_id(monkeypatch) -> None:
    called = False

    async def fake_create(payload):  # noqa: ANN001
        nonlocal called
        called = True
        return {"success": True, "app": {"build_registry_id": "appreg_1", "app_id": payload["app_id"]}}

    monkeypatch.setattr(module, "_create_studio_app", fake_create)

    result = await module.create_app_record(
        _Context(
            {
                "app_id": "factory-app",
                "workflow_name": "ValueEngine",
                "user_id": "user_1",
            }
        )
    )

    assert result["success"] is False
    assert result["skipped"] is True
    assert result["reason"] == "No workflow chat id available for app registry tracking."
    assert called is False


@pytest.mark.asyncio
async def test_value_engine_create_app_record_preserves_chat_app_id_on_reopen(monkeypatch) -> None:
    async def fake_create(payload):  # noqa: ANN001
        assert payload["name"] == "Concept App"
        assert payload["name_source"] == "manual"
        assert payload["app_id"] == "build-app"
        assert payload["chat_app_id"] == "factory-app"
        assert payload["active_chat_id"] == "chat_1"
        assert payload["build_context_profile"]["workflow_sequence"] == "build"
        assert payload["current_build_run"]["build_id"] == "chat_1"
        assert payload["current_build_run"]["active_workflow_id"] == "ValueEngine"
        return {"success": True, "app": {"build_registry_id": "appreg_1", "app_id": "build-app"}}

    monkeypatch.setattr(module, "_create_studio_app", fake_create)

    ctx = _Context(
        {
            "app_id": "build-app",
            "chat_app_id": "factory-app",
            "build_registry_id": "appreg_1",
            "app_name": "Concept App",
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
    assert ctx["chat_app_id"] == "factory-app"
