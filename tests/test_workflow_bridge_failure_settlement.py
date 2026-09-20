from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mozaiksai.core.ports.orchestration import RunStatus
from mozaiksai.core.session import router as session_router
from mozaiksai.core.workflow.execution import lifecycle
from mozaiksai.core.workflow.outputs import structured
from tests.test_workflow_bridge import (
    _DummyTransport,
    _FakeLiveRun,
    _FakePersistenceManager,
    _LiveRunResult,
)


@pytest.fixture
def live_settlement(monkeypatch):
    persistence = _FakePersistenceManager()
    transport = _DummyTransport(persistence)
    trigger = AsyncMock()
    revision_failed = AsyncMock()
    resolve_router = AsyncMock(return_value=SimpleNamespace(fail_active_revision=revision_failed))
    monkeypatch.setattr(transport, "_apply_user_text_context_updates", AsyncMock(return_value={}))
    monkeypatch.setattr(structured, "load_workflow_structured_outputs", lambda _: ({}, {}))
    monkeypatch.setattr(lifecycle, "get_lifecycle_manager", lambda _: SimpleNamespace(execute_trigger=trigger))
    monkeypatch.setattr(session_router, "get_session_router_for_chat", resolve_router)
    return SimpleNamespace(
        persistence=persistence, transport=transport, trigger=trigger,
        revision_failed=revision_failed, resolve_router=resolve_router,
    )


async def _continue(fixture, *, status: RunStatus, error: str | None = None):
    result = _LiveRunResult(status=status)
    result.error = error
    live_run = _FakeLiveRun(result=result)
    fixture.transport.register_live_ag2_workflow_run("chat-1", live_run)
    response = await fixture.transport._continue_live_ag2_workflow_run(
        live_run=live_run, chat_id="chat-1", app_id="execution-app", user_id="owner-1",
        workflow_name="AppGenerator", message="Proceed",
    )
    return response, live_run


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    "AG2 turn failed for AppPlanAgent: structured output validation failed: truncated JSON",
    "AG2 turn failed for AppPlanAgent: provider unavailable",
    "workflow channel did not settle within 120.0 seconds",
])
async def test_live_failure_runs_declared_lifecycle_and_target_scoped_revision_cleanup(live_settlement, error):
    fixture = live_settlement
    order = []

    async def record_failure(*args, **kwargs):
        assert fixture.persistence.status == 2
        order.append("on_fail")

    async def record_revision(**kwargs):
        order.append("revision")

    send = fixture.transport.send_event_to_ui

    async def record_ui(event, chat_id):
        if event.get("kind") == "run_complete":
            order.append("terminal_event")
        await send(event, chat_id)

    fixture.trigger.side_effect = record_failure
    fixture.revision_failed.side_effect = record_revision
    fixture.transport.send_event_to_ui = record_ui
    response, _ = await _continue(fixture, status=RunStatus.FAILED, error=error)

    assert order == ["on_fail", "revision", "terminal_event"]
    fixture.trigger.assert_awaited_once_with(
        "on_fail", context_variables={"target_user": "founders"},
        app_id="execution-app", execution_id="chat-1", chat_id="chat-1", user_id="owner-1",
        workflow_name="AppGenerator", error=error,
    )
    fixture.resolve_router.assert_awaited_once_with(
        app_id="execution-app", user_id="owner-1", chat_id="chat-1",
    )
    fixture.revision_failed.assert_awaited_once_with(
        app_id="execution-app", user_id="owner-1", workflow_id="AppGenerator",
    )
    assert fixture.transport.get_live_ag2_workflow_run("chat-1") is None
    assert fixture.persistence.completed == []
    assert fixture.persistence.failed == [{"chat_id": "chat-1", "app_id": "execution-app"}]
    assert response["status"] == "error"
    assert response["run_status"] == "failed"
    terminal = [item["event"] for item in fixture.transport.sent_ui_events
                if item["event"].get("kind") == "run_complete"]
    assert len(terminal) == 1
    assert terminal[0]["status"] == "failed"
    assert terminal[0]["error"] == error
    assert terminal[0]["run_completed"] is False
    assert terminal[0]["awaiting_user_input"] is False
    if "structured output validation failed" in error:
        assert fixture.persistence.run_assistant_messages == []
        assert not any(item["event"].get("kind") == "chat.text"
                       for item in fixture.transport.sent_ui_events)
    else:
        assert len(fixture.persistence.run_assistant_messages) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [RunStatus.PAUSED, RunStatus.COMPLETED])
