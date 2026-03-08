# ==============================================================================
# FILE: mozaiksai/transport/websocket/event_sender.py
# DESCRIPTION: Outbound event pipeline — visibility, trace downgrading, dispatch
#
# Phase 3 migration: AG2 sender-name extraction removed from this module.
# Inject ``sender_name_fn`` (from ``adapters.ag2.serializer.extract_sender_name``)
# so this module contains zero AG2 object traversal.
# ==============================================================================
import asyncio
import re
import traceback
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Dict, List, Optional, Tuple, Union

from logs.logging_config import get_core_logger

logger = get_core_logger("transport.event_sender")


# Module-level content cleaner to allow reuse without constructing any class
def _extract_clean_content(message: Union[str, Dict[str, Any], Any]) -> str:
    """Extract clean content from AG2 UUID-formatted messages or other formats.

    This is the same logic previously at handler module-level; placed here so
    other modules can ``from mozaiksai.transport.websocket.event_sender import _extract_clean_content``.
    """
    if isinstance(message, str):
        match = re.search(r"content='(.*?)'", message, re.DOTALL)
        if match:
            return match.group(1)
        return message
    elif isinstance(message, dict):
        return message.get('content', str(message))
    else:
        return str(message)


def _dict_sender_name(event: Any) -> Optional[str]:
    """Fallback sender-name extraction for dict payloads and DomainEvents.

    Handles the common case where an event is already a dict with an ``agent``
    key (as emitted by iostream_bridge, send_chat_message, etc.).  Raw AG2
    object traversal is NOT performed here — inject
    ``extract_sender_name`` from ``adapters.ag2.serializer`` for full support.
    """
    if event is None:
        return None
    # DomainEvent or similar object with a direct .agent attribute
    agent = getattr(event, "agent", None)
    if isinstance(agent, str) and agent.strip():
        return agent.strip()
    # Dict payload
    if isinstance(event, dict):
        for key in ("agent", "agent_name", "sender"):
            val = event.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return None


