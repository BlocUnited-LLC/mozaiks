# ==============================================================================
# FILE: mozaiksai/core/events/unified_event_dispatcher.py
# DESCRIPTION: Centralized event dispatcher for all event types in mozaiksai
# ==============================================================================

"""Unified Event Dispatcher.

Centralized dispatcher for mozaiksai runtime events.

This module is responsible for:
- BusinessLogEvent / ToolCallRequestEvent internal domain events
- Lightweight outbound envelope construction for already-normalized chat/runtime events

AG2 runtime events should already arrive normalized into dicts with a `kind` field
(handled earlier by event serialization / orchestration).
"""

import asyncio
import inspect
import uuid
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from logs.logging_config import get_core_logger, get_workflow_logger
from mozaiksai.core.events.auto_tool_handler import AutoToolEventHandler
from mozaiksai.core.events.event_serialization import serialize_event_content
from mozaiksai.core.events.runtime_events import (
    RUNTIME_AGENT_OUTPUT_VALIDATED,
    RUNTIME_PROCESS_COMPLETED,
)
from mozaiksai.core.events.usage_ingest import get_usage_ingest_client
from mozaiksai.core.ports.orchestration import DomainEvent
from mozaiksai.core.workflow.runtime_signals import SYSTEM_RESUME_SIGNAL
from mozaiksai.core.workflow.startup_messages import matches_hidden_initial_message
from mozaiksai.core.workflow.workflow_manager import workflow_manager

logger = get_core_logger("unified_event_dispatcher")
wf_logger = get_workflow_logger("event_dispatcher")

class EventCategory(Enum):
    """
    mozaiksai Event System Categories:
    
    BUSINESS: System monitoring and lifecycle events
              - Uses 'log_event_type' field for classification
              - Examples: SERVER_STARTUP_COMPLETED, WORKFLOW_SYSTEM_READY
              - Handled by BusinessLogHandler for logging/observability
    
    TOOL_CALL: Interactive agent-to-UI communication
              - Uses 'tool_name' field for component identification
              - Examples: agent_api_key_input, file_download_center
              - Handled by ToolCallRequestHandler for dynamic UI components
              
    Note: AG2 Runtime Events use a separate 'kind' field and are processed
          via event_serialization.py -> WebSocket transport (not this enum)
    """
    BUSINESS = "business"
    TOOL_CALL = "tool_call"

@dataclass
class BusinessLogEvent:
    """
    System monitoring and lifecycle events for observability.
    
    Key field: log_event_type (distinguishes this from AG2 'kind' and tool-call 'tool_name')
    Purpose: Application health monitoring, performance tracking, debugging
    Handler: BusinessLogHandler -> structured logging
    """
    log_event_type: str  # Event classification (e.g. "SERVER_STARTUP_COMPLETED")
    description: str
    context: dict[str, Any] = field(default_factory=dict)
    level: str = "INFO"
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    event_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    category: str = field(default="business")

@dataclass
class ToolCallRequestEvent:
    """
    Interactive agent-to-UI communication for dynamic components.
    
    Key field: tool_name (distinguishes this from business 'log_event_type' and AG2 'kind')
    Purpose: Agent requests for user input, dynamic UI rendering, interactive flows
    Handler: ToolCallRequestHandler -> WebSocket transport to frontend
    """
    tool_name: str
    payload: dict[str, Any]
    workflow_name: str
    display: str = "inline"
    chat_id: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    event_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    category: str = field(default="tool_call")

EventType = BusinessLogEvent | ToolCallRequestEvent | DomainEvent

class EventHandler(ABC):
    @abstractmethod
    async def handle(self, _event: EventType) -> bool:
        ...

    @abstractmethod
    def can_handle(self, _event: EventType) -> bool:
        ...

class BusinessLogHandler(EventHandler):
    def can_handle(self, event: EventType) -> bool:
        return isinstance(event, BusinessLogEvent)

    async def handle(self, event: EventType) -> bool:
        if not isinstance(event, BusinessLogEvent):
            return False
        lvl = getattr(logger, event.level.lower(), logger.info)
        lvl(f"[BUSINESS] {event.log_event_type}: {event.description} context={event.context}")
        return True

