"""
Tests for WorkflowPackCoordinator — kernel bridge
==================================================

Covers:
1. DecompositionPlan integration (AgentSignalDecomposition → SubTask spawning)
2. MergeStrategy integration (ConcatenateMerge / StructuredMerge / _CollectAllMerge)
3. Input/output contract validation
4. Multi-MFJ sequencing (requires field)
5. Timeout enforcement + partial failure strategies
6. Backward compatibility (raw PatternSelection → DecompositionPlan)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Import directly from source files to avoid chain imports through
# mozaiksai.core.__init__ → orchestration_patterns → autogen (which requires
# AG2 0.11.x runtime not available in this test env).
# ---------------------------------------------------------------------------
import importlib.util
import sys
import types
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent  # mozaiks repo root


def _direct_import(module_name: str, file_path: Path):
    """Import a single .py file as module_name, registering parent stubs."""
    parts = module_name.split(".")
    # Ensure parent namespace packages exist
    for i in range(1, len(parts)):
        parent = ".".join(parts[:i])
        if parent not in sys.modules:
            m = types.ModuleType(parent)
            m.__path__ = []
            m.__package__ = parent
            sys.modules[parent] = m
    spec = importlib.util.spec_from_file_location(module_name, str(file_path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


# Pre-register namespace stubs so chained __init__.py's don't fire
for _ns in [
    "mozaiksai",
    "mozaiksai.core",
    "mozaiksai.core.contracts",
    "mozaiksai.core.contracts.events",
    "mozaiksai.core.workflow",
    "mozaiksai.core.workflow.pack",
    "mozaiksai.orchestration",
]:
    if _ns not in sys.modules:
        _m = types.ModuleType(_ns)
        _m.__path__ = [str(_ROOT / _ns.replace(".", "/"))]
        _m.__package__ = _ns
        sys.modules[_ns] = _m

# Now import only the specific modules we need (they don't depend on autogen)
# 1. contracts.events (needed by orchestration modules)
_events_mod = _direct_import(
    "mozaiksai.core.contracts.events",
    _ROOT / "mozaiksai" / "core" / "contracts" / "events.py",
)

# 2. orchestration.decomposition
_decomp_mod = _direct_import(
    "mozaiksai.orchestration.decomposition",
    _ROOT / "mozaiksai" / "orchestration" / "decomposition.py",
)
AgentSignalDecomposition = _decomp_mod.AgentSignalDecomposition
DecompositionContext = _decomp_mod.DecompositionContext
DecompositionPlan = _decomp_mod.DecompositionPlan
ExecutionMode = _decomp_mod.ExecutionMode
SubTask = _decomp_mod.SubTask

# 3. orchestration.merge
_merge_mod = _direct_import(
    "mozaiksai.orchestration.merge",
    _ROOT / "mozaiksai" / "orchestration" / "merge.py",
)
ChildResult = _merge_mod.ChildResult
ConcatenateMerge = _merge_mod.ConcatenateMerge
MergeContext = _merge_mod.MergeContext
MergeResult = _merge_mod.MergeResult
StructuredMerge = _merge_mod.StructuredMerge

# 4. The coordinator itself
_coord_mod = _direct_import(
    "mozaiksai.core.workflow.pack.workflow_pack_coordinator",
    _ROOT / "mozaiksai" / "core" / "workflow" / "pack" / "workflow_pack_coordinator.py",
)
FanInContractError = _coord_mod.FanInContractError
FanOutContractError = _coord_mod.FanOutContractError
MergeMode = _coord_mod.MergeMode
PartialFailureStrategy = _coord_mod.PartialFailureStrategy
WorkflowPackCoordinator = _coord_mod.WorkflowPackCoordinator
_CollectAllMerge = _coord_mod._CollectAllMerge
_MFJCompletionRecord = _coord_mod._MFJCompletionRecord
_validate_child_outputs = _coord_mod._validate_child_outputs
_validate_fan_out_context = _coord_mod._validate_fan_out_context
_ActivePackRun = _coord_mod._ActivePackRun


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


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


# ===========================================================================
# 1. Input/Output Contract Validation
# ===========================================================================

class TestFanOutContractValidation:
    """Validate parent context before fan-out."""

    def test_no_required_context_passes(self):
        """No required_context → always passes."""
        _validate_fan_out_context({}, {"anything": True})
        _validate_fan_out_context({"trigger_agent": "X"}, {})

    def test_empty_required_context_passes(self):
        _validate_fan_out_context({"required_context": []}, {})

    def test_all_required_present_passes(self):
        _validate_fan_out_context(
            {"required_context": ["InterviewTranscript", "PatternSelection"]},
            {"InterviewTranscript": "...", "PatternSelection": {}, "extra": 1},
        )

    def test_missing_required_raises(self):
        with pytest.raises(FanOutContractError, match="InterviewTranscript"):
            _validate_fan_out_context(
                {"required_context": ["InterviewTranscript", "PatternSelection"]},
                {"PatternSelection": {}},
            )

    def test_multiple_missing(self):
        with pytest.raises(FanOutContractError, match="InterviewTranscript"):
            _validate_fan_out_context(
                {"required_context": ["InterviewTranscript", "foo"]},
                {},
            )


class TestFanInContractValidation:
    """Validate child outputs before merge."""

    def test_no_expected_keys_returns_empty(self):
        results = [_make_child_result()]
        assert _validate_child_outputs(results, {}) == []

    def test_expected_keys_present_no_warnings(self):
        results = [
            _make_child_result(structured_output={"report": "ok", "score": 9}),
        ]
        assert _validate_child_outputs(
            results, {"expected_output_keys": ["report", "score"]}
        ) == []

    def test_missing_key_warning(self):
        results = [
            _make_child_result(
                task_id="t1", workflow_name="wf_a",
                structured_output={"report": "ok"},
            ),
        ]
        warnings = _validate_child_outputs(
            results, {"expected_output_keys": ["report", "score"]}
        )
        assert len(warnings) == 1
        assert "score" in warnings[0]
        assert "t1" in warnings[0]

    def test_failed_children_skipped(self):
        results = [
            _make_child_result(success=False, structured_output={}),
        ]
        warnings = _validate_child_outputs(
            results, {"expected_output_keys": ["report"]}
        )
        assert warnings == []


# ===========================================================================
# 2. Merge Strategies
# ===========================================================================

class TestCollectAllMerge:
    """Backward-compatible merge: raw dict dump."""

    def test_basic_collect(self):
        merge = _CollectAllMerge()
        ctx = MergeContext(
            parent_run_id="p1",
            parent_workflow_name="parent_wf",
            child_results=[
                _make_child_result(task_id="t1", structured_output={"a": 1}),
                _make_child_result(task_id="t2", structured_output={"b": 2}),
            ],
        )
        result = merge.merge(ctx)
        assert result.merged_data == {"t1": {"a": 1}, "t2": {"b": 2}}
        assert result.all_succeeded is True
        assert "2/2" in result.summary_message

    def test_failed_child_in_collect(self):
        merge = _CollectAllMerge()
        ctx = MergeContext(
            parent_run_id="p1",
            parent_workflow_name="parent_wf",
            child_results=[
                _make_child_result(task_id="t1", success=True, structured_output={"a": 1}),
                _make_child_result(task_id="t2", success=False, structured_output={}),
            ],
        )
        result = merge.merge(ctx)
        assert result.all_succeeded is False
        assert result.succeeded_count == 1
        assert result.failed_count == 1

    def test_empty_children(self):
        merge = _CollectAllMerge()
        ctx = MergeContext(
            parent_run_id="p1",
            parent_workflow_name="parent_wf",
            child_results=[],
        )
        result = merge.merge(ctx)
        assert result.merged_data == {}
        assert result.all_succeeded is True


class TestMergeStrategyResolution:
    """Test coordinator._resolve_merge_strategy."""

    def test_concatenate(self):
        c = WorkflowPackCoordinator()
        s = c._resolve_merge_strategy("concatenate")
        assert isinstance(s, ConcatenateMerge)

    def test_structured(self):
        c = WorkflowPackCoordinator()
        s = c._resolve_merge_strategy("structured")
        assert isinstance(s, StructuredMerge)

    def test_collect_all(self):
        c = WorkflowPackCoordinator()
        s = c._resolve_merge_strategy("collect_all")
        assert isinstance(s, _CollectAllMerge)

    def test_unknown_defaults_concatenate(self):
        c = WorkflowPackCoordinator()
        s = c._resolve_merge_strategy("unknown_mode")
        assert isinstance(s, ConcatenateMerge)


# ===========================================================================
# 3. Multi-MFJ Sequencing
# ===========================================================================

class TestMFJSequencing:
    """Test requires-based trigger sequencing."""

    @pytest.mark.asyncio
    async def test_no_requires_always_passes(self):
        c = WorkflowPackCoordinator()
        assert await c._check_mfj_requires("parent1", []) is True

    @pytest.mark.asyncio
    async def test_requires_not_met(self):
        c = WorkflowPackCoordinator()
        assert await c._check_mfj_requires("parent1", ["trigger_a"]) is False

    @pytest.mark.asyncio
    async def test_requires_met_after_recording(self):
        c = WorkflowPackCoordinator()
        await c._record_mfj_completion(
            parent_chat_id="parent1",
            trigger_id="trigger_a",
            child_count=2,
            all_succeeded=True,
            merge_summary_preview="ok",
        )
        assert await c._check_mfj_requires("parent1", ["trigger_a"]) is True

    @pytest.mark.asyncio
    async def test_requires_partially_met(self):
        c = WorkflowPackCoordinator()
        await c._record_mfj_completion(
            parent_chat_id="parent1",
            trigger_id="trigger_a",
            child_count=1,
            all_succeeded=True,
            merge_summary_preview="ok",
        )
        # Needs both trigger_a and trigger_b
        assert await c._check_mfj_requires("parent1", ["trigger_a", "trigger_b"]) is False

    @pytest.mark.asyncio
    async def test_multiple_triggers_met(self):
        c = WorkflowPackCoordinator()
        await c._record_mfj_completion(
            parent_chat_id="parent1",
            trigger_id="trigger_a",
            child_count=1,
            all_succeeded=True,
            merge_summary_preview="ok",
        )
        await c._record_mfj_completion(
            parent_chat_id="parent1",
            trigger_id="trigger_b",
            child_count=1,
            all_succeeded=True,
            merge_summary_preview="ok",
        )
        assert await c._check_mfj_requires("parent1", ["trigger_a", "trigger_b"]) is True

    @pytest.mark.asyncio
    async def test_requires_scoped_to_parent(self):
        """Completions for parent1 don't satisfy parent2's requires."""
        c = WorkflowPackCoordinator()
        await c._record_mfj_completion(
            parent_chat_id="parent1",
            trigger_id="trigger_a",
            child_count=1,
            all_succeeded=True,
            merge_summary_preview="ok",
        )
        assert await c._check_mfj_requires("parent2", ["trigger_a"]) is False


class TestMFJCompletionRecord:
    """Test the completion record dataclass."""

    def test_record_creation(self):
        r = _MFJCompletionRecord(
            trigger_id="t1",
            parent_chat_id="p1",
            completed_at=_now(),
            child_count=3,
            all_succeeded=True,
            merge_summary_preview="ok",
        )
        assert r.trigger_id == "t1"
        assert r.child_count == 3

    @pytest.mark.asyncio
    async def test_recording_stores_in_coordinator(self):
        c = WorkflowPackCoordinator()
        await c._record_mfj_completion(
            parent_chat_id="p1",
            trigger_id="t1",
            child_count=2,
            all_succeeded=False,
            merge_summary_preview="partial",
        )
        records = c._completed_mfjs.get("p1", [])
        assert len(records) == 1
        assert records[0].trigger_id == "t1"
        assert records[0].all_succeeded is False


# ===========================================================================
# 4. Partial Failure Strategy Enum
# ===========================================================================

class TestPartialFailureStrategy:
    """Test PartialFailureStrategy enum values."""

    def test_values(self):
        assert PartialFailureStrategy.RESUME_WITH_AVAILABLE.value == "resume_with_available"
        assert PartialFailureStrategy.FAIL_ALL.value == "fail_all"
        assert PartialFailureStrategy.RETRY_FAILED.value == "retry_failed"
        assert PartialFailureStrategy.PROMPT_USER.value == "prompt_user"

    def test_from_string(self):
        assert PartialFailureStrategy("resume_with_available") == PartialFailureStrategy.RESUME_WITH_AVAILABLE

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            PartialFailureStrategy("nonexistent")


class TestMergeMode:
    """Test MergeMode enum."""

    def test_values(self):
        assert MergeMode.CONCATENATE.value == "concatenate"
        assert MergeMode.STRUCTURED.value == "structured"
        assert MergeMode.COLLECT_ALL.value == "collect_all"


# ===========================================================================
# 5. Plan from Raw (backward compat)
# ===========================================================================

class TestPlanFromRaw:
    """Test _plan_from_raw converting old PatternSelection dicts."""

    def test_basic_conversion(self):
        c = WorkflowPackCoordinator()
        raw = {
            "is_multi_workflow": True,
            "workflows": [
                {"name": "wf_a", "description": "A"},
                {"name": "wf_b", "initial_message": "Start B"},
            ],
            "resume_agent": "Merger",
        }
        plan = c._plan_from_raw(raw, {})
        assert plan is not None
        assert plan.task_count == 2
        assert plan.sub_tasks[0].workflow_name == "wf_a"
        assert plan.sub_tasks[1].initial_message == "Start B"
        assert plan.resume_agent == "Merger"
        assert "backward" in plan.reason.lower()

    def test_no_workflows_returns_none(self):
        c = WorkflowPackCoordinator()
        assert c._plan_from_raw({"workflows": []}, {}) is None

    def test_bad_workflow_entries_skipped(self):
        c = WorkflowPackCoordinator()
        raw = {
            "workflows": [
                {"name": ""},
                {"name": "  "},
                42,
                {"name": "valid_wf"},
            ],
        }
        plan = c._plan_from_raw(raw, {})
        assert plan is not None
        assert plan.task_count == 1
        assert plan.sub_tasks[0].workflow_name == "valid_wf"

    def test_metadata_preserved(self):
        c = WorkflowPackCoordinator()
        raw = {
            "workflows": [
                {"name": "wf_a", "description": "A thing", "custom_field": 42},
            ],
            "decomposition_reason": "user requested",
        }
        plan = c._plan_from_raw(raw, {})
        assert plan is not None
        assert plan.sub_tasks[0].metadata.get("description") == "A thing"
        assert plan.sub_tasks[0].metadata.get("custom_field") == 42
        assert plan.strategy_metadata.get("decomposition_reason") == "user requested"


# ===========================================================================
# 6. Coordinator Init & Merge Apply
# ===========================================================================

class TestCoordinatorInit:
    """Test constructor defaults and overrides."""

    def test_defaults(self):
        c = WorkflowPackCoordinator()
        assert isinstance(c._default_merge, ConcatenateMerge)
        assert c._default_timeout is None
        assert c._default_partial_failure == PartialFailureStrategy.RESUME_WITH_AVAILABLE

    def test_custom_merge(self):
        custom_merge = StructuredMerge()
        c = WorkflowPackCoordinator(default_merge_strategy=custom_merge)
        assert c._default_merge is custom_merge

    def test_custom_timeout(self):
        c = WorkflowPackCoordinator(default_timeout_seconds=60.0)
        assert c._default_timeout == 60.0

    def test_custom_partial_failure(self):
        c = WorkflowPackCoordinator(
            default_partial_failure=PartialFailureStrategy.FAIL_ALL
        )
        assert c._default_partial_failure == PartialFailureStrategy.FAIL_ALL


class TestApplyMerge:
    """Test _apply_merge with different strategies."""

    def _make_active(self, plan=None, merge=None):
        return _ActivePackRun(
            parent_chat_id="parent1",
            parent_workflow_name="pwf",
            app_id="app1",
            user_id="user1",
            ws_id=None,
            resume_agent=None,
            child_chat_ids=["c1"],
            decomposition_plan=plan,
            merge_strategy=merge,
        )

    def test_concatenate_merge(self):
        c = WorkflowPackCoordinator()
        active = self._make_active(merge=ConcatenateMerge())
        results = [
            _make_child_result(task_id="t1", workflow_name="wf1", text_output="Hello"),
            _make_child_result(task_id="t2", workflow_name="wf2", text_output="World"),
        ]
        mr = c._apply_merge(active, results, ConcatenateMerge())
        assert "Hello" in mr.summary_message
        assert "World" in mr.summary_message
        assert mr.all_succeeded is True
        assert mr.succeeded_count == 2

    def test_structured_merge(self):
        c = WorkflowPackCoordinator()
        active = self._make_active(merge=StructuredMerge())
        results = [
            _make_child_result(
                task_id="t1", workflow_name="wf1",
                structured_output={"report": "A"},
            ),
        ]
        mr = c._apply_merge(active, results, StructuredMerge())
        assert "t1" in mr.merged_data
        assert mr.merged_data["t1"]["data"] == {"report": "A"}

    def test_fallback_on_merge_error(self):
        """If the merge strategy raises, falls back to ConcatenateMerge."""

        class BrokenMerge:
            def merge(self, ctx):
                raise RuntimeError("boom")

        c = WorkflowPackCoordinator()
        active = self._make_active()
        results = [_make_child_result(task_id="t1", text_output="recovery")]
        mr = c._apply_merge(active, results, BrokenMerge())
        # Should have fallen back to ConcatenateMerge
        assert "recovery" in mr.summary_message


# ===========================================================================
# 7. Extract Pack Plan (backward compat)
# ===========================================================================

class TestExtractPackPlan:
    """Test _extract_pack_plan raw dict extraction."""

    def test_pattern_selection_key(self):
        c = WorkflowPackCoordinator()
        result = c._extract_pack_plan({
            "PatternSelection": {"is_multi_workflow": True, "workflows": [{"name": "a"}]},
        })
        assert result is not None
        assert result["is_multi_workflow"] is True

    def test_lowercase_key(self):
        c = WorkflowPackCoordinator()
        result = c._extract_pack_plan({
            "pattern_selection": {"is_multi_workflow": True, "workflows": []},
        })
        assert result is not None

    def test_not_dict_returns_none(self):
        c = WorkflowPackCoordinator()
        assert c._extract_pack_plan("string") is None
        assert c._extract_pack_plan(None) is None
        assert c._extract_pack_plan(42) is None

    def test_no_matching_key_returns_none(self):
        c = WorkflowPackCoordinator()
        assert c._extract_pack_plan({"other_key": {}}) is None


# ===========================================================================
# 8. DecompositionStrategy detection via coordinator
# ===========================================================================

class TestDecompositionIntegration:
    """Test that AgentSignalDecomposition is used correctly."""

    def test_pattern_selection_detected(self):
        strategy = AgentSignalDecomposition()
        ctx = DecompositionContext(
            run_id="run1",
            workflow_name="wf",
            app_id="app1",
            user_id="user1",
            trigger_event={
                "structured_data": {
                    "PatternSelection": {
                        "is_multi_workflow": True,
                        "workflows": [
                            {"name": "child_a"},
                            {"name": "child_b"},
                        ],
                        "resume_agent": "Merger",
                    }
                }
            },
        )
        plan = strategy.detect(ctx)
        assert plan is not None
        assert plan.task_count == 2
        assert plan.resume_agent == "Merger"

    def test_domain_event_detected(self):
        strategy = AgentSignalDecomposition()
        ctx = DecompositionContext(
            run_id="run1",
            workflow_name="wf",
            app_id="app1",
            user_id="user1",
            trigger_event={
                "event_type": "process.decompose_requested",
                "payload": {
                    "sub_tasks": [
                        {"workflow": "sub_a"},
                        {"workflow": "sub_b"},
                    ],
                    "mode": "sequential",
                    "resume_agent": "Reviewer",
                },
            },
        )
        plan = strategy.detect(ctx)
        assert plan is not None
        assert plan.task_count == 2
        assert plan.execution_mode == ExecutionMode.SEQUENTIAL

    def test_no_trigger_returns_none(self):
        strategy = AgentSignalDecomposition()
        ctx = DecompositionContext(
            run_id="run1",
            workflow_name="wf",
            app_id="app1",
            user_id="user1",
        )
        assert strategy.detect(ctx) is None


# ===========================================================================
# 9. Coordinator __all__ exports
# ===========================================================================

class TestExports:
    """Verify package exports."""

    def test_all_exports(self):
        from mozaiksai.core.workflow.pack import workflow_pack_coordinator as mod
        for name in mod.__all__:
            assert hasattr(mod, name), f"Missing export: {name}"


# ===========================================================================
# 10. Coordinator collect_child_results (integration with mock PM)
# ===========================================================================

class TestCollectChildResults:
    """Test _collect_child_results with mocked persistence manager."""

    @pytest.mark.asyncio
    async def test_collects_from_mongodb(self):
        c = WorkflowPackCoordinator()

        # Mock persistence manager
        pm = AsyncMock()
        pm.fetch_chat_session_extra_context = AsyncMock(
            side_effect=[
                {"report": "A"},  # child1
                {"report": "B"},  # child2
            ]
        )

        active = _ActivePackRun(
            parent_chat_id="parent1",
            parent_workflow_name="pwf",
            app_id="app1",
            user_id="user1",
            ws_id=None,
            resume_agent=None,
            child_chat_ids=["c1", "c2"],
            task_to_chat={"task_1": "c1", "task_2": "c2"},
        )

        # Mock transport + background tasks (both done successfully)
        mock_task = MagicMock()
        mock_task.done.return_value = True
        mock_task.result.return_value = None

        mock_transport = AsyncMock()
        mock_transport._background_tasks = {"c1": mock_task, "c2": mock_task}

        # Patch the lazy import inside _collect_child_results
        fake_transport_module = MagicMock()
        fake_transport_module.SimpleTransport.get_instance = AsyncMock(return_value=mock_transport)

        with patch.dict(sys.modules, {
            "mozaiksai.core.transport.simple_transport": fake_transport_module,
        }):
            results = await c._collect_child_results(active, pm)

        assert len(results) == 2
        assert results[0].task_id == "task_1"
        assert results[0].structured_output == {"report": "A"}
        assert results[0].success is True
        assert results[1].task_id == "task_2"
        assert results[1].structured_output == {"report": "B"}

    @pytest.mark.asyncio
    async def test_failed_child_detected(self):
        c = WorkflowPackCoordinator()

        pm = AsyncMock()
        pm.fetch_chat_session_extra_context = AsyncMock(return_value={})

        active = _ActivePackRun(
            parent_chat_id="parent1",
            parent_workflow_name="pwf",
            app_id="app1",
            user_id="user1",
            ws_id=None,
            resume_agent=None,
            child_chat_ids=["c1"],
            task_to_chat={"task_1": "c1"},
        )

        # Mock task that raised an exception
        mock_task = MagicMock()
        mock_task.done.return_value = True
        mock_task.result.side_effect = RuntimeError("agent crashed")

        mock_transport = AsyncMock()
        mock_transport._background_tasks = {"c1": mock_task}

        fake_transport_module = MagicMock()
        fake_transport_module.SimpleTransport.get_instance = AsyncMock(return_value=mock_transport)

        with patch.dict(sys.modules, {
            "mozaiksai.core.transport.simple_transport": fake_transport_module,
        }):
            results = await c._collect_child_results(active, pm)

        assert len(results) == 1
        assert results[0].success is False
        assert "agent crashed" in results[0].error
