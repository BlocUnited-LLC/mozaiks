"""Protocol ports required by runtime context."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, Sequence, runtime_checkable

from mozaiks.contracts import EventEnvelope


@runtime_checkable
class LedgerPort(Protocol):
    """Append/read domain event envelopes."""

    async def append(self, envelope: EventEnvelope) -> str: ...

    async def read(
        self,
        *,
        correlation_id: str | None = None,
        run_id: str | None = None,
        limit: int = 100,
    ) -> Sequence[EventEnvelope]: ...


@runtime_checkable
class ControlPlanePort(Protocol):
    """Optional interface to query/update run control state."""

    async def get_run_state(self, run_id: str) -> dict[str, object] | None: ...

    async def notify_run_state(
        self,
        run_id: str,
        state: str,
        *,
        metadata: dict[str, object] | None = None,
    ) -> None: ...


@runtime_checkable
class ArtifactPort(Protocol):
    """Optional binary artifact persistence port."""

    async def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str | None = None,
    ) -> str: ...

    async def get_bytes(self, key: str) -> bytes | None: ...


@runtime_checkable
class ClockPort(Protocol):
    """Optional clock abstraction for deterministic runtimes."""

    def now(self) -> datetime: ...


@runtime_checkable
class LoggerPort(Protocol):
    """Optional logger adapter for runtime integrations."""

    def debug(self, message: str, **context: object) -> None: ...

    def info(self, message: str, **context: object) -> None: ...

    def warning(self, message: str, **context: object) -> None: ...

    def error(self, message: str, **context: object) -> None: ...


__all__ = [
    "ArtifactPort",
    "ClockPort",
    "ControlPlanePort",
    "LedgerPort",
    "LoggerPort",
]
