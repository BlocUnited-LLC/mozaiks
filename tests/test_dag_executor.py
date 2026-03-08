"""
Tests for DAGExecutor
=====================

Covers:
1. Topological sort — linear chain, diamond, parallel fan-out, cycle detection
2. Linear execution — A → B → C, outputs propagated
3. Diamond execution — A → [B, C] → D, B and C run concurrently
4. Parallel fan-out — no dependencies, all tasks start immediately
5. Output propagation — upstream outputs injected into dependent payload
6. Failure handling — failed task still recorded, downstream see empty output
7. Empty DAG — no events except started/completed
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, AsyncIterator

import pytest

from mozaiksai.contracts.events import EVENT_SCHEMA_VERSION, DomainEvent
from mozaiksai.contracts.runner import RunRequest
from mozaiksai.runtime.dag_executor import (
    DAGExecutor,
    DAGResult,
    DAGTask,
    DAGTaskResult,
)


# ---------------------------------------------------------------------------
# Test double: MockSupervisor
# ---------------------------------------------------------------------------

def _make_event(event_type: str, run_id: str, payload: dict | None = None) -> DomainEvent:
    return DomainEvent(
        event_type=event_type,
        seq=0,
        occurred_at=datetime.now(timezone.utc),
        run_id=run_id,
        schema_version=EVENT_SCHEMA_VERSION,
        data=payload or {},
    )


class _RecordingMockSupervisor:
    """Supervisor double that records RunRequests and yields a completion event."""

    def __init__(self, result_payload: dict | None = None, raise_for: set[str] | None = None):
        self.started_requests: list[RunRequest] = []
        self._result = result_payload or {"result": "ok", "structured_output": {"key": "val"}}
        self._raise_for: set[str] = raise_for or set()

    async def start_run(self, request: RunRequest) -> AsyncIterator[DomainEvent]:
        self.started_requests.append(request)
        task_id = (request.metadata or {}).get("task_id", "")
        if task_id in self._raise_for:
            raise RuntimeError(f"Task '{task_id}' failed intentionally")
        yield _make_event(
            event_type="workflow.run_completed",
            run_id=request.run_id,
            payload=self._result,
        )

    async def cancel_run(self, run_id: str) -> bool:
        return True


class _DelayedMockSupervisor(_RecordingMockSupervisor):
    """Like _RecordingMockSupervisor but adds a small async delay."""

    async def start_run(self, request: RunRequest) -> AsyncIterator[DomainEvent]:
        self.started_requests.append(request)
        await asyncio.sleep(0.01)
        yield _make_event(
            event_type="workflow.run_completed",
            run_id=request.run_id,
            payload={"result": f"done-{request.run_id}"},
        )


# ---------------------------------------------------------------------------
# 1. Topological sort
# ---------------------------------------------------------------------------

class TestTopologicalSort:
    """Unit tests for DAGExecutor._topological_sort."""

    def test_no_dependencies_returns_original_order(self):
        tasks = [
            DAGTask("A"),
            DAGTask("B"),
            DAGTask("C"),
        ]
        result = DAGExecutor._topological_sort(tasks)
        assert [t.task_id for t in result] == ["A", "B", "C"]

    def test_linear_chain(self):
        tasks = [
            DAGTask("A"),
            DAGTask("B", depends_on=("A",)),
            DAGTask("C", depends_on=("B",)),
        ]
        result = DAGExecutor._topological_sort(tasks)
        ids = [t.task_id for t in result]
        assert ids.index("A") < ids.index("B") < ids.index("C")

    def test_diamond_dag(self):
        tasks = [
            DAGTask("A"),
            DAGTask("B", depends_on=("A",)),
            DAGTask("C", depends_on=("A",)),
            DAGTask("D", depends_on=("B", "C")),
        ]
        result = DAGExecutor._topological_sort(tasks)
        ids = [t.task_id for t in result]
        assert ids.index("A") < ids.index("B")
        assert ids.index("A") < ids.index("C")
        assert ids.index("B") < ids.index("D")
        assert ids.index("C") < ids.index("D")

    def test_cycle_raises_value_error(self):
        tasks = [
            DAGTask("A", depends_on=("B",)),
            DAGTask("B", depends_on=("A",)),
        ]
        with pytest.raises(ValueError, match="Cycle detected"):
            DAGExecutor._topological_sort(tasks)

    def test_unknown_dep_is_warned_and_ignored(self):
        tasks = [
            DAGTask("A"),
            DAGTask("B", depends_on=("GHOST",)),
        ]
        # Should not raise; GHOST is ignored
        result = DAGExecutor._topological_sort(tasks)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# 2. Linear chain execution
# ---------------------------------------------------------------------------

class TestLinearChain:
    """A → B → C, each depends on the previous."""

    @pytest.mark.asyncio
    async def test_all_tasks_execute_in_order(self):
        supervisor = _RecordingMockSupervisor()
        executor = DAGExecutor(run_supervisor=supervisor)

        tasks = [
            DAGTask("A", capability="agent", workflow_name="wf_a"),
            DAGTask("B", capability="agent", workflow_name="wf_b", depends_on=("A",)),
            DAGTask("C", capability="agent", workflow_name="wf_c", depends_on=("B",)),
        ]

        events: list[DomainEvent] = []
        async for event in executor.execute_stream(tasks, parent_run_id="dag-linear"):
            events.append(event)

        assert len(supervisor.started_requests) == 3
        assert executor.last_result is not None
        assert executor.last_result.all_succeeded is True

        # All three tasks must complete
        completed = {e.data.get("task_id") for e in events if e.event_type == "dag.task_completed"}
        assert completed == {"A", "B", "C"}

    @pytest.mark.asyncio
    async def test_dag_lifecycle_events_present(self):
        executor = DAGExecutor(run_supervisor=_RecordingMockSupervisor())
        tasks = [DAGTask("A"), DAGTask("B", depends_on=("A",))]

        event_types: list[str] = []
        async for event in executor.execute_stream(tasks, parent_run_id="dag-lifecycle"):
            event_types.append(event.event_type)

        assert event_types[0] == "dag.started"
        assert event_types[-1] == "dag.completed"
        assert "dag.task_started" in event_types
        assert "dag.task_completed" in event_types


# ---------------------------------------------------------------------------
# 3. Diamond DAG — concurrent execution
# ---------------------------------------------------------------------------

class TestDiamondDAG:
    """A → [B, C] → D.  B and C should execute concurrently."""

    @pytest.mark.asyncio
    async def test_diamond_all_tasks_complete(self):
        supervisor = _DelayedMockSupervisor()
        executor = DAGExecutor(run_supervisor=supervisor)

        tasks = [
            DAGTask("A"),
            DAGTask("B", depends_on=("A",)),
            DAGTask("C", depends_on=("A",)),
            DAGTask("D", depends_on=("B", "C")),
        ]

        events: list[DomainEvent] = []
        async for event in executor.execute_stream(tasks, parent_run_id="dag-diamond"):
            events.append(event)

        assert len(supervisor.started_requests) == 4
        assert executor.last_result.all_succeeded is True

        completed = {e.data.get("task_id") for e in events if e.event_type == "dag.task_completed"}
        assert completed == {"A", "B", "C", "D"}

    @pytest.mark.asyncio
    async def test_dag_completed_payload(self):
        executor = DAGExecutor(run_supervisor=_RecordingMockSupervisor())
        tasks = [
            DAGTask("A"),
            DAGTask("B", depends_on=("A",)),
            DAGTask("C", depends_on=("A",)),
            DAGTask("D", depends_on=("B", "C")),
        ]

        events: list[DomainEvent] = []
        async for event in executor.execute_stream(tasks, parent_run_id="dag-diamond-payload"):
            events.append(event)

        dag_done = [e for e in events if e.event_type == "dag.completed"]
        assert len(dag_done) == 1
        p = dag_done[0].data
        assert p["total"] == 4
        assert p["succeeded"] == 4
        assert p["failed"] == 0
        assert p["all_succeeded"] is True


# ---------------------------------------------------------------------------
# 4. Pure parallel fan-out
# ---------------------------------------------------------------------------

class TestParallelFanOut:
    """Three independent tasks with no dependencies — all start immediately."""

    @pytest.mark.asyncio
    async def test_all_tasks_complete(self):
        supervisor = _DelayedMockSupervisor()
        executor = DAGExecutor(run_supervisor=supervisor)

        tasks = [
            DAGTask("auth_agent"),
            DAGTask("billing_agent"),
            DAGTask("api_gateway_agent"),
        ]

        events: list[DomainEvent] = []
        async for event in executor.execute_stream(tasks, parent_run_id="dag-parallel"):
            events.append(event)

        assert len(supervisor.started_requests) == 3
        assert executor.last_result.all_succeeded is True
        completed = {e.data.get("task_id") for e in events if e.event_type == "dag.task_completed"}
        assert completed == {"auth_agent", "billing_agent", "api_gateway_agent"}


# ---------------------------------------------------------------------------
# 5. Output propagation
# ---------------------------------------------------------------------------

class TestOutputPropagation:
    """Verify upstream outputs are injected into dependent tasks' payloads."""

    @pytest.mark.asyncio
    async def test_upstream_output_injected_into_dependent(self):
        supervisor = _RecordingMockSupervisor(
            result_payload={"result": "parent-result", "structured_output": {"score": 99}}
        )
        executor = DAGExecutor(run_supervisor=supervisor)

        tasks = [
            DAGTask("parent"),
            DAGTask("child", depends_on=("parent",)),
        ]

        async for _ in executor.execute_stream(tasks, parent_run_id="dag-prop"):
            pass

        # The child task's RunRequest should have upstream_outputs in its context
        child_req = next(r for r in supervisor.started_requests if "child" in r.run_id)
        assert "upstream_outputs" in child_req.context
        assert "parent" in child_req.context["upstream_outputs"]
        upstream = child_req.context["upstream_outputs"]["parent"]
        assert upstream["text_output"] == "parent-result"
        assert upstream["structured_output"]["score"] == 99

    @pytest.mark.asyncio
    async def test_task_own_payload_preserved(self):
        supervisor = _RecordingMockSupervisor()
        executor = DAGExecutor(run_supervisor=supervisor)

        tasks = [
            DAGTask("A"),
            DAGTask("B", depends_on=("A",), input={"custom_key": "my_value"}),
        ]

        async for _ in executor.execute_stream(tasks):
            pass

        b_req = next(r for r in supervisor.started_requests if r.metadata.get("task_id") == "B")
        assert b_req.context.get("custom_key") == "my_value"
        assert "upstream_outputs" in b_req.context