class EventSender:
    """Outbound event pipeline: envelope building, visibility gating, trace downgrading.

    Extracted from ``SimpleTransport`` (Phase 5).

    Dependencies (injected via constructor):
    - ``connection_manager`` — for ``broadcast_to_websockets``, ``get_next_sequence``,
      and read access to ``connections``.
    - ``ui_tool_metadata`` — shared dict (written here, consumed by ``InputHandler``).
    - ``persist_ui_tool_cb`` — optional async callback for persisting UI tool state.
    - ``sender_name_fn`` — optional callable ``(event) -> Optional[str]`` that extracts
      the sending agent name from an event object.  Defaults to ``_dict_sender_name``
      (dict/DomainEvent only).  Inject ``extract_sender_name`` from
      ``adapters.ag2.serializer`` to support raw AG2 event objects.
    """

    def __init__(
        self,
        *,
        connection_manager: Any,
        ui_tool_metadata: Dict[str, Dict[str, Any]],
        persist_ui_tool_cb: Optional[Callable[..., Coroutine]] = None,
        sender_name_fn: Optional[Callable[[Any], Optional[str]]] = None,
    ) -> None:
        self._cm = connection_manager
        self._ui_tool_metadata = ui_tool_metadata
        self._persist_ui_tool_cb = persist_ui_tool_cb
        self._get_sender_name: Callable[[Any], Optional[str]] = sender_name_fn or _dict_sender_name

    # ------------------------------------------------------------------
    # Usage event forwarding (registered on kernel dispatcher)
    # ------------------------------------------------------------------

    async def handle_usage_delta_event(self, payload: Dict[str, Any]) -> None:
        chat_id = payload.get("chat_id")
        if not chat_id:
            return
        try:
            await self.send_event_to_ui({"kind": "usage_delta", **payload}, str(chat_id))
        except Exception:
            logger.debug("Failed to forward usage_delta to UI", exc_info=True)

    async def handle_usage_summary_event(self, payload: Dict[str, Any]) -> None:
        chat_id = payload.get("chat_id")
        if not chat_id:
            return
        try:
            await self.send_event_to_ui({"kind": "usage_summary", **payload}, str(chat_id))
        except Exception:
            logger.debug("Failed to forward usage_summary to UI", exc_info=True)

    # ------------------------------------------------------------------
    # Visibility filtering
    # ------------------------------------------------------------------

    def should_show_to_user(self, agent_name: Optional[str], chat_id: Optional[str] = None) -> bool:
        """Check if a message should be shown to the user interface."""
        if not agent_name:
            return True  # Show system messages

        from mozaiksai.transport.websocket.registry import session_registry

        # Get the workflow type and ws_id for this chat session
        workflow_name = None
        ws_id = None
        if chat_id and chat_id in self._cm.connections:
            conn = self._cm.connections[chat_id]
            workflow_name = conn.workflow_name
            ws_id = conn.ws_id

        # If in general mode, show all messages (bypass visual_agents filtering)
        if ws_id and session_registry.is_in_general_mode(ws_id):
            logger.debug(f"🧠 [GENERAL_MODE] Allowing message from '{agent_name}' (general mode bypass)")
            return True

        # If we have workflow type, use visual_agents filtering
        if workflow_name:
            try:
                from mozaiksai.kernel.workflow_manager import workflow_manager

                config = workflow_manager.get_config(workflow_name)
                visual_agents = config.get("visual_agents")

                if isinstance(visual_agents, list):
                    if not visual_agents:
                        logger.debug(
                            f"🔍 visual_agents empty for {workflow_name}; "
                            f"allowing message from {agent_name}"
                        )
                        return True

                    def normalize_agent(name):
                        if not name:
                            return ''
                        return str(name).lower().replace('agent', '').replace(' ', '').strip()

                    normalized_agent = normalize_agent(agent_name)
                    normalized_visual_agents = [normalize_agent(va) for va in visual_agents]

                    is_allowed = normalized_agent in normalized_visual_agents
                    logger.debug(
                        f"🔍 Backend visual_agents check: '{agent_name}' -> "
                        f"'{normalized_agent}' in {normalized_visual_agents} = {is_allowed}"
                    )
                    return is_allowed
            except FileNotFoundError:
                pass

        return True

    def sanitize_trace_content(
        self, content: str, *, limit: int = 800
    ) -> Tuple[str, bool, bool]:
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

    # ------------------------------------------------------------------
    # Event sender name extraction
    # ------------------------------------------------------------------

    def extract_event_sender_name(self, event: Any) -> Optional[str]:
        """Delegate to the injected sender-name resolver.

        Phase 3 change: AG2-specific object traversal moved to
        ``adapters.ag2.serializer.extract_sender_name`` and injected as
        ``sender_name_fn``.  Callers retaining the old method name continue
        to work transparently.
        """
        return self._get_sender_name(event)

    def extract_clean_content(self, message: Union[str, Dict[str, Any], Any]) -> str:
        """Instance wrapper around the module-level cleaner."""
        return _extract_clean_content(message)

    # ------------------------------------------------------------------
    # Core outbound event pipeline
    # ------------------------------------------------------------------

    async def send_event_to_ui(self, event: Any, chat_id: Optional[str] = None) -> None:
        """Serializes and sends a raw AG2 event to the UI.

        This is the primary method for forwarding AG2 native events.
        """
        try:
            # Allow callers to provide a fully-formed transport envelope (e.g., ack.ui_tool_response)
            # without forcing another serialization pass through the dispatcher.
            if isinstance(event, dict) and 'type' in event and 'data' in event and 'kind' not in event:
                logger.info(
                    "🔁 [TRANSPORT] Forwarding pre-built envelope without re-serialization: %s",
                    event.get('type'),
                )
                await self._cm.broadcast_to_websockets(event, chat_id)
                return

            from mozaiksai.kernel.dispatcher import get_event_dispatcher  # local import to avoid cycle

            dispatcher = get_event_dispatcher()
            workflow_name = None
            if chat_id and chat_id in self._cm.connections:
                workflow_name = self._cm.connections[chat_id].workflow_name

            # DEBUG: Log what we're processing
            event_type = type(event).__name__ if hasattr(event, '__class__') else 'dict'
            if isinstance(event, dict):
                event_kind = event.get('kind', 'unknown')
                logger.info(
                    f"🔍 [TRANSPORT] Processing event: type={event_type}, kind={event_kind}, "
                    f"chat_id={chat_id}, dict_keys="
                    f"{list(event.keys()) if isinstance(event, dict) else 'N/A'}"
                )
            else:
                logger.info(
                    f"🔍 [TRANSPORT] Processing event: type={event_type}, chat_id={chat_id}"
                )

            envelope = dispatcher.build_outbound_event_envelope(
                raw_event=event,
                chat_id=chat_id,
                get_sequence_cb=self._cm.get_next_sequence,
                workflow_name=workflow_name,
            )
            if not envelope:
                logger.warning(f"❌ [TRANSPORT] No envelope created for event type={event_type}")
                return

            logger.info(
                f"✅ [TRANSPORT] Envelope created successfully: type={envelope.get('type')}, "
                f"has_data={bool(envelope.get('data'))}"
            )

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
                    sanitized, redacted, truncated = self.sanitize_trace_content(original_content)
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
                logger.info(
                    f"🎯 [TRANSPORT] UI tool event detected - bypassing agent visibility filter "
                    f"(component={envelope.get('data', {}).get('component_type')})"
                )
            elif is_input_request_event:
                logger.info(
                    "🎯 [TRANSPORT] Input request event detected - bypassing agent visibility filter"
                )

            # Additional filtering (agent visibility) using event sender metadata.
            agent_name = self.extract_event_sender_name(event)
            if not skip_visibility_filter and agent_name and not self.should_show_to_user(agent_name, chat_id):
                if _downgrade_to_trace(agent=str(agent_name)):
                    logger.info(
                        f"[TRANSPORT] Downgraded non-visual message from '{agent_name}' "
                        f"to trace for chat {chat_id}"
                    )
                else:
                    logger.info(
                        f"🚫 [TRANSPORT] Filtered out AG2 event from agent '{agent_name}' "
                        f"for chat {chat_id} (should_show_to_user=False)"
                    )
                    return

            # Apply visibility filtering for dict events (post-envelope) as well
            if not agent_name:
                data_payload = envelope.get('data') if isinstance(envelope, dict) else None
                if isinstance(data_payload, dict):
                    agent_name = data_payload.get('agent') or data_payload.get('agent_name')
                    if not agent_name and isinstance(event, dict):
                        agent_name = event.get('agent') or event.get('agent_name')
                if (
                    not skip_visibility_filter
                    and agent_name
                    and not self.should_show_to_user(agent_name, chat_id)
                ):
                    if _downgrade_to_trace(agent=str(agent_name)):
                        logger.info(
                            f"[TRANSPORT] Downgraded non-visual message from '{agent_name}' "
                            f"to trace for chat {chat_id}"
                        )
                    else:
                        logger.info(
                            f"🚫 [TRANSPORT] Filtered out event from agent '{agent_name}' "
                            f"for chat {chat_id} (visual_agents gate, should_show_to_user=False)"
                        )
                        return

            # Record performance metrics for tool calls (best-effort)
            try:
                et_name = type(event).__name__
                if any(token in et_name for token in ("Tool", "Function", "Call")):
                    tool_name = getattr(event, "tool_name", None)
                    if isinstance(tool_name, str) and tool_name.strip():
                        try:
                            from mozaiksai.runtime.observability.performance_manager import (
                                get_performance_manager,
                            )

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
                    logger.info(
                        f"🚫 [TRANSPORT] Suppressing hidden message (derived context trigger) "
                        f"for chat {chat_id}: {data_payload.get('content', 'no content')[:100]}"
                    )
                    return

            logger.info(
                f"📤 [TRANSPORT] Sending envelope: type={envelope.get('type')}, chat_id={chat_id}"
            )
            await self._cm.broadcast_to_websockets(envelope, chat_id)

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
                            conn = self._cm.connections.get(chat_id) if chat_id else None
                            if conn is not None:
                                for k in ("app_id", "user_id", "workflow_name", "ws_id"):
                                    v = getattr(conn, k, None)
                                    if v is not None and k not in dispatch_payload:
                                        dispatch_payload[k] = v
                        except Exception:
                            pass
                        asyncio.create_task(dispatcher.emit('chat.run_complete', dispatch_payload))
            except Exception:
                pass
        except Exception as e:
            logger.error(f"❌ Failed to serialize or send UI event: {e}\n{traceback.format_exc()}")

    # ------------------------------------------------------------------
    # Convenience senders
    # ------------------------------------------------------------------

    async def send_error(
        self,
        error_message: str,
        error_code: str = "GENERAL_ERROR",
        chat_id: Optional[str] = None,
    ) -> None:
        """Send error message to UI via WebSocket."""
        event_data = {
            "type": "error",
            "data": {
                "message": error_message,
                "error_code": error_code,
                "chat_id": chat_id,
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        await self._cm.broadcast_to_websockets(event_data, chat_id)
        logger.error(f"❌ Error: {error_message}")

    async def send_chat_message(
        self,
        message: str,
        agent_name: Optional[str] = None,
        chat_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Send chat message to user interface."""
        event_data = {
            "kind": "text",
            "agent": agent_name or "Agent",
            "content": str(message),
            "chat_id": chat_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if metadata:
            event_data["metadata"] = metadata

        logger.info(
            f"💬 Sending chat message: kind={event_data['kind']} agent='{agent_name}' "
            f"content_len={len(message)} content_preview='{message[:50]}...'"
        )

        await self.send_event_to_ui(event_data, chat_id)

    async def send_ui_tool_event(
        self,
        event_id: str,
        chat_id: Optional[str],
        tool_name: str,
        component_name: str,
        display_type: str,
        payload: Dict[str, Any],
        awaiting_response: bool = True,
        agent_name: Optional[str] = None,
    ) -> None:
        """Emit a tool_call event to the frontend using the strict chat.tool_call protocol."""
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

        if agent_name:
            event["agent"] = agent_name

        payload_keys = list(payload.keys()) if isinstance(payload, dict) else []
        logger.info(
            f"🛠️ [UI_TOOL] Emitting tool_call event: tool={tool_name}, "
            f"component={component_name}, display={display_type}, event_id={event_id}, "
            f"chat_id={chat_id}, payload_keys={payload_keys[:12]}"
        )

        # Persist UI tool state (best-effort via callback)
        if self._persist_ui_tool_cb:
            try:
                await self._persist_ui_tool_cb(
                    chat_id=chat_id,
                    tool_name=tool_name,
                    event_id=event_id,
                    display_type=display_type,
                    payload=payload,
                )
            except Exception as persist_exc:  # pragma: no cover
                logger.debug(
                    f"🧩 [UI_TOOL] Persist hook raised for chat {chat_id}: {persist_exc}"
                )

        if event_id and bool(awaiting_response):
            self._ui_tool_metadata[event_id] = {
                "chat_id": chat_id,
                "tool_name": tool_name,
                "display": display_type,
            }

        # Delegate to core event sender for namespacing and sequence handling
        await self.send_event_to_ui(event, chat_id)
