# ==============================================================================
# FILE: mozaiksai/core/ports/collaboration.py
# DESCRIPTION: CollaborationPort — engine-agnostic contract for workspace
#              sharing events and presence tracking hooks.
#
#              This port gives the platform a consistent hook surface for
#              collaboration features without coupling the runtime to any
#              specific hosted product's real-time backend.
#
#              Default implementation: NoOpCollaborationAdapter — all events
#              are silently dropped and presence always returns empty.  OSS
#              apps and local dev remain completely unaffected.
#
#              Hosted products wire a concrete adapter at startup that pushes
#              events to their real-time infrastructure (e.g. WebSockets,
#              pub/sub, presence store).
#
# Hook points called by the runtime:
#   CollaborationPort.on_workspace_shared   — when a workspace is shared
#   CollaborationPort.on_user_active        — when a user session goes active
#   CollaborationPort.on_user_inactive      — when a user session goes inactive
#   CollaborationPort.get_presence          — read current workspace presence
# ==============================================================================
"""CollaborationPort — OSS hook contract for workspace sharing and presence."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class WorkspaceShareEvent:
    """Emitted when a workspace is shared with one or more principals.

    Fields:
        workspace_id:   Canonical workspace identifier.
        shared_by:      user_id of the principal initiating the share.
        shared_with:    List of user_ids receiving access.
        permissions:    Permission names granted (e.g. ``["read", "comment"]``).
        app_id:         App context for multi-tenant deployments.
        shared_at:      ISO-8601 UTC timestamp of the share event.
    """

    workspace_id: str
    shared_by: str
    shared_with: list[str]
    permissions: list[str] = field(default_factory=list)
    app_id: str | None = None
    shared_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass
class PresenceEntry:
    """A single user presence record within a workspace.

    Fields:
        user_id:        Authenticated user identifier.
        workspace_id:   Workspace being tracked.
        session_id:     WebSocket or session correlation id.
        last_seen_at:   ISO-8601 UTC timestamp of last activity.
        is_active:      True when the session is currently open.
        app_id:         App context for multi-tenant deployments.
    """

    user_id: str
    workspace_id: str
    session_id: str
    last_seen_at: str
    is_active: bool = True
    app_id: str | None = None


# ---------------------------------------------------------------------------
# Port protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class CollaborationPort(Protocol):
    """Engine-agnostic contract for workspace collaboration events and presence.

    Implementors:
        NoOpCollaborationAdapter    — drops all events (default; OSS / local dev)
        [hosted product adapter]    — pushes events to real-time infrastructure

    Consumers:
        Platform host               — calls on_user_active / on_user_inactive
                                      around WebSocket lifecycle
        Workspace share service     — calls on_workspace_shared after grant write
        Studio / hosted surface     — calls get_presence for live collaborator UI
    """

    async def on_workspace_shared(self, event: WorkspaceShareEvent) -> None:
        """Notify the collaboration backend that a workspace was shared.

        Called after the share grant has been durably written.  Implementations
        should push a notification to each ``event.shared_with`` recipient.
        Failures should be logged, not raised — the share grant is already
        committed and must not be rolled back.

        Args:
            event: Full share event including workspace, principals, and permissions.
        """
        ...

    async def on_user_active(
        self,
        *,
        workspace_id: str,
        user_id: str,
        session_id: str,
        app_id: str | None = None,
    ) -> None:
        """Record that a user session has become active in a workspace.

        Called when a WebSocket connection is established for an authenticated
        user.  The implementation should upsert a presence record and broadcast
        a ``user_joined`` event to other active collaborators.

        Args:
            workspace_id: Target workspace.
            user_id:      Authenticated user.
            session_id:   WebSocket or transport session id.
            app_id:       App context for multi-tenant deployments.
        """
        ...

    async def on_user_inactive(
        self,
        *,
        workspace_id: str,
        user_id: str,
        session_id: str,
        app_id: str | None = None,
    ) -> None:
        """Record that a user session has gone inactive in a workspace.

        Called when a WebSocket connection closes.  The implementation should
        mark the presence record inactive and broadcast a ``user_left`` event
        to remaining active collaborators.

        Args:
            workspace_id: Target workspace.
            user_id:      Authenticated user.
            session_id:   WebSocket or transport session id.
            app_id:       App context for multi-tenant deployments.
        """
        ...

    async def get_presence(
        self,
        *,
        workspace_id: str,
        app_id: str | None = None,
    ) -> list[PresenceEntry]:
        """Return the current active presence entries for a workspace.

        Called by the Studio surface and hosted APIs to render live collaborator
        indicators.  An empty list is a valid response when no one is active or
        when the adapter is not wired.

        Args:
            workspace_id: Target workspace.
            app_id:       App context for multi-tenant deployments.

        Returns:
            List of :class:`PresenceEntry` for currently active sessions.
        """
        ...


# ---------------------------------------------------------------------------
# Default no-op adapter
# ---------------------------------------------------------------------------


class NoOpCollaborationAdapter:
    """Default collaboration adapter — all events are silently dropped.

    Used when no collaboration adapter is wired (OSS apps, local dev, tests).
    Platform lifecycle calls go through without error; presence always returns
    an empty list.

    Hosted products replace this with a concrete adapter at platform startup.
    """

    async def on_workspace_shared(self, event: WorkspaceShareEvent) -> None:
        pass  # No-op: collaboration not configured

    async def on_user_active(
        self,
        *,
        workspace_id: str,
        user_id: str,
        session_id: str,
        app_id: str | None = None,
    ) -> None:
        pass  # No-op: collaboration not configured

    async def on_user_inactive(
        self,
        *,
        workspace_id: str,
        user_id: str,
        session_id: str,
        app_id: str | None = None,
    ) -> None:
        pass  # No-op: collaboration not configured

    async def get_presence(
        self,
        *,
        workspace_id: str,
        app_id: str | None = None,
    ) -> list[PresenceEntry]:
        return []  # No-op: collaboration not configured


__all__ = [
    "CollaborationPort",
    "NoOpCollaborationAdapter",
    "PresenceEntry",
    "WorkspaceShareEvent",
]
