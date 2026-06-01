# ==============================================================================
# FILE: mozaiksai/core/workflow/orchestration_patterns.py
# DESCRIPTION: autogen.beta Agent orchestration engine.
#
# This is the single entry point to run a workflow using the beta Agent harness
# with streaming, tools, routing, persistence, and observability.
#
# Multi-agent routing compiles handoffs.yaml into an AG2 beta Network
# TransitionGraph. Each agent turn is executed via agent.ask() with a
# MemoryStream while the orchestration adapter owns Mozaiks transport,
# persistence, hooks, and UI event integration.
# ==============================================================================

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import uuid
from collections import Counter
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, Callable, Dict, List, Optional, Tuple  # Tuple kept for _resume_or_initialize_chat

from autogen.beta import MemoryStream
from autogen.beta.events import (
    HumanInputRequest,
    ModelMessageChunk,
    ModelResponse,
    ToolCallsEvent,
    ToolResultsEvent,
)
from pydantic import BaseModel

from logs.logging_config import get_workflow_logger
from logs.runtime_artifacts import get_agent_outputs_dir
from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager
from mozaiksai.core.observability.ag2_runtime_logger import ag2_logging_session
from mozaiksai.core.observability.performance_manager import get_performance_manager

from .context import DerivedContextManager
from .execution import create_termination_handler
from .execution.network_graph import (
    compile_handoffs_to_transition_graph,
    resolve_next_agent,
)
from .messages import (
    normalize_to_strict_ag2 as _normalize_to_strict_ag2,
)
from .orchestration_utils import (
    _load_workflow_config,
    _reconcile_final_usage,
    _safe_float_value,
    log_conversation_to_agent_chat_file,
)
from .validation import SENTINEL_STATUS

logger = logging.getLogger(__name__)

chat_logger = get_workflow_logger("orchestration")
workflow_logger = get_workflow_logger("orchestration")
performance_logger = get_workflow_logger("performance.orchestration")

__all__ = [
    "run_workflow_orchestration",
    "_merge_persisted_extra_context",
]


# ---------------------------------------------------------------------------
# Context merge helper
# ---------------------------------------------------------------------------

def _merge_persisted_extra_context(context: Any, extra_ctx: Dict[str, Any]) -> None:
    """Apply persisted run context over workflow-declared defaults.

    Workflow ``context_variables.yaml`` supplies defaults. Persisted chat-session
    extra fields are the explicit launch/resume context for this run and must
    therefore override those defaults. ``fetch_chat_session_extra_context`` strips
    canonical chat identity fields before this function is called.
    """
    if not isinstance(extra_ctx, dict) or not extra_ctx:
        return
    for k, v in extra_ctx.items():
        if not isinstance(k, str) or not k.strip():
            continue
        try:
            if hasattr(context, "set"):
                context.set(k, v)
            elif hasattr(context, "__setitem__"):
                context[k] = v
        except Exception:
            continue
    try:
        if extra_ctx.get("parent_chat_id"):
            if hasattr(context, "set"):
                context.set("automated_workflow_run", True)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Resume / init chat (same as before, stores messages in MongoDB)
# ---------------------------------------------------------------------------

