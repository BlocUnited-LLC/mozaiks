
# FILE: mozaiksai/core/ports/__init__.py
# DESCRIPTION: Port protocols — engine-agnostic contracts for the runtime layer.
# ==============================================================================

from .orchestration import (
    OrchestrationPort,
    RunRequest,
    ResumeRequest,
    RunResult,
    RunStatus,
    DomainEvent,
)

from .app_backend import (
    AppBackendPort,
    BackendResponse,
    BackendHealth,
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
    # Sandbox
    "SandboxPort",
    "SandboxRunResult",
    "SandboxSessionInfo",
]