class ToolCallRequestHandler(EventHandler):
    def can_handle(self, event: EventType) -> bool:
        return isinstance(event, ToolCallRequestEvent)

    async def handle(self, event: EventType) -> bool:
        if not isinstance(event, ToolCallRequestEvent):
            return False
        logger.debug("[TOOL_CALL] tool=%s workflow=%s display=%s", event.tool_name, event.workflow_name, event.display)
        return True


class DomainEventHandler(EventHandler):
    """Handle canonical runtime DomainEvents without importing engine-specific types."""

    def can_handle(self, event: EventType) -> bool:
        return isinstance(event, DomainEvent)

    async def handle(self, event: EventType) -> bool:
        if not isinstance(event, DomainEvent):
            return False
        logger.debug(
            "[DOMAIN_EVENT] kind=%s chat_id=%s source=%s",
            event.kind,
            event.chat_id,
            event.source,
        )
        return True

class UnifiedEventDispatcher:
    """
    Central event dispatcher for mozaiksai's three-layer event system:
    
    1. Business Events: Use emit_business_event(log_event_type=...) for monitoring
    2. Tool call request events: Use emit_tool_call_request(tool_name=...) for agent-UI interaction
    3. AG2 Runtime Events: Handled separately via event_serialization.py using 'kind' field
    
    AG2 events flow: AG2 -> event_serialization.py -> WebSocket transport
    Business/UI events flow: Code -> UnifiedEventDispatcher -> Handlers
    """
    def __init__(self):
        self.handlers: list[EventHandler] = []
        self._event_handlers: dict[str, list[Callable[[dict[str, Any]], Awaitable[Any] | Any]]] = {}
        # Greeting echo dedup: tracks (chat_id, agent_name) pairs where
        # ws_protocol already sent the greeting before the workflow started.
        # The next text message from that agent is a run-history echo and
        # should be converted to a silent 'greeting_echo' event.
        self._greeting_echo_keys: set = set()
        self._hidden_initial_message_keys: set = set()
        self.metrics: dict[str, Any] = {
            "events_processed": 0,
            "events_failed": 0,
            "events_by_category": {"business": 0, "tool_call": 0},
            "created": datetime.now(UTC).isoformat(),
        }
        from mozaiksai.core.workflow.pack.journey_orchestrator import JourneyOrchestrator
        self._auto_tool_handler = AutoToolEventHandler()
        self._journey_orchestrator = JourneyOrchestrator()
        # Advisory-only usage ingest (measurement signals only; no billing mutations).
        self._usage_ingest = get_usage_ingest_client()
        self._setup_default_handlers()
        self.register_runtime_handler(RUNTIME_AGENT_OUTPUT_VALIDATED, self._auto_tool_handler.handle_tool_dispatch)
        # Journey auto-advance
        self.register_runtime_handler(RUNTIME_PROCESS_COMPLETED, self._journey_orchestrator.handle_run_complete)
        # Best-effort control-plane notification; must never block execution.
        self.register_handler("chat.usage_summary", self._usage_ingest.handle_usage_summary)
        from mozaiksai.core.usage import get_runtime_usage_ledger

        self._runtime_usage_ledger = get_runtime_usage_ledger()
        self.register_handler("chat.usage_delta", self._runtime_usage_ledger.record_usage_delta)
        from mozaiksai.core.tokens.usage_ingest import get_token_wallet_usage_ingest_client

        self._token_wallet_usage_ingest = get_token_wallet_usage_ingest_client()
        self.register_handler("chat.usage_delta", self._token_wallet_usage_ingest.handle_usage_delta)

    def _setup_default_handlers(self):
        self.register_handler(BusinessLogHandler())
        self.register_handler(ToolCallRequestHandler())
        self.register_handler(DomainEventHandler())

    def register_handler(
        self,
        handler_or_event_type: EventHandler | str,
        handler: Callable[[dict[str, Any]], Awaitable[Any] | Any] | None = None,
    ) -> None:
        if isinstance(handler_or_event_type, EventHandler):
            self.handlers.append(handler_or_event_type)
            return
        if isinstance(handler_or_event_type, str):
            if handler is None or not callable(handler):
                raise ValueError("handler must be callable when registering by event type")
            self._event_handlers.setdefault(handler_or_event_type, []).append(handler)
            return
        raise TypeError("Unsupported handler registration signature")

    def register_runtime_handler(
        self,
        canonical_event_type: str,
        handler: Callable[[dict[str, Any]], Awaitable[Any] | Any],
    ) -> None:
        """Register a handler for a canonical runtime event."""
        self.register_handler(canonical_event_type, handler)

    async def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        listeners = list(self._event_handlers.get(event_type, []))
        if not listeners:
            logger.debug("No listeners registered for event_type=%s", event_type)
            return

        # Avoid log spam for high-frequency runtime measurement events.
        if event_type.startswith("chat.usage_"):
            logger.debug("[DISPATCH] Emitting event %s to %s listener(s)", event_type, len(listeners))
        else:
            logger.debug("[DISPATCH] Emitting event %s to %s listener(s)", event_type, len(listeners))

        pending_results: list[Awaitable[Any]] = []
        for listener in listeners:
            try:
                result = listener(payload)
                if inspect.isawaitable(result):
                    pending_results.append(result)
            except Exception as exc:
                logger.error("Event handler raised for %s: %s", event_type, exc, exc_info=True)

        if pending_results:
            settled = await asyncio.gather(*pending_results, return_exceptions=True)
            for outcome in settled:
                if isinstance(outcome, Exception):
                    logger.error(
                        "Async event handler failure for %s: %s",
                        event_type,
                        outcome,
                        exc_info=True,
                    )

        self.metrics.setdefault("custom_events_emitted", 0)
        self.metrics["custom_events_emitted"] += 1
        emitted_by_type = self.metrics.setdefault("custom_events_by_type", {})
        emitted_by_type[event_type] = emitted_by_type.get(event_type, 0) + 1

    async def dispatch(self, event: EventType) -> bool:
        start_time = datetime.now(UTC)
        try:
            handler = next((h for h in self.handlers if h.can_handle(event)), None)
            if not handler:
                logger.warning("No handler for event category=%s", getattr(event,'category',None))
                self.metrics["events_failed"] += 1
                return False
            success = await handler.handle(event)
            if success:
                self.metrics["events_processed"] += 1
                cat = getattr(event, "category", None)
                if cat in self.metrics["events_by_category"]:
                    self.metrics["events_by_category"][cat] += 1
            else:
                self.metrics["events_failed"] += 1
            dur_ms = (datetime.now(UTC) - start_time).total_seconds() * 1000
            if dur_ms > 100:
                logger.warning("SLOW_EVENT_DISPATCH category=%s dur=%sms", getattr(event,'category',None), dur_ms)
            return success
        except Exception as e:  # pragma: no cover
            logger.error("Dispatch failure: %s", e, exc_info=True)
            self.metrics["events_failed"] += 1
            return False

    def mark_greeting_echo(self, chat_id: str, agent_name: str) -> None:
        """Flag that the next text from *agent_name* in *chat_id* is a greeting
        echo (already sent by ws_protocol) and should be silently dropped."""
        if chat_id and agent_name:
            self._greeting_echo_keys.add((chat_id, agent_name))

    def get_metrics(self) -> dict[str, Any]:
        return {**self.metrics, "handler_count": len(self.handlers), "timestamp": datetime.now(UTC).isoformat()}

    async def emit_business_event(
        self,
        log_event_type: str,
        description: str,
        context: dict[str, Any] | None = None,
        level: str = "INFO",
    ) -> bool:
        event = BusinessLogEvent(log_event_type=log_event_type, description=description, context=context or {}, level=level)
        return await self.dispatch(event)

    async def emit_tool_call_request(
        self,
        tool_name: str,
        payload: dict[str, Any],
        workflow_name: str,
        display: str = "inline",
        chat_id: str | None = None,
    ) -> bool:
        event = ToolCallRequestEvent(tool_name=tool_name, payload=payload, workflow_name=workflow_name, display=display, chat_id=chat_id)
        return await self.dispatch(event)

    async def emit_domain_event(self, event: DomainEvent) -> bool:
        """Dispatch a typed control-plane DomainEvent and fan it out to listeners."""
        success = await self.dispatch(event)
        await self.emit(
            event.kind,
            {
                "kind": event.kind,
                "payload": event.payload,
                "chat_id": event.chat_id,
                "timestamp": event.timestamp,
                "source": event.source,
            },
        )
        return success

    @staticmethod
    def domain_event_to_envelope(event: DomainEvent) -> dict[str, Any]:
        """Convert a typed DomainEvent into a websocket-style envelope."""
        return {
            "type": event.kind,
            "data": {
                "kind": event.kind,
                "payload": event.payload,
                "chat_id": event.chat_id,
                "timestamp": event.timestamp,
                "source": event.source,
            },
            "chat_id": event.chat_id,
            "timestamp": event.timestamp,
        }

    def build_outbound_event_envelope(
        self,
        *,
        raw_event: Any,
        chat_id: str | None,
        get_sequence_cb: Any | None = None,
        workflow_name: str | None = None,
    ) -> dict[str, Any] | None:
        """
        Transform AG2 runtime events for WebSocket transport.
        
        This handles the third event type (AG2 Runtime Events):
        - Input: Dict with 'kind' field (e.g. {"kind": "text", "content": "..."})  
        - Output: WebSocket envelope with 'type' field (e.g. {"type": "chat.text", "data": {...}})
        
        The 'kind' -> 'type' namespace mapping ensures frontend receives consistent event names.
        """
        if isinstance(raw_event, DomainEvent):
            return self.domain_event_to_envelope(raw_event)
        if not (isinstance(raw_event, dict) and 'kind' in raw_event):
            return None
        timestamp = datetime.now(UTC).isoformat()
        event_dict: dict[str, Any] = raw_event  # type: ignore[assignment]
        kind = str(event_dict.get('kind', 'unknown'))
        base_kind = kind.split('.', 1)[1] if kind.startswith('chat.') else kind

        # ── Greeting echo detection (no workflow_manager dependency) ──
        # If ws_protocol already sent the UserDriven greeting, the AG2
        # run-history replay is just bookkeeping — emit as chat.greeting_echo
        # so the frontend silently ignores it.
        if base_kind in ('text', 'print') and chat_id:
            _agent = event_dict.get('agent') or event_dict.get('sender')
            _key = (chat_id, _agent)
            if _key in self._greeting_echo_keys:
                self._greeting_echo_keys.discard(_key)
                logger.debug("[GREETING_ECHO] Converting duplicate greeting from %s to chat.greeting_echo", _agent)
                return {
                    "type": "chat.greeting_echo",
                    "data": event_dict,
                    "chat_id": chat_id,
                    "timestamp": timestamp,
                }

        # SUPPRESSION CHECKS (must happen BEFORE other flags)
        if base_kind in ('text', 'print') and workflow_name:
            agent_name = event_dict.get('agent') or event_dict.get('sender')
            content = event_dict.get('content', '')
            metadata = event_dict.get('metadata') if isinstance(event_dict.get('metadata'), dict) else {}
            seed_kind = event_dict.get('_mozaiks_seed_kind') or metadata.get('_mozaiks_seed_kind')  # type: ignore[union-attr]
            
            # Check 0: System resume signals (always suppress)
            if isinstance(content, str) and SYSTEM_RESUME_SIGNAL in content:
                event_dict['_mozaiks_hide'] = True
                logger.debug("[SYSTEM_SIGNAL] Suppressing internal resume signal from %s", agent_name)
                return {
                    "type": f"chat.{base_kind}",
                    "data": event_dict,
                    "chat_id": chat_id,
                    "timestamp": timestamp
                }

            # Check 0.25: Hidden AG2 seed messages should never surface to users.
            # These are internal orchestration primers retained for AG2 context only.
            if isinstance(seed_kind, str) and seed_kind.strip() in {"initial_message", "userdriven_trigger"}:
                event_dict['_mozaiks_hide'] = True
                logger.debug(
                    "SEED_MSG_SUPPRESSED seed=%s agent=%s",
                    seed_kind.strip(),
                    agent_name,
                )
                return {
                    "type": f"chat.{base_kind}",
                    "data": event_dict,
                    "chat_id": chat_id,
                    "timestamp": timestamp,
                }

            suppression_key = (str(chat_id), str(workflow_name))
            if (
                suppression_key not in self._hidden_initial_message_keys
                and matches_hidden_initial_message(
                    workflow_name=workflow_name,
                    role="user",
                    content=content,
                    agent_name=agent_name,
                )
            ):
                self._hidden_initial_message_keys.add(suppression_key)
                event_dict['_mozaiks_hide'] = True
                logger.debug(
                    "HIDDEN_INITIAL_MSG_SUPPRESSED chat=%s workflow=%s",
                    chat_id,
                    workflow_name,
                )
                return {
                    "type": f"chat.{base_kind}",
                    "data": event_dict,
                    "chat_id": chat_id,
                    "timestamp": timestamp,
                }

            # Check 0.5: UserDriven synthetic trigger (the "." message used to start a workflow run)
            # This should never be shown to users - it's an internal AG2 mechanism
            if isinstance(content, str) and content.strip() == ".":
                # Check if this is a userdriven workflow
                try:
                    wf_config = workflow_manager.get_config(workflow_name)
                    workflow_startup_mode = str(wf_config.get("workflow_startup_mode", "")).strip().lower() if wf_config else ""
                    sender_lower = str(agent_name or "").lower()
                    # Suppress if userdriven mode AND sender looks like a user proxy
                    if workflow_startup_mode == "userdriven" and sender_lower in {"user", "userproxy", "chat_manager", "manager"}:
                        event_dict['_mozaiks_hide'] = True
                        logger.debug("[USERDRIVEN_TRIGGER] Suppressing synthetic '.' trigger from %s (startup_mode=%s)", agent_name, workflow_startup_mode)
                        return {
                            "type": f"chat.{base_kind}",
                            "data": event_dict,
                            "chat_id": chat_id,
                            "timestamp": timestamp
                        }
                except Exception as e:
                    logger.debug("[USERDRIVEN_TRIGGER] Could not check workflow_startup_mode: %s", e)

            if agent_name and isinstance(content, str):
                # Check 1: UI_HIDDEN triggers (exact match suppression)
                try:
                    hidden_triggers = workflow_manager.get_ui_hidden_triggers(workflow_name)
                    
                    if agent_name in hidden_triggers and content.strip() in hidden_triggers[agent_name]:
                        event_dict['_mozaiks_hide'] = True
                        logger.debug("[UI_HIDDEN] Suppressing hidden trigger agent=%s", agent_name)
                        return {
                            "type": f"chat.{base_kind}",
                            "data": event_dict,
                            "chat_id": chat_id,
                            "timestamp": timestamp
                        }
                except Exception as e:
                    logger.warning("UI_HIDDEN_CHECK_FAILED: %s", e, exc_info=True)
                
                # Check 2: AUTO_TOOL agent message deduplication
                # Auto-tool agents emit text (with agent_message) then tool_call (with same agent_message)
                # Suppress the text message to avoid duplication in UI
                try:
                    auto_tool_agents = workflow_manager.get_auto_tool_agents(workflow_name)
                    if agent_name in auto_tool_agents:
                        event_dict['_mozaiks_hide'] = True
                        logger.debug("AUTO_TOOL_DEDUP_SUPPRESS agent=%s content_prefix=%r", agent_name, content[:100])
                        return {
                            "type": f"chat.{base_kind}",
                            "data": event_dict,
                            "chat_id": chat_id,
                            "timestamp": timestamp
                        }
                except Exception as e:
                    logger.warning("AUTO_TOOL_DEDUP_CHECK_FAILED: %s", e, exc_info=True)
            
            # Now check other agent flags
            structured_flag = False
            visual_flag = False
            tool_agent_flag = False
            if agent_name:
                try:
                    cfg = workflow_manager.get_agent_structured_outputs_config(workflow_name)
                    structured_flag = cfg.get(agent_name, False)
                except Exception as _sc_err:
                    logger.debug("AGENT_FLAG_LOOKUP_FAILED structured workflow=%s agent=%s: %s", workflow_name, agent_name, _sc_err)
                try:
                    visual_agents = workflow_manager.get_visual_agents(workflow_name)
                    visual_flag = agent_name in visual_agents
                except Exception as _va_err:
                    logger.debug("AGENT_FLAG_LOOKUP_FAILED visual workflow=%s agent=%s: %s", workflow_name, agent_name, _va_err)
                try:
                    ui_tools = workflow_manager.get_ui_tools(workflow_name)
                    tool_agent_flag = any(
                        tool.get('agent') == agent_name or tool.get('caller') == agent_name
                        for tool in ui_tools.values()
                    )
                except Exception as _ut_err:
                    logger.debug("AGENT_FLAG_LOOKUP_FAILED ui_tools workflow=%s agent=%s: %s", workflow_name, agent_name, _ut_err)

            event_dict['is_structured_capable'] = structured_flag
            event_dict['is_visual'] = visual_flag
            event_dict['is_tool_agent'] = tool_agent_flag
            if get_sequence_cb and chat_id:
                try:
                    event_dict['sequence'] = get_sequence_cb(chat_id)
                except Exception as _seq_err:
                    logger.debug("SEQUENCE_CB_FAILED chat=%s: %s", chat_id, _seq_err)
        if kind == 'agent_output_validated' and isinstance(event_dict, dict):
            if 'structured_data' in event_dict:
                event_dict['structured_data'] = serialize_event_content(event_dict['structured_data'])
            if 'auto_tool_call' in event_dict:
                event_dict['auto_tool_call'] = bool(event_dict['auto_tool_call'])

        ns_map = {
            'print': 'chat.print', 'text': 'chat.text', 'input_ack': 'chat.input_ack',
            'input_timeout': 'chat.input_timeout', 'select_speaker': 'chat.select_speaker', 'resume_boundary': 'chat.resume_boundary',
            'usage_delta': 'chat.usage_delta', 'usage_summary': 'chat.usage_summary', 'run_complete': 'chat.run_complete', 'error': 'chat.error', 'tool_call': 'chat.tool_call', 'tool_response': 'chat.tool_response',
            'agent_output_validated': 'chat.agent_output_validated', 'run_start': 'chat.run_start', 'tool_call_dismiss': 'chat.tool_call_dismiss',
            'awaiting_reply': 'chat.awaiting_reply', 'activity': 'chat.activity',
            'attachment_uploaded': 'chat.attachment_uploaded',
            'stream_chunk': 'chat.stream_chunk', 'stream_end': 'chat.stream_end', 'custom_event': 'chat.custom_event',
            'greeting_echo': 'chat.greeting_echo',
            # ui.* namespace — typed primitive event contract (L2)
            'ui_render': 'ui.render', 'ui_update': 'ui.update', 'ui_dismiss': 'ui.dismiss',
        }
        mapped_type = kind if kind.startswith('chat.') else ns_map.get(kind, kind)
        
        return {'type': mapped_type, 'data': event_dict, 'timestamp': timestamp}

