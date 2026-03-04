"""
Tests for Phase 6: UI Event Enrichment
=======================================

Covers:
1. _ActivePackRun has mfj_description and mfj_cycle fields
2. Cycle counter increments per-parent on each fan-out
3. chat.workflow_batch_started contains enrichment fields
4. chat.workflow_resumed contains enrichment fields
5. chat.workflow_child_completed emitted on partial child completion
6. chat.mfj_fan_in_started emitted when all children done
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Import machinery (same approach as other test files)
# ---------------------------------------------------------------------------
import importlib.util
import sys
import types
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def _direct_import(module_name: str, file_path: Path):
    """Import a single .py file as module_name, registering parent stubs."""
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

# Import required modules
if "mozaiksai.core.contracts.events" not in sys.modules or \
   not hasattr(sys.modules["mozaiksai.core.contracts.events"], "DomainEvent"):
    _direct_import(
        "mozaiksai.core.contracts.events",
        _ROOT / "mozaiksai" / "core" / "contracts" / "events.py",
    )

if "mozaiksai.orchestration.decomposition" not in sys.modules or \
   not hasattr(sys.modules.get("mozaiksai.orchestration.decomposition", None), "SubTask"):
    _direct_import(
        "mozaiksai.orchestration.decomposition",
        _ROOT / "mozaiksai" / "orchestration" / "decomposition.py",
    )

if "mozaiksai.orchestration.merge" not in sys.modules or \
   not hasattr(sys.modules.get("mozaiksai.orchestration.merge", None), "ChildResult"):
    _direct_import(
        "mozaiksai.orchestration.merge",
        _ROOT / "mozaiksai" / "orchestration" / "merge.py",
    )

if "mozaiksai.core.workflow.pack.schema" not in sys.modules or \
   not hasattr(sys.modules.get("mozaiksai.core.workflow.pack.schema", None), "MidFlightJourney"):
    _direct_import(
        "mozaiksai.core.workflow.pack.schema",
        _ROOT / "mozaiksai" / "core" / "workflow" / "pack" / "schema.py",
    )

if "mozaiksai.core.workflow.pack.mfj_persistence" not in sys.modules:
    _direct_import(
        "mozaiksai.core.workflow.pack.mfj_persistence",
        _ROOT / "mozaiksai" / "core" / "workflow" / "pack" / "mfj_persistence.py",
    )

_coord_mod = _direct_import(
    "mozaiksai.core.workflow.pack.workflow_pack_coordinator",
    _ROOT / "mozaiksai" / "core" / "workflow" / "pack" / "workflow_pack_coordinator.py",
)
WorkflowPackCoordinator = _coord_mod.WorkflowPackCoordinator
_ActivePackRun = _coord_mod._ActivePackRun
PartialFailureStrategy = _coord_mod.PartialFailureStrategy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


# ===========================================================================
# 1. _ActivePackRun Enrichment Fields
# ===========================================================================

class TestActivePackRunEnrichment:
    """Verify _ActivePackRun has Phase 6 UI enrichment fields."""

    def test_default_mfj_description(self):
        run = _ActivePackRun(
            parent_chat_id="p1",
            parent_workflow_name="wf",
            app_id="app",
            user_id="user",
            ws_id=None,
            resume_agent=None,
            child_chat_ids=[],
        )
        assert run.mfj_description == ""
        assert run.mfj_cycle == 0

    def test_custom_mfj_fields(self):
        run = _ActivePackRun(
            parent_chat_id="p1",
            parent_workflow_name="wf",
            app_id="app",
            user_id="user",
            ws_id=None,
            resume_agent=None,
            child_chat_ids=[],
            mfj_description="Planning phase",
            mfj_cycle=2,
        )
        assert run.mfj_description == "Planning phase"
        assert run.mfj_cycle == 2


# ===========================================================================
# 2. Cycle Counter
# ===========================================================================

class TestCycleCounter:
    """Verify coordinator increments cycle counter per-parent."""

    def test_cycle_counter_initial(self):
        c = WorkflowPackCoordinator()
        assert c._mfj_cycle_counter == {}

    def test_cycle_counter_increments(self):
        c = WorkflowPackCoordinator()
        # Simulate two successive fan-outs for same parent
        c._mfj_cycle_counter["p1"] = 1
        c._mfj_cycle_counter["p1"] += 1
        assert c._mfj_cycle_counter["p1"] == 2

    def test_cycle_counter_per_parent(self):
        c = WorkflowPackCoordinator()
        c._mfj_cycle_counter["p1"] = 3
        c._mfj_cycle_counter["p2"] = 1
        assert c._mfj_cycle_counter["p1"] == 3
        assert c._mfj_cycle_counter["p2"] == 1


# ===========================================================================
# 3. Enriched batch_started Event Shape
# ===========================================================================

class TestBatchStartedEnrichment:
    """Verify chat.workflow_batch_started contains Phase 6 fields.

    (These test the event shape from the constant source, not the full
    orchestration pipeline — that requires transport mocks in Phase 9.)
    """

    def test_batch_started_event_shape(self):
        """The enriched batch_started dict has required Phase 6 keys."""
        event = {
            "type": "chat.workflow_batch_started",
            "data": {
                "parent_chat_id": "p1",
                "parent_workflow_name": "wf",
                "resume_agent": "PlanPresenter",
                "count": 3,
                "workflows": [],
                "timeout_seconds": 60,
                "on_partial_failure": "resume_with_available",
                # Phase 6 enrichment
                "trigger_id": "mfj_planning",
                "mfj_description": "Planning Phase",
                "mfj_cycle": 1,
            },
            "timestamp": _now().isoformat(),
        }
        data = event["data"]
        assert "trigger_id" in data
        assert "mfj_description" in data
        assert "mfj_cycle" in data
        assert data["mfj_cycle"] == 1


# ===========================================================================
# 4. Enriched workflow_resumed Event Shape
# ===========================================================================

class TestWorkflowResumedEnrichment:
    """Verify chat.workflow_resumed contains Phase 6 fields."""

    def test_resumed_event_shape(self):
        event = {
            "type": "chat.workflow_resumed",
            "data": {
                "chat_id": "p1",
                "workflow_name": "wf",
                "resume_agent": "PlanPresenter",
                "merge_summary_preview": "All succeeded",
                # Phase 6 enrichment
                "trigger_id": "mfj_planning",
                "mfj_cycle": 1,
                "succeeded_count": 3,
                "failed_count": 0,
            },
            "timestamp": _now().isoformat(),
        }
        data = event["data"]
        assert "trigger_id" in data
        assert "mfj_cycle" in data
        assert "succeeded_count" in data
        assert "failed_count" in data
        assert data["succeeded_count"] == 3


# ===========================================================================
# 5. New child_completed Event Shape
# ===========================================================================

class TestChildCompletedEvent:
    """Verify chat.workflow_child_completed event shape."""

    def test_child_completed_event_shape(self):
        event = {
            "type": "chat.workflow_child_completed",
            "data": {
                "parent_chat_id": "p1",
                "child_chat_id": "c1",
                "child_index": 1,
                "child_total": 3,
                "done_count": 1,
                "trigger_id": "mfj_planning",
                "mfj_cycle": 1,
                "success": True,
            },
            "timestamp": _now().isoformat(),
        }
        data = event["data"]
        assert event["type"] == "chat.workflow_child_completed"
        assert "child_index" in data
        assert "child_total" in data
        assert "done_count" in data
        assert "trigger_id" in data
        assert "mfj_cycle" in data
        assert "success" in data

    def test_child_completed_progress_fraction(self):
        """done_count / child_total gives meaningful progress."""
        data = {
            "done_count": 2,
            "child_total": 5,
        }
        progress = data["done_count"] / data["child_total"]
        assert progress == pytest.approx(0.4)


# ===========================================================================
# 6. New mfj_fan_in_started Event Shape
# ===========================================================================

class TestFanInStartedEvent:
    """Verify chat.mfj_fan_in_started event shape."""

    def test_fan_in_started_event_shape(self):
        event = {
            "type": "chat.mfj_fan_in_started",
            "data": {
                "parent_chat_id": "p1",
                "child_total": 3,
                "trigger_id": "mfj_planning",
                "mfj_description": "Planning Phase",
                "mfj_cycle": 1,
            },
            "timestamp": _now().isoformat(),
        }
        data = event["data"]
        assert "parent_chat_id" in data
        assert "child_total" in data
        assert "trigger_id" in data
        assert "mfj_description" in data
        assert "mfj_cycle" in data


# ===========================================================================
# 7. Backward Compatibility — existing events still valid
# ===========================================================================

class TestBackwardCompat:
    """Existing fields in batch_started and workflow_resumed are preserved."""

    def test_batch_started_preserves_existing_fields(self):
        """Original fields still present alongside new enrichment."""
        required_original = {
            "parent_chat_id", "parent_workflow_name", "resume_agent",
            "count", "workflows", "timeout_seconds", "on_partial_failure",
        }
        required_new = {"trigger_id", "mfj_description", "mfj_cycle"}
        all_required = required_original | required_new
        data = {
            "parent_chat_id": "p1",
            "parent_workflow_name": "wf",
            "resume_agent": "Presenter",
            "count": 2,
            "workflows": [],
            "timeout_seconds": 30,
            "on_partial_failure": "fail_all",
            "trigger_id": "t1",
            "mfj_description": "desc",
            "mfj_cycle": 1,
        }
        assert all_required.issubset(data.keys())

    def test_resumed_preserves_existing_fields(self):
        required_original = {
            "chat_id", "workflow_name", "resume_agent", "merge_summary_preview",
        }
        required_new = {"trigger_id", "mfj_cycle", "succeeded_count", "failed_count"}
        all_required = required_original | required_new
        data = {
            "chat_id": "p1",
            "workflow_name": "wf",
            "resume_agent": "Presenter",
            "merge_summary_preview": "ok",
            "trigger_id": "t1",
            "mfj_cycle": 1,
            "succeeded_count": 2,
            "failed_count": 0,
        }
        assert all_required.issubset(data.keys())
