# ==============================================================================
# FILE: core/workflow/orchestration_patterns.py
# DESCRIPTION: COMPLETE AG2 execution engine - Single-responsibility pattern for all workflow orchestration
# ==============================================================================

"""
MozaiksAI Orchestration Engine (organized)

Purpose
- Single entry point to run a workflow using AG2 patterns with streaming, tools, persistence, and perforamnce.

Sections (skim map)
- Logging setup (chat/workflow/perf)
- run_workflow_orchestration: main orchestration contract and steps
- create_orchestration_pattern: AG2 pattern factory
- logging helpers: agent message details and full conversation logging
"""

from typing import Dict, List, Optional, Any, Callable
import os
import uuid
from datetime import datetime, UTC
import logging
import time
from time import perf_counter
import asyncio
import json
from collections import Counter

from pydantic import ValidationError

from mozaiksai.engine.outputs import get_structured_outputs_for_workflow
from mozaiksai.adapters.ag2.event_builders import (
    build_ui_event_payload as unified_build_ui_event_payload,
    EventBuildContext as UnifiedEventBuildContext,
    build_select_speaker_ui_dict,
    build_structured_output_ready_event,
    serialize_event_content,
)

from .execution import LifecycleTrigger
from logs.logging_config import get_workflow_logger
from mozaiksai.engine.observability.runtime_logger import ag2_logging_session
from mozaiksai.kernel.dispatcher import get_event_dispatcher

from .validation import SENTINEL_STATUS

from .messages import (
    normalize_text_content as _normalize_text_content,
    safe_context_snapshot as _safe_context_snapshot,
)
from .execution import create_ag2_pattern

logger = logging.getLogger(__name__)

# Module-level logger for orchestration events
chat_logger = get_workflow_logger("orchestration")


# Known internal system coordination markers to ensure consistent UI labeling.
_SYSTEM_SIGNAL_MARKERS: tuple[str, ...] = ("[SYSTEM_RESUME_SIGNAL]",)


__all__ = [
    'run_workflow_orchestration',
    'create_ag2_pattern'
]

# ===================================================================
# AG2 INTERNAL LOGGING CONFIGURATION
# ===================================================================
# Set AG2 internal logging to INFO level for production
logging.getLogger("autogen.agentchat").setLevel(logging.INFO)
logging.getLogger("autogen.io").setLevel(logging.INFO)
logging.getLogger("autogen.agentchat.group").setLevel(logging.INFO)

# ===================================================================
# HEALTH ENDPOINT SUPPORT
# ===================================================================
# NOTE: get_run_registry_summary has moved to
#       mozaiksai.runtime.observability.run_registry
# ===================================================================

# ===================================================================
# NOTE: Helper functions have been extracted to separate modules:
# - message_utils.py: Message normalization, text extraction, agent name resolution
# - event_payload_builder.py: UI event payload construction
# - pattern_factory.py: AG2 pattern creation
# This refactoring reduces orchestration_patterns.py from 2800+ to ~2000 lines
# and improves maintainability through separation of concerns.
# ===================================================================

# ===================================================================
# ORCHESTRATION HELPERS — moved to engine/executor/groupchat_executor.py
# ===================================================================
from mozaiksai.engine.executor import GroupChatExecutor, PreparedRun


