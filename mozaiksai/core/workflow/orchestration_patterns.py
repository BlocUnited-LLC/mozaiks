# ==============================================================================
# FILE: mozaiksai/core/workflow/orchestration_patterns.py
# DESCRIPTION: Workflow orchestration entry point.
#
# Wires together the AG2 beta agent factory, chat resume/init, AG2 Network
# execution, persistence, transport, lifecycle, and observability into a single
# async function that callers use to start or resume a workflow run.
#
# Internals are in focused execution sub-modules:
#   execution/stream_bridge.py  — AG2 MemoryStream → UI transport bridge
#   execution/resume.py         — chat resume / session init logic
# ==============================================================================

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from collections.abc import Callable
from time import perf_counter
from typing import Any

from logs.logging_config import get_workflow_logger
from logs.runtime_artifacts import get_agent_outputs_dir
from mozaiksai.core.adapters.ag2_network_runner import AG2NetworkRunner, AG2NetworkRunnerRequest
from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager
from mozaiksai.core.ports.orchestration import RunStatus

from .context import DerivedContextManager
from .execution.resume import merge_persisted_extra_context, resume_or_initialize_chat
from .messages import normalize_to_strict_ag2 as _normalize_to_strict_ag2
from .orchestration_utils import _load_workflow_config

logger = logging.getLogger(__name__)

chat_logger = get_workflow_logger("orchestration")
workflow_logger = get_workflow_logger("orchestration")
performance_logger = get_workflow_logger("performance.orchestration")

# ---------------------------------------------------------------------------
# Public re-exports — existing callers import these names from this module
# ---------------------------------------------------------------------------

# _merge_persisted_extra_context keeps its underscore-prefixed name for callers
# that import it (tests, ag2_orchestration adapter).
_merge_persisted_extra_context = merge_persisted_extra_context
_resume_or_initialize_chat = resume_or_initialize_chat

__all__ = [
    "run_workflow_orchestration",
    "_merge_persisted_extra_context",
]


# ---------------------------------------------------------------------------
# AG2 Network orchestration helpers
# ---------------------------------------------------------------------------

def _messages_to_network_prompt(messages: list[dict[str, Any]]) -> str:
    """Render persisted Mozaiks messages into one AG2 workflow-channel prompt."""

    rendered: list[str] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        role = str(message.get("role") or "user").strip() or "user"
        name = str(message.get("name") or role).strip() or role
        rendered.append(f"{name} ({role}): {content}")
    return "\n\n".join(rendered).strip() or "."


def _first_agent_payload_from_runner_result(runner_result: Any, agent_name: str) -> dict[str, Any] | None:
    for entry in runner_result.structured_outputs:
        if entry.get("agent") == agent_name and isinstance(entry.get("structured_data"), dict):
            return dict(entry["structured_data"])

    for envelope in runner_result.wal:
        if not isinstance(envelope, dict) or envelope.get("event_type") != "ag2.packet":
            continue
        sender_id = str(envelope.get("sender_id") or "")
        if runner_result.agent_name_by_id.get(sender_id) != agent_name:
            continue
        event_data = envelope.get("event_data")
        if not isinstance(event_data, dict):
            continue
        body = event_data.get("body")
        if isinstance(body, dict):
            return dict(body)
        if isinstance(body, str):
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError:
                return None
            return dict(parsed) if isinstance(parsed, dict) else None
    return None


def _next_agent_after_trigger(
    *,
    transition_rules: list[dict[str, Any]],
    trigger_agent: str,
) -> str | None:
    for rule in transition_rules:
        if str(rule.get("source_agent") or "").strip() != trigger_agent:
            continue
        target = str(rule.get("target_agent") or "").strip()
        if target and target != "terminate":
            return target
    return None


