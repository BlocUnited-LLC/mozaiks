# ==============================================================================
# FILE: mozaiksai/core/workflow/stream/handlers/text_handler.py
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
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, Optional, Set, Type

from pydantic import ValidationError
from logs.runtime_artifacts import get_agent_outputs_dir

from .base import BaseEventHandler

if TYPE_CHECKING:
    from ..context import StreamContext, StreamState

# Import AG2 event types
from autogen.events.agent_events import TextEvent
from autogen.events.print_event import PrintEvent

# Import serialization utilities
from mozaiksai.core.events.event_serialization import (
    build_ui_event_payload,
    EventBuildContext,
    extract_agent_name,
    normalize_text_content,
    serialize_event_content,
)
from mozaiksai.core.events.ag2_events import emit_decomposition_planned, emit_structured_output
from mozaiksai.core.events.runtime_events import (
    RUNTIME_AGENT_OUTPUT_VALIDATED,
    RUNTIME_DECOMPOSITION_PLANNED,
    build_runtime_agent_output_validated_event,
    build_runtime_context_payload,
    build_runtime_decomposition_planned_event,
    build_turn_idempotency_key,
)
from mozaiksai.core.multitenant import build_app_scope_filter
from mozaiksai.core.ports.orchestration import DomainEvent
from mozaiksai.core.workflow.pack.resume_contract import (
    MFJ_RESUME_PENDING_KEY,
    MFJ_RESUME_TARGET_KEY,
    mark_resume_consumed,
)
from mozaiksai.core.workflow.runtime_signals import SYSTEM_RESUME_SIGNAL


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
        return {TextEvent, PrintEvent}

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
        sender_name = sender_name or state.turn_agent or "Agent"
        sender_name = self._resolve_validated_output_sender(sender_name, ctx, state) or sender_name
        sender_name_lower = sender_name.strip().lower()
        state.last_text_role = (
            "user" if sender_name_lower in {"user", "userproxy", "chat_manager", "manager", "agentmanager"} else "assistant"
        )
        state.last_text_content = message_content

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
                and SYSTEM_RESUME_SIGNAL in content
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
        if sender_name not in ctx.validated_output_agents:
            return content, False

        auto_tool_call_enabled = sender_name in ctx.auto_tool_agents
        mode_label = "auto-tool" if auto_tool_call_enabled else "structured-output"
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
                normalized_structured = validated.model_dump(mode="json")
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
            f"emitting dispatcher event (auto_tool_call={auto_tool_call_enabled})."
        )

        # Extract display message
        agent_message = normalized_structured.get("agent_message")
        if isinstance(agent_message, str) and agent_message.strip():
            display_message = agent_message.strip()
        else:
            display_message = f"{sender_name} prepared structured output."

        # Emit agent output validated event
        await self._emit_agent_output_validated(
            sender_name,
            normalized_structured,
            ctx,
            state,
            auto_tool_call_enabled=auto_tool_call_enabled,
        )
        await self._emit_decomposition_planned(
            sender_name, normalized_structured, ctx, state
        )
        await self._consume_resume_contract_if_needed(sender_name, ctx)

        return display_message, True

    async def _consume_resume_contract_if_needed(
        self,
        sender_name: str,
        ctx: "StreamContext",
    ) -> None:
        """Mark MFJ resume metadata consumed once the target agent actually replies."""
        ctx_vars = ctx.context_variables
        if ctx_vars is None:
            return

        try:
            resume_pending = bool(ctx_vars.get(MFJ_RESUME_PENDING_KEY)) if hasattr(ctx_vars, "get") else False
        except Exception:
            resume_pending = False
        if not resume_pending:
            return

        try:
            resume_target = ctx_vars.get(MFJ_RESUME_TARGET_KEY) if hasattr(ctx_vars, "get") else None
        except Exception:
            resume_target = None
        if not isinstance(resume_target, str) or resume_target.strip() != sender_name:
            return

        updates = mark_resume_consumed(ctx_vars)
        if not updates:
            return

        try:
            coll = await ctx.persistence_manager._coll()
            await coll.update_one(
                {"_id": ctx.chat_id, **build_app_scope_filter(ctx.app_id)},
                {"$set": updates},
            )
        except Exception as persist_err:
            ctx.wf_logger.warning(
                f" [{ctx.workflow_name_upper}] Failed to persist MFJ resume consumption: {persist_err}"
            )
            return

        ctx.wf_logger.info(
            f" [{ctx.workflow_name_upper}] Marked MFJ resume consumed for {sender_name}"
        )

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
            agent_outputs_dir = get_agent_outputs_dir()
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

    async def _emit_agent_output_validated(
        self,
        sender_name: str,
        normalized_structured: Dict[str, Any],
        ctx: "StreamContext",
        state: "StreamState",
        *,
        auto_tool_call_enabled: bool,
    ) -> None:
        """Emit canonical runtime.agent_output_validated event through dispatcher."""
        turn_key = build_turn_idempotency_key(ctx.chat_id, state.sequence_counter)

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
        validated_event = build_runtime_agent_output_validated_event(
            agent=sender_name,
            model_name=model_name,
            structured_data=normalized_structured,
            auto_tool_call=auto_tool_call_enabled,
            context=context_payload,
            turn_idempotency_key=turn_key,
            pattern_context_ref=ctx.context_variables,
        )

        if ctx.dispatcher:
            ctx.wf_logger.info(
                f" [{ctx.workflow_name_upper}] Dispatching {RUNTIME_AGENT_OUTPUT_VALIDATED} "
                f"for {sender_name} (turn_key={turn_key})"
            )
            await ctx.dispatcher.emit(RUNTIME_AGENT_OUTPUT_VALIDATED, validated_event)

        emitted_via_ag2 = emit_structured_output(
            agent_name=sender_name,
            chat_id=ctx.chat_id,
            output_type=model_name,
            output_data=normalized_structured,
            validation_passed=True,
        )
        if emitted_via_ag2:
            ctx.wf_logger.info(
                f" [{ctx.workflow_name_upper}] Emitted AG2 structured output checkpoint for {sender_name}"
            )

    async def _emit_decomposition_planned(
        self,
        sender_name: str,
        normalized_structured: Dict[str, Any],
        ctx: "StreamContext",
        state: "StreamState",
    ) -> None:
        """Emit explicit decomposition event for MFJ-capable workflows."""
        if not self._should_emit_decomposition_event(sender_name, normalized_structured, ctx):
            return

        model_name = None
        agent_obj = ctx.agents.get(sender_name)
        if agent_obj:
            model_name = getattr(agent_obj, "_mozaiks_structured_model_name", None)
        if not model_name:
            model_cls = ctx.structured_registry.get(sender_name)
            if model_cls is not None:
                model_name = getattr(model_cls, "__name__", None)
        if not model_name:
            return

        payload = build_runtime_decomposition_planned_event(
            agent=sender_name,
            model_name=model_name,
            structured_data=normalized_structured,
            context=self._build_auto_tool_context_payload(ctx, state),
        )

        emitted_via_ag2 = emit_decomposition_planned(
            agent_name=sender_name,
            chat_id=ctx.chat_id,
            workflow_name=ctx.workflow_name,
            model_name=model_name,
            structured_data=normalized_structured,
            context=payload.get("context") if isinstance(payload.get("context"), dict) else {},
        )
        if emitted_via_ag2:
            ctx.wf_logger.info(
                f" [{ctx.workflow_name_upper}] Emitted AG2 decomposition checkpoint for {sender_name}"
            )
            return

        if not ctx.dispatcher:
            return

        domain_event = DomainEvent(
            kind=RUNTIME_DECOMPOSITION_PLANNED,
            payload=payload,
            chat_id=ctx.chat_id,
            source="runtime",
        )
        ctx.wf_logger.info(
            f" [{ctx.workflow_name_upper}] Dispatching fallback {RUNTIME_DECOMPOSITION_PLANNED} "
            f"for {sender_name}"
        )
        await ctx.dispatcher.emit_domain_event(domain_event)

    def _should_emit_decomposition_event(
        self,
        sender_name: str,
        normalized_structured: Dict[str, Any],
        ctx: "StreamContext",
    ) -> bool:
        """Return True when the workflow declares sender_name as an MFJ planner."""
        if not isinstance(normalized_structured.get("workflows"), list):
            return False

        try:
            from mozaiksai.core.workflow.pack.config import load_workflow_pack_graph

            graph = load_workflow_pack_graph(ctx.workflow_name)
        except Exception:
            return False

        if graph is None:
            return False

        for entry in graph.mid_flight_journeys:
            if entry.trigger_on != "decomposition_event":
                continue
            if entry.decomposition_agent == sender_name:
                return True
        return False

    def _build_auto_tool_context_payload(
        self,
        ctx: "StreamContext",
        state: "StreamState",
    ) -> Dict[str, Any]:
        """Build context payload for auto-tool events."""
        try:
            return build_runtime_context_payload(
                chat_id=ctx.chat_id,
                app_id=ctx.app_id,
                workflow_name=ctx.workflow_name,
                user_id=ctx.user_id,
                turn_sequence=state.sequence_counter,
                context_variables=ctx.context_variables,
            )
        except Exception as ctx_err:
            ctx.wf_logger.debug(
                f" [{ctx.workflow_name_upper}] Auto-tool context snapshot failed: {ctx_err}"
            )
            return {
                "chat_id": ctx.chat_id,
                "app_id": ctx.app_id,
                "workflow_name": ctx.workflow_name,
                "turn_sequence": state.sequence_counter,
            }

    def _resolve_validated_output_sender(
        self,
        sender_name: str,
        ctx: "StreamContext",
        state: "StreamState",
    ) -> Optional[str]:
        normalized_sender = str(sender_name or "").strip()
        if normalized_sender in ctx.validated_output_agents:
            return normalized_sender

        candidates: list[str] = []
        preferred_resume_target: Optional[str] = None

        if isinstance(state.turn_agent, str) and state.turn_agent in ctx.validated_output_agents:
            candidates.append(state.turn_agent)

        ctx_vars = ctx.context_variables
        if ctx_vars is not None:
            resume_target = None
            try:
                if hasattr(ctx_vars, "get"):
                    resume_target = ctx_vars.get("_mfj_resume_target_agent")
            except Exception:
                resume_target = None
            if isinstance(resume_target, str) and resume_target in ctx.validated_output_agents:
                preferred_resume_target = resume_target
                candidates.append(resume_target)

        try:
            from mozaiksai.core.workflow.workflow_manager import workflow_manager

            workflow_cfg = workflow_manager.get_config(ctx.workflow_name) or {}
            initial_agent = workflow_cfg.get("initial_agent")
            if isinstance(initial_agent, str) and initial_agent in ctx.validated_output_agents:
                candidates.append(initial_agent)
        except Exception:
            pass

        deduped = []
        for candidate in candidates:
            if candidate not in deduped:
                deduped.append(candidate)

        if preferred_resume_target and preferred_resume_target in deduped:
            return preferred_resume_target

        if len(deduped) == 1:
            return deduped[0]
        return None

    def should_break(self, event: Any, state: "StreamState") -> bool:
        """TextEvent does not terminate the stream."""
        return False
