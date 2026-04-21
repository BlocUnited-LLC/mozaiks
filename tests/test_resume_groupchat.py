from __future__ import annotations

import pytest

from tests.import_utils import import_module_directly

_resume_mod = import_module_directly("mozaiksai.core.transport.resume_groupchat")

GroupChatResumer = _resume_mod.GroupChatResumer


@pytest.mark.asyncio
async def test_handle_resume_request_emits_resume_state(monkeypatch):
    resumer = GroupChatResumer()
    emitted = []

    async def _fake_send(event, chat_id):  # noqa: ANN001
        emitted.append((chat_id, event))

    async def _fake_fetch(chat_id, app_id, projection=None):  # noqa: ANN001
        return {
            "status": 0,
            "workflow_name": "AgentGenerator",
            "user_id": "user_1",
            "messages": [
                {"role": "user", "name": "user", "content": "hello"},
                {"role": "assistant", "name": "BuilderAgent", "content": "hi"},
            ],
        }

    async def _fake_resume_state(app_id, user_id):  # noqa: ANN001
        return {
            "lifecycle_state": "active",
            "current_workflow_id": "AgentGenerator",
            "current_chat_id": "chat_1",
            "journey_key": "build",
        }

    monkeypatch.setattr(resumer, "_fetch_chat_doc", _fake_fetch)
    monkeypatch.setattr(resumer, "_load_resume_state", _fake_resume_state)

    summary = await resumer.handle_resume_request(
        chat_id="chat_1",
        app_id="app_1",
        last_client_index=1,
        send_event=_fake_send,
    )

    assert summary["replayed_messages"] == 0
    assert emitted
    _, boundary = emitted[-1]
    assert boundary["kind"] == "resume_boundary"
    assert boundary["resume_state"]["current_chat_id"] == "chat_1"
    assert boundary["resume_state"]["journey_key"] == "build"
