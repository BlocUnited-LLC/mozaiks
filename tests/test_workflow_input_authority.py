"""Input to a live run is refused when its sender may no longer run that workflow."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from mozaiksai.core.ports.orchestration import RunStatus
from mozaiksai.core.runtime.composition import platform_hooks
from tests.test_workflow_bridge import (
    _bridge_mod,
    _DummyTransport,
    _FakeLiveRun,
    _FakePersistenceManager,
    _LiveRunResult,
)

CHAT, APP, USER = "chat-1", "app-1", "user-1"


class _Collection:
    def __init__(self, document):
        self.document = document
        self.queries = []

    async def find_one(self, query, _projection):
        self.queries.append(query)
        return self.document


class _Persistence(_FakePersistenceManager):
    def __init__(self, document):
        super().__init__()
        self.collection = _Collection(document)

    async def _coll(self):
        return self.collection


@pytest.fixture
def hooked(monkeypatch):
    def install(hook):
        hooks = platform_hooks.PlatformHookRegistry()
        if hook is not None:
            hooks._chat_session_fields_hooks.append(hook)
        monkeypatch.setattr(platform_hooks, "get_platform_hooks", lambda: hooks)

    return install


async def _send(transport, live_run, *, named="NamedByTheRequest"):
    return await transport._continue_live_ag2_workflow_run(
        live_run=live_run, chat_id=CHAT, user_id=USER, workflow_name=named, message="continue", app_id=APP,
    )


def _refuse(calls):
    def hook(**kwargs):
        calls.append(kwargs)
        raise PermissionError("not any more")

    return hook


@pytest.mark.asyncio
async def test_refused_input_reaches_neither_the_run_nor_history(hooked):
    calls = []
    hooked(_refuse(calls))
    persistence = _Persistence({"_id": CHAT, "workflow_name": "OperatorOnly"})
    transport = _DummyTransport(persistence)
    live_run = _FakeLiveRun(result=_LiveRunResult(status=RunStatus.PAUSED))

    result = await _send(transport, live_run)

    assert (result["error_code"], result["input_accepted"]) == ("WORKFLOW_ACCESS_DENIED", False)
    assert live_run.continued == []
    assert persistence.run_user_messages == transport.persisted_messages == persistence.persisted_context == []
    assert [error["error_code"] for error in transport.errors] == ["WORKFLOW_ACCESS_DENIED"]
    # The stored workflow decides, not the one the request names.
    assert [(call["workflow_name"], call["user_id"], call["phase"]) for call in calls] == [
        ("OperatorOnly", USER, "resume")]


@pytest.mark.asyncio
async def test_a_chat_that_cannot_be_read_is_refused(hooked):
    hooked(lambda **_kwargs: {})
    transport = _DummyTransport(_Persistence(None))
    live_run = _FakeLiveRun(result=_LiveRunResult(status=RunStatus.PAUSED))
    assert (await _send(transport, live_run))["error_code"] == "WORKFLOW_ACCESS_DENIED"
    assert live_run.continued == []


@pytest.mark.asyncio
@pytest.mark.parametrize("hook", [None, lambda **_kwargs: {}])
async def test_admitted_input_continues(hooked, monkeypatch, hook):
    hooked(hook)
    transport = _DummyTransport(_Persistence({"_id": CHAT, "workflow_name": "ValueEngine"}))
    live_run = _FakeLiveRun(result=_LiveRunResult(status=RunStatus.PAUSED))

    async def no_updates(**_kwargs):
        return {}

    monkeypatch.setattr(transport, "_apply_user_text_context_updates", no_updates)
    result = await _send(transport, live_run, named="ValueEngine")
    assert result["status"] == "success"
    assert len(live_run.continued) == 1


@pytest.mark.asyncio
async def test_refused_input_does_not_answer_a_waiting_request(hooked, monkeypatch):
    hooked(_refuse([]))
    transport = _DummyTransport(_Persistence({"_id": CHAT, "workflow_name": "OperatorOnly"}))
    transport._input_request_registries = {CHAT: {"req-1": SimpleNamespace()}}
    monkeypatch.setattr(_bridge_mod, "get_workflow_lifecycle_hooks", lambda _workflow_name: {})
    result = await transport.handle_user_input_from_api(
        chat_id=CHAT, user_id=USER, workflow_name="NamedByTheRequest", message="continue", app_id=APP,
    )
    assert result["error_code"] == "WORKFLOW_ACCESS_DENIED"
    assert transport.submitted_inputs == []
