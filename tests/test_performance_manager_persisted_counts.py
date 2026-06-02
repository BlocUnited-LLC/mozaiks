from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


perf_mod = importlib.import_module("mozaiksai.core.observability.performance_manager")
PerformanceManager = perf_mod.PerformanceManager
ChatPerfState = perf_mod.ChatPerfState


class _FakeChatCollection:
    def __init__(self, docs: dict[str, dict] | None = None) -> None:
        self.update_calls: list[tuple[dict, dict]] = []
        self.docs = dict(docs or {})

    async def update_one(self, query, update):  # type: ignore[no-untyped-def]
        self.update_calls.append((dict(query), dict(update)))
        return SimpleNamespace(modified_count=1)

    async def find_one(self, query, projection=None):  # type: ignore[no-untyped-def]
        doc = self.docs.get(str(query.get("_id") or ""))
        if doc is None:
            return None
        if not isinstance(projection, dict):
            return dict(doc)
        projected = {"_id": doc.get("_id")}
        for key in projection:
            if key == "_id":
                continue
            if projection.get(key) and key in doc:
                projected[key] = doc[key]
        return projected


@pytest.mark.asyncio
async def test_record_tool_call_persists_chat_session_tool_and_error_counters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = PerformanceManager()
    manager._states["chat-1"] = ChatPerfState(
        chat_id="chat-1",
        app_id="app-1",
        workflow_name="ValueEngine",
        user_id="user-1",
    )
    collection = _FakeChatCollection()

    async def _fake_get_coll():
        return collection

    monkeypatch.setattr(manager, "_get_coll", _fake_get_coll)

    await manager.record_tool_call("chat-1", "search_docs", True)
    await manager.record_tool_call("chat-1", "save_artifact", False)

    state = manager._states["chat-1"]
    assert state.tool_calls == 2
    assert state.errors == 1
    assert len(collection.update_calls) == 2
    assert collection.update_calls[0][0] == {"_id": "chat-1"}
    assert collection.update_calls[0][1]["$inc"] == {"tool_calls_final": 1}
    assert collection.update_calls[1][1]["$inc"] == {"tool_calls_final": 1, "errors_final": 1}


@pytest.mark.asyncio
async def test_record_workflow_start_hydrates_session_router_and_journey_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = PerformanceManager()
    collection = _FakeChatCollection(
        {
            "chat-1": {
                "_id": "chat-1",
                "session_router_session_id": "session_router::app-1::user-1",
                "journey_instance_id": "journey-1",
            }
        }
    )

    async def _fake_get_coll():
        return collection

    monkeypatch.setattr(manager, "_get_coll", _fake_get_coll)
    manager._persistence = SimpleNamespace(create_chat_session=AsyncMock())

    await manager.record_workflow_start("chat-1", "app-1", "ValueEngine", "user-1")

    state = manager._states["chat-1"]
    assert state.session_router_session_id == "session_router::app-1::user-1"
    assert state.journey_instance_id == "journey-1"

    snapshot = await manager.snapshot_chat("chat-1")
    assert snapshot is not None
    assert snapshot["session_router_session_id"] == "session_router::app-1::user-1"
    assert snapshot["journey_instance_id"] == "journey-1"