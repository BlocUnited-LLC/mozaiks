from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

from tests.import_utils import import_module_directly

_auto_tool_mod = import_module_directly("mozaiksai.core.events.auto_tool_handler")
AutoToolBinding = _auto_tool_mod.AutoToolBinding
AutoToolEventHandler = _auto_tool_mod.AutoToolEventHandler

_persistence_mod = import_module_directly("mozaiksai.core.data.persistence.persistence_manager")
AG2PersistenceManager = _persistence_mod.AG2PersistenceManager


class SmokePresentation(BaseModel):
    summary: str
    worker_name: str


class _ContextVariables:
    def __init__(self, initial: dict[str, object] | None = None) -> None:
        self.data = dict(initial or {})

    def get(self, key: str, default: object | None = None) -> object | None:
        return self.data.get(key, default)

    def set(self, key: str, value: object) -> None:
        self.data[key] = value


async def _consume_resume_state(
    summary: str,
    worker_name: str,
    context_variables: _ContextVariables,
) -> dict[str, object]:
    context_variables.set("_mfj_resume_pending", False)
    context_variables.set("_mfj_resume_consumed_nonce", "nonce-123")
    context_variables.set("smoke_presented_summary", summary)
    context_variables.set("smoke_presented_worker", worker_name)
    return {"status": "ok"}


@pytest.mark.asyncio
async def test_handle_tool_dispatch_persists_updated_context(monkeypatch):
    handler = AutoToolEventHandler()
    binding = AutoToolBinding(
        model_name="SmokePresentation",
        agent_name="PresenterAgent",
        tool_name="consume_resume_state",
        function=_consume_resume_state,
        param_names=("summary", "worker_name", "context_variables"),
        accepts_context=True,
        ui_config={},
        model_cls=SmokePresentation,
    )

    monkeypatch.setattr(handler, "_resolve_binding", AsyncMock(return_value=binding))
    monkeypatch.setattr(handler, "_emit_tool_call", AsyncMock())
    monkeypatch.setattr(handler, "_emit_tool_result", AsyncMock())
    monkeypatch.setattr(handler, "_register_turn", AsyncMock())

    persist_mock = AsyncMock()

    class _FakePersistenceManager:
        async def persist_context_variables(self, **kwargs):
            await persist_mock(**kwargs)

    monkeypatch.setattr(_auto_tool_mod, "AG2PersistenceManager", _FakePersistenceManager)

    pattern_context = _ContextVariables({
        "_mfj_resume_pending": True,
        "_mfj_resume_nonce": "nonce-123",
    })
    event = {
        "auto_tool_mode": True,
        "agent_name": "PresenterAgent",
        "model_name": "SmokePresentation",
        "structured_data": {
            "summary": "Smoke path summarized.",
            "worker_name": "SmokeChild",
        },
        "context": {
            "workflow_name": "SmokeParent",
            "chat_id": "chat-1",
            "app_id": "app-1",
        },
        "turn_idempotency_key": "turn-1",
        "_pattern_context_ref": pattern_context,
    }

    await handler.handle_tool_dispatch(event)

    persist_mock.assert_awaited_once()
    persisted = persist_mock.await_args.kwargs
    assert persisted["chat_id"] == "chat-1"
    assert persisted["app_id"] == "app-1"
    assert persisted["variables"]["_mfj_resume_pending"] is False
    assert persisted["variables"]["_mfj_resume_consumed_nonce"] == "nonce-123"
    assert persisted["variables"]["smoke_presented_summary"] == "Smoke path summarized."


@pytest.mark.asyncio
async def test_persist_context_variables_filters_canonical_fields():
    manager = AG2PersistenceManager.__new__(AG2PersistenceManager)
    fake_coll = MagicMock()
    fake_coll.update_one = AsyncMock()
    manager._coll = AsyncMock(return_value=fake_coll)

    await manager.persist_context_variables(
        chat_id="chat-1",
        app_id="app-1",
        variables={
            "chat_id": "override-me",
            "workflow_name": "override-me",
            "_mfj_resume_pending": False,
            "smoke_presented_summary": "Smoke path summarized.",
        },
    )

    fake_coll.update_one.assert_awaited_once()
    filter_doc, update_doc = fake_coll.update_one.await_args.args
    assert filter_doc["_id"] == "chat-1"
    assert filter_doc["app_id"] == "app-1"
    assert "chat_id" not in update_doc["$set"]
    assert "workflow_name" not in update_doc["$set"]
    assert update_doc["$set"]["_mfj_resume_pending"] is False
    assert update_doc["$set"]["smoke_presented_summary"] == "Smoke path summarized."
    assert isinstance(update_doc["$set"]["last_updated_at"], datetime)