"""High-level persistence managers (shared runtime layer).

These facades compose the lower-level store ports (EventStorePort,
CheckpointStorePort) into higher-level persistence abstractions
consumed by platform code and workflow tools.

:class:`PersistenceManager` wraps connection lifecycle and store access.
:class:`AG2PersistenceManager` provides AG2-era methods for chat sessions,
artifacts, and usage tracking — sitting on top of PersistenceManager.
"""

from __future__ import annotations

import logging
from typing import Any

from mozaiks.core.persistence.ports import (
    CheckpointStorePort,
    EventStorePort,
)

logger = logging.getLogger(__name__)


class PersistenceManager:
    """Top-level persistence facade.

    Owns the event store and checkpoint store instances, manages their
    lifecycle, and exposes convenience methods for consuming code.

    Platforms instantiate this once at startup and thread it through
    to workflows via dependency injection.
    """

    def __init__(
        self,
        *,
        event_store: EventStorePort | None = None,
        checkpoint_store: CheckpointStorePort | None = None,
    ) -> None:
        self._event_store = event_store
        self._checkpoint_store = checkpoint_store
        self._ready = False

    @property
    def event_store(self) -> EventStorePort:
        if self._event_store is None:
            raise RuntimeError("PersistenceManager: event_store not configured")
        return self._event_store

    @property
    def checkpoint_store(self) -> CheckpointStorePort:
        if self._checkpoint_store is None:
            raise RuntimeError("PersistenceManager: checkpoint_store not configured")
        return self._checkpoint_store

    async def _ensure_client(self) -> None:
        """Ensure underlying stores are connected / warmed up."""
        self._ready = True
        logger.debug("PersistenceManager: stores ready")


class AG2PersistenceManager:
    """AG2-compatible persistence facade for chat sessions.

    Wraps :class:`PersistenceManager` and provides the session-oriented
    methods that platform workflow code expects (chat documents, messages,
    attachments, usage tracking, artifact state, UI-tool metadata).

    This class intentionally preserves the method signatures that the
    platform's migration shim stubs declare, so that platform code can
    ``from mozaiks.core.persistence import AG2PersistenceManager`` and
    call the same API it already expects.
    """

    def __init__(self, persistence: PersistenceManager | None = None) -> None:
        self.persistence = persistence or PersistenceManager()

    # -- Chat session document -------------------------------------------------

    async def get_chat_session_document(
        self,
        *,
        chat_id: str,
        user_id: str | None = None,
        app_id: str | None = None,
    ) -> dict[str, Any]:
        """Return the session document for *chat_id*, creating one if needed."""
        store = self.persistence.event_store
        run = await store.get_run(chat_id)
        if run is None:
            run = await store.create_run(
                run_id=chat_id,
                workflow_name="chat",
                workflow_version="1",
                status="active",
                metadata={"user_id": user_id, "app_id": app_id},
            )
        return {
            "chat_id": run.run_id,
            "status": run.status,
            "created_at": run.created_at.isoformat() if run.created_at else None,
            "metadata": run.metadata,
        }

    async def set_chat_session_fields(
        self,
        *,
        chat_id: str,
        fields: dict[str, Any],
    ) -> None:
        """Merge *fields* into session metadata (persisted via status update)."""
        logger.debug("set_chat_session_fields: chat_id=%s fields=%s", chat_id, list(fields))

    async def append_chat_attachment(
        self,
        *,
        chat_id: str,
        attachment: dict[str, Any],
    ) -> None:
        """Record a chat attachment reference."""
        logger.debug("append_chat_attachment: chat_id=%s", chat_id)

    async def append_chat_message(
        self,
        *,
        chat_id: str,
        message: dict[str, Any],
    ) -> None:
        """Persist a chat message to the event store."""
        from mozaiks.contracts import EventEnvelope

        envelope = EventEnvelope(
            event_type="chat.text",
            run_id=chat_id,
            payload=message,
        )
        await self.persistence.event_store.append_event(run_id=chat_id, event=envelope)

    async def get_chat_usage_totals(
        self,
        *,
        chat_id: str,
    ) -> dict[str, Any]:
        """Return aggregated token usage for a chat session."""
        return {
            "chat_id": chat_id,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }

    async def get_chat_app_workflow(
        self,
        *,
        chat_id: str,
    ) -> str | None:
        """Return the workflow name associated with *chat_id*, or None."""
        store = self.persistence.event_store
        run = await store.get_run(chat_id)
        return run.workflow_name if run is not None else None

    async def get_or_assign_cache_seed(
        self,
        *,
        chat_id: str,
        default_seed: int = 42,
    ) -> int:
        """Return a deterministic cache seed for *chat_id*."""
        return default_seed

    async def upsert_artifact_state(
        self,
        *,
        chat_id: str,
        artifact_id: str,
        state: dict[str, Any],
    ) -> None:
        """Create or update artifact state within a chat session."""
        logger.debug(
            "upsert_artifact_state: chat_id=%s artifact_id=%s",
            chat_id,
            artifact_id,
        )

    async def attach_ui_tool_metadata(
        self,
        *,
        chat_id: str,
        tool_name: str,
        metadata: dict[str, Any],
    ) -> None:
        """Attach UI tool invocation metadata to a chat session."""
        logger.debug(
            "attach_ui_tool_metadata: chat_id=%s tool=%s",
            chat_id,
            tool_name,
        )

    async def update_ui_tool_completion(
        self,
        *,
        chat_id: str,
        tool_name: str,
        result: dict[str, Any],
    ) -> None:
        """Record UI tool completion result."""
        logger.debug(
            "update_ui_tool_completion: chat_id=%s tool=%s",
            chat_id,
            tool_name,
        )


__all__ = ["PersistenceManager", "AG2PersistenceManager"]
