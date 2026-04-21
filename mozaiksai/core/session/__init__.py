from .model import (
    JourneyAdvanceDecision,
    RoutingDecision,
    SessionLifecycle,
    SessionState,
    TransitionResolution,
    TriggerInput,
    UnmetDependency,
)


def get_session_router():
    from .router import get_session_router as _get_session_router

    return _get_session_router()


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
    "RoutingDecision",
    "SessionLifecycle",
    "SessionRouter",
    "SessionState",
    "SessionStateStore",
    "TransitionResolution",
    "TriggerInput",
    "UnmetDependency",
    "get_session_router",
]
