# ==============================================================================
# FILE: mozaiksai/core/adapters/circuit_breaker.py
# DESCRIPTION: Simple async circuit breaker for external service calls.
#              Wraps HttpAppBackendAdapter.request() to open on repeated
#              failures and return a degraded response without blocking threads.
#
# States:
#   CLOSED  — normal operation, requests pass through
#   OPEN    — requests fail fast with CircuitOpenError
#   HALF_OPEN — a probe request is allowed through; success → CLOSED
#
# Configuration (env vars):
#   CIRCUIT_BREAKER_FAILURE_THRESHOLD   — consecutive failures to trip (default 5)
#   CIRCUIT_BREAKER_RECOVERY_TIMEOUT    — seconds before half-open probe (default 30)
#   CIRCUIT_BREAKER_SUCCESS_THRESHOLD   — probes needed to close (default 2)
# ==============================================================================
from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any

from logs.logging_config import get_core_logger

logger = get_core_logger("circuit_breaker")


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except (ValueError, AttributeError):
        return default


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """Raised when a call is rejected because the circuit is open."""

    def __init__(self, name: str) -> None:
        super().__init__(f"Circuit '{name}' is OPEN — request rejected to protect downstream")
        self.name = name


class CircuitBreaker:
    """Async circuit breaker for a named downstream service.

    Thread-safe via asyncio.Lock. One instance per downstream service.

    Usage:
        breaker = CircuitBreaker(name="app_backend")
        try:
            result = await breaker.call(adapter.request, "GET", "/health")
        except CircuitOpenError:
            return degraded_response()
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int | None = None,
        recovery_timeout: float | None = None,
        success_threshold: int | None = None,
    ) -> None:
        self.name = name
        self._failure_threshold = failure_threshold or _int_env("CIRCUIT_BREAKER_FAILURE_THRESHOLD", 5)
        self._recovery_timeout = recovery_timeout or float(
            os.getenv("CIRCUIT_BREAKER_RECOVERY_TIMEOUT", "30")
        )
        self._success_threshold = success_threshold or _int_env("CIRCUIT_BREAKER_SUCCESS_THRESHOLD", 2)

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._opened_at: float | None = None
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        return self._state

    @property
    def is_open(self) -> bool:
        return self._state == CircuitState.OPEN

    def _should_attempt_reset(self) -> bool:
        """OPEN → HALF_OPEN after recovery_timeout."""
        if self._opened_at is None:
            return False
        return (time.monotonic() - self._opened_at) >= self._recovery_timeout

    async def _trip(self) -> None:
        """Transition to OPEN state."""
        if self._state != CircuitState.OPEN:
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()
            logger.warning(
                "CIRCUIT_OPEN name=%s failures=%d — fast-failing requests for %.0fs",
                self.name,
                self._failure_count,
                self._recovery_timeout,
            )

    async def _record_success(self) -> None:
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self._success_threshold:
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
                    self._success_count = 0
                    self._opened_at = None
                    logger.info("CIRCUIT_CLOSED name=%s — service recovered", self.name)
            elif self._state == CircuitState.CLOSED:
                self._failure_count = 0

    async def _record_failure(self, exc: Exception) -> None:
        async with self._lock:
            self._failure_count += 1
            self._success_count = 0
            logger.warning(
                "CIRCUIT_FAILURE name=%s count=%d/%d error=%s",
                self.name,
                self._failure_count,
                self._failure_threshold,
                exc,
            )
            if self._state == CircuitState.HALF_OPEN or self._failure_count >= self._failure_threshold:
                await self._trip()

    async def call(self, fn: Callable[..., Awaitable[Any]], *args: Any, **kwargs: Any) -> Any:
        """Execute fn with circuit breaker protection.

        Raises CircuitOpenError if the circuit is OPEN and recovery has not elapsed.
        Re-raises the original exception from fn on failure.
        """
        async with self._lock:
            if self._state == CircuitState.OPEN:
                if self._should_attempt_reset():
                    self._state = CircuitState.HALF_OPEN
                    self._success_count = 0
                    logger.info("CIRCUIT_HALF_OPEN name=%s — probing service", self.name)
                else:
                    raise CircuitOpenError(self.name)

        try:
            result = await fn(*args, **kwargs)
            await self._record_success()
            return result
        except CircuitOpenError:
            raise
        except Exception as exc:
            await self._record_failure(exc)
            raise


# ---------------------------------------------------------------------------
# Per-service registry (one breaker per named downstream)
# ---------------------------------------------------------------------------

_breakers: dict[str, CircuitBreaker] = {}
_registry_lock = asyncio.Lock()


async def get_circuit_breaker(name: str) -> CircuitBreaker:
    """Return or create a named circuit breaker (process-wide singleton)."""
    if name not in _breakers:
        async with _registry_lock:
            if name not in _breakers:
                _breakers[name] = CircuitBreaker(name=name)
    return _breakers[name]


def get_circuit_breaker_sync(name: str) -> CircuitBreaker:
    """Return or create a named circuit breaker — sync variant for startup."""
    if name not in _breakers:
        _breakers[name] = CircuitBreaker(name=name)
    return _breakers[name]


__all__ = [
    "CircuitBreaker",
    "CircuitOpenError",
    "CircuitState",
    "get_circuit_breaker",
    "get_circuit_breaker_sync",
]
