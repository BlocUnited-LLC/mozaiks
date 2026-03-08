"""Typed connection state for WebSocket sessions.

Replaces the untyped ``Dict[str, Any]`` previously stored in
``SimpleTransport.connections[chat_id]`` with an explicit dataclass so
every read/write site benefits from IDE completion and static analysis.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class GeneralSession:
    """Metadata for a general-mode (non-workflow) chat session."""

    chat_id: str
    label: str
    sequence: int
    app_id: str
    user_id: str
    created_at: str  # ISO-8601


@dataclass
class ConnectionState:
    """Typed state for a single WebSocket chat connection.

    Every field corresponds to a value that was previously accessed via
    ``conn["key"]`` or ``conn.get("key")`` on a plain dict.
    """

    # --- Set at WebSocket accept ---
    websocket: Any = None  # fastapi.WebSocket — typed as Any to avoid import at module level
    user_id: Optional[str] = None
    workflow_name: Optional[str] = None
    app_id: Optional[str] = None
    active: bool = True
    ws_id: Optional[int] = None

    # --- Set by orchestration engine after agent creation ---
    agents: Any = None  # dict of AG2 agent objects; None before first run
    context: Any = None  # AG2 ContextVariables (has .set()/.get())

    # --- Set by inbound WS handlers (switch_workflow / start_workflow) ---
    frontend_context: Optional[Dict[str, Any]] = None

    # --- Set by _ensure_general_chat_context ---
    general_session: Optional[GeneralSession] = None

    # --- Set by ws_routes auto-start guard ---
    autostarted: bool = False
