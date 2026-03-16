# ==============================================================================
# FILE: core/transport/simple_transport.py
# DESCRIPTION: Lean transport system for real-time UI communication
# ==============================================================================
import asyncio
import re
import json
import uuid
import traceback
import os
from typing import Dict, Any, Optional, Union, Tuple, List
from fastapi import WebSocket
from datetime import datetime, timezone
try:  # pymongo optional in some test environments
    from pymongo import ReturnDocument  # type: ignore
except Exception:  # pragma: no cover
    class ReturnDocument:  # minimal fallback so attribute exists
        AFTER = 1

# AG2 imports for event type checking
from autogen.events import BaseEvent

# Import workflow configuration for agent visibility filtering
from mozaiksai.core.workflow.workflow_manager import workflow_manager

# Enhanced logging setup
from logs.logging_config import get_core_logger

# Session manager for multi-workflow navigation
from mozaiksai.core.workflow import session_manager
from mozaiksai.core.transport.session_registry import session_registry

# Message handlers (extracted for maintainability)
from mozaiksai.core.transport.handlers import MESSAGE_HANDLERS, ERROR_CODES

# Extracted mixins for separation of concerns
from mozaiksai.core.transport.general_mode import GeneralModeMixin
from mozaiksai.core.transport.ws_protocol import WebSocketProtocolMixin
from mozaiksai.core.transport.workflow_bridge import WorkflowBridgeMixin
from mozaiksai.core.transport.ui_tools import UIToolsMixin

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
    return datetime.now(timezone.utc).isoformat()


