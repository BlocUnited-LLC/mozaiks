from __future__ import annotations

import logging

import pytest

from tests.import_utils import import_module_directly

_patterns_mod = import_module_directly("mozaiksai.core.workflow.orchestration_patterns")


class _StubPersistenceManager:
    def __init__(self, resumed_messages=None):
        self._resumed_messages = list(resumed_messages or [])
        self.created_sessions = []
        self.persisted_batches = []
        self.persisted_run_messages = []

    async def load_run_history(self, *, chat_id, app_id):
        return list(self._resumed_messages)

    async def create_chat_session(self, **kwargs):
        self.created_sessions.append(kwargs)

    async def persist_initial_messages(self, **kwargs):
        self.persisted_batches.append(kwargs)

    async def append_run_assistant_message(self, **kwargs):
        self.persisted_run_messages.append(kwargs)


@pytest.mark.asyncio
async def test_resume_initialize_keeps_direct_initial_message_out_of_persisted_transcript() -> None:
    pm = _StubPersistenceManager()

    resumed_messages, initial_messages = await _patterns_mod._resume_or_initialize_chat(
        persistence_manager=pm,
        config={},
        chat_id="chat-new",
        app_id="app-1",
        workflow_name="RuntimeSmoke",
        user_id="user-1",
        initial_message="Hello runtime",
        initial_agent_name=None,
        wf_logger=logging.getLogger("test.orchestration.seed"),
    )

    assert resumed_messages == []
    assert initial_messages == [
        {
            "role": "user",
            "name": "user",
            "content": "Hello runtime",
            "_mozaiks_seed_kind": "initial_message",
        }
    ]
    assert pm.created_sessions == [
        {
            "chat_id": "chat-new",
            "app_id": "app-1",
            "workflow_name": "RuntimeSmoke",
            "user_id": "user-1",
        }
    ]
    assert pm.persisted_batches == []


@pytest.mark.asyncio
async def test_resume_initialize_keeps_config_seed_out_of_persisted_transcript() -> None:
    pm = _StubPersistenceManager()

    resumed_messages, initial_messages = await _patterns_mod._resume_or_initialize_chat(
        persistence_manager=pm,
        config={"initial_message": "Seed from config"},
        chat_id="chat-seed",
        app_id="app-1",
        workflow_name="RuntimeSmoke",
        user_id="user-1",
        initial_message=None,
        initial_agent_name=None,
        wf_logger=logging.getLogger("test.orchestration.seed"),
    )

    assert resumed_messages == []
    assert initial_messages == [
        {
            "role": "user",
            "name": "user",
            "content": "Seed from config",
            "_mozaiks_seed_kind": "initial_message",
        }
    ]
    assert pm.persisted_batches == []


@pytest.mark.asyncio
async def test_resume_initialize_reinjects_hidden_config_seed_on_resume_without_persisting() -> None:
    pm = _StubPersistenceManager(
        resumed_messages=[
            {
                "role": "assistant",
                "name": "ValueInterviewAgent",
                "content": "Which niche do you want to explore first?",
            }
        ]
    )

    resumed_messages, initial_messages = await _patterns_mod._resume_or_initialize_chat(
        persistence_manager=pm,
        config={"initial_message": "Seed from config"},
        chat_id="chat-resume-seed",
        app_id="app-1",
        workflow_name="RuntimeSmoke",
        user_id="user-1",
        initial_message=None,
        initial_agent_name=None,
        wf_logger=logging.getLogger("test.orchestration.seed"),
    )

    assert resumed_messages == [
        {
            "role": "assistant",
            "name": "ValueInterviewAgent",
            "content": "Which niche do you want to explore first?",
        }
    ]
    assert initial_messages == [
        {
            "role": "user",
            "name": "user",
            "content": "Seed from config",
            "_mozaiks_seed_kind": "initial_message",
        },
        {
            "role": "assistant",
            "name": "ValueInterviewAgent",
            "content": "Which niche do you want to explore first?",
        },
    ]
    assert pm.persisted_batches == []


@pytest.mark.asyncio
async def test_resume_initialize_skips_persist_for_userdriven_trigger_seed() -> None:
    pm = _StubPersistenceManager()

    resumed_messages, initial_messages = await _patterns_mod._resume_or_initialize_chat(
        persistence_manager=pm,
        config={"workflow_startup_mode": "UserDriven"},
        chat_id="chat-userdriven",
        app_id="app-1",
        workflow_name="RuntimeSmoke",
        user_id="user-1",
        initial_message=None,
        initial_agent_name=None,
        wf_logger=logging.getLogger("test.orchestration.seed"),
    )

    assert resumed_messages == []
    assert initial_messages == [
        {
            "role": "user",
            "name": "user",
            "content": ".",
            "_mozaiks_seed_kind": "userdriven_trigger",
        }
    ]
    assert pm.persisted_batches == []


@pytest.mark.asyncio
async def test_userdriven_greeting_persists_as_run_stream_event() -> None:
    pm = _StubPersistenceManager()

    class _Transport:
        def __init__(self):
            self.events = []

        async def send_event_to_ui(self, event, chat_id):
            self.events.append((chat_id, dict(event)))

    transport = _Transport()

    sequence = await _patterns_mod._emit_startup_greeting_if_needed(
        config={"initial_message_to_user": "How can I help?"},
        resumed_messages=[],
        workflow_startup_mode="UserDriven",
        initial_agent_name="InterviewAgent",
        transport=transport,
        chat_id="chat-userdriven",
        app_id="app-1",
        persistence_manager=pm,
        workflow_name_upper="RUNTIMESMOKE",
        wf_logger=logging.getLogger("test.orchestration.greeting"),
    )

    assert sequence == 1
    assert pm.persisted_run_messages == [
        {
            "chat_id": "chat-userdriven",
            "app_id": "app-1",
            "content": "How can I help?",
            "agent_name": "InterviewAgent",
            "metadata": {"source": "startup_greeting"},
        }
    ]
    assert transport.events[0][1]["kind"] == "chat.text"
