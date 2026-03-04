"""
Tests for MFJ Observability — Phase 8
======================================

Covers:
1. MFJSpanContext — dataclass defaults
2. MFJObserver — structured logging output (extra fields)
3. MFJObserver — lifecycle callback sequencing
4. MFJObserver — graceful fallback when OTel is absent
5. Observer singleton (get/reset)
6. Coordinator integration — observer calls fired during MFJ lifecycle
"""

from __future__ import annotations

import logging
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Direct import mechanism (same pattern as other test files)
# ---------------------------------------------------------------------------
import importlib.util
import sys
import types
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent  # mozaiks repo root


def _direct_import(module_name: str, file_path: Path):
    """Import a single .py file as *module_name*, registering parent stubs."""
    parts = module_name.split(".")
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


# Pre-register namespace stubs
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

# Import contracts.events (needed by later modules)
_direct_import(
    "mozaiksai.core.contracts.events",
    _ROOT / "mozaiksai" / "core" / "contracts" / "events.py",
)

# Import merge (needed by coordinator)
_direct_import(
    "mozaiksai.orchestration.merge",
    _ROOT / "mozaiksai" / "orchestration" / "merge.py",
)

# Import decomposition (needed by coordinator)
_direct_import(
    "mozaiksai.orchestration.decomposition",
    _ROOT / "mozaiksai" / "orchestration" / "decomposition.py",
)

# Import mfj_persistence (needed by coordinator)
_direct_import(
    "mozaiksai.core.workflow.pack.mfj_persistence",
    _ROOT / "mozaiksai" / "core" / "workflow" / "pack" / "mfj_persistence.py",
)

# Import the observability module under test
_obs_mod = _direct_import(
    "mozaiksai.core.workflow.pack.mfj_observability",
    _ROOT / "mozaiksai" / "core" / "workflow" / "pack" / "mfj_observability.py",
)
MFJObserver = _obs_mod.MFJObserver
MFJSpanContext = _obs_mod.MFJSpanContext
get_mfj_observer = _obs_mod.get_mfj_observer
reset_mfj_observer = _obs_mod.reset_mfj_observer

# Import coordinator
_coord_mod = _direct_import(
    "mozaiksai.core.workflow.pack.workflow_pack_coordinator",
    _ROOT / "mozaiksai" / "core" / "workflow" / "pack" / "workflow_pack_coordinator.py",
)
WorkflowPackCoordinator = _coord_mod.WorkflowPackCoordinator


# ===========================================================================
# 1.  MFJSpanContext defaults
# ===========================================================================

class TestMFJSpanContext:
    """Verify dataclass defaults and field access."""

    def test_defaults(self):
        ctx = MFJSpanContext()
        assert ctx.full_cycle_span is None
        assert ctx.fan_out_span is None
        assert ctx.child_spans == {}
        assert ctx.fan_in_span is None
        assert ctx.start_time_ns == 0
        assert ctx.mfj_trace_id == ""

    def test_child_spans_mutable(self):
        ctx = MFJSpanContext()
        ctx.child_spans["t1"] = "span_t1"
        assert ctx.child_spans["t1"] == "span_t1"


# ===========================================================================
# 2.  MFJObserver — structured logging
# ===========================================================================

