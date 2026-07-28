from __future__ import annotations

import pytest

from tests.import_utils import import_module_directly

_replay_mod = import_module_directly("mozaiksai.core.transport.run_replay")

WorkflowRunReplayer = _replay_mod.WorkflowRunReplayer


@pytest.mark.asyncio
async def test_handle_resume_request_emits_session_snapshot(monkeypatch):
    replayer = WorkflowRunReplayer()
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

    class _FakePM:
        async def load_run_history(self, **kwargs):  # noqa: ANN003
            return [
                {"role": "user", "name": "user", "content": "hello"},
                {"role": "assistant", "name": "BuilderAgent", "content": "hi"},
            ]

        async def get_pending_input_request(self, **kwargs):  # noqa: ANN003
            return None

    monkeypatch.setattr(replayer, "_fetch_chat_doc", _fake_fetch)
    monkeypatch.setattr(replayer, "_load_resume_state", _fake_resume_state)
    monkeypatch.setattr(replayer, "_get_persistence_manager", lambda: _FakePM())

    summary = await replayer.handle_resume_request(
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
    replayer = WorkflowRunReplayer()
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
        async def load_run_history(self, **kwargs):  # noqa: ANN003
            return [
                {"role": "assistant", "name": "AppPlanAgent", "content": "Please review the plan."},
            ]

        async def get_pending_input_request(self, **kwargs):  # noqa: ANN003
            captured.update(kwargs)
            return None

    monkeypatch.setattr(replayer, "_fetch_chat_doc", _fake_fetch)
    monkeypatch.setattr(replayer, "_load_resume_state", _fake_resume_state)
    monkeypatch.setattr(replayer, "_get_persistence_manager", lambda: _FakePM())

    await replayer.handle_resume_request(
        chat_id="chat_1",
        app_id="app_1",
        last_client_index=-1,
        send_event=_fake_send,
    )

    assert emitted
    assert captured == {"chat_id": "chat_1", "app_id": "app_1"}


@pytest.mark.asyncio
async def test_handle_resume_request_re_emits_awaiting_reply_for_generic_feedback_pending_input(monkeypatch):
    replayer = WorkflowRunReplayer()
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
        async def load_run_history(self, **kwargs):  # noqa: ANN003
            return [
                {"role": "assistant", "name": "InterviewAgent", "content": "Tell me about the app."},
            ]

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

    monkeypatch.setattr(replayer, "_fetch_chat_doc", _fake_fetch)
    monkeypatch.setattr(replayer, "_load_resume_state", _fake_resume_state)
    monkeypatch.setattr(replayer, "_get_persistence_manager", lambda: _FakePM())

    await replayer.handle_resume_request(
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


@pytest.mark.asyncio
async def test_handle_resume_request_skips_marked_agentdriven_seed_message(monkeypatch):
    replayer = WorkflowRunReplayer()
    emitted = []

    async def _fake_send(event, chat_id):  # noqa: ANN001
        emitted.append((chat_id, event))

    async def _fake_fetch(chat_id, app_id, projection=None):  # noqa: ANN001
        return {
            "status": 0,
            "workflow_name": "ValueEngine",
            "user_id": "user_1",
            "messages": [
                {
                    "role": "user",
                    "agent_name": "user",
                    "content": "Hidden workflow primer",
                    "_mozaiks_seed_kind": "initial_message",
                },
                {"role": "assistant", "agent_name": "InterviewAgent", "content": "Visible question"},
            ],
        }

    async def _fake_resume_state(app_id, user_id):  # noqa: ANN001
        return None

    class _FakePM:
        async def load_run_history(self, **kwargs):  # noqa: ANN003
            return [
                {
                    "role": "user",
                    "agent_name": "user",
                    "content": "Hidden workflow primer",
                    "_mozaiks_seed_kind": "initial_message",
                },
                {"role": "assistant", "agent_name": "InterviewAgent", "content": "Visible question"},
            ]

        async def get_pending_input_request(self, **kwargs):  # noqa: ANN003
            return None

    monkeypatch.setattr(replayer, "_fetch_chat_doc", _fake_fetch)
    monkeypatch.setattr(replayer, "_load_resume_state", _fake_resume_state)
    monkeypatch.setattr(replayer, "_get_persistence_manager", lambda: _FakePM())

    await replayer.handle_resume_request(
        chat_id="chat_1",
        app_id="app_1",
        last_client_index=-1,
        send_event=_fake_send,
        workflow_startup_mode="AgentDriven",
    )

    text_events = [event for _chat_id, event in emitted if event.get("kind") == "text"]
    assert len(text_events) == 1
    assert text_events[0]["content"] == "Visible question"


@pytest.mark.asyncio
async def test_handle_resume_request_skips_ui_hidden_messages(monkeypatch):
    replayer = WorkflowRunReplayer()
    emitted = []

    async def _fake_send(event, chat_id):  # noqa: ANN001
        emitted.append((chat_id, event))

    async def _fake_fetch(chat_id, app_id, projection=None):  # noqa: ANN001
        return {
            "status": 0,
            "workflow_name": "ValueEngine",
            "user_id": "user_1",
        }

    async def _fake_resume_state(app_id, user_id):  # noqa: ANN001
        return None

    class _FakePM:
        async def load_run_history(self, **kwargs):  # noqa: ANN003
            return [
                {
                    "role": "assistant",
                    "name": "GapAnalysisAgent",
                    "content": '{"app_name": "ContractorFlow CRM"}',
                    "metadata": {
                        "ui_visibility": "hidden",
                        "trace_reason": "structured_output_artifact",
                    },
                },
                {
                    "role": "assistant",
                    "name": "ResearchAgent",
                    "content": "Visible research summary.",
                },
            ]

        async def get_pending_input_request(self, **kwargs):  # noqa: ANN003
            return None

    monkeypatch.setattr(replayer, "_fetch_chat_doc", _fake_fetch)
    monkeypatch.setattr(replayer, "_load_resume_state", _fake_resume_state)
    monkeypatch.setattr(replayer, "_get_persistence_manager", lambda: _FakePM())

    await replayer.handle_resume_request(
        chat_id="chat_1",
        app_id="app_1",
        last_client_index=-1,
        send_event=_fake_send,
    )

    text_events = [event for _chat_id, event in emitted if event.get("kind") == "text"]
    assert len(text_events) == 1
    assert text_events[0]["content"] == "Visible research summary."


@pytest.mark.asyncio
async def test_handle_resume_request_skips_unmarked_agentdriven_primer_match(monkeypatch):
    replayer = WorkflowRunReplayer()
    emitted = []

    async def _fake_send(event, chat_id):  # noqa: ANN001
        emitted.append((chat_id, event))

    async def _fake_fetch(chat_id, app_id, projection=None):  # noqa: ANN001
        return {
            "status": 0,
            "workflow_name": "ValueEngine",
            "user_id": "user_1",
            "messages": [
                {
                    "role": "user",
                    "agent_name": "user",
                    "content": "Hidden startup primer",
                },
                {"role": "assistant", "agent_name": "InterviewAgent", "content": "Visible follow-up"},
            ],
        }

    async def _fake_resume_state(app_id, user_id):  # noqa: ANN001
        return None

    class _FakePM:
        async def load_run_history(self, **kwargs):  # noqa: ANN003
            return [
                {
                    "role": "user",
                    "agent_name": "user",
                    "content": "Hidden startup primer",
                },
                {"role": "assistant", "agent_name": "InterviewAgent", "content": "Visible follow-up"},
            ]

        async def get_pending_input_request(self, **kwargs):  # noqa: ANN003
            return None

    monkeypatch.setattr(replayer, "_fetch_chat_doc", _fake_fetch)
    monkeypatch.setattr(replayer, "_load_resume_state", _fake_resume_state)
    monkeypatch.setattr(replayer, "_get_persistence_manager", lambda: _FakePM())
    monkeypatch.setattr(
        replayer,
        "_resolve_hidden_initial_message",
        lambda **_kwargs: "Hidden startup primer",
    )

    await replayer.handle_resume_request(
        chat_id="chat_1",
        app_id="app_1",
        last_client_index=-1,
        send_event=_fake_send,
        workflow_startup_mode="AgentDriven",
    )

    text_events = [event for _chat_id, event in emitted if event.get("kind") == "text"]
    assert len(text_events) == 1
    assert text_events[0]["content"] == "Visible follow-up"


@pytest.mark.asyncio
async def test_handle_resume_request_restores_tool_call_state_from_workflow_ui_state(monkeypatch):
    replayer = WorkflowRunReplayer()
    emitted = []

    async def _fake_send(event, chat_id):  # noqa: ANN001
        emitted.append((chat_id, event))

    async def _fake_fetch(chat_id, app_id, projection=None):  # noqa: ANN001
        return {
            "status": 0,
            "workflow_name": "AgentGenerator",
            "user_id": "user_1",
        }

    async def _fake_resume_state(app_id, user_id):  # noqa: ANN001
        return None

    class _FakePM:
        async def load_run_history(self, **kwargs):  # noqa: ANN003
            return [
                {
                    "role": "assistant",
                    "name": "BuilderAgent",
                    "content": "Here is the draft plan.",
                },
            ]

        async def get_workflow_tool_call_states(self, **kwargs):  # noqa: ANN003
            return [
                {
                    "tool_name": "PlanReview",
                    "tool_call_id": "plan_review_123",
                    "component_type": "PlanReview",
                    "display": "artifact",
                    "workflow_name": "AgentGenerator",
                    "payload": {"plan": "v1"},
                    "tool_call_completed": False,
                    "tool_call_status": "pending",
                    "message_index": 0,
                    "timestamp": "2026-01-01T00:00:00+00:00",
                },
            ]

        async def get_pending_input_request(self, **kwargs):  # noqa: ANN003
            return None

    monkeypatch.setattr(replayer, "_fetch_chat_doc", _fake_fetch)
    monkeypatch.setattr(replayer, "_load_resume_state", _fake_resume_state)
    monkeypatch.setattr(replayer, "_get_persistence_manager", lambda: _FakePM())

    await replayer.handle_resume_request(
        chat_id="chat_1",
        app_id="app_1",
        last_client_index=-1,
        send_event=_fake_send,
    )

    text_events = [event for _chat_id, event in emitted if event.get("kind") == "text"]
    assert len(text_events) == 1
    text_event = text_events[0]
    assert text_event["toolCall"]["tool_name"] == "PlanReview"
    assert text_event["toolCall"]["tool_call_id"] == "plan_review_123"
    assert text_event["toolCall"]["payload"] == {"plan": "v1"}
    assert text_event["tool_call_completed"] is False
    assert text_event["tool_call_status"] == "pending"

    _, boundary = emitted[-1]
    assert boundary["kind"] == "resume_boundary"
    resume_event = boundary["ag2_resume"]["events"][0]
    assert resume_event["metadata"]["tool_call"]["tool_call_id"] == "plan_review_123"
    assert resume_event["metadata"]["tool_call"]["tool_name"] == "PlanReview"

