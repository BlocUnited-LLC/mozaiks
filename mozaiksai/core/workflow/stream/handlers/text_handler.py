# ==============================================================================
# FILE: core/workflow/stream/handlers/text_handler.py
# DESCRIPTION: Handler for TextEvent and PrintEvent (agent messages)
# ==============================================================================

"""
Text Event Handler

Handles TextEvent and PrintEvent for agent message content.

Responsibilities:
- Persist events to database
- Handle derived context updates
- Emit synthetic speaker selection when needed
- Filter seed messages to avoid duplication
- Process structured outputs and auto-tool follow-ups
- Build text payload for UI
"""

import asyncio
import json
import uuid
from pathlib import Path
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, Optional, Set, Type

from pydantic import ValidationError

from .base import BaseEventHandler

if TYPE_CHECKING:
    from ..context import StreamContext, StreamState

# Import AG2 event types
from autogen.events.agent_events import TextEvent

try:
    from autogen.events.print_event import PrintEvent
    HAS_PRINT_EVENT = True
except ImportError:
    HAS_PRINT_EVENT = False
    PrintEvent = type(None)  # type: ignore

# Import serialization utilities
from mozaiksai.core.events.event_serialization import (
    build_ui_event_payload,
    EventBuildContext,
    extract_agent_name,
    normalize_text_content,
    serialize_event_content,
    build_structured_output_ready_event,
)

# System signal markers for internal coordination
_SYSTEM_SIGNAL_MARKERS: tuple[str, ...] = ("[SYSTEM_RESUME_SIGNAL]",)


