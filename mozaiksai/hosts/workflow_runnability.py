"""Which workflow names a host will actually run.

Shared by the platform host and the sessions router, which each carried their
own copy of this rule.
"""

from __future__ import annotations

from mozaiksai.core.runtime.composition.platform_hooks import get_platform_hooks

__all__ = [
    "NON_RUNNABLE_WORKFLOW_IDS",
    "get_ordered_workflow_names",
    "is_runnable_workflow_name",
]

NON_RUNNABLE_WORKFLOW_IDS: frozenset[str] = frozenset({"extended_orchestration"})


def get_ordered_workflow_names() -> list[str]:
    from mozaiksai.core.workflow.workflow_manager import workflow_manager

    return get_platform_hooks().call_workflow_ordering(
        sorted(workflow_manager.get_all_workflow_names())
    )


def is_runnable_workflow_name(
    workflow_name: str | None, ordered_names: list[str] | None = None
) -> bool:
    name = str(workflow_name or "").strip()
    if not name:
        return False
    if name in NON_RUNNABLE_WORKFLOW_IDS:
        return False
    names = ordered_names if ordered_names is not None else get_ordered_workflow_names()
    return any(name.lower() == loaded.lower() for loaded in names)