_global_dispatcher: UnifiedEventDispatcher | None = None

def get_event_dispatcher() -> UnifiedEventDispatcher:
    global _global_dispatcher
    if _global_dispatcher is None:
        _global_dispatcher = UnifiedEventDispatcher()
    return _global_dispatcher

async def emit_business_event(
    log_event_type: str,
    description: str,
    context: dict[str, Any] | None = None,
    level: str = "INFO"
) -> bool:
    """
    Emit a BUSINESS event for system monitoring and logging.
    
    Args:
        log_event_type: Event classification (e.g. "SERVER_STARTUP_COMPLETED")
        description: Human-readable description
        context: Additional structured data for debugging
        level: Log level (INFO, DEBUG, WARNING, ERROR)
    
    This is distinct from:
    - AG2 runtime events (use 'kind' field, processed via event_serialization.py)  
    - Tool call request events (use emit_tool_call_request with 'tool_name' field)
    """
    dispatcher = get_event_dispatcher()
    return await dispatcher.emit_business_event(log_event_type, description, context, level)

async def emit_tool_call_request(
    tool_name: str,
    payload: dict[str, Any],
    workflow_name: str,
    display: str = "inline",
    chat_id: str | None = None
) -> bool:
    """
    Emit a UI TOOL event for agent-to-UI interactive communication.
    
    Args:
        tool_name: UI component identifier (e.g. "agent_api_key_input")
        payload: Component-specific data and configuration
        workflow_name: Which workflow is requesting the UI interaction
        display: Display mode ("inline", "artifact", etc.)
        chat_id: Associated chat session
    
    This is distinct from:
    - Business events (use emit_business_event with 'log_event_type' field)
    - AG2 runtime events (use 'kind' field, processed via event_serialization.py)
    """
    dispatcher = get_event_dispatcher()
    return await dispatcher.emit_tool_call_request(tool_name, payload, workflow_name, display, chat_id)


