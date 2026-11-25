"""A UI tool reply reaches only the chat it was authorized for, even when two chats hold one id."""
from __future__ import annotations

import asyncio
import contextlib
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from mozaiksai.core.workflow import ui_tools as workflow_ui_tools
from tests.test_ui_response_ownership import _principal
from tests.test_ui_response_ownership import _Transport as _OwnershipTransport

A, B, FLOOD, EVENT = "chat-a", "chat-b", "chat-flood", "ApprovalCard_shared"
A_REPLY, B_REPLY = {"action": "approve", "from": A}, {"action": "reject", "from": B}


class _Transport(_OwnershipTransport):
    _instance = None

    def __init__(self):
        super().__init__()
        self.sent = []
        type(self)._instance = self
        sessions = {chat: {"_id": chat, "app_id": "app", "user_id": "owner"} for chat in (A, B, FLOOD)}

        async def find_one(query, _projection):
            return sessions.get(query["_id"])

        collection = SimpleNamespace(find_one=find_one)
        self._get_or_create_persistence_manager = Mock(
            return_value=SimpleNamespace(_coll=AsyncMock(return_value=collection)),
        )

    @classmethod
    async def get_instance(cls):
        return cls._instance

    async def send_event_to_ui(self, event, chat_id=None):
        self.sent.append((event, chat_id))

    async def _persist_ui_tool_state(self, **_kwargs):
        return False


async def _emit(transport, chat_id, event_id=EVENT):
    await transport.send_tool_call_event(
        event_id=event_id, chat_id=chat_id, tool_name="ApprovalCard", component_name="ApprovalCard",
        display_type="inline", payload={"workflow_name": "Flow"}, awaiting_response=True,
    )


async def _wait(chat_id, event_id=EVENT):
    """Register a waiter and let it reach its future before the caller goes on."""
    task = asyncio.create_task(
        _Transport.wait_for_tool_call_response(event_id, timeout=None, chat_id=chat_id),
    )
    await asyncio.sleep(0)
    return task


async def _answer(transport, chat_id, response, event_id=EVENT):
    return await transport.submit_tool_call_response_for_user(
        event_id, response, principal=_principal(), chat_id=chat_id,
    )


async def _settled(task):
    return await asyncio.wait_for(task, timeout=2)


async def test_a_chat_receives_its_own_reply():
    transport = _Transport()
    await _emit(transport, A)
    waiter_a = await _wait(A)
    assert await _answer(transport, A, A_REPLY) is True
    assert await _settled(waiter_a) == A_REPLY


async def test_a_late_waiter_receives_its_buffered_reply():
    transport = _Transport()
    await _emit(transport, A)
    assert await _answer(transport, A, A_REPLY) is True
    assert await _settled(await _wait(A)) == A_REPLY


async def test_an_id_bound_by_another_chat_while_a_reply_is_stored_stays_with_its_chat():
    transport = _Transport()
    entered, release = asyncio.Event(), asyncio.Event()

    async def store(**_kwargs):
        entered.set()
        await release.wait()

    transport._finalize_tool_call_response.side_effect = store
    await _emit(transport, A)
    reply_a = asyncio.create_task(_answer(transport, A, A_REPLY))
    await asyncio.wait_for(entered.wait(), timeout=2)
    # Chat B binds the same id and starts waiting while A's reply is being stored.
    await _emit(transport, B)
    waiter_b = await _wait(B)
    release.set()

    assert await _settled(reply_a) is True
    await asyncio.sleep(0)
    assert not waiter_b.done()
    assert await _settled(await _wait(A)) == A_REPLY
    assert await _answer(transport, B, B_REPLY) is True
    assert await _settled(waiter_b) == B_REPLY


async def test_two_chats_waiting_on_one_id_do_not_share_a_future():
    transport = _Transport()
    await _emit(transport, A)
    waiter_a = await _wait(A)
    await _emit(transport, B)
    waiter_b = await _wait(B)

    assert await _answer(transport, B, B_REPLY) is True
    assert await _settled(waiter_b) == B_REPLY
    assert not waiter_a.done()
    assert await _answer(transport, A, A_REPLY) is True
    assert await _settled(waiter_a) == A_REPLY


