# ==============================================================================
# FILE: mozaiksai/core/transport/ui_events.py
# DESCRIPTION: Typed event contract for the ui.* WebSocket event namespace.
#
# Defines canonical shapes for UI component rendering events sent from the
# Python runtime to the frontend over WebSocket.
#
# Outbound event types (Python → frontend):
#   ui.render  — render a named UI component (inline or artifact)
#   ui.update  — patch an already-rendered component's payload in place
#   ui.dismiss — tear down a rendered component
#
# Inbound event types (frontend → backend) are unchanged:
#   tool_call_response — user interaction response correlated by tool_call_id
# ==============================================================================
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class UIDisplayMode(str, Enum):
    """Canonical display mode for a rendered UI component."""

    INLINE = "inline"
    ARTIFACT = "artifact"


class UIRenderData(BaseModel):
    """Data payload for ``ui.render`` events.

    Emitted by the backend to request that the frontend render a named UI
    component inside the chat stream (``inline``) or in the artifact panel
    (``artifact``).

    ``tool_call_id`` is the correlation key used to match the component to any
    subsequent ``tool_call_response`` sent back by the user.
    """

    tool_call_id: str
    component: str
    display_mode: UIDisplayMode
    awaiting_response: bool = True
    workflow: str | None = None
    agent: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    interaction_type: str = "ui_tool"

    model_config = {"populate_by_name": True}


class UIUpdateData(BaseModel):
    """Data payload for ``ui.update`` events.

    Emitted to patch the payload of an already-rendered component without
    re-mounting it. The ``patch`` dict is shallow-merged into the component's
    current payload on the frontend.
    """

    tool_call_id: str
    patch: dict[str, Any] = Field(default_factory=dict)


class UIDismissData(BaseModel):
    """Data payload for ``ui.dismiss`` events.

    Emitted to signal that a rendered UI component should be removed from the
    chat stream or artifact panel.
    """

    tool_call_id: str


__all__ = [
    "UIDisplayMode",
    "UIRenderData",
    "UIUpdateData",
    "UIDismissData",
]