def _extract_clean_content(message: Union[str, Dict[str, Any], Any]) -> str:
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
    - Message filtering (removes AutoGen noise)
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
        self.connections: Dict[str, Dict[str, Any]] = {}

        # AG2-aligned input request callback registry
        self._input_request_registries: Dict[str, Dict[str, Any]] = {}

    # T-series: WebSocket protocol support structures
        self._sequence_counters: Dict[str, int] = {}          # T3

        # H1-H2: Hardening features
        self._message_queues: Dict[str, List[Dict[str, Any]]] = {}  # H1
        self._heartbeat_tasks: Dict[str, asyncio.Task] = {}         # H2
        self._max_queue_size = 100
        self._heartbeat_interval = 120

        # H4: Pre-connection buffering (delivery reliability)
        self._pre_connection_buffers: Dict[str, List[Dict[str, Any]]] = {}
        self._max_pre_connection_buffer = 200
        self._scheduled_flush_tasks: Dict[str, asyncio.Task] = {}

        # UI tool response correlation
        self.pending_ui_tool_responses: Dict[str, asyncio.Future] = {}
        self._ui_tool_metadata: Dict[str, Dict[str, Any]] = {}

        # Runtime context trigger managers (per chat)
        # Used to apply declarative ui_response triggers without bespoke agents.
        self._derived_context_managers: Dict[str, Any] = {}

        # Background workflow execution (for parallel child chats)
        self._background_tasks: Dict[str, asyncio.Task] = {}
        try:
            max_parallel = int(os.environ.get("MOZAIKS_MAX_PARALLEL_WORKFLOWS", "4"))
        except Exception:
            max_parallel = 4
        self._workflow_spawn_semaphore = asyncio.Semaphore(max(1, max_parallel))

        # Usage emission fan-out (measurement only; no billing enforcement).
        try:
            from mozaiksai.core.events.unified_event_dispatcher import get_event_dispatcher

            dispatcher = get_event_dispatcher()
            dispatcher.register_handler("chat.usage_delta", self._handle_usage_delta_event)
            dispatcher.register_handler("chat.usage_summary", self._handle_usage_summary_event)
        except Exception:
            logger.debug("Usage event handler registration skipped", exc_info=True)

        self._initialized = True
        logger.info("🚀 SimpleTransport singleton initialized")
        
    async def _handle_usage_delta_event(self, payload: Dict[str, Any]) -> None:
        chat_id = payload.get("chat_id")
        if not chat_id:
            return
        try:
            await self.send_event_to_ui({"kind": "usage_delta", **payload}, str(chat_id))
        except Exception:
            logger.debug("Failed to forward usage_delta to UI", exc_info=True)

    async def _handle_usage_summary_event(self, payload: Dict[str, Any]) -> None:
        chat_id = payload.get("chat_id")
        if not chat_id:
            return
        try:
            await self.send_event_to_ui({"kind": "usage_summary", **payload}, str(chat_id))
        except Exception:
            logger.debug("Failed to forward usage_summary to UI", exc_info=True)

    # ==================================================================================
    # CONNECTION HELPERS
    # ==================================================================================

    def _get_conn_meta(self, chat_id: str) -> Dict[str, Any]:
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
        await websocket.send_json({
            "type": "chat.error",
            "data": {"message": message, "error_code": error_code},
            "timestamp": _utc_timestamp(),
        })

    # ==================================================================================
    # USER INPUT COLLECTION (Production-Ready)
    # ==================================================================================
    
    async def submit_user_input(self, input_request_id: str, user_input: str) -> bool:
        """
        Submit user input response for a pending input request.
        
        This method is called by the API endpoint when the frontend submits user input.
        """
        logger.info(f"🔍 [INPUT_SUBMIT] Looking for request_id={input_request_id} in {len(self._input_request_registries)} chat registries")
        for cid, reg in self._input_request_registries.items():
            logger.info(f"  📋 [INPUT_SUBMIT] chat={cid} has {len(reg)} pending requests: {list(reg.keys())}")
        
        # First try orchestration registry respond callback(s)
        handled = False
        ack_chat_id = None
        for chat_id, reg in list(self._input_request_registries.items()):
            respond_cb = reg.get(input_request_id)
            if respond_cb:
                logger.info(f"✅ [INPUT_SUBMIT] Found callback for {input_request_id} in chat {chat_id}")
            if respond_cb:
                try:
                    logger.info(f"🚀 [INPUT_SUBMIT] Invoking respond callback with user_input='{user_input[:50]}...'")
                    # Support both async and sync lambdas assigned by AG2
                    result = respond_cb(user_input)
                    if asyncio.iscoroutine(result):
                        await result
                    handled = True
                    ack_chat_id = chat_id
                    logger.info(f"✅ [INPUT] Respond callback invoked for request {input_request_id} (chat {chat_id})")
                except Exception as e:
                    logger.error(f"❌ [INPUT] Respond callback failed {input_request_id}: {e}", exc_info=True)
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
                    await pm.clear_pending_input_request(chat_id=ack_chat_id)
                except Exception as e:
                    logger.debug(f"Failed to clear pending input request: {e}")
            # Emit chat.input_ack for B9/B10 protocol compliance
            if ack_chat_id:
                try:
                    await self.send_event_to_ui({
                        'kind': 'input_ack',
                        'request_id': input_request_id,
                        'corr': input_request_id,
                    }, ack_chat_id)
                except Exception as e:
                    logger.warning(f"Failed to emit input_ack: {e}")
            return True
        
        logger.error(f"❌ [INPUT] No active request found for {input_request_id}")
        return False

    # ------------------------------------------------------------------
    # Orchestration registry integration
    # ------------------------------------------------------------------
    def register_orchestration_input_registry(self, chat_id: str, registry: Dict[str, Any]) -> None:
        self._input_request_registries[chat_id] = registry

    def register_input_request(self, chat_id: str, request_id: str, respond_cb: Any) -> str:
        normalized_id = str(request_id) if request_id is not None else ""
        if not normalized_id or normalized_id.lower() == "none":
            normalized_id = uuid.uuid4().hex
            logger.debug(f"Generated fallback input request id {normalized_id} for chat {chat_id}")
        if chat_id not in self._input_request_registries:
            self._input_request_registries[chat_id] = {}
        self._input_request_registries[chat_id][normalized_id] = respond_cb
        logger.debug(f"Registered input request {normalized_id} for chat {chat_id}")
        return normalized_id

    def _build_resume_signal(self, chat_id: str, request_id: str) -> str:
        """Produce a non-empty fallback message when resuming pending input requests.

        Ensures downstream ChatCompletion payloads always contain valid user content even when
        lifecycle tools resume execution without explicit text input.
        
        Note: This is an internal coordination signal for AG2 continuation. It should never
        be persisted to the database or shown in the UI as it has no semantic meaning to users.
        """
        return "[SYSTEM_RESUME_SIGNAL] Continue workflow execution after UI tool response."
    
    
    def should_show_to_user(self, agent_name: Optional[str], chat_id: Optional[str] = None) -> bool:
        """Check if a message should be shown to the user interface"""
        if not agent_name:
            return True  # Show system messages
        
        # Get the workflow type and ws_id for this chat session
        workflow_name = None
        ws_id = None
        if chat_id and chat_id in self.connections:
            workflow_name = self.connections[chat_id].get("workflow_name")
            ws_id = self.connections[chat_id].get("ws_id")
        
        # If in general mode, show all messages (bypass visual_agents filtering)
        if ws_id and session_registry.is_in_general_mode(ws_id):
            logger.debug(f"🧠 [GENERAL_MODE] Allowing message from '{agent_name}' (general mode bypass)")
            return True
        
        # If we have workflow type, use visual_agents filtering
        if workflow_name:
            try:
                config = workflow_manager.get_config(workflow_name)
                visual_agents = config.get("visual_agents")
                
                # If visual_agents is defined, only show messages from those agents
                if isinstance(visual_agents, list):
                    if not visual_agents:
                        logger.debug(f"🔍 visual_agents empty for {workflow_name}; allowing message from {agent_name}")
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
                    logger.debug(f"🔍 Backend visual_agents check: '{agent_name}' -> '{normalized_agent}' in {normalized_visual_agents} = {is_allowed}")
                    return is_allowed
            except FileNotFoundError:
                # If no specific config, default to showing the message
                pass
        
        return True

    def _sanitize_trace_content(self, content: str, *, limit: int = 800) -> Tuple[str, bool, bool]:
        """Redact likely secrets and truncate trace content before sending to UI."""
        if not isinstance(content, str):
            return str(content), False, False

        redacted = False
        value = content

        rules: List[Tuple[re.Pattern, str]] = [
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
    async def process_incoming_user_message(self, *, chat_id: str, user_id: Optional[str], content: str, source: str = 'ws') -> None:
        """Persist and forward a free-form user message into the active workflow orchestration.

        This is used by both WebSocket (user.input.submit without request_id) and
        HTTP input endpoint. It appends the message to persistence so that future
        resume operations have it, and (if an orchestration is already running)
        attempts to surface it to the user proxy agent if available.
        """
        if not content:
            return
        index: Optional[int] = None
        try:
            from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager
            pm = getattr(self, '_persistence_manager', None)
            if not pm:
                pm = AG2PersistenceManager()
                self._persistence_manager = pm
            coll = await pm._coll()  # type: ignore[attr-defined]
            now_dt = datetime.now(timezone.utc)
            bump = await coll.find_one_and_update(
                {"_id": chat_id},
                {"$inc": {"last_sequence": 1}, "$set": {"last_updated_at": now_dt}},
                return_document=ReturnDocument.AFTER,
            )
            seq = int(bump.get('last_sequence', 1)) if bump else 1
            index = seq - 1  # zero-based index for UI
            msg_doc = {
                'role': 'user',
                'name': 'user',
                'content': content,
                'timestamp': now_dt,
                'event_type': 'message.created',
                'sequence': seq,
                'source': source,
            }
            await coll.update_one({"_id": chat_id}, {"$push": {"messages": msg_doc}})
        except Exception as e:
            # Persistence failure should not block UI emission; fall back to in-memory sequence
            logger.error(f"Failed to persist user message for {chat_id}: {e}")
            try:
                # Use transport sequence counter (converted to zero-based)
                seq_fallback = self._get_next_sequence(chat_id)
                index = max(0, seq_fallback - 1)
            except Exception:
                index = 0
        # Always emit event (best-effort) even if persistence failed
        try:
            await self.send_event_to_ui({'kind': 'text', 'agent': 'user', 'content': content, 'index': index}, chat_id)
        except Exception as emit_err:
            logger.error(f"Failed to emit user message event for {chat_id}: {emit_err}")

    async def process_component_action(self, *, chat_id: str, app_id: str, component_id: str, action_type: str, action_data: dict) -> Dict[str, Any]:
        """Apply a component action to context variables and emit acknowledgement.

        Returns a structured result indicating applied changes.
        """
        conn = self.connections.get(chat_id) or {}
        context = conn.get('context')
        applied: Dict[str, Any] = {}
        try:
            # Basic pattern: if action_data has 'set': {k: v} apply to context
            sets = action_data.get('set') if isinstance(action_data, dict) else None
            if context and isinstance(sets, dict):
                for k, v in sets.items():
                    try:
                        context.set(k, v)
                        applied[k] = v
                    except Exception as ce:
                        logger.debug(f"Context set failed for {k}: {ce}")
                # Persist a lightweight snapshot of changed keys ONLY
                try:
                    from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager
                    pm = getattr(self, '_persistence_manager', None) or AG2PersistenceManager()
                    self._persistence_manager = pm
                    coll = await pm._coll()  # type: ignore[attr-defined]
                    now = datetime.now(timezone.utc)
                    snapshot_doc = {
                        'role': 'system',
                        'name': 'context',
                        'content': {'updated': applied, 'component_id': component_id, 'action_type': action_type},
                        'timestamp': now,
                        'event_type': 'context.updated',
                    }
                    await coll.update_one({"_id": chat_id, "app_id": app_id}, {"$push": {"messages": snapshot_doc}, "$set": {"last_updated_at": now}})
                except Exception as pe:
                    logger.debug(f"Context snapshot persistence failed: {pe}")
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
            logger.error(f"Component action processing failed for {chat_id}: {e}")
            raise
        
    # ==================================================================================
    # AG2 EVENT SENDING (Production)
    # ==================================================================================
    
    async def send_event_to_ui(self, event: Any, chat_id: Optional[str] = None) -> None:
        """
        Serializes and sends a raw AG2 event to the UI.
        This is the primary method for forwarding AG2 native events.
        """
        try:
            # Allow callers to provide a fully-formed transport envelope (e.g., ack.ui_tool_response)
            # without forcing another serialization pass through the dispatcher.
            if isinstance(event, dict) and 'type' in event and 'data' in event and 'kind' not in event:
                logger.info(
                    "🔁 [TRANSPORT] Forwarding pre-built envelope without re-serialization: %s",
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

            from mozaiksai.core.events.unified_event_dispatcher import get_event_dispatcher  # local import to avoid cycle
            dispatcher = get_event_dispatcher()
            workflow_name = None
            if chat_id and chat_id in self.connections:
                workflow_name = self.connections[chat_id].get('workflow_name')

            # DEBUG: Log what we're processing
            event_type = type(event).__name__ if hasattr(event, '__class__') else 'dict'
            if isinstance(event, dict):
                event_kind = event.get('kind', 'unknown')
                logger.info(f"🔍 [TRANSPORT] Processing event: type={event_type}, kind={event_kind}, chat_id={chat_id}, dict_keys={list(event.keys()) if isinstance(event, dict) else 'N/A'}")
            else:
                logger.info(f"🔍 [TRANSPORT] Processing event: type={event_type}, chat_id={chat_id}")

            envelope = dispatcher.build_outbound_event_envelope(
                raw_event=event,
                chat_id=chat_id,
                get_sequence_cb=self._get_next_sequence,
                workflow_name=workflow_name,
            )
            if not envelope:
                logger.warning(f"❌ [TRANSPORT] No envelope created for event type={event_type}")
                return
            
            logger.info(f"✅ [TRANSPORT] Envelope created successfully: type={envelope.get('type')}, has_data={bool(envelope.get('data'))}")

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
            
            # Skip visibility filtering for select_speaker, input_request, and UI tool events
            is_input_request_event = envelope_type == 'chat.input_request'
            skip_visibility_filter = (
                envelope_type == 'chat.select_speaker'
                or is_ui_tool_event
                or is_input_request_event
            )
            
            if is_ui_tool_event:
                logger.info(f"🎯 [TRANSPORT] UI tool event detected - bypassing agent visibility filter (component={envelope.get('data', {}).get('component_type')})")
            elif is_input_request_event:
                logger.info("🎯 [TRANSPORT] Input request event detected - bypassing agent visibility filter")

            # Additional filtering (agent visibility) only for BaseEvent path where needed
            agent_name = None
            if isinstance(event, BaseEvent) and hasattr(event, 'sender') and getattr(event.sender, 'name', None):  # type: ignore
                agent_name = event.sender.name  # type: ignore
            if not skip_visibility_filter and agent_name and not self.should_show_to_user(agent_name, chat_id):
                if _downgrade_to_trace(agent=str(agent_name)):
                    logger.info(f"[TRANSPORT] Downgraded non-visual message from '{agent_name}' to trace for chat {chat_id}")
                else:
                    logger.info(f"🚫 [TRANSPORT] Filtered out AG2 event from agent '{agent_name}' for chat {chat_id} (should_show_to_user=False)")
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
                        logger.info(f"[TRANSPORT] Downgraded non-visual message from '{agent_name}' to trace for chat {chat_id}")
                    else:
                        logger.info(f"🚫 [TRANSPORT] Filtered out event from agent '{agent_name}' for chat {chat_id} (visual_agents gate, should_show_to_user=False)")
                        return
                
            # Record performance metrics for tool calls (best-effort)
            try:
                et_name = type(event).__name__
                if any(token in et_name for token in ("Tool", "Function", "Call")):
                    tool_name = getattr(event, "tool_name", None)
                    if isinstance(tool_name, str) and tool_name.strip():
                        try:
                            from mozaiksai.core.observability.performance_manager import get_performance_manager
                            perf = await get_performance_manager()
                            await perf.record_tool_call(chat_id or "unknown", tool_name.strip(), True)
                        except Exception:
                            pass
            except Exception:
                pass

            # Check for suppression flag from derived context hooks
            if envelope and isinstance(envelope, dict):
                data_payload = envelope.get('data')
                if isinstance(data_payload, dict) and data_payload.get('_mozaiks_hide'):
                    logger.info(f"🚫 [TRANSPORT] Suppressing hidden message (derived context trigger) for chat {chat_id}: {data_payload.get('content', 'no content')[:100]}")
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
                    logger.info(
                        "🌊 [STREAM] Chunking chat.text → stream_chunk + stream_end "
                        "agent=%s chat_id=%s len=%d",
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

            logger.info(f"📤 [TRANSPORT] Sending envelope: type={envelope.get('type')}, chat_id={chat_id}")
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
                        except Exception:
                            pass
                        asyncio.create_task(dispatcher.emit('chat.run_complete', dispatch_payload))
            except Exception:
                pass
        except Exception as e:
            logger.error(f"❌ Failed to serialize or send UI event: {e}\n{traceback.format_exc()}")

    def _extract_clean_content(self, message: Union[str, Dict[str, Any], Any]) -> str:
        """Instance wrapper around the module-level cleaner."""
        return _extract_clean_content(message)

    async def _emit_text_as_chunks(
        self,
        content: str,
        agent_name: str,
        chat_id: str,
        stream_id: str,
    ) -> None:
        """Split *content* into word-level tokens and emit each as chat.stream_chunk.

        Emitting one token per WebSocket message lets the browser render each
        piece as it arrives, producing the typewriter effect. A minimal async
        yield between chunks ensures React 18's automatic batching doesn't
        collapse all updates into a single render.  The caller is responsible
        for sending chat.stream_end afterwards.
        """
        import re as _re
        # Word + trailing whitespace so spaces are preserved between tokens
        tokens = _re.findall(r'\S+\s*', content)
        if not tokens:
            tokens = [content]
        for seq, token in enumerate(tokens):
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
            # Without this, React 18's automatic batching may collapse all
            # state updates into a single render, defeating the typewriter effect.
            if seq < len(tokens) - 1:  # No delay after the last chunk
                await asyncio.sleep(0.015)  # 15ms between chunks

    async def _broadcast_to_websockets(self, event_data: Dict[str, Any], target_chat_id: Optional[str] = None) -> None:
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
                    logger.warning(f"🧹 Dropped {overflow} pre-connection buffered messages for {target_chat_id}")
                logger.debug(f"🕑 Buffered pre-connection message for {target_chat_id} (size={len(buf)})")
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

    def _serialize_ag2_events(self, obj: Any, _seen: Optional[set[int]] = None, _depth: int = 0) -> Any:
        """Convert AG2 event objects to JSON-serializable format."""
        if _seen is None:
            _seen = set()
        try:
            # Lazy import so absence of autogen doesn't break app start.
            try:
                from autogen.events.agent_events import InputRequestEvent  # type: ignore
            except Exception:  # pragma: no cover - autogen optional
                InputRequestEvent = tuple()  # type: ignore

            # Optional tool events (some versions place them elsewhere)
            ToolResponseEvent = None  # default
            for mod_path in [
                "autogen.events.tool_events",
                "autogen.events.agent_events",  # fallback if class relocated
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

            # Specific AG2 event shapes
            def _extract_sender(o):
                s = getattr(o, "sender", None)
                try:
                    if s is not None and hasattr(s, "name"):
                        return getattr(s, "name")
                except Exception:
                    pass
                return self._stringify_unknown(s)

            def _extract_recipient(o):
                r = getattr(o, "recipient", None)
                try:
                    if r is not None and hasattr(r, "name"):
                        return getattr(r, "name")
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

    async def _handle_artifact_action(self, event: Dict[str, Any], chat_id: str, websocket) -> None:
        """
        Handle artifact action events from frontend (launch_workflow, update_state, etc.).
        
        Args:
            event: Event data with type='chat.artifact_action' and data payload
            chat_id: Current chat/session ID
            websocket: WebSocket connection for response
        """
        data = event.get("data", {})
        action = data.get("action")
        payload = data.get("payload", {})
        artifact_id = data.get("artifact_id")
        
        conn_meta = self.connections.get(chat_id, {})
        app_id = conn_meta.get("app_id")
        user_id = conn_meta.get("user_id")
        
        if not app_id or not user_id:
            logger.error(f"❌ Missing app_id or user_id for artifact action in chat {chat_id}")
            return
        
        # Route: launch_workflow (pause current, create new session)
        if action == "launch_workflow":
            target_workflow = payload.get("workflow_name")
            if not target_workflow:
                logger.warning(f"⚠️ Missing workflow_name in launch_workflow action")
                return
            
            logger.info(f"🚀 Launching workflow {target_workflow} from chat {chat_id}")
            
        # Validate pack prerequisites before launching
            from mozaiksai.core.workflow.pack.gating import validate_pack_prereqs

            pm = self._get_or_create_persistence_manager()
            is_valid, error_msg = await validate_pack_prereqs(
                app_id=str(app_id),
                user_id=str(user_id),
                workflow_name=str(target_workflow),
                persistence=pm,
            )
            
            if not is_valid:
                logger.warning(f"⚠️ Prerequisite validation failed for {target_workflow}: {error_msg}")
                await websocket.send_json({
                    "type": "chat.prereq_blocked",
                    "data": {
                        "workflow_name": target_workflow,
                        "message": error_msg or "Prerequisites not met",
                        "error_code": "WORKFLOW_PREREQS_NOT_MET"
                    },
                    "chat_id": chat_id,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                })
                return
            
            # Create new session and artifact (old session stays IN_PROGRESS)
            new_session = await session_manager.create_workflow_session(
                app_id, user_id, target_workflow
            )
            artifact = await session_manager.create_artifact_instance(
                app_id,
                target_workflow,
                payload.get("artifact_type", "ActionPlan")
            )
            await session_manager.attach_artifact_to_session(
                new_session["_id"], artifact["_id"], app_id
            )
            
            logger.info(f"✅ Created new session {new_session['_id']} with artifact {artifact['_id']}")
            
            # Notify frontend to navigate to new chat
            await websocket.send_json({
                "type": "chat.navigate",
                "data": {
                    "chat_id": new_session["_id"],
                    "workflow_name": target_workflow,
                    "artifact_instance_id": artifact["_id"],
                    "app_id": app_id
                },
                "correlation_id": event.get("correlation_id"),
                "timestamp": datetime.now(timezone.utc).isoformat()
            })
            return
        
        # Route: update_state (partial artifact state updates)
        if action == "update_state" and artifact_id:
            state_updates = payload.get("state_updates", {})
            if not state_updates:
                logger.warning(f"⚠️ Empty state_updates in update_state action")
                return
            
            await session_manager.update_artifact_state(
                artifact_id, app_id, state_updates
            )
            
            logger.info(f"✅ Updated artifact state for {artifact_id}: {list(state_updates.keys())}")
            
            # Broadcast state update to all connections for this artifact
            await websocket.send_json({
                "type": "artifact.state.updated",
                "data": {
                    "artifact_id": artifact_id,
                    "state_delta": state_updates
                },
                "chat_id": chat_id,
                "timestamp": datetime.now(timezone.utc).isoformat()
            })
            return
        
        # Route: other actions (forward to agent as tool_call or handle directly)
        logger.info(f"🔄 Artifact action {action} received for chat {chat_id}")
        # Future: route to agent or handle other action types
        await websocket.send_json({
            "type": "ack.artifact_action",
            "data": {
                "action": action,
                "status": "received"
            },
            "chat_id": chat_id,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })

    async def _handle_resume_request(self, chat_id: str, last_client_index: int, websocket) -> None:
        """Resume protocol aligned with AG2 GroupChat resume semantics.

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

        This mirrors AG2's requirement that the *messages array* is the source
        of truth for preparing agents via GroupChatManager.resume, while giving
        the WebSocket consumer a minimal, deterministic replay mechanism.
        """
        try:
            conn_meta = self.connections.get(chat_id) or {}
            app_id = conn_meta.get('app_id')
            startup_mode = None
            if not app_id:
                raise RuntimeError("Missing app_id for resume")

            workflow_name = conn_meta.get('workflow_name')
            if workflow_name:
                try:
                    from mozaiksai.core.workflow.workflow_manager import workflow_manager

                    cfg = workflow_manager.get_config(str(workflow_name)) or {}
                    startup_mode = cfg.get('startup_mode')
                except Exception:
                    startup_mode = None

            # Use the AG2-aligned resumer so visibility filtering and UI tool replay
            # semantics stay consistent with live events (no leaking hidden agents).
            from mozaiksai.core.transport.resume_groupchat import GroupChatResumer

            resumer = GroupChatResumer()
            summary = await resumer.handle_resume_request(
                chat_id=str(chat_id),
                app_id=str(app_id),
                last_client_index=int(last_client_index),
                send_event=self.send_event_to_ui,
                startup_mode=startup_mode,
            )

            # Real-time sequence continuity: do not reduce existing counter.
            last_idx_sent = summary.get("last_message_index") if isinstance(summary, dict) else None
            if isinstance(last_idx_sent, int):
                existing_seq = self._sequence_counters.get(chat_id, 0)
                if existing_seq < last_idx_sent + 1:
                    self._sequence_counters[chat_id] = last_idx_sent + 1

            logger.info(
                "✅ Resume complete chat=%s replayed=%s missing_from>%s now_at_index=%s total=%s",
                chat_id,
                (summary.get("replayed_messages") if isinstance(summary, dict) else None),
                last_client_index,
                last_idx_sent,
                (summary.get("total_messages") if isinstance(summary, dict) else None),
            )
        except Exception as e:
            logger.error(f"❌ Resume failed chat={chat_id}: {e}")
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
        
        elif msg_type == "ui_tool_response":
            # UI tool response from frontend (Approve/Cancel/Submit buttons)
            # Must have ui_tool_id or eventId to correlate with pending wait_for_ui_tool_response
            return ("ui_tool_id" in message_data or "eventId" in message_data)
        
        elif msg_type == "client.resume":
            # Canonical resume field: lastClientIndex (0-based index of last message the client has)
            return all(field in message_data for field in ["chat_id", "lastClientIndex"]) and isinstance(message_data.get("lastClientIndex"), int)
        
        elif msg_type in (
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
        chat_id: Optional[str] = None
    ) -> None:
        """Send error message to UI via WebSocket"""
        event_data = {
            "type": "error",
            "data": {
                "message": error_message,
                "error_code": error_code,
                "chat_id": chat_id
            },
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        await self._broadcast_to_websockets(event_data, chat_id)
        logger.error(f"❌ Error: {error_message}")
        
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
        app_id: Optional[str] = None,
        ws_id: Optional[int] = None
    ) -> None:
        """Handle WebSocket connection for real-time communication with multi-workflow session support"""
        await websocket.accept()
        
        # Store ws_id for session registry lookups
        if ws_id is None:
            ws_id = id(websocket)
        
        self.connections[chat_id] = {
            "websocket": websocket,
            "user_id": user_id,
            "workflow_name": workflow_name,
            "app_id": app_id,
            "active": True,
            "ws_id": ws_id,  # Track WebSocket ID for session switching
        }
        logger.info(f"🔌 WebSocket connected for chat_id: {chat_id} (ws_id={ws_id})")
        
        # H2: Start heartbeat for connection
        await self._start_heartbeat(chat_id, websocket)
        
        # H1: Initialize message queue for backpressure control
        self._message_queues[chat_id] = []

        # H4: Flush any pre-connection buffered messages (if orchestration
        # started emitting before the UI finished the handshake)
        if chat_id in self._pre_connection_buffers:
            buffered = self._pre_connection_buffers.pop(chat_id)
            if buffered:
                logger.info(f"📤 Flushing {len(buffered)} pre-connection buffered messages for {chat_id}")
                for msg in buffered:
                    await self._queue_message_with_backpressure(chat_id, msg)
                await self._flush_message_queue(chat_id)

        # H5: Auto-resume for IN_PROGRESS chats (check status and restore chat history)
        await self._auto_resume_if_needed(chat_id, websocket, app_id)
        
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
                try:
                    data = json.loads(msg)
                except Exception:
                    logger.debug(f"⚠️ Received non-JSON message on WS chat {chat_id}: {msg[:80]}")
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
                        logger.error(f"Failed to process {mtype} for chat {chat_id}: {handler_err}")
                        await self._send_ws_error(websocket, f"{mtype} failed: {str(handler_err)}", error_code)
                # Unknown message type -> handler is None, ignore silently
        except Exception as e:
            logger.warning(f"WebSocket error for chat {chat_id}: {e}")
        finally:
            # H1-H2: Clean up connection resources (heartbeat, message queues, etc.)
            await self._cleanup_connection(chat_id)
            logger.info(f"🔌 WebSocket disconnected for chat_id: {chat_id}")

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
        chat_id: Optional[str],
        *,
        pm,
        payload_workflow: Optional[str] = None,
    ) -> Tuple[Optional[str], Optional[str]]:
        """Resolve app/workflow for a chat regardless of live connection."""
        if not chat_id:
            return None, payload_workflow

        app_id: Optional[str] = None
        workflow_name: Optional[str] = payload_workflow

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
            logger.debug(f"dY'\" [UI_TOOL] Context lookup failed for chat {chat_id}: {ctx_err}")

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
    #   _auto_resume_if_needed, _cleanup_connection
    # - WorkflowBridgeMixin: handle_user_input_from_api, _run_workflow_background, pause_background_workflow,
    #   send_chat_message
    # - GeneralModeMixin: _ensure_general_chat_context, _handle_general_agent_exchange,
    #   _persist_general_message
    # - UIToolsMixin: _persist_ui_tool_state, send_ui_tool_event, wait_for_ui_tool_response,
    #   submit_ui_tool_response, register_derived_context_manager, unregister_derived_context_manager
    