async def _stream_events(run: PreparedRun):
    """Stream AG2 events, forwarding them to transport/UI and persisting as needed.

    Accepts a :class:`PreparedRun` produced by :class:`GroupChatExecutor`.
    The AG2 launch (resume / new-run) has already completed; this function only
    iterates ``run.response.events`` and handles transport/persistence/lifecycle.
    """
    # Unpack from PreparedRun -------------------------------------------------
    pattern = run.pattern
    agents = run.agents
    response = run.response
    chat_id = run.chat_id
    app_id = run.app_id
    workflow_name = run.workflow_name
    workflow_name_upper = run.workflow_name_upper
    transport = run.transport
    user_id = run.user_id
    persistence_manager = run.persistence_manager
    perf_mgr = run.perf_mgr
    derived_context_manager = run.derived_context_manager
    lifecycle_manager = run.lifecycle_manager
    resumed_messages = run.resumed_messages
    initial_messages = run.initial_messages
    wf_logger = run.wf_logger or get_workflow_logger(workflow_name, chat_id=chat_id, app_id=app_id)

    # Event adapter (AG2-free) ------------------------------------------------
    from mozaiksai.engine.streaming.ag2_event_adapter import translate as _translate_ag2_event
    from mozaiksai.engine.streaming.domain_event import EventKind

    try:
        structured_registry = get_structured_outputs_for_workflow(workflow_name)
    except Exception as so_err:
        structured_registry = {}
        wf_logger.debug(f"[{workflow_name_upper}] Structured outputs unavailable: {so_err}")

    auto_tool_agents = {name for name, agent in agents.items() if getattr(agent, '_mozaiks_auto_tool_mode', False)}
    if auto_tool_agents:
        wf_logger.info(f" [{workflow_name_upper}] Auto-tool agents detected: {sorted(auto_tool_agents)}")
    else:
        wf_logger.debug(f" [{workflow_name_upper}] No auto-tool agents registered for this run.")
    dispatcher = get_event_dispatcher()

    resumed_mode = bool(resumed_messages)
    # Log which context keys are present at stream start for diagnostics
    try:
        gm_ctx_keys = []
        gm = getattr(pattern, "group_manager", None)
        if gm and hasattr(gm, "context_variables"):
            cv = getattr(gm, "context_variables")
            if hasattr(cv, "data") and isinstance(getattr(cv, "data"), dict):
                gm_ctx_keys = list(cv.data.keys())
            elif hasattr(cv, "to_dict"):
                gm_ctx_keys = list(cv.to_dict().keys())
        wf_logger.info(f" [EVENTS_INIT] ContextVariables available at start | keys={gm_ctx_keys}")
    except Exception as _ctx_log_err:
        wf_logger.debug(f"[EVENTS_INIT] ContextVariables keys logging skipped: {_ctx_log_err}")

    turn_agent: Optional[str] = None
    turn_started: Optional[float] = None
    sequence_counter = 0
    first_event_logged = False
    chat_logger.info(f"[EVENT_STREAM] Starting event processing loop for chat {chat_id}")

    pending_input_requests: dict[str, Any] = {}
    # Track which agent initiated each tool call so we can echo it on the response if missing
    tool_call_initiators: dict[str, str] = {}
    # Track tool names by id so responses can be labeled even if AG2 omits tool_name
    tool_names_by_id: dict[str, str] = {}
    # Track schema validation retries per call/agent so we avoid infinite loops
    schema_retry_tracker: dict[str, int] = {}
    MAX_SCHEMA_RETRIES = 2
    # Tracks which sequence numbers have already been forwarded to the UI.
    # Replaces the previous pattern of annotating raw AG2 event objects with
    # `setattr(ev, "_mozaiks_forwarded", True)`.
    forwarded_seqs: set[int] = set()
    # Per-event metadata populated in event-specific branches and consumed by
    # build_ui_event_payload via EventBuildContext.  Avoids setattr on raw AG2 events.
    latest_request_id: Optional[str] = None
    latest_prompt_hint: Optional[str] = None

    def _build_auto_tool_context_payload(turn_sequence: int) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "app_id": app_id,
            "workflow_name": workflow_name,
            "turn_sequence": turn_sequence,
        }
        try:
            ctx_source = None
            gm_candidate = getattr(pattern, "group_manager", None)
            if gm_candidate and hasattr(gm_candidate, "context_variables"):
                ctx_source = getattr(gm_candidate, "context_variables")
            elif hasattr(pattern, "context_variables"):
                ctx_source = getattr(pattern, "context_variables")

            raw_ctx: Optional[Dict[str, Any]] = None
            if ctx_source is not None:
                if hasattr(ctx_source, "data") and isinstance(getattr(ctx_source, "data"), dict):
                    raw_ctx = dict(getattr(ctx_source, "data"))  # type: ignore[arg-type]
                elif hasattr(ctx_source, "to_dict") and callable(getattr(ctx_source, "to_dict")):
                    raw_ctx = dict(ctx_source.to_dict())  # type: ignore[arg-type]
                elif isinstance(ctx_source, dict):
                    raw_ctx = dict(ctx_source)

            if raw_ctx:
                sanitized: Dict[str, Any] = {}
                for key, value in raw_ctx.items():
                    try:
                        sanitized[key] = serialize_event_content(value)
                    except Exception:
                        sanitized[key] = str(value)
                payload["context_variables"] = sanitized
        except Exception as ctx_err:
            wf_logger.debug(
                f" [{workflow_name_upper}] Auto-tool context snapshot failed: {ctx_err}"
            )
        return payload

    def _resolve_agent_object(agent_name: Optional[str]):
        if not agent_name:
            return None
        if agent_name in agents:
            return agents[agent_name]
        for candidate in agents.values():
            if getattr(candidate, "name", None) == agent_name:
                return candidate
        return None
    # ------------------------------------------------------------------
    # Verbose context diff support (optional via CONTEXT_VERBOSE_DEBUG=1)
    # ------------------------------------------------------------------
    verbose_ctx = os.getenv("CONTEXT_VERBOSE_DEBUG", "0").strip() in {"1", "true", "True"}
    prev_ctx_snapshot: Dict[str, Any] = {}
    if verbose_ctx:
        try:
            gm0 = getattr(pattern, "group_manager", None)
            base_ctx = None
            if gm0 and hasattr(gm0, "context_variables"):
                base_ctx = getattr(gm0, "context_variables")
            elif hasattr(pattern, "context_variables"):
                base_ctx = getattr(pattern, "context_variables")
            prev_ctx_snapshot = _safe_context_snapshot(base_ctx) if base_ctx else {}
            wf_logger.info(f" [CONTEXT_VERBOSE] Baseline snapshot captured | keys={len(prev_ctx_snapshot)}")
        except Exception as _init_snap_err:
            wf_logger.debug(f" [CONTEXT_VERBOSE] baseline snapshot failed: {_init_snap_err}")


    seed_user_messages = Counter()
    try:
        for seed in initial_messages or []:
            if (
                isinstance(seed, dict)
                and seed.get('role') == 'user'
                and seed.get('_mozaiks_seed_kind') == 'initial_message'
            ):
                content = seed.get('content')
                if isinstance(content, str) and content.strip():
                    seed_user_messages[content.strip()] += 1
    except Exception:
        seed_user_messages = Counter()

    try:
        if transport:
            transport.register_orchestration_input_registry(chat_id, pending_input_requests)  # type: ignore[attr-defined]
    except Exception as e:
        logger.debug(f"Failed to register orchestration input registry for {chat_id}: {e}")

    from .outputs.ui_tools import handle_tool_call_for_ui_interaction
    
    # Initialize stream state tracking
    stream_state: Dict[str, Any] = {
        "run_completed": False,
        "completion_event": None,
    }
    
    try:
        executed_agents: set[str] = set()
        async for ev in response.events:  # type: ignore[attr-defined]
            sequence_counter += 1
            ev_type_name = type(ev).__name__
            # Reset per-event hints; populated in event-specific branches below
            latest_request_id = None
            latest_prompt_hint = None

            # Translate raw AG2 event → engine-agnostic DomainEvent
            domain_ev = _translate_ag2_event(ev, sequence=sequence_counter)

            if not first_event_logged:
                wf_logger.info(
                    f" [{workflow_name_upper}] First event received: {domain_ev.kind} chat_id={chat_id}"
                )
                first_event_logged = True
            
            # Comprehensive event tracing for debugging
            if domain_ev.kind == EventKind.TEXT:
                content_preview = str(domain_ev.content)[:100] if domain_ev.content else 'None'
                wf_logger.debug(
                    f" [EVENT_TRACE] {domain_ev.kind} from {domain_ev.agent}: "
                    f"content_len={len(str(domain_ev.content)) if domain_ev.content else 0} preview='{content_preview}...'"
                )
            else:
                wf_logger.debug(f" [EVENT_TRACE] {domain_ev.kind} event received")
            # Context diffing relies on prev_ctx_snapshot captured before the loop; no per-event copy needed here.
            # TextEvent persistence + forwarding (wrapped in tight try so other event types continue on failure)
            if domain_ev.kind == EventKind.TEXT:
                try:
                    await persistence_manager.save_event(domain_ev, chat_id, app_id)
                    if derived_context_manager:
                        derived_context_manager.handle_event(domain_ev.raw)

                    # Forward TextEvent to UI via WebSocket (inner try isolates transport issues)
                    try:
                        from mozaiksai.transport.websocket.handler import SimpleTransport
                        transport = await SimpleTransport.get_instance()
                        if transport:
                            sender_name = domain_ev.agent
                            
                            # SYNTHETIC SELECT_SPEAKER: When AG2 doesn't emit SelectSpeakerEvent (e.g., after lifecycle resume),
                            # we synthesize one to ensure thinking bubbles appear in the UI
                            if sender_name and sender_name != turn_agent:
                                try:
                                    # Check if this is a system resume signal (internal coordination message)
                                    message_content = _normalize_text_content(domain_ev.content)
                                    is_internal_signal = (
                                        isinstance(message_content, str)
                                        and any(marker in message_content for marker in _SYSTEM_SIGNAL_MARKERS)
                                    )
                                    
                                    # Use 'system' instead of resume sender for internal coordination signals
                                    display_agent = 'system' if is_internal_signal else sender_name
                                    
                                    synthetic_select_event = {
                                        "kind": "select_speaker",
                                        "agent": display_agent,
                                        "source": "synthetic",
                                        "_synthetic": True,
                                    }
                                    await transport.send_event_to_ui(synthetic_select_event, chat_id)
                                    wf_logger.debug(f"🎭 [SYNTHETIC_SPEAKER] Emitted synthetic select_speaker for {display_agent}")
                                except Exception as synth_err:
                                    wf_logger.warning(f"Failed to emit synthetic select_speaker event: {synth_err}")
                            sender_name = sender_name or 'Agent'
                            message_content = _normalize_text_content(domain_ev.content)
                            content_key = message_content.strip()
                            sender_lower = sender_name.lower() if isinstance(sender_name, str) else ''
                            if content_key and seed_user_messages.get(content_key) and sender_lower in {'user', 'chat_manager', 'manager', 'agentmanager'}:
                                seed_user_messages[content_key] -= 1
                                if seed_user_messages[content_key] <= 0:
                                    seed_user_messages.pop(content_key, None)
                                wf_logger.debug(f" [{workflow_name_upper}] Suppressed seeded initial message for chat {chat_id}")
                                continue

                            wf_logger.info(
                                f" [{workflow_name_upper}] TextEvent details: sender='{sender_name}' content='{message_content[:100]}...' "
                                f"content_len={len(message_content)} has_sender={hasattr(ev, 'sender')} has_content={hasattr(ev, 'content')}"
                            )
                            
                            wf_logger.info(f" [{workflow_name_upper}] 🚨 CHECKPOINT A: About to check auto-tool intercept")
                            
                            # AUTO-TOOL INTERCEPT: Process structured outputs from auto-tool agents before UI forwarding
                            import uuid
                            actual_message_to_send = message_content
                            
                            wf_logger.info(f" [{workflow_name_upper}] 🚨 CHECKPOINT B: Variables initialized, checking auto_tool_agents")
                            try:
                                wf_logger.info(f" [{workflow_name_upper}] Auto-tool debug: sender_name='{sender_name}' type={type(sender_name)}")
                                wf_logger.info(f" [{workflow_name_upper}] Auto-tool debug: auto_tool_agents={auto_tool_agents}")
                                wf_logger.info(f" [{workflow_name_upper}] Auto-tool debug: sender in agents? {sender_name in auto_tool_agents}")
                            except Exception as debug_err:
                                wf_logger.error(f" [{workflow_name_upper}] Debug logging error: {debug_err}")
                            if sender_name in auto_tool_agents:
                                wf_logger.info(f" [{workflow_name_upper}] Auto-tool intercept for {sender_name} (content_len={len(message_content)})")
                                
                                # Try to extract structured output from message content
                                structured_blob = None
                                try:
                                    from mozaiksai.runtime.data.persistence.persistence_manager import AG2PersistenceManager as _PM
                                    if hasattr(_PM, '_extract_json_from_text'):
                                        structured_blob = _PM._extract_json_from_text(message_content)
                                        wf_logger.info(f" [{workflow_name_upper}] JSON extraction result for {sender_name}: {structured_blob is not None}")
                                    
                                    if not structured_blob and isinstance(message_content, str):
                                        # Fallback: direct JSON parsing
                                        import json
                                        try:
                                            stripped_content = message_content.strip()
                                            if stripped_content.startswith('{') and stripped_content.endswith('}'):
                                                structured_blob = json.loads(stripped_content)
                                                wf_logger.info(f" [{workflow_name_upper}] Direct JSON parsing succeeded for {sender_name}")
                                        except json.JSONDecodeError:
                                            pass
                                    
                                    if structured_blob and isinstance(structured_blob, dict):
                                        # We have valid structured output - process it
                                        wf_logger.info(f" [{workflow_name_upper}] Structured output detected for {sender_name}, keys: {list(structured_blob.keys())}")
                                        
                                        # Save full structured output to dedicated agent outputs file
                                        try:
                                            from pathlib import Path
                                            from datetime import datetime
                                            import json as _json
                                            
                                            agent_outputs_dir = Path("logs/agent_outputs")
                                            agent_outputs_dir.mkdir(parents=True, exist_ok=True)
                                            
                                            # One file per chat session for all agent outputs
                                            output_file = agent_outputs_dir / f"agent_outputs_{chat_id}.jsonl"
                                            
                                            # Append as JSONL (one JSON object per line)
                                            output_entry = {
                                                "timestamp": datetime.now().isoformat(),
                                                "chat_id": chat_id,
                                                "workflow_name": workflow_name,
                                                "agent_name": sender_name,
                                                "sequence": sequence_counter,
                                                "output": structured_blob
                                            }
                                            
                                            with open(output_file, 'a', encoding='utf-8') as f:
                                                f.write(_json.dumps(output_entry, ensure_ascii=False) + '\n')
                                            
                                            wf_logger.debug(f" [{workflow_name_upper}] 💾 Saved {sender_name} output to {output_file}")
                                        except Exception as save_err:
                                            wf_logger.debug(f" [{workflow_name_upper}] Failed to save agent output: {save_err}")
                                        
                                        # Log preview to console
                                        try:
                                            import json
                                            preview = json.dumps(structured_blob, indent=2)[:1000]
                                            wf_logger.info(f" [{workflow_name_upper}] 📋 STRUCTURED OUTPUT from {sender_name}:")
                                            wf_logger.info(f" {preview}{'...' if len(json.dumps(structured_blob)) > 1000 else ''}")
                                        except Exception:
                                            pass
                                        
                                        # Extract friendly message from structured output
                                        agent_message = structured_blob.get("agent_message")
                                        if isinstance(agent_message, str) and agent_message.strip():
                                            actual_message_to_send = agent_message.strip()
                                            wf_logger.info(f" [{workflow_name_upper}] Using agent_message as display text: '{actual_message_to_send[:100]}...'")
                                        else:
                                            actual_message_to_send = f"{sender_name} prepared structured output."
                                            wf_logger.info(f" [{workflow_name_upper}] Using fallback display message for {sender_name}")
                                        
                                        # Emit structured_output_ready event
                                        try:
                                            turn_uuid = uuid.uuid5(uuid.NAMESPACE_URL, f"{chat_id}:{sequence_counter}")
                                            turn_key = f"turn-{turn_uuid.hex}"
                                            context_payload = _build_auto_tool_context_payload(sequence_counter)
                                            # Add agent_name to context so auto-tool handler can inject it into context_variables
                                            context_payload["agent_name"] = sender_name
                                            
                                            # Get the correct model name from structured outputs registry
                                            model_name = structured_registry.get(sender_name)
                                            if model_name and hasattr(model_name, '__name__'):
                                                model_name = model_name.__name__
                                            else:
                                                model_name = "UnknownModel"
                                            
                                            from mozaiksai.adapters.ag2.event_builders import build_structured_output_ready_event
                                            structured_event = build_structured_output_ready_event(
                                                agent=sender_name,
                                                model_name=model_name,
                                                structured_data=structured_blob,
                                                auto_tool_mode=True,
                                                context=context_payload,
                                            )
                                            structured_event["turn_idempotency_key"] = turn_key
                                            
                                            # Attach pattern context reference for auto-tool write-back
                                            try:
                                                gm_candidate = getattr(pattern, "group_manager", None)
                                                if gm_candidate and hasattr(gm_candidate, "context_variables"):
                                                    structured_event["_pattern_context_ref"] = getattr(gm_candidate, "context_variables")
                                                elif hasattr(pattern, "context_variables"):
                                                    structured_event["_pattern_context_ref"] = getattr(pattern, "context_variables")
                                            except Exception:
                                                pass
                                            
                                            if dispatcher:
                                                wf_logger.info(f" [{workflow_name_upper}] Dispatching structured_output_ready for {sender_name} (turn_key={turn_key})")
                                                import asyncio
                                                asyncio.create_task(dispatcher.emit("chat.structured_output_ready", structured_event))
                                        except Exception as struct_err:
                                            wf_logger.warning(f" [{workflow_name_upper}] Failed to emit structured_output_ready for {sender_name}: {struct_err}")
                                    else:
                                        wf_logger.debug(f" [{workflow_name_upper}] No valid structured output found for {sender_name}")
                                        
                                except Exception as auto_tool_err:
                                    wf_logger.warning(f" [{workflow_name_upper}] Auto-tool processing failed for {sender_name}: {auto_tool_err}")
                            
                            try:
                                await transport.send_chat_message(
                                    message=actual_message_to_send,
                                    agent_name=sender_name,
                                    chat_id=chat_id,
                                    metadata={"source": "ag2_textevent", "sequence": sequence_counter}
                                )
                                forwarded_seqs.add(sequence_counter)
                            except TypeError as te:
                                # Known intermittent issue: some AG2 TextEvent objects (older versions / mixed reload state)
                                # bubble a "'TextEvent' object is not subscriptable" error if a downstream layer
                                # accidentally treats them like dicts. Provide a resilient fallback path.
                                if "TextEvent" in str(te) and "not subscriptable" in str(te):
                                    wf_logger.warning(
                                        f" [{workflow_name_upper}] Fallback serialization for TextEvent (subscriptable TypeError) sender={sender_name} len={len(message_content)}"
                                    )
                                    # Build a normalized 'kind' payload so dispatcher fast-path handles it
                                    fallback_payload = {
                                        "kind": "text",
                                        "agent": sender_name,
                                        "content": message_content,
                                        "_fallback": True,
                                        "sequence": sequence_counter,
                                        "source": "textevent_fallback",
                                    }
                                    try:
                                        await transport.send_event_to_ui(fallback_payload, chat_id)
                                        wf_logger.info(f" [{workflow_name_upper}] Fallback TextEvent forwarded successfully sender={sender_name}")
                                    except Exception as fallback_err:
                                        wf_logger.error(
                                            f" [{workflow_name_upper}] Fallback TextEvent forwarding failed sender={sender_name}: {fallback_err}"
                                        )
                                else:
                                    # Re-raise unexpected TypeError
                                    raise
                            wf_logger.info(
                                f" [{workflow_name_upper}] TextEvent forwarded to UI: sender='{sender_name}' message_len={len(message_content)}"
                            )
                            
                            # LIFECYCLE TRIGGER: after_agent
                            # Execute after_agent lifecycle tools when an agent completes its turn
                            try:
                                gm_ctx = getattr(pattern, "group_manager", None)
                                active_ctx = getattr(gm_ctx, "context_variables", None) if gm_ctx else None
                                if not active_ctx and hasattr(pattern, "context_variables"):
                                    active_ctx = getattr(pattern, "context_variables")
                                
                                await lifecycle_manager.execute_trigger(
                                    trigger=LifecycleTrigger.AFTER_AGENT,
                                    workflow_name=workflow_name,
                                    agent_name=sender_name,
                                    agent_output=message_content,
                                    chat_id=chat_id,
                                    app_id=app_id,
                                    context_variables=active_ctx,
                                    sequence_number=sequence_counter,
                                )
                            except Exception as lc_err:
                                wf_logger.debug(f" [{workflow_name_upper}] after_agent lifecycle tools failed: {lc_err}")
                            
                    except Exception as transport_err:
                        import traceback as _tb
                        logger.warning(
                            f"Failed to forward TextEvent to UI for {chat_id}: {transport_err}\n"
                            f"Type={type(transport_err).__name__} Traceback:\n{_tb.format_exc()}"
                        )
                except Exception as e:
                    logger.warning(f"Failed to persist/handle TextEvent for {chat_id}: {e}")

            # AG2 FunctionCallEvent/ToolCallEvent are handled by the executor
            # Tools emit UI artifacts via use_ui_tool() calls

            if domain_ev.kind == EventKind.SELECT_SPEAKER:
                # Forward SelectSpeakerEvent to UI transport for thinking bubbles
                try:
                    if transport:
                        _speaker_event = {
                            "kind": "select_speaker",
                            "agent": domain_ev.agent,
                            "selected_agent": domain_ev.metadata.get("selected_agent"),
                        }
                        await transport.send_event_to_ui(_speaker_event, chat_id)
                        wf_logger.debug(f"🎭 [SPEAKER_SELECT] Forwarded to UI: {domain_ev.agent}")
                except Exception as transport_err:
                    wf_logger.warning(f"Failed to forward SelectSpeakerEvent to UI: {transport_err}")

                # Update realtime logger context when speaker changes
                try:
                    next_agent_name = domain_ev.metadata.get("selected_agent") or domain_ev.agent
                    if next_agent_name:
                        from mozaiksai.engine.observability.token_logger import get_realtime_token_logger
                        realtime_logger = get_realtime_token_logger()
                        realtime_logger.set_active_agent(next_agent_name)
                        wf_logger.debug(f"[REALTIME_TOKENS] Context updated for agent: {next_agent_name}")
                except Exception as ctx_err:
                    wf_logger.debug(f"Failed to update realtime token context: {ctx_err}")

                # LIFECYCLE TRIGGER: after_agent (for previous agent)
                # Execute after_agent lifecycle tools when previous agent's turn completes
                if turn_agent and turn_started is not None:
                    duration = max(0.0, time.perf_counter() - turn_started)
                    # Record agent turn performance
                    try:
                        await perf_mgr.record_agent_turn(
                            chat_id=chat_id,
                            agent_name=turn_agent,
                            duration_sec=duration,
                            model=None,
                        )
                    except Exception as perf_err:
                        logger.warning(f"Failed to record turn for {turn_agent}: {perf_err}")
                    
                    # Execute after_agent lifecycle tools
                    if lifecycle_manager:
                        try:
                            gm_ctx = getattr(pattern, "group_manager", None)
                            active_ctx = getattr(gm_ctx, "context_variables", None) if gm_ctx else None
                            if not active_ctx and hasattr(pattern, "context_variables"):
                                active_ctx = getattr(pattern, "context_variables")
                            
                            await lifecycle_manager.trigger_after_agent(
                                agent_name=str(turn_agent),
                                context_variables=active_ctx,
                            )
                        except Exception as lc_err:
                            wf_logger.warning(f" [{workflow_name_upper}] after_agent lifecycle tools failed for {turn_agent}: {lc_err}")

                turn_agent = domain_ev.agent or domain_ev.metadata.get("selected_agent")
                if turn_agent:
                    executed_agents.add(str(turn_agent))
                    
                    # LIFECYCLE TRIGGER: before_agent (for new agent)
                    # Execute before_agent lifecycle tools when an agent's turn begins
                    if lifecycle_manager:
                        try:
                            gm_ctx = getattr(pattern, "group_manager", None)
                            active_ctx = getattr(gm_ctx, "context_variables", None) if gm_ctx else None
                            if not active_ctx and hasattr(pattern, "context_variables"):
                                active_ctx = getattr(pattern, "context_variables")
                            
                            await lifecycle_manager.trigger_before_agent(
                                agent_name=str(turn_agent),
                                context_variables=active_ctx,
                            )
                        except Exception as lc_err:
                            wf_logger.warning(f" [{workflow_name_upper}] before_agent lifecycle tools failed for {turn_agent}: {lc_err}")
                    
                turn_started = time.perf_counter()
                wf_logger.debug(
                    f"[{workflow_name_upper}] New turn started with agent={turn_agent} seq={sequence_counter} chat_id={chat_id}"
                )

                candidates = domain_ev.metadata.get("candidates")
                selected_name = domain_ev.metadata.get("selected_agent")
                
                # Generic handoff debugging (workflow-agnostic)
                wf_logger.debug(
                    f"[HANDOFF_TRACE] SelectSpeakerEvent candidates={candidates} selected={selected_name}"
                )

            if domain_ev.kind == EventKind.INPUT_REQUEST:
                latest_request_id = domain_ev.metadata.get("request_id", str(uuid.uuid4()))
                respond_cb = domain_ev.metadata.get("respond")
                prompt_hint = domain_ev.metadata.get("prompt")
                latest_prompt_hint = prompt_hint

                if callable(respond_cb):
                    pending_input_requests[latest_request_id] = respond_cb
                    try:
                        if transport:
                            registered_id = transport.register_input_request(chat_id, latest_request_id, respond_cb)  # type: ignore[attr-defined]
                            if registered_id and registered_id != latest_request_id:
                                pending_input_requests.pop(latest_request_id, None)
                                pending_input_requests[registered_id] = respond_cb
                                latest_request_id = registered_id
                    except Exception as e:
                        logger.debug(f"Failed to register input request {latest_request_id}: {e}")
                else:
                    logger.debug(f"No respond callback available for input request {latest_request_id}")

            # -- HANDOFF_TO_USER: AG2 swarm "RevertToUserTarget" ---------------
            # AG2's a_run_group_chat() creates its own IOStream that blocks
            # forever on input().  When the swarm hands off to user, the AG2
            # task is effectively frozen.  We detect this via
            # AfterWorksTransitionEvent → RevertToUserTarget and:
            #   1. Emit chat.input_request so the frontend shows an input box
            #   2. Break out of the event loop (the run is done from our POV)
            # The next user message will start a fresh orchestration run with
            # conversation resume.
            if domain_ev.kind == EventKind.HANDOFF_TO_USER:
                source_agent = domain_ev.metadata.get("source_agent", "Agent")
                wf_logger.info(
                    f" [{workflow_name_upper}] Handoff to user detected from {source_agent}. "
                    f"Emitting input_request and ending stream."
                )

                if transport:
                    input_request_payload = {
                        "kind": "input_request",
                        "agent": source_agent,
                        "prompt": "",
                        "chat_id": chat_id,
                        "metadata": {
                            "source": "handoff_to_user",
                            "transition_target": "RevertToUserTarget",
                        },
                    }
                    try:
                        await transport.send_event_to_ui(input_request_payload, chat_id)
                        wf_logger.info(f" [{workflow_name_upper}] chat.input_request sent to UI")
                    except Exception as ir_err:
                        wf_logger.warning(f" [{workflow_name_upper}] Failed to send input_request: {ir_err}")

                stream_state["run_completed"] = True
                stream_state["handoff_to_user"] = True
                break

            if transport and sequence_counter not in forwarded_seqs:
                try:
                    # Unified serialization context object
                    build_ctx = UnifiedEventBuildContext(
                        workflow_name=workflow_name,
                        turn_agent=turn_agent,
                        tool_call_initiators=tool_call_initiators,
                        tool_names_by_id=tool_names_by_id,
                        workflow_name_upper=workflow_name_upper,
                        wf_logger=wf_logger,
                        pending_request_id=latest_request_id,
                        pending_prompt_hint=latest_prompt_hint,
                    )
                    # Pass domain_ev.raw so the adapter layer (event_builders) handles
                    # AG2-specific isinstance checks — this is the intentional boundary crossing.
                    payload = unified_build_ui_event_payload(ev=domain_ev.raw, ctx=build_ctx)
                    if payload:
                        if payload.get("kind") in {"text", "print"}:
                            if payload.get("kind") == "text":
                                wf_logger.debug(
                                    " [%s] Text payload from %s: %s", workflow_name_upper, payload.get("agent"), payload.get("content")
                                )
                            agent_for_event = payload.get("agent") or payload.get("sender") or turn_agent
                            wf_logger.debug(f" [{workflow_name_upper}] Checking auto-tool: agent={agent_for_event}, auto_tool_agents={auto_tool_agents}")
                            if agent_for_event in auto_tool_agents:
                                wf_logger.debug(f" [{workflow_name_upper}] Auto-tool intercept for agent {agent_for_event}; payload keys={list(payload.keys())}")
                                structured_blob = payload.get("structured_output")
                                wf_logger.debug(f" [{workflow_name_upper}] Initial structured_blob present: {bool(structured_blob)}")
                                if not structured_blob and isinstance(payload.get("content"), str):
                                    wf_logger.debug(
                                        f" [{workflow_name_upper}] No structured_output field for {agent_for_event}; attempting fallback parse."
                                    )
                                    try:
                                        structured_blob = _PM._extract_json_from_text(payload["content"]) if hasattr(_PM, '_extract_json_from_text') else None
                                        wf_logger.debug(f" [{workflow_name_upper}] Fallback parse result present={bool(structured_blob)}")
                                    except Exception as parse_err:
                                        wf_logger.debug(f" [{workflow_name_upper}] Structured output parse fallback failed for {agent_for_event}: {parse_err}")
                                        structured_blob = None
                                    # Additional parse attempts if persistence helper fails
                                    if not structured_blob:
                                        candidate_text = payload.get("content")
                                        if isinstance(candidate_text, str):
                                            stripped_candidate = candidate_text.strip()
                                            try:
                                                structured_blob = json.loads(stripped_candidate)
                                                wf_logger.debug(
                                                    f" [{workflow_name_upper}] Direct json.loads succeeded for {agent_for_event}"
                                                )
                                            except Exception as direct_err:
                                                # Try slicing the first JSON object substring
                                                start_idx = stripped_candidate.find('{')
                                                end_idx = stripped_candidate.rfind('}')
                                                if start_idx != -1 and end_idx > start_idx:
                                                    try:
                                                        structured_blob = json.loads(stripped_candidate[start_idx:end_idx + 1])
                                                        wf_logger.debug(
                                                            f" [{workflow_name_upper}] Substring json.loads succeeded for {agent_for_event}"
                                                        )
                                                    except Exception as substring_err:
                                                        wf_logger.debug(
                                                            f" [{workflow_name_upper}] Substring parse failed for {agent_for_event}: {substring_err}"
                                                        )
                                                else:
                                                    wf_logger.debug(
                                                        f" [{workflow_name_upper}] No JSON braces found in payload content for {agent_for_event}. Direct parse error: {direct_err}"
                                                    )
                                if structured_blob:

                                    wf_logger.debug(f" [{workflow_name_upper}] structured_blob truthy for {agent_for_event}: {bool(structured_blob)}")
                                    normalized_structured = structured_blob
                                    if isinstance(structured_blob, str):
                                        try:
                                            normalized_structured = json.loads(structured_blob)
                                        except json.JSONDecodeError:
                                            wf_logger.debug(
                                                f" [{workflow_name_upper}] Structured output JSON decode failed for {agent_for_event}"
                                            )
                                            normalized_structured = None
                                    if isinstance(normalized_structured, dict):
                                        model_cls = structured_registry.get(agent_for_event)
                                        if model_cls is not None:
                                            try:
                                                validated = model_cls.model_validate(normalized_structured)
                                                normalized_structured = validated.model_dump()  # type: ignore[attr-defined]
                                            except ValidationError as err:
                                                wf_logger.warning(
                                                    f" [{workflow_name_upper}] Structured output validation failed for {agent_for_event}: {err}"
                                                )
                                                normalized_structured = None
                                    else:
                                        normalized_structured = None
                                    if normalized_structured:
                                        wf_logger.info(f" [{workflow_name_upper}] Structured output ready for {agent_for_event}; emitting auto-tool event.")
                                        agent_message = normalized_structured.get("agent_message")
                                        if isinstance(agent_message, str) and agent_message.strip():
                                            display_message = agent_message.strip()
                                        else:
                                            display_message = f"{agent_for_event} prepared structured output."
                                        payload["content"] = display_message
                                        payload["is_structured_capable"] = True
                                        payload.pop("structured_output", None)
                                        payload.pop("structured_schema", None)
                                        turn_uuid = uuid.uuid5(uuid.NAMESPACE_URL, f"{chat_id}:{sequence_counter}")
                                        turn_key = f"turn-{turn_uuid.hex}"
                                        context_payload = _build_auto_tool_context_payload(sequence_counter)
                                        # Add agent_name to context so auto-tool handler can inject it into context_variables
                                        context_payload["agent_name"] = agent_for_event
                                        model_name = getattr(agents.get(agent_for_event), "_mozaiks_structured_model_name", None)
                                        if not model_name:
                                            model_cls = structured_registry.get(agent_for_event)
                                            if model_cls is not None:
                                                model_name = getattr(model_cls, "__name__", None)
                                        if not model_name:
                                            wf_logger.warning(
                                                f" [{workflow_name_upper}] Unable to determine structured model name for {agent_for_event}; skipping auto-tool dispatch"
                                            )
                                        else:
                                            structured_event = build_structured_output_ready_event(
                                                agent=agent_for_event,
                                                model_name=model_name,
                                                structured_data=normalized_structured,
                                                auto_tool_mode=True,
                                                context=context_payload,
                                            )
                                            structured_event["turn_idempotency_key"] = turn_key
                                            if dispatcher:
                                                wf_logger.info(
                                                    f" [{workflow_name_upper}] Dispatching chat.structured_output_ready for {agent_for_event} (turn_key={turn_key})"
                                                )
                                                asyncio.create_task(dispatcher.emit("chat.structured_output_ready", structured_event))
                                    else:
                                        wf_logger.debug(
                                            f" [{workflow_name_upper}] Normalized structured payload empty for {agent_for_event}; nothing to emit."
                                        )
                                else:
                                    wf_logger.debug(
                                        f" [{workflow_name_upper}] No structured content detected for {agent_for_event}; leaving message unchanged."
                                    )
                        if payload.get("kind") == "tool_response" and payload.get("status") == SENTINEL_STATUS:
                            agent_name = payload.get("agent")
                            tool_name = payload.get("name") or payload.get("tool_name") or "unknown_tool"
                            call_id = payload.get("call_id")
                            retry_key_parts = [str(part) for part in (call_id, agent_name, tool_name) if part]
                            retry_key = "|".join(retry_key_parts) if retry_key_parts else f"agent:{agent_name}|tool:{tool_name}"
                            attempts = schema_retry_tracker.get(retry_key, 0)
                            error_info = payload.get("error") or {}
                            expected_model = error_info.get("expected_model")
                            validation_errors = error_info.get("errors")

                            if attempts >= MAX_SCHEMA_RETRIES:
                                if attempts == MAX_SCHEMA_RETRIES:
                                    wf_logger.warning(
                                        f" [{workflow_name_upper}] Schema validation failed {attempts} time(s) for agent={agent_name} tool={tool_name} call_id={call_id}. No further auto-retries."
                                    )
                                    schema_retry_tracker[retry_key] = MAX_SCHEMA_RETRIES + 1
                                    if transport:
                                        error_payload = {
                                            "kind": "error",
                                            "agent": agent_name,
                                            "code": "SCHEMA_VALIDATION_FAILED",
                                            "message": (
                                                f"Schema validation failed repeatedly for tool '{tool_name}'. "
                                                "Manual follow-up is required."
                                            ),
                                        }
                                        try:
                                            await transport.send_event_to_ui(error_payload, chat_id)
                                        except Exception as err:
                                            logger.debug(
                                                f"Failed to send schema failure error event for {chat_id}: {err}"
                                            )
                            else:
                                schema_retry_tracker[retry_key] = attempts + 1
                                gm = getattr(pattern, "group_manager", None)
                                target_agent = _resolve_agent_object(agent_name)
                                if gm and target_agent:
                                    message_lines = []
                                    if attempts > 0:
                                        message_lines.append(
                                            f"Retry attempt {attempts + 1} of {MAX_SCHEMA_RETRIES}."
                                        )
                                    if expected_model:
                                        message_lines.append(
                                            f"The previous call to `{tool_name}` failed schema validation for model `{expected_model}`."
                                        )
                                    else:
                                        message_lines.append(
                                            f"The previous call to `{tool_name}` failed schema validation."
                                        )
                                    message_lines.append(
                                        "Review the validation errors and call the tool again with arguments that satisfy the schema."
                                    )
                                    if validation_errors:
                                        try:
                                            message_lines.append(
                                                "Validation errors: "
                                                + json.dumps(validation_errors, ensure_ascii=False)
                                            )
                                        except Exception:
                                            message_lines.append(
                                                f"Validation errors: {validation_errors}"
                                            )
                                    feedback_text = "\n".join(message_lines)
                                    try:
                                        await gm.a_send(
                                            message=feedback_text,
                                            recipient=target_agent,
                                            request_reply=True,
                                            silent=True,
                                        )
                                        wf_logger.info(
                                            f" [{workflow_name_upper}] Requested schema retry for agent={agent_name} tool={tool_name} attempt={attempts + 1}"
                                        )
                                    except Exception as retry_err:
                                        wf_logger.warning(
                                            f" [{workflow_name_upper}] Failed to enqueue schema retry for agent={agent_name} tool={tool_name}: {retry_err}"
                                        )
                                else:
                                    wf_logger.debug(
                                        f" [{workflow_name_upper}] Schema retry skipped; agent or group manager missing for agent={agent_name}"
                                    )
                        if payload.get("kind") == "text" and "source" not in payload:
                            payload["source"] = "ag2_textevent"
                        if payload.get("kind") == "run_complete" and not payload.get("agent"):
                            payload["agent"] = turn_agent or "workflow"
                        await transport.send_event_to_ui(payload, chat_id)
                except Exception as e:
                    logger.debug(f"Failed to send event to UI for {chat_id}: {e}")

            if domain_ev.kind == EventKind.TOOL_CALL:
                try:
                    ui_response = await handle_tool_call_for_ui_interaction(domain_ev.raw, chat_id)
                    if ui_response and transport:
                        try:
                            await transport.send_event_to_ui(
                                {
                                    "kind": "tool_ui_response",
                                    "tool_name": getattr(ev, "tool_name", None),
                                    "response": ui_response,
                                    "chat_id": chat_id,
                                },
                                chat_id,
                            )
                        except Exception as e:
                            logger.debug(f"Failed to send tool UI response for {chat_id}: {e}")
                except Exception as tool_err:
                    wf_logger.debug(f"Tool UI interaction error: {tool_err}")

            if domain_ev.kind == EventKind.USAGE_SUMMARY:
                try:
                    usage = domain_ev.content or {}
                    agg_prompt = usage.get("prompt_tokens", 0)
                    agg_completion = usage.get("completion_tokens", 0)
                    agg_cost = usage.get("cost", 0.0)
                    wf_logger.info(
                        f"[USAGE_SUMMARY] prompt={agg_prompt} completion={agg_completion} cost=${agg_cost:.4f}"
                    )
                except Exception as summary_err:
                    logger.debug(f"UsageSummaryEvent logging failed: {summary_err}")

            # StreamEvent: real-time token chunks from AG2 (A2A or streaming LLM).
            # The IOStream bridge already forwards these to SimpleTransport via
            # the fast-path, but if StreamEvent appears in response.events (e.g.
            # from an A2A remote agent) we handle it here as a fallback so no
            # chunks are lost.
            if domain_ev.kind == EventKind.STREAM_CHUNK:
                try:
                    chunk_content = domain_ev.content or ""
                    if chunk_content and transport:
                        sender = turn_agent or "Agent"
                        await transport.send_event_to_ui(
                            {
                                "kind": "stream_chunk",
                                "agent": sender,
                                "content": chunk_content,
                                "chunk_seq": sequence_counter,
                                "stream_id": f"{chat_id}:{sender}:{sequence_counter}",
                            },
                            chat_id,
                        )
                except Exception as stream_err:
                    wf_logger.debug(f"StreamEvent forwarding failed: {stream_err}")
                continue  # StreamEvent is handled; skip remaining checks

            if domain_ev.kind == EventKind.RUN_COMPLETE:
                try:
                    from .agents.handoffs import handoff_manager  # noqa: F401 (for side-effects / attr access)
                    remaining_after_work = []
                    for a_name, a_obj in agents.items():
                        tgt = None
                        try:
                            h = getattr(a_obj, "handoffs", None)
                            if h and hasattr(h, "after_work"):
                                tgt = getattr(h, "after_work", None)
                        except Exception:
                            pass
                        if tgt and a_name not in executed_agents:
                            remaining_after_work.append(
                                f"{a_name}->{getattr(getattr(tgt,'target',None),'name',getattr(tgt,'target',None))}"
                            )
                    if remaining_after_work:
                        wf_logger.warning(
                            f" [{workflow_name_upper}] RunCompletionEvent early. Executed: {sorted(executed_agents)} | Pending after_work chain: {remaining_after_work}"
                        )
                except Exception as diag_err:
                    wf_logger.debug(
                        f"Early termination diagnostics failed: {diag_err}"
                    )
                
                # Log completion with execution summary
                wf_logger.info(
                    f" [{workflow_name_upper}] Run complete chat_id={chat_id} events={sequence_counter} executed_agents={sorted(executed_agents)}"
                )
                
                # Store completion metadata in stream state for final processing
                stream_state['run_completed'] = True
                stream_state['completion_event'] = ev
                
                break

            # After processing event, compute diff if verbose enabled
            # Also trigger on_context_change lifecycle tools for ANY changed variables
            try:
                gm_live = getattr(pattern, 'group_manager', None)
                active_ctx = None
                if gm_live and hasattr(gm_live, 'context_variables'):
                    active_ctx = getattr(gm_live, 'context_variables')
                elif hasattr(pattern, 'context_variables'):
                    active_ctx = getattr(pattern, 'context_variables')
                current_snapshot = _safe_context_snapshot(active_ctx) if active_ctx else {}
                # Diff
                added = [k for k in current_snapshot.keys() if k not in prev_ctx_snapshot]
                removed = [k for k in prev_ctx_snapshot.keys() if k not in current_snapshot]
                changed = []
                for k in current_snapshot.keys():
                    if k in prev_ctx_snapshot and current_snapshot[k] != prev_ctx_snapshot[k]:
                        changed.append(k)
                
                # LIFECYCLE TRIGGER: on_context_change
                # Trigger lifecycle tools for each changed variable
                if changed and active_ctx:
                    for context_key in changed:
                        try:
                            old_value = prev_ctx_snapshot.get(context_key)
                            new_value = current_snapshot.get(context_key)
                            
                            await lifecycle_manager.execute_trigger(
                                trigger=LifecycleTrigger.ON_CONTEXT_CHANGE,
                                workflow_name=workflow_name,
                                chat_id=chat_id,
                                app_id=app_id,
                                context_key=context_key,
                                old_value=old_value,
                                new_value=new_value,
                                context_variables=active_ctx,
                            )
                        except Exception as lc_err:
                            wf_logger.debug(
                                f" [{workflow_name_upper}] on_context_change lifecycle tool failed for {context_key}: {lc_err}"
                            )
                
                if verbose_ctx and (added or removed or changed):
                    wf_logger.info(
                        f" [CONTEXT_DIFF] seq={sequence_counter} added={added} removed={removed} changed={changed}"
                    )
                    # Only dump detailed values at DEBUG to avoid log noise
                    wf_logger.debug(
                        f" [CONTEXT_DIFF_DEBUG] seq={sequence_counter} snapshot={current_snapshot}"
                    )
                prev_ctx_snapshot = current_snapshot
            except Exception as _diff_err:
                wf_logger.debug(f" [CONTEXT_VERBOSE] diff computation failed: {_diff_err}")
    except Exception as loop_err:
        wf_logger.error(f"Event loop failure: {loop_err}", exc_info=True)
    finally:
        # LIFECYCLE TRIGGER: after_chat
        # Execute after_chat lifecycle tools after event loop completes (success or error)
        try:
            gm_ctx = getattr(pattern, "group_manager", None)
            active_ctx = getattr(gm_ctx, "context_variables", None) if gm_ctx else None
            if not active_ctx and hasattr(pattern, "context_variables"):
                active_ctx = getattr(pattern, "context_variables")
            
            final_status = "error" if "loop_err" in locals() else "success"
            
            await lifecycle_manager.execute_trigger(
                trigger=LifecycleTrigger.AFTER_CHAT,
                workflow_name=workflow_name,
                chat_id=chat_id,
                app_id=app_id,
                user_id=user_id,
                context_variables=active_ctx,
                final_status=final_status,
            )
        except Exception as lc_err:
            wf_logger.warning(f" [{workflow_name_upper}] after_chat lifecycle tools failed: {lc_err}")
        
        # AG2-native: No manual context cleanup needed - AG2 handles lifecycle automatically

        # Cancel the zombie AG2 task when we break out early (e.g. handoff_to_user).
        # AG2's a_run_group_chat() creates an internal task that may block forever
        # on IOStream.input().  We cancel it to prevent resource leaks.
        if stream_state.get("handoff_to_user"):
            from mozaiksai.adapters.ag2.response_helpers import cancel_ag2_task
            cancel_ag2_task(response, logger=wf_logger, label=workflow_name_upper)

    return {
        "response": response,
        "turn_agent": turn_agent,
        "turn_started": turn_started,
        "sequence_counter": sequence_counter,
        "run_completed": stream_state.get("run_completed", False),
        "completion_event": stream_state.get("completion_event"),
        "handoff_to_user": stream_state.get("handoff_to_user", False),
    }


