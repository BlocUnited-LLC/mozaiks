"""Runtime execution package facade."""

from .patterns import create_ag2_pattern
from .lifecycle import (
    LifecycleTrigger,
    LifecycleTool,
    LifecycleToolManager,
    get_lifecycle_manager,
)
from .termination import AG2TerminationHandler, TerminationResult, create_termination_handler
from .hooks import RegisteredHook, register_hooks_for_workflow, summarize_hooks

__all__ = [
    "create_ag2_pattern",
    "LifecycleTrigger",
    "LifecycleTool",
    "LifecycleToolManager",
    "get_lifecycle_manager",
    "AG2TerminationHandler",
    "TerminationResult",
    "create_termination_handler",
    "RegisteredHook",
    "register_hooks_for_workflow",
    "summarize_hooks",
]

