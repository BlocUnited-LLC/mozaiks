"""Workflow pack graph helpers."""

from __future__ import annotations

from pathlib import Path

from ..task_batches import workflow_has_task_batches


def workflow_declares_task_batches(
    workflow_name: str,
    workflows_root: Path | None = None,
) -> bool:
    """Return true when a workflow declares AG2 task batch execution."""

    return workflow_has_task_batches(workflow_name, workflows_root)


__all__ = ["workflow_declares_task_batches"]
