from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mozaiksai.core.data.models import WorkflowStatus
from mozaiksai.core.workflow.stream.context import StreamState
from mozaiksai.core.workflow.stream.handlers.completion_handler import CompletionHandler


def _build_ctx() -> SimpleNamespace:
    return SimpleNamespace(
        chat_id="chat-1",
        workflow_name_upper="WORKFLOW",
        wf_logger=logging.getLogger("test.completion_handler"),
        agents={},
        workflow_name="Workflow",
        app_id="app-1",
        user_id="user-1",
        context_variables=None,
        persistence_manager=SimpleNamespace(clear_pending_input_request=AsyncMock()),
    )


@pytest.mark.asyncio
async def test_completion_handler_marks_terminal_completion(monkeypatch) -> None:
    handler = CompletionHandler()
    async def _noop(_ctx, _state) -> None:
        return None
    monkeypatch.setattr(handler, "_dispatch_webhook", _noop)
    ctx = _build_ctx()
    state = StreamState(turn_agent="PlannerAgent", sequence_counter=4)
    state.executed_agents.update({"PlannerAgent", "ReviewerAgent"})

    payload = await handler.handle(object(), ctx, state)

    assert state.run_completed is True
    ctx.persistence_manager.clear_pending_input_request.assert_awaited_once_with(
        chat_id="chat-1",
        app_id="app-1",
    )
    assert payload["status"] == int(WorkflowStatus.COMPLETED)
    assert payload["reason"] == "finished"
    assert payload["awaiting_user_input"] is False


@pytest.mark.asyncio
async def test_completion_handler_keeps_run_in_progress_when_input_is_pending(monkeypatch) -> None:
    handler = CompletionHandler()
    async def _noop(_ctx, _state) -> None:
        return None
    monkeypatch.setattr(handler, "_dispatch_webhook", _noop)
    ctx = _build_ctx()
    state = StreamState(turn_agent="ValueInterviewAgent", sequence_counter=2)
    state.awaiting_user_input = True
    state.pending_input_requests["req-1"] = object()
    state.executed_agents.add("ValueInterviewAgent")

    payload = await handler.handle(object(), ctx, state)

    assert state.run_completed is False
    ctx.persistence_manager.clear_pending_input_request.assert_not_awaited()
    assert payload["status"] == int(WorkflowStatus.IN_PROGRESS)
    assert payload["reason"] == "awaiting_user_input"
    assert payload["awaiting_user_input"] is True
