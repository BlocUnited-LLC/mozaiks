"""The Mozaiks watchdog consumes AG2's live descendant source accounting."""

from __future__ import annotations

import pytest
from ag2 import Agent, observer, tool
from ag2.events import ModelMessage, ModelResponse, ToolCallEvent, ToolCallsEvent, UsageEvent
from ag2.events.types import Usage
from ag2.testing import TestConfig

from mozaiksai.core.usage.watchdog import build_ag2_token_watchdog_observers


def _response(tokens: int, *, delegate: str | None = None) -> ModelResponse:
    usage = Usage(prompt_tokens=tokens - 1, completion_tokens=1, total_tokens=tokens)
    if delegate:
        return ModelResponse(
            tool_calls=ToolCallsEvent([
                ToolCallEvent(delegate, arguments='{"objective":"complete the assigned step"}'),
            ]),
            usage=usage,
        )
    return ModelResponse(ModelMessage("completed"), usage=usage)


@pytest.mark.asyncio
@pytest.mark.parametrize("leaf_fails", [False, True])
async def test_watchdog_counts_native_descendant_usage_once_even_when_worker_fails(leaf_fails):
    records: list[UsageEvent] = []

    @observer(UsageEvent)
    def collect_usage(event: UsageEvent) -> None:
        records.append(event)

    @tool
    def reject(objective: str) -> str:
        """Fail this fake worker after the model has already consumed tokens."""
        raise RuntimeError("planned worker failure")

    leaf = Agent(
        "leaf",
        config=TestConfig(_response(7, delegate="reject" if leaf_fails else None)),
        tools=[reject] if leaf_fails else [],
    )
    worker = Agent(
        "worker",
        config=TestConfig(_response(3, delegate="task_leaf"), _response(5)),
        tools=[leaf.as_tool(description="Execute the leaf step")],
    )
    monitor, _ = build_ag2_token_watchdog_observers(
        agent_name="coordinator",
        workflow_name="UsageContract",
        context_variables={"token_watchdog_warn_tokens": 100, "token_watchdog_alert_tokens": 200},
    )
    coordinator = Agent(
        "coordinator",
        config=TestConfig(_response(2, delegate="task_worker"), _response(11)),
        tools=[worker.as_tool(description="Execute the worker step")],
        observers=[monitor, collect_usage],
    )

    reply = await coordinator.ask("Run the declared fake work")
    report = await reply.usage()

    assert reply.body == "completed"
    assert monitor.total_tokens == report.total.total_tokens == 28
    assert len(records) == len({event.event_id for event in records}) == 5
    assert sorted(len(event.path) for event in records) == [0, 0, 1, 1, 2]
    assert {event.kind for event in records} == {"model_call"}
    # Re-observing retained records cannot bill the watchdog a second time.
    await monitor.process(records, reply.context)
    assert monitor.total_tokens == 28