async def _emit_startup_greeting_if_needed(
    *,
    config: dict[str, Any],
    resumed_messages: list[dict[str, Any]],
    workflow_startup_mode: str,
    initial_agent_name: str,
    transport: Any,
    persistence_manager: AG2PersistenceManager,
    chat_id: str,
    app_id: str,
    workflow_name_upper: str,
    wf_logger: Any,
) -> int:
    """Emit the static UserDriven greeting before AG2 owns agent turns."""

    if resumed_messages or workflow_startup_mode != "UserDriven":
        return 0
    greeting = str(config.get("initial_message_to_user") or "").strip()
    if not greeting:
        return 0

    await transport.send_event_to_ui(
        {
            "kind": "chat.text",
            "agent": initial_agent_name,
            "role": "assistant",
            "content": greeting,
            "sequence": 0,
        },
        chat_id,
    )
    await persistence_manager.append_run_assistant_message(
        chat_id=chat_id,
        app_id=app_id,
        content=greeting,
        agent_name=initial_agent_name,
        metadata={"source": "startup_greeting"},
    )
    wf_logger.info("[%s] UserDriven greeting sent", workflow_name_upper)
    return 1


async def _project_ag2_wal_to_mozaiks_transport(
    *,
    runner_result: Any,
    transport: Any,
    persistence_manager: AG2PersistenceManager,
    chat_id: str,
    app_id: str,
    agent_name_by_id: dict[str, str],
    initial_sequence: int,
) -> int:
    """Project AG2 round-end packets into Mozaiks chat events and run storage."""

    sequence = initial_sequence
    for envelope in runner_result.wal:
        if not isinstance(envelope, dict) or envelope.get("event_type") != "ag2.packet":
            continue
        agent_name = agent_name_by_id.get(str(envelope.get("sender_id") or ""))
        if not agent_name:
            continue
        event_data = envelope.get("event_data")
        if not isinstance(event_data, dict):
            continue
        body = event_data.get("body")
        if isinstance(body, (dict, list)):
            content = json.dumps(body, ensure_ascii=False, default=str)
        else:
            content = str(body or "").strip()
        if not content:
            continue

        await transport.send_event_to_ui(
            {
                "kind": "chat.text",
                "agent": agent_name,
                "role": "assistant",
                "content": content,
                "sequence": sequence,
                "source": "ag2_network_wal",
            },
            chat_id,
        )
        await persistence_manager.append_run_assistant_message(
            chat_id=chat_id,
            app_id=app_id,
            content=content,
            agent_name=agent_name,
            metadata={"source": "ag2_network_wal", "channel_id": runner_result.channel_id},
        )
        sequence += 1
    return sequence


async def _run_ag2_network_phase(
    *,
    workflow_name: str,
    chat_id: str,
    app_id: str,
    agents: dict[str, Any],
    transition_rules: list[dict[str, Any]],
    initial_agent_name: str,
    initial_message: str,
    context_variables: dict[str, Any],
    structured_registry: dict[str, Any],
    max_turns: int,
) -> Any:
    return await AG2NetworkRunner().run(
        AG2NetworkRunnerRequest(
            workflow_name=workflow_name,
            chat_id=chat_id,
            app_id=app_id,
            agents=agents,
            transition_rules=transition_rules,
            initial_agent_name=initial_agent_name,
            initial_message=initial_message,
            context_variables=context_variables,
            structured_registry=structured_registry,
            max_turns=max_turns,
        )
    )


# ---------------------------------------------------------------------------
# Main orchestration entry point
# ---------------------------------------------------------------------------