class TestObserverStructuredLogging:
    """Verify structured extra fields in log records."""

    def test_fan_out_started_log_fields(self, caplog):
        """on_fan_out_started emits structured log with expected fields."""
        observer = MFJObserver()
        with caplog.at_level(logging.INFO, logger="core.workflow.pack.mfj_observability"):
            ctx = observer.on_fan_out_started(
                trigger_id="trig_1",
                parent_chat_id="chat_abc",
                child_count=3,
                merge_mode="concatenate",
                timeout_seconds=30.0,
                workflow_name="my_pack",
                cycle=1,
            )

        assert ctx.mfj_trace_id.startswith("mfj-")
        assert ctx.start_time_ns > 0

        # Check the log record was emitted.
        assert len(caplog.records) >= 1
        rec = caplog.records[0]
        assert "fan-out started" in rec.message
        assert "trig_1" in rec.message
        assert "chat_abc" in rec.message

    def test_child_completed_log_fields(self, caplog):
        """on_child_completed emits structured log with task_id and success."""
        observer = MFJObserver()
        ctx = MFJSpanContext(mfj_trace_id="mfj-test123")

        with caplog.at_level(logging.INFO, logger="core.workflow.pack.mfj_observability"):
            observer.on_child_completed(
                ctx,
                task_id="t1",
                success=True,
                duration_ms=1234.5,
                trigger_id="trig_1",
                parent_chat_id="chat_abc",
            )

        assert len(caplog.records) >= 1
        assert "t1" in caplog.records[0].message
        assert "True" in caplog.records[0].message

    def test_fan_in_log_fields(self, caplog):
        """on_fan_in_started and completed emit logs."""
        observer = MFJObserver()
        ctx = MFJSpanContext(mfj_trace_id="mfj-test456")

        with caplog.at_level(logging.INFO, logger="core.workflow.pack.mfj_observability"):
            observer.on_fan_in_started(
                ctx, available_count=3, total_count=3,
                trigger_id="trig_1", parent_chat_id="chat_abc",
            )
            observer.on_fan_in_completed(
                ctx, strategy="ConcatenateMerge", succeeded=3, failed=0,
                trigger_id="trig_1", parent_chat_id="chat_abc",
            )

        messages = [r.message for r in caplog.records]
        assert any("fan-in started" in m for m in messages)
        assert any("fan-in completed" in m for m in messages)

    def test_timeout_log(self, caplog):
        """on_timeout emits a warning-level log."""
        observer = MFJObserver()
        ctx = MFJSpanContext(mfj_trace_id="mfj-timeout")

        with caplog.at_level(logging.WARNING, logger="core.workflow.pack.mfj_observability"):
            observer.on_timeout(
                ctx, timeout_seconds=30.0, strategy="resume_with_available",
                trigger_id="trig_1", parent_chat_id="chat_abc",
            )

        assert len(caplog.records) >= 1
        assert "timeout" in caplog.records[0].message.lower()

    def test_cycle_completed_log(self, caplog):
        """on_cycle_completed emits log with duration."""
        observer = MFJObserver()
        ctx = MFJSpanContext(
            mfj_trace_id="mfj-cycle",
            start_time_ns=time.monotonic_ns() - 500_000_000,  # 500ms ago
        )

        with caplog.at_level(logging.INFO, logger="core.workflow.pack.mfj_observability"):
            observer.on_cycle_completed(
                ctx, success=True, trigger_id="trig_1", parent_chat_id="chat_abc",
            )

        assert len(caplog.records) >= 1
        assert "cycle completed" in caplog.records[0].message.lower()
        assert "duration_ms" in caplog.records[0].message

    def test_contract_violation_log(self, caplog):
        """on_contract_violation emits warning with violation details."""
        observer = MFJObserver()
        with caplog.at_level(logging.WARNING, logger="core.workflow.pack.mfj_observability"):
            observer.on_contract_violation(
                trigger_id="trig_1",
                parent_chat_id="chat_abc",
                violation="Missing required_context key: InterviewTranscript",
            )

        assert len(caplog.records) >= 1
        assert "contract violation" in caplog.records[0].message.lower()

    def test_duplicate_suppressed_log(self, caplog):
        """on_duplicate_suppressed emits info log."""
        observer = MFJObserver()
        with caplog.at_level(logging.INFO, logger="core.workflow.pack.mfj_observability"):
            observer.on_duplicate_suppressed(
                trigger_id="trig_1", parent_chat_id="chat_abc",
            )

        assert len(caplog.records) >= 1
        assert "duplicate" in caplog.records[0].message.lower()


# ===========================================================================
# 3.  MFJObserver lifecycle sequencing
# ===========================================================================

class TestObserverLifecycleSequence:
    """Verify full lifecycle can be called without errors."""

    def test_full_happy_path(self):
        """All observer callbacks in correct order — no exceptions."""
        observer = MFJObserver()

        # Fan-out
        ctx = observer.on_fan_out_started(
            trigger_id="trig_1",
            parent_chat_id="chat_abc",
            child_count=2,
            merge_mode="concatenate",
            timeout_seconds=None,
            workflow_name="my_pack",
            cycle=1,
        )
        observer.on_child_spawned(ctx, task_id="t1", workflow_name="child_a")
        observer.on_child_spawned(ctx, task_id="t2", workflow_name="child_b")
        observer.on_fan_out_completed(ctx)

        # Children complete
        observer.on_child_completed(ctx, task_id="t1", success=True, duration_ms=100.0)
        observer.on_child_completed(ctx, task_id="t2", success=True, duration_ms=200.0)

        # Fan-in
        observer.on_fan_in_started(ctx, available_count=2, total_count=2)
        observer.on_fan_in_completed(ctx, strategy="ConcatenateMerge", succeeded=2, failed=0)

        # Cycle done
        observer.on_cycle_completed(ctx, success=True)

    def test_timeout_path(self):
        """Timeout lifecycle path — no exceptions."""
        observer = MFJObserver()

        ctx = observer.on_fan_out_started(
            trigger_id="trig_1",
            parent_chat_id="chat_xyz",
            child_count=3,
            merge_mode="structured",
            timeout_seconds=10.0,
            workflow_name="slow_pack",
            cycle=1,
        )
        observer.on_child_spawned(ctx, task_id="t1", workflow_name="child_a")
        observer.on_fan_out_completed(ctx)

        # Only 1 of 3 children completed before timeout
        observer.on_child_completed(ctx, task_id="t1", success=True)

        # Timeout fires
        observer.on_timeout(ctx, timeout_seconds=10.0, strategy="resume_with_available")

        # Fan-in with partial results
        observer.on_fan_in_started(ctx, available_count=1, total_count=3)
        observer.on_fan_in_completed(ctx, strategy="ConcatenateMerge", succeeded=1, failed=2)
        observer.on_cycle_completed(ctx, success=False)


