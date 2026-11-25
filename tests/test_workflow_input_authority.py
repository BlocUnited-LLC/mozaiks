"""Input and replies are refused when their sender may no longer run that workflow."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from mozaiksai.core.auth import UserPrincipal
from mozaiksai.core.ports.orchestration import RunStatus
from mozaiksai.core.runtime.composition import platform_hooks
from mozaiksai.core.transport.simple_transport import SimpleTransport
from tests.test_ui_response_ownership import _Transport as _UIToolsTransport
from tests.test_workflow_bridge import (
    _ag2_mod,
    _bridge_mod,
    _DummyTransport,
    _FakeAdapter,
    _FakeLiveRun,
    _FakePersistenceManager,
    _LiveRunResult,
)

CHAT, APP, USER = "chat-1", "app-1", "user-1"
DENIED = "WORKFLOW_ACCESS_DENIED"


class _Persistence(_FakePersistenceManager):
    def __init__(self, document):
        super().__init__()
        self.document = document

    async def _coll(self):
        async def find_one(query, _projection):
            assert query["_id"] == CHAT
            return self.document

        return SimpleNamespace(find_one=find_one)


def _session(workflow_name="OperatorOnly", **fields):
    return {"_id": CHAT, "user_id": USER, "app_id": APP, "workflow_name": workflow_name, **fields}


@pytest.fixture
def hooked(monkeypatch):
    def install(hook):
        registry = platform_hooks.PlatformHookRegistry()
        if hook is not None:
            registry._chat_session_fields_hooks.append(hook)
        monkeypatch.setattr(platform_hooks, "get_platform_hooks", lambda: registry)

    return install


@pytest.fixture
def bridge(monkeypatch):
    def build(document):
        transport = _DummyTransport(_Persistence(document))

        async def no_updates(**_kwargs):
            return {}

        monkeypatch.setattr(transport, "_apply_user_text_context_updates", no_updates)
        monkeypatch.setattr(_bridge_mod, "get_workflow_lifecycle_hooks", lambda _workflow_name: {})
        adapter = _FakeAdapter()
        monkeypatch.setattr(_ag2_mod, "get_ag2_adapter", lambda: adapter)
        return transport, adapter

    return build


async def _send(transport, *, message="continue", **overrides):
    return await transport.handle_user_input_from_api(**{
        "chat_id": CHAT, "user_id": USER, "workflow_name": "NamedByTheRequest", "message": message, "app_id": APP,
        **overrides,
    })


def _refuse(calls):
    def hook(**kwargs):
        calls.append(kwargs)
        raise PermissionError("not any more")

    return hook


def _untouched(transport, adapter):
    persistence = transport._persistence_manager
    assert persistence.run_user_messages == transport.persisted_messages == persistence.persisted_context == []
    assert persistence.pending_clears == persistence.failed == persistence.completed == []
    assert persistence.status == 0
    assert adapter.run_requests == adapter.resume_requests == []
    assert transport.submitted_inputs == transport.sent_ui_events == []
    assert [error["error_code"] for error in transport.errors] == [DENIED]


@pytest.mark.asyncio
async def test_refused_text_changes_nothing_in_a_live_run(hooked, bridge):
    calls = []
    hooked(_refuse(calls))
    transport, adapter = bridge(_session())
    live_run = _FakeLiveRun(result=_LiveRunResult(status=RunStatus.PAUSED))
    transport.register_live_ag2_workflow_run(CHAT, live_run)

    result = await _send(transport)

    assert (result["error_code"], result["input_accepted"], result["route"]) == (DENIED, False, "access_denied")
    assert live_run.continued == []
    _untouched(transport, adapter)
    # The stored workflow decides, not the one the request names.
    assert [(call["workflow_name"], call["user_id"], call["phase"]) for call in calls] == [
        ("OperatorOnly", USER, "resume")]


@pytest.mark.asyncio
@pytest.mark.parametrize("resumable", [True, False])
async def test_refused_text_does_not_recover_or_start_a_run(hooked, bridge, resumable):
    hooked(_refuse([]))
    transport, adapter = bridge(_session())
    transport._persistence_manager.resumable_run = resumable
    assert (await _send(transport))["error_code"] == DENIED
    _untouched(transport, adapter)


@pytest.mark.asyncio
async def test_refused_resume_request_changes_nothing(hooked, bridge):
    hooked(_refuse([]))
    transport, adapter = bridge(_session())
    result = await _send(transport, message=None, initial_agent_name_override="Planner")
    assert result["error_code"] == DENIED
    _untouched(transport, adapter)


@pytest.mark.asyncio
async def test_refused_text_does_not_answer_a_waiting_request(hooked, bridge):
    hooked(_refuse([]))
    transport, adapter = bridge(_session())
    transport._input_request_registries = {CHAT: {"req-1": SimpleNamespace()}}
    assert (await _send(transport))["error_code"] == DENIED
    _untouched(transport, adapter)


@pytest.mark.asyncio
async def test_a_background_refusal_is_not_a_run_outcome(hooked, bridge):
    hooked(_refuse([]))
    transport, adapter = bridge(_session())
    transport._workflow_spawn_semaphore = asyncio.Semaphore(1)
    result = await transport._run_workflow_background(
        chat_id=CHAT, workflow_name="OperatorOnly", app_id=APP, user_id=USER, ws_id=None, initial_message="go",
    )
    assert result["error_code"] == DENIED
    _untouched(transport, adapter)


@pytest.mark.asyncio
async def test_a_live_run_whose_chat_cannot_be_read_is_refused(hooked, bridge):
    hooked(lambda **_kwargs: {})
    transport, _adapter = bridge(None)
    live_run = _FakeLiveRun(result=_LiveRunResult(status=RunStatus.PAUSED))
    transport.register_live_ag2_workflow_run(CHAT, live_run)
    assert (await _send(transport))["error_code"] == DENIED
    assert live_run.continued == []


@pytest.mark.asyncio
async def test_a_first_start_without_a_stored_chat_is_left_to_orchestration(hooked, bridge):
    calls = []
    hooked(_refuse(calls))
    transport, adapter = bridge(None)
    assert (await _send(transport))["status"] == "success"
    assert len(adapter.run_requests) == 1 and calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("bound", [False, True])
@pytest.mark.parametrize("hook", [None, "empty", "same_fields"])
async def test_admitted_text_continues(hooked, bridge, hook, bound):
    fields = {"run_build_binding": {"target_app_id": "build-1"}} if bound else {}
    hooked({"empty": lambda **_kwargs: {}, "same_fields": lambda **kwargs: dict(kwargs["session_fields"])}.get(hook))
    transport, _adapter = bridge(_session("ValueEngine", **fields))
    live_run = _FakeLiveRun(result=_LiveRunResult(status=RunStatus.PAUSED))
    transport.register_live_ag2_workflow_run(CHAT, live_run)
    assert (await _send(transport, workflow_name="ValueEngine"))["status"] == "success"
    assert len(live_run.continued) == 1
    assert transport.errors == []


@pytest.mark.asyncio
async def test_an_anonymous_chat_asks_as_anonymous(hooked, bridge):
    calls = []
    hooked(_refuse(calls))
    transport, _adapter = bridge(_session(user_id="anonymous"))
    assert (await _send(transport, user_id=None))["error_code"] == DENIED
    assert [call["user_id"] for call in calls] == ["anonymous"]


def _reply_transport(document):
    transport = _UIToolsTransport()
    persistence = _Persistence(document)
    transport._get_or_create_persistence_manager = lambda: persistence
    transport._ui_tool_metadata["evt-1"] = {"chat_id": CHAT, "tool_name": "Card", "display": "artifact"}
    return transport


def _principal(user_id=USER, provenance="token_validated"):
    return UserPrincipal(
        user_id=user_id, email=None, name=None, roles=[], scopes=[], raw_claims={}, provider="none",
        auth_provenance=provenance,
    )


@pytest.mark.asyncio
async def test_a_refused_reply_is_not_delivered(hooked, monkeypatch):
    calls = []
    hooked(_refuse(calls))
    transport = _reply_transport(_session())
    delivered = []

    async def deliver(*args):
        delivered.append(args)
        return True

    monkeypatch.setattr(transport, "submit_tool_call_response", deliver)
    assert await transport.submit_tool_call_response_for_user("evt-1", "continue", principal=_principal()) is False
    assert delivered == []
    assert [(call["workflow_name"], call["user_id"]) for call in calls] == [("OperatorOnly", USER)]

    hooked(lambda **_kwargs: {})
    assert await transport.submit_tool_call_response_for_user("evt-1", "continue", principal=_principal()) is True
    assert len(delivered) == 1


@pytest.mark.asyncio
async def test_refused_feedback_is_neither_stored_nor_delivered(hooked, monkeypatch):
    hooked(_refuse([]))
    transport = _reply_transport(_session())
    transport._ui_tool_metadata["evt-1"].update(
        workflow_primitive="outcome_feedback", workflow_name="OperatorOnly", feedback_agent_name="Reviewer",
        outcome_id="outcome-1",
    )
    stored, delivered = [], []

    async def store(receipt):
        stored.append(receipt)
        return True

    async def deliver(*args):
        delivered.append(args)
        return True

    transport._get_or_create_persistence_manager().save_workflow_feedback_receipt = store
    monkeypatch.setattr(transport, "submit_tool_call_response", deliver)
    reply = {"status": "submitted", "rating": 5}
    assert await transport.submit_tool_call_response_for_user("evt-1", reply, principal=_principal()) is False
    assert stored == delivered == []

    hooked(lambda **_kwargs: {})
    assert await transport.submit_tool_call_response_for_user("evt-1", reply, principal=_principal()) is True
    assert len(stored) == len(delivered) == 1


@pytest.mark.asyncio
async def test_an_event_id_belongs_to_one_chat():
    transport = object.__new__(SimpleTransport)
    transport._ui_tool_metadata = {"evt-1": {"chat_id": CHAT, "tool_name": "Card"}}
    emit = {"tool_name": "Card", "component_name": "Card", "display_type": "inline", "payload": {}}
    with pytest.raises(ValueError, match="another chat"):
        await transport.send_tool_call_event("evt-1", "chat-b", **emit)
    # Answered or not, the id stays with its chat.
    transport._mark_tool_call_response_resolved("evt-1")
    with pytest.raises(ValueError, match="another chat"):
        await transport.send_tool_call_event("evt-1", "chat-b", **emit)
    assert transport._ui_tool_metadata["evt-1"]["chat_id"] == CHAT


@pytest.mark.asyncio
async def test_two_chats_cannot_bind_one_event_id_while_the_first_is_stored(monkeypatch):
    transport = object.__new__(SimpleTransport)
    transport._ui_tool_metadata, transport.connections = {}, {}

    async def persist(**_kwargs):
        transport._ui_tool_metadata["evt-1"] = {"chat_id": CHAT, "tool_name": "Card"}
        return True

    monkeypatch.setattr(transport, "_persist_ui_tool_state", persist)
    with pytest.raises(ValueError, match="another chat"):
        await transport.send_tool_call_event(
            "evt-1", "chat-b", tool_name="Card", component_name="Card", display_type="inline", payload={},
        )
    assert transport._ui_tool_metadata["evt-1"]["chat_id"] == CHAT


def test_an_input_request_id_belongs_to_one_chat():
    transport = object.__new__(SimpleTransport)
    transport._input_request_registries = {}
    assert transport.register_input_request(CHAT, "req-1", print) == "req-1"
    assert transport.register_input_request(CHAT, "req-1", print) == "req-1"
    other = transport.register_input_request("chat-b", "req-1", print)
    assert other != "req-1" and set(transport._input_request_registries["chat-b"]) == {other}
    assert set(transport._input_request_registries[CHAT]) == {"req-1"}


def test_an_unread_reply_is_dropped_with_its_event():
    transport = object.__new__(SimpleTransport)
    transport._ui_tool_metadata = {"evt-old": {"chat_id": CHAT}}
    transport._buffered_tool_call_responses = {"evt-old": {"action": "yes"}}
    transport._mark_tool_call_response_resolved("evt-old")
    for index in range(1024):
        transport._mark_tool_call_response_resolved(f"evt-{index}")
    assert "evt-old" not in transport._ui_tool_metadata
    assert transport._buffered_tool_call_responses == {}


@pytest.mark.asyncio
async def test_input_is_delivered_only_to_the_chat_that_was_checked():
    transport = object.__new__(SimpleTransport)
    answered = []
    other = {"req-1": lambda text: answered.append(("other", text))}
    transport._input_request_registries = {"other": other}
    transport._recent_input_submit_chats = set()
    assert await transport.submit_user_input("req-1", "", chat_id=CHAT) is False
    assert answered == [] and "req-1" in other


@pytest.mark.asyncio
async def test_the_shared_development_identity_replies_as_the_chat_owner(hooked, monkeypatch):
    calls = []

    def admit(**kwargs):
        calls.append(kwargs)
        return {}

    hooked(admit)
    transport = _reply_transport(_session())

    async def deliver(*_args):
        return True

    monkeypatch.setattr(transport, "submit_tool_call_response", deliver)
    developer = _principal("anonymous", "local_development")
    assert await transport.submit_tool_call_response_for_user("evt-1", "continue", principal=developer) is True
    assert [call["user_id"] for call in calls] == [USER]