async def run_workflow_orchestration(
    workflow_name: str,
    app_id: str,
    chat_id: str,
    user_id: str | None = None,
    initial_message: str | None = None,
    initial_agent_name_override: str | None = None,
    agents_factory: Callable | None = None,
    context_factory: Callable | None = None,
    transition_graph_factory: Callable | None = None,
    **kwargs: Any,
) -> dict[str, Any] | None:
    start_time = perf_counter()
    workflow_name_upper = workflow_name.upper()
    agents: dict[str, Any] = {}
    stream_state: dict[str, Any] = {}
    result_payload: dict[str, Any] | None = None
    workflow_status_value = 0

    wf_logger = get_workflow_logger(workflow_name, chat_id=chat_id, app_id=app_id)
    wf_lifecycle_logger = get_workflow_logger(workflow_name, chat_id=chat_id)
    logger.info("[ORCHESTRATION] Starting %s workflow", workflow_name)

    persistence_manager = AG2PersistenceManager()

    from mozaiksai.core.transport.simple_transport import SimpleTransport

    transport = await SimpleTransport.get_instance()
    if not transport:
        raise RuntimeError(f"SimpleTransport not available for {workflow_name}")

    lifecycle_manager = None

    try:
        # 1) Load configuration
        cfg = _load_workflow_config(workflow_name)
        config = cfg["config"]
        max_turns = cfg["max_turns"]
        workflow_startup_mode = cfg["workflow_startup_mode"]
        initial_agent_name: str = cfg["initial_agent_name"]
        transition_rules: list[dict[str, Any]] = (
            (config.get("transition_graph") or {}).get("transition_rules") or []
        )

        if initial_agent_name_override:
            initial_agent_name = str(initial_agent_name_override)

        wf_logger.info(
            "[%s] CONFIG: mode=%s pattern=beta initial_agent=%s",
            workflow_name_upper, workflow_startup_mode, initial_agent_name,
        )

        # 2) Resume or start chat
        resumed_messages, initial_messages = await resume_or_initialize_chat(
            persistence_manager=persistence_manager,
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

        # 4) Structured outputs and task batch contract check
        structured_registry: dict[str, Any] = {}
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

        chat_logger.info(
            "[%s] WORKFLOW_STARTED chat_id=%s mode=%s",
            workflow_name_upper, chat_id, workflow_startup_mode,
        )

        # 5) Build context
        context_start = perf_counter()
        frontend_context: dict[str, Any] | None = None
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

        try:
            if context is not None:
                extra_ctx = await persistence_manager.fetch_chat_session_extra_context(chat_id=chat_id, app_id=app_id)
                if isinstance(extra_ctx, dict) and extra_ctx:
                    merge_persisted_extra_context(context, extra_ctx)
        except Exception as seed_err:
            wf_logger.debug("[%s] Persisted extra context merge failed: %s", workflow_name_upper, seed_err)

        context_time = (perf_counter() - context_start) * 1000
        performance_logger.info("context_load_duration_ms", extra={
            "metric_name": "context_load_duration_ms", "value": float(context_time),
            "unit": "ms", "workflow_name": workflow_name, "app_id": app_id,
        })

        # Flatten context to a plain dict for beta Agent
        if context is None:
            ctx_dict: dict[str, Any] = {}
        elif hasattr(context, "to_dict"):
            ctx_dict = context.to_dict()
        elif hasattr(context, "data") and isinstance(getattr(context, "data", None), dict):
            ctx_dict = dict(context.data)
        elif isinstance(context, dict):
            ctx_dict = context
        else:
            ctx_dict = {}

        ctx_dict.setdefault("workflow_name", workflow_name)
        ctx_dict.setdefault("app_id", app_id)
        ctx_dict.setdefault("chat_id", chat_id)
        if user_id:
            ctx_dict.setdefault("user_id", user_id)
        if context is not None:
            for key in ("workflow_name", "app_id", "chat_id", "user_id"):
                if key not in ctx_dict:
                    continue
                try:
                    if hasattr(context, "set"):
                        context.set(key, ctx_dict[key])
                    elif hasattr(context, "__setitem__"):
                        context[key] = ctx_dict[key]
                except Exception:
                    pass

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
                context_bridge._data.update(ctx_dict)
                ctx_dict = context_bridge._data
                break

        if context_bridge is None:
            from .agents.factory import ContextVariablesBridge
            context_bridge = ContextVariablesBridge(ctx_dict)

        try:
            if transport and hasattr(transport, "connections") and chat_id in transport.connections:
                transport.connections[chat_id]["agents"] = agents
                transport.connections[chat_id]["context"] = ctx_dict
        except Exception:
            pass

        # 7) Derived context manager
        derived_context_manager: Any | None = None
        try:
            derived_context_manager = DerivedContextManager(workflow_name, agents, context)  # type: ignore[misc]
            if derived_context_manager.has_variables():
                derived_context_manager.seed_defaults()

                def _derived_listener(payload: dict[str, Any]) -> None:
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

        try:
            from .agents.transition_graph import wire_transition_graph_with_debugging
            wire_transition_graph_with_debugging(workflow_name, agents)
        except Exception as hw_err:
            wf_logger.debug("[%s] Transition graph validation failed: %s", workflow_name_upper, hw_err)

        # 8) Normalize initial messages
        initial_messages = _normalize_to_strict_ag2(initial_messages, default_user_name="user")

        # 9) Lifecycle before_chat
        try:
            from mozaiksai.core.workflow.execution.lifecycle import get_lifecycle_manager
            lifecycle_manager = get_lifecycle_manager(workflow_name)
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

        # 10) Execute AG2 Network workflow channel
        startup_sequence = await _emit_startup_greeting_if_needed(
            config=config,
            resumed_messages=resumed_messages,
            workflow_startup_mode=workflow_startup_mode,
            initial_agent_name=initial_agent_name,
            transport=transport,
            persistence_manager=persistence_manager,
            chat_id=chat_id,
            app_id=app_id,
            workflow_name_upper=workflow_name_upper,
            wf_logger=wf_logger,
        )
        network_prompt = _messages_to_network_prompt(initial_messages)
        projected_sequence = startup_sequence
        project_final_runner_result = True
        if task_batches_config is not None:
            trigger_agent_name = initial_agent_name
            first_phase_result = await _run_ag2_network_phase(
                workflow_name=workflow_name,
                chat_id=chat_id,
                app_id=app_id,
                agents=agents,
                transition_rules=[
                    {
                        "source_agent": trigger_agent_name,
                        "target_agent": "terminate",
                        "transition_type": "after_turn",
                    }
                ],
                initial_agent_name=trigger_agent_name,
                initial_message=network_prompt,
                context_variables=dict(ctx_dict),
                structured_registry=structured_registry,
                max_turns=max_turns,
            )
            if first_phase_result.status is not RunStatus.COMPLETED:
                runner_result = first_phase_result
            else:
                projected_sequence = await _project_ag2_wal_to_mozaiks_transport(
                    runner_result=first_phase_result,
                    transport=transport,
                    persistence_manager=persistence_manager,
                    chat_id=chat_id,
                    app_id=app_id,
                    agent_name_by_id=first_phase_result.agent_name_by_id,
                    initial_sequence=projected_sequence,
                )
                structured_payload = _first_agent_payload_from_runner_result(
                    first_phase_result,
                    trigger_agent_name,
                )
                from .task_batches import execute_task_batches_for_trigger

                await execute_task_batches_for_trigger(
                    workflow_name=workflow_name,
                    trigger_agent=trigger_agent_name,
                    batches_config=task_batches_config,
                    agents=agents,
                    context_variables=ctx_dict,
                    structured_output=structured_payload,
                    chat_id=chat_id,
                    app_id=app_id,
                    user_id=user_id,
                    transport=transport,
                    wf_logger=wf_logger,
                    fresh_agents_per_task=True,
                    agents_factory=agents_factory,
                )
                continuation_agent = _next_agent_after_trigger(
                    transition_rules=transition_rules,
                    trigger_agent=trigger_agent_name,
                )
                if continuation_agent:
                    runner_result = await _run_ag2_network_phase(
                        workflow_name=workflow_name,
                        chat_id=chat_id,
                        app_id=app_id,
                        agents=agents,
                        transition_rules=transition_rules,
                        initial_agent_name=continuation_agent,
                        initial_message="Continue with the completed deterministic task batch outputs.",
                        context_variables=ctx_dict,
                        structured_registry=structured_registry,
                        max_turns=max_turns,
                    )
                else:
                    runner_result = first_phase_result
                    project_final_runner_result = False
        else:
            runner_result = await _run_ag2_network_phase(
                workflow_name=workflow_name,
                chat_id=chat_id,
                app_id=app_id,
                agents=agents,
                transition_rules=transition_rules,
                initial_agent_name=initial_agent_name,
                initial_message=network_prompt,
                context_variables=ctx_dict,
                structured_registry=structured_registry,
                max_turns=max_turns,
            )

        if project_final_runner_result:
            sequence_counter = await _project_ag2_wal_to_mozaiks_transport(
                runner_result=runner_result,
                transport=transport,
                persistence_manager=persistence_manager,
                chat_id=chat_id,
                app_id=app_id,
                agent_name_by_id=runner_result.agent_name_by_id,
                initial_sequence=projected_sequence,
            )
        else:
            sequence_counter = projected_sequence

        ctx_dict.update(dict(runner_result.context_variables or {}))
        run_failed = runner_result.status is RunStatus.FAILED
        run_error = runner_result.error
        awaiting_user_input = runner_result.status is RunStatus.PAUSED
        run_completed = runner_result.status is RunStatus.COMPLETED
        run_status = (
            "failed" if run_failed
            else "paused" if awaiting_user_input
            else "completed" if run_completed
            else "in_progress"
        )
        await transport.send_event_to_ui(
            {
                "kind": "run_complete",
                "workflow": workflow_name,
                "chat_id": chat_id,
                "run_completed": bool(run_completed and not run_failed),
                "awaiting_user_input": awaiting_user_input,
                "status": run_status,
                "reason": "failed" if run_failed else ("awaiting_user_input" if awaiting_user_input else "finished"),
                **({"error": run_error} if run_error else {}),
            },
            chat_id,
        )
        stream_state = {
            "run_completed": bool(run_completed and not run_failed),
            "awaiting_user_input": awaiting_user_input,
            "failed": run_failed,
            "error": run_error,
            "run_status": run_status,
            "sequence_counter": sequence_counter,
        }

        # 11) Persist final context snapshot
        try:
            await persistence_manager.persist_context_variables(
                chat_id=chat_id, app_id=app_id, variables=dict(ctx_dict),
            )
        except Exception as persist_ctx_err:
            wf_logger.debug("[%s] Final context persist failed: %s", workflow_name_upper, persist_ctx_err)

        workflow_complete = run_completed and not awaiting_user_input and not run_failed
        workflow_status_value = 1 if workflow_complete else 0

        if run_failed:
            wf_logger.error("[%s] Run failed%s", workflow_name_upper, f": {run_error}" if run_error else "")
            if lifecycle_manager is not None:
                try:
                    await lifecycle_manager.execute_trigger(
                        "on_fail",
                        context_variables=context_bridge,
                        app_id=app_id,
                        execution_id=chat_id,
                        chat_id=chat_id,
                        user_id=user_id,
                        workflow_name=workflow_name,
                        error=run_error,
                    )
                except Exception as fail_lifecycle_err:
                    wf_logger.debug("[%s] Lifecycle on_fail failed: %s", workflow_name_upper, fail_lifecycle_err)
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
            "response": None,
            "run_completed": workflow_complete,
            "awaiting_user_input": awaiting_user_input,
            "run_status": "failed" if run_failed else workflow_status_value,
            "failed": run_failed,
            "error": run_error,
            "ag2_channel_id": runner_result.channel_id,
            "ag2_close_reason": runner_result.close_reason,
            "structured_outputs": runner_result.structured_outputs,
        }

    except Exception as e:
        logger.error("[%s] Orchestration failed: %s", workflow_name_upper, e, exc_info=True)
        if lifecycle_manager is not None:
            try:
                await lifecycle_manager.execute_trigger(
                    "on_fail",
                    app_id=app_id,
                    execution_id=chat_id,
                    chat_id=chat_id,
                    user_id=user_id,
                    workflow_name=workflow_name,
                    error=str(e),
                )
            except Exception:
                pass
        try:
            await transport.send_event_to_ui(
                {"kind": "error", "workflow": workflow_name, "chat_id": chat_id, "message": str(e), "error": str(e), "status": "failed"},
                chat_id,
            )
        except Exception:
            pass
        try:
            await transport.send_event_to_ui(
                {"kind": "run_complete", "workflow": workflow_name, "chat_id": chat_id, "run_completed": False, "awaiting_user_input": False, "status": "failed", "reason": "failed", "error": str(e)},
                chat_id,
            )
        except Exception:
            pass
        raise
    finally:
        try:
            if workflow_status_value == 1:
                await persistence_manager.mark_chat_completed(chat_id, app_id=app_id)
        except Exception:
            pass

    # Post-run cleanup and final logging
    try:
        duration = perf_counter() - start_time
        failed_label = bool(isinstance(result_payload, dict) and result_payload.get("failed"))
        final_label = "completed" if workflow_status_value == 1 else ("failed" if failed_label else "awaiting_input")
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
            "COMPLETED" if workflow_status_value == 1 else ("FAILED" if failed_label else "AWAITING_INPUT"),
            chat_id, duration, len(agents),
        )
        try:
            agent_outputs_file = get_agent_outputs_dir() / f"agent_outputs_{chat_id}.jsonl"
            if agent_outputs_file.exists():
                file_size = agent_outputs_file.stat().st_size
                with open(agent_outputs_file, encoding="utf-8") as f:
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
