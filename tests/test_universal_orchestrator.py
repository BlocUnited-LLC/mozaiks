"""
Tests for Phase 4 — UniversalOrchestrator
==========================================

Covers:
1. Data-model construction (SubTask, DecompositionPlan, ChildResult, MergeResult)
2. ConfigDrivenDecomposition strategy detection
3. AgentSignalDecomposition strategy detection
4. ConcatenateMerge and StructuredMerge strategies
5. GroupChatPool with mocked adapter (sequential & parallel)
6. UniversalOrchestrator happy path + decomposition path
7. OrchestrationPort protocol conformance
8. Singleton lifecycle (get / reset)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Imports under test
# ---------------------------------------------------------------------------
from mozaiksai.core.contracts.events import EVENT_SCHEMA_VERSION, DomainEvent
from mozaiksai.core.contracts.runner import ResumeRequest, RunRequest
from mozaiksai.core.ports.orchestration import OrchestrationPort

from mozaiksai.orchestration.decomposition import (
    AgentSignalDecomposition,
    ConfigDrivenDecomposition,
    DecompositionContext,
    DecompositionPlan,
    DecompositionStrategy,
    ExecutionMode,
    SubTask,
)
from mozaiksai.orchestration.merge import (
    ChildResult,
    ConcatenateMerge,
    MergeContext,
    MergeResult,
    MergeStrategy,
    StructuredMerge,
)
from mozaiksai.orchestration.groupchat_pool import GroupChatPool
from mozaiksai.orchestration.universal import (
    OrchestratorRun,
    RunState,
    UniversalOrchestrator,
    get_universal_orchestrator,
    reset_universal_orchestrator,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_domain_event(
    event_type: str = "workflow.run_completed",
    seq: int = 0,
    run_id: str = "test-run",
    payload: dict | None = None,
) -> DomainEvent:
    return DomainEvent(
        event_type=event_type,
        seq=seq,
        occurred_at=_now(),
        run_id=run_id,
        schema_version=EVENT_SCHEMA_VERSION,
        payload=payload or {"result": "ok"},
    )


def _make_run_request(
    run_id: str = "run-001",
    workflow_name: str = "test_workflow",
    **extra: Any,
) -> RunRequest:
    return RunRequest(
        run_id=run_id,
        workflow_name=workflow_name,
        app_id="app-1",
        user_id="user-1",
        chat_id="chat-1",
        payload=extra.pop("payload", {"initial_message": "hello"}),
        metadata=extra.pop("metadata", {}),
        **extra,
    )


def _make_child_result(
    task_id: str = "sub_abc",
    workflow_name: str = "sub_wf",
    run_id: str = "child-run-1",
    text_output: str = "child output",
    success: bool = True,
    error: str | None = None,
    structured_output: dict | None = None,
) -> ChildResult:
    return ChildResult(
        task_id=task_id,
        workflow_name=workflow_name,
        run_id=run_id,
        text_output=text_output,
        success=success,
        error=error,
        structured_output=structured_output or {},
    )


def _make_context(**overrides: Any) -> DecompositionContext:
    defaults = dict(
        run_id="run-001",
        workflow_name="test_workflow",
        app_id="app-1",
        user_id="user-1",
    )
    defaults.update(overrides)
    return DecompositionContext(**defaults)


# ===========================================================================
# 1. Data model construction
# ===========================================================================
class TestDataModels:
    """Verify frozen dataclasses and auto-generated fields."""

    def test_subtask_auto_id(self):
        st = SubTask(workflow_name="wf_a")
        assert st.task_id.startswith("sub_")
        assert len(st.task_id) > 4  # sub_ + hex

    def test_subtask_frozen(self):
        st = SubTask(workflow_name="wf_a", task_id="fixed")
        with pytest.raises(AttributeError):
            st.workflow_name = "changed"  # type: ignore[misc]

    def test_decomposition_plan_task_count(self):
        plan = DecompositionPlan(
            sub_tasks=(
                SubTask(workflow_name="a"),
                SubTask(workflow_name="b"),
                SubTask(workflow_name="c"),
            ),
        )
        assert plan.task_count == 3

    def test_execution_mode_values(self):
        assert ExecutionMode.PARALLEL.value == "parallel"
        assert ExecutionMode.SEQUENTIAL.value == "sequential"

    def test_child_result_frozen(self):
        cr = _make_child_result()
        with pytest.raises(AttributeError):
            cr.success = False  # type: ignore[misc]

    def test_merge_result_counts(self):
        results = (
            _make_child_result(task_id="t1", success=True),
            _make_child_result(task_id="t2", success=False, error="boom"),
            _make_child_result(task_id="t3", success=True),
        )
        mr = MergeResult(
            summary_message="ok",
            child_results=results,
            all_succeeded=False,
        )
        assert mr.succeeded_count == 2
        assert mr.failed_count == 1

    def test_run_state_values(self):
        assert RunState.INITIALIZING.value == "initializing"
        assert RunState.COMPLETED.value == "completed"
        assert RunState.DECOMPOSING.value == "decomposing"

    def test_orchestrator_run_defaults(self):
        r = OrchestratorRun(run_id="r1")
        assert r.state == RunState.INITIALIZING
        assert r.child_run_ids == []
        assert r.decomposition_plan is None
        assert r.created_at.tzinfo is not None


# ===========================================================================
# 2. ConfigDrivenDecomposition
# ===========================================================================
class TestConfigDrivenDecomposition:
    """Test config-driven strategy from workflow YAML and pack graph."""

    def test_no_config_returns_none(self):
        strategy = ConfigDrivenDecomposition()
        ctx = _make_context()
        assert strategy.detect(ctx) is None

    def test_empty_config_returns_none(self):
        strategy = ConfigDrivenDecomposition()
        ctx = _make_context(workflow_config={})
        assert strategy.detect(ctx) is None

    def test_workflow_config_parallel(self):
        strategy = ConfigDrivenDecomposition()
        ctx = _make_context(workflow_config={
            "decomposition": {
                "mode": "parallel",
                "resume_agent": "Reviewer",
                "sub_tasks": [
                    {"workflow": "sub_a", "initial_message": "part A"},
                    {"workflow": "sub_b", "initial_message": "part B"},
                ],
            },
        })
        plan = strategy.detect(ctx)
        assert plan is not None
        assert plan.task_count == 2
        assert plan.execution_mode == ExecutionMode.PARALLEL
        assert plan.resume_agent == "Reviewer"
        assert plan.sub_tasks[0].workflow_name == "sub_a"
        assert plan.sub_tasks[1].initial_message == "part B"

    def test_workflow_config_sequential(self):
        strategy = ConfigDrivenDecomposition()
        ctx = _make_context(workflow_config={
            "decomposition": {
                "mode": "sequential",
                "sub_tasks": [
                    {"workflow": "step1"},
                    {"workflow": "step2", "depends_on": ["step1"]},
                ],
            },
        })
        plan = strategy.detect(ctx)
        assert plan is not None
        assert plan.execution_mode == ExecutionMode.SEQUENTIAL
        assert plan.sub_tasks[1].depends_on == ("step1",)

    def test_pack_graph_journeys(self):
        strategy = ConfigDrivenDecomposition()
        ctx = _make_context(pack_config={
            "journeys": [{
                "trigger_agent": "Planner",
                "children": ["sub_a", "sub_b"],
                "resume_agent": "Merger",
            }],
        })
        plan = strategy.detect(ctx)
        assert plan is not None
        assert plan.task_count == 2
        assert plan.resume_agent == "Merger"
        assert plan.reason == "pack-graph journeys"

    def test_pack_graph_with_dict_children(self):
        strategy = ConfigDrivenDecomposition()
        ctx = _make_context(pack_config={
            "journeys": [{
                "children": [
                    {"name": "wf_x", "initial_message": "do X"},
                    {"name": "wf_y"},
                ],
            }],
        })
        plan = strategy.detect(ctx)
        assert plan is not None
        assert plan.sub_tasks[0].workflow_name == "wf_x"
        assert plan.sub_tasks[0].initial_message == "do X"

    def test_workflow_config_takes_priority_over_pack(self):
        """When both configs have decomposition, workflow YAML wins."""
        strategy = ConfigDrivenDecomposition()
        ctx = _make_context(
            workflow_config={
                "decomposition": {
                    "sub_tasks": [{"workflow": "from_yaml"}],
                },
            },
            pack_config={
                "journeys": [{
                    "children": ["from_pack"],
                }],
            },
        )
        plan = strategy.detect(ctx)
        assert plan is not None
        assert plan.sub_tasks[0].workflow_name == "from_yaml"

    def test_invalid_sub_tasks_returns_none(self):
        strategy = ConfigDrivenDecomposition()
        ctx = _make_context(workflow_config={
            "decomposition": {
                "sub_tasks": "not a list",
            },
        })
        assert strategy.detect(ctx) is None


# ===========================================================================
# 3. AgentSignalDecomposition
# ===========================================================================
class TestAgentSignalDecomposition:
    """Test agent-signal-based decomposition detection."""

    def test_no_event_returns_none(self):
        strategy = AgentSignalDecomposition()
        ctx = _make_context()
        assert strategy.detect(ctx) is None

    def test_non_dict_event_returns_none(self):
        strategy = AgentSignalDecomposition()
        ctx = _make_context(trigger_event="not a dict")
        assert strategy.detect(ctx) is None

    def test_domain_event_decompose_requested(self):
        strategy = AgentSignalDecomposition()
        ctx = _make_context(trigger_event={
            "event_type": "process.decompose_requested",
            "payload": {
                "sub_tasks": [
                    {"workflow": "task_a", "initial_message": "do A"},
                    "task_b",  # string shorthand
                ],
                "mode": "parallel",
                "resume_agent": "SynthAgent",
            },
        })
        plan = strategy.detect(ctx)
        assert plan is not None
        assert plan.task_count == 2
        assert plan.sub_tasks[0].workflow_name == "task_a"
        assert plan.sub_tasks[1].workflow_name == "task_b"
        assert plan.resume_agent == "SynthAgent"
        assert "agent signal" in plan.reason

    def test_pattern_selection_structured_output(self):
        strategy = AgentSignalDecomposition()
        ctx = _make_context(trigger_event={
            "structured_data": {
                "PatternSelection": {
                    "is_multi_workflow": True,
                    "workflows": [
                        {"name": "wf_1", "initial_message": "msg1"},
                        {"name": "wf_2"},
                    ],
                    "resume_agent": "Coordinator",
                    "decomposition_reason": "complex task",
                },
            },
        })
        plan = strategy.detect(ctx)
        assert plan is not None
        assert plan.task_count == 2
        assert plan.reason == "PatternSelection structured output"
        assert plan.strategy_metadata["decomposition_reason"] == "complex task"

    def test_pattern_selection_not_multi_returns_none(self):
        strategy = AgentSignalDecomposition()
        ctx = _make_context(trigger_event={
            "structured_data": {
                "PatternSelection": {
                    "is_multi_workflow": False,
                    "workflows": [{"name": "single"}],
                },
            },
        })
        assert strategy.detect(ctx) is None

    def test_unrelated_event_returns_none(self):
        strategy = AgentSignalDecomposition()
        ctx = _make_context(trigger_event={
            "event_type": "process.completed",
            "payload": {},
        })
        assert strategy.detect(ctx) is None


# ===========================================================================
# 4. Merge strategies
# ===========================================================================
class TestConcatenateMerge:
    """Test the concatenation merge strategy."""

    def test_all_succeeded(self):
        merge = ConcatenateMerge()
        ctx = MergeContext(
            parent_run_id="parent-1",
            parent_workflow_name="main_wf",
            child_results=[
                _make_child_result(task_id="t1", text_output="Result A"),
                _make_child_result(task_id="t2", text_output="Result B"),
            ],
        )
        result = merge.merge(ctx)
        assert result.all_succeeded is True
        assert "2/2" in result.summary_message
        assert "Result A" in result.summary_message
        assert "Result B" in result.summary_message
        assert "✅" in result.summary_message

    def test_partial_failure(self):
        merge = ConcatenateMerge()
        ctx = MergeContext(
            parent_run_id="parent-1",
            parent_workflow_name="main_wf",
            child_results=[
                _make_child_result(task_id="t1", success=True, text_output="ok"),
                _make_child_result(task_id="t2", success=False, error="timeout", text_output=""),
            ],
        )
        result = merge.merge(ctx)
        assert result.all_succeeded is False
        assert "1/2" in result.summary_message
        assert "❌" in result.summary_message
        assert "timeout" in result.summary_message

    def test_structured_output_collected_by_task_id(self):
        merge = ConcatenateMerge()
        ctx = MergeContext(
            parent_run_id="p",
            parent_workflow_name="w",
            child_results=[
                _make_child_result(
                    task_id="t1",
                    structured_output={"key": "value"},
                ),
            ],
        )
        result = merge.merge(ctx)
        assert "t1" in result.merged_data
        assert result.merged_data["t1"]["key"] == "value"


class TestStructuredMerge:
    """Test the structured merge strategy."""

    def test_merges_structured_outputs(self):
        merge = StructuredMerge()
        ctx = MergeContext(
            parent_run_id="p",
            parent_workflow_name="w",
            child_results=[
                _make_child_result(
                    task_id="t1",
                    structured_output={"score": 95},
                ),
                _make_child_result(
                    task_id="t2",
                    structured_output={"score": 87},
                ),
            ],
        )
        result = merge.merge(ctx)
        assert result.all_succeeded is True
        assert result.merged_data["t1"]["data"]["score"] == 95
        assert result.merged_data["t2"]["data"]["score"] == 87
        assert "json" in result.summary_message.lower()

    def test_text_fallback(self):
        merge = StructuredMerge(include_text_fallback=True)
        ctx = MergeContext(
            parent_run_id="p",
            parent_workflow_name="w",
            child_results=[
                _make_child_result(
                    task_id="t1",
                    text_output="plain text output",
                    structured_output={},
                ),
            ],
        )
        result = merge.merge(ctx)
        assert result.merged_data["t1"]["data"]["_text"] == "plain text output"

    def test_no_text_fallback(self):
        merge = StructuredMerge(include_text_fallback=False)
        ctx = MergeContext(
            parent_run_id="p",
            parent_workflow_name="w",
            child_results=[
                _make_child_result(
                    task_id="t1",
                    text_output="plain text",
                    structured_output={},
                ),
            ],
        )
        result = merge.merge(ctx)
        assert "data" not in result.merged_data["t1"]

    def test_failure_recorded(self):
        merge = StructuredMerge()
        ctx = MergeContext(
            parent_run_id="p",
            parent_workflow_name="w",
            child_results=[
                _make_child_result(task_id="t1", success=False, error="kaboom"),
            ],
        )
        result = merge.merge(ctx)
        assert result.all_succeeded is False
        assert result.merged_data["t1"]["error"] == "kaboom"


# ===========================================================================
# 5. Protocol conformance
# ===========================================================================
class TestProtocolConformance:
    """Verify that key classes satisfy their Protocol contracts."""

    def test_decomposition_strategy_protocol(self):
        assert isinstance(ConfigDrivenDecomposition(), DecompositionStrategy)
        assert isinstance(AgentSignalDecomposition(), DecompositionStrategy)

    def test_merge_strategy_protocol(self):
        assert isinstance(ConcatenateMerge(), MergeStrategy)
        assert isinstance(StructuredMerge(), MergeStrategy)

    def test_universal_orchestrator_is_not_structural_subtype(self):
        """UniversalOrchestrator has async generators for run/resume,
        which do NOT structurally match Protocol async iterators at runtime
        via isinstance.  This is a known Python limitation with Protocol
        and async generators.  We verify the methods exist instead."""
        orchestrator = UniversalOrchestrator()
        assert hasattr(orchestrator, "run")
        assert hasattr(orchestrator, "resume")
        assert hasattr(orchestrator, "cancel")
        assert hasattr(orchestrator, "capabilities")


# ===========================================================================
# 6. UniversalOrchestrator — happy path (no decomposition)
# ===========================================================================
class TestUniversalOrchestratorHappyPath:
    """Test the orchestrator with no decomposition detected."""

    @pytest.fixture(autouse=True)
    def _reset(self):
        reset_universal_orchestrator()
        yield
        reset_universal_orchestrator()

    @pytest.mark.asyncio
    async def test_happy_path_delegates_to_adapter(self):
        """When no strategy triggers, the orchestrator delegates to the
        AG2 adapter and yields its events + a run_completed event."""

        completion_event = _make_domain_event(
            event_type="workflow.run_completed",
            run_id="run-001",
            payload={"result": "all good"},
        )

        # Mock adapter that yields a single completion event
        mock_adapter = MagicMock()

        async def _mock_run(request: RunRequest) -> AsyncIterator[DomainEvent]:
            yield completion_event

        mock_adapter.run = _mock_run

        # Use a no-op strategy that never triggers
        class NoOpStrategy:
            def detect(self, ctx):
                return None

        orchestrator = UniversalOrchestrator(
            decomposition_strategies=[NoOpStrategy()],
        )

        request = _make_run_request()
        events: list[DomainEvent] = []

        with patch(
            "mozaiksai.core.ports.ag2_adapter.get_ag2_orchestration_adapter",
            return_value=mock_adapter,
        ):
            async for event in orchestrator.run(request):
                events.append(event)

        # Should have the forwarded adapter event + orchestration.run_completed
        event_types = [e.event_type for e in events]
        assert "workflow.run_completed" in event_types
        assert "orchestration.run_completed" in event_types

    @pytest.mark.asyncio
    async def test_happy_path_run_completed_payload(self):
        """Verify the final run_completed event payload."""
        completion_event = _make_domain_event(
            event_type="workflow.run_completed",
            run_id="run-002",
        )
        mock_adapter = MagicMock()

        async def _mock_run(request: RunRequest) -> AsyncIterator[DomainEvent]:
            yield completion_event

        mock_adapter.run = _mock_run

        class NoOpStrategy:
            def detect(self, ctx):
                return None

        orchestrator = UniversalOrchestrator(
            decomposition_strategies=[NoOpStrategy()],
        )

        request = _make_run_request(run_id="run-002")
        events: list[DomainEvent] = []

        with patch(
            "mozaiksai.core.ports.ag2_adapter.get_ag2_orchestration_adapter",
            return_value=mock_adapter,
        ):
            async for event in orchestrator.run(request):
                events.append(event)

        completed = [e for e in events if e.event_type == "orchestration.run_completed"]
        assert len(completed) == 1
        assert completed[0].payload["decomposed"] is False
        assert completed[0].payload["child_count"] == 0


# ===========================================================================
# 7. UniversalOrchestrator — decomposition path
# ===========================================================================
class TestUniversalOrchestratorDecomposition:
    """Test the orchestrator when decomposition IS triggered."""

    @pytest.fixture(autouse=True)
    def _reset(self):
        reset_universal_orchestrator()
        yield
        reset_universal_orchestrator()

    @pytest.mark.asyncio
    async def test_decomposition_emits_lifecycle_events(self):
        """When a strategy returns a plan, the orchestrator emits decomposition
        events and pool events."""

        plan = DecompositionPlan(
            sub_tasks=(
                SubTask(workflow_name="sub_a", task_id="t1"),
                SubTask(workflow_name="sub_b", task_id="t2"),
            ),
            execution_mode=ExecutionMode.SEQUENTIAL,
            reason="test decomposition",
        )

        class AlwaysDecompose:
            def detect(self, ctx):
                return plan

        # Mock the adapter to yield a completion event per sub-task call
        mock_adapter = MagicMock()
        call_count = 0

        async def _mock_run(request: RunRequest) -> AsyncIterator[DomainEvent]:
            nonlocal call_count
            call_count += 1
            yield _make_domain_event(
                event_type="workflow.run_completed",
                run_id=request.run_id,
                payload={"result": f"sub-result-{call_count}"},
            )

        mock_adapter.run = _mock_run

        orchestrator = UniversalOrchestrator(
            decomposition_strategies=[AlwaysDecompose()],
            auto_resume_parent=False,  # skip resume for simplicity
        )

        # Patch both the adapter in universal.py and groupchat_pool.py
        request = _make_run_request()
        events: list[DomainEvent] = []

        with patch(
            "mozaiksai.core.ports.ag2_adapter.get_ag2_orchestration_adapter",
            return_value=mock_adapter,
        ):
            async for event in orchestrator.run(request):
                events.append(event)

        event_types = [e.event_type for e in events]

        # Must see decomposition lifecycle
        assert "orchestration.decomposition_started" in event_types
        assert "orchestration.pool_started" in event_types
        assert "orchestration.pool_completed" in event_types
        assert "orchestration.merge_completed" in event_types
        assert "orchestration.run_completed" in event_types

        # Must see sub-task lifecycle
        assert "process.started" in event_types
        assert "process.completed" in event_types

    @pytest.mark.asyncio
    async def test_decomposition_run_completed_shows_decomposed(self):
        """The final run_completed payload should indicate decomposition."""
        plan = DecompositionPlan(
            sub_tasks=(SubTask(workflow_name="sub_a", task_id="t1"),),
        )

        class AlwaysDecompose:
            def detect(self, ctx):
                return plan

        mock_adapter = MagicMock()

        async def _mock_run(request: RunRequest) -> AsyncIterator[DomainEvent]:
            yield _make_domain_event(
                event_type="workflow.run_completed",
                run_id=request.run_id,
            )

        mock_adapter.run = _mock_run

        orchestrator = UniversalOrchestrator(
            decomposition_strategies=[AlwaysDecompose()],
            auto_resume_parent=False,
        )

        request = _make_run_request()
        events: list[DomainEvent] = []

        with patch(
            "mozaiksai.core.ports.ag2_adapter.get_ag2_orchestration_adapter",
            return_value=mock_adapter,
        ):
            async for event in orchestrator.run(request):
                events.append(event)

        completed = [e for e in events if e.event_type == "orchestration.run_completed"]
        assert len(completed) == 1
        assert completed[0].payload["decomposed"] is True
        assert completed[0].payload["child_count"] >= 1


# ===========================================================================
# 8. UniversalOrchestrator — error handling
# ===========================================================================
class TestUniversalOrchestratorErrors:
    """Test failure scenarios."""

    @pytest.fixture(autouse=True)
    def _reset(self):
        reset_universal_orchestrator()
        yield
        reset_universal_orchestrator()

    @pytest.mark.asyncio
    async def test_adapter_failure_emits_run_failed(self):
        """If the adapter raises, the orchestrator emits run_failed."""

        mock_adapter = MagicMock()

        async def _mock_run(request: RunRequest) -> AsyncIterator[DomainEvent]:
            raise RuntimeError("AG2 exploded")
            # Make this a generator
            yield  # pragma: no cover

        mock_adapter.run = _mock_run

        class NoOpStrategy:
            def detect(self, ctx):
                return None

        orchestrator = UniversalOrchestrator(
            decomposition_strategies=[NoOpStrategy()],
        )

        request = _make_run_request()
        events: list[DomainEvent] = []

        with patch(
            "mozaiksai.core.ports.ag2_adapter.get_ag2_orchestration_adapter",
            return_value=mock_adapter,
        ):
            async for event in orchestrator.run(request):
                events.append(event)

        event_types = [e.event_type for e in events]
        assert "orchestration.run_failed" in event_types
        failed = [e for e in events if e.event_type == "orchestration.run_failed"]
        assert "AG2 exploded" in failed[0].payload["error"]


# ===========================================================================
# 9. Capabilities
# ===========================================================================
class TestCapabilities:
    """Test capabilities() returns expected shape."""

    def test_capabilities_dict(self):
        orchestrator = UniversalOrchestrator()
        caps = orchestrator.capabilities()
        assert caps["engine"] == "universal_orchestrator"
        assert caps["decomposition"] is True
        assert caps["merge"] is True
        assert caps["cancel"] is True
        assert caps["resume"] is True
        assert "ConfigDrivenDecomposition" in caps["strategies"]
        assert "AgentSignalDecomposition" in caps["strategies"]
        assert caps["merge_strategy"] == "ConcatenateMerge"


# ===========================================================================
# 10. Singleton lifecycle
# ===========================================================================
class TestSingleton:
    """Test get/reset singleton pattern."""

    @pytest.fixture(autouse=True)
    def _reset(self):
        reset_universal_orchestrator()
        yield
        reset_universal_orchestrator()

    def test_get_returns_same_instance(self):
        a = get_universal_orchestrator()
        b = get_universal_orchestrator()
        assert a is b

    def test_reset_clears_instance(self):
        a = get_universal_orchestrator()
        reset_universal_orchestrator()
        b = get_universal_orchestrator()
        assert a is not b


# ===========================================================================
# 11. GroupChatPool (isolated)
# ===========================================================================
class TestGroupChatPool:
    """Test GroupChatPool with mocked adapter."""

    @pytest.mark.asyncio
    async def test_sequential_execution(self):
        """Sequential pool executes sub-tasks one at a time."""
        plan = DecompositionPlan(
            sub_tasks=(
                SubTask(workflow_name="wf_a", task_id="ta"),
                SubTask(workflow_name="wf_b", task_id="tb"),
            ),
            execution_mode=ExecutionMode.SEQUENTIAL,
        )

        mock_adapter = MagicMock()
        call_order: list[str] = []

        async def _mock_run(request: RunRequest) -> AsyncIterator[DomainEvent]:
            call_order.append(request.workflow_name)
            yield _make_domain_event(
                event_type="workflow.run_completed",
                run_id=request.run_id,
                payload={"result": f"done-{request.workflow_name}"},
            )

        mock_adapter.run = _mock_run

        pool = GroupChatPool(
            parent_run_id="parent-1",
            parent_app_id="app-1",
            parent_user_id="user-1",
        )

        events: list[DomainEvent] = []
        with patch(
            "mozaiksai.core.ports.ag2_adapter.get_ag2_orchestration_adapter",
            return_value=mock_adapter,
        ):
            async for event in pool.execute(plan):
                events.append(event)

        # Both sub-tasks should complete
        assert len(pool.results) == 2
        assert pool.results[0].workflow_name == "wf_a"
        assert pool.results[1].workflow_name == "wf_b"

        # Event lifecycle
        event_types = [e.event_type for e in events]
        assert event_types[0] == "orchestration.pool_started"
        assert event_types[-1] == "orchestration.pool_completed"

    @pytest.mark.asyncio
    async def test_parallel_execution(self):
        """Parallel pool fires sub-tasks concurrently."""
        plan = DecompositionPlan(
            sub_tasks=(
                SubTask(workflow_name="wf_x", task_id="tx"),
                SubTask(workflow_name="wf_y", task_id="ty"),
            ),
            execution_mode=ExecutionMode.PARALLEL,
        )

        mock_adapter = MagicMock()

        async def _mock_run(request: RunRequest) -> AsyncIterator[DomainEvent]:
            # Brief delay to simulate async work
            await asyncio.sleep(0.01)
            yield _make_domain_event(
                event_type="workflow.run_completed",
                run_id=request.run_id,
                payload={"result": f"done-{request.workflow_name}"},
            )

        mock_adapter.run = _mock_run

        pool = GroupChatPool(
            parent_run_id="parent-2",
            parent_app_id="app-1",
            parent_user_id="user-1",
        )

        events: list[DomainEvent] = []
        with patch(
            "mozaiksai.core.ports.ag2_adapter.get_ag2_orchestration_adapter",
            return_value=mock_adapter,
        ):
            async for event in pool.execute(plan):
                events.append(event)

        assert len(pool.results) == 2
        event_types = [e.event_type for e in events]
        assert "orchestration.pool_started" in event_types
        assert "orchestration.pool_completed" in event_types

    @pytest.mark.asyncio
    async def test_sub_task_failure_recorded(self):
        """A failing sub-task records error in ChildResult."""
        plan = DecompositionPlan(
            sub_tasks=(SubTask(workflow_name="wf_boom", task_id="t_boom"),),
            execution_mode=ExecutionMode.SEQUENTIAL,
        )

        mock_adapter = MagicMock()

        async def _mock_run(request: RunRequest) -> AsyncIterator[DomainEvent]:
            raise RuntimeError("sub-task crashed")
            yield  # pragma: no cover

        mock_adapter.run = _mock_run

        pool = GroupChatPool(
            parent_run_id="parent-3",
            parent_app_id="app-1",
            parent_user_id="user-1",
        )

        events: list[DomainEvent] = []
        with patch(
            "mozaiksai.core.ports.ag2_adapter.get_ag2_orchestration_adapter",
            return_value=mock_adapter,
        ):
            async for event in pool.execute(plan):
                events.append(event)

        assert len(pool.results) == 1
        assert pool.results[0].success is False
        assert "sub-task crashed" in pool.results[0].error

        # Should see process.failed event
        event_types = [e.event_type for e in events]
        assert "process.failed" in event_types

    @pytest.mark.asyncio
    async def test_pool_completed_payload(self):
        """Verify pool_completed event has correct counts."""
        plan = DecompositionPlan(
            sub_tasks=(
                SubTask(workflow_name="wf_ok", task_id="t_ok"),
                SubTask(workflow_name="wf_fail", task_id="t_fail"),
            ),
            execution_mode=ExecutionMode.SEQUENTIAL,
        )

        mock_adapter = MagicMock()
        call_idx = 0

        async def _mock_run(request: RunRequest) -> AsyncIterator[DomainEvent]:
            nonlocal call_idx
            call_idx += 1
            if call_idx == 2:
                raise RuntimeError("second fails")
            yield _make_domain_event(
                event_type="workflow.run_completed",
                run_id=request.run_id,
            )

        mock_adapter.run = _mock_run

        pool = GroupChatPool(
            parent_run_id="parent-4",
            parent_app_id="app-1",
            parent_user_id="user-1",
        )

        events: list[DomainEvent] = []
        with patch(
            "mozaiksai.core.ports.ag2_adapter.get_ag2_orchestration_adapter",
            return_value=mock_adapter,
        ):
            async for event in pool.execute(plan):
                events.append(event)

        pool_done = [e for e in events if e.event_type == "orchestration.pool_completed"]
        assert len(pool_done) == 1
        assert pool_done[0].payload["total"] == 2
        assert pool_done[0].payload["succeeded"] == 1
        assert pool_done[0].payload["failed"] == 1
        assert pool_done[0].payload["all_succeeded"] is False


# ===========================================================================
# 12. Orchestration events module
# ===========================================================================
class TestOrchestrationEvents:
    """Test event emission helpers."""

    def test_event_kind_constants_are_strings(self):
        from mozaiksai.orchestration.events import (
            EVENT_KIND_DECOMPOSITION_STARTED,
            EVENT_KIND_MERGE_COMPLETED,
            EVENT_KIND_SUBTASK_SPAWNED,
        )
        assert isinstance(EVENT_KIND_DECOMPOSITION_STARTED, str)
        assert isinstance(EVENT_KIND_MERGE_COMPLETED, str)
        assert isinstance(EVENT_KIND_SUBTASK_SPAWNED, str)

    def test_emit_decomposition_started(self):
        from mozaiksai.orchestration.events import emit_decomposition_started

        with patch("mozaiksai.orchestration.events.emit_handoff_event") as mock_emit:
            emit_decomposition_started(
                run_id="r1",
                workflow_name="wf",
                task_count=3,
                execution_mode="parallel",
                reason="test",
            )
            mock_emit.assert_called_once()
            args = mock_emit.call_args
            assert args[0][0] == "orchestration.decomposition_started"
            assert args[0][1]["task_count"] == 3

    def test_emit_merge_completed(self):
        from mozaiksai.orchestration.events import emit_merge_completed

        with patch("mozaiksai.orchestration.events.emit_handoff_event") as mock_emit:
            emit_merge_completed(
                run_id="r1",
                workflow_name="wf",
                all_succeeded=True,
                summary_preview="looks good",
            )
            mock_emit.assert_called_once()
            args = mock_emit.call_args
            assert args[0][0] == "orchestration.merge_completed"
            assert args[0][1]["all_succeeded"] is True


# ===========================================================================
# 13. __init__.py exports
# ===========================================================================
class TestPackageExports:
    """Verify the orchestration package exports all Phase 4 symbols."""

    def test_all_phase4_symbols_importable(self):
        from mozaiksai.orchestration import __all__ as exports
        expected = {
            "create_ai_workflow_runner",
            "DecompositionStrategy",
            "DecompositionPlan",
            "DecompositionContext",
            "SubTask",
            "ExecutionMode",
            "ConfigDrivenDecomposition",
            "AgentSignalDecomposition",
            "MergeStrategy",
            "MergeResult",
            "MergeContext",
            "ChildResult",
            "ConcatenateMerge",
            "StructuredMerge",
            "GroupChatPool",
            "UniversalOrchestrator",
            "OrchestratorRun",
            "RunState",
            "get_universal_orchestrator",
            "reset_universal_orchestrator",
        }
        assert expected.issubset(set(exports)), (
            f"Missing from __all__: {expected - set(exports)}"
        )

    def test_all_symbols_actually_resolve(self):
        """Every name in __all__ should be importable from the package."""
        import mozaiksai.orchestration as pkg
        for name in pkg.__all__:
            assert hasattr(pkg, name), f"'{name}' in __all__ but not importable"