async def run_workflow_orchestration(
    workflow_name: str,
    app_id: str,
    chat_id: str,
    user_id: Optional[str] = None,
    initial_message: Optional[str] = None,
    initial_agent_name_override: Optional[str] = None,
    agents_factory: Optional[Callable] = None,
    context_factory: Optional[Callable] = None,
    handoffs_factory: Optional[Callable] = None,
    **kwargs
) -> Any:
    start_time = perf_counter()
    workflow_name_upper = workflow_name.upper()

    # Create workflow logger for this session
    wf_lifecycle_logger = get_workflow_logger(workflow_name, chat_id=chat_id)
    wf_logger = get_workflow_logger(workflow_name, chat_id=chat_id, app_id=app_id)

    logger.info(f" [ORCHESTRATION] Starting {workflow_name} workflow")

    result_payload: Optional[Dict[str, Any]] = None
    stream_state: Dict[str, Any] = {}
    # These are populated after executor.prepare_and_launch()
    agents: Dict[str, Any] = {}
    orchestration_pattern = "unknown"

    # Start AG2 runtime logging for this workflow session
    with ag2_logging_session(chat_id, workflow_name, app_id):
        # Set up realtime token logger for immediate token tracking
        try:
            from mozaiksai.engine.observability.token_logger import get_realtime_token_logger
            realtime_logger = get_realtime_token_logger()
            realtime_logger.set_user(user_id or "unknown")
            realtime_logger.set_active_agent(workflow_name)
            wf_logger.info(f" [REALTIME_TOKENS] Realtime token logging prepared for chat {chat_id}")
        except Exception as rt_err:
            wf_logger.warning(f" [REALTIME_TOKENS] Failed to prepare realtime token logging: {rt_err}")

        try:
            # =============================================================
            # Prepare and launch AG2 via the executor
            # =============================================================
            executor = GroupChatExecutor(
                workflow_name=workflow_name,
                app_id=app_id,
                chat_id=chat_id,
                user_id=user_id,
                initial_message=initial_message,
                initial_agent_name_override=initial_agent_name_override,
                agents_factory=agents_factory,
                context_factory=context_factory,
                handoffs_factory=handoffs_factory,
                **kwargs,
            )
            run = await executor.prepare_and_launch()

            # Unpack commonly-used references for post-stream processing
            agents = run.agents
            orchestration_pattern = run.orchestration_pattern

            # =============================================================
            # Stream AG2 events
            # =============================================================
            stream_state = await _stream_events(run)
            response = stream_state["response"]
            turn_agent = stream_state["turn_agent"]
            turn_started = stream_state["turn_started"]

            if turn_agent and turn_started is not None:
                duration = max(0.0, time.perf_counter() - turn_started)
                try:
                    await run.perf_mgr.record_agent_turn(
                        chat_id=chat_id,
                        agent_name=turn_agent,
                        duration_sec=duration,
                        model=None,
                    )
                except Exception as e:
                    logger.warning(f"Failed to record final turn for {turn_agent}: {e}")

            # ---------------------------------------------------------
            # HANDOFF_TO_USER early-return: the conversation is paused,
            # not complete.  Skip termination / cleanup so that the
            # session stays alive for the next user message.
            # ---------------------------------------------------------
            if stream_state.get("handoff_to_user"):
                duration_sec = perf_counter() - start_time
                wf_logger.info(
                    f" [{workflow_name_upper}] Handoff to user — orchestration "
                    f"paused after {duration_sec:.2f}s. Session stays ACTIVE."
                )
                result_payload = {
                    "workflow_name": workflow_name,
                    "chat_id": chat_id,
                    "app_id": app_id,
                    "user_id": user_id,
                    "messages": None,
                    "max_turns_reached": False,
                    "response": response,
                    "handoff_to_user": True,
                }
                # jump to finally — which will see handoff_to_user and
                # keep the workflow IN_PROGRESS instead of COMPLETED.
                return result_payload

            # Final usage reconciliation — delegated to adapter layer
            try:
                from mozaiksai.adapters.ag2.usage import collect_usage_summary

                agent_list = list(agents.values())
                if agent_list:
                    final_summary = collect_usage_summary(agent_list)

                    def _safe_float_local(value: Any) -> float:
                        try:
                            if isinstance(value, dict):
                                if "total_cost" in value:
                                    return float(value.get("total_cost", 0.0))
                                values = list(value.values())
                                if values:
                                    return float(values[0])
                                return 0.0
                            return float(value)
                        except (TypeError, ValueError):
                            return 0.0

                    ag2_total_cost = _safe_float_local(final_summary.get("total_cost", 0.0))
                    ag2_usage_including_cached = final_summary.get("usage_including_cached", {})
                    ag2_usage_excluding_cached = final_summary.get("usage_excluding_cached", {})

                    wf_logger.info(
                        "[AG2_FINAL_SUMMARY] Authoritative usage data | "
                        f"total_cost=${ag2_total_cost:.4f} | "
                        f"with_cache={ag2_usage_including_cached} | "
                        f"without_cache={ag2_usage_excluding_cached}"
                    )

                    persisted_prompt = 0
                    persisted_completion = 0
                    persisted_cost = 0.0
                    try:
                        coll = await run.persistence_manager._coll()
                        persisted = await coll.find_one(
                            {"_id": chat_id, "app_id": app_id},
                            {
                                "usage_prompt_tokens_final": 1,
                                "usage_completion_tokens_final": 1,
                                "usage_total_cost_final": 1,
                            },
                        )
                        if isinstance(persisted, dict):
                            persisted_prompt = int(persisted.get("usage_prompt_tokens_final") or 0)
                            persisted_completion = int(persisted.get("usage_completion_tokens_final") or 0)
                            persisted_cost = float(persisted.get("usage_total_cost_final") or 0.0)
                    except Exception as read_err:
                        wf_logger.debug(f"[FINAL_RECONCILIATION] Failed to read persisted usage: {read_err}")

                    ag2_prompt_total = int(ag2_usage_excluding_cached.get("prompt_tokens", 0) or 0)
                    ag2_completion_total = int(ag2_usage_excluding_cached.get("completion_tokens", 0) or 0)

                    final_cost_delta = max(0.0, ag2_total_cost - persisted_cost)
                    final_prompt_delta = max(0, ag2_prompt_total - persisted_prompt)
                    final_completion_delta = max(0, ag2_completion_total - persisted_completion)

                    if final_cost_delta > 0.01 or final_prompt_delta or final_completion_delta:
                        wf_logger.warning(
                            "[FINAL_RECONCILIATION] Delta detected | "
                            f"ag2_total=${ag2_total_cost:.4f} persisted_total=${persisted_cost:.4f} | "
                            f"delta=${final_cost_delta:.4f} prompt_delta={final_prompt_delta} completion_delta={final_completion_delta}"
                        )
                        await run.persistence_manager.update_session_metrics(
                            chat_id=chat_id,
                            app_id=app_id,
                            user_id=user_id or "unknown",
                            workflow_name=workflow_name,
                            prompt_tokens=final_prompt_delta,
                            completion_tokens=final_completion_delta,
                            cost_usd=final_cost_delta,
                            agent_name="ag2_final_reconciliation",
                            event_ts=datetime.now(UTC)
                        )
                    else:
                        wf_logger.info(
                            "o. [FINAL_RECONCILIATION] Usage tracking accurate | "
                            f"ag2=${ag2_total_cost:.4f} persisted=${persisted_cost:.4f} | "
                            f"delta=${final_cost_delta:.4f}"
                        )

                    for agent_name, agent in agents.items():
                        try:
                            if hasattr(agent, 'print_usage_summary'):
                                wf_logger.debug(f" [AGENT_USAGE] {agent_name} summary logged to stdout")
                        except Exception as agent_summary_err:
                            wf_logger.debug(f"Failed to log usage summary for {agent_name}: {agent_summary_err}")

            except ImportError:
                wf_logger.warning(" [FINAL_RECONCILIATION] AG2 usage adapter not available")
            except Exception as reconcile_err:
                wf_logger.error(f" [FINAL_RECONCILIATION] Failed: {reconcile_err}")

            max_turns_reached = getattr(response, 'max_turns_reached', False)

            # Ensure termination handler is called to update status
            try:
                termination_result = await run.termination_handler.on_conversation_end(
                    max_turns_reached=max_turns_reached
                )
                try:
                    status_val = getattr(termination_result, 'status', 'completed')
                    logger.info(f" Termination completed: {status_val}")
                except Exception:
                    logger.info(" Termination completed (offline mode)")
            except Exception as term_err:
                logger.error(f" Termination handler failed: {term_err}")

            messages_obj = None
            try:
                messages_obj = getattr(response, 'messages', None)
                if asyncio.iscoroutine(messages_obj):
                    messages_obj = await messages_obj
                if messages_obj is not None:
                    await log_conversation_to_agent_chat_file(messages_obj, chat_id, app_id, workflow_name)
            except Exception as log_err:
                logger.error(f" Failed to log conversation to agent chat file for {chat_id}: {log_err}")

            duration_sec = perf_counter() - start_time
            wf_logger.info(f" [EXECUTION_COMPLETE] Duration: {duration_sec:.2f}s")

            # Lifecycle after_chat trigger
            try:
                from mozaiksai.engine.execution.lifecycle import get_lifecycle_manager
                lifecycle_manager = get_lifecycle_manager(workflow_name)
                await lifecycle_manager.trigger_after_chat(context_variables=run.ag2_context)
                wf_logger.info(f" [{workflow_name_upper}] Lifecycle after_chat triggers completed")
            except Exception as lc_err:
                wf_logger.debug(f" [{workflow_name_upper}] Lifecycle after_chat failed: {lc_err}")

            result_payload = {
                "workflow_name": workflow_name,
                "chat_id": chat_id,
                "app_id": app_id,
                "user_id": user_id,
                "messages": messages_obj,
                "max_turns_reached": max_turns_reached,
                "response": response
            }

        except Exception as e:
            logger.error(f" [{workflow_name_upper}] Orchestration failed: {e}", exc_info=True)
            try:
                if "run" in locals() and hasattr(run, "termination_handler"):
                    await run.termination_handler.on_conversation_end()
                logger.info(" Termination handler called for error case")
            except Exception as term_err:
                logger.error(f" Termination handler error cleanup failed: {term_err}")
            raise
        finally:
            from mozaiksai.runtime.data.models import WorkflowStatus
            is_handoff = stream_state.get("handoff_to_user", False)
            status = WorkflowStatus.IN_PROGRESS if is_handoff else WorkflowStatus.COMPLETED
            try:
                if "run" in locals() and hasattr(run, "perf_mgr"):
                    if not is_handoff:
                        await run.perf_mgr.record_workflow_end(chat_id, int(status))
                    await run.perf_mgr.flush(chat_id)
            except Exception as e:
                logger.debug(f"perf finalize failed: {e}")
            duration_sec = perf_counter() - start_time

    # Final logging & cleanup
    try:
        duration = perf_counter() - start_time

        wf_lifecycle_logger.info(
            f" [{workflow_name_upper}] Workflow completed",
            duration_sec=duration,
            event_count=(stream_state.get('sequence_counter', 0) if isinstance(stream_state, dict) else 0),
            agent_count=len(agents),
            pattern_used=orchestration_pattern,
            chat_id=chat_id,
            app_id=app_id,
            result_status="success" if result_payload else "empty"
        )

        chat_logger.info(f"[{workflow_name_upper}] WORKFLOW_COMPLETED chat_id={chat_id} duration={duration:.2f}s agents={len(agents)}")

        try:
            from pathlib import Path
            agent_outputs_file = Path("logs/agent_outputs") / f"agent_outputs_{chat_id}.jsonl"
            if agent_outputs_file.exists():
                file_size = agent_outputs_file.stat().st_size
                with open(agent_outputs_file, 'r', encoding='utf-8') as f:
                    line_count = sum(1 for _ in f)

                abs_path = agent_outputs_file.resolve()
                print("\n" + "=" * 80)
                print(f"📋 AGENT OUTPUTS LOG:")
                print(f"   File: {abs_path}")
                print(f"   Agent outputs captured: {line_count}")
                print(f"   Size: {file_size:,} bytes")
                print("=" * 80 + "\n")
                chat_logger.info(f"[{workflow_name_upper}] Agent outputs saved: {abs_path} ({line_count} outputs, {file_size:,} bytes)")
        except Exception:
            pass

    finally:
        try:
            if "run" in locals() and run.transport and hasattr(run.transport, "unregister_derived_context_manager"):
                run.transport.unregister_derived_context_manager(chat_id)
        except Exception:
            pass

    return result_payload


