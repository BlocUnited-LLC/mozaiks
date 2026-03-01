# ==============================================================================
# FILE: orchestration/events.py
# DESCRIPTION: Orchestration-level event emission for the UniversalOrchestrator.
#
# Extends the existing handoff event system with decomposition and merge
# event kinds.  Uses the same unified dispatcher as handoff_events.py.
# ==============================================================================
"""
Orchestration event helpers.

Provides convenience functions to emit structured events for:
- Decomposition lifecycle (started, sub-task spawned, completed)
- Merge lifecycle (started, completed)
- Parent resume after merge

These map to ``runtime.handoff`` events with orchestration-specific
``event_kind`` values, preserving full compatibility with the existing
event pipeline and UI transport.
"""

from __future__ import annotations

import logging
from typing import Any

from mozaiksai.core.events.handoff_events import emit_handoff_event

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Event kind constants
# ---------------------------------------------------------------------------

EVENT_KIND_DECOMPOSITION_STARTED = "orchestration.decomposition_started"
EVENT_KIND_SUBTASK_SPAWNED = "orchestration.subtask_spawned"
EVENT_KIND_DECOMPOSITION_COMPLETED = "orchestration.decomposition_completed"
EVENT_KIND_MERGE_STARTED = "orchestration.merge_started"
EVENT_KIND_MERGE_COMPLETED = "orchestration.merge_completed"
EVENT_KIND_PARENT_RESUMING = "orchestration.parent_resuming"
EVENT_KIND_PARENT_RESUMED = "orchestration.parent_resumed"


# ---------------------------------------------------------------------------
# Emission helpers
# ---------------------------------------------------------------------------

def emit_decomposition_started(
    *,
    run_id: str,
    workflow_name: str,
    task_count: int,
    execution_mode: str,
    reason: str = "",
    sub_tasks: list[dict[str, Any]] | None = None,
) -> None:
    """Emit when the orchestrator begins decomposing a run."""
    emit_handoff_event(EVENT_KIND_DECOMPOSITION_STARTED, {
        "run_id": run_id,
        "workflow_name": workflow_name,
        "task_count": task_count,
        "execution_mode": execution_mode,
        "reason": reason,
        "sub_tasks": sub_tasks or [],
    })


def emit_subtask_spawned(
    *,
    parent_run_id: str,
    task_id: str,
    workflow_name: str,
    child_run_id: str,
) -> None:
    """Emit when a sub-GroupChat is spawned."""
    emit_handoff_event(EVENT_KIND_SUBTASK_SPAWNED, {
        "parent_run_id": parent_run_id,
        "task_id": task_id,
        "workflow_name": workflow_name,
        "child_run_id": child_run_id,
    })


def emit_decomposition_completed(
    *,
    run_id: str,
    workflow_name: str,
    total: int,
    succeeded: int,
    failed: int,
) -> None:
    """Emit when all sub-tasks have finished."""
    emit_handoff_event(EVENT_KIND_DECOMPOSITION_COMPLETED, {
        "run_id": run_id,
        "workflow_name": workflow_name,
        "total": total,
        "succeeded": succeeded,
        "failed": failed,
    })


def emit_merge_completed(
    *,
    run_id: str,
    workflow_name: str,
    all_succeeded: bool,
    summary_preview: str = "",
) -> None:
    """Emit when the merge strategy has produced a result."""
    emit_handoff_event(EVENT_KIND_MERGE_COMPLETED, {
        "run_id": run_id,
        "workflow_name": workflow_name,
        "all_succeeded": all_succeeded,
        "summary_preview": summary_preview[:500],
    })


def emit_parent_resuming(
    *,
    run_id: str,
    workflow_name: str,
    resume_agent: str,
) -> None:
    """Emit when the parent GroupChat is about to be resumed."""
    emit_handoff_event(EVENT_KIND_PARENT_RESUMING, {
        "run_id": run_id,
        "workflow_name": workflow_name,
        "resume_agent": resume_agent,
    })


__all__ = [
    "EVENT_KIND_DECOMPOSITION_COMPLETED",
    "EVENT_KIND_DECOMPOSITION_STARTED",
    "EVENT_KIND_MERGE_COMPLETED",
    "EVENT_KIND_MERGE_STARTED",
    "EVENT_KIND_PARENT_RESUMED",
    "EVENT_KIND_PARENT_RESUMING",
    "EVENT_KIND_SUBTASK_SPAWNED",
    "emit_decomposition_completed",
    "emit_decomposition_started",
    "emit_merge_completed",
    "emit_parent_resuming",
    "emit_subtask_spawned",
]
