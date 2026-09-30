"""The workflow channel deadline measures inactivity, not total run time.

These runs drive the real runner and AG2 Hub on a virtual clock. Agents await
``asyncio.sleep`` for their model-call time and the loop jumps to the next
timer instead of blocking, so a 250 s timeline finishes in milliseconds.
"""

from __future__ import annotations

import asyncio
import selectors
from collections.abc import Coroutine
from typing import Any

import pytest

from mozaiksai.core.adapters.ag2_network_runner import (
    DEFAULT_IDLE_TIMEOUT_SECONDS,
    AG2NetworkRunner,
    AG2NetworkRunnerRequest,
    AG2NetworkRunnerResult,
    checkpoint_agent_context,
)
from mozaiksai.core.ports.orchestration import RunStatus
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge, _workflow_tool_invocation
from mozaiksai.core.workflow.context.authority import (
    TASK_BATCH_WRITER,
    build_context_authority_policy,
)
from tests.test_ag2_network_execution_alignment import _DeterministicAgent, _Reply


class _VirtualClockSelector:
    """Advance a virtual clock to the next timer instead of blocking on it."""

    def __init__(self) -> None:
        self._selector = selectors.DefaultSelector()
        self.now = 0.0

    def select(self, timeout: float | None = None) -> list[Any]:
        ready = self._selector.select(0)
        if ready:
            return ready
        if timeout is None:
            raise RuntimeError("virtual clock stalled: nothing is ready or scheduled")
        self.now += timeout
        return []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._selector, name)


class _VirtualClockLoop(asyncio.SelectorEventLoop):
    def __init__(self) -> None:
        self._virtual_clock = _VirtualClockSelector()
        super().__init__(self._virtual_clock)  # type: ignore[arg-type]

    def time(self) -> float:
        return self._virtual_clock.now


async def _cancel_leftover_tasks() -> None:
    leftover = asyncio.all_tasks() - {asyncio.current_task()}
    for task in leftover:
        task.cancel()
    await asyncio.gather(*leftover, return_exceptions=True)


def _run_on_virtual_clock(main: Coroutine[Any, Any, Any]) -> Any:
    loop = _VirtualClockLoop()
    try:
        return loop.run_until_complete(main)
    finally:
        loop.run_until_complete(_cancel_leftover_tasks())
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()


class _TimedAgent(_DeterministicAgent):
    """Reply to each turn after a simulated model call of the given seconds."""

    def __init__(self, name: str, turns: list[tuple[float, str]]) -> None:
        super().__init__(name, "")
        self._turns = turns
        self.replied_at: list[float] = []
        self.cancelled = asyncio.Event()

    async def ask(self, *msg: Any, **kwargs: Any) -> _Reply:  # type: ignore[override]
        seconds, self._body = self._turns[len(self.ask_calls)]
        try:
            await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        reply = await super().ask(*msg, **kwargs)
        self.replied_at.append(asyncio.get_running_loop().time())
        return reply


def _chain(names: list[str]) -> list[dict[str, Any]]:
    rules = [
        {"source_agent": source, "target_agent": target, "transition_type": "after_turn"}
        for source, target in zip(names, names[1:], strict=False)
    ]
    rules.append({"source_agent": names[-1], "target_agent": "terminate", "transition_type": "after_turn"})
    return rules


async def _settle(
    entry: str, steps: list[_TimedAgent], *, idle_timeout_seconds: float,
) -> tuple[AG2NetworkRunnerResult, float]:
    """Run ``steps`` from a fresh channel or a user reply; return result and start time."""
    loop = asyncio.get_running_loop()
    agents: dict[str, Any] = {step.name: step for step in steps}
    rules = _chain([step.name for step in steps])
    if entry == "run":
        started = loop.time()
        result = await AG2NetworkRunner().run(AG2NetworkRunnerRequest(
            workflow_name="IdleDeadline", chat_id="idle-run", app_id="idle-app",
            agents=agents, transition_rules=rules,
            initial_agent_name=steps[0].name, initial_message="Begin.",
            idle_timeout_seconds=idle_timeout_seconds,
        ))
        return result, started

    interviewer = _TimedAgent("Interviewer", [(0.0, "What should I build?")])
    paused = await AG2NetworkRunner().run(AG2NetworkRunnerRequest(
        workflow_name="IdleDeadline", chat_id="idle-continuation", app_id="idle-app",
        agents={"Interviewer": interviewer, **agents},
        transition_rules=[
            {"source_agent": "Interviewer", "target_agent": "user", "transition_type": "after_turn"},
            {"source_agent": "user", "target_agent": steps[0].name, "transition_type": "after_turn"},
            *rules,
        ],
        initial_agent_name="Interviewer", initial_message="Begin.",
        idle_timeout_seconds=idle_timeout_seconds,
    ))
    assert paused.status is RunStatus.PAUSED, paused.error
    live_run = paused.live_run
    try:
        started = loop.time()
        result = await live_run.continue_with_user_message("A tracker.")
        assert live_run._closed
        return result, started
    finally:
        await live_run.close()


@pytest.mark.parametrize("entry", ["run", "continuation"])
def test_steady_progress_outlives_the_idle_budget(entry: str) -> None:
    steps = [_TimedAgent(f"Step{index}", [(50.0, f"step {index} done")]) for index in range(1, 6)]

    async def scenario() -> tuple[AG2NetworkRunnerResult, float, float]:
        result, started = await _settle(entry, steps, idle_timeout_seconds=120.0)
        return result, started, asyncio.get_running_loop().time()

    result, started, settled = _run_on_virtual_clock(scenario())

    assert result.status is RunStatus.COMPLETED, result.error
    assert result.close_reason == "workflow_complete"
    assert [step.replied_at[0] - started for step in steps] == pytest.approx([50.0, 100.0, 150.0, 200.0, 250.0])
    # 250 s in total, more than twice the 120 s budget, and never idle for it.
    assert settled - started == pytest.approx(250.0)


