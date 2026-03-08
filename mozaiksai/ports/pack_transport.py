"""Transport port for WorkflowPackCoordinator.

Defines the narrow interface that WPC needs from the transport layer.
SimpleTransport implements this; tests can provide a lightweight stub.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional, Protocol, runtime_checkable

from mozaiksai.contracts import RunRequest


@runtime_checkable
class PackTransportPort(Protocol):
    """Transport capabilities required by WorkflowPackCoordinator.

    This port inverts the dependency: WPC depends on this abstraction
    (in ``ports/``), and the concrete transport layer implements it.
    WPC never imports from ``transport/`` directly.
    """

    async def spawn_run(self, request: RunRequest) -> asyncio.Task:
        """Spawn a workflow run as a background task.

        The implementation MUST dispatch through RunSupervisor so that
        capability-based routing applies.

        Returns the ``asyncio.Task`` handle so the caller can check
        completion status.
        """
        ...

    async def send_ui_event(self, event: Dict[str, Any], chat_id: str) -> None:
        """Send an event envelope to the UI for a given chat_id."""
        ...

    async def pause_workflow(self, chat_id: str, reason: str) -> None:
        """Pause (cancel) a running background workflow task."""
        ...

    def is_task_running(self, chat_id: str) -> bool:
        """Return True if the background task for *chat_id* is still running."""
        ...

    def get_task_error(self, chat_id: str) -> Optional[str]:
        """Return the error message if the task for *chat_id* failed, else None."""
        ...

    def get_persistence(self) -> Any:
        """Return the persistence manager instance."""
        ...

    def get_connection_meta(self, chat_id: str) -> Optional[Dict[str, Any]]:
        """Return connection metadata for *chat_id*.

        Returns a dict with keys: ``app_id``, ``user_id``, ``ws_id``,
        ``websocket``, ``frontend_context``.  Returns ``None`` if no
        connection exists.
        """
        ...

    async def setup_child_connection(
        self,
        *,
        source_chat_id: str,
        target_chat_id: str,
        workflow_name: str,
        app_id: str,
        user_id: str,
    ) -> None:
        """Clone WebSocket routing from *source_chat_id* to *target_chat_id*.

        Enables the child/next-step workflow to send messages over the
        same WebSocket without requiring the client to reconnect.
        """
        ...

    async def flush_pre_connection_buffers(self, chat_id: str) -> None:
        """Flush any events buffered before the connection alias was set up."""
        ...
