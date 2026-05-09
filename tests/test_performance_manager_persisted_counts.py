from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest


perf_mod = importlib.import_module("mozaiksai.core.observability.performance_manager")
PerformanceManager = perf_mod.PerformanceManager
ChatPerfState = perf_mod.ChatPerfState


class _FakeChatCollection:
    def __init__(self) -> None:
        self.update_calls: list[tuple[dict, dict]] = []

    async def update_one(self, query, update):  # type: ignore[no-untyped-def]
        self.update_calls.append((dict(query), dict(update)))
        return SimpleNamespace(modified_count=1)


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