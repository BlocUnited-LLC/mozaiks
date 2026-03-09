# ==============================================================================
# FILE: core/transport/simple_transport.py
# DESCRIPTION: Lean transport system for real-time UI communication
# ==============================================================================
import logging
import asyncio
import re
import json
import uuid
import traceback
import os
import importlib
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

# Runtime extensions (workflow-declared lifecycle hooks)
from mozaiksai.core.runtime.extensions import get_workflow_lifecycle_hooks

# Get our enhanced loggers
logger = get_core_logger("simple_transport")


def _load_general_agent_service():
    """Load the non-AG2 capability executor used for "general" mode.

    Core transport must remain workflow-agnostic, so we resolve this via a module
    path rather than importing workflow-specific code directly.
    """

    module_path = os.getenv("MOZAIKS_GENERAL_AGENT_MODULE", "core.capabilities.simple_llm")
    factory_name = os.getenv("MOZAIKS_GENERAL_AGENT_FACTORY", "get_general_capability_service")
    try:
        module = importlib.import_module(module_path)
        factory = getattr(module, factory_name, None)
        if callable(factory):
            return factory()
        logger.debug(
            "General agent factory not callable",
            extra={"module": module_path, "factory": factory_name},
        )
    except Exception as exc:
        logger.debug(
            "General agent service unavailable",
            extra={"module": module_path, "factory": factory_name, "error": str(exc)},
        )
    return None


def _load_platform_build_lifecycle() -> dict:
    """Load platform-level build lifecycle hooks from an optional external module.

    The module path is resolved from the environment variable
    ``MOZAIKS_PLATFORM_BUILD_LIFECYCLE_MODULE``.  If the variable is unset,
    the module cannot be imported, or the expected factory is missing, the
    function silently returns a dict whose values are all ``None``.

    Returns a dict with keys:
        is_build_workflow, emit_build_started, emit_build_completed, emit_build_failed
    """
    empty: dict = {
        "is_build_workflow": None,
        "emit_build_started": None,
        "emit_build_completed": None,
        "emit_build_failed": None,
    }
    module_path = os.getenv("MOZAIKS_PLATFORM_BUILD_LIFECYCLE_MODULE", "")
    if not module_path:
        return empty
    try:
        mod = importlib.import_module(module_path)
        result = dict(empty)
        for key in empty:
            val = getattr(mod, key, None)
            if callable(val):
                result[key] = val
        return result
    except Exception as exc:
        logger.debug(f"Platform build lifecycle unavailable: {exc}")
        return empty


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


# Module-level content cleaner to allow reuse without constructing SimpleTransport
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

