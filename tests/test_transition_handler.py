from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from mozaiksai.core.workflow.stream import EventStreamProcessor, StreamContext
from mozaiksai.core.workflow.stream.context import StreamState
from mozaiksai.core.workflow.stream.handlers import transition_handler as transition_handler_module
from mozaiksai.core.workflow.stream.handlers.transition_handler import TransitionHandler


class _FakePattern:
    group_manager = None
    context_variables = None


class _FakeIterResponse:
    def __init__(self, events, task=None):
        self._events = list(events)
        self._task = task

    def __aiter__(self):
        return self._generator()

    async def _generator(self):
        for event in self._events:
            yield event


class _FakeTask:
    def __init__(self) -> None:
        self.cancel_called = False

    def cancel(self) -> None:
        self.cancel_called = True


class _FakeRevertToUserTarget:
    pass


class _FakeAfterWorksTransitionEvent:
    def __init__(self, source_agent=None, transition_target=None):
        self.source_agent = source_agent
        self.transition_target = transition_target


def _build_ctx() -> StreamContext:
    return StreamContext(
        chat_id="chat-1",
        app_id="app-1",
        workflow_name="Workflow",
        user_id="user-1",
        pattern=_FakePattern(),
        transport=None,
        persistence_manager=None,
        lifecycle_manager=None,
        derived_context_manager=None,
        perf_mgr=None,
        dispatcher=None,
        agents={},
        structured_registry={},
        validated_output_agents=set(),
        auto_tool_agents=set(),
        max_turns=5,
        wf_logger=logging.getLogger("test.transition_handler"),
        workflow_name_upper="WORKFLOW",
    )


@pytest.mark.asyncio
async def test_transition_handler_observes_user_handoff_without_synthesizing_input(monkeypatch) -> None:
    monkeypatch.setattr(transition_handler_module, "RevertToUserTarget", _FakeRevertToUserTarget)

    handler = TransitionHandler()
    ctx = _build_ctx()
    state = StreamState(turn_agent="PlannerAgent")
    event = _FakeAfterWorksTransitionEvent(
        source_agent=SimpleNamespace(name="PlannerAgent"),
        transition_target=_FakeRevertToUserTarget(),
    )

    payload = await handler.handle(event, ctx, state)

    assert payload is None
    assert state.run_completed is False
    assert handler.should_break(event, state) is False


@pytest.mark.asyncio
async def test_event_stream_processor_does_not_cancel_on_transition_metadata(monkeypatch) -> None:
    monkeypatch.setattr(transition_handler_module, "AfterWorksTransitionEvent", _FakeAfterWorksTransitionEvent)
    monkeypatch.setattr(transition_handler_module, "RevertToUserTarget", _FakeRevertToUserTarget)

    processor = EventStreamProcessor()
    task = _FakeTask()
    response = _FakeIterResponse(
        [
            _FakeAfterWorksTransitionEvent(
                source_agent=SimpleNamespace(name="PlannerAgent"),
                transition_target=_FakeRevertToUserTarget(),
            )
        ],
        task=task,
    )

    result = await processor.process_stream(response, _build_ctx())

    assert result["response"] is response
    assert result["run_completed"] is False
    assert task.cancel_called is False