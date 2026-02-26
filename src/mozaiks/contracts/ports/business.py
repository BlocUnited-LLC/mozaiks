"""Business event backend port interfaces.

These are pure contracts — no IO, no runtime dependencies.
Dispatchers in ``core.events`` depend on these interfaces;
concrete backends are injected at runtime.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ConsumeResult:
    """Result from a resource consumption check."""

    allowed: bool
    remaining: int = -1
    error: str | None = None


@dataclass(frozen=True)
class UpdateResult:
    """Result from a settings update."""

    success: bool
    errors: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Usage / subscription backend
# ---------------------------------------------------------------------------

class UsageBackend(ABC):
    """Backend for usage tracking and plan management."""

    @abstractmethod
    async def get_usage(self, user_id: str, resource: str) -> int:
        """Return current usage for *resource*."""

    @abstractmethod
    async def increment_usage(self, user_id: str, resource: str, amount: int) -> int:
        """Increment usage for *resource* and return new total."""

    @abstractmethod
    async def get_user_plan(self, user_id: str) -> str:
        """Return the current plan identifier for *user_id*."""


# ---------------------------------------------------------------------------
# Settings backend
# ---------------------------------------------------------------------------

class SettingsBackend(ABC):
    """Backend for per-user settings storage."""

    @abstractmethod
    async def get_settings(self, user_id: str, namespace: str) -> dict[str, Any]:
        """Retrieve all stored settings in *namespace* for *user_id*."""

    @abstractmethod
    async def update_settings(
        self,
        user_id: str,
        namespace: str,
        updates: dict[str, Any],
    ) -> None:
        """Persist *updates* for *user_id* in *namespace*."""


# ---------------------------------------------------------------------------
# Notification backend
# ---------------------------------------------------------------------------

class NotificationBackend(ABC):
    """Backend for multi-channel notification delivery."""

    @abstractmethod
    async def send_in_app(
        self,
        user_id: str,
        title: str,
        message: str,
        priority: str,
        data: dict[str, Any],
    ) -> str:
        """Send an in-app notification.  Return a delivery ID."""

    @abstractmethod
    async def send_email(
        self,
        user_id: str,
        title: str,
        message: str,
        priority: str,
        data: dict[str, Any],
    ) -> str:
        """Send an email notification.  Return a delivery ID."""

    @abstractmethod
    async def send_push(
        self,
        user_id: str,
        title: str,
        message: str,
        priority: str,
        data: dict[str, Any],
    ) -> str:
        """Send a push notification.  Return a delivery ID."""

    @abstractmethod
    async def send_webhook(
        self,
        user_id: str,
        title: str,
        message: str,
        priority: str,
        data: dict[str, Any],
    ) -> str:
        """Send a webhook notification.  Return a delivery ID."""


# ---------------------------------------------------------------------------
# Throttle backend
# ---------------------------------------------------------------------------

class ThrottleBackend(ABC):
    """Backend for notification throttling / dedup."""

    @abstractmethod
    async def is_throttled(self, key: str) -> bool:
        """Return ``True`` if *key* should be suppressed."""

    @abstractmethod
    async def set_throttle(self, key: str, ttl_seconds: int) -> None:
        """Mark *key* as throttled for *ttl_seconds*."""
