# ==============================================================================
# FILE: mozaiksai/core/transport/simple_transport.py
# DESCRIPTION: Lean transport system for real-time UI communication
# ==============================================================================
import asyncio
import json
import os
import re
import time
import uuid
from datetime import UTC, datetime
from typing import Any

# AG2 imports for event type checking
from ag2.events import BaseEvent
from fastapi import WebSocket
from pydantic import BaseModel
from starlette.websockets import WebSocketDisconnect, WebSocketState
from websockets.exceptions import ConnectionClosed

# Enhanced logging setup
from logs.logging_config import get_core_logger
from mozaiksai.core.events.runtime_events import RUNTIME_PROCESS_COMPLETED

# Extracted mixins for separation of concerns
from mozaiksai.core.transport.general_mode import GeneralModeMixin

# Message handlers (extracted for maintainability)
from mozaiksai.core.transport.handlers import ERROR_CODES, MESSAGE_HANDLERS
from mozaiksai.core.transport.session_registry import session_registry
from mozaiksai.core.transport.ui_tools import UIToolsMixin
from mozaiksai.core.transport.workflow_bridge import WorkflowBridgeMixin
from mozaiksai.core.transport.ws_protocol import WebSocketProtocolMixin

# Session manager for multi-workflow navigation
from mozaiksai.core.workflow import session_manager
from mozaiksai.core.workflow.runtime_signals import SYSTEM_RESUME_SIGNAL

# Import workflow configuration for agent visibility filtering
from mozaiksai.core.workflow.workflow_manager import workflow_manager

# Get our enhanced loggers
logger = get_core_logger("simple_transport")


async def handle_user_input_api(
    chat_id: str,
    input_request_id: str,
    user_input: str,
) -> bool:
    """Module-level convenience wrapper for SimpleTransport.submit_user_input.

    Resolves the singleton SimpleTransport instance and forwards the call so
    callers do not need to hold a reference to the transport object.
    """
    transport = await SimpleTransport.get_instance()
    return await transport.submit_user_input(input_request_id, user_input)


# Module-level helpers for transport operations
def _utc_timestamp() -> str:
    """Return current UTC timestamp in ISO format for WebSocket messages."""
    return datetime.now(UTC).isoformat()


def _extract_clean_content(message: str | dict[str, Any] | Any) -> str:
    """Extract clean content from AG2 UUID-formatted messages or other formats.

    This is the same logic previously implemented as an instance method; moving it
    to module-level allows other modules to call it without instantiating the
    transport singleton.
    """
    # Handle string messages (most common case)
    if isinstance(message, str):
        # Check for AG2's UUID format and extract only the 'content' part
        match = re.search(r"content='(.*?)'", message, re.DOTALL)
        if match:
            return match.group(1)
        return message  # Return original string if not in UUID format
    elif isinstance(message, dict):
        # Handle dictionary messages
        return message.get('content', str(message))
    else:
        # Handle any other type by converting to string
        return str(message)

# ==================================================================================
# COMMUNICATION CHANNEL WRAPPER & MESSAGE FILTERING
# ==================================================================================


# ==================================================================================
# MAIN TRANSPORT CLASS
# ==================================================================================

