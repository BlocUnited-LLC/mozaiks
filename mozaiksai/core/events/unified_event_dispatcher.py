# ==============================================================================
# FILE: core/events/unified_event_dispatcher.py
# DESCRIPTION: Centralized event dispatcher for all event types in MozaiksAI
# ==============================================================================

"""Unified Event Dispatcher.

Centralized dispatcher for MozaiksAI runtime events.

This module is responsible for:
- BusinessLogEvent / UIToolEvent internal domain events
- Lightweight outbound envelope construction for already-normalized chat/runtime events

AG2 runtime events should already arrive normalized into dicts with a `kind` field
(handled earlier by event serialization / orchestration).
"""

import logging
import uuid
import asyncio
import inspect
from typing import Dict, Any, Optional, Union, List, Callable, Awaitable
from datetime import datetime, UTC
from enum import Enum
from dataclasses import dataclass, field
from abc import ABC, abstractmethod

from mozaiksai.core.events.auto_tool_handler import AutoToolEventHandler
from mozaiksai.core.events.usage_ingest import get_usage_ingest_client
from mozaiksai.core.workflow.pack.workflow_pack_coordinator import WorkflowPackCoordinator
from mozaiksai.core.workflow.pack.journey_orchestrator import JourneyOrchestrator
from logs.logging_config import get_core_logger, get_workflow_logger
from mozaiksai.core.events.event_serialization import serialize_event_content

logger = get_core_logger("unified_event_dispatcher")
wf_logger = get_workflow_logger("event_dispatcher")

try:  # workflow config (optional in some minimal test contexts)
    from mozaiksai.core.workflow.workflow_manager import workflow_manager  # type: ignore
except Exception:  # pragma: no cover
    workflow_manager = None  # type: ignore

class EventCategory(Enum):
    """
    MozaiksAI Event System Categories:
    
    BUSINESS: System monitoring and lifecycle events
              - Uses 'log_event_type' field for classification
              - Examples: SERVER_STARTUP_COMPLETED, WORKFLOW_SYSTEM_READY
              - Handled by BusinessLogHandler for logging/observability
    
    UI_TOOL:  Interactive agent-to-UI communication
              - Uses 'ui_tool_id' field for component identification  
              - Examples: agent_api_key_input, file_download_center
              - Handled by UIToolHandler for dynamic UI components
              
    Note: AG2 Runtime Events use a separate 'kind' field and are processed
          via event_serialization.py -> WebSocket transport (not this enum)
    """
    BUSINESS = "business"
    UI_TOOL = "ui_tool"

@dataclass
class BusinessLogEvent:
    """
    System monitoring and lifecycle events for observability.
    
    Key field: log_event_type (distinguishes this from AG2 'kind' and UI 'ui_tool_id')
    Purpose: Application health monitoring, performance tracking, debugging
    Handler: BusinessLogHandler -> structured logging
    """
    log_event_type: str  # Event classification (e.g. "SERVER_STARTUP_COMPLETED")
    description: str
    context: Dict[str, Any] = field(default_factory=dict)
    level: str = "INFO"
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    event_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    category: str = field(default="business")

@dataclass
class UIToolEvent:
    """
    Interactive agent-to-UI communication for dynamic components.
    
    Key field: ui_tool_id (distinguishes this from business 'log_event_type' and AG2 'kind')
    Purpose: Agent requests for user input, dynamic UI rendering, interactive flows
    Handler: UIToolHandler -> WebSocket transport to frontend
    """
    ui_tool_id: str       # Component identifier (e.g. "agent_api_key_input")  
    payload: Dict[str, Any]
    workflow_name: str
    display: str = "inline"
    chat_id: Optional[str] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    event_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    category: str = field(default="ui_tool")


@dataclass
class SessionPausedEvent:
    chat_id: str
    reason: str
    required_tokens: Optional[int] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    category: str = field(default="runtime")


EventType = Union[BusinessLogEvent, UIToolEvent, SessionPausedEvent]

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

class UIToolHandler(EventHandler):
    def can_handle(self, event: EventType) -> bool:
        return isinstance(event, UIToolEvent)

    async def handle(self, event: EventType) -> bool:
        if not isinstance(event, UIToolEvent):
            return False
        logger.debug(f"[UI_TOOL] id={event.ui_tool_id} workflow={event.workflow_name} display={event.display}")
        return True