class SimpleTransport:
    """
    Lean transport system focused solely on real-time UI communication.
    
    Features:
    - Message filtering (removes AutoGen noise)
    - WebSocket connection management
    - Event forwarding to the UI
    - Thread-safe singleton pattern
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
                    agent_name = data_payload.get('agent') or data_payload.get('agent_name')
                    if not agent_name and isinstance(event, dict):
                        agent_name = event.get('agent') or event.get('agent_name')
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
        try:
            if obj is None:
                return ""
            if isinstance(obj, (str, int, float, bool)):
                return str(obj)
            # Try JSON first with default=str to preserve structure
            return json.dumps(obj, default=str)
        except Exception:
            try:
                return str(obj)
            except Exception:
                return "<unserializable>"

    def _serialize_ag2_events(self, obj: Any) -> Any:
        """Convert AG2 event objects to JSON-serializable format."""
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

            # Dict / list recursive handling
            if isinstance(obj, dict):
                return {k: self._serialize_ag2_events(v) for k, v in obj.items()}
            if isinstance(obj, (list, tuple, set)):
                return [self._serialize_ag2_events(v) for v in list(obj)]

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
                public_attrs[name] = self._serialize_ag2_events(value)

            if public_attrs:
                public_attrs["_ag2_event_type"] = cls_name
                return public_attrs

            # Fallback textual representation
            return self._stringify_unknown(obj)
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
            if not app_id:
                raise RuntimeError("Missing app_id for resume")

            # Use the AG2-aligned resumer so visibility filtering and UI tool replay
            # semantics stay consistent with live events (no leaking hidden agents).
            from mozaiksai.core.transport.resume_groupchat import GroupChatResumer

            resumer = GroupChatResumer()
            summary = await resumer.handle_resume_request(
                chat_id=str(chat_id),
                app_id=str(app_id),
                last_client_index=int(last_client_index),
                send_event=self.send_event_to_ui,
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
            "chat.enter_general_mode",
            "chat.start_general_chat",
            "chat.switch_workflow",
            "chat.start_workflow",
            "chat.start_workflow_batch",
        ):
            # Mode switching commands - no additional validation needed
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
                    await websocket.send_json({
                        "type": "chat.error",
                        "data": {
                            "message": "Invalid message schema",
                            "error_code": "SCHEMA_VALIDATION_FAILED"
                        },
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    })
                    continue

                mtype = data.get('type') or data.get('kind')
                # Handle user input submission (alternative to REST endpoint)
                if mtype in ("user.input.submit", "user_input_submit"):
                    req_id = data.get('input_request_id') or data.get('request_id')
                    text = (data.get('text') or data.get('user_input') or "").strip()
                    ws_id = self.connections.get(chat_id, {}).get("ws_id")
                    ui_context_payload = data.get("context") or data.get("ui_context") or {}
                    if not isinstance(ui_context_payload, dict):
                        ui_context_payload = {}

                    logger.info(f"📥 [INPUT] Received user.input.submit: chat={chat_id}, req_id={req_id}, text_len={len(text)}, ws_id={ws_id}")

                    is_general_mode = bool(ws_id and session_registry.is_in_general_mode(ws_id))
                    logger.info(f"🔍 [INPUT] Mode check: is_general={is_general_mode}, has_req_id={bool(req_id)}")

                    if not req_id and is_general_mode:
                        if not text:
                            await websocket.send_json({
                                    "type": "chat.error",
                                    "data": {
                                        "message": "Message cannot be empty in general mode",
                                        "error_code": "GENERAL_MODE_EMPTY_MESSAGE"
                                    },
                                    "timestamp": datetime.now(timezone.utc).isoformat()
                                })
                            continue
                        try:
                            await self._handle_general_agent_exchange(
                                chat_id=chat_id,
                                ws_id=ws_id,
                                user_message=text,
                                ui_context=ui_context_payload,
                            )
                            await websocket.send_json({
                                "type": "chat.input_ack",
                                "data": {"chat_id": chat_id, "status": "accepted"},
                                "timestamp": datetime.now(timezone.utc).isoformat()
                            })
                        except Exception as general_err:
                            logger.error(f"Failed to process general-mode message for {chat_id}: {general_err}")
                            await websocket.send_json({
                                "type": "chat.error",
                                "data": {
                                    "message": "General mode is unavailable right now. Please try again.",
                                    "error_code": "GENERAL_MODE_FAILED"
                                },
                                "timestamp": datetime.now(timezone.utc).isoformat()
                            })
                        continue

                    if req_id:
                        # Treat as response to AG2 InputRequestEvent
                        logger.info(f"🎯 [INPUT] Routing to submit_user_input for AG2 InputRequestEvent: req_id={req_id}")
                        try:
                            ok = await self.submit_user_input(req_id, text)
                            logger.info(f"✅ [INPUT] submit_user_input returned: {ok} for req_id={req_id}")
                            await websocket.send_json({
                                "type": "ack.input",
                                "data": {"input_request_id": req_id, "status": "accepted" if ok else "rejected"},
                                "timestamp": datetime.now(timezone.utc).isoformat()
                            })
                        except Exception as ie:
                            logger.error(f"❌ Failed to process inbound user input {req_id}: {ie}", exc_info=True)
                    else:
                        # Free-form user message (no pending request). Persist & feed to orchestrator.
                        try:
                            target_chat_id = chat_id
                            try:
                                if ws_id:
                                    active_ctx = session_registry.get_active_workflow(ws_id)
                                    if active_ctx and getattr(active_ctx, "chat_id", None):
                                        target_chat_id = str(active_ctx.chat_id)
                            except Exception:
                                target_chat_id = chat_id
                            await self.process_incoming_user_message(
                                chat_id=target_chat_id,
                                user_id=self.connections.get(target_chat_id, {}).get('user_id') or self.connections.get(chat_id, {}).get('user_id'),
                                content=text,
                                source='ws'
                            )
                            await websocket.send_json({
                                "type": "chat.input_ack",
                                "data": {"chat_id": target_chat_id, "status": "accepted"},
                                "timestamp": datetime.now(timezone.utc).isoformat()
                            })
                        except Exception as e:
                            logger.error(f"Failed to process free-form user message for {chat_id}: {e}")
                            await websocket.send_json({
                                "type": "chat.error",
                                "data": {"message": "User message failed", "error_code": "USER_MESSAGE_FAILED"},
                                "timestamp": datetime.now(timezone.utc).isoformat()
                            })
                    continue
                
                # Handle UI tool response submission (Approve/Cancel/Submit buttons from frontend)
                if mtype == "ui_tool_response":
                    event_id = data.get('eventId') or data.get('ui_tool_id')
                    response_data = data.get('response', {})
                    if event_id:
                        try:
                            ok = await self.submit_ui_tool_response(event_id, response_data)
                            logger.info(f"✅ UI tool response received for event {event_id}: {ok}")
                            await websocket.send_json({
                                "type": "ack.ui_tool_response",
                                "data": {"eventId": event_id, "status": "accepted" if ok else "rejected"},
                                "timestamp": datetime.now(timezone.utc).isoformat()
                            })
                        except Exception as uie:
                            logger.error(f"❌ Failed to process UI tool response {event_id}: {uie}")
                            await websocket.send_json({
                                "type": "chat.error",
                                "data": {"message": "UI tool response failed", "error_code": "UI_TOOL_RESPONSE_FAILED"},
                                "timestamp": datetime.now(timezone.utc).isoformat()
                            })
                    continue
                
                # Handle artifact action (launch_workflow, update_state, etc.)
                if mtype == "chat.artifact_action":
                    try:
                        await self._handle_artifact_action(data, chat_id, websocket)
                    except Exception as ae:
                        logger.error(f"❌ Failed to process artifact action for chat {chat_id}: {ae}")
                        await websocket.send_json({
                            "type": "chat.error",
                            "data": {"message": "Artifact action failed", "error_code": "ARTIFACT_ACTION_FAILED"},
                            "timestamp": datetime.now(timezone.utc).isoformat()
                        })
                    continue

                # Handle workflow switching (manual UI-driven)
                if mtype == "chat.switch_workflow":
                    try:
                        target_chat_id = data.get("chat_id")
                        frontend_context = data.get("frontend_context")  # UI-scoped context from host app
                        
                        if not target_chat_id:
                            raise ValueError("chat_id required for workflow switch")
                        
                        ws_id = self.connections.get(chat_id, {}).get("ws_id")
                        if not ws_id:
                            raise ValueError("WebSocket ID not found in connection metadata")
                        
                        # Store frontend context in connection metadata for this chat
                        if frontend_context and isinstance(frontend_context, dict):
                            if target_chat_id not in self.connections:
                                self.connections[target_chat_id] = {}
                            self.connections[target_chat_id]["frontend_context"] = frontend_context
                            logger.info(f"📋 Stored frontend context for {target_chat_id}: {list(frontend_context.keys())}")
                        
                        # Switch workflow context in registry
                        active_context = session_registry.switch_workflow(ws_id, target_chat_id)
                        
                        if not active_context:
                            raise ValueError(f"Workflow {target_chat_id} not found or already completed")
                        
                        logger.info(f"🔄 Switched from {chat_id} to {target_chat_id} (ws_id={ws_id})")
                        
                        # Notify frontend of successful switch
                        await websocket.send_json({
                            "type": "chat.context_switched",
                            "data": {
                                "from_chat_id": chat_id,
                                "to_chat_id": target_chat_id,
                                "workflow_name": active_context.workflow_name,
                                "artifact_id": active_context.artifact_id,
                                "app_id": active_context.app_id
                            },
                            "timestamp": datetime.now(timezone.utc).isoformat()
                        })
                    except Exception as se:
                        logger.error(f"❌ Failed to switch workflow: {se}")
                        await websocket.send_json({
                            "type": "chat.error",
                            "data": {"message": f"Workflow switch failed: {str(se)}", "error_code": "SWITCH_WORKFLOW_FAILED"},
                            "timestamp": datetime.now(timezone.utc).isoformat()
                        })
                    continue
                
                # Handle general mode (pause all workflows; non-AG2 capability execution)
                if mtype == "chat.enter_general_mode":
                    try:
                        ws_id = self.connections.get(chat_id, {}).get("ws_id")
                        
                        if not ws_id:
                            raise ValueError("WebSocket ID not found in connection metadata")
                        
                        # Pause all workflows
                        session_registry.enter_general_mode(ws_id)
                        general_ctx = await self._ensure_general_chat_context(chat_id=chat_id)
                        general_chat_id = general_ctx.get("chat_id")
                        
                        logger.info(
                            f"💬 Entered general mode (ws_id={ws_id}, general_chat={general_chat_id})"
                        )

                        # Notify frontend
                        await websocket.send_json({
                            "type": "chat.mode_changed",
                            "data": {
                                "mode": "general",
                                "general_chat_id": general_ctx.get("chat_id"),
                                "general_chat_label": general_ctx.get("label"),
                                "general_chat_sequence": general_ctx.get("sequence"),
                            },
                            "timestamp": datetime.now(timezone.utc).isoformat()
                        })
                    except Exception as ge:
                        logger.error(f"❌ Failed to enter general mode: {ge}")
                        await websocket.send_json({
                            "type": "chat.error",
                            "data": {"message": f"General mode failed: {str(ge)}", "error_code": "GENERAL_MODE_FAILED"},
                            "timestamp": datetime.now(timezone.utc).isoformat()
                        })
                    continue
                
                # Handle starting a new workflow from UI button

                if mtype == "chat.start_general_chat":
                    try:
                        ws_id = self.connections.get(chat_id, {}).get("ws_id")
                        if not ws_id:
                            raise ValueError("WebSocket ID not found in connection metadata")
                        session_registry.enter_general_mode(ws_id)
                        general_ctx = await self._ensure_general_chat_context(chat_id=chat_id, force_new=True)

                        await websocket.send_json({
                            "type": "chat.general_session_created",
                            "data": {
                                "general_chat_id": general_ctx.get("chat_id"),
                                "general_chat_label": general_ctx.get("label"),
                                "general_chat_sequence": general_ctx.get("sequence"),
                            },
                            "timestamp": datetime.now(timezone.utc).isoformat()
                        })
                        logger.info(
                            f"🆕 Started new general chat session {general_ctx.get('chat_id')} (ws_id={ws_id})"
                        )
                    except Exception as gc_err:
                        logger.error(f"❌ Failed to start new general chat: {gc_err}")
                        await websocket.send_json({
                            "type": "chat.error",
                            "data": {
                                "message": f"General chat creation failed: {gc_err}",
                                "error_code": "GENERAL_CHAT_CREATE_FAILED",
                            },
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        })
                    continue
                if mtype == "chat.start_workflow":
                    try:
                        target_workflow = data.get("workflow_name")
                        initial_message = data.get("initial_message") or data.get("message")
                        auto_run = bool(data.get("auto_run", True))
                        initial_agent_name_override = data.get("initial_agent") or data.get("initial_agent_name")
                        frontend_context = data.get("frontend_context")  # UI-scoped context from host app
                        
                        if not target_workflow:
                            raise ValueError("workflow_name required")
                        
                        ws_id = self.connections.get(chat_id, {}).get("ws_id")
                        ent_id = self.connections.get(chat_id, {}).get("app_id")
                        usr_id = self.connections.get(chat_id, {}).get("user_id")
                        
                        if not ws_id or not ent_id or not usr_id:
                            raise ValueError("Missing connection metadata")

                        # Enforce pack prerequisites before starting/spawning.
                        from mozaiksai.core.workflow.pack.gating import validate_pack_prereqs
                        pm = self._get_or_create_persistence_manager()
                        ok, prereq_error = await validate_pack_prereqs(
                            app_id=str(ent_id),
                            user_id=str(usr_id),
                            workflow_name=str(target_workflow),
                            persistence=pm,
                        )
                        if not ok:
                            await websocket.send_json(
                                {
                                    "type": "chat.prereq_blocked",
                                    "data": {
                                        "workflow_name": str(target_workflow),
                                        "message": prereq_error or "Prerequisites not met",
                                        "error_code": "WORKFLOW_PREREQS_NOT_MET",
                                    },
                                    "chat_id": chat_id,
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                }
                            )
                            continue
                        
                        # Create new chat session
                        new_chat_id = f"chat_{target_workflow}_{uuid.uuid4().hex[:8]}"

                        # Ensure Mongo chat session exists so downstream persistence works
                        await pm.create_chat_session(
                            chat_id=new_chat_id,
                            app_id=str(ent_id),
                            workflow_name=str(target_workflow),
                            user_id=str(usr_id),
                        )
                        
                        # Store frontend context in connection metadata for this chat
                        if frontend_context and isinstance(frontend_context, dict):
                            if new_chat_id not in self.connections:
                                self.connections[new_chat_id] = {}
                            self.connections[new_chat_id]["frontend_context"] = frontend_context
                            logger.info(f"📋 Stored frontend context for new workflow {new_chat_id}: {list(frontend_context.keys())}")
                        
                        # Register in session registry (pauses current workflow)
                        session_registry.add_workflow(
                            ws_id=ws_id,
                            chat_id=new_chat_id,
                            workflow_name=target_workflow,
                            app_id=ent_id,
                            user_id=usr_id,
                            auto_activate=True
                        )
                        
                        logger.info(f"🚀 Started new workflow {target_workflow} (chat_id={new_chat_id}, ws_id={ws_id})")
                        
                        # Notify frontend
                        await websocket.send_json({
                            "type": "chat.workflow_started",
                            "data": {
                                "chat_id": new_chat_id,
                                "workflow_name": target_workflow,
                                "app_id": ent_id,
                                "user_id": usr_id
                            },
                            "timestamp": datetime.now(timezone.utc).isoformat()
                        })

                        # Optionally start execution immediately (parallel-safe)
                        if auto_run:
                            self._background_tasks[new_chat_id] = asyncio.create_task(
                                self._run_workflow_background(
                                    chat_id=new_chat_id,
                                    workflow_name=str(target_workflow),
                                    app_id=str(ent_id),
                                    user_id=str(usr_id),
                                    ws_id=ws_id,
                                    initial_message=str(initial_message) if isinstance(initial_message, str) and initial_message.strip() else None,
                                    initial_agent_name_override=str(initial_agent_name_override) if isinstance(initial_agent_name_override, str) and initial_agent_name_override.strip() else None,
                                )
                            )
                    except Exception as we:
                        logger.error(f"❌ Failed to start workflow: {we}")
                        await websocket.send_json({
                            "type": "chat.error",
                            "data": {"message": f"Workflow start failed: {str(we)}", "error_code": "START_WORKFLOW_FAILED"},
                            "timestamp": datetime.now(timezone.utc).isoformat()
                        })
                    continue

                # Start multiple workflows concurrently (batch)
                if mtype == "chat.start_workflow_batch":
                    try:
                        runs = data.get("runs")
                        activate_first = bool(data.get("activate_first", False))
                        auto_run = bool(data.get("auto_run", True))

                        ws_id = self.connections.get(chat_id, {}).get("ws_id")
                        ent_id = self.connections.get(chat_id, {}).get("app_id")
                        usr_id = self.connections.get(chat_id, {}).get("user_id")
                        if not ws_id or not ent_id or not usr_id:
                            raise ValueError("Missing connection metadata")

                        if not isinstance(runs, list) or not runs:
                            raise ValueError("runs must be a non-empty list")

                        pm = self._get_or_create_persistence_manager()
                        from mozaiksai.core.workflow.pack.gating import validate_pack_prereqs

                        started: List[Dict[str, Any]] = []
                        blocked: List[Dict[str, Any]] = []
                        for i, run in enumerate(runs):
                            if not isinstance(run, dict):
                                raise ValueError("Each run must be an object")
                            target_workflow = run.get("workflow_name")
                            if not target_workflow:
                                raise ValueError("Each run requires workflow_name")

                            initial_message = run.get("initial_message") or run.get("message") or run.get("prompt")
                            initial_agent_name_override = run.get("initial_agent") or run.get("initial_agent_name")
                            label = run.get("label")

                            ok, prereq_error = await validate_pack_prereqs(
                                app_id=str(ent_id),
                                user_id=str(usr_id),
                                workflow_name=str(target_workflow),
                                persistence=pm,
                            )
                            if not ok:
                                blocked.append(
                                    {
                                        "workflow_name": str(target_workflow),
                                        "reason": prereq_error or "Prerequisites not met",
                                    }
                                )
                                await websocket.send_json(
                                    {
                                        "type": "chat.prereq_blocked",
                                        "data": {
                                            "workflow_name": str(target_workflow),
                                            "message": prereq_error or "Prerequisites not met",
                                            "error_code": "WORKFLOW_PREREQS_NOT_MET",
                                        },
                                        "chat_id": chat_id,
                                        "timestamp": datetime.now(timezone.utc).isoformat(),
                                    }
                                )
                                continue

                            new_chat_id = f"chat_{target_workflow}_{uuid.uuid4().hex[:8]}"

                            await pm.create_chat_session(
                                chat_id=new_chat_id,
                                app_id=str(ent_id),
                                workflow_name=str(target_workflow),
                                user_id=str(usr_id),
                            )

                            # Register but keep paused by default so we don't thrash the active chat
                            session_registry.add_workflow(
                                ws_id=ws_id,
                                chat_id=new_chat_id,
                                workflow_name=str(target_workflow),
                                app_id=str(ent_id),
                                user_id=str(usr_id),
                                auto_activate=bool(activate_first and i == 0),
                            )

                            started.append(
                                {
                                    "chat_id": new_chat_id,
                                    "workflow_name": str(target_workflow),
                                    "app_id": str(ent_id),
                                    "user_id": str(usr_id),
                                    "label": str(label) if label else None,
                                }
                            )

                            # Notify frontend using the existing single-start event (so tabs can appear)
                            await websocket.send_json(
                                {
                                    "type": "chat.workflow_started",
                                    "data": {
                                        "chat_id": new_chat_id,
                                        "workflow_name": str(target_workflow),
                                        "app_id": str(ent_id),
                                        "user_id": str(usr_id),
                                        "label": str(label) if label else None,
                                    },
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                }
                            )

                            if auto_run:
                                self._background_tasks[new_chat_id] = asyncio.create_task(
                                    self._run_workflow_background(
                                        chat_id=new_chat_id,
                                        workflow_name=str(target_workflow),
                                        app_id=str(ent_id),
                                        user_id=str(usr_id),
                                        ws_id=ws_id,
                                        initial_message=str(initial_message) if isinstance(initial_message, str) and initial_message.strip() else None,
                                        initial_agent_name_override=str(initial_agent_name_override) if isinstance(initial_agent_name_override, str) and initial_agent_name_override.strip() else None,
                                    )
                                )

                        # Summary ack (best-effort)
                        await websocket.send_json(
                            {
                                "type": "chat.workflow_batch_started",
                                "data": {
                                    "count": len(started),
                                    "workflows": started,
                                    "blocked": blocked,
                                },
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            }
                        )
                    except Exception as be:
                        logger.error(f"❌ Failed to start workflow batch: {be}")
                        await websocket.send_json(
                            {
                                "type": "chat.error",
                                "data": {
                                    "message": f"Workflow batch start failed: {str(be)}",
                                    "error_code": "START_WORKFLOW_BATCH_FAILED",
                                },
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            }
                        )
                    continue
                
                # Client resume handshake (B11)
                if mtype == "client.resume":
                    try:
                        last_client_index = data.get("lastClientIndex")
                        if not isinstance(last_client_index, int):
                            raise ValueError("lastClientIndex must be int")
                        await self._handle_resume_request(chat_id, last_client_index, websocket)
                    except Exception as re:
                        logger.error(f"❌ Failed to process client.resume for chat {chat_id}: {re}")
                        await websocket.send_json({
                            "type": "chat.error",
                            "data": {"message": f"Resume failed: {str(re)}", "error_code": "RESUME_FAILED"},
                            "timestamp": datetime.now(timezone.utc).isoformat()
                        })
                    continue
                # Unknown control message -> ignore silently
        except Exception as e:
            logger.warning(f"WebSocket error for chat {chat_id}: {e}")
        finally:
            # H1-H2: Clean up connection resources (heartbeat, message queues, etc.)
            await self._cleanup_connection(chat_id)
            logger.info(f"🔌 WebSocket disconnected for chat_id: {chat_id}")

    # ==================================================================================
    # WORKFLOW INTEGRATION METHODS
    # ==================================================================================
    
    async def handle_user_input_from_api(
        self,
        chat_id: str,
        user_id: Optional[str],
        workflow_name: str,
        message: Optional[str],
        app_id: str,
        initial_agent_name_override: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Handle user input from the POST API endpoint with smart routing

        Checks if there's an active AG2 GroupChat session waiting for input.
        If yes, passes message to existing session. If no, starts new workflow.
        """
        try:
            starting_new_workflow = False
            is_build = False
            
            # Load workflow-declared lifecycle hooks (modular, per-workflow)
            lifecycle = get_workflow_lifecycle_hooks(workflow_name)
            _is_build_workflow = lifecycle.get("is_build_workflow")
            _emit_build_started = lifecycle.get("on_start")
            _emit_build_completed = lifecycle.get("on_complete")
            _emit_build_failed = lifecycle.get("on_fail")
            
            try:
                if callable(_is_build_workflow):
                    is_build = bool(_is_build_workflow(workflow_name))
            except Exception:
                is_build = False

            # Check if there's an active AG2 session waiting for user input
            has_active_session = bool(self._input_request_registries.get(chat_id))

            # Also check if there are pending input callbacks for this chat
            active_callbacks = False
            if chat_id in self._input_request_registries:
                active_callbacks = bool(self._input_request_registries[chat_id])

            logger.info(f"🔀 [SMART_ROUTING] chat={chat_id} has_registry={has_active_session} has_callbacks={active_callbacks}")

            if has_active_session and active_callbacks:
                # Route to existing AG2 session via WebSocket callback mechanism
                logger.info(f"🔄 [SMART_ROUTING] Continuing existing AG2 session for chat {chat_id}")

                # Get any available request_id from the registry
                registry = self._input_request_registries.get(chat_id, {})
                if registry:
                    # Get the first available request_id
                    request_id = next(iter(registry.keys()))

                    normalized_message = message
                    resume_signal = False
                    if not normalized_message or (isinstance(normalized_message, str) and not normalized_message.strip()):
                        normalized_message = self._build_resume_signal(chat_id, request_id)
                        resume_signal = True

                    success = await self.submit_user_input(request_id, str(normalized_message))

                    if success:
                        route = "existing_session_resume" if resume_signal else "existing_session"
                        # Don't persist/echo resume signal messages - they're internal coordination only
                        if not resume_signal:
                            # Only persist actual user messages to database
                            try:
                                await self.process_incoming_user_message(
                                    chat_id=chat_id,
                                    user_id=user_id,
                                    content=message,
                                    source='http'
                                )
                            except Exception as persist_err:
                                logger.debug(f"User message persistence failed (non-fatal): {persist_err}")
                        return {"status": "success", "chat_id": chat_id, "message": "Input passed to existing AG2 session.", "route": route}
                    else:
                        logger.warning(f"⚠️ [SMART_ROUTING] Failed to submit input to existing session, falling back to new workflow")

            # No active session or callback failed - start new workflow
            logger.info(f"🚀 [SMART_ROUTING] Starting new workflow for chat {chat_id}")
            starting_new_workflow = True

            from mozaiksai.core.workflow.orchestration_patterns import run_workflow_orchestration

            # Only persist and echo user message when starting NEW workflows
            # For existing sessions, the message goes directly to AG2 via callback
            if message:
                try:
                    await self.process_incoming_user_message(
                        chat_id=chat_id,
                        user_id=user_id,
                        content=message,
                        source='http'
                    )
                except Exception as persist_err:
                    logger.debug(f"Early persistence of user message failed (non-fatal): {persist_err}")

            # Build lifecycle reporting (best-effort; non-blocking).
            if is_build and _emit_build_started is not None:
                try:
                    asyncio.create_task(
                        _emit_build_started(
                            app_id=app_id,
                            build_id=chat_id,
                            user_id=user_id,
                            workflow_name=workflow_name,
                        )
                    )
                except Exception:
                    pass

            # Launch orchestration (will also seed initial_messages including the persisted one)
            await run_workflow_orchestration(
                workflow_name=workflow_name,
                app_id=app_id,
                chat_id=chat_id,
                user_id=user_id,
                initial_message=None,  # already persisted & sent upstream
                initial_agent_name_override=initial_agent_name_override,
            )

            if is_build and _emit_build_completed is not None:
                try:
                    asyncio.create_task(
                        _emit_build_completed(
                            app_id=app_id,
                            build_id=chat_id,
                            user_id=user_id,
                            workflow_name=workflow_name,
                        )
                    )
                except Exception:
                    pass

            return {"status": "success", "chat_id": chat_id, "message": "Workflow started successfully.", "route": "new_workflow"}

        except Exception as e:
            logger.error(f"❌ User input handling failed for chat {chat_id}: {e}\n{traceback.format_exc()}")
            if starting_new_workflow and is_build and _emit_build_failed is not None:
                try:
                    err_details = traceback.format_exc()
                    asyncio.create_task(
                        _emit_build_failed(
                            app_id=app_id,
                            build_id=chat_id,
                            user_id=user_id,
                            workflow_name=workflow_name,
                            message=str(e),
                            details=str(err_details) if isinstance(err_details, str) else None,
                        )
                    )
                except Exception:
                    pass
            await self.send_error(
                error_message=f"An internal error occurred: {e}",
                error_code="WORKFLOW_EXECUTION_FAILED",
                chat_id=chat_id
            )
            return {"status": "error", "chat_id": chat_id, "message": str(e)}

    async def _run_workflow_background(
        self,
        *,
        chat_id: str,
        workflow_name: str,
        app_id: str,
        user_id: str,
        ws_id: Optional[int],
        initial_message: Optional[str] = None,
        initial_agent_name_override: Optional[str] = None,
    ) -> None:
        """Run a workflow orchestration in the background.

        This enables parallel execution of multiple independent chats (each with its
        own chat_id) while preserving AG2-native semantics within each chat.
        """
        try:
            async with self._workflow_spawn_semaphore:
                try:
                    result = await self.handle_user_input_from_api(
                        chat_id=chat_id,
                        user_id=user_id,
                        workflow_name=workflow_name,
                        message=initial_message,
                        app_id=app_id,
                        initial_agent_name_override=initial_agent_name_override,
                    )
                    # Emit run_complete success asynchronously to dispatcher
                    try:
                        from mozaiksai.core.events.unified_event_dispatcher import get_event_dispatcher

                        dispatcher = get_event_dispatcher()
                        asyncio.create_task(
                            dispatcher.emit(
                                "chat.run_complete",
                                {
                                    "chat_id": chat_id,
                                    "workflow_name": workflow_name,
                                    "app_id": app_id,
                                    "user_id": user_id,
                                    "status": "completed",
                                },
                            )
                        )
                    except Exception:
                        pass
                except Exception:
                    # Emit failed run_complete before re-raising so listeners can react
                    try:
                        from mozaiksai.core.events.unified_event_dispatcher import get_event_dispatcher

                        dispatcher = get_event_dispatcher()
                        asyncio.create_task(
                            dispatcher.emit(
                                "chat.run_complete",
                                {
                                    "chat_id": chat_id,
                                    "workflow_name": workflow_name,
                                    "app_id": app_id,
                                    "user_id": user_id,
                                    "status": "failed",
                                },
                            )
                        )
                    except Exception:
                        pass
                    raise
        except asyncio.CancelledError:
            # Treat cancellation as an explicit pause request (adapter-driven).
            logger.info(
                "⏸️ Background workflow cancelled (paused) workflow=%s chat=%s",
                workflow_name,
                chat_id,
            )
            raise
        except Exception as e:
            logger.error(
                f"❌ Background workflow run failed (workflow={workflow_name} chat={chat_id}): {e}",
                exc_info=True,
            )
            try:
                await self.send_error(
                    error_message=f"Background workflow failed: {e}",
                    error_code="WORKFLOW_BACKGROUND_FAILED",
                    chat_id=chat_id,
                )
            except Exception:
                pass
        finally:
            # Drop task handle
            try:
                self._background_tasks.pop(chat_id, None)
            except Exception:
                pass

            # Mark completed ONLY if we weren't cancelled.
            try:
                if ws_id:
                    task = asyncio.current_task()
                    was_cancelled = bool(task and task.cancelled())
                    if not was_cancelled:
                        session_registry.complete_workflow(ws_id, chat_id)
            except Exception:
                pass

    async def pause_background_workflow(self, *, chat_id: str, reason: str = "paused") -> bool:
        """Cancel a running background workflow task so it can be resumed later.

        This is runtime-level orchestration only: AG2 state is persisted to Mongo,
        and resuming replays messages + continues from history.
        """
        task = self._background_tasks.get(chat_id)
        if not task:
            return False
        if task.done():
            return False

        # Best-effort: mark session as paused in the runtime registry.
        try:
            conn = self.connections.get(chat_id) or {}
            ws_id = conn.get("ws_id")
            if ws_id:
                # switch_workflow will mark the previous active chat paused; we also
                # want this chat paused if it was active.
                ctx = session_registry.get_workflow_by_chat_id(ws_id, chat_id)
                if ctx and getattr(ctx, "status", None) != "completed":
                    ctx.status = "paused"
        except Exception:
            pass

        # Emit a lightweight runtime event for observability.
        try:
            from mozaiksai.core.events.unified_event_dispatcher import get_event_dispatcher

            dispatcher = get_event_dispatcher()
            if dispatcher:
                await dispatcher.emit(
                    "runtime.workflow_paused",
                    {"chat_id": chat_id, "reason": str(reason)},
                )
        except Exception:
            pass

        task.cancel()
        return True

    # ==================================================================================
    # SIMPLIFIED EVENT API - WEBSOCKET ONLY
    # ==================================================================================
    
    async def send_chat_message(
        self,
        message: str,
        agent_name: Optional[str] = None,
        chat_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """Send chat message to user interface"""
        # Create properly formatted event data with 'kind' field for envelope builder
        event_data = {
            "kind": "text",
            "agent": agent_name or "Agent", 
            "content": str(message),
            "chat_id": chat_id,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        if metadata:
            event_data["metadata"] = metadata

        # Enhanced logging for debugging UI rendering
        logger.info(f"💬 Sending chat message: kind={event_data['kind']} agent='{agent_name}' content_len={len(message)} content_preview='{message[:50]}...'")

        await self.send_event_to_ui(event_data, chat_id)
    
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

    async def _ensure_general_chat_context(
        self,
        *,
        chat_id: str,
        force_new: bool = False,
    ) -> Dict[str, Any]:
        """Return (or create) the general chat context associated with this connection."""

        conn = self.connections.get(chat_id)
        if not conn:
            raise RuntimeError(f"No active connection metadata for chat {chat_id}")

        if not force_new:
            existing_ctx = conn.get("general_session")
            if isinstance(existing_ctx, dict) and existing_ctx.get("chat_id"):
                return existing_ctx

        app_id = conn.get("app_id")
        user_id = conn.get("user_id") or "anonymous"
        if not app_id:
            raise RuntimeError("Cannot create general chat without app context")

        pm = self._get_or_create_persistence_manager()
        session_info = await pm.create_general_chat_session(
            app_id=str(app_id),
            user_id=str(user_id),
        )

        general_ctx = {
            "chat_id": session_info.get("chat_id"),
            "label": session_info.get("label"),
            "sequence": session_info.get("sequence"),
            "app_id": str(app_id),
            "user_id": str(user_id),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        conn["general_session"] = general_ctx
        return general_ctx

    async def _handle_general_agent_exchange(
        self,
        *,
        chat_id: str,
        ws_id: Optional[int],
        user_message: str,
        ui_context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Route a general-mode utterance to the configured non-AG2 capability executor."""

        conn = self.connections.get(chat_id) or {}
        app_id = conn.get("app_id")
        user_id = conn.get("user_id")
        if not app_id:
            raise RuntimeError("Cannot route general-mode message without app context")

        general_ctx = await self._ensure_general_chat_context(chat_id=chat_id)
        general_chat_id = general_ctx.get("chat_id")
        general_label = general_ctx.get("label")
        if not general_chat_id:
            raise RuntimeError("Failed to resolve general chat identifier")

        workflows_payload: List[Dict[str, Any]] = []
        if ws_id:
            contexts = session_registry.get_all_workflows(ws_id)
            for ctx in contexts:
                if hasattr(ctx, "to_dict"):
                    workflows_payload.append(ctx.to_dict())
                else:
                    workflows_payload.append({
                        "chat_id": getattr(ctx, "chat_id", None),
                        "workflow_name": getattr(ctx, "workflow_name", None),
                        "status": getattr(ctx, "status", None),
                        "artifact_id": getattr(ctx, "artifact_id", None),
                        "app_id": getattr(ctx, "app_id", None),
                        "user_id": getattr(ctx, "user_id", None),
                    })

        metadata_base = {
            "source": "general_agent",
            "ui_context": ui_context or {},
            "workflows": workflows_payload,
            "general_chat_id": general_chat_id,
            "general_chat_label": general_label,
        }

        await self._persist_general_message(
            general_chat_id=str(general_chat_id),
            app_id=str(app_id),
            role="user",
            content=user_message,
            user_id=str(user_id) if user_id else None,
            metadata=metadata_base,
        )

        await self.send_event_to_ui(
            {
                "kind": "text",
                "agent": "user",
                "content": user_message,
                "chat_id": chat_id,
                "metadata": metadata_base,
            },
            chat_id,
        )

        service = _load_general_agent_service()
        if service is None:
            await self.send_chat_message(
                "General mode is not configured for this runtime.",
                agent_name="System",
                chat_id=chat_id,
                metadata=metadata_base,
            )
            return

        response = await service.generate_response(
            prompt=user_message,
            workflows=workflows_payload,
            app_id=str(app_id),
            user_id=str(user_id) if user_id else None,
            ui_context=ui_context,
        )

        assistant_metadata = {
            "source": "general_agent",
            "workflows": workflows_payload,
            "ui_context": ui_context or {},
            "general_chat_id": general_chat_id,
            "general_chat_label": general_label,
        }

        await self._persist_general_message(
            general_chat_id=str(general_chat_id),
            app_id=str(app_id),
            role="assistant",
            content=response.get("content", ""),
            user_id=str(user_id) if user_id else None,
            metadata=assistant_metadata,
        )

        await self.send_chat_message(
            response.get("content", ""),
            agent_name="Assistant",
            chat_id=chat_id,
            metadata=assistant_metadata,
        )

        usage = response.get("usage") or {}
        try:
            pm = self._get_or_create_persistence_manager()
            await pm.update_session_metrics(
                chat_id=str(general_chat_id),
                app_id=str(app_id),
                user_id=str(user_id) if user_id else "anonymous",
                workflow_name="GeneralCapability",
                prompt_tokens=int(usage.get("prompt_tokens") or 0),
                completion_tokens=int(usage.get("completion_tokens") or 0),
                cost_usd=0.0,
                agent_name="assistant",
                session_type="general",
            )
            try:
                from mozaiksai.core.tokens.manager import TokenManager

                await TokenManager.emit_usage_delta(
                    chat_id=str(general_chat_id),
                    app_id=str(app_id),
                    user_id=str(user_id) if user_id else "anonymous",
                    workflow_name="GeneralCapability",
                    agent_name="assistant",
                    prompt_tokens=int(usage.get("prompt_tokens") or 0),
                    completion_tokens=int(usage.get("completion_tokens") or 0),
                    total_tokens=int(
                        usage.get("total_tokens")
                        or (int(usage.get("prompt_tokens") or 0) + int(usage.get("completion_tokens") or 0))
                    ),
                    cached=False,
                    duration_sec=0.0,
                )

                # Emit a cumulative usage summary snapshot for the general session.
                try:
                    coll = await pm._general_coll()  # type: ignore[attr-defined]
                    totals = await coll.find_one(
                        {"_id": str(general_chat_id), "app_id": str(app_id)},
                        {
                            "usage_prompt_tokens_final": 1,
                            "usage_completion_tokens_final": 1,
                            "usage_total_tokens_final": 1,
                        },
                    )
                    if isinstance(totals, dict):
                        await TokenManager.emit_usage_summary(
                            chat_id=str(general_chat_id),
                            app_id=str(app_id),
                            user_id=str(user_id) if user_id else "anonymous",
                            workflow_name="GeneralCapability",
                            prompt_tokens=int(totals.get("usage_prompt_tokens_final") or 0),
                            completion_tokens=int(totals.get("usage_completion_tokens_final") or 0),
                            total_tokens=int(totals.get("usage_total_tokens_final") or 0),
                        )
                except Exception:
                    logger.debug("Failed to emit general-mode usage summary", exc_info=True)
            except Exception:
                logger.debug("Failed to emit general-mode usage delta", exc_info=True)
        except Exception as metrics_err:
            logger.debug(f"Failed to record general-mode usage metrics: {metrics_err}")

    async def _persist_general_message(
        self,
        *,
        general_chat_id: str,
        app_id: str,
        role: str,
        content: str,
        user_id: Optional[str],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        pm = self._get_or_create_persistence_manager()
        try:
            await pm.append_general_message(
                general_chat_id=general_chat_id,
                app_id=app_id,
                role=role,
                content=content,
                user_id=user_id,
                metadata=metadata,
            )
        except Exception as persist_err:
            logger.debug(f"Failed to persist general agent message for {general_chat_id}: {persist_err}")

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

    async def _persist_ui_tool_state(
        self,
        *,
        chat_id: Optional[str],
        tool_name: str,
        event_id: str,
        display_type: str,
        payload: Dict[str, Any],
    ) -> None:
        """Persist latest artifact/inline UI payload for chat restoration."""
        if not chat_id or not isinstance(payload, dict):
            return

        mode_candidates = [
            display_type,
            payload.get("display"),
            payload.get("mode"),
        ]
        display_mode = next(
            (m.strip() for m in mode_candidates if isinstance(m, str) and m.strip()),
            None,
        )
        normalized_mode = display_mode.lower() if display_mode else None
        persist_flag = bool(payload.get("persist_ui_state")) if isinstance(payload, dict) else False

        if not normalized_mode and not persist_flag:
            return
        if normalized_mode not in ("artifact", "inline") and not persist_flag:
            return

        if not normalized_mode:
            normalized_mode = "artifact"

        try:
            pm = self._get_or_create_persistence_manager()
        except Exception as pm_err:  # pragma: no cover
            logger.debug(f"dY'\" [UI_TOOL] Persistence manager unavailable: {pm_err}")
            return

        try:
            app_id, workflow_name = await self._resolve_chat_context(
                chat_id,
                pm=pm,
                payload_workflow=payload.get("workflow_name"),
            )
            if not app_id:
                logger.debug(f"dY'\" [UI_TOOL] Missing app_id for chat {chat_id}; skipping last_artifact persist")
                return

            try:
                sanitized_payload = json.loads(json.dumps(payload))
            except Exception:
                sanitized_payload = payload

            artifact_doc = {
                "ui_tool_id": tool_name,
                "event_id": event_id,
                "display": normalized_mode,
                "workflow_name": payload.get("workflow_name") or workflow_name,
                "payload": sanitized_payload,
            }
            await pm.update_last_artifact(
                chat_id=chat_id,
                app_id=app_id,
                artifact=artifact_doc,
            )
        except Exception as persist_err:
            logger.debug(f"dY'\" [UI_TOOL] Failed to persist last_artifact for chat {chat_id}: {persist_err}")
    
    async def send_ui_tool_event(
        self,
        event_id: str,
        chat_id: Optional[str],
        tool_name: str,
        component_name: str,
        display_type: str,
        payload: Dict[str, Any],
        awaiting_response: bool = True,
        agent_name: Optional[str] = None
    ) -> None:
        """
        Emit a tool_call event to the frontend using the strict chat.tool_call protocol.
        """
        # Extract agent_name from payload if not explicitly provided
        if not agent_name and isinstance(payload, dict):
            agent_name = payload.get("agent_name")
        
        # Build a standardized AG2 tool_call payload
        event = {
            "kind": "tool_call",
            "tool_name": tool_name,
            "component_type": component_name,
            "awaiting_response": bool(awaiting_response),
            "payload": payload,
            "corr": event_id,
            "display": display_type,
            "display_type": display_type,
        }
        
        # Set agent field if available
        if agent_name:
            event["agent"] = agent_name

        payload_keys = list(payload.keys()) if isinstance(payload, dict) else []
        logger.info(
            f"🛠️ [UI_TOOL] Emitting tool_call event: tool={tool_name}, component={component_name}, display={display_type}, event_id={event_id}, chat_id={chat_id}, payload_keys={payload_keys[:12]}"
        )

        try:
            await self._persist_ui_tool_state(
                chat_id=chat_id,
                tool_name=tool_name,
                event_id=event_id,
                display_type=display_type,
                payload=payload,
            )
        except Exception as persist_exc:  # pragma: no cover
            logger.debug(f"🧩 [UI_TOOL] Persist hook raised for chat {chat_id}: {persist_exc}")
        if event_id and bool(awaiting_response):
            self._ui_tool_metadata[event_id] = {
                "chat_id": chat_id,
                "tool_name": tool_name,
                "display": display_type,
            }

        # Delegate to core event sender for namespacing and sequence handling
        await self.send_event_to_ui(event, chat_id)

    @classmethod
    async def wait_for_ui_tool_response(cls, event_id: str, timeout: Optional[float] = 300.0) -> Dict[str, Any]:
        """Await a UI tool response with an optional timeout.

        Args:
            event_id: Correlation id originally sent in the ui_tool_event.
            timeout: Seconds to wait before raising TimeoutError (None = wait forever).
        """
        instance = await cls.get_instance()
        if not instance:
            raise RuntimeError("SimpleTransport instance not available")

        if event_id not in instance.pending_ui_tool_responses:
            instance.pending_ui_tool_responses[event_id] = asyncio.Future()

        fut = instance.pending_ui_tool_responses[event_id]
        try:
            response_data = await asyncio.wait_for(fut, timeout=timeout) if timeout else await fut
            return response_data
        except asyncio.TimeoutError:
            if not fut.done():
                fut.set_exception(asyncio.TimeoutError("UI tool response timed out"))
            logger.error(f"⏰ UI tool response timed out for event {event_id}")
            raise
        finally:
            instance.pending_ui_tool_responses.pop(event_id, None)

    async def submit_ui_tool_response(self, event_id: str, response_data: Dict[str, Any]) -> bool:
        """
        Submit response data for a pending UI tool event.
        
        This method is called by an API endpoint when the frontend submits data
        from an interactive UI component.
        """
        if event_id in self.pending_ui_tool_responses:
            future = self.pending_ui_tool_responses[event_id]
            if not future.done():
                future.set_result(response_data)
                logger.info(f"✅ [UI_TOOL] Submitted response for event {event_id}")
                metadata = self._ui_tool_metadata.pop(event_id, None)
                if metadata:
                    display_mode = (metadata.get("display") or "").lower()
                    chat_ref = metadata.get("chat_id")
                    tool_name = metadata.get("tool_name")

                    # Apply declarative ui_response triggers into AG2 ContextVariables
                    # (AG2-native: updates the same context object used by handoffs).
                    if chat_ref and tool_name:
                        manager = self._derived_context_managers.get(chat_ref)
                        if manager and hasattr(manager, "apply_ui_tool_response"):
                            try:
                                updated = manager.apply_ui_tool_response(
                                    tool_name=str(tool_name),
                                    response_data=response_data if isinstance(response_data, dict) else {},
                                )
                                if updated:
                                    logger.info(
                                        f"🧭 [UI_TOOL] Applied ui_response triggers: chat={chat_ref} tool={tool_name} vars={updated}"
                                    )
                            except Exception as trigger_err:
                                logger.debug(f"[UI_TOOL] ui_response trigger apply failed: {trigger_err}")

                    if display_mode == "artifact":
                        try:
                            await self.send_event_to_ui({"kind": "ui_tool_dismiss", "event_id": event_id, "ui_tool_id": metadata.get("tool_name")}, chat_ref)
                            logger.debug(f"🧹 [UI_TOOL] Emitted dismiss event for artifact {event_id}")
                        except Exception as dismiss_err:
                            logger.debug(f"⚠️ [UI_TOOL] Failed to emit dismiss event for {event_id}: {dismiss_err}")
                return True
            else:
                self._ui_tool_metadata.pop(event_id, None)
                logger.warning(f"⚠️ [UI_TOOL] Event {event_id} already completed")
                return False
        else:
            logger.warning(f"⚠️ [UI_TOOL] No pending event found for {event_id}")
            return False

    # ------------------------------------------------------------------
    # Context trigger manager registry
    # ------------------------------------------------------------------
    def register_derived_context_manager(self, chat_id: str, manager: Any) -> None:
        if not chat_id:
            return
        self._derived_context_managers[chat_id] = manager

    def unregister_derived_context_manager(self, chat_id: str) -> None:
        if not chat_id:
            return
        self._derived_context_managers.pop(chat_id, None)

    # T3: Sequence tracking methods for resume capability
    def _get_next_sequence(self, chat_id: str) -> int:
        """Get the next sequence number for a chat session."""
        if chat_id not in self._sequence_counters:
            self._sequence_counters[chat_id] = 0
        self._sequence_counters[chat_id] += 1
        return self._sequence_counters[chat_id]
    
    # H1: Server backpressure implementation
    async def _check_backpressure(self, chat_id: str) -> bool:
        """Check if connection should be throttled due to backpressure."""
        if chat_id not in self._message_queues:
            self._message_queues[chat_id] = []
        
        queue_size = len(self._message_queues[chat_id])
        if queue_size >= self._max_queue_size:
            logger.warning(f"🚨 Backpressure triggered for {chat_id}: queue size {queue_size}")
            # Drop oldest messages to make room
            dropped = queue_size - self._max_queue_size + 10  # Keep some buffer
            self._message_queues[chat_id] = self._message_queues[chat_id][dropped:]
            logger.info(f"📉 Dropped {dropped} queued messages for {chat_id}")
            return True
        return False

    async def _queue_message_with_backpressure(self, chat_id: str, message_data: Dict[str, Any]) -> bool:
        """Queue message with backpressure control."""
        if await self._check_backpressure(chat_id):
            # Connection is under backpressure - message may have been dropped
            pass
        # Early serialization guard: ensure no raw AG2 objects linger in queue.
        if not isinstance(message_data, (dict, list, tuple, str, int, float, bool, type(None))):
            try:
                message_data = self._serialize_ag2_events(message_data)
            except Exception:
                message_data = {"type": "log", "data": {"message": self._stringify_unknown(message_data)}}

        self._message_queues[chat_id].append(message_data)
        return True

    async def _flush_message_queue(self, chat_id: str) -> None:
        """Flush queued messages for a connection."""
        if chat_id not in self._message_queues or not self._message_queues[chat_id]:
            return
        
        logger.info(f"🔄 [TRANSPORT] Flushing message queue for chat_id={chat_id}, queue_size={len(self._message_queues[chat_id])}")
        
        if chat_id in self.connections:
            websocket = self.connections[chat_id]["websocket"]
            messages_to_send = self._message_queues[chat_id].copy()
            self._message_queues[chat_id].clear()
            
            for message in messages_to_send:
                try:
                    # Check if message is already in proper format for WebSocket
                    if isinstance(message, dict) and 'type' in message and 'data' in message:
                        # Ensure the 'data' payload is JSON-serializable (may contain AG2 objects)
                        try:
                            safe_message = message.copy()
                            safe_message['data'] = self._serialize_ag2_events(message['data'])
                            
                            # Extract agent name from data payload and add to top-level envelope for frontend attribution
                            if isinstance(safe_message.get('data'), dict):
                                agent_from_data = safe_message['data'].get('agent') or safe_message['data'].get('sender')
                                if agent_from_data and isinstance(agent_from_data, str):
                                    safe_message['agent'] = agent_from_data
                                elif 'agent' not in safe_message:
                                    # Fallback to generic if no agent in data
                                    safe_message['agent'] = 'Agent'
                            
                            if safe_message.get('type') == 'chat.tool_call':
                                payload_obj = safe_message.get('data', {}).get('payload', {})
                                payload_keys = list(payload_obj.keys()) if isinstance(payload_obj, dict) else []
                                logger.info('TRANSPORT payload keys before send: %s', payload_keys[:12])
                            await websocket.send_json(safe_message)
                            logger.info(f"✅ [TRANSPORT] WebSocket send_json completed for envelope type={safe_message.get('type')}, chat_id={chat_id}")
                        except Exception:
                            # Fallback: attempt to serialize whole message as a last resort
                            try:
                                await websocket.send_json(self._serialize_ag2_events(message))
                            except Exception:
                                raise
                    else:
                        serialized_message = self._serialize_ag2_events(message)
                        await websocket.send_json(serialized_message)
                except Exception as e:
                    logger.error(f"Failed to send queued message to {chat_id}: {e}. Will retry shortly.")
                    # Re-queue remaining (including current) for retry
                    remaining = [message] + messages_to_send[messages_to_send.index(message)+1:]
                    self._message_queues[chat_id] = remaining + self._message_queues[chat_id]
                    # Schedule a retry flush with small backoff
                    self._schedule_flush_retry(chat_id)
                    break

    def _schedule_flush_retry(self, chat_id: str, delay: float = 0.5) -> None:
        """Schedule a single retry flush if not already pending."""
        if chat_id in self._scheduled_flush_tasks and not self._scheduled_flush_tasks[chat_id].done():
            return  # already scheduled
        async def _delayed():
            try:
                await asyncio.sleep(delay)
                await self._flush_message_queue(chat_id)
            finally:
                # Clear handle so future retries can be scheduled
                self._scheduled_flush_tasks.pop(chat_id, None)
        self._scheduled_flush_tasks[chat_id] = asyncio.create_task(_delayed())

    # H2: Heartbeat implementation
    async def _start_heartbeat(self, chat_id: str, websocket) -> None:
        """Start heartbeat task for a connection."""
        if chat_id in self._heartbeat_tasks:
            self._heartbeat_tasks[chat_id].cancel()
        
        self._heartbeat_tasks[chat_id] = asyncio.create_task(
            self._heartbeat_loop(chat_id, websocket)
        )
        logger.info(f"💓 Started heartbeat for {chat_id}")

    async def _heartbeat_loop(self, chat_id: str, websocket) -> None:
        """Heartbeat loop for detecting silent disconnects."""
        try:
            while chat_id in self.connections:
                await asyncio.sleep(self._heartbeat_interval)
                
                # Send ping
                ping_data = {
                    "type": "ping",
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
                
                try:
                    await websocket.send_json(ping_data)
                    logger.debug(f"📡 Sent ping to {chat_id}")
                except Exception as e:
                    logger.warning(f"💔 Heartbeat failed for {chat_id}: {e}")
                    # Connection is dead - clean up
                    await self._cleanup_connection(chat_id)
                    break
        except asyncio.CancelledError:
            logger.debug(f"💔 Heartbeat cancelled for {chat_id}")
        except Exception as e:
            logger.error(f"💔 Heartbeat error for {chat_id}: {e}")

    async def _stop_heartbeat(self, chat_id: str) -> None:
        """Stop heartbeat task for a connection."""
        if chat_id in self._heartbeat_tasks:
            self._heartbeat_tasks[chat_id].cancel()
            del self._heartbeat_tasks[chat_id]
            logger.debug(f"💔 Stopped heartbeat for {chat_id}")

    async def _auto_resume_if_needed(self, chat_id: str, websocket, app_id: Optional[str]) -> None:
        """Automatically restore chat history for IN_PROGRESS chats on WebSocket connection."""
        try:
            if not app_id:
                logger.debug(f"[AUTO_RESUME] No app_id for {chat_id}, skipping auto-resume")
                return

            # Get workflow name and startup_mode from connection
            workflow_name = None
            startup_mode = None
            if chat_id in self.connections:
                workflow_name = self.connections[chat_id].get("workflow_name")
                if workflow_name:
                    try:
                        config = workflow_manager.get_config(workflow_name)
                        startup_mode = config.get("startup_mode", "AgentDriven")
                        logger.debug(f"[AUTO_RESUME] Retrieved startup_mode={startup_mode} for workflow={workflow_name}")
                    except Exception as cfg_err:
                        logger.warning(f"[AUTO_RESUME] Failed to get workflow config: {cfg_err}")

            # Use GroupChatResumer for proper message replay with filtering
            from mozaiksai.core.transport.resume_groupchat import GroupChatResumer
            resumer = GroupChatResumer()
            
            async def send_event_wrapper(event_dict: Dict[str, Any], target_chat_id: Optional[str]) -> None:
                """Wrapper to convert resume events to transport format."""
                if not isinstance(event_dict, dict):
                    return
                
                kind = event_dict.get("kind")
                if kind == "text":
                    # Convert to chat.text format
                    await self._queue_message_with_backpressure(chat_id, {
                        "type": "chat.text",
                        "data": {
                            "index": event_dict.get("index", 0),
                            "content": event_dict.get("content", ""),
                            "role": event_dict.get("role", "user"),
                            "agent": event_dict.get("agent", "user"),
                            "sender": event_dict.get("agent", "user"),
                            "replay": event_dict.get("replay", True),
                            "timestamp": event_dict.get("timestamp"),
                            "metadata": event_dict.get("metadata"),
                        }
                    })
                elif kind == "resume_boundary":
                    # Convert boundary to transport format
                    await self._queue_message_with_backpressure(chat_id, {
                        "type": "chat.resume_boundary",
                        "data": event_dict.get("data", {})
                    })
            
            # Call the resumer with startup_mode filtering
            await resumer.auto_resume_if_needed(
                chat_id=chat_id,
                app_id=app_id,
                send_event=send_event_wrapper,
                startup_mode=startup_mode,
            )
            
            await self._flush_message_queue(chat_id)

        except Exception as e:
            logger.warning(f"[AUTO_RESUME] Failed to auto-resume chat {chat_id}: {e}")

    async def _cleanup_connection(self, chat_id: str) -> None:
        """Clean up connection resources."""
        if chat_id in self.connections:
            del self.connections[chat_id]

        if chat_id in self._message_queues:
            del self._message_queues[chat_id]

        await self._stop_heartbeat(chat_id)
        logger.info(f"🧹 Cleaned up connection resources for {chat_id}")
    


