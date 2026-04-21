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

    async def resume_chat(self, chat_id, app_id):
        return list(self._resumed_messages)

    async def create_chat_session(self, **kwargs):
        self.created_sessions.append(kwargs)

    async def persist_initial_messages(self, **kwargs):
        self.persisted_batches.append(kwargs)


class _StubTerminationHandler:
    def __init__(self):
        self.started_for = []

    async def on_conversation_start(self, *, user_id):
        self.started_for.append(user_id)


@pytest.mark.asyncio
async def test_resume_initialize_persists_direct_initial_message_for_new_chat() -> None:
    pm = _StubPersistenceManager()
    termination = _StubTerminationHandler()

    resumed_messages, initial_messages = await _patterns_mod._resume_or_initialize_chat(
        persistence_manager=pm,
        termination_handler=termination,
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
    assert termination.started_for == ["user-1"]
    assert len(pm.persisted_batches) == 1
    assert pm.persisted_batches[0]["messages"] == initial_messages


@pytest.mark.asyncio
async def test_resume_initialize_persists_config_seed_message_for_new_chat() -> None:
    pm = _StubPersistenceManager()
    termination = _StubTerminationHandler()

    resumed_messages, initial_messages = await _patterns_mod._resume_or_initialize_chat(
        persistence_manager=pm,
        termination_handler=termination,
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
    assert len(pm.persisted_batches) == 1
    assert pm.persisted_batches[0]["messages"] == initial_messages


@pytest.mark.asyncio
async def test_resume_initialize_skips_persist_for_userdriven_trigger_seed() -> None:
    pm = _StubPersistenceManager()
    termination = _StubTerminationHandler()

    resumed_messages, initial_messages = await _patterns_mod._resume_or_initialize_chat(
        persistence_manager=pm,
        termination_handler=termination,
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