async def _resume_or_initialize_chat(
    persistence_manager: AG2PersistenceManager,
    termination_handler: Any,
    config: Dict[str, Any],
    chat_id: str,
    app_id: str,
    workflow_name: str,
    user_id: Optional[str],
    initial_message: Optional[str],
    initial_agent_name: Optional[str],
    wf_logger: Any,
    suppress_config_seed: bool = False,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    def _build_hidden_config_seed() -> Optional[Dict[str, Any]]:
        if suppress_config_seed:
            return None
        seed = config.get("initial_message")
        if not isinstance(seed, str) or not seed.strip():
            return None
        return {"role": "user", "name": "user", "content": seed.strip(), "_mozaiks_seed_kind": "initial_message"}

    resumed_messages = await persistence_manager.resume_chat(chat_id, app_id) or []
    initial_messages: List[Dict[str, Any]] = []
    hidden_config_seed = _build_hidden_config_seed()

    # Strip general-mode chatter
    filtered: List[Dict[str, Any]] = []
    for msg in resumed_messages:
        meta = msg.get("metadata") if isinstance(msg, dict) else None
        if isinstance(meta, dict) and meta.get("source") == "general_agent":
            continue
        filtered.append(msg)
    resumed_messages = filtered

    meaningful_roles = {"user", "assistant", "agent", "tool"}
    meaningful_messages = [m for m in resumed_messages if isinstance(m, dict) and m.get("role") in meaningful_roles]
    resume_valid = bool(resumed_messages) and bool(meaningful_messages)

    if resume_valid:
        wf_logger.info("[RESUME] Resuming chat %s: messages=%d meaningful=%d", chat_id, len(resumed_messages), len(meaningful_messages))
        initial_messages = list(resumed_messages)
        if hidden_config_seed:
            initial_messages = [dict(hidden_config_seed)] + initial_messages
        if initial_message:
            initial_messages.append({"role": "user", "name": "user", "content": initial_message, "_mozaiks_seed_kind": "initial_message"})
    else:
        if resumed_messages:
            wf_logger.info("[RESUME] Discarding scaffolding-only resume for %s; treating as NEW", chat_id)
        resumed_messages = []

        if hidden_config_seed:
            initial_messages.append(dict(hidden_config_seed))
        if initial_message:
            initial_messages.append({"role": "user", "name": "user", "content": initial_message, "_mozaiks_seed_kind": "initial_message"})

        current_user_id = user_id or "system_user"
        try:
            await persistence_manager.create_chat_session(
                chat_id=chat_id, app_id=app_id, workflow_name=workflow_name, user_id=current_user_id,
            )
        except Exception as cs_err:
            wf_logger.error("Failed to create chat session for %s: %s", chat_id, cs_err)

        try:
            await termination_handler.on_conversation_start(user_id=current_user_id)
        except Exception:
            pass

    # UserDriven trigger
    if not initial_messages and config.get("workflow_startup_mode", "").strip().lower() == "userdriven":
        initial_messages = [{"role": "user", "name": "user", "content": ".", "_mozaiks_seed_kind": "userdriven_trigger"}]

    return resumed_messages, initial_messages


# ---------------------------------------------------------------------------
# Pre-turn hook prompt injection
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Beta event forwarding
# ---------------------------------------------------------------------------

async def _forward_beta_events(
    stream: MemoryStream,
    transport: Any,
    chat_id: str,
    agent_name: str,
    persistence_manager: AG2PersistenceManager,
    wf_logger: Any,
    sequence_state: Dict[str, Any],
) -> None:
    """Subscribe to MemoryStream events and forward them to the UI transport."""

    async def on_event(event: Any) -> None:
        try:
            seq = sequence_state.get("counter", 0)
            sequence_state["counter"] = seq + 1

            if isinstance(event, ModelMessageChunk):
                # Streaming text chunk
                await transport.send_event_to_ui({
                    "kind": "stream_chunk",
                    "agent": agent_name,
                    "content": event.content,
                    "sequence": seq,
                }, chat_id)

            elif isinstance(event, ModelResponse) and event.content:
                # Final complete response
                await transport.send_event_to_ui({
                    "kind": "chat.text",
                    "agent": agent_name,
                    "role": "assistant",
                    "content": event.content,
                    "sequence": seq,
                    "metadata": {
                        "source": "agent",
                        "model": event.model,
                    },
                }, chat_id)
                # Persist the agent message
                try:
                    await persistence_manager.save_message(chat_id, {
                        "role": "assistant",
                        "name": agent_name,
                        "content": event.content,
                        "metadata": {"source": "agent", "model": event.model},
                    })
                except Exception as persist_err:
                    wf_logger.debug("[STREAM] Message persist failed: %s", persist_err)

            elif isinstance(event, ToolCallsEvent):
                for call in event.calls:
                    await transport.send_event_to_ui({
                        "kind": "tool_call",
                        "agent": agent_name,
                        "tool": getattr(call, "name", "unknown"),
                        "call_id": getattr(call, "id", None),
                        "sequence": seq,
                    }, chat_id)

            elif isinstance(event, ToolResultsEvent):
                for result in event.results:
                    await transport.send_event_to_ui({
                        "kind": "tool_result",
                        "agent": agent_name,
                        "call_id": getattr(result, "id", None),
                        "sequence": seq,
                    }, chat_id)

            elif isinstance(event, HumanInputRequest):
                # Signal the orchestration loop that user input is needed
                sequence_state["awaiting_user_input"] = True
                await transport.send_event_to_ui({
                    "kind": "input_request",
                    "agent": agent_name,
                    "prompt": getattr(event, "prompt", ""),
                    "request_id": getattr(event, "id", str(uuid.uuid4())),
                    "sequence": seq,
                }, chat_id)

        except Exception as fwd_err:
            wf_logger.debug("[STREAM] Event forward failed (%s): %s", type(event).__name__, fwd_err)

    stream.subscribe(on_event, sync_to_thread=False)


# ---------------------------------------------------------------------------
# Multi-agent orchestration loop
# ---------------------------------------------------------------------------


def _reply_body_to_data(reply: Any) -> Any:
    body = getattr(reply, "body", reply)
    if isinstance(body, BaseModel):
        return body.model_dump(mode="json")
    if isinstance(body, (dict, list)):
        return body
    if isinstance(body, str):
        text = body.strip()
        if not text:
            return text
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return body
    return body


def _compact_history_content(agent_name: str, content: str, max_chars: int = 12000) -> str:
    if len(content) <= max_chars:
        return content

    def _summarize_value(value: Any) -> Any:
        if isinstance(value, dict):
            summary: Dict[str, Any] = {}
            for key, item in value.items():
                if key == "code_files" and isinstance(item, list):
                    summary[key] = [
                        {
                            "filename": str(entry.get("filename") or entry.get("path") or ""),
                            "chars": len(str(entry.get("content") or "")),
                        }
                        for entry in item
                        if isinstance(entry, dict)
                    ]
                elif key == "build_tasks" and isinstance(item, list):
                    summary[key] = [
                        {
                            "task_id": str(entry.get("task_id") or ""),
                            "task_type": str(entry.get("task_type") or ""),
                            "initial_agent": str(entry.get("initial_agent") or ""),
                        }
                        for entry in item
                        if isinstance(entry, dict)
                    ]
                elif key == "pages" and isinstance(item, list):
                    summary[key] = [
                        {
                            "name": str(entry.get("name") or ""),
                            "route": str(entry.get("route") or ""),
                            "sections": len(entry.get("sections") or []),
                        }
                        for entry in item
                        if isinstance(entry, dict)
                    ]
                elif isinstance(item, (dict, list)):
                    encoded = json.dumps(item, ensure_ascii=False, default=str)
                    summary[key] = {
                        "type": type(item).__name__,
                        "items": len(item),
                        "chars": len(encoded),
                    }
                else:
                    text = "" if item is None else str(item)
                    summary[key] = text if len(text) <= 500 else f"{text[:500]}... [truncated]"
            return summary
        return f"{str(value)[:max_chars]}... [truncated]"

    try:
        parsed = json.loads(content)
        summary = _summarize_value(parsed)
    except Exception:
        summary = f"{content[:max_chars]}... [truncated]"

    return json.dumps(
        {
            "_mozaiks_compacted_agent_output": True,
            "agent": agent_name,
            "original_chars": len(content),
            "summary": summary,
        },
        ensure_ascii=False,
    )


async def _emit_validated_agent_output(
    *,
    current_agent_name: str,
    last_reply: Any,
    workflow_name: str,
    chat_id: str,
    app_id: str,
    user_id: Optional[str],
    turn_sequence: int,
    context_vars_dict: Dict[str, Any],
    context_bridge: Any,
    structured_registry: Dict[str, Any],
    auto_tool_agents: set[str],
    wf_logger: Any,
) -> Optional[Dict[str, Any]]:
    model_cls = structured_registry.get(current_agent_name)
    if model_cls is None or last_reply is None:
        return None

    raw_data = _reply_body_to_data(last_reply)
    if not isinstance(raw_data, dict):
        wf_logger.warning(
            "[%s] Structured output from %s was not a JSON object",
            workflow_name.upper(),
            current_agent_name,
        )
        return None

    try:
        validated = model_cls.model_validate(raw_data)
        structured_data = validated.model_dump(mode="json")
    except Exception as err:
        wf_logger.warning(
            "[%s] Structured output validation failed for %s: %s",
            workflow_name.upper(),
            current_agent_name,
            err,
        )
        return None

    model_name = getattr(model_cls, "__name__", str(model_cls))

    try:
        from mozaiksai.core.events.ag2_events import emit_structured_output

        emit_structured_output(
            agent_name=current_agent_name,
            chat_id=chat_id,
            output_type=model_name,
            output_data=structured_data,
            validation_passed=True,
        )
    except Exception:
        wf_logger.debug(
            "[%s] StructuredOutputEvent emission skipped for %s",
            workflow_name.upper(),
            current_agent_name,
        )

    try:
        from mozaiksai.core.events.runtime_events import (
            RUNTIME_AGENT_OUTPUT_VALIDATED,
            build_runtime_agent_output_validated_event,
            build_runtime_context_payload,
            build_turn_idempotency_key,
        )
        from mozaiksai.core.events.unified_event_dispatcher import get_event_dispatcher

        context_payload = build_runtime_context_payload(
            chat_id=chat_id,
            app_id=app_id,
            workflow_name=workflow_name,
            turn_sequence=turn_sequence,
            user_id=user_id,
            context_variables=context_vars_dict,
        )
        event_payload = build_runtime_agent_output_validated_event(
            agent=current_agent_name,
            model_name=model_name,
            structured_data=structured_data,
            auto_tool_call=current_agent_name in auto_tool_agents,
            context=context_payload,
            source="ag2_beta_orchestration",
            turn_idempotency_key=build_turn_idempotency_key(chat_id, turn_sequence),
            pattern_context_ref=context_bridge,
            validation_passed=True,
        )
        await get_event_dispatcher().emit(RUNTIME_AGENT_OUTPUT_VALIDATED, event_payload)
    except Exception as err:
        wf_logger.warning(
            "[%s] runtime.agent_output_validated dispatch failed for %s: %s",
            workflow_name.upper(),
            current_agent_name,
            err,
        )

    return structured_data

async def _run_beta_orchestration_loop(
    *,
    agents: Dict[str, Any],
    initial_agent_name: str,
    initial_messages: List[Dict[str, Any]],
    resumed_messages: List[Dict[str, Any]],
    context_vars_dict: Dict[str, Any],
    context_bridge: Any,
    handoff_rules: List[Dict[str, Any]],
    max_turns: int,
    transport: Any,
    chat_id: str,
    app_id: str,
    workflow_name: str,
    workflow_name_upper: str,
    user_id: Optional[str],
    persistence_manager: AG2PersistenceManager,
    perf_mgr: Any,
    wf_logger: Any,
    lifecycle_manager: Optional[Any] = None,
    derived_context_manager: Optional[Any] = None,
    workflow_startup_mode: str = "AgentDriven",
    config: Optional[Dict[str, Any]] = None,
    task_batches_config: Optional[Any] = None,
    structured_registry: Optional[Dict[str, Any]] = None,
    auto_tool_agents: Optional[set[str]] = None,
) -> Dict[str, Any]:
    agent_id_by_name = {name: name for name in agents}
    agent_id_by_name.setdefault("user", "user")
    agent_name_by_id = {agent_id: name for name, agent_id in agent_id_by_name.items()}
    transition_graph = compile_handoffs_to_transition_graph(
        handoff_rules,
        initial_agent_name=initial_agent_name,
        agent_id_by_name=agent_id_by_name,
        max_turns=max_turns,
    )
    config = config or {}

    # History starts from the initial messages (possibly with resumed history)
    history = list(initial_messages)
    current_agent_name = initial_agent_name
    awaiting_user_input = False
    run_completed = False
    last_reply: Optional[Any] = None
    sequence_state: Dict[str, Any] = {"counter": 0, "awaiting_user_input": False}
    structured_registry = structured_registry or {}
    auto_tool_agents = auto_tool_agents or set()

    # UserDriven greeting via static first reply (before LLM call)
    if (
        not resumed_messages
        and workflow_startup_mode == "UserDriven"
        and config.get("initial_message_to_user")
    ):
        greeting = str(config["initial_message_to_user"]).strip()
        if greeting:
            await transport.send_event_to_ui({
                "kind": "chat.text",
                "agent": current_agent_name,
                "role": "assistant",
                "content": greeting,
                "sequence": sequence_state.get("counter", 0),
            }, chat_id)
            sequence_state["counter"] = sequence_state.get("counter", 0) + 1
            wf_logger.info("[%s] UserDriven greeting sent", workflow_name_upper)

    # Main orchestration loop
    for turn in range(max_turns):
        if current_agent_name in (None, "terminate"):
            run_completed = True
            break

        agent = agents.get(current_agent_name)
        if agent is None:
            wf_logger.warning("[%s] Agent '%s' not found; ending loop", workflow_name_upper, current_agent_name)
            run_completed = True
            break

        # Emit select_speaker so UI shows thinking indicator
        await transport.send_event_to_ui({
            "kind": "select_speaker",
            "agent": current_agent_name,
            "sequence": sequence_state.get("counter", 0),
        }, chat_id)

        # Build a MemoryStream for this turn; subscribe event forwarder
        turn_stream = MemoryStream()
        await _forward_beta_events(
            turn_stream, transport, chat_id, current_agent_name,
            persistence_manager, wf_logger, sequence_state,
        )

        # Execute agent turn
        wf_logger.info(
            "[%s] Turn %d — running agent '%s' (history=%d)",
            workflow_name_upper, turn, current_agent_name, len(history),
        )

        # Convert history list to beta-compatible messages
        # Beta Agent.ask() accepts str | Input; we pass the last user message
        # as a string and the rest as prior context via the stream history.
        # For full history replay we pass the whole list as a joined prompt
        # via variables and pass the user-facing trigger as the first arg.

        # Determine the user-facing message for this turn
        user_messages = [m for m in history if isinstance(m, dict) and m.get("role") == "user"]
        last_user_msg = (user_messages[-1].get("content") or "") if user_messages else ""

        # Keep the shared context bridge aligned with the per-turn AG2 variables so
        # tools can observe the same live history and runtime metadata as the agent.
        context_vars_dict["_mozaiks_history"] = list(history)
        context_vars_dict["_mozaiks_agent_name"] = current_agent_name
        context_vars_dict["_mozaiks_chat_id"] = chat_id
        context_vars_dict["_mozaiks_app_id"] = app_id

        if lifecycle_manager is not None:
            try:
                await lifecycle_manager.trigger_before_agent(
                    current_agent_name,
                    context_variables=context_bridge,
                )
            except Exception as lc_err:
                wf_logger.debug(
                    "[%s] Lifecycle before_agent failed for '%s': %s",
                    workflow_name_upper,
                    current_agent_name,
                    lc_err,
                )

        # Build context with full history for the agent
        turn_vars = dict(context_vars_dict)

        try:
            last_reply = await agent.ask(
                last_user_msg or ".",
                stream=turn_stream,
                variables=turn_vars,
            )
        except Exception as ask_err:
            wf_logger.error(
                "[%s] agent.ask() failed for '%s': %s", workflow_name_upper, current_agent_name, ask_err,
                exc_info=True,
            )
            run_completed = True
            break

        # After ask() completes, sync context_vars_dict with any updates from variables
        # (AG2 tool calls receive the per-turn variables object and may mutate it).
        for key, value in turn_vars.items():
            context_vars_dict[key] = value

        # Apply agent_text derived context triggers before routing.
        if derived_context_manager is not None and last_reply is not None:
            reply_body = last_reply.body if hasattr(last_reply, "body") else str(last_reply or "")
            if reply_body:
                derived_context_manager.apply_agent_text(current_agent_name, reply_body)

        structured_payload = await _emit_validated_agent_output(
            current_agent_name=current_agent_name,
            last_reply=last_reply,
            workflow_name=workflow_name,
            chat_id=chat_id,
            app_id=app_id,
            user_id=user_id,
            turn_sequence=turn,
            context_vars_dict=context_vars_dict,
            context_bridge=context_bridge,
            structured_registry=structured_registry,
            auto_tool_agents=auto_tool_agents,
            wf_logger=wf_logger,
        )
        if isinstance(structured_payload, dict):
            code_files = structured_payload.get("code_files")
            if isinstance(code_files, list) and code_files:
                file_map: Dict[str, str] = {}
                for entry in code_files:
                    if not isinstance(entry, dict):
                        continue
                    filename = entry.get("filename") or entry.get("path")
                    content = entry.get("content")
                    if filename and content is not None:
                        file_map[str(filename)] = str(content)
                if file_map and not (
                    current_agent_name in auto_tool_agents
                    and isinstance(context_vars_dict.get("generated_files"), dict)
                    and context_vars_dict.get("generated_files")
                ):
                    context_vars_dict["generated_files"] = file_map
                    context_vars_dict.setdefault("assembled_source", "structured_code_files")

        if task_batches_config is not None:
            try:
                from .task_batches import execute_task_batches_for_trigger

                await execute_task_batches_for_trigger(
                    workflow_name=workflow_name,
                    trigger_agent=current_agent_name,
                    batches_config=task_batches_config,
                    agents=agents,
                    context_variables=context_vars_dict,
                    structured_output=structured_payload,
                    chat_id=chat_id,
                    app_id=app_id,
                    user_id=user_id,
                    transport=transport,
                    wf_logger=wf_logger,
                )
            except Exception as task_batch_err:
                wf_logger.error(
                    "[%s] Task batch execution failed after %s: %s",
                    workflow_name_upper,
                    current_agent_name,
                    task_batch_err,
                    exc_info=True,
                )
                run_completed = True
                break

        # Determine next agent through the compiled AG2 Network graph.
        if sequence_state.get("awaiting_user_input"):
            awaiting_user_input = True
            wf_logger.info("[%s] Pausing for user input at agent '%s'", workflow_name_upper, current_agent_name)
            break

        next_agent = resolve_next_agent(
            transition_graph,
            current_agent_name=current_agent_name,
            context_variables=context_vars_dict,
            agent_name_by_id=agent_name_by_id,
            participant_order=list(agent_id_by_name.values()),
        )
        wf_logger.info("[%s] Routing: %s -> %s", workflow_name_upper, current_agent_name, next_agent)

        if next_agent == "user":
            awaiting_user_input = True
            break
        elif next_agent in ("terminate", None):
            run_completed = True
            break
        else:
            # Add agent response to shared history for next agent
            if last_reply and last_reply.body:
                body = last_reply.body
                if not isinstance(body, str):
                    body = json.dumps(_reply_body_to_data(last_reply), ensure_ascii=False, default=str)
                history.append({
                    "role": "assistant",
                    "name": current_agent_name,
                    "content": _compact_history_content(current_agent_name, body),
                })
            current_agent_name = next_agent

    else:
        # Exhausted max_turns
        run_completed = True

    # Emit run completion event
    await transport.send_event_to_ui({
        "kind": "run_complete",
        "workflow": workflow_name,
        "chat_id": chat_id,
        "run_completed": run_completed,
        "awaiting_user_input": awaiting_user_input,
    }, chat_id)

    return {
        "response": last_reply,
        "run_completed": run_completed,
        "awaiting_user_input": awaiting_user_input,
        "sequence_counter": sequence_state.get("counter", 0),
    }


# ---------------------------------------------------------------------------
# Main orchestration entry point
# ---------------------------------------------------------------------------

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
    **kwargs: Any,
) -> Optional[Dict[str, Any]]:
    start_time = perf_counter()
    workflow_name_upper = workflow_name.upper()
    agents: Dict[str, Any] = {}
    stream_state: Dict[str, Any] = {}
    result_payload: Optional[Dict[str, Any]] = None
    workflow_status_value = 0

    wf_logger = get_workflow_logger(workflow_name, chat_id=chat_id, app_id=app_id)
    wf_lifecycle_logger = get_workflow_logger(workflow_name, chat_id=chat_id)
    logger.info("[ORCHESTRATION] Starting %s workflow", workflow_name)

    persistence_manager = AG2PersistenceManager()

    from mozaiksai.core.transport.simple_transport import SimpleTransport

    transport = await SimpleTransport.get_instance()
    if not transport:
        raise RuntimeError(f"SimpleTransport not available for {workflow_name}")

    termination_handler = create_termination_handler(
        chat_id=chat_id, app_id=app_id, workflow_name=workflow_name, transport=transport,
    )

    trace_id_hex = uuid.uuid4().hex
    perf_mgr = await get_performance_manager()
    await perf_mgr.initialize()
    await perf_mgr.record_workflow_start(chat_id, app_id, workflow_name, user_id or "unknown")
    await perf_mgr.attach_trace_id(chat_id, trace_id_hex)

    with ag2_logging_session(chat_id, workflow_name, app_id):
        try:
            # 1) Load configuration
            cfg = _load_workflow_config(workflow_name)
            config = cfg["config"]
            max_turns = cfg["max_turns"]
            workflow_startup_mode = cfg["workflow_startup_mode"]
            initial_agent_name: str = cfg["initial_agent_name"]
            handoff_rules: List[Dict[str, Any]] = (
                (config.get("handoffs") or {}).get("handoff_rules") or []
            )

            if initial_agent_name_override:
                initial_agent_name = str(initial_agent_name_override)

            wf_logger.info(
                "[%s] CONFIG: mode=%s pattern=beta initial_agent=%s",
                workflow_name_upper, workflow_startup_mode, initial_agent_name,
            )

            # 2) Resume or start chat
            resumed_messages, initial_messages = await _resume_or_initialize_chat(
                persistence_manager=persistence_manager,
                termination_handler=termination_handler,
                config=config,
                chat_id=chat_id,
                app_id=app_id,
                workflow_name=workflow_name,
                user_id=user_id,
                initial_message=initial_message,
                initial_agent_name=initial_agent_name,
                wf_logger=wf_logger,
            )
            resumed_mode = bool(resumed_messages)

            # 3) Cache seed
            try:
                cache_seed = await persistence_manager.get_or_assign_cache_seed(chat_id, app_id)
            except Exception:
                cache_seed = None

            # 4) Structured outputs and task batch preload
            structured_registry: Dict[str, Any] = {}
            try:
                from .outputs.structured import load_workflow_structured_outputs as _preload_so

                _, structured_registry = _preload_so(workflow_name)
            except Exception as so_err:
                wf_logger.warning("[%s] Structured outputs preload failed: %s", workflow_name_upper, so_err)

            try:
                from .task_batches import load_task_batches_config

                task_batches_config = load_task_batches_config(workflow_name)
            except Exception as tb_err:
                task_batches_config = None
                wf_logger.warning("[%s] task_batches.yaml preload failed: %s", workflow_name_upper, tb_err)

            try:
                from .workflow_manager import workflow_manager

                auto_tool_agents = set(workflow_manager.get_auto_tool_agents(workflow_name))
            except Exception:
                auto_tool_agents = set()

            chat_logger.info(
                "[%s] WORKFLOW_STARTED chat_id=%s mode=%s",
                workflow_name_upper, chat_id, workflow_startup_mode,
            )

            # 5) Build context
            context_start = perf_counter()
            frontend_context: Optional[Dict[str, Any]] = None
            try:
                if transport and hasattr(transport, "connections") and chat_id in transport.connections:
                    frontend_context = transport.connections[chat_id].get("frontend_context")
            except Exception:
                pass

            context: Any = None
            if context_factory:
                result_ctx = context_factory()
                if inspect.isawaitable(result_ctx):
                    context = await result_ctx
                else:
                    context = result_ctx
            else:
                from .context.variables import _load_context_async
                context = await _load_context_async(workflow_name, app_id)

            # Merge frontend context
            if frontend_context and context is not None:
                for key, value in frontend_context.items():
                    prefixed = f"ui_{key}" if not key.startswith("ui_") else key
                    try:
                        if hasattr(context, "set"):
                            context.set(prefixed, value)
                        elif hasattr(context, "__setitem__"):
                            context[prefixed] = value
                    except Exception:
                        pass

            # Merge persisted extra context (caller-provided run context and correlation)
            try:
                if context is not None:
                    extra_ctx = await persistence_manager.fetch_chat_session_extra_context(chat_id=chat_id, app_id=app_id)
                    if isinstance(extra_ctx, dict) and extra_ctx:
                        _merge_persisted_extra_context(context, extra_ctx)
            except Exception as seed_err:
                wf_logger.debug("[%s] Persisted extra context merge failed: %s", workflow_name_upper, seed_err)

            context_time = (perf_counter() - context_start) * 1000
            performance_logger.info("context_load_duration_ms", extra={
                "metric_name": "context_load_duration_ms", "value": float(context_time),
                "unit": "ms", "workflow_name": workflow_name, "app_id": app_id,
            })

            # Flatten context to a plain dict for beta Agent
            if context is None:
                ctx_dict: Dict[str, Any] = {}
            elif hasattr(context, "to_dict"):
                ctx_dict = context.to_dict()
            elif hasattr(context, "data") and isinstance(getattr(context, "data", None), dict):
                ctx_dict = dict(context.data)
            elif isinstance(context, dict):
                ctx_dict = context
            else:
                ctx_dict = {}

            # Ensure routing keys are present
            ctx_dict.setdefault("workflow_name", workflow_name)
            ctx_dict.setdefault("app_id", app_id)
            ctx_dict.setdefault("chat_id", chat_id)
            if user_id:
                ctx_dict.setdefault("user_id", user_id)

            # 6) Create agents
            if agents_factory:
                agents = await agents_factory(workflow_name, context, cache_seed)
            else:
                from .agents import create_agents
                agents = await create_agents(workflow_name, context_variables=context, cache_seed=cache_seed)

            agents = agents or {}
            if not agents:
                raise RuntimeError(f"No agents defined for workflow '{workflow_name}'")

            # Get context_bridge from any local agent (they all share the same bridge)
            context_bridge = None
            for ag in agents.values():
                cb = getattr(ag, "_mozaiks_context_bridge", None)
                if cb is not None:
                    context_bridge = cb
                    # Sync ctx_dict into the bridge's underlying data dict
                    context_bridge._data.update(ctx_dict)
                    ctx_dict = context_bridge._data  # point to the same dict
                    break

            if context_bridge is None:
                from .agents.factory import ContextVariablesBridge
                context_bridge = ContextVariablesBridge(ctx_dict)

            # Store agents on transport
            try:
                if transport and hasattr(transport, "connections") and chat_id in transport.connections:
                    transport.connections[chat_id]["agents"] = agents
                    transport.connections[chat_id]["context"] = ctx_dict
            except Exception:
                pass

            # Derived context manager
            derived_context_manager: Optional[Any] = None
            try:
                derived_context_manager = DerivedContextManager(workflow_name, agents, context)
                if derived_context_manager.has_variables():
                    derived_context_manager.seed_defaults()

                    def _derived_listener(payload: Dict[str, Any]) -> None:
                        try:
                            var_name = payload.get("variable")
                            value = payload.get("value")
                            if var_name and transport:
                                asyncio.create_task(transport.send_event_to_ui({
                                    "kind": "context_update",
                                    "variable": var_name,
                                    "value": value,
                                }, chat_id))
                        except Exception:
                            pass

                    derived_context_manager.add_listener(_derived_listener)
                else:
                    derived_context_manager = None
            except Exception as dcm_err:
                wf_logger.debug("[%s] DerivedContextManager setup failed: %s", workflow_name_upper, dcm_err)
                derived_context_manager = None

            if derived_context_manager and transport and hasattr(transport, "register_derived_context_manager"):
                try:
                    transport.register_derived_context_manager(chat_id, derived_context_manager)
                except Exception:
                    pass

            # Validate handoff routing (warn on misconfigured rules)
            try:
                from .agents.handoffs import wire_handoffs_with_debugging
                wire_handoffs_with_debugging(workflow_name, agents)
            except Exception as hw_err:
                wf_logger.debug("[%s] Handoff validation failed: %s", workflow_name_upper, hw_err)

            # 7) Normalize initial messages
            initial_messages = _normalize_to_strict_ag2(initial_messages, default_user_name="user")

            # 8) Lifecycle before_chat
            lifecycle_manager = None
            try:
                from mozaiksai.core.workflow.execution.lifecycle import get_lifecycle_manager
                lifecycle_manager = get_lifecycle_manager(workflow_name)
                # Wrap ctx_dict in a bridge-compatible object for lifecycle tools
                await lifecycle_manager.trigger_before_chat(context_variables=context_bridge)
                wf_logger.info("[%s] Lifecycle before_chat completed", workflow_name_upper)
            except Exception as lc_err:
                wf_logger.debug("[%s] Lifecycle before_chat failed: %s", workflow_name_upper, lc_err)

            wf_lifecycle_logger.info(
                "[%s] Starting beta agent orchestration",
                workflow_name_upper,
                agent_count=len(agents),
                max_turns=max_turns,
                is_resume=resumed_mode,
            )

            # 9) Execute beta orchestration loop
            stream_state = await _run_beta_orchestration_loop(
                agents=agents,
                initial_agent_name=initial_agent_name,
                initial_messages=initial_messages,
                resumed_messages=resumed_messages,
                context_vars_dict=ctx_dict,
                context_bridge=context_bridge,
                handoff_rules=handoff_rules,
                max_turns=max_turns,
                transport=transport,
                chat_id=chat_id,
                app_id=app_id,
                workflow_name=workflow_name,
                workflow_name_upper=workflow_name_upper,
                user_id=user_id,
                persistence_manager=persistence_manager,
                perf_mgr=perf_mgr,
                wf_logger=wf_logger,
                lifecycle_manager=lifecycle_manager,
                derived_context_manager=derived_context_manager,
                workflow_startup_mode=workflow_startup_mode,
                config=config,
                task_batches_config=task_batches_config,
                structured_registry=structured_registry,
                auto_tool_agents=auto_tool_agents,
            )

            # 10) Persist final context snapshot
            try:
                await persistence_manager.persist_context_variables(
                    chat_id=chat_id, app_id=app_id, variables=dict(ctx_dict),
                )
            except Exception as persist_ctx_err:
                wf_logger.debug("[%s] Final context persist failed: %s", workflow_name_upper, persist_ctx_err)

            # 11) Usage reconciliation
            await _reconcile_final_usage(
                agents=agents,
                persistence_manager=persistence_manager,
                chat_id=chat_id,
                app_id=app_id,
                user_id=user_id,
                workflow_name=workflow_name,
                wf_logger=wf_logger,
            )

            run_completed = bool(stream_state.get("run_completed", False))
            awaiting_user_input = bool(stream_state.get("awaiting_user_input", False))
            workflow_complete = run_completed and not awaiting_user_input
            workflow_status_value = 1 if workflow_complete else 0

            if workflow_complete:
                try:
                    await termination_handler.on_conversation_end(max_turns_reached=False)
                except Exception as term_err:
                    logger.error("Termination handler failed: %s", term_err)
            else:
                wf_logger.info("[%s] Run paused awaiting user input", workflow_name_upper)

            duration_sec = perf_counter() - start_time
            wf_logger.info("[EXECUTION_COMPLETE] Duration: %.2fs", duration_sec)

            result_payload = {
                "workflow_name": workflow_name,
                "chat_id": chat_id,
                "app_id": app_id,
                "user_id": user_id,
                "messages": None,
                "max_turns_reached": False,
                "response": stream_state.get("response"),
                "run_completed": workflow_complete,
                "awaiting_user_input": awaiting_user_input,
                "run_status": workflow_status_value,
            }

        except Exception as e:
            logger.error("[%s] Orchestration failed: %s", workflow_name_upper, e, exc_info=True)
            try:
                await termination_handler.on_conversation_end()
            except Exception:
                pass
            raise
        finally:
            try:
                await perf_mgr.record_workflow_end(chat_id, workflow_status_value)
                await perf_mgr.flush(chat_id)
            except Exception:
                pass
            duration_sec = perf_counter() - start_time

    # Post-run cleanup
    try:
        duration = perf_counter() - start_time
        final_label = "completed" if workflow_status_value == 1 else "awaiting_input"
        wf_lifecycle_logger.info(
            "[%s] Workflow settled",
            workflow_name_upper,
            duration_sec=duration,
            event_count=stream_state.get("sequence_counter", 0) if isinstance(stream_state, dict) else 0,
            agent_count=len(agents),
            chat_id=chat_id,
            app_id=app_id,
            result_status=final_label if result_payload else "empty",
        )
        chat_logger.info(
            "[%s] WORKFLOW_%s chat_id=%s duration=%.2fs agents=%d",
            workflow_name_upper,
            "COMPLETED" if workflow_status_value == 1 else "AWAITING_INPUT",
            chat_id, duration, len(agents),
        )
        # Log agent outputs file if it exists
        try:
            agent_outputs_file = get_agent_outputs_dir() / f"agent_outputs_{chat_id}.jsonl"
            if agent_outputs_file.exists():
                file_size = agent_outputs_file.stat().st_size
                with open(agent_outputs_file, "r", encoding="utf-8") as f:
                    line_count = sum(1 for _ in f)
                print("\n" + "=" * 80)
                print(f"AGENT OUTPUTS LOG: {agent_outputs_file.resolve()}")
                print(f"Outputs: {line_count}  Size: {file_size:,} bytes")
                print("=" * 80 + "\n")
        except Exception:
            pass
    finally:
        try:
            keep_dcm = isinstance(stream_state, dict) and stream_state.get("awaiting_user_input")
            if transport and hasattr(transport, "unregister_derived_context_manager") and not keep_dcm:
                transport.unregister_derived_context_manager(chat_id)
        except Exception:
            pass

    return result_payload
