# ==============================================================================
# FILE: core/ports/core_service.py
# DESCRIPTION: CoreServicePort — engine-agnostic contract for cross-substrate
#              communication between mozaiksai (AI runtime) and mozaikscore
#              (application services substrate).
#
# This port defines the verbs that the AI runtime uses to interact with
# application-level services: module execution, notifications, navigation,
# subscriptions, user profiles, and admin operations.
#
# The adapter (CoreServiceClient) implements this via HTTP calls to mozaikscore.
# ==============================================================================
"""CoreServicePort — runtime ↔ application-substrate contract.

The port defines cross-substrate operations:
    execute_module      — invoke a mozaikscore module by name
    get_navigation      — fetch tenant-scoped navigation config
    get_user_profile    — fetch user profile data
    get_subscription    — fetch user subscription status
    create_notification — enqueue a notification for delivery
    admin_list_users    — paginated user listing (admin)
    admin_get_analytics — KPI snapshot (admin)
    health              — substrate health check
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModuleRequest:
    """Request to execute a mozaikscore module."""

    module_name: str
    action: str
    user_id: str
    app_id: str
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ModuleResult:
    """Result of a module execution."""

    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


@dataclass
class NotificationRequest:
    """Request to create a notification via mozaikscore."""

    user_id: str
    title: str
    message: str
    category: str = "system"
    channels: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class SubstrateHealth:
    """Health status of the mozaikscore substrate."""

    healthy: bool
    version: str = "unknown"
    modules_loaded: int = 0
    details: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Port protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class CoreServicePort(Protocol):
    """Engine-agnostic contract for application-substrate operations.

    Implementors:
        CoreServiceClient — HTTP adapter calling mozaikscore REST API

    Consumers:
        core_bridge tools — AG2 tool functions that agents can invoke
        transport layer   — admin API proxying
    """

    async def execute_module(self, request: ModuleRequest) -> ModuleResult:
        """Execute a mozaikscore module and return the result."""
        ...

    async def get_navigation(self, user_id: str, token: str) -> Dict[str, Any]:
        """Fetch navigation config for a user."""
        ...

    async def get_user_profile(self, user_id: str, token: str) -> Dict[str, Any]:
        """Fetch user profile data."""
        ...

    async def get_subscription(self, user_id: str, token: str) -> Dict[str, Any]:
        """Fetch user subscription status."""
        ...

    async def create_notification(self, request: NotificationRequest) -> bool:
        """Enqueue a notification for delivery. Returns True on success."""
        ...

    async def admin_list_users(
        self, token: str, page: int = 1, per_page: int = 20
    ) -> Dict[str, Any]:
        """Paginated user listing (admin)."""
        ...

    async def admin_get_analytics(self, token: str) -> Dict[str, Any]:
        """Fetch KPI analytics snapshot (admin)."""
        ...

    async def health(self) -> SubstrateHealth:
        """Check mozaikscore health."""
        ...

    async def relay_event(self, event_type: str, data: Dict[str, Any]) -> bool:
        """Relay an event to mozaikscore's internal event bus."""
        ...


__all__ = [
    "CoreServicePort",
    "ModuleRequest",
    "ModuleResult",
    "NotificationRequest",
    "SubstrateHealth",
]
