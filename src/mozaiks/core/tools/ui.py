"""UI-tool helpers for the shared runtime layer.

These functions interact with the :class:`~mozaiks.core.streaming.SimpleTransport`
to send UI-tool events to the frontend and wait for user responses.

They belong in ``core`` because they are transport-level primitives
shared across all workflows.  The *orchestration* layer's ``use_ui_tool``
(registry helper) is a different concern — it registers tool metadata;
this module sends/receives events over the wire.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def use_ui_tool(
    tool_id: str,
    payload: dict[str, Any],
    *,
    chat_id: str | None = None,
    workflow_name: str = "unknown",
    display: str | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Emit a UI-tool event and wait for the user's response.

    This is the high-level convenience function that platform workflow
    tools call when they need to present a UI component to the user
    and collect a response (HITL pattern).

    Parameters
    ----------
    tool_id:
        Identifier for the UI tool / component.
    payload:
        Arbitrary data sent to the frontend component.
    chat_id:
        Target chat/run session.
    workflow_name:
        Name of the calling workflow (for logging / telemetry).
    display:
        Optional display mode hint (e.g. ``"modal"``, ``"inline"``).
    timeout:
        Seconds to wait for the response.  ``None`` = indefinite.

    Returns
    -------
    dict
        The user's response payload.
    """
    from mozaiks.core.streaming.transport import SimpleTransport

    transport = await SimpleTransport.get_instance()

    event_id = await transport.send_ui_tool_event(
        tool_name=tool_id,
        payload={
            **(payload or {}),
            "workflow_name": workflow_name,
            "display": display,
        },
        chat_id=chat_id,
    )

    return await transport.wait_for_ui_tool_response(event_id, timeout=timeout)


async def emit_tool_progress_event(
    tool_name: str,
    progress_percent: float,
    status_message: str,
    chat_id: str | None = None,
    correlation_id: str | None = None,
) -> None:
    """Emit a ``chat.tool_progress`` event to the frontend.

    Used by long-running tools to report incremental progress to the
    UI surface.

    Parameters
    ----------
    tool_name:
        Name of the tool emitting progress.
    progress_percent:
        Progress value in ``[0, 100]``.
    status_message:
        Human-readable status string.
    chat_id:
        Target chat/run session.
    correlation_id:
        Optional correlation ID for event tracing.
    """
    from mozaiks.core.streaming.transport import SimpleTransport

    transport = await SimpleTransport.get_instance()

    event = {
        "type": "chat.tool_progress",
        "tool_name": tool_name,
        "progress_percent": progress_percent,
        "status_message": status_message,
        "correlation_id": correlation_id,
    }
    await transport.send_event_to_ui(event, chat_id=chat_id)


__all__ = ["use_ui_tool", "emit_tool_progress_event"]