class UnifiedEventDispatcher:
    """
    Central event dispatcher for MozaiksAI's three-layer event system:
    
    1. Business Events: Use emit_business_event(log_event_type=...) for monitoring
    2. UI Tool Events: Use emit_ui_tool_event(ui_tool_id=...) for agent-UI interaction  
    3. AG2 Runtime Events: Handled separately via event_serialization.py using 'kind' field
    
    AG2 events flow: AG2 -> event_serialization.py -> WebSocket transport
    Business/UI events flow: Code -> UnifiedEventDispatcher -> Handlers
    """
    def __init__(self):
        self.handlers: List[EventHandler] = []
        self._event_handlers: Dict[str, List[Callable[[Dict[str, Any]], Awaitable[Any] | Any]]] = {}
        self.metrics: Dict[str, Any] = {
            "events_processed": 0,
            "events_failed": 0,
            "events_by_category": {"business": 0, "ui_tool": 0},
            "created": datetime.now(UTC).isoformat(),
        }
        self._auto_tool_handler = AutoToolEventHandler()
        self._pack_coordinator = WorkflowPackCoordinator()
        self._journey_orchestrator = JourneyOrchestrator()
        # Advisory-only usage ingest (measurement signals only; no billing mutations).
        self._usage_ingest = get_usage_ingest_client()
        self._setup_default_handlers()
        self.register_handler("chat.structured_output_ready", self._auto_tool_handler.handle_structured_output_ready)
        self.register_handler("chat.structured_output_ready", self._pack_coordinator.handle_structured_output_ready)
        self.register_handler("chat.run_complete", self._pack_coordinator.handle_run_complete)
        # Journey auto-advance (pack v2)
        self.register_handler("chat.run_complete", self._journey_orchestrator.handle_run_complete)
        # Best-effort control-plane notification; must never block execution.
        self.register_handler("chat.usage_summary", self._usage_ingest.handle_usage_summary)

    def _setup_default_handlers(self):
        self.register_handler(BusinessLogHandler())
        self.register_handler(UIToolHandler())

    def register_handler(
        self,
        handler_or_event_type: Union[EventHandler, str],
        handler: Optional[Callable[[Dict[str, Any]], Awaitable[Any] | Any]] = None,
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

    async def emit(self, event_type: str, payload: Dict[str, Any]) -> None:
        listeners = list(self._event_handlers.get(event_type, []))
        if not listeners:
            logger.debug("No listeners registered for event_type=%s", event_type)
            return

        # Avoid log spam for high-frequency runtime measurement events.
        if event_type.startswith("chat.usage_"):
            logger.debug("[DISPATCH] Emitting event %s to %s listener(s)", event_type, len(listeners))
        else:
            logger.info("[DISPATCH] Emitting event %s to %s listener(s) payload=%s", event_type, len(listeners), payload)

        def _log_task_completion(task: asyncio.Future, evt: str) -> None:
            try:
                task.result()
            except Exception as exc:  # pragma: no cover - logged for observability
                logger.error("Async event handler failure for %s: %s", evt, exc, exc_info=True)

        for listener in listeners:
            try:
                result = listener(payload)
                if inspect.isawaitable(result):
                    task = asyncio.create_task(result)
                    task.add_done_callback(lambda t, evt=event_type: _log_task_completion(t, evt))
            except Exception as exc:
                logger.error("Event handler raised for %s: %s", event_type, exc, exc_info=True)

        self.metrics.setdefault("custom_events_emitted", 0)
        self.metrics["custom_events_emitted"] += 1
        emitted_by_type = self.metrics.setdefault("custom_events_by_type", {})
        emitted_by_type[event_type] = emitted_by_type.get(event_type, 0) + 1

    async def dispatch(self, event: EventType) -> bool:
        start_time = datetime.now(UTC)
        try:
            handler = next((h for h in self.handlers if h.can_handle(event)), None)
            if not handler:
                logger.warning(f"No handler for event category={getattr(event,'category',None)}")
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
                logger.info(f"Slow event dispatch category={getattr(event,'category',None)} dur={dur_ms:.1f}ms")
            return success
        except Exception as e:  # pragma: no cover
            logger.error(f"Dispatch failure: {e}")
            self.metrics["events_failed"] += 1
            return False

    def get_metrics(self) -> Dict[str, Any]:
        return {**self.metrics, "handler_count": len(self.handlers), "timestamp": datetime.now(UTC).isoformat()}

    async def emit_business_event(
        self,
        log_event_type: str,
        description: str,
        context: Optional[Dict[str, Any]] = None,
        level: str = "INFO",
    ) -> bool:
        event = BusinessLogEvent(log_event_type=log_event_type, description=description, context=context or {}, level=level)
        return await self.dispatch(event)

    async def emit_ui_tool_event(
        self,
        ui_tool_id: str,
        payload: Dict[str, Any],
        workflow_name: str,
        display: str = "inline",
        chat_id: Optional[str] = None,
    ) -> bool:
        event = UIToolEvent(ui_tool_id=ui_tool_id, payload=payload, workflow_name=workflow_name, display=display, chat_id=chat_id)
        return await self.dispatch(event)

    def build_outbound_event_envelope(
        self,
        *,
        raw_event: Any,
        chat_id: Optional[str],
        get_sequence_cb: Optional[Any] = None,
        workflow_name: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Transform AG2 runtime events for WebSocket transport.
        
        This handles the third event type (AG2 Runtime Events):
        - Input: Dict with 'kind' field (e.g. {"kind": "text", "content": "..."})  
        - Output: WebSocket envelope with 'type' field (e.g. {"type": "chat.text", "data": {...}})
        
        The 'kind' -> 'type' namespace mapping ensures frontend receives consistent event names.
        """
        if not (isinstance(raw_event, dict) and 'kind' in raw_event):
            return None
        timestamp = datetime.now(UTC).isoformat()
        event_dict: Dict[str, Any] = raw_event  # type: ignore[assignment]
        kind = str(event_dict.get('kind', 'unknown'))
        base_kind = kind.split('.', 1)[1] if kind.startswith('chat.') else kind
        
        # DEBUG: Log what we're checking
        logger.info(f"🔍 [UI_HIDDEN_DEBUG] base_kind={base_kind}, workflow_manager={'present' if workflow_manager else 'MISSING'}, workflow_name={workflow_name}")
        
        # SUPPRESSION CHECKS (must happen BEFORE other flags)
        if base_kind in ('text', 'print') and workflow_manager and workflow_name:
            agent_name = event_dict.get('agent') or event_dict.get('sender')
            content = event_dict.get('content', '')
            
            logger.info(f"🔍 [UI_HIDDEN_DEBUG] Checking message: agent={agent_name}, content='{content[:50] if content else 'EMPTY'}...'")
            
            # Check 0: System resume signals (always suppress)
            if isinstance(content, str) and '[SYSTEM_RESUME_SIGNAL]' in content:
                event_dict['_mozaiks_hide'] = True
                logger.info(f"🚫 [SYSTEM_SIGNAL] Suppressing internal resume signal from {agent_name}")
                return {
                    "type": f"chat.{base_kind}",
                    "data": event_dict,
                    "chat_id": chat_id,
                    "timestamp": timestamp
                }
            
            if agent_name and isinstance(content, str):
                # Check 1: UI_HIDDEN triggers (exact match suppression)
                try:
                    hidden_triggers = workflow_manager.get_ui_hidden_triggers(workflow_name)  # type: ignore
                    logger.info(f"🔍 [UI_HIDDEN_DEBUG] Got hidden triggers: {hidden_triggers}")
                    
                    if agent_name in hidden_triggers and content.strip() in hidden_triggers[agent_name]:
                        event_dict['_mozaiks_hide'] = True
                        logger.info(f"🚫 [UI_HIDDEN] Suppressing hidden trigger: agent={agent_name}, content='{content.strip()}'")
                        return {
                            "type": f"chat.{base_kind}",
                            "data": event_dict,
                            "chat_id": chat_id,
                            "timestamp": timestamp
                        }
                except Exception as e:
                    logger.warning(f"⚠️ [UI_HIDDEN] Failed to check hidden triggers: {e}", exc_info=True)
                
                # Check 2: AUTO_TOOL agent message deduplication
                # Auto-tool agents emit text (with agent_message) then tool_call (with same agent_message)
                # Suppress the text message to avoid duplication in UI
                logger.info(f"🔍 [AUTO_TOOL_DEBUG] About to check auto_tool agents for agent={agent_name}, workflow_name={workflow_name}")
                try:
                    auto_tool_agents = workflow_manager.get_auto_tool_agents(workflow_name)  # type: ignore
                    logger.info(f"🔍 [AUTO_TOOL_DEBUG] Got auto_tool agents: {auto_tool_agents}, checking if {agent_name} in set")
                    if agent_name in auto_tool_agents:
                        event_dict['_mozaiks_hide'] = True
                        logger.info(f"� [AUTO_TOOL_DEDUP] Suppressing text from auto_tool agent {agent_name}: '{content[:100]}'")
                        return {
                            "type": f"chat.{base_kind}",
                            "data": event_dict,
                            "chat_id": chat_id,
                            "timestamp": timestamp
                        }
                except Exception as e:
                    logger.warning(f"⚠️ [AUTO_TOOL_DEDUP] Failed to check auto_tool agents: {e}", exc_info=True)
            
            # Now check other agent flags
            structured_flag = False
            visual_flag = False
            tool_agent_flag = False
            if agent_name:
                try:
                    cfg = workflow_manager.get_agent_structured_outputs_config(workflow_name)  # type: ignore
                    structured_flag = cfg.get(agent_name, False)
                except Exception:
                    pass
                try:
                    visual_agents = workflow_manager.get_visual_agents(workflow_name)  # type: ignore
                    visual_flag = agent_name in visual_agents
                except Exception:
                    pass
                try:
                    ui_tools = workflow_manager.get_ui_tools(workflow_name)  # type: ignore
                    tool_agent_flag = any(
                        tool.get('agent') == agent_name or tool.get('caller') == agent_name
                        for tool in ui_tools.values()
                    )
                except Exception:
                    pass
            
            event_dict['is_structured_capable'] = structured_flag
            event_dict['is_visual'] = visual_flag
            event_dict['is_tool_agent'] = tool_agent_flag
            if get_sequence_cb and chat_id:
                try:
                    event_dict['sequence'] = get_sequence_cb(chat_id)
                except Exception:
                    pass
        if kind == 'structured_output_ready' and isinstance(event_dict, dict):
            if 'structured_data' in event_dict:
                event_dict['structured_data'] = serialize_event_content(event_dict['structured_data'])
            if 'auto_tool_mode' in event_dict:
                event_dict['auto_tool_mode'] = bool(event_dict['auto_tool_mode'])

        ns_map = {
            'print': 'chat.print', 'text': 'chat.text', 'input_request': 'chat.input_request', 'input_ack': 'chat.input_ack',
            'input_timeout': 'chat.input_timeout', 'select_speaker': 'chat.select_speaker', 'resume_boundary': 'chat.resume_boundary',
            'usage_delta': 'chat.usage_delta', 'usage_summary': 'chat.usage_summary', 'run_complete': 'chat.run_complete', 'error': 'chat.error', 'tool_call': 'chat.tool_call', 'tool_response': 'chat.tool_response',
            'structured_output_ready': 'chat.structured_output_ready', 'run_start': 'chat.run_start', 'ui_tool_dismiss': 'chat.ui_tool_dismiss',
            'attachment_uploaded': 'chat.attachment_uploaded'
        }
        mapped_type = kind if kind.startswith('chat.') else ns_map.get(kind, kind)
        
        return {'type': mapped_type, 'data': event_dict, 'timestamp': timestamp}

_global_dispatcher: Optional[UnifiedEventDispatcher] = None

def get_event_dispatcher() -> UnifiedEventDispatcher:
    global _global_dispatcher
    if _global_dispatcher is None:
        _global_dispatcher = UnifiedEventDispatcher()
    return _global_dispatcher

async def emit_business_event(
    log_event_type: str,
    description: str,
    context: Optional[Dict[str, Any]] = None,
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
    - UI tool events (use emit_ui_tool_event with 'ui_tool_id' field)
    """
    dispatcher = get_event_dispatcher()
    return await dispatcher.emit_business_event(log_event_type, description, context, level)

async def emit_ui_tool_event(
    ui_tool_id: str,
    payload: Dict[str, Any],
    workflow_name: str,
    display: str = "inline",
    chat_id: Optional[str] = None
) -> bool:
    """
    Emit a UI TOOL event for agent-to-UI interactive communication.
    
    Args:
        ui_tool_id: UI component identifier (e.g. "agent_api_key_input")
        payload: Component-specific data and configuration
        workflow_name: Which workflow is requesting the UI interaction
        display: Display mode ("inline", "artifact", etc.)
        chat_id: Associated chat session
    
    This is distinct from:
    - Business events (use emit_business_event with 'log_event_type' field)
    - AG2 runtime events (use 'kind' field, processed via event_serialization.py)
    """
    dispatcher = get_event_dispatcher()
    return await dispatcher.emit_ui_tool_event(ui_tool_id, payload, workflow_name, display, chat_id)


