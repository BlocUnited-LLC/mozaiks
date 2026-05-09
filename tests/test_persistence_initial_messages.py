from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.import_utils import import_module_directly


_persistence_mod = import_module_directly("mozaiksai.core.data.persistence.persistence_manager")

AG2PersistenceManager = _persistence_mod.AG2PersistenceManager


class _FakeChatCollection:
    def __init__(self) -> None:
        self.last_sequence = 0
        self.messages = []
        self.workflow_name = None

    async def find_one(self, *_args, **_kwargs):
        return {"messages": list(self.messages[-5:])}

    async def find_one_and_update(self, *_args, **_kwargs):
        self.last_sequence += 1
        return {
            "last_sequence": self.last_sequence,
            "workflow_name": self.workflow_name,
        }

    async def update_one(self, *_args, **kwargs):
        msg_doc = kwargs.get("update", {}).get("$push", {}).get("messages")
        if msg_doc is None and len(_args) > 1:
            msg_doc = _args[1].get("$push", {}).get("messages")
        if msg_doc is not None:
            self.messages.append(msg_doc)


@pytest.mark.asyncio
async def test_persist_initial_messages_skips_hidden_seed_messages(monkeypatch):
    manager = AG2PersistenceManager()
    coll = _FakeChatCollection()

    async def _fake_coll():
        return coll

    monkeypatch.setattr(manager, "_coll", _fake_coll)

    await manager.persist_initial_messages(
        chat_id="chat-1",
        app_id="app-1",
        messages=[
            {
                "role": "user",
                "name": "user",
                "content": "Hidden workflow primer",
                "_mozaiks_seed_kind": "initial_message",
                "metadata": {"source": "test.seed"},
            }
        ],
    )

    assert coll.messages == []


@pytest.mark.asyncio
async def test_persist_initial_messages_keeps_visible_bootstrap_prompt(monkeypatch):
    manager = AG2PersistenceManager()
    coll = _FakeChatCollection()

    async def _fake_coll():
        return coll

    monkeypatch.setattr(manager, "_coll", _fake_coll)

    await manager.persist_initial_messages(
        chat_id="chat-1",
        app_id="app-1",
        messages=[
            {
                "role": "assistant",
                "name": "ValueInterviewAgent",
                "content": "Tell me about your idea.",
                "metadata": {"source": "orchestrator.initial_message_to_user"},
            }
        ],
    )

    assert len(coll.messages) == 1
    stored = coll.messages[0]
    assert stored["agent_name"] == "ValueInterviewAgent"
    assert stored["metadata"]["source"] == "orchestrator.initial_message_to_user"


class _FakeTextEvent:
    def __init__(self, *, content: str, sender_name: str) -> None:
        self.content = content
        self.sender = SimpleNamespace(name=sender_name)
        self.id = "evt-1"
        self.timestamp = 0


@pytest.mark.asyncio
async def test_save_event_skips_hidden_agentdriven_initial_message(monkeypatch):
    manager = AG2PersistenceManager()
    coll = _FakeChatCollection()
    coll.workflow_name = "ValueEngine"

    async def _fake_coll():
        return coll

    monkeypatch.setattr(manager, "_coll", _fake_coll)
    monkeypatch.setattr(_persistence_mod, "TextEvent", _FakeTextEvent)
    monkeypatch.setattr(
        _persistence_mod,
        "matches_hidden_initial_message",
        lambda **kwargs: kwargs.get("workflow_name") == "ValueEngine"
        and kwargs.get("role") == "user"
        and kwargs.get("content") == "Hidden workflow primer"
        and kwargs.get("agent_name") == "user",
    )

    await manager.save_event(
        _FakeTextEvent(content="Hidden workflow primer", sender_name="user"),
        chat_id="chat-1",
        app_id="app-1",
    )

    assert coll.messages == []
