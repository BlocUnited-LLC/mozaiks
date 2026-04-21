import logging

import pytest

from mozaiksai.core.workflow.stream import EventStreamProcessor, StreamContext


class _FakePattern:
    group_manager = None
    context_variables = None


class _FakeIterResponse:
    def __init__(self, events):
        self._events = list(events)

    def __aiter__(self):
        return self._generator()

    async def _generator(self):
        for event in self._events:
            yield event


class _FakeEvent:
    pass


@pytest.mark.asyncio
async def test_process_stream_accepts_direct_async_iterator_response() -> None:
    processor = EventStreamProcessor()
    response = _FakeIterResponse([_FakeEvent()])
    logger = logging.getLogger("test.event_stream_processor_iter_response")
    ctx = StreamContext(
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
        wf_logger=logger,
        workflow_name_upper="WORKFLOW",
    )

    result = await processor.process_stream(response, ctx)

    assert result["response"] is response
    assert result["sequence_counter"] == 1
    assert result["run_completed"] is False