# ==============================================================================
# NOTE: create_ag2_pattern function has been extracted to pattern_factory.py
# This refactoring improves modularity and maintainability.
# Import: from .pattern_factory import create_ag2_pattern
async def log_conversation_to_agent_chat_file(conversation_history, chat_id: str, app_id: str, workflow_name: str):
    """
    Log the complete AG2 conversation to the agent chat log file.
    """
    try:
        agent_chat_logger = get_workflow_logger(
            "agent_messages",
            base_logger=logging.getLogger("mozaiks.workflow.agent_messages"),
        )

        if not conversation_history:
            agent_chat_logger.info(f" [{workflow_name}] No conversation history to log for chat {chat_id}")
            return

        msg_count = len(conversation_history) if hasattr(conversation_history, '__len__') else 0
        agent_chat_logger.info(f" [{workflow_name}] Logging {msg_count} messages to agent chat file for chat {chat_id}")

        for i, message in enumerate(conversation_history):
            try:
                sender_name = "Unknown"
                content = ""

                if isinstance(message, dict):
                    if 'name' in message and message['name']:
                        sender_name = message['name']
                    elif 'sender' in message and message['sender']:
                        sender_name = message['sender']
                    elif 'from' in message and message['from']:
                        sender_name = message['from']

                    if 'content' in message and message['content'] is not None:
                        content = message['content']
                    elif 'message' in message and message['message'] is not None:
                        content = message['message']
                    elif 'text' in message and message['text'] is not None:
                        content = message['text']
                elif isinstance(message, str):
                    content = message
                elif hasattr(message, 'name') and hasattr(message, 'content'):
                    sender_name = getattr(message, 'name', 'Unknown')
                    content = getattr(message, 'content', '')
                elif hasattr(message, 'sender') and hasattr(message, 'message'):
                    sender_name = getattr(message, 'sender', 'Unknown')
                    content = getattr(message, 'message', '')
                else:
                    content = str(message)

                clean_content = content if isinstance(content, str) else str(content)
                clean_content = clean_content.strip() if clean_content else ""

                if clean_content:
                    agent_chat_logger.info(
                        f"AGENT_MESSAGE | Chat: {chat_id} | App: {app_id} | Agent: {sender_name} | Message #{i+1}: {clean_content}"
                    )
                    # Skip user proxy messages to prevent echo back to UI
                    message_role = message.get('role') if isinstance(message, dict) else None
                    if not (sender_name.lower() in ("user", "userproxy", "userproxyagent") or message_role == 'user'):
                        try:
                            from mozaiksai.transport.websocket.handler import SimpleTransport
                            transport = await SimpleTransport.get_instance()
                            if transport:
                                await transport.send_chat_message(
                                    message=clean_content,
                                    agent_name=sender_name,
                                    chat_id=chat_id,
                                    metadata={"source": "ag2_conversation", "message_index": i+1}
                                )
                        except Exception as ui_error:
                            logger.debug(f"UI forwarding failed for message {i+1}: {ui_error}")
                else:
                    agent_chat_logger.debug(f"EMPTY_MESSAGE | Chat: {chat_id} | Agent: {sender_name} | Message #{i+1}: (empty)")

            except Exception as msg_error:
                agent_chat_logger.error(f" Failed to log message {i+1} in chat {chat_id}: {msg_error}")

        agent_chat_logger.info(f" [{workflow_name}] Successfully logged {msg_count} messages for chat {chat_id}")

    except Exception as e:
        logger.error(f" Failed to log conversation to agent chat file for {chat_id}: {e}")
        # Do not raise