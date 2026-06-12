from __future__ import annotations

from types import SimpleNamespace

import pytest


class _FakeCollection:
    async def find_one(self, query, projection=None):  # noqa: ANN001
        _ = query
        _ = projection
        return {
            "_id": "chat-1",
            "workflow_name": "AgentGenerator",
            "cache_seed": 123,
            "status": 0,
            "workflow_ui_state": {
                "schema_version": 1,
                "last_artifact": {"tool_name": "RenderPlan"},
                "pending_input_request": None,
                "tool_calls": {},
            },
        }


@pytest.mark.asyncio
async def test_platform_chat_meta_returns_run_history_count(monkeypatch: pytest.MonkeyPatch) -> None:
    from mozaiksai.hosts import platform as platform_app

    async def _fake_chat_coll():
        return _FakeCollection()

    async def _fake_load_run_history(*, chat_id: str, app_id: str):
        assert chat_id == "chat-1"
        assert app_id == "app-1"
        return [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]

    monkeypatch.setattr(platform_app.runtime_app, "_chat_coll", _fake_chat_coll)
    monkeypatch.setattr(platform_app.runtime_app.persistence_manager, "load_run_history", _fake_load_run_history)

    payload = await platform_app.chat_meta(
        app_id="app-1",
        workflow_name="AgentGenerator",
        chat_id="chat-1",
        principal=SimpleNamespace(user_id="user-1"),
    )

    assert payload["exists"] is True
    assert payload["chat_id"] == "chat-1"
    assert payload["workflow_name"] == "AgentGenerator"
    assert payload["run_history_count"] == 2
    assert payload["last_artifact"] == {"tool_name": "RenderPlan"}
    assert "last_sequence" not in payload