class SimpleTransport(WebSocketProtocolMixin, WorkflowBridgeMixin, GeneralModeMixin, UIToolsMixin):
    """
    Lean transport system focused solely on real-time UI communication.

    Features:
    - Message filtering (removes non-user-facing AG2 event payload noise)
    - WebSocket connection management
    - Event forwarding to the UI
    - Thread-safe singleton pattern

    Mixins provide:
    - WebSocketProtocolMixin: heartbeat, backpressure, message queuing
    - WorkflowBridgeMixin: workflow execution integration
    - GeneralModeMixin: general-mode chat routing and persistence
    - UIToolsMixin: interactive UI component handling
    """
    
    _instance = None
    _lock = asyncio.Lock()
    
    @classmethod
    async def get_instance(cls, *args, **kwargs):
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:
                    # Call __new__ and __init__ inside the lock
                    instance = super().__new__(cls)
                    instance.__init__(*args, **kwargs)
                    cls._instance = instance
        return cls._instance

    def __init__(self):
        """Singleton initializer (idempotent)."""
        if getattr(self, '_initialized', False):
            return

        # Core structures
        self.connections: dict[str, dict[str, Any]] = {}

        # AG2-aligned input request callback registry
        self._input_request_registries: dict[str, dict[str, Any]] = {}
        self._recent_input_submit_chats: set[str] = set()

    # T-series: WebSocket protocol support structures
        self._sequence_counters: dict[str, int] = {}          # T3

        # H1-H2: Hardening features
        self._message_queues: dict[str, list[dict[str, Any]]] = {}  # H1
        self._heartbeat_tasks: dict[str, asyncio.Task] = {}         # H2
        self._max_queue_size = 100
        self._heartbeat_interval = 120
        # WebSocket inbound message size limit (bytes). Mirrors the HTTP endpoint guard.
        # Set CHAT_MESSAGE_MAX_CHARS=0 to disable.
        try:
            _ws_max = int(os.environ.get("CHAT_MESSAGE_MAX_CHARS", "8000"))
        except Exception:
            _ws_max = 8000
        self._ws_message_max_chars = _ws_max or 0  # 0 means disabled

        # H4: Pre-connection buffering (delivery reliability)
        self._pre_connection_buffers: dict[str, list[dict[str, Any]]] = {}
        self._max_pre_connection_buffer = 200
        self._scheduled_flush_tasks: dict[str, asyncio.Task] = {}
        self._pre_connection_buffer_overflow_counts: dict[str, int] = {}

        # UI tool response correlation
        self.pending_tool_call_responses: dict[str, asyncio.Future] = {}
        self._buffered_tool_call_responses: dict[str, dict[str, Any]] = {}
        self._ui_tool_metadata: dict[str, dict[str, Any]] = {}
        self._resolved_tool_call_ids: dict[str, None] = {}
        self._owner_loop = None

        # Runtime context trigger managers (per chat)
        # Used to apply declarative ui_response triggers without bespoke agents.
        self._derived_context_managers: dict[str, Any] = {}
        self._live_ag2_workflow_runs: dict[str, Any] = {}

        # Background workflow execution for workflow sequence runs.
        self._background_tasks: dict[str, asyncio.Task] = {}
        try:
            max_parallel = int(os.environ.get("MOZAIKS_MAX_PARALLEL_WORKFLOWS", "4"))
        except Exception:
            max_parallel = 4
        self._workflow_spawn_semaphore = asyncio.Semaphore(max(1, max_parallel))

        try:
            self._max_connections = int(os.environ.get("MOZAIKS_MAX_WS_CONNECTIONS", "500"))
        except Exception:
            self._max_connections = 500

        # Per-user concurrent connection limit. Prevents a single user from
        # exhausting the global connection pool by opening many chat sessions.
        # Set to 0 to disable per-user enforcement.
        try:
            self._max_connections_per_user = int(
                os.environ.get("MOZAIKS_MAX_WS_CONNECTIONS_PER_USER", "20")
            )
        except Exception:
            self._max_connections_per_user = 20

        # Idle connection timeout: how long a client can be silent before the
        # heartbeat considers it dead. Set to 0 to disable idle detection.
        # Clients should send pong (any message) within heartbeat_interval seconds.
        try:
            self._heartbeat_idle_timeout = int(os.environ.get("MOZAIKS_WS_IDLE_TIMEOUT", "360"))
        except Exception:
            self._heartbeat_idle_timeout = 360

        # Usage emission broadcast (measurement only; no billing enforcement).
        try:
            from mozaiksai.core.events.unified_event_dispatcher import get_event_dispatcher

            dispatcher = get_event_dispatcher()
            dispatcher.register_handler("chat.usage_delta", self._handle_usage_delta_event)
            dispatcher.register_handler("chat.usage_summary", self._handle_usage_summary_event)
            dispatcher.register_handler("chat.token_budget_alert", self._handle_token_budget_alert_event)
        except Exception:
            logger.debug("Usage event handler registration skipped", exc_info=True)

        self._initialized = True
        logger.info("SimpleTransport singleton initialized")

    def register_live_ag2_workflow_run(self, chat_id: str, live_run: Any) -> None:
        previous = self._live_ag2_workflow_runs.get(chat_id)
        if previous is not None and previous is not live_run:
            close = getattr(previous, "close", None)
            if callable(close):
                task = asyncio.create_task(close())
                task.add_done_callback(
                    lambda t: logger.debug(
                        "LIVE_AG2_RUN_REPLACE_CLOSE_FAILED chat=%s: %s",
                        chat_id,
                        t.exception(),
                    )
                    if not t.cancelled() and t.exception() is not None
                    else None
                )
        self._live_ag2_workflow_runs[chat_id] = live_run

    def get_live_ag2_workflow_run(self, chat_id: str) -> Any | None:
        return self._live_ag2_workflow_runs.get(chat_id)

    def pop_live_ag2_workflow_run(self, chat_id: str) -> Any | None:
        return self._live_ag2_workflow_runs.pop(chat_id, None)
        
    async def _handle_usage_delta_event(self, payload: dict[str, Any]) -> None:
        chat_id = payload.get("chat_id")
        if not chat_id:
            return
        try:
            await self.send_event_to_ui({"kind": "usage_delta", **payload}, str(chat_id))
        except Exception:
            logger.debug("Failed to forward usage_delta to UI", exc_info=True)

    async def _handle_usage_summary_event(self, payload: dict[str, Any]) -> None:
        chat_id = payload.get("chat_id")
        if not chat_id:
            return
        try:
            await self.send_event_to_ui({"kind": "usage_summary", **payload}, str(chat_id))
        except Exception:
            logger.debug("Failed to forward usage_summary to UI", exc_info=True)

    async def _handle_token_budget_alert_event(self, payload: dict[str, Any]) -> None:
        chat_id = payload.get("chat_id")
        if not chat_id:
            return
        try:
            await self.send_event_to_ui({"kind": "token_budget_alert", **payload}, str(chat_id))
        except Exception:
            logger.debug("Failed to forward token_budget_alert to UI", exc_info=True)

    # ==================================================================================
    # CONNECTION HELPERS
    # ==================================================================================

    def _get_conn_meta(self, chat_id: str) -> dict[str, Any]:
        """Get connection metadata for a chat_id with safe defaults."""
        conn = self.connections.get(chat_id, {})
        if conn and not conn.get("ws_id"):
            ws = conn.get("websocket")
            if ws is not None:
                try:
                    conn["ws_id"] = id(ws)
                except Exception:
                    pass
        return conn

    async def _send_ws_error(
        self,
        websocket: WebSocket,
        message: str,
        error_code: str,
    ) -> None:
        """Send a standardized error response to the WebSocket client."""
        if getattr(websocket, "application_state", None) is not WebSocketState.CONNECTED:
            return
        if getattr(websocket, "client_state", None) is WebSocketState.DISCONNECTED:
            return
        try:
            await websocket.send_json({
                "type": "chat.error",
                "data": {"message": message, "error_code": error_code},
                "timestamp": _utc_timestamp(),
            })
        except RuntimeError:
            logger.debug("Skipping websocket error frame because the socket is already closed")

    # ==================================================================================
    # USER INPUT COLLECTION (Production-Ready)
    # ==================================================================================
    
    async def submit_user_input(self, input_request_id: str, user_input: str) -> bool:
        """
        Submit user input response for a pending input request.
        
        This method is called by the API endpoint when the frontend submits user input.
        """
        # First try orchestration registry respond callback(s)
        handled = False
        ack_chat_id = None
        for chat_id, reg in list(self._input_request_registries.items()):
            respond_cb = reg.get(input_request_id)
            if respond_cb:
                try:
                    if user_input and user_input != SYSTEM_RESUME_SIGNAL:
                        try:
                            pm = self._get_or_create_persistence_manager()
                            app_id, workflow_name = await self._resolve_chat_context(chat_id, pm=pm)
                            await self._apply_user_text_context_updates(
                                chat_id=chat_id,
                                workflow_name=workflow_name,
                                app_id=app_id,
                                user_input=user_input,
                            )
                        except Exception as trigger_err:
                            logger.debug("[INPUT_SUBMIT] user_text trigger update skipped for %s: %s", chat_id, trigger_err)
                    logger.debug("INPUT_SUBMIT invoking callback request_id=%s chat=%s", input_request_id, chat_id)
                    # Support both async and sync lambdas assigned by AG2
                    result = respond_cb(user_input)
                    if asyncio.iscoroutine(result):
                        await result
                    handled = True
                    ack_chat_id = chat_id
                    self._recent_input_submit_chats.add(chat_id)
                    logger.debug("INPUT_SUBMIT callback invoked request_id=%s chat=%s", input_request_id, chat_id)
                except Exception as e:
                    logger.error("[INPUT] Respond callback failed %s: %s", input_request_id, e, exc_info=True)
                finally:
                    # Remove after use
                    try:
                        del reg[input_request_id]
                    except Exception:
                        pass
                break
        if handled:
            # Clear pending input request from persistence
            if ack_chat_id:
                try:
                    pm = self._get_or_create_persistence_manager()
                    app_id, _workflow_name = await self._resolve_chat_context(
                        ack_chat_id,
                        pm=pm,
                    )
                    if app_id:
                        await pm.clear_pending_input_request(
                            chat_id=ack_chat_id,
                            app_id=app_id,
                        )
                except Exception as e:
                    logger.debug("Failed to clear pending input request: %s", e)
            # Emit chat.input_ack for B9/B10 protocol compliance
            if ack_chat_id:
                try:
                    await self.send_event_to_ui({
                        'kind': 'input_ack',
                        'request_id': input_request_id,
                        'corr': input_request_id,
                    }, ack_chat_id)
                except Exception as e:
                    logger.warning("Failed to emit input_ack: %s", e)
            return True
        
        logger.warning("[INPUT] No active request found for %s", input_request_id)
        return False

    # ------------------------------------------------------------------
    # Orchestration registry integration
    # ------------------------------------------------------------------
    def register_orchestration_input_registry(self, chat_id: str, registry: dict[str, Any]) -> None:
        self._input_request_registries[chat_id] = registry

    def register_input_request(self, chat_id: str, request_id: str, respond_cb: Any) -> str:
        normalized_id = str(request_id) if request_id is not None else ""
        if not normalized_id or normalized_id.lower() == "none":
            normalized_id = uuid.uuid4().hex
            logger.debug("Generated fallback input request id %s for chat %s", normalized_id, chat_id)
        if chat_id not in self._input_request_registries:
            self._input_request_registries[chat_id] = {}
        self._input_request_registries[chat_id][normalized_id] = respond_cb
        logger.debug("Registered input request %s for chat %s", normalized_id, chat_id)
        return normalized_id

    def consume_recent_input_submit(self, chat_id: str) -> bool:
        if chat_id in self._recent_input_submit_chats:
            self._recent_input_submit_chats.discard(chat_id)
            return True
        return False

    def _build_resume_signal(self, chat_id: str, request_id: str) -> str:
        """Produce a non-empty fallback message when resuming pending input requests.

        Ensures downstream ChatCompletion payloads always contain valid user content even when
        lifecycle tools resume execution without explicit text input.
        
        Note: This is an internal coordination signal for AG2 continuation. It should never
        be persisted to the database or shown in the UI as it has no semantic meaning to users.
        """
        return SYSTEM_RESUME_SIGNAL

    def get_background_run_summary(self) -> dict[str, Any]:
        """Return the current in-memory background workflow task snapshot."""
        runs: list[dict[str, Any]] = []
        for chat_id, task in sorted(self._background_tasks.items()):
            conn = self.connections.get(chat_id) or {}
            task_name = task.get_name() if hasattr(task, "get_name") else None
            runs.append(
                {
                    "chat_id": chat_id,
                    "workflow_name": conn.get("workflow_name"),
                    "user_id": conn.get("user_id"),
                    "has_connection": bool(conn),
                    "task_name": task_name,
                }
            )
        return {"active_count": len(runs), "runs": runs}
    
    
    def should_show_to_user(self, agent_name: str | None, chat_id: str | None = None) -> bool:
        """Check if a message should be shown to the user interface"""
        if not agent_name:
            return True  # Show system messages

        normalized_name = str(agent_name).strip().lower()
        if normalized_name in {"user", "system"}:
            return True
        
        # Get the workflow type and ws_id for this chat session
        workflow_name = None
        ws_id = None
        if chat_id and chat_id in self.connections:
            workflow_name = self.connections[chat_id].get("workflow_name")
            ws_id = self.connections[chat_id].get("ws_id")
        
        # If in general mode, show all messages (bypass visual_agents filtering)
        if ws_id and session_registry.is_in_general_mode(ws_id):
            logger.debug("GENERAL_MODE_BYPASS agent=%s", agent_name)
            return True
        
        # If we have workflow type, use visual_agents filtering
        if workflow_name:
            try:
                config = workflow_manager.get_config(workflow_name)
                visual_agents = config.get("visual_agents")
                has_visual_agents_key = "visual_agents" in config

                # Explicit null is an authoring signal to hide all agent text for this workflow.
                if has_visual_agents_key and visual_agents is None:
                    logger.debug("VISUAL_AGENTS_NULL workflow=%s agent=%s — hiding message", workflow_name, agent_name)
                    return False
                
                # If visual_agents is defined, only show messages from those agents
                if isinstance(visual_agents, list):
                    if not visual_agents:
                        logger.debug("VISUAL_AGENTS_EMPTY workflow=%s agent=%s — allowing message", workflow_name, agent_name)
                        return True
                    # Normalize both the agent name and visual_agents list for comparison
                    # This matches the frontend normalization logic in ChatPage.js
                    def normalize_agent(name):
                        if not name:
                            return ''
                        return str(name).lower().replace('agent', '').replace(' ', '').strip()
                    
                    normalized_agent = normalize_agent(agent_name)
                    normalized_visual_agents = [normalize_agent(va) for va in visual_agents]
                    
                    is_allowed = normalized_agent in normalized_visual_agents
                    logger.debug("VISUAL_AGENTS_CHECK agent=%s normalized=%s allowed=%s", agent_name, normalized_agent, is_allowed)
                    return is_allowed
            except FileNotFoundError:
                # If no specific config, default to showing the message
                pass
        
        return True

    def _sanitize_trace_content(self, content: str, *, limit: int = 800) -> tuple[str, bool, bool]:
        """Redact likely secrets and truncate trace content before sending to UI."""
        if not isinstance(content, str):
            return str(content), False, False

        redacted = False
        value = content

        rules: list[tuple[re.Pattern, str]] = [
            (re.compile(r"\bBearer\s+[A-Za-z0-9\-_\.=]+\b"), "Bearer [REDACTED]"),
            (re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"), "sk-[REDACTED]"),
            (re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"), "ghp_[REDACTED]"),
            (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AKIA[REDACTED]"),
            (re.compile(r"mongodb\+srv://[^\s]+"), "mongodb+srv://[REDACTED]"),
            (re.compile(r"mongodb://[^\s]+"), "mongodb://[REDACTED]"),
            (re.compile(r"eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}"), "[REDACTED_JWT]"),
            (re.compile(r"(?i)\b(api[_-]?key|secret|password)\s*[:=]\s*[^\s]+"), r"\1=[REDACTED]"),
        ]

        for pattern, replacement in rules:
            if pattern.search(value):
                redacted = True
                value = pattern.sub(replacement, value)

        truncated = False
        if limit and len(value) > limit:
            value = value[:limit].rstrip() + "…"
            truncated = True

        return value, redacted, truncated

    # ==================================================================================
    # UNIFIED USER MESSAGE INGESTION
    # ==================================================================================
    async def process_incoming_user_message(self, *, chat_id: str, user_id: str | None, content: str, source: str = 'ws') -> None:
        """Forward a free-form user message into the active workflow orchestration.

        This is used by both WebSocket (user.input.submit without request_id) and
        HTTP input endpoint. The orchestration bridge persists canonical run input
        to the AG2 stream before this method is called, so transport only emits
        the local UI echo here.
        """
        if not content:
            return
        _ = user_id
        _ = source
        try:
            seq = self._get_next_sequence(chat_id)
            index = max(0, seq - 1)
        except Exception:
            index = 0
        try:
            await self.send_event_to_ui({'kind': 'text', 'agent': 'user', 'content': content, 'index': index}, chat_id)
        except Exception as emit_err:
            logger.error("Failed to emit user message event for %s: %s", chat_id, emit_err, exc_info=True)

    async def process_component_action(self, *, chat_id: str, app_id: str, component_id: str, action_type: str, action_data: dict) -> dict[str, Any]:
        """Apply a component action to context variables and emit acknowledgement.

        Returns a structured result indicating applied changes.
        """
        conn = self.connections.get(chat_id) or {}
        context = conn.get('context')
        applied: dict[str, Any] = {}
        try:
            # Basic pattern: if action_data has 'set': {k: v} apply to context
            sets = action_data.get('set') if isinstance(action_data, dict) else None
            if context and isinstance(sets, dict):
                for k, v in sets.items():
                    try:
                        context.set(k, v)
                        applied[k] = v
                    except Exception as ce:
                        logger.debug("Context set failed for %s: %s", k, ce)
            # Emit acknowledgement event
            await self.send_event_to_ui({
                'kind': 'component_action_ack',
                'component_id': component_id,
                'action_type': action_type,
                'applied': applied,
                'chat_id': chat_id,
            }, chat_id)
            return {'applied': applied, 'component_id': component_id, 'action_type': action_type}
        except Exception as e:
            logger.error("Component action processing failed for %s: %s", chat_id, e, exc_info=True)
            raise
        
    # ==================================================================================
    # AG2 EVENT SENDING (Production)
    # ==================================================================================
    
    async def send_event_to_ui(self, event: Any, chat_id: str | None = None) -> None:
        """
        Serializes and sends a raw AG2 event to the UI.
        This is the primary method for forwarding AG2 native events.
        """
        try:
            # Allow callers to provide a fully-formed transport envelope (e.g., ack.tool_call_response)
            # without forcing another serialization pass through the dispatcher.
            if isinstance(event, dict) and 'type' in event and 'data' in event and 'kind' not in event:
                logger.debug(
                    "TRANSPORT_ENVELOPE_PASSTHROUGH type=%s",
                    event.get('type')
                )
                await self._broadcast_to_websockets(event, chat_id)
                return

            # Suppress IOStream-originated stream/print events.
            # WebSocketIOStream calls send_event_to_ui with kind='stream_chunk',
            # 'stream_end', or 'print' for its print()-capture output.  The
            # chat.text post-hoc chunking path (further below) already delivers
            # the same content as stream_chunk + stream_end, so letting the
            # IOStream events through would create duplicate / split bubbles.
            if isinstance(event, dict):
                _iostream_kind = event.get('kind', '')
                if _iostream_kind in ('stream_chunk', 'stream_end', 'print'):
                    logger.debug(
                        "[TRANSPORT] Suppressing IOStream event kind=%s chat=%s",
                        _iostream_kind, chat_id,
                    )
                    return

            from mozaiksai.core.events.unified_event_dispatcher import (
                get_event_dispatcher,  # local import to avoid cycle
            )
            dispatcher = get_event_dispatcher()
            workflow_name = None
            if chat_id and chat_id in self.connections:
                workflow_name = self.connections[chat_id].get('workflow_name')

            envelope = dispatcher.build_outbound_event_envelope(
                raw_event=event,
                chat_id=chat_id,
                get_sequence_cb=self._get_next_sequence,
                workflow_name=workflow_name,
            )
            if not envelope:
                event_kind = event.get('kind', type(event).__name__) if isinstance(event, dict) else type(event).__name__
                logger.warning("No envelope created for event kind=%s chat=%s", event_kind, chat_id)
                return

            envelope_type = envelope.get('type') if isinstance(envelope, dict) else None

            def _downgrade_to_trace(*, agent: str) -> bool:
                """Convert non-visual chat.text/print into a UI-hidden trace event."""
                if not isinstance(envelope, dict):
                    return False
                if envelope_type not in ("chat.text", "chat.print"):
                    return False
                data_payload = envelope.get("data")
                if not isinstance(data_payload, dict):
                    return False
                original_content = data_payload.get("content")
                if isinstance(original_content, str):
                    sanitized, redacted, truncated = self._sanitize_trace_content(original_content)
                    data_payload["content"] = sanitized
                    data_payload["trace_original_len"] = len(original_content)
                    data_payload["trace_redacted"] = redacted
                    data_payload["trace_truncated"] = truncated
                data_payload["ui_visibility"] = "trace"
                data_payload["trace_reason"] = "visual_agents_gate"
                data_payload["trace_agent"] = agent
                return True
            
            # Determine if this is a UI tool event (requires user interaction)
            is_ui_tool_event = False
            if envelope_type == 'chat.tool_call' and isinstance(envelope.get('data'), dict):
                data_payload = envelope.get('data')
                # UI tool events have awaiting_response=True and component_type
                is_ui_tool_event = data_payload.get('awaiting_response') and data_payload.get('component_type')
            
            # Skip visibility filtering for select_speaker and response-required UI tool events
            skip_visibility_filter = (
                envelope_type == 'chat.select_speaker'
                or envelope_type == 'chat.run_complete'
                or envelope_type == 'chat.activity'
                or is_ui_tool_event
            )
            
            if is_ui_tool_event:
                logger.debug("TRANSPORT_UI_TOOL_EVENT chat=%s component=%s — bypassing agent visibility filter", chat_id, envelope.get('data', {}).get('component_type'))

            # Additional filtering (agent visibility) only for BaseEvent path where needed
            agent_name = None
            if isinstance(event, BaseEvent) and hasattr(event, 'sender') and getattr(event.sender, 'name', None):  # type: ignore
                agent_name = event.sender.name  # type: ignore
            if not skip_visibility_filter and agent_name and not self.should_show_to_user(agent_name, chat_id):
                if _downgrade_to_trace(agent=str(agent_name)):
                    logger.debug("TRANSPORT downgraded non-visual message agent=%s chat=%s", agent_name, chat_id)
                else:
                    logger.debug("TRANSPORT filtered AG2 event agent=%s chat=%s (not visual)", agent_name, chat_id)
                    return

            # Apply visibility filtering for dict events (post-envelope) as well
            if not agent_name:
                data_payload = envelope.get('data') if isinstance(envelope, dict) else None
                if isinstance(data_payload, dict):
                    raw_payload = data_payload.get('raw_content') if isinstance(data_payload.get('raw_content'), dict) else None
                    raw_sender = None
                    if isinstance(raw_payload, dict):
                        raw_sender = raw_payload.get('sender') or raw_payload.get('agent') or raw_payload.get('agent_name') or raw_payload.get('name')

                    payload_agent = data_payload.get('agent') or data_payload.get('agent_name')
                    if isinstance(payload_agent, str) and payload_agent.strip().lower() in {'assistant', 'agent'} and raw_sender:
                        agent_name = raw_sender
                    else:
                        agent_name = raw_sender or payload_agent

                    if not agent_name and isinstance(event, dict):
                        agent_name = event.get('sender') or event.get('agent') or event.get('agent_name')
                if not skip_visibility_filter and agent_name and not self.should_show_to_user(agent_name, chat_id):
                    if _downgrade_to_trace(agent=str(agent_name)):
                        logger.debug("TRANSPORT downgraded non-visual message agent=%s chat=%s", agent_name, chat_id)
                    else:
                        logger.debug("TRANSPORT filtered event agent=%s chat=%s (visual_agents gate)", agent_name, chat_id)
                        return

            # Check for suppression flag from derived context hooks
            if envelope and isinstance(envelope, dict):
                data_payload = envelope.get('data')
                if isinstance(data_payload, dict) and data_payload.get('_mozaiks_hide'):
                    logger.debug("TRANSPORT suppressing hidden message chat=%s", chat_id)
                    return

            # ----------------------------------------------------------------
            # Token chunking: stream visible agent text as word-level chunks
            # so the frontend renders a typewriter effect via stream_chunk /
            # stream_end instead of a single chat.text burst.
            # ----------------------------------------------------------------
            if envelope_type == 'chat.text' and isinstance(envelope.get('data'), dict):
                _d = envelope['data']
                _content = _d.get('content', '')
                _role = str(_d.get('role', '') or '').lower()
                _sender = str(
                    _d.get('agent') or _d.get('agent_name') or _d.get('sender') or ''
                ).lower()
                _vis = _d.get('ui_visibility')
                # Only stream visible assistant messages; pass user echoes and
                # trace events through as plain chat.text (no chunking).
                if _content and _role not in ('user',) and _sender not in ('user',) and _vis != 'trace':
                    _agent = (
                        _d.get('agent') or _d.get('agent_name') or _d.get('sender') or 'Agent'
                    )
                    _stream_id = f"{chat_id or 'x'}:{_agent}:{uuid.uuid4().hex[:8]}"
                    # Metadata keys forwarded to stream_end so the frontend can
                    # apply capability flags when it finalises the message.
                    _meta_keys = (
                        'is_structured_capable', 'is_visual', 'is_tool_agent',
                        'structured_output', 'structured_schema', 'metadata', 'sequence',
                    )
                    _stream_meta = {k: _d[k] for k in _meta_keys if k in _d}
                    logger.debug(
                        "STREAM_TEXT_CHUNK agent=%s chat_id=%s len=%d",
                        _agent, chat_id, len(_content),
                    )
                    await self._emit_text_as_chunks(
                        _content, str(_agent), chat_id or '', _stream_id
                    )
                    await self._broadcast_to_websockets(
                        {
                            "type": "chat.stream_end",
                            "data": {
                                "agent": _agent,
                                "stream_id": _stream_id,
                                # Use 'content' (not 'full_content') so the frontend's
                                # data.data promotion makes it available as data.content,
                                # which the stream_end handler reads via
                                # `data.full_content || data.content`.
                                "content": _content,
                                **_stream_meta,
                            },
                        },
                        chat_id,
                    )
                    # stream_end replaces chat.text for this message; skip
                    # the plain broadcast below.
                    return  # noqa: RET504
            # ----------------------------------------------------------------

            logger.debug("TRANSPORT_ENVELOPE_SEND type=%s chat=%s", envelope.get('type'), chat_id)
            try:
                envelope_type = envelope.get('type') if isinstance(envelope, dict) else None
                if envelope_type == 'chat.run_complete' and chat_id:
                    registry = getattr(self, "_ui_run_complete_sent", None)
                    if not isinstance(registry, dict):
                        registry = {}
                        self._ui_run_complete_sent = registry
                    registry[chat_id] = True
                    conn = self.connections.get(chat_id)
                    if isinstance(conn, dict):
                        conn["ui_run_complete_sent"] = True
            except Exception as _rc_exc:
                logger.debug("RUN_COMPLETE_REGISTRY_UPDATE_FAILED chat=%s: %s", chat_id, _rc_exc)
            await self._broadcast_to_websockets(envelope, chat_id)

            # Runtime hook: surface run completion to the unified dispatcher so
            # higher-level coordinators (e.g., workflow pack adapter) can react.
            try:
                envelope_type = envelope.get('type') if isinstance(envelope, dict) else None
                if envelope_type == 'chat.run_complete':
                    data_payload = envelope.get('data') if isinstance(envelope, dict) else None
                    if isinstance(data_payload, dict):
                        dispatch_payload = dict(data_payload)
                        if chat_id and "chat_id" not in dispatch_payload:
                            dispatch_payload["chat_id"] = chat_id
                        try:
                            conn = self.connections.get(chat_id) if chat_id else None
                            if isinstance(conn, dict):
                                for k in ("app_id", "user_id", "workflow_name", "ws_id"):
                                    v = conn.get(k)
                                    if v is not None and k not in dispatch_payload:
                                        dispatch_payload[k] = v
                        except Exception as _conn_exc:
                            logger.debug("RUNTIME_PROCESS_COMPLETED_PAYLOAD_ENRICH_FAILED chat=%s: %s", chat_id, _conn_exc)
                        _t = asyncio.create_task(dispatcher.emit(RUNTIME_PROCESS_COMPLETED, dispatch_payload))
                        _t.add_done_callback(
                            lambda t: logger.debug("RUNTIME_PROCESS_COMPLETED_EMIT_FAILED chat=%s: %s", chat_id, t.exception())
                            if not t.cancelled() and t.exception() is not None
                            else None
                        )
            except Exception as _dispatch_exc:
                logger.debug("RUNTIME_PROCESS_COMPLETED_HOOK_FAILED chat=%s: %s", chat_id, _dispatch_exc)
        except Exception as e:
            logger.error("Failed to serialize or send UI event: %s", e, exc_info=True)

    def _extract_clean_content(self, message: str | dict[str, Any] | Any) -> str:
        """Instance wrapper around the module-level cleaner."""
        return _extract_clean_content(message)

    async def _emit_text_as_chunks(
        self,
        content: str,
        agent_name: str,
        chat_id: str,
        stream_id: str,
    ) -> None:
        """Split *content* into adaptive text chunks and emit each as chat.stream_chunk.

        Short responses keep the more granular word-by-word typewriter feel.
        Longer responses are compacted into larger semantic chunks so transport
        and harness logs do not get flooded with hundreds of single-word frames.

        A minimal async yield between chunks ensures React 18's automatic
        batching doesn't collapse all updates into a single render. The caller
        is responsible for sending chat.stream_end afterwards.
        """
        chunks = self._chunk_text_for_stream(content)
        for seq, token in enumerate(chunks):
            await self._broadcast_to_websockets(
                {
                    "type": "chat.stream_chunk",
                    "data": {
                        "agent": agent_name,
                        "content": token,
                        "stream_id": stream_id,
                        "chunk_seq": seq,
                    },
                },
                chat_id or None,
            )
            # Minimal delay to allow browser render cycles between chunks.
            if seq < len(chunks) - 1:  # No delay after the last chunk
                await asyncio.sleep(0.015)  # 15ms between chunks

    def _chunk_text_for_stream(self, content: str) -> list[str]:
        """Return adaptive websocket chunks for a visible assistant message.

        The chunker stays workflow-agnostic: it looks only at text length and
        punctuation. Short messages preserve the stronger typewriter effect,
        while longer messages are grouped to keep event volume reasonable.
        """
        import re as _re

        tokens = _re.findall(r"\S+\s*", content)
        if not tokens:
            return [content]
        if len(content) <= 80:
            return tokens

        if len(content) <= 220:
            target_chars = 20
            hard_max = 36
        elif len(content) <= 600:
            target_chars = 36
            hard_max = 64
        else:
            target_chars = 52
            hard_max = 88

        chunks: list[str] = []
        current = ""
        punctuation_endings = (".", "!", "?", ";", ":", "\n")

        for token in tokens:
            if not current:
                current = token
                continue

            candidate = current + token
            if len(candidate) > hard_max:
                chunks.append(current)
                current = token
                continue

            current = candidate
            if len(current) >= target_chars and current.rstrip().endswith(punctuation_endings):
                chunks.append(current)
                current = ""

        if current:
            chunks.append(current)

        return chunks

    async def _broadcast_to_websockets(self, event_data: dict[str, Any], target_chat_id: str | None = None) -> None:
        """Broadcast event data to relevant WebSocket connections."""
        active_connections = list(self.connections.items())
        
        # If a chat_id is specified, only send to that connection
        if target_chat_id:
            connection_info = self.connections.get(target_chat_id)
            if connection_info and connection_info.get("websocket"):
                # H1: Use message queuing with backpressure control
                await self._queue_message_with_backpressure(target_chat_id, event_data)
                await self._flush_message_queue(target_chat_id)
            else:
                # H4: Buffer message until the websocket connects
                buf = self._pre_connection_buffers.setdefault(target_chat_id, [])
                buf.append(event_data)
                if len(buf) > self._max_pre_connection_buffer:
                    # Drop oldest while keeping newest insight
                    overflow = len(buf) - self._max_pre_connection_buffer
                    del buf[0:overflow]
                    total_overflow = self._pre_connection_buffer_overflow_counts.get(target_chat_id, 0) + overflow
                    self._pre_connection_buffer_overflow_counts[target_chat_id] = total_overflow
                    if total_overflow == overflow:
                        logger.warning(
                            "PRE_CONN_BUFFER_OVERFLOW_DROPPED dropped=%d chat=%s; "
                            "suppressing repeated overflow logs until the connection is restored",
                            overflow,
                            target_chat_id,
                        )
                    else:
                        logger.debug(
                            "Suppressed pre-connection buffer overflow log for %s "
                            "(additional_dropped=%d total_dropped=%d)",
                            target_chat_id,
                            overflow,
                            total_overflow,
                        )
                logger.debug("PRE_CONN_BUFFER_QUEUED chat=%s size=%s", target_chat_id, len(buf))
            return

        # Otherwise, broadcast to all connections
        for chat_id, info in active_connections:
            websocket = info.get("websocket")
            if websocket:
                # H1: Use message queuing with backpressure control
                await self._queue_message_with_backpressure(chat_id, event_data)
                await self._flush_message_queue(chat_id)

    def _stringify_unknown(self, obj: Any) -> str:
        """Safely convert any object to a string for logging/transport."""
        if obj is None:
            return ""
        if isinstance(obj, (str, int, float, bool)):
            return str(obj)
        if isinstance(obj, (bytes, bytearray)):
            try:
                return obj.decode("utf-8", errors="replace")
            except Exception:
                pass
        try:
            # Keep container payload readable while avoiding recursive __str__/__repr__ calls.
            if isinstance(obj, dict):
                return json.dumps(obj, default=lambda o: f"<{type(o).__name__}@{hex(id(o))}>")
            if isinstance(obj, (list, tuple, set)):
                return json.dumps(list(obj), default=lambda o: f"<{type(o).__name__}@{hex(id(o))}>")
        except Exception:
            pass
        try:
            type_name = type(obj).__name__
        except Exception:
            type_name = "object"
        try:
            object_id = hex(id(obj))
        except Exception:
            object_id = "unknown"
        return f"<unserializable {type_name} {object_id}>"

    def _serialize_ag2_events(self, obj: Any, _seen: set[int] | None = None, _depth: int = 0) -> Any:
        """Convert AG2 event objects to JSON-serializable format."""
        if _seen is None:
            _seen = set()
        try:
            # Lazy import so absence of optional AG2 event classes doesn't break app start.
            try:
                from ag2.events import HumanInputRequest as InputRequestEvent  # type: ignore
            except Exception:  # pragma: no cover - AG2 optional class
                InputRequestEvent = tuple()  # type: ignore

            # Optional tool events (some versions place them elsewhere)
            ToolResponseEvent = None  # default
            for mod_path in [
                "ag2.events.tool_events",
                "ag2.events",  # fallback if class relocated
            ]:
                if ToolResponseEvent:
                    break
                try:  # pragma: no cover - defensive import paths
                    mod = __import__(mod_path, fromlist=["ToolResponseEvent"])
                    ToolResponseEvent = getattr(mod, "ToolResponseEvent", None)
                except Exception:
                    continue

            # Primitive fast-path
            if obj is None or isinstance(obj, (str, int, float, bool)):
                return obj

            # Datetime fast-path (common from Mongo persistence payloads)
            if isinstance(obj, datetime):
                return obj.isoformat()

            if isinstance(obj, uuid.UUID):
                return str(obj)

            # Guard against deep recursive/cyclic objects from external runtimes.
            if _depth > 12:
                return self._stringify_unknown(obj)

            obj_id = id(obj)
            if obj_id in _seen:
                return f"<circular_ref {type(obj).__name__}>"

            # Dict / list recursive handling
            if isinstance(obj, dict):
                _seen.add(obj_id)
                try:
                    safe_obj = {}
                    for k, v in obj.items():
                        if isinstance(k, str):
                            safe_key = k
                        elif isinstance(k, datetime):
                            safe_key = k.isoformat()
                        else:
                            safe_key = str(k)
                        safe_obj[safe_key] = self._serialize_ag2_events(v, _seen, _depth + 1)
                    return safe_obj
                finally:
                    _seen.discard(obj_id)
            if isinstance(obj, (list, tuple, set)):
                _seen.add(obj_id)
                try:
                    return [self._serialize_ag2_events(v, _seen, _depth + 1) for v in list(obj)]
                finally:
                    _seen.discard(obj_id)

            if isinstance(obj, BaseModel) and hasattr(obj, "model_dump"):
                _seen.add(obj_id)
                try:
                    return self._serialize_ag2_events(obj.model_dump(), _seen, _depth + 1)
                finally:
                    _seen.discard(obj_id)

            # Specific AG2 event shapes
            def _extract_sender(o):
                s = getattr(o, "sender", None)
                try:
                    if s is not None and hasattr(s, "name"):
                        return s.name
                except Exception:
                    pass
                return self._stringify_unknown(s)

            def _extract_recipient(o):
                r = getattr(o, "recipient", None)
                try:
                    if r is not None and hasattr(r, "name"):
                        return r.name
                except Exception:
                    pass
                return self._stringify_unknown(r)

            cls_name = obj.__class__.__name__

            # TextEvent
            try:
                if "TextEvent" in cls_name:
                    return {
                        "uuid": str(getattr(obj, "uuid", "")),
                        "content": self._stringify_unknown(getattr(obj, "content", None)),
                        "sender": _extract_sender(obj),
                        "recipient": _extract_recipient(obj),
                        "_ag2_event_type": "TextEvent",
                    }
            except Exception:
                pass

            # InputRequestEvent
            if InputRequestEvent and isinstance(obj, InputRequestEvent):  # type: ignore[arg-type]
                return {
                    "uuid": str(getattr(obj, "uuid", "")),
                    "prompt": self._stringify_unknown(getattr(obj, "prompt", None)),
                    "password": None,  # never forward secrets
                    "type": self._stringify_unknown(getattr(obj, "type", None)),
                    "_ag2_event_type": "InputRequestEvent",
                }

            # ToolResponseEvent (covers tool outputs)
            if ToolResponseEvent and isinstance(obj, ToolResponseEvent):  # type: ignore[arg-type]
                return {
                    "uuid": str(getattr(obj, "uuid", "")),
                    "tool_name": self._stringify_unknown(getattr(obj, "tool_name", None)),
                    "content": self._stringify_unknown(getattr(obj, "content", getattr(obj, "result", None))),
                    "sender": _extract_sender(obj),
                    "recipient": _extract_recipient(obj),
                    "_ag2_event_type": "ToolResponseEvent",
                }

            # Generic event-like objects with a small public attribute surface.
            _seen.add(obj_id)
            try:
                public_attrs = {}
                # Avoid exploding on very large objects; cap attributes
                attr_count = 0
                for name in dir(obj):
                    if name.startswith("_"):
                        continue
                    if attr_count > 25:
                        break
                    try:
                        value = getattr(obj, name)
                    except Exception:
                        continue
                    # Skip callables
                    if callable(value):
                        continue
                    attr_count += 1
                    public_attrs[name] = self._serialize_ag2_events(value, _seen, _depth + 1)

                if public_attrs:
                    public_attrs["_ag2_event_type"] = cls_name
                    return public_attrs

                # Fallback textual representation
                return self._stringify_unknown(obj)
            finally:
                _seen.discard(obj_id)
        except Exception:
            # Final safety fallback
            return self._stringify_unknown(obj)

    async def _handle_artifact_action(self, event: dict[str, Any], chat_id: str, websocket) -> None:
        """
        Handle artifact action events from frontend (launch_workflow, update_state, etc.).
        
        Args:
            event: Event data with type='chat.artifact_action' and data payload
            chat_id: Current chat/session ID
            websocket: WebSocket connection for response
        """
        data = event.get("data", {})
        if not isinstance(data, dict) or not data:
            data = event if isinstance(event, dict) else {}
        action = data.get("action") or data.get("tool")
        params = data.get("params", {})
        if not isinstance(params, dict):
            params = {}
        payload = data.get("payload", {})
        if not isinstance(payload, dict):
            payload = {}
        artifact_id = data.get("artifact_id")
        action_id = data.get("action_id")
        
        conn_meta = self.connections.get(chat_id, {})
        app_id = conn_meta.get("app_id")
        user_id = conn_meta.get("user_id")
        
        if not app_id or not user_id:
            logger.error("Missing app_id or user_id for artifact action in chat %s", chat_id)
            return

        if action == "media.promote":
            asset_id = str(params.get("asset_id") or payload.get("asset_id") or "").strip()
            promotion_target = str(params.get("promotion_target") or params.get("target") or "artifact").strip()
            if action_id:
                await websocket.send_json({
                    "type": "artifact.action.started",
                    "data": {
                        "action_id": action_id,
                        "artifact_id": artifact_id,
                        "tool": action,
                    },
                    "chat_id": chat_id,
                    "timestamp": datetime.now(UTC).isoformat(),
                })
            try:
                if not asset_id:
                    raise ValueError("asset_id is required")
                from mozaiksai.core.media.store import get_media_asset_store

                updated = await get_media_asset_store().mark_asset_promoted(
                    app_id=str(app_id),
                    asset_id=asset_id,
                    promotion_target=promotion_target,
                    promoted_by=str(user_id),
                )
                await websocket.send_json({
                    "type": "artifact.action.completed",
                    "data": {
                        "action_id": action_id,
                        "artifact_id": artifact_id,
                        "tool": action,
                        "result": {
                            "asset_id": asset_id,
                            "promotion_target": promotion_target,
                            "status": "promoted",
                            "asset": updated.model_dump(by_alias=False, mode="json") if updated else None,
                        },
                    },
                    "chat_id": chat_id,
                    "timestamp": datetime.now(UTC).isoformat(),
                })
            except Exception as exc:
                await websocket.send_json({
                    "type": "artifact.action.failed",
                    "data": {
                        "action_id": action_id,
                        "artifact_id": artifact_id,
                        "tool": action,
                        "error": str(exc) or "Media promotion failed",
                    },
                    "chat_id": chat_id,
                    "timestamp": datetime.now(UTC).isoformat(),
                })
            return
        
        # Route: launch_workflow (pause current, create new session)
        if action == "launch_workflow":
            target_workflow = payload.get("workflow_name")
            if not target_workflow:
                logger.warning("Missing workflow_name in launch_workflow action")
                return
            
            logger.debug("WORKFLOW_LAUNCH workflow=%s chat=%s", target_workflow, chat_id)
            
        # Route workflow start through SessionRouter so dependency reroutes are consistent
            from mozaiksai.core.session import TriggerInput, get_session_router

            session_router = get_session_router()
            route_decision = await session_router.route_trigger(
                TriggerInput(
                    app_id=str(app_id),
                    user_id=str(user_id),
                    trigger_source="action",
                    workflow_id=str(target_workflow),
                )
            )
            resolved_workflow = route_decision.workflow_id
            if route_decision.rerouted_by_dependency and route_decision.unmet_dependency is not None:
                await websocket.send_json({
                    "type": "chat.workflow_rerouted",
                    "data": {
                        "requested_workflow_name": str(target_workflow),
                        "resolved_workflow_name": resolved_workflow,
                        "reason": route_decision.unmet_dependency.reason,
                        "blocked_workflow_name": route_decision.unmet_dependency.blocked_workflow_id,
                    },
                    "chat_id": chat_id,
                    "timestamp": datetime.now(UTC).isoformat()
                })
            
            # Create new session and artifact (old session stays IN_PROGRESS)
            new_session = await session_manager.create_workflow_session(
                app_id, user_id, resolved_workflow
            )
            artifact = await session_manager.create_artifact_instance(
                app_id,
                resolved_workflow,
                payload.get("artifact_type", "ActionPlan")
            )
            await session_manager.attach_artifact_to_session(
                new_session["_id"], artifact["_id"], app_id
            )
            
            logger.debug("Created new session %s with artifact %s", new_session['_id'], artifact['_id'])
            
            # Notify frontend to navigate to new chat
            await websocket.send_json({
                "type": "chat.navigate",
                "data": {
                    "chat_id": new_session["_id"],
                    "workflow_name": resolved_workflow,
                    "requested_workflow_name": str(target_workflow),
                    "artifact_instance_id": artifact["_id"],
                    "app_id": app_id
                },
                "correlation_id": event.get("correlation_id"),
                "timestamp": datetime.now(UTC).isoformat()
            })
            return
        
        # Route: update_state (partial artifact state updates)
        if action == "update_state" and artifact_id:
            state_updates = payload.get("state_updates", {})
            if not state_updates:
                logger.warning("Empty state_updates in update_state action")
                return
            
            await session_manager.update_artifact_state(
                artifact_id, app_id, state_updates
            )
            
            logger.debug("Updated artifact state for %s: %s", artifact_id, list(state_updates.keys()))
            
            # Broadcast state update to all connections for this artifact
            await websocket.send_json({
                "type": "artifact.state.updated",
                "data": {
                    "artifact_id": artifact_id,
                    "state_delta": state_updates
                },
                "chat_id": chat_id,
                "timestamp": datetime.now(UTC).isoformat()
            })
            return
        
        # Route: other actions (forward to agent as tool_call or handle directly)
        logger.debug("ARTIFACT_ACTION_RECEIVED action=%s chat=%s", action, chat_id)
        # Future: route to agent or handle other action types
        await websocket.send_json({
            "type": "ack.artifact_action",
            "data": {
                "action": action,
                "status": "received"
            },
            "chat_id": chat_id,
            "timestamp": datetime.now(UTC).isoformat()
        })

    async def _handle_resume_request(self, chat_id: str, last_client_index: int, websocket) -> None:
        """Resume protocol for persisted AG2 1.0 beta workflow runs.

        We DO NOT compute sequence diffs via a bespoke diff endpoint anymore.
        Instead we:
          1. Load the authoritative persisted message list for the chat.
          2. Determine the slice of messages the client is missing based on the
             last message *index* the client reports it has (last_client_index).
             The client sends -1 if it has none.
          3. Re-emit each missing message to the client as chat.text with a
             replay flag and a stable index. We keep an internal sequence counter
             but its primary purpose is ordering of new live events; indexes are
             sufficient for replay correctness.
          4. Emit chat.resume_boundary summarizing counts and boundaries.

        The persisted messages array is the runtime source of truth for
        rebuilding agent context, while the WebSocket consumer gets a minimal,
        deterministic replay mechanism.
        """
        try:
            conn_meta = self.connections.get(chat_id) or {}
            app_id = conn_meta.get('app_id')
            workflow_startup_mode = None
            if not app_id:
                raise RuntimeError("Missing app_id for resume")

            workflow_name = conn_meta.get('workflow_name')
            if workflow_name:
                try:
                    from mozaiksai.core.workflow.workflow_manager import workflow_manager

                    cfg = workflow_manager.get_config(str(workflow_name)) or {}
                    workflow_startup_mode = cfg.get('workflow_startup_mode')
                except Exception:
                    workflow_startup_mode = None

            # Use the AG2-aligned replayer so visibility filtering and UI tool replay
            # semantics stay consistent with live events (no leaking hidden agents).
            from mozaiksai.core.transport.run_replay import WorkflowRunReplayer

            replayer = WorkflowRunReplayer()
            summary = await replayer.handle_resume_request(
                chat_id=str(chat_id),
                app_id=str(app_id),
                last_client_index=int(last_client_index),
                send_event=self.send_event_to_ui,
                workflow_startup_mode=workflow_startup_mode,
            )

            # Real-time sequence continuity: do not reduce existing counter.
            last_idx_sent = summary.get("last_message_index") if isinstance(summary, dict) else None
            if isinstance(last_idx_sent, int):
                existing_seq = self._sequence_counters.get(chat_id, 0)
                if existing_seq < last_idx_sent + 1:
                    self._sequence_counters[chat_id] = last_idx_sent + 1

            logger.info(
                "RESUME_COMPLETE chat=%s replayed=%s missing_from>%s now_at_index=%s total=%s",
                chat_id,
                (summary.get("replayed_messages") if isinstance(summary, dict) else None),
                last_client_index,
                last_idx_sent,
                (summary.get("total_messages") if isinstance(summary, dict) else None),
            )
        except Exception as e:
            logger.error("Resume failed chat=%s: %s", chat_id, e, exc_info=True)
            raise

    def _validate_inbound_message(self, message_data: dict) -> bool:
        """H3: Validate inbound WebSocket message schema"""
        if not isinstance(message_data, dict):
            return False
        
        msg_type = message_data.get('type') or message_data.get('kind')
        if not msg_type or not isinstance(msg_type, str):
            return False
        
        # T1: Validate required fields based on message type
        if msg_type == "user.input.submit":
            # Allow either (a) input_request response with request_id OR (b) free-form user chat message
            base_ok = "chat_id" in message_data and "text" in message_data
            if not base_ok:
                return False
            # request_id optional (only when responding to InputRequestEvent)
            return True
        
        elif msg_type == "tool_call_response":
            # Tool call response from frontend (Approve/Cancel/Submit buttons)
            # Must have tool_call_id to correlate with pending wait_for_tool_call_response
            return "tool_call_id" in message_data
        
        elif msg_type == "client.resume":
            # Canonical resume field: lastClientIndex (0-based index of last message the client has)
            return all(field in message_data for field in ["chat_id", "lastClientIndex"]) and isinstance(message_data.get("lastClientIndex"), int)
        
        elif msg_type in (
            "artifact.action",
            "chat.artifact_action",
            "chat.enter_general_mode",
            "chat.start_general_chat",
            "chat.switch_workflow",
            "chat.start_workflow",
            "chat.start_workflow_batch",
        ):
            # Control commands - no additional validation needed
            return True

        # Unknown message types are invalid
        return False
        
    async def send_error(
        self,
        error_message: str,
        error_code: str = "GENERAL_ERROR",
        chat_id: str | None = None,
        extra_data: dict | None = None,
    ) -> None:
        """Send error message to UI via WebSocket"""
        data: dict = {
            "message": error_message,
            "error_code": error_code,
            "chat_id": chat_id,
        }
        if extra_data:
            data.update(extra_data)
        event_data = {
            "type": "error",
            "data": data,
            "timestamp": datetime.now(UTC).isoformat()
        }

        await self._broadcast_to_websockets(event_data, chat_id)
        logger.error("Error: %s", error_message)
        
    def _get_message_handler(self, mtype: str):
        """Get the handler function for a message type.

        Returns a handler from the extracted handlers module, or None if unknown.
        """
        return MESSAGE_HANDLERS.get(mtype)

    # ==================================================================================
    # CONNECTION MANAGEMENT METHODS
    # ==================================================================================

    async def handle_websocket(
        self,
        websocket: WebSocket,
        chat_id: str,
        user_id: str,
        workflow_name: str,
        app_id: str | None = None,
        ws_id: int | None = None,
        token_exp: int = 0,
    ) -> None:
        """Handle WebSocket connection for real-time communication with multi-workflow session support"""
        if self._owner_loop is None:
            self._owner_loop = asyncio.get_running_loop()

        # Reject new connections when at capacity (excludes same-chat_id reconnects
        # which evict the stale slot rather than consuming a new one).
        if chat_id not in self.connections and len(self.connections) >= self._max_connections:
            logger.warning(
                "WS_CONNECTION_LIMIT_REACHED limit=%d chat=%s user=%s — rejecting",
                self._max_connections,
                chat_id,
                user_id,
            )
            await websocket.close(code=1008, reason="Server connection limit reached")
            return

        # Per-user connection limit: count how many existing slots belong to this user.
        # Excludes same-chat_id reconnects (those evict the old slot, not a new one).
        if (
            self._max_connections_per_user > 0
            and user_id
            and chat_id not in self.connections
        ):
            user_connection_count = sum(
                1
                for conn in self.connections.values()
                if conn.get("user_id") == user_id
            )
            if user_connection_count >= self._max_connections_per_user:
                logger.warning(
                    "WS_USER_CONNECTION_LIMIT_REACHED limit=%d chat=%s user=%s — rejecting",
                    self._max_connections_per_user,
                    chat_id,
                    user_id,
                )
                await websocket.close(code=1008, reason="User connection limit reached")
                return

        await websocket.accept()

        # Store ws_id for session registry lookups
        if ws_id is None:
            ws_id = id(websocket)

        # Evict any stale connection for the same chat_id before registering the new one.
        # Without this, the old receive loop's finally block would delete the new connection
        # entry and leave the client in a broken state.
        if chat_id in self.connections:
            stale = self.connections[chat_id]
            stale_ws = stale.get("websocket")
            logger.warning("Evicting stale WebSocket for chat_id=%s (ws_id=%s)", chat_id, stale.get("ws_id"))
            try:
                await stale_ws.close(code=1001)
            except Exception:
                pass
            await self._cleanup_connection(chat_id)

        self.connections[chat_id] = {
            "websocket": websocket,
            "user_id": user_id,
            "workflow_name": workflow_name,
            "app_id": app_id,
            "active": True,
            "ws_id": ws_id,  # Track WebSocket ID for session switching
            "token_exp": token_exp,  # JWT expiry (unix epoch); 0 means no expiry check
            "last_received_at": time.time(),  # Updated on every inbound message for idle detection
        }
        logger.info("WS_CONNECTED chat=%s ws_id=%s", chat_id, ws_id)

        try:
            session_registry.add_workflow(
                ws_id=ws_id,
                chat_id=chat_id,
                workflow_name=str(workflow_name),
                app_id=str(app_id) if app_id is not None else "",
                user_id=str(user_id),
                auto_activate=True,
            )
        except Exception as registry_err:
            logger.warning(
                "Failed to register workflow context on websocket connect for chat %s: %s",
                chat_id,
                registry_err,
            )
        
        # H2: Start heartbeat for connection
        await self._start_heartbeat(chat_id, websocket)
        
        # H1: Initialize message queue for backpressure control
        self._message_queues[chat_id] = []

        # H4: Flush any pre-connection buffered messages (if orchestration
        # started emitting before the UI finished the handshake)
        if chat_id in self._pre_connection_buffers:
            buffered = self._pre_connection_buffers.pop(chat_id)
            if buffered:
                logger.debug("PRE_CONN_BUFFER_FLUSH count=%s chat=%s", len(buffered), chat_id)
                for msg in buffered:
                    await self._queue_message_with_backpressure(chat_id, msg)
                await self._flush_message_queue(chat_id)
        self._pre_connection_buffer_overflow_counts.pop(chat_id, None)

        # H5: Auto-resume for IN_PROGRESS chats (check status and restore chat history)
        await self._replay_run_on_connect_if_needed(chat_id, websocket, app_id)
        
        try:
            # Inbound loop: receive JSON control messages from client
            while True:
                try:
                    msg = await websocket.receive_text()
                except Exception as recv_err:
                    # Client disconnected
                    raise recv_err
                if not msg:
                    await asyncio.sleep(0.05)
                    continue
                # Update last-received timestamp for idle connection detection.
                _conn = self.connections.get(chat_id)
                if isinstance(_conn, dict):
                    _conn["last_received_at"] = time.time()
                # Enforce JWT token expiry on each inbound message.
                _exp = self.connections.get(chat_id, {}).get("token_exp", 0)
                if _exp and int(time.time()) > _exp:
                    logger.warning("WS_TOKEN_EXPIRED chat=%s — closing connection", chat_id)
                    await self._send_ws_error(websocket, "Session token has expired", "TOKEN_EXPIRED")
                    await websocket.close(code=4401, reason="Token expired")
                    break
                # Guard against oversized messages before JSON parsing.
                _max = getattr(self, "_ws_message_max_chars", None)
                if _max and len(msg) > _max:
                    logger.warning(
                        "WS_MESSAGE_TOO_LARGE chat=%s size=%d limit=%d — dropping",
                        chat_id, len(msg), _max,
                    )
                    await self._send_ws_error(websocket, "Message exceeds maximum allowed size", "MESSAGE_TOO_LARGE")
                    continue
                try:
                    data = json.loads(msg)
                except Exception:
                    logger.debug("Received non-JSON message on WS chat %s: %s", chat_id, msg[:80])
                    continue
                # H3: Validate message schema
                if not self._validate_inbound_message(data):
                    await self._send_ws_error(websocket, "Invalid message schema", "SCHEMA_VALIDATION_FAILED")
                    continue

                mtype = data.get('type') or data.get('kind')

                # Dispatch to handler (handlers receive transport as first arg)
                handler = self._get_message_handler(mtype)
                if handler:
                    try:
                        await handler(self, data, chat_id, websocket)
                    except Exception as handler_err:
                        error_code = ERROR_CODES.get(mtype, "MESSAGE_HANDLER_FAILED")
                        logger.error("Failed to process %s for chat %s: %s", mtype, chat_id, handler_err, exc_info=True)
                        await self._send_ws_error(websocket, f"{mtype} failed: {str(handler_err)}", error_code)
                # Unknown message type -> handler is None, ignore silently
        except Exception as e:
            close_code = getattr(e, "code", None)
            if isinstance(e, WebSocketDisconnect) and close_code == 1000:
                logger.debug("WS_CLOSED_NORMALLY chat=%s code=%s", chat_id, close_code)
            elif ConnectionClosed and isinstance(e, ConnectionClosed) and close_code == 1000:
                logger.debug("WS_CLOSED_NORMALLY chat=%s code=%s", chat_id, close_code)
            else:
                logger.warning("WebSocket error for chat %s: %s", chat_id, e)
        finally:
            # H1-H2: Clean up connection resources (heartbeat, message queues, etc.).
            # Pass ws_id so _cleanup_connection skips cleanup when a concurrent
            # reconnect (e.g. React StrictMode double-invoke) has already evicted
            # this WS and registered a new one under the same chat_id.
            await self._cleanup_connection(chat_id, ws_id=ws_id)
            logger.info("WS_DISCONNECTED chat=%s", chat_id)

    # NOTE: Workflow integration methods are provided by WorkflowBridgeMixin:
    # - handle_user_input_from_api
    # - _run_workflow_background
    # - pause_background_workflow
    # - send_chat_message

    # ==================================================================================
    # UI TOOL EVENT HANDLING (Companion to user input)
    # ==================================================================================

    def _get_or_create_persistence_manager(self):
        """Return cached AG2PersistenceManager instance (lazy import)."""
        pm = getattr(self, "_persistence_manager", None)
        if pm is None:
            from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager
            pm = AG2PersistenceManager()
            self._persistence_manager = pm
        return pm

    # NOTE: General-mode methods are provided by GeneralModeMixin:
    # - _ensure_general_chat_context
    # - _handle_general_agent_exchange
    # - _persist_general_message

    async def _resolve_chat_context(
        self,
        chat_id: str | None,
        *,
        pm,
        payload_workflow: str | None = None,
    ) -> tuple[str | None, str | None]:
        """Resolve app/workflow for a chat regardless of live connection."""
        if not chat_id:
            return None, payload_workflow

        app_id: str | None = None
        workflow_name: str | None = payload_workflow

        conn = self.connections.get(chat_id)
        if conn:
            raw_ent = conn.get("app_id")
            if raw_ent:
                app_id = str(raw_ent)
            if not workflow_name:
                workflow_name = conn.get("workflow_name")

        if app_id and workflow_name:
            return app_id, workflow_name

        try:
            coll = await pm._coll()
            doc = await coll.find_one({"_id": chat_id}, {"app_id": 1, "workflow_name": 1})
            if doc:
                if not app_id and doc.get("app_id") is not None:
                    app_id = str(doc.get("app_id"))
                if not workflow_name and doc.get("workflow_name"):
                    workflow_name = doc.get("workflow_name")
        except Exception as ctx_err:
            logger.debug("dY'\" [UI_TOOL] Context lookup failed for chat %s: %s", chat_id, ctx_err)

        if chat_id in self.connections:
            conn = self.connections[chat_id]
            if app_id and not conn.get("app_id"):
                conn["app_id"] = app_id
            if workflow_name and not conn.get("workflow_name"):
                conn["workflow_name"] = workflow_name

        return app_id, workflow_name

    # NOTE: The following methods are provided by mixins:
    # - WebSocketProtocolMixin: _get_next_sequence, _check_backpressure, _queue_message_with_backpressure,
    #   _flush_message_queue, _schedule_flush_retry, _start_heartbeat, _heartbeat_loop, _stop_heartbeat,
    #   _replay_run_on_connect_if_needed, _cleanup_connection
    # - WorkflowBridgeMixin: handle_user_input_from_api, _run_workflow_background, pause_background_workflow,
    #   send_chat_message
    # - GeneralModeMixin: _ensure_general_chat_context, _handle_general_agent_exchange,
    #   _persist_general_message
    # - UIToolsMixin: _persist_ui_tool_state, send_tool_call_event, wait_for_tool_call_response,
    #   submit_tool_call_response, register_derived_context_manager, unregister_derived_context_manager
    