async def test_live_nonfailure_keeps_existing_pause_and_completion_semantics(live_settlement, status):
    fixture = live_settlement
    response, live_run = await _continue(fixture, status=status)
    assert response["status"] == "success"
    assert response["run_status"] == status.value
    fixture.trigger.assert_not_awaited()
    fixture.resolve_router.assert_not_awaited()
    fixture.revision_failed.assert_not_awaited()
    assert fixture.persistence.failed == []
    if status is RunStatus.PAUSED:
        assert fixture.transport.get_live_ag2_workflow_run("chat-1") is live_run
        assert fixture.persistence.completed == []
    else:
        assert fixture.transport.get_live_ag2_workflow_run("chat-1") is None
        assert fixture.persistence.completed == [{"chat_id": "chat-1", "app_id": "execution-app"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("unavailable", ["lifecycle", "revision_scope", "revision_cleanup"])
async def test_live_failure_observer_errors_do_not_claim_success(live_settlement, unavailable, caplog):
    fixture = live_settlement
    operation = {
        "lifecycle": fixture.trigger,
        "revision_scope": fixture.resolve_router,
        "revision_cleanup": fixture.revision_failed,
    }[unavailable]
    operation.side_effect = RuntimeError("observer unavailable")
    response, _ = await _continue(fixture, status=RunStatus.FAILED, error="turn failed")
    assert response["run_status"] == "failed"
    assert response["status"] == "error"
    assert fixture.persistence.completed == []
    assert "observer unavailable" in caplog.text
    assert fixture.transport.sent_ui_events[-1]["event"]["status"] == "failed"
    fixture.trigger.assert_awaited_once()
    if unavailable == "lifecycle":
        fixture.revision_failed.assert_awaited_once()


@pytest.mark.asyncio
async def test_live_failure_settles_lifecycle_before_ui_delivery_failure(live_settlement):
    fixture = live_settlement
    fixture.transport.send_event_to_ui = AsyncMock(side_effect=RuntimeError("UI unavailable"))
    with pytest.raises(RuntimeError, match="UI unavailable"):
        await _continue(
            fixture, status=RunStatus.FAILED,
            error="structured output validation failed: truncated JSON",
        )
    fixture.trigger.assert_awaited_once()
    fixture.revision_failed.assert_awaited_once()
    assert fixture.persistence.completed == []


@pytest.mark.asyncio
async def test_live_failure_dispatches_actual_appgenerator_on_fail_contract(monkeypatch, live_settlement):
    calls = []

    def load_tool(self, base_dir, file_name, func_name):
        async def tool(context_variables=None, **kwargs):
            calls.append((func_name, context_variables, kwargs))
        return tool

    monkeypatch.setattr(lifecycle.LifecycleToolManager, "_load_tool_function", load_tool)
    manager = lifecycle.LifecycleToolManager("AppGenerator")
    manager.load_lifecycle_tools()
    monkeypatch.setattr(manager, "_emit_lifecycle_event", AsyncMock())
    monkeypatch.setattr(lifecycle, "get_lifecycle_manager", lambda _: manager)
    await _continue(live_settlement, status=RunStatus.FAILED, error="plan truncated")
    assert [call[0] for call in calls] == ["emit_build_failed"]
    assert calls[0][1] == {"target_user": "founders"}
    assert calls[0][2] == {
        "app_id": "execution-app", "execution_id": "chat-1", "chat_id": "chat-1",
        "user_id": "owner-1", "workflow_name": "AppGenerator", "error": "plan truncated",
    }