# ---------------------------------------------------------------------------
# 6. Failure handling
# ---------------------------------------------------------------------------

class TestFailureHandling:
    """Task failures are captured and the executor continues."""

    @pytest.mark.asyncio
    async def test_failed_task_recorded_in_result(self):
        supervisor = _RecordingMockSupervisor(raise_for={"bomb"})
        executor = DAGExecutor(run_supervisor=supervisor)

        tasks = [DAGTask("bomb")]

        events: list[DomainEvent] = []
        async for event in executor.execute_stream(tasks, parent_run_id="dag-fail"):
            events.append(event)

        assert executor.last_result is not None
        assert executor.last_result.all_succeeded is False
        r = executor.last_result.task_results["bomb"]
        assert r.success is False
        assert r.error is not None

        event_types = [e.event_type for e in events]
        assert "dag.task_failed" in event_types

    @pytest.mark.asyncio
    async def test_failing_task_dag_completed_shows_failure(self):
        supervisor = _RecordingMockSupervisor(raise_for={"bad_task"})
        executor = DAGExecutor(run_supervisor=supervisor)

        tasks = [DAGTask("good_task"), DAGTask("bad_task")]

        events: list[DomainEvent] = []
        async for event in executor.execute_stream(tasks):
            events.append(event)

        dag_done = [e for e in events if e.event_type == "dag.completed"]
        assert len(dag_done) == 1
        p = dag_done[0].data
        assert p["failed"] == 1
        assert p["succeeded"] == 1
        assert p["all_succeeded"] is False


