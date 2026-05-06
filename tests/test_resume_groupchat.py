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


@pytest.mark.asyncio
async def test_handle_resume_request_uses_app_scope_for_pending_input_lookup(monkeypatch):
    resumer = GroupChatResumer()
    emitted = []
    captured: dict[str, object] = {}

    async def _fake_send(event, chat_id):  # noqa: ANN001
        emitted.append((chat_id, event))

    async def _fake_fetch(chat_id, app_id, projection=None):  # noqa: ANN001
        return {
            "status": 0,
            "workflow_name": "AppGenerator",
            "user_id": "user_1",
            "messages": [
                {"role": "assistant", "name": "AppPlanAgent", "content": "Please review the plan."},
            ],
        }

    async def _fake_resume_state(app_id, user_id):  # noqa: ANN001
        return None

    class _FakePM:
        async def get_pending_input_request(self, **kwargs):  # noqa: ANN003
            captured.update(kwargs)
            return None

    monkeypatch.setattr(resumer, "_fetch_chat_doc", _fake_fetch)
    monkeypatch.setattr(resumer, "_load_resume_state", _fake_resume_state)
    monkeypatch.setattr(resumer, "_get_persistence_manager", lambda: _FakePM())

    await resumer.handle_resume_request(
        chat_id="chat_1",
        app_id="app_1",
        last_client_index=-1,
        send_event=_fake_send,
    )

    assert emitted
    assert captured == {"chat_id": "chat_1", "app_id": "app_1"}


@pytest.mark.asyncio
async def test_handle_resume_request_re_emits_awaiting_reply_for_generic_feedback_pending_input(monkeypatch):
    resumer = GroupChatResumer()
    emitted = []

    async def _fake_send(event, chat_id):  # noqa: ANN001
        emitted.append((chat_id, event))

    async def _fake_fetch(chat_id, app_id, projection=None):  # noqa: ANN001
        return {
            "status": 0,
            "workflow_name": "AppGenerator",
            "user_id": "user_1",
            "messages": [
                {"role": "assistant", "name": "InterviewAgent", "content": "Tell me about the app."},
            ],
        }

    async def _fake_resume_state(app_id, user_id):  # noqa: ANN001
        return None

    class _FakePM:
        async def get_pending_input_request(self, **kwargs):  # noqa: ANN003
            return {
                "request_id": "req-1",
                "agent": "InterviewAgent",
                "prompt": "",
                "component_type": "UserInputRequest",
                "workflow_name": "AppGenerator",
                "tool_name": "UserInputRequest",
                "display": "composer",
                "interaction_type": "input_request",
                "raw_payload": {
                    "resume_ui_kind": "awaiting_reply",
                },
            }

    monkeypatch.setattr(resumer, "_fetch_chat_doc", _fake_fetch)
    monkeypatch.setattr(resumer, "_load_resume_state", _fake_resume_state)
    monkeypatch.setattr(resumer, "_get_persistence_manager", lambda: _FakePM())

    await resumer.handle_resume_request(
        chat_id="chat_1",
        app_id="app_1",
        last_client_index=-1,
        send_event=_fake_send,
    )

    awaiting_events = [event for _chat_id, event in emitted if event.get("kind") == "awaiting_reply"]
    assert len(awaiting_events) == 1
    awaiting_event = awaiting_events[0]
    assert awaiting_event["agent"] == "InterviewAgent"
    assert awaiting_event["chat_id"] == "chat_1"
    assert awaiting_event["workflow_name"] == "AppGenerator"
    assert awaiting_event["display"] == "composer"
    assert awaiting_event["interaction_type"] == "input_request"
    assert awaiting_event["reason"] == "awaiting_user_reply"
    assert awaiting_event["prompt"] == ""
    assert awaiting_event["source_agent"] == "InterviewAgent"
    assert awaiting_event["replay"] is True
    assert awaiting_event["metadata"] == {"source": "resume_pending_input"}
    assert awaiting_event["timestamp"]