class TextEventHandler(BaseEventHandler):
    """
    Handler for TextEvent and PrintEvent.

    Processes agent message content with:
    - Event persistence
    - Derived context handling
    - Synthetic speaker emission
    - Seed message deduplication
    - Structured output processing
    """

    def event_types(self) -> Set[Type]:
        """Handle TextEvent and PrintEvent."""
        types: Set[Type] = {TextEvent}
        if HAS_PRINT_EVENT:
            types.add(PrintEvent)
        return types

    async def handle(
        self,
        event: Any,
        ctx: "StreamContext",
        state: "StreamState",
    ) -> Optional[Dict[str, Any]]:
        """
        Handle TextEvent/PrintEvent.

        Args:
            event: TextEvent or PrintEvent instance
            ctx: Stream context
            state: Stream state

        Returns:
            text payload for UI, or None if suppressed
        """
        # Persist event
        try:
            await ctx.persistence_manager.save_event(event, ctx.chat_id, ctx.app_id)
        except Exception as e:
            ctx.wf_logger.warning(f"Failed to persist TextEvent: {e}")

        # Handle derived context
        if ctx.derived_context_manager:
            try:
                ctx.derived_context_manager.handle_event(event)
            except Exception as dc_err:
                ctx.wf_logger.debug(f"Derived context handling failed: {dc_err}")

        # Extract sender and content
        sender_name = extract_agent_name(event)
        message_content = normalize_text_content(getattr(event, "content", None))

        # Emit synthetic speaker selection if needed
        if sender_name and sender_name != state.turn_agent:
            await self._emit_synthetic_speaker(sender_name, message_content, ctx, state)

        # Fallback sender extraction
        if not sender_name:
            sender_attr = getattr(event, "sender", None)
            if isinstance(sender_attr, str) and sender_attr.strip():
                sender_name = sender_attr.strip()
            elif hasattr(sender_attr, "name") and isinstance(getattr(sender_attr, "name"), str):
                sender_name = getattr(sender_attr, "name").strip()
        sender_name = sender_name or "Agent"

        # Seed message deduplication
        if state.is_seed_message(message_content, sender_name):
            state.consume_seed_message(message_content)
            ctx.wf_logger.debug(
                f" [{ctx.workflow_name_upper}] Suppressed seeded initial message for chat {ctx.chat_id}"
            )
            return None

        ctx.wf_logger.info(
            f" [{ctx.workflow_name_upper}] TextEvent details: sender='{sender_name}' "
            f"content='{message_content[:100]}...' content_len={len(message_content)}"
        )

        # Process structured outputs and auto-tool follow-ups
        actual_message, is_structured = await self._process_structured_output(
            sender_name, message_content, ctx, state
        )

        # Build UI payload using existing serialization
        build_ctx = EventBuildContext(
            workflow_name=ctx.workflow_name,
            turn_agent=state.turn_agent,
            tool_call_initiators=state.tool_call_initiators,
            tool_names_by_id=state.tool_names_by_id,
            workflow_name_upper=ctx.workflow_name_upper,
            wf_logger=ctx.wf_logger,
        )
        payload = build_ui_event_payload(ev=event, ctx=build_ctx)

        if payload:
            # Override content if auto-tool processed
            if actual_message != message_content:
                payload["content"] = actual_message

            if is_structured:
                payload["is_structured_capable"] = True

            # Add source tag
            if payload.get("kind") == "text" and "source" not in payload:
                payload["source"] = "ag2_textevent"

        return payload

    async def _emit_synthetic_speaker(
        self,
        sender_name: str,
        content: str,
        ctx: "StreamContext",
        state: "StreamState",
    ) -> None:
        """
        Emit synthetic select_speaker event when AG2 doesn't emit one.

        This ensures UI thinking bubbles appear for speaker transitions.
        """
        if not ctx.transport:
            return

        try:
            # Check if this is a system resume signal
            is_internal_signal = (
                isinstance(content, str)
                and any(marker in content for marker in _SYSTEM_SIGNAL_MARKERS)
            )

            # Use 'system' for internal signals instead of actual sender
            display_agent = "system" if is_internal_signal else sender_name

            synthetic_event = {
                "kind": "select_speaker",
                "agent": display_agent,
                "source": "synthetic",
                "_synthetic": True,
            }
            await ctx.transport.send_event_to_ui(synthetic_event, ctx.chat_id)
            ctx.wf_logger.debug(
                f" [SYNTHETIC_SPEAKER] Emitted synthetic select_speaker for {display_agent}"
            )
        except Exception as synth_err:
            ctx.wf_logger.warning(
                f"Failed to emit synthetic select_speaker event: {synth_err}"
            )

    async def _process_structured_output(
        self,
        sender_name: str,
        content: str,
        ctx: "StreamContext",
        state: "StreamState",
    ) -> tuple[str, bool]:
        """
        Process auto-tool structured output from agent message.

        Returns:
            Tuple of (display_message, is_structured)
        """
        if sender_name not in ctx.structured_agents:
            return content, False

        auto_mode = sender_name in ctx.auto_tool_agents
        mode_label = "auto-tool" if auto_mode else "structured-output"
        ctx.wf_logger.info(
            f" [{ctx.workflow_name_upper}] {mode_label} intercept for {sender_name} "
            f"(content_len={len(content)})"
        )

        # Try to extract structured output
        structured_blob = await self._extract_structured_output(content, sender_name, ctx)

        if not structured_blob or not isinstance(structured_blob, dict):
            ctx.wf_logger.debug(
                f" [{ctx.workflow_name_upper}] No structured content detected for {sender_name}"
            )
            return content, False

        ctx.wf_logger.info(
            f" [{ctx.workflow_name_upper}] Structured output detected for {sender_name}, "
            f"keys: {list(structured_blob.keys())}"
        )

        # Save to agent outputs file
        await self._save_agent_output(structured_blob, sender_name, ctx, state)

        # Validate against schema if available
        model_cls = ctx.structured_registry.get(sender_name)
        normalized_structured = None

        if model_cls is not None:
            try:
                validated = model_cls.model_validate(structured_blob)
                normalized_structured = validated.model_dump()
            except ValidationError as err:
                ctx.wf_logger.warning(
                    f" [{ctx.workflow_name_upper}] Structured output validation failed "
                    f"for {sender_name}: {err}"
                )
        else:
            normalized_structured = structured_blob

        if not normalized_structured:
            return content, False

        ctx.wf_logger.info(
            f" [{ctx.workflow_name_upper}] Structured output ready for {sender_name}; "
            f"emitting dispatcher event (auto_tool_mode={auto_mode})."
        )

        # Extract display message
        agent_message = normalized_structured.get("agent_message")
        if isinstance(agent_message, str) and agent_message.strip():
            display_message = agent_message.strip()
        else:
            display_message = f"{sender_name} prepared structured output."

        # Emit structured output ready event
        await self._emit_structured_output_event(
            sender_name, normalized_structured, ctx, state, auto_mode=auto_mode
        )

        return display_message, True

    async def _extract_structured_output(
        self,
        content: str,
        sender_name: str,
        ctx: "StreamContext",
    ) -> Optional[Dict[str, Any]]:
        """Extract JSON structured output from message content."""
        structured_blob = None

        # Try persistence manager extraction first
        try:
            from mozaiksai.core.data.persistence import AG2PersistenceManager as _PM
            if hasattr(_PM, "_extract_json_from_text"):
                structured_blob = _PM._extract_json_from_text(content)
                ctx.wf_logger.debug(
                    f" [{ctx.workflow_name_upper}] JSON extraction result for {sender_name}: "
                    f"{structured_blob is not None}"
                )
        except Exception as parse_err:
            ctx.wf_logger.debug(
                f" [{ctx.workflow_name_upper}] Persistence manager JSON extraction failed: {parse_err}"
            )

        # Fallback: direct JSON parsing
        if not structured_blob and isinstance(content, str):
            stripped_content = content.strip()
            try:
                if stripped_content.startswith("{") and stripped_content.endswith("}"):
                    structured_blob = json.loads(stripped_content)
                    ctx.wf_logger.debug(
                        f" [{ctx.workflow_name_upper}] Direct JSON parsing succeeded for {sender_name}"
                    )
            except json.JSONDecodeError:
                # Try extracting JSON substring
                start_idx = stripped_content.find("{")
                end_idx = stripped_content.rfind("}")
                if start_idx != -1 and end_idx > start_idx:
                    try:
                        structured_blob = json.loads(stripped_content[start_idx : end_idx + 1])
                        ctx.wf_logger.debug(
                            f" [{ctx.workflow_name_upper}] Substring JSON parsing succeeded for {sender_name}"
                        )
                    except json.JSONDecodeError:
                        pass

        return structured_blob

    async def _save_agent_output(
        self,
        structured_blob: Dict[str, Any],
        sender_name: str,
        ctx: "StreamContext",
        state: "StreamState",
    ) -> None:
        """Save structured output to agent outputs file."""
        try:
            agent_outputs_dir = Path("logs/agent_outputs")
            agent_outputs_dir.mkdir(parents=True, exist_ok=True)

            output_file = agent_outputs_dir / f"agent_outputs_{ctx.chat_id}.jsonl"

            output_entry = {
                "timestamp": datetime.now().isoformat(),
                "chat_id": ctx.chat_id,
                "workflow_name": ctx.workflow_name,
                "agent_name": sender_name,
                "sequence": state.sequence_counter,
                "output": structured_blob,
            }

            with open(output_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(output_entry, ensure_ascii=False) + "\n")

            ctx.wf_logger.debug(
                f" [{ctx.workflow_name_upper}] Saved structured output for {sender_name} to {output_file}"
            )
        except Exception as save_err:
            ctx.wf_logger.warning(
                f" [{ctx.workflow_name_upper}] Failed to save agent output: {save_err}"
            )

    async def _emit_structured_output_event(
        self,
        sender_name: str,
        normalized_structured: Dict[str, Any],
        ctx: "StreamContext",
        state: "StreamState",
        *,
        auto_mode: bool,
    ) -> None:
        """Emit structured_output_ready event through dispatcher."""
        # Build turn key for idempotency
        turn_uuid = uuid.uuid5(
            uuid.NAMESPACE_URL, f"{ctx.chat_id}:{state.sequence_counter}"
        )
        turn_key = f"turn-{turn_uuid.hex}"

        # Build context payload
        context_payload = self._build_auto_tool_context_payload(ctx, state)
        context_payload["agent_name"] = sender_name

        # Get model name
        model_name = None
        agent_obj = ctx.agents.get(sender_name)
        if agent_obj:
            model_name = getattr(agent_obj, "_mozaiks_structured_model_name", None)
        if not model_name:
            model_cls = ctx.structured_registry.get(sender_name)
            if model_cls is not None:
                model_name = getattr(model_cls, "__name__", None)

        if not model_name:
            ctx.wf_logger.warning(
                f" [{ctx.workflow_name_upper}] Unable to determine structured model name "
                f"for {sender_name}; skipping auto-tool dispatch"
            )
            return

        # Build and emit event
        structured_event = build_structured_output_ready_event(
            agent=sender_name,
            model_name=model_name,
            structured_data=normalized_structured,
            auto_tool_mode=auto_mode,
            context=context_payload,
        )
        structured_event["turn_idempotency_key"] = turn_key

        if ctx.dispatcher:
            ctx.wf_logger.info(
                f" [{ctx.workflow_name_upper}] Dispatching chat.structured_output_ready "
                f"for {sender_name} (turn_key={turn_key})"
            )
            asyncio.create_task(
                ctx.dispatcher.emit("chat.structured_output_ready", structured_event)
            )

    def _build_auto_tool_context_payload(
        self,
        ctx: "StreamContext",
        state: "StreamState",
    ) -> Dict[str, Any]:
        """Build context payload for auto-tool events."""
        payload: Dict[str, Any] = {
            "chat_id": ctx.chat_id,
            "app_id": ctx.app_id,
            "workflow_name": ctx.workflow_name,
            "turn_sequence": state.sequence_counter,
        }

        try:
            ctx_vars = ctx.context_variables
            if ctx_vars is not None:
                raw_ctx: Optional[Dict[str, Any]] = None

                if hasattr(ctx_vars, "data") and isinstance(getattr(ctx_vars, "data"), dict):
                    raw_ctx = dict(getattr(ctx_vars, "data"))
                elif hasattr(ctx_vars, "to_dict") and callable(getattr(ctx_vars, "to_dict")):
                    raw_ctx = dict(ctx_vars.to_dict())
                elif isinstance(ctx_vars, dict):
                    raw_ctx = dict(ctx_vars)

                if raw_ctx:
                    sanitized: Dict[str, Any] = {}
                    for key, value in raw_ctx.items():
                        try:
                            sanitized[key] = serialize_event_content(value)
                        except Exception:
                            sanitized[key] = str(value)
                    payload["context_variables"] = sanitized
        except Exception as ctx_err:
            ctx.wf_logger.debug(
                f" [{ctx.workflow_name_upper}] Auto-tool context snapshot failed: {ctx_err}"
            )

        return payload

    def should_break(self, event: Any, state: "StreamState") -> bool:
        """TextEvent does not terminate the stream."""
        return False

    def priority(self) -> int:
        """Standard priority."""
        return 50