@pytest.mark.parametrize("entry", ["run", "continuation"])
def test_no_progress_for_the_idle_budget_fails_and_cancels_the_turn(entry: str) -> None:
    first = _TimedAgent("Step1", [(50.0, "step 1 done")])
    stuck = _TimedAgent("Step2", [(10_000.0, "never sent")])

    async def scenario() -> tuple[AG2NetworkRunnerResult, float, float]:
        result, started = await _settle(entry, [first, stuck], idle_timeout_seconds=120.0)
        failed_at = asyncio.get_running_loop().time()
        await asyncio.wait_for(stuck.cancelled.wait(), timeout=1.0)
        return result, started, failed_at

    result, started, failed_at = _run_on_virtual_clock(scenario())

    assert result.status is RunStatus.FAILED
    assert result.live_run is None
    assert result.error == (
        "workflow channel made no progress for 120.0 seconds (last progress: ag2.packet from Step1)"
    )
    # Idle time counts from Step1's packet at 50 s, not from the start.
    assert failed_at - started == pytest.approx(170.0)
    assert stuck.replied_at == []


def test_designdocs_retry_from_chat_c65f5d0f_is_not_cancelled() -> None:
    """Replay the live DesignDocs chat c65f5d0f at 25ebe474.

    Attempt 1 committed its packet at 43 s and its save was rejected. The retry
    was still streaming when the 120 s whole-run deadline cancelled it. With
    the default idle budget, a retry taking 80 s completes.
    """
    designer = _TimedAgent("DesignDocsAgent", [(43.0, "rejected draft"), (80.0, "accepted draft")])

    async def scenario() -> tuple[AG2NetworkRunnerResult, float, float]:
        loop = asyncio.get_running_loop()
        started = loop.time()
        result = await AG2NetworkRunner().run(AG2NetworkRunnerRequest(
            workflow_name="DesignDocs", chat_id="c65f5d0f-replay", app_id="replay-app",
            agents={"DesignDocsAgent": designer},
            transition_rules=[
                {
                    "source_agent": "DesignDocsAgent", "target_agent": "DesignDocsAgent",
                    "transition_type": "condition", "condition_type": "context_equals",
                    "condition_key": "save_outcome", "condition_value": "revise",
                },
                {"source_agent": "DesignDocsAgent", "target_agent": "terminate", "transition_type": "after_turn"},
            ],
            agent_text_context_deriver=lambda name, text: {
                "save_outcome": "revise" if text == "rejected draft" else "accepted",
            },
            initial_agent_name="DesignDocsAgent", initial_message="Design the app.", max_turns=8,
        ))
        return result, started, loop.time()

    # The request default is the production budget; this run does not override it.
    default_budget = AG2NetworkRunnerRequest.__dataclass_fields__["idle_timeout_seconds"].default
    assert default_budget == DEFAULT_IDLE_TIMEOUT_SECONDS == 300.0
    result, started, settled = _run_on_virtual_clock(scenario())

    assert result.status is RunStatus.COMPLETED, result.error
    assert [at - started for at in designer.replied_at] == pytest.approx([43.0, 123.0])
    assert settled - started == pytest.approx(123.0)
    assert not designer.cancelled.is_set()
    assert result.context_variables["save_outcome"] == "accepted"


@pytest.mark.parametrize("checkpoints", [True, False])
def test_context_checkpoints_count_as_progress(checkpoints: bool) -> None:
    planner = _TimedAgent("Planner", [(0.0, "planned")])
    policy = build_context_authority_policy(
        workflow_name="IdleCheckpoint", definitions={"batch_results": {"type": "object", "source": "computed"}},
        task_batch_context_keys={"batch_results"},
    )
    bridge = ContextVariablesBridge({}, authority_policy=policy)
    planner._mozaiks_context_bridge = bridge

    async def output_handler(agent_name: str, packet: Any) -> None:
        # Three 100 s batch tasks run before the planner's packet commits.
        for completed in range(1, 4):
            await asyncio.sleep(100.0)
            if checkpoints:
                with _workflow_tool_invocation(bridge, writer_id=TASK_BATCH_WRITER):
                    bridge.set("batch_results", {"completed": completed})
                await checkpoint_agent_context()

    async def scenario() -> tuple[AG2NetworkRunnerResult, float]:
        loop = asyncio.get_running_loop()
        started = loop.time()
        result = await AG2NetworkRunner().run(AG2NetworkRunnerRequest(
            workflow_name="IdleCheckpoint", chat_id="idle-checkpoint", app_id="idle-app",
            agents={"Planner": planner}, transition_rules=_chain(["Planner"]),
            initial_agent_name="Planner", initial_message="Build.",
            agent_output_handler=output_handler, context_authority_policy=policy,
            idle_timeout_seconds=120.0,
        ))
        return result, loop.time() - started

    result, elapsed = _run_on_virtual_clock(scenario())

    if checkpoints:
        assert result.status is RunStatus.COMPLETED, result.error
        assert result.context_variables["batch_results"] == {"completed": 3}
        assert elapsed == pytest.approx(300.0)
    else:
        assert result.status is RunStatus.FAILED
        assert result.error == (
            "workflow channel made no progress for 120.0 seconds (last progress: ag2.msg.text from user)"
        )
        assert elapsed == pytest.approx(120.0)