# ===========================================================================
# 4.  OTel absent — graceful fallback
# ===========================================================================

class TestOtelFallback:
    """Verify observer works when opentelemetry is not installed."""

    def test_no_otel_no_crash(self):
        """Observer does not crash when _HAS_OTEL is False."""
        # Temporarily patch _HAS_OTEL to False.
        with patch.object(_obs_mod, "_HAS_OTEL", False):
            observer = MFJObserver()
            assert observer._tracer is None
            assert observer._metrics_ready is False

            ctx = observer.on_fan_out_started(
                trigger_id="t1",
                parent_chat_id="c1",
                child_count=1,
                merge_mode="concatenate",
                timeout_seconds=None,
                workflow_name="w1",
                cycle=1,
            )
            assert ctx.full_cycle_span is None
            assert ctx.fan_out_span is None

            observer.on_child_spawned(ctx, task_id="t1", workflow_name="w1")
            observer.on_fan_out_completed(ctx)
            observer.on_child_completed(ctx, task_id="t1", success=True)
            observer.on_fan_in_started(ctx, available_count=1, total_count=1)
            observer.on_fan_in_completed(ctx, strategy="ConcatenateMerge", succeeded=1, failed=0)
            observer.on_cycle_completed(ctx, success=True)

    def test_span_helpers_safe_with_none(self):
        """_end_span(None) and _start_span with no tracer return None safely."""
        observer = MFJObserver()
        observer._tracer = None  # force no tracer

        assert observer._start_span("test") is None
        MFJObserver._end_span(None)  # should not raise
        MFJObserver._end_span(None, error="some error")  # should not raise


# ===========================================================================
# 5.  Singleton management
# ===========================================================================

class TestObserverSingleton:
    """Test get/reset singleton lifecycle."""

    def setup_method(self):
        reset_mfj_observer()

    def teardown_method(self):
        reset_mfj_observer()

    def test_get_returns_same_instance(self):
        o1 = get_mfj_observer()
        o2 = get_mfj_observer()
        assert o1 is o2

    def test_reset_creates_new_instance(self):
        o1 = get_mfj_observer()
        reset_mfj_observer()
        o2 = get_mfj_observer()
        assert o1 is not o2

    def test_instance_is_mfj_observer(self):
        o = get_mfj_observer()
        assert isinstance(o, MFJObserver)


# ===========================================================================
# 6.  Coordinator integration — observer is called
# ===========================================================================

class TestCoordinatorObserverIntegration:
    """Verify the coordinator creates an observer and stores observer_ctx."""

    def test_coordinator_has_observer(self):
        """WorkflowPackCoordinator.__init__ sets self._observer."""
        reset_mfj_observer()
        coord = WorkflowPackCoordinator()
        assert isinstance(coord._observer, MFJObserver)

    def test_active_pack_run_has_observer_ctx_field(self):
        """_ActivePackRun dataclass accepts observer_ctx."""
        APR = _coord_mod._ActivePackRun
        run = APR(
            parent_chat_id="c1",
            parent_workflow_name="w1",
            app_id="a1",
            user_id="u1",
            ws_id=None,
            resume_agent=None,
            child_chat_ids=["ch1"],
        )
        # Default should be None
        assert run.observer_ctx is None

        # Can be set
        ctx = MFJSpanContext(mfj_trace_id="mfj-test")
        run.observer_ctx = ctx
        assert run.observer_ctx is ctx
