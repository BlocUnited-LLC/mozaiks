# === MOZAIKS-CORE-HEADER ===
# FILE: core/ports/__init__.py
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

from .core_service import (
    CoreServicePort,
    ModuleRequest,
    ModuleResult,
    NotificationRequest,
    SubstrateHealth,
)

__all__ = [
    "OrchestrationPort",
    "RunRequest",
    "ResumeRequest",
    "RunResult",
    "RunStatus",
    "DomainEvent",
    "CoreServicePort",
    "ModuleRequest",
    "ModuleResult",
    "NotificationRequest",
    "SubstrateHealth",
]
