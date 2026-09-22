"""One accepted execution announces its completion exactly once.

``SimpleTransport.send_event_to_ui`` dispatches ``runtime.process_completed``
for every ``chat.run_complete`` envelope, and the journey orchestrator is its
listener. When ``_run_workflow_background`` also emitted the event, every
handoff-started run dispatched twice: the journey advanced twice, the second
advance spawned a second start of the same in-progress child, and the chat
execution lease correctly refused it as CHAT_LOCK_BUSY.

These tests drive the real send path — real transport, real envelope builder,
real dispatch hook — so the count is the system's, not a fake's. The AG2
outcome itself is stubbed; one case has that stub announce its own outcome the
way ``orchestration_patterns`` does, so the production emitter is covered and
not only the completion backstop.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from mozaiksai.core.events import unified_event_dispatcher as _dispatcher_mod
from mozaiksai.core.ports.orchestration import RunStatus
from mozaiksai.core.transport import workflow_bridge as _bridge_mod
from mozaiksai.core.transport.simple_transport import SimpleTransport

CHAT_ID = "chat-handoff-1"
APP_ID = "app-1"
USER_ID = "user-1"
WORKFLOW = "SubscriptionContractDesigner"


class _FakePersistence:
    """Only the seams the accepted start path touches."""

    def __init__(self) -> None:
        self.status = 0
        self.completed: list[str] = []
        self.failed: list[str] = []

    async def chat_session_exists(self, chat_id, app_id, workflow_name=None):  # noqa: ANN001
        return False

    async def assert_chat_resumable(self, chat_id, app_id) -> None:  # noqa: ANN001
        from mozaiksai.core.data.models import WorkflowStatus
        from mozaiksai.core.data.persistence.persistence_manager import ChatSessionTerminalError

        if self.status:
            raise ChatSessionTerminalError(WorkflowStatus(self.status))

    async def get_pending_input_request(self, **kwargs):  # noqa: ANN003
        return None

    async def clear_pending_input_request(self, **kwargs) -> None:  # noqa: ANN003
        return None

    async def append_run_user_message(self, **kwargs) -> None:  # noqa: ANN003
        return None

    async def append_run_assistant_message(self, **kwargs) -> None:  # noqa: ANN003
        return None

    async def persist_context_variables(self, **kwargs) -> None:  # noqa: ANN003
        return None

    async def mark_chat_completed(self, chat_id, app_id=None) -> bool:  # noqa: ANN001
        self.completed.append(chat_id)
        return True

    async def mark_chat_failed(self, chat_id, app_id=None) -> bool:  # noqa: ANN001
        self.failed.append(chat_id)
        self.status = 2
        return True


class _FakeAdapter:
    """Stands in for AG2. With ``announce`` it also reports its own outcome.

    ``orchestration_patterns`` sends one run_complete envelope for every
    terminal outcome before returning. Without ``announce`` the only envelope
    is the bridge's completion backstop, which is a different, later ordering.
    """

    def __init__(self, status: RunStatus = RunStatus.COMPLETED) -> None:
        self.status = status
        self.runs = 0
        self.transport = None
        self.announce = False

    async def _announce(self) -> None:
        run_completed = self.status is RunStatus.COMPLETED
        awaiting = self.status is RunStatus.PAUSED
        await self.transport.send_event_to_ui(
            {
                "kind": "run_complete",
                "workflow": WORKFLOW,
                "chat_id": CHAT_ID,
                "run_completed": run_completed,
                "awaiting_user_input": awaiting,
                "status": self.status.value,
                "reason": "finished" if run_completed else self.status.value,
            },
            CHAT_ID,
        )

    async def run(self, request):  # noqa: ANN001
        self.runs += 1
        if self.announce and self.transport is not None:
            await self._announce()
        return SimpleNamespace(status=self.status, error=None)

    async def resume(self, request):  # noqa: ANN001
        self.runs += 1
        if self.announce and self.transport is not None:
            await self._announce()
        return SimpleNamespace(status=self.status, error=None)


@pytest.fixture
def live_send_path(monkeypatch):
    """A real transport whose only stub is the websocket boundary."""
    transport = SimpleTransport()
    persistence = _FakePersistence()
    adapter = _FakeAdapter()
    adapter.transport = transport

    broadcast: list[dict] = []

    async def _record_broadcast(envelope, chat_id=None):  # noqa: ANN001
        broadcast.append({"chat_id": chat_id, "envelope": envelope})

    emitted: list[tuple[str, dict]] = []
    dispatcher = _dispatcher_mod.get_event_dispatcher()

    # Patched on the class, so the recorder takes the dispatcher as ``self``.
    async def _record_emit(_self, event_name, payload=None, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        emitted.append((event_name, payload))

    # The journey handoff aliases the source connection onto the new chat before
    # it spawns the run (JourneyOrchestrator._ensure_connection_alias), and the
    # send path reads identity from that entry. Reproduce it.
    transport.connections[CHAT_ID] = {
        "websocket": object(),
        "ws_id": 7,
        "app_id": APP_ID,
        "user_id": USER_ID,
        "workflow_name": WORKFLOW,
        "active": True,
    }

    monkeypatch.setattr(type(dispatcher), "emit", _record_emit)
    monkeypatch.setattr(transport, "_broadcast_to_websockets", _record_broadcast)
    monkeypatch.setattr(transport, "_get_or_create_persistence_manager", lambda: persistence)
    monkeypatch.setattr(_bridge_mod, "get_workflow_lifecycle_hooks", lambda _name: {})
    monkeypatch.setattr(_bridge_mod.session_registry, "complete_workflow", lambda *a, **k: None)

    ag2_mod = __import__(
        "mozaiksai.core.adapters.ag2_orchestration", fromlist=["get_ag2_adapter"]
    )
    monkeypatch.setattr(ag2_mod, "get_ag2_adapter", lambda: adapter)

    async def run() -> None:
        await transport._run_workflow_background(
            chat_id=CHAT_ID,
            workflow_name=WORKFLOW,
            app_id=APP_ID,
            user_id=USER_ID,
            ws_id=7,
            initial_message=None,
        )
        await asyncio.sleep(0)

    return SimpleNamespace(
        transport=transport,
        persistence=persistence,
        adapter=adapter,
        broadcast=broadcast,
        emitted=emitted,
        run=run,
    )


def _completions(emitted: list[tuple[str, dict]]) -> list[dict]:
    return [payload for name, payload in emitted if name == "runtime.process_completed"]


def _run_complete_envelopes(broadcast: list[dict]) -> list[dict]:
    return [
        entry["envelope"]
        for entry in broadcast
        if isinstance(entry["envelope"], dict)
        and entry["envelope"].get("type") == "chat.run_complete"
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [RunStatus.COMPLETED, RunStatus.PAUSED, RunStatus.FAILED])
async def test_a_run_that_announces_its_own_outcome_dispatches_once(live_send_path, status):
    """The production ordering: the run sends its envelope, the wrapper adds nothing."""
    live_send_path.adapter.status = status
    live_send_path.adapter.announce = True

    await live_send_path.run()

    completions = _completions(live_send_path.emitted)
    assert len(completions) == 1, f"expected one completion dispatch, got {completions}"
    assert completions[0]["status"] == status.value
    assert completions[0]["chat_id"] == CHAT_ID

    envelopes = _run_complete_envelopes(live_send_path.broadcast)
    assert len(envelopes) == 1
    # The run announced itself, so the bridge's completion backstop stayed silent.
    assert (envelopes[0].get("data") or {}).get("metadata", {}).get("source") is None


@pytest.mark.asyncio
async def test_accepted_run_dispatches_process_completed_exactly_once(live_send_path):
    """A run that announces nothing: the completion backstop is the single emitter."""
    await live_send_path.run()

    completions = _completions(live_send_path.emitted)
    assert len(completions) == 1, f"expected one completion dispatch, got {completions}"

    # The surviving dispatch must be one the journey orchestrator acts on, and
    # must carry the identity it needs; otherwise the handoff silently stops.
    from mozaiksai.core.workflow.pack import journey_orchestrator

    surviving = completions[0]
    assert surviving["chat_id"] == CHAT_ID
    assert surviving["app_id"] == APP_ID
    assert surviving["user_id"] == USER_ID
    assert (surviving.get("workflow_name") or surviving.get("workflow")) == WORKFLOW
    assert journey_orchestrator._is_successful_completion(surviving), surviving

    assert len(_run_complete_envelopes(live_send_path.broadcast)) == 1
    assert live_send_path.adapter.runs == 1


@pytest.mark.asyncio
async def test_rejected_start_still_dispatches_its_outcome(live_send_path):
    """A terminal chat sends no run_complete envelope, so the wrapper is its only signal."""
    live_send_path.persistence.status = 1

    await live_send_path.run()

    completions = _completions(live_send_path.emitted)
    assert len(completions) == 1
    assert completions[0]["status"] == "failed"
    assert completions[0]["error_code"] == "WORKFLOW_SESSION_TERMINAL"
    assert completions[0]["route"] == "terminal_session"
    assert live_send_path.adapter.runs == 0
    assert not _run_complete_envelopes(live_send_path.broadcast)
