"""Runtime context interface composed from kernel ports."""

from __future__ import annotations

from typing import Mapping, Protocol, runtime_checkable

from mozaiksai.core.ports.orchestration import OrchestrationPort
from mozaiksai.core.ports.runtime import (
    ArtifactPort,
    ClockPort,
    ControlPlanePort,
    LedgerPort,
    LoggerPort,
)
from mozaiksai.core.ports.sandbox import SandboxPort
from mozaiksai.core.ports.secrets import SecretsPort
from mozaiksai.core.ports.tool_execution import ToolExecutionPort


@runtime_checkable
class RuntimeContext(Protocol):
    """Kernel-neutral runtime context contract."""

    @property
    def run_id(self) -> str: ...

    @property
    def tenant_id(self) -> str | None: ...

    @property
    def workflow_name(self) -> str: ...

    @property
    def workflow_version(self) -> str: ...

    @property
    def ledger(self) -> LedgerPort: ...

    @property
    def control_plane(self) -> ControlPlanePort | None: ...

    @property
    def artifacts(self) -> ArtifactPort | None: ...

    @property
    def clock(self) -> ClockPort | None: ...

    @property
    def logger(self) -> LoggerPort | None: ...

    @property
    def orchestrator(self) -> OrchestrationPort | None: ...

    @property
    def tool_executor(self) -> ToolExecutionPort | None: ...

    @property
    def sandbox(self) -> SandboxPort | None: ...

    @property
    def secrets(self) -> SecretsPort | None: ...

    @property
    def metadata(self) -> Mapping[str, object]: ...


__all__ = ["RuntimeContext"]
