
# FILE: mozaiksai/core/ports/__init__.py
# DESCRIPTION: Port protocols — engine-agnostic contracts for the runtime layer.
# ==============================================================================

from .app_backend import (
    AppBackendPort,
    BackendHealth,
    BackendResponse,
)
from .collaboration import (
    CollaborationPort,
    NoOpCollaborationAdapter,
    PresenceEntry,
    WorkspaceShareEvent,
)
from .entitlement import (
    ENTITLEMENT_REQUIRED,
    EntitlementPort,
    EntitlementResult,
    NoOpEntitlementAdapter,
)
from .orchestration import (
    DomainEvent,
    OrchestrationPort,
    ResumeRequest,
    RunRequest,
    RunResult,
    RunStatus,
)
from .sandbox import (
    SandboxPort,
    SandboxRunResult,
    SandboxSessionInfo,
)

__all__ = [
    # Orchestration
    "OrchestrationPort",
    "RunRequest",
    "ResumeRequest",
    "RunResult",
    "RunStatus",
    "DomainEvent",
    # App backend
    "AppBackendPort",
    "BackendResponse",
    "BackendHealth",
    # Collaboration
    "CollaborationPort",
    "NoOpCollaborationAdapter",
    "PresenceEntry",
    "WorkspaceShareEvent",
    # Sandbox
    "SandboxPort",
    "SandboxRunResult",
    "SandboxSessionInfo",
    # Entitlement
    "EntitlementPort",
    "EntitlementResult",
    "NoOpEntitlementAdapter",
    "ENTITLEMENT_REQUIRED",
]