async def test_a_buffered_reply_is_not_consumed_by_a_later_chat_with_the_same_id():
    transport = _Transport()
    await _emit(transport, A)
    # A's reply arrives before A's waiter does, so it is buffered.
    assert await _answer(transport, A, A_REPLY) is True
    await _emit(transport, B)
    waiter_b = await _wait(B)

    assert not waiter_b.done()
    assert await _settled(await _wait(A)) == A_REPLY
    assert await _answer(transport, B, B_REPLY) is True
    assert await _settled(waiter_b) == B_REPLY


@pytest.mark.parametrize("storing", [False, True])
async def test_eviction_keeps_the_owner_and_the_reply_a_late_waiter_needs(storing):
    transport = _Transport()
    entered, release = asyncio.Event(), asyncio.Event()

    async def store(**kwargs):
        if kwargs["event_id"] == EVENT:
            entered.set()
            await release.wait()

    if storing:
        transport._finalize_tool_call_response.side_effect = store
    await _emit(transport, A)
    reply_a = asyncio.create_task(_answer(transport, A, A_REPLY))
    if storing:
        await asyncio.wait_for(entered.wait(), timeout=2)
    else:
        assert await _settled(reply_a) is True
    # Fill the 1,024-entry resolved registry past A's entry with answered interactions.
    for index in range(1025):
        flood_id = f"flood-{index}"
        await _emit(transport, FLOOD, flood_id)
        waiter = await _wait(FLOOD, flood_id)
        assert await transport.submit_tool_call_response(flood_id, {"n": index}) is True
        assert await _settled(waiter) == {"n": index}
    assert len(transport._resolved_tool_call_ids) == 1024
    assert transport._tool_call_chat_id(EVENT) == A
    release.set()
    assert await _settled(reply_a) is True

    assert transport._tool_call_chat_id(EVENT) == A
    # A retry from A's owner is still the idempotent duplicate, not a new decision.
    assert await _answer(transport, A, {"action": "retry"}) is True
    await _emit(transport, B)
    waiter_b = await _wait(B)
    assert not waiter_b.done()
    assert await _settled(await _wait(A)) == A_REPLY
    assert await _answer(transport, B, B_REPLY) is True
    assert await _settled(waiter_b) == B_REPLY


async def test_an_interaction_without_a_chat_is_never_released_by_a_chat_reply():
    transport = _Transport()
    with contextlib.suppress(ValueError):
        await _emit(transport, None)
    orphan = await _wait(None)
    transport._input_request_registries[B] = {EVENT: Mock()}

    assert await _answer(transport, B, B_REPLY) is True

    # B's reply answers B's own request; nothing without a chat is released by it.
    transport.submit_user_input.assert_awaited_once()
    assert transport.submit_user_input.await_args.kwargs["chat_id"] == B
    transport._finalize_tool_call_response.assert_not_awaited()
    await asyncio.sleep(0)
    assert not orphan.done() or orphan.exception() is not None
    orphan.cancel()


async def test_a_waiting_interaction_requires_a_chat():
    transport = _Transport()
    with pytest.raises(ValueError):
        await _emit(transport, None)
    with pytest.raises(ValueError):
        await _Transport.wait_for_tool_call_response(EVENT, timeout=None, chat_id=None)
    assert transport._ui_tool_metadata == {} and transport.pending_tool_call_responses == {}
    assert transport.sent == []


async def test_an_ambiguous_id_without_a_named_chat_is_refused():
    transport = _Transport()
    await _emit(transport, A)
    await _emit(transport, B)
    assert await transport.submit_tool_call_response(EVENT, A_REPLY) is False
    assert await transport.submit_tool_call_response_for_user(EVENT, A_REPLY, principal=_principal()) is False
    assert transport._buffered_tool_call_responses == {} and transport._resolved_tool_call_ids == {}
    transport._finalize_tool_call_response.assert_not_awaited()


async def test_server_generated_ids_carry_the_full_uuid(monkeypatch):
    from mozaiksai.core.transport.simple_transport import SimpleTransport

    transport = SimpleNamespace(send_tool_call_event=AsyncMock(return_value=None))
    monkeypatch.setattr(SimpleTransport, "get_instance", AsyncMock(return_value=transport))
    event_id = await workflow_ui_tools._emit_tool_call_core(
        tool_id="ApprovalCard", payload={}, chat_id=A, workflow_name="Flow",
    )
    assert re.fullmatch(r"ApprovalCard_[0-9a-f]{32}", event_id)
    assert transport.send_tool_call_event.await_args.kwargs["event_id"] == event_id
