from .model import (
    JourneyAdvanceDecision,
    PendingDecisionAction,
    PendingHarnessDecision,
    RevisionEntry,
    RoutingDecision,
    SequenceStatus,
    SessionLifecycle,
    SessionState,
    TransitionResolution,
    TriggerInput,
    UnmetDependency,
)
from .launcher import (
    PreparedWorkflowLaunch,
    TransitionLaunchResult,
    WorkflowLaunchResult,
    apply_launch_context_provider,
    create_routed_chat_session,
    emit_workflow_launch_navigation,
    launch_prepared_workflow,
    launch_routed_workflow,
    launch_transition,
    prepare_routed_workflow_launch,
    validate_context_for_workflow,
)
from .build_context import (
    BuildContextError,
    merge_build_context,
    resolve_build_context_root,
)


def get_session_router():
    from .router import get_session_router as _get_session_router

    return _get_session_router()


def configure_session_router(*, trigger_route_resolver=None):
    from .router import configure_session_router as _configure_session_router

    return _configure_session_router(trigger_route_resolver=trigger_route_resolver)


def __getattr__(name: str):
    if name == "SessionRouter":
        from .router import SessionRouter

        return SessionRouter
    if name == "SessionStateStore":
        from .persistence import SessionStateStore

        return SessionStateStore
    raise AttributeError(name)

__all__ = [
    "JourneyAdvanceDecision",
    "PendingDecisionAction",
    "PendingHarnessDecision",
    "PreparedWorkflowLaunch",
    "RevisionEntry",
    "RoutingDecision",
    "SequenceStatus",
    "SessionLifecycle",
    "SessionRouter",
    "SessionState",
    "SessionStateStore",
    "TransitionLaunchResult",
    "TransitionResolution",
    "TriggerInput",
    "UnmetDependency",
    "WorkflowLaunchResult",
    "BuildContextError",
    "apply_launch_context_provider",
    "create_routed_chat_session",
    "configure_session_router",
    "emit_workflow_launch_navigation",
    "get_session_router",
    "launch_prepared_workflow",
    "launch_routed_workflow",
    "launch_transition",
    "merge_build_context",
    "prepare_routed_workflow_launch",
    "resolve_build_context_root",
    "validate_context_for_workflow",
]