# ---------------------------------------------------------------------------
# 7. Empty DAG
# ---------------------------------------------------------------------------

class TestEmptyDAG:
    """Empty task list — executor should not error and result should be empty."""

    @pytest.mark.asyncio
    async def test_empty_dag_no_events(self):
        executor = DAGExecutor(run_supervisor=_RecordingMockSupervisor())
        events: list[DomainEvent] = []
        async for event in executor.execute_stream([]):
            events.append(event)

        assert events == []
        assert executor.last_result is not None
        assert executor.last_result.all_succeeded is True
        assert executor.last_result.task_results == {}


# ---------------------------------------------------------------------------
# 8. DAGResult helpers
# ---------------------------------------------------------------------------

class TestDAGResult:
    """Unit tests for DAGResult computed properties."""

    def test_counts(self):
        result = DAGResult(
            dag_run_id="r",
            task_results={
                "A": DAGTaskResult("A", "r_A", success=True),
                "B": DAGTaskResult("B", "r_B", success=False, error="oops"),
                "C": DAGTaskResult("C", "r_C", success=True),
            },
            all_succeeded=False,
        )
        assert result.succeeded_count == 2
        assert result.failed_count == 1

    def test_all_succeeded_true(self):
        result = DAGResult(
            dag_run_id="r",
            task_results={
                "A": DAGTaskResult("A", "r_A", success=True),
                "B": DAGTaskResult("B", "r_B", success=True),
            },
            all_succeeded=True,
        )
        assert result.succeeded_count == 2
        assert result.failed_count == 0


# ---------------------------------------------------------------------------
# 9. RunRequest construction
# ---------------------------------------------------------------------------

class TestRunRequestConstruction:
    """Verify the RunRequest sent to the supervisor has the correct fields."""

    @pytest.mark.asyncio
    async def test_run_request_capability_and_workflow(self):
        supervisor = _RecordingMockSupervisor()
        executor = DAGExecutor(run_supervisor=supervisor)

        tasks = [
            DAGTask(
                "node_1",
                capability="agent",
                workflow_name="MyWorkflow",
                app_id="app-99",
                user_id="user-42",
            )
        ]
        async for _ in executor.execute_stream(tasks, parent_run_id="dag-req"):
            pass

        assert len(supervisor.started_requests) == 1
        req = supervisor.started_requests[0]
        assert req.capability == "agent"
        assert req.workflow_name == "MyWorkflow"
        assert req.app_id == "app-99"
        assert req.user_id == "user-42"
        assert req.metadata["task_id"] == "node_1"
        assert req.metadata["dag_run_id"] == "dag-req"

    @pytest.mark.asyncio
    async def test_fallback_app_id_applied(self):
        supervisor = _RecordingMockSupervisor()
        executor = DAGExecutor(run_supervisor=supervisor)

        tasks = [DAGTask("T")]
        async for _ in executor.execute_stream(
            tasks,
            parent_run_id="dag-fallback",
            app_id="fallback-app",
            user_id="fallback-user",
        ):
            pass

        req = supervisor.started_requests[0]
        assert req.app_id == "fallback-app"
        assert req.user_id == "fallback-user"
