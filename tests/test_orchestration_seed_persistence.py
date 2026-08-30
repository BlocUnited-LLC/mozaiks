from __future__ import annotations

import logging

import pytest
from ag2.events.input_events import TextInput

from mozaiksai.core.workflow.execution.run_bootstrap import (
    bootstrap_run_messages as _bootstrap_run_messages,
)


class _StubPersistenceManager:
    def __init__(self, run_events=None, event_messages=None):
        self._run_events = list(run_events or [])
        self._event_messages = list(event_messages or [])
        self.created_sessions = []
        self.persisted_batches = []
        self.persisted_run_messages = []

    async def load_run_events(self, *, chat_id, app_id):
        return list(self._run_events)

    def project_run_events_to_messages(self, events):
        assert list(events) == self._run_events
        return list(self._event_messages)

    async def create_chat_session(self, **kwargs):
        self.created_sessions.append(kwargs)

    async def persist_initial_messages(self, **kwargs):
        self.persisted_batches.append(kwargs)

    async def append_run_assistant_message(self, **kwargs):
        self.persisted_run_messages.append(kwargs)


class _FailingSessionPersistenceManager(_StubPersistenceManager):
    async def create_chat_session(self, **kwargs):
        self.created_sessions.append(kwargs)
        raise RuntimeError("required session persistence unavailable")


@pytest.mark.asyncio
async def test_run_bootstrap_keeps_direct_initial_message_out_of_persisted_transcript() -> None:
    pm = _StubPersistenceManager()

    has_persisted_events, initial_messages = await _bootstrap_run_messages(
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

    assert has_persisted_events is False
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
async def test_run_bootstrap_keeps_config_seed_out_of_persisted_transcript() -> None:
    pm = _StubPersistenceManager()

    has_persisted_events, initial_messages = await _bootstrap_run_messages(
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

    assert has_persisted_events is False
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
async def test_run_bootstrap_propagates_required_session_creation_failure() -> None:
    pm = _FailingSessionPersistenceManager()

    with pytest.raises(RuntimeError, match="required session persistence unavailable"):
        await _bootstrap_run_messages(
            persistence_manager=pm,
            config={},
            chat_id="chat-create-fails",
            app_id="app-1",
            workflow_name="RuntimeSmoke",
            user_id="user-1",
            initial_message="Hello runtime",
            initial_agent_name=None,
            wf_logger=logging.getLogger("test.orchestration.seed"),
        )

    assert pm.created_sessions == [
        {
            "chat_id": "chat-create-fails",
            "app_id": "app-1",
            "workflow_name": "RuntimeSmoke",
            "user_id": "user-1",
        }
    ]


@pytest.mark.asyncio
async def test_run_bootstrap_uses_latest_user_event_as_trigger_without_reinjecting_config_seed() -> None:
    pm = _StubPersistenceManager(
        run_events=[TextInput("Polymarket for AI startups")],
        event_messages=[
            {
                "role": "assistant",
                "name": "ValueInterviewAgent",
                "content": "Which niche do you want to explore first?",
            },
            {"role": "user", "name": "user", "content": "Polymarket for AI startups"},
        ]
    )

    has_persisted_events, initial_messages = await _bootstrap_run_messages(
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

    assert has_persisted_events is True
    assert initial_messages == [
        {"role": "user", "name": "user", "content": "Polymarket for AI startups", "_mozaiks_seed_kind": "ag2_event_trigger"},
    ]
    assert pm.persisted_batches == []


@pytest.mark.asyncio
async def test_run_bootstrap_skips_persist_for_userdriven_trigger_seed() -> None:
    pm = _StubPersistenceManager()

    has_persisted_events, initial_messages = await _bootstrap_run_messages(
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

    assert has_persisted_events is False
    assert initial_messages == [
        {
            "role": "user",
            "name": "user",
            "content": ".",
            "_mozaiks_seed_kind": "userdriven_trigger",
        }
    ]
    assert pm.persisted_batches == []

