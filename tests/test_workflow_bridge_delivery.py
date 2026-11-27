"""Same-chat races must deliver each accepted message, or refuse it explicitly.

The bridge and its live continuation run unchanged. An in-memory lease and
barriers make ordering deterministic; the fake channel records the user text
received at the provider boundary independently of transport/WAL receipts.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mozaiksai.core.ports.orchestration import RunStatus
from mozaiksai.core.transport.handlers.input_handlers import handle_user_input_submit
from tests import test_run_complete_dispatch_once as dispatch_tests
from tests.test_run_complete_dispatch_once import live_send_path as live_send_path
from tests.test_workflow_bridge import (
    _ag2_mod,
    _bridge_mod,
    _DummyTransport,
    _FakeAdapter,
    _FakePersistenceManager,
)

CHAT = "delivery-chat"
APP = "delivery-app"
WORKFLOW = "DeliveryProbe"
FIRST_TEXT = "First: build the reading list."
SECOND_TEXT = "Second: make the reading list private."


class _DeliveryPersistence(_FakePersistenceManager):
    async def chat_has_resumable_run(self, *_args):
        return self.status == 0 and bool(self.run_user_messages)

    async def mark_chat_completed(self, chat_id, app_id):
        self.status = 1
        return await super().mark_chat_completed(chat_id, app_id)


class _Lease:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.waiting = asyncio.Event()

    @asynccontextmanager
    async def acquire(self, *, app_id, chat_id):
        assert (app_id, chat_id) == (APP, CHAT)
        if self.lock.locked():
            self.waiting.set()
        async with self.lock:
            yield


class _Channel:
    def __init__(self, provider_inputs, *, status=RunStatus.PAUSED, block=False):
        self.provider_inputs = provider_inputs
        self.status = status
        self.block = block
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.deliveries = []

    async def end_if_closed(self):
        return None

    async def continue_with_user_message(self, message, *, context_updates):
        self.deliveries.append(message)
        self.provider_inputs.append(message)
        self.entered.set()
        if self.block:
            await self.release.wait()
        return SimpleNamespace(
            status=self.status, error=None, close_reason=None,
            context_variables={}, agent_name_by_id={}, wal=[],
            channel_id="one-channel", workflow_name=WORKFLOW,
        )


class _Adapter:
    def __init__(self, transport, persistence, provider_inputs):
        self.transport = transport
        self.persistence = persistence
        self.provider_inputs = provider_inputs
        self.channel = _Channel(provider_inputs)
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.status = RunStatus.PAUSED
        self.starts = []
        self.resumes = []

    async def run(self, request):
        self.starts.append(request)
        # A fresh AG2 run reads the already-persisted initial user message.
        self.provider_inputs.append(self.persistence.run_user_messages[-1]["content"])
        self.entered.set()
        await self.release.wait()
        if self.status is RunStatus.PAUSED:
            self.transport.register_live_ag2_workflow_run(CHAT, self.channel)
        else:
            self.persistence.status = 1 if self.status is RunStatus.COMPLETED else 2
        return SimpleNamespace(status=self.status)

    async def resume(self, request):
        # Recovering a paused channel does not deliver a new user message.
        self.resumes.append(request)
        self.transport.register_live_ag2_workflow_run(CHAT, self.channel)
        return SimpleNamespace(status=RunStatus.PAUSED)


@pytest.fixture
def delivery(monkeypatch):
    persistence = _DeliveryPersistence()
    persistence.pending_input_request = None
    transport = _DummyTransport(persistence)
    provider_inputs = []
    adapter = _Adapter(transport, persistence, provider_inputs)
    lease = _Lease()
    monkeypatch.setattr(_bridge_mod, "chat_execution_lease", lease.acquire)
    monkeypatch.setattr(_bridge_mod, "get_workflow_lifecycle_hooks", lambda _name: {})
    monkeypatch.setattr(_ag2_mod, "get_ag2_adapter", lambda: adapter)
    monkeypatch.setattr(transport, "_apply_user_text_context_updates", AsyncMock(return_value={}))
    return SimpleNamespace(
        transport=transport, persistence=persistence, adapter=adapter,
        provider_inputs=provider_inputs, lease=lease,
    )


async def _send(delivery, text):
    return await delivery.transport.handle_user_input_from_api(
        chat_id=CHAT, app_id=APP, user_id="delivery-user",
        workflow_name=WORKFLOW, message=text,
    )


async def _race(delivery, boundary):
    tasks = [asyncio.create_task(_send(delivery, FIRST_TEXT))]
    try:
        await asyncio.wait_for(boundary.entered.wait(), timeout=10)
        tasks.append(asyncio.create_task(_send(delivery, SECOND_TEXT)))
        # The second request is now behind the first lease, after any stale
        # pre-lease lookup. Only now may the first run settle/register a channel.
        await asyncio.wait_for(delivery.lease.waiting.wait(), timeout=10)
        boundary.release.set()
        return await asyncio.wait_for(asyncio.gather(*tasks), timeout=10)
    finally:
        boundary.release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def _assert_receipts(delivery, expected):
    assert delivery.provider_inputs == expected
    assert [entry["content"] for entry in delivery.persistence.run_user_messages] == expected
    assert [entry["content"] for entry in delivery.transport.persisted_messages] == expected


@pytest.mark.asyncio
async def test_racing_first_messages_reach_the_same_channel_once(delivery):
    first, second = await _race(delivery, delivery.adapter)

    assert first["status"] == second["status"] == "success"
    _assert_receipts(delivery, [FIRST_TEXT, SECOND_TEXT])
    assert len(delivery.adapter.starts) == 1
    assert delivery.adapter.resumes == []
    assert delivery.adapter.channel.deliveries == [SECOND_TEXT]
    assert delivery.transport.get_live_ag2_workflow_run(CHAT) is delivery.adapter.channel
    assert delivery.transport.errors == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [RunStatus.COMPLETED, RunStatus.FAILED])
async def test_racing_first_message_refuses_loser_when_winner_settles(delivery, status):
    delivery.adapter.status = status

    _first, second = await _race(delivery, delivery.adapter)

    assert second["status"] == "error"
    assert second["route"] == "terminal_session"
    assert second["error_code"] == "WORKFLOW_SESSION_TERMINAL"
    _assert_receipts(delivery, [FIRST_TEXT])
    assert len(delivery.adapter.starts) == 1
    assert delivery.adapter.resumes == []
    assert delivery.adapter.channel.deliveries == []


@pytest.mark.asyncio
async def test_waiting_live_message_refuses_after_prior_message_completes(delivery):
    channel = _Channel(delivery.provider_inputs, status=RunStatus.COMPLETED, block=True)
    delivery.transport.register_live_ag2_workflow_run(CHAT, channel)

    first, second = await _race(delivery, channel)

    assert first["status"] == "success"
    assert second["status"] == "error"
    assert second["route"] == "terminal_session"
    assert second["error_code"] == "WORKFLOW_SESSION_TERMINAL"
    _assert_receipts(delivery, [FIRST_TEXT])
    assert channel.deliveries == [FIRST_TEXT]
    assert delivery.adapter.starts == delivery.adapter.resumes == []
    assert delivery.transport.get_live_ag2_workflow_run(CHAT) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.PAUSED])
async def test_recovery_announces_once_before_refusing_undeliverable_input(live_send_path, status):  # noqa: F811 - imported pytest fixture
    """Input refusal must not emit another outcome after recovery announced it."""
    path = live_send_path
    path.persistence.chat_has_resumable_run = AsyncMock(return_value=True)
    path.adapter.status = status
    path.adapter.announce = True
    resume = path.adapter.resume

    async def settle(request):
        result = await resume(request)
        if status is not RunStatus.PAUSED:
            path.persistence.status = 1 if status is RunStatus.COMPLETED else 2
        return result

    path.adapter.resume = settle
    result = await path.transport._run_workflow_background(
        chat_id=dispatch_tests.CHAT_ID, workflow_name=dispatch_tests.WORKFLOW,
        app_id=dispatch_tests.APP_ID, user_id=dispatch_tests.USER_ID,
        ws_id=7, initial_message="Deliver this reply after recovery",
    )
    await asyncio.sleep(0)  # Flush the real send path's scheduled dispatch.

    assert result["status"] == "error"
    assert result["error_code"] == (
        "WORKFLOW_EXECUTION_FAILED" if status is RunStatus.PAUSED else "WORKFLOW_SESSION_TERMINAL"
    )
    outcomes = dispatch_tests._completions(path.emitted)
    assert [outcome["status"] for outcome in outcomes] == [status.value]
    assert len(dispatch_tests._run_complete_envelopes(path.broadcast)) == 1
    assert result["outcome_announced"] is True
    assert path.adapter.runs == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("ready_type", ["chat.awaiting_reply", "chat.run_complete"])
async def test_immediate_socket_reply_is_admitted_after_each_persisted_pause(live_send_path, monkeypatch, ready_type):  # noqa: F811
    """The user can reply from the first ready frame, even before the prior socket task returns."""
    p = live_send_path
    transport, chat_id = p.transport, dispatch_tests.CHAT_ID
    lease = asyncio.Lock()

    @asynccontextmanager
    async def acquire(*, app_id, chat_id):
        assert (app_id, chat_id) == (dispatch_tests.APP_ID, dispatch_tests.CHAT_ID)
        async with lease:
            yield

    monkeypatch.setattr(_bridge_mod, "chat_execution_lease", acquire)
    monkeypatch.setattr(transport, "_apply_user_text_context_updates", AsyncMock(return_value={}))
    monkeypatch.setattr(transport, "process_incoming_user_message", AsyncMock())
    monkeypatch.setattr(_bridge_mod.session_registry, "is_in_general_mode", lambda _: False)
    monkeypatch.setattr(_bridge_mod.session_registry, "get_active_workflow", lambda _: None)
    channel = _Channel([], block=True)
    socket = SimpleNamespace(send_json=AsyncMock())
    replies = [FIRST_TEXT, SECOND_TEXT]
    tasks, at_ready = [], []
    broadcast = transport._broadcast_to_websockets

    async def receive(envelope, chat_id=None):
        await broadcast(envelope, chat_id)
        if envelope["type"] == ready_type and len(tasks) < len(replies):
            at_ready.append((lease.locked(), chat_id in transport._background_tasks))
            await handle_user_input_submit(transport, {
                "text": replies[len(tasks)], "context": {"client_message_id": f"reply-{len(tasks)}"},
            }, chat_id, socket)
            tasks.append(transport._background_tasks[chat_id])
            if len(tasks) == 1:
                await asyncio.wait_for(channel.entered.wait(), 5)

    monkeypatch.setattr(transport, "_broadcast_to_websockets", receive)

    async def start(request):
        transport.register_live_ag2_workflow_run(chat_id, channel)
        await transport.send_event_to_ui({"kind": "awaiting_reply", "workflow": dispatch_tests.WORKFLOW,
                                         "chat_id": chat_id, "reason": "awaiting_user_reply"}, chat_id)
        p.adapter.status = RunStatus.PAUSED
        await p.adapter._announce()
        return SimpleNamespace(status=RunStatus.PAUSED)

    monkeypatch.setattr(p.adapter, "run", start)
    launch = asyncio.create_task(p.run())
    transport._background_tasks[chat_id] = launch
    try:
        await asyncio.wait_for(launch, 10)
        assert tasks and transport._background_tasks.get(chat_id) is tasks[0]
        channel.release.set()
        await asyncio.wait_for(tasks[0], 10)
        assert len(tasks) == 2
        await asyncio.wait_for(tasks[1], 10)
        assert at_ready == [(False, False), (False, False)]
        assert channel.deliveries == replies
        assert [c.args[0]["data"] for c in socket.send_json.await_args_list] == [
            {"chat_id": chat_id, "status": "accepted", "client_message_id": f"reply-{i}"} for i in range(2)]
        assert chat_id not in transport._background_tasks
    finally:
        channel.release.set()
        for task in [launch, *tasks]:
            if not task.done():
                task.cancel()
        await asyncio.gather(launch, *tasks, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("message", [None, "   "])
async def test_explicit_empty_input_still_resumes_callback_when_live_handle_exists(
    monkeypatch, message,
):
    persistence = _FakePersistenceManager()
    transport = _DummyTransport(persistence)
    adapter = _FakeAdapter()
    transport.register_live_ag2_workflow_run(CHAT, object())
    transport._input_request_registries[CHAT] = {"pending-request": object()}
    monkeypatch.setattr(_bridge_mod, "get_workflow_lifecycle_hooks", lambda _name: {})
    monkeypatch.setattr(_ag2_mod, "get_ag2_adapter", lambda: adapter)

    result = await transport.handle_user_input_from_api(
        chat_id=CHAT, app_id=APP, user_id="delivery-user",
        workflow_name=WORKFLOW, message=message,
        initial_agent_name_override="ValueInterviewAgent",
    )

    assert result["status"] == "success"
    assert result["route"] == "existing_session_resume"
    assert transport.submitted_inputs == [{
        "request_id": "pending-request",
        "user_input": f"resume:{CHAT}:pending-request",
    }]
    assert adapter.run_requests == adapter.resume_requests == []
    assert persistence.run_user_messages == transport.persisted_messages == []
