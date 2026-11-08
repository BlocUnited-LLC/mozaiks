# ==============================================================================
# FILE: mozaiksai/core/workflow/ui_tools.py
# DESCRIPTION: Centralized helper utilities for agent-driven UI interactions.
#   - UI tool emission + response handling
#   - InputTimeoutEvent (kept lightweight)
#   - (Normalization & event translation removed; orchestration handles streaming payloads)
# ==============================================================================

from __future__ import annotations

import asyncio
import datetime as _dt
import logging
import uuid
from copy import deepcopy
from typing import Any

from logs.logging_config import get_workflow_logger
from mozaiksai.core.workflow.workflow_manager import workflow_manager

logger = logging.getLogger(__name__)


try:  # Prefer pydantic if available
    from pydantic import BaseModel as _PydanticBase
    BaseModel = _PydanticBase  # type: ignore
except Exception:  # pragma: no cover
    class BaseModel:  # type: ignore[no-redef]  # minimal fallback
        def model_dump(self, *a, **k):
            return {k: v for k, v in self.__dict__.items() if not k.startswith('_')}

class InputTimeoutEvent(BaseModel):
    input_request_id: str
    timeout_seconds: float
    chat_id: str
    occurred_at: str = _dt.datetime.now(_dt.UTC).isoformat()
    reason: str = "input_timeout"

    def dict(self, *a, **kw):  # pragma: no cover
        if hasattr(super(), "model_dump"):
            try:
                return super().model_dump(*a, **kw)  # type: ignore
            except Exception:
                return self.__dict__
        return self.__dict__

class UIToolError(Exception):
    """Custom exception for UI tool errors."""
    pass

def is_ui_tool(tool_id: str) -> bool:
    """Return True if tool_id is registered as a UI tool in any workflow.

    Thin wrapper over workflow_manager registry; keeps runtime surface minimal.
    """
    try:
        for rec in workflow_manager.iter_ui_tools():
            if rec.get('tool_id') == tool_id or rec.get('fn') == tool_id:
                return True
    except Exception:
        return False
    return False


def _format_action_label(action_id: str) -> str:
    parts = [part for part in str(action_id or "").replace("-", "_").split("_") if part]
    if not parts:
        return "Action"
    return " ".join(part.capitalize() for part in parts)


def _normalize_manifest_action(action: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(action, dict):
        return None
    action_id = str(action.get("id") or "").strip()
    if not action_id:
        return None
    normalized: dict[str, Any] = {
        "id": action_id,
        "label": str(action.get("label") or _format_action_label(action_id)).strip(),
    }
    description = str(action.get("description") or "").strip()
    if description:
        normalized["description"] = description
    variant = str(action.get("variant") or "").strip()
    if variant:
        normalized["variant"] = variant
    if isinstance(action.get("approved"), bool):
        normalized["approved"] = action["approved"]
    payload_schema = action.get("payload_schema")
    if isinstance(payload_schema, dict) and payload_schema:
        normalized["payload_schema"] = deepcopy(payload_schema)
    payload = action.get("payload")
    if isinstance(payload, dict) and payload:
        normalized["payload"] = deepcopy(payload)
    return normalized


def _enrich_payload_with_ui_contract(
    tool_id: str,
    payload: dict[str, Any],
    wf_logger,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    enriched_payload = dict(payload if isinstance(payload, dict) else {})
    tool_record = workflow_manager.get_ui_tool_record(tool_id)
    if not tool_record:
        return enriched_payload, None

    component_name = str(tool_record.get("component") or tool_id).strip() or tool_id
    workflow_primitive = str(tool_record.get("workflow_primitive") or "").strip()
    realization = str(tool_record.get("realization") or "").strip()
    if component_name:
        enriched_payload["component_type"] = component_name
    if workflow_primitive:
        enriched_payload["workflow_primitive"] = workflow_primitive
    if realization:
        enriched_payload["ui_realization"] = realization

    ui_contract = tool_record.get("ui_contract")
    if isinstance(ui_contract, dict) and ui_contract:
        normalized_contract = deepcopy(ui_contract)
        actions_schema = normalized_contract.get("actions_schema")
        normalized_actions = []
        if isinstance(actions_schema, list):
            for action in actions_schema:
                normalized = _normalize_manifest_action(action)
                if normalized is not None:
                    normalized_actions.append(normalized)
            normalized_contract["actions_schema"] = normalized_actions

        enriched_payload["ui_contract"] = normalized_contract
        if normalized_actions and not isinstance(enriched_payload.get("actions"), list):
            enriched_payload["actions"] = [deepcopy(action) for action in normalized_actions]

        wf_logger.debug(
            "UI_PAYLOAD_ENRICHED tool=%s primitive=%s actions=%d",
            tool_id,
            workflow_primitive or "<none>",
            len(normalized_actions),
        )

    return enriched_payload, tool_record

async def _emit_tool_call_core(
    tool_id: str,
    payload: dict[str, Any],
    display: str = "inline",
    chat_id: str | None = None,
    workflow_name: str = "unknown",
    agent_name: str | None = None,
    awaiting_response: bool = True,
    component_name: str | None = None,
    require_persistence: bool = False,
) -> str:
    """
    Core function to emit a response-aware tool_call to the frontend.

    This function is the standardized way for any agent tool to request
    that a UI component be rendered.

    Args:
        tool_id: The unique identifier for the UI component (e.g., "agent_api_key_input").
        payload: The data required by the UI component (props).
        display: How the component should be displayed ("composer", "inline", or "artifact").
        chat_id: The ID of the chat session to send the event to.
        workflow_name: The name of the workflow emitting the event.

    Returns:
        The unique event ID for this interaction.
    """
    persistence_options: dict[str, Any] = {}
    if require_persistence:
        from mozaiksai.core.workflow.agents.factory import active_workflow_tool_run

        try:
            run_workflow, run_app, run_chat, _ = active_workflow_tool_run()
        except PermissionError as exc:
            raise UIToolError("Durable UI emission requires an active workflow tool invocation") from exc
        if (chat_id, workflow_name) != (run_chat, run_workflow):
            raise UIToolError("Durable UI emission does not match the active workflow scope")
        if display != "artifact" or awaiting_response:
            raise UIToolError("Durable UI emission requires a one-way artifact surface")
        persistence_options = {"require_persistence": True, "persistence_app_id": run_app}

    event_id = f"{tool_id}_{str(uuid.uuid4())[:8]}"
    wf_logger = get_workflow_logger(workflow_name=workflow_name, chat_id=chat_id)
    chat_logger = get_workflow_logger("ui_tools", chat_id=chat_id)
    
    try:
        from mozaiksai.core.transport.simple_transport import SimpleTransport
        transport = await SimpleTransport.get_instance()
    except Exception as e:
        wf_logger.error("[UI_TOOLS] Transport unavailable: %s", e, exc_info=True)
        raise UIToolError(f"SimpleTransport not available: {e}") from e

    payload_to_send = {
        **payload,
        "workflow_name": workflow_name,
        "display": display,
        "mode": payload.get("mode") or display,
        "interaction_type": payload.get("interaction_type") or ("ui_tool" if awaiting_response else "ui_surface"),
    }
    chat_logger.debug(
        "UI_TOOL_EVENT tool=%s event=%s display=%s payload_keys=%s", tool_id, event_id, display, list(payload_to_send.keys())[:12])
    
    # Extract agent_name from payload if not explicitly provided
    if not agent_name and isinstance(payload_to_send, dict):
        agent_name = payload_to_send.get("agent_name")
    
    try:
        persisted = await transport.send_tool_call_event(
            event_id=event_id,
            chat_id=chat_id,
            tool_name=tool_id,  # use actual tool id
            component_name=component_name or tool_id,
            display_type=display,
            payload=payload_to_send,
            agent_name=agent_name,
            awaiting_response=awaiting_response,
            **persistence_options,
        )
        if require_persistence and persisted is not True:
            raise UIToolError("Artifact persistence was not confirmed")
        wf_logger.debug("UI_TOOL_EMIT_OK event=%s", event_id)
        return event_id
    except Exception as e:
        wf_logger.error(
            "UI_TOOL_EMIT_FAILED event=%s: %s", event_id, e,
            exc_info=True,
        )
        raise UIToolError(f"Failed to emit UI tool event: {e}") from e

async def _wait_for_tool_call_response_internal(event_id: str, timeout: float | None = None) -> dict[str, Any]:
    from mozaiksai.core.transport.simple_transport import SimpleTransport  # local import
    transport = await SimpleTransport.get_instance()
    try:
        # Always wait indefinitely for user/UI response; ignore provided timeout to avoid premature cancellations.
        # Explicitly pass timeout=None to ensure no default timeout is applied in the transport layer.
        fut = transport.wait_for_tool_call_response(event_id, timeout=None)  # type: ignore[attr-defined]
        return await fut  # type: ignore[no-any-return]
    except Exception as e:  # pragma: no cover
        raise UIToolError(f"UI tool response failure for {event_id}: {e}") from e


def _resolve_ui_tool_owner(
    tool_id: str,
    payload: dict[str, Any],
    wf_logger,
) -> str | None:
    agent_name = None
    try:
        tool_record = workflow_manager.get_ui_tool_record(tool_id)
        if tool_record:
            agent_name = tool_record.get("agent")
            wf_logger.debug("UI_TOOL_OWNER tool=%s agent=%s", tool_id, agent_name)
    except Exception as e:
        wf_logger.warning("UI_TOOL_OWNER_FAILED tool=%s: %s", tool_id, e)

    if not agent_name and isinstance(payload, dict):
        agent_name = payload.get("agent_name")
    return agent_name


def _resolve_ui_display(
    tool_id: str,
    display: str | None,
    wf_logger,
) -> str:
    resolved_display = display
    if resolved_display is None:
        try:
            tool_record = workflow_manager.get_ui_tool_record(tool_id)
            if tool_record:
                resolved_display = tool_record.get('mode', 'inline')
                wf_logger.debug("UI_TOOL_DISPLAY_RESOLVED tool=%s mode=%s", tool_id, resolved_display)
            else:
                resolved_display = 'inline'
                wf_logger.debug("UI_TOOL_DISPLAY_FALLBACK tool=%s (no record, using inline)", tool_id)
        except Exception as e:
            resolved_display = 'inline'
            wf_logger.warning("UI_TOOL_DISPLAY_FAILED tool=%s: %s (using inline)", tool_id, e)
    return str(resolved_display or "inline")


async def _await_on_transport_loop(transport: Any, coro: Any) -> Any:
    owner_loop = getattr(transport, "_owner_loop", None)
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    if owner_loop is not None and current_loop is not None and owner_loop is not current_loop:
        return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(coro, owner_loop))

    return await coro

async def use_ui_tool(
    tool_id: str,
    payload: dict[str, Any],
    *,
    chat_id: str | None,
    workflow_name: str,
    display: str | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Single-call convenience: emit then wait for a UI tool response.

    Returns the UI response dict augmented with ui_event_id.

    The `display` parameter is now optional and will be auto-resolved from tool
    configuration if not provided. This eliminates redundancy between tool declarations
    and tool function implementations.
    """
    wf_logger = get_workflow_logger(workflow_name=workflow_name, chat_id=chat_id)
    start = _dt.datetime.now(_dt.UTC)
    enriched_payload, tool_record = _enrich_payload_with_ui_contract(tool_id, payload, wf_logger)
    component_name = str((tool_record or {}).get("component") or tool_id).strip() or tool_id
    agent_name = _resolve_ui_tool_owner(tool_id, enriched_payload, wf_logger)
    resolved_display = _resolve_ui_display(tool_id, display, wf_logger)
    
    event_id = await _emit_tool_call_core(
        tool_id=tool_id,
        payload=enriched_payload,
        display=resolved_display,
        chat_id=chat_id,
        workflow_name=workflow_name,
        agent_name=agent_name,
        awaiting_response=True,
        component_name=component_name,
    )

    try:
        # Try to log via tools logger (optional import)
        from logs.tools_logs import get_tool_logger as _get_tool_logger  # type: ignore
        from logs.tools_logs import log_tool_event as _log_tool_event
        _tlog = _get_tool_logger(tool_name=tool_id, chat_id=chat_id, workflow_name=workflow_name, ui_event_id=event_id)
        _log_tool_event(_tlog, action="emit_ui", status="done", event_id=event_id, display=resolved_display)
    except Exception:
        pass
    try:
        # Ignore the timeout parameter to prevent user feedback from timing out; wait until the UI responds
        resp = await _wait_for_tool_call_response_internal(event_id, timeout=None)
        duration_ms = (_dt.datetime.now(_dt.UTC) - start).total_seconds() * 1000.0
        # Assemble log message to keep line length under linter limits
        wf_logger.debug("UI_TOOL_ROUNDTRIP tool=%s event=%s duration_ms=%.2f", tool_id, event_id, duration_ms)
        if isinstance(resp, dict):
            # Attribution belongs to the emitted event, never a browser field.
            resp['ui_event_id'] = event_id
        try:
            from logs.tools_logs import get_tool_logger as _get_tool_logger  # type: ignore
            from logs.tools_logs import log_tool_event as _log_tool_event
            _tlog = _get_tool_logger(tool_name=tool_id, chat_id=chat_id, workflow_name=workflow_name, ui_event_id=event_id)
            _log_tool_event(_tlog, action="ui_response", status=str(resp.get('status', 'unknown')), event_id=event_id)
        except Exception:
            pass

        # Inline components should collapse cleanly after completion.
        if resolved_display == "inline" and chat_id:
            try:
                from mozaiksai.core.transport.simple_transport import SimpleTransport

                transport = await SimpleTransport.get_instance()
                completion_event = {
                    "schema_version": "mozaiks.ui.event.v1",
                    "type": "chat.tool_call_complete",
                    "data": {
                        "tool_call_id": event_id,
                        "tool_name": tool_id,
                        "display": "inline",
                        "status": resp.get("status", "completed"),
                        "summary": f"{tool_id} completed",
                    },
                    "timestamp": _dt.datetime.now(_dt.UTC).isoformat(),
                }
                await transport.send_event_to_ui(completion_event, chat_id=chat_id)
                wf_logger.debug("UI_TOOL_COMPLETION_SENT event=%s", event_id)
            except Exception as vanish_err:
                wf_logger.warning("UI_TOOL_COMPLETION_SEND_FAILED event=%s: %s", event_id, vanish_err)

            try:
                from mozaiksai.core.transport.simple_transport import SimpleTransport

                transport = await SimpleTransport.get_instance()
                pm = None
                app_id = None
                if transport is not None and hasattr(transport, "_get_or_create_persistence_manager"):
                    pm = transport._get_or_create_persistence_manager()  # type: ignore[attr-defined]
                    if hasattr(transport, "connections"):
                        conn = transport.connections.get(chat_id) or {}  # type: ignore[attr-defined]
                        app_id = conn.get("app_id")
                if pm and app_id:
                    await _await_on_transport_loop(
                        transport,
                        pm.update_tool_call_state(
                            chat_id=chat_id,
                            app_id=str(app_id),
                            event_id=event_id,
                            status=resp.get("status", "completed"),
                            completed=True,
                        ),
                    )
                    wf_logger.debug("UI_TOOL_STATUS_UPDATED tool=%s event=%s", tool_id, event_id)
            except Exception as persist_err:
                wf_logger.warning("UI_TOOL_PERSIST_FAILED: %s", persist_err)

        return resp
    except Exception as e:
        duration_ms = (_dt.datetime.now(_dt.UTC) - start).total_seconds() * 1000.0
        wf_logger.error("UI_TOOL_ROUNDTRIP_FAILED tool=%s event=%s duration_ms=%.2f: %s", tool_id, event_id, duration_ms, e, exc_info=True)
        raise


async def emit_ui_surface(
    tool_id: str,
    payload: dict[str, Any],
    *,
    chat_id: str | None,
    workflow_name: str,
    display: str | None = None,
    agent_name: str | None = None,
    require_persistence: bool = False,
) -> str:
    """Emit a one-way UI surface without awaiting a response.

    Use this for read-only artifacts or status surfaces where the frontend should
    render the component but no user response is required.

    Set ``require_persistence=True`` for an artifact emitted by an active
    workflow tool. The returned event ID then confirms an acknowledged,
    app/chat-scoped save to the canonical last-artifact projection. A failed
    save raises ``UIToolError`` before delivery. This is not a browser render
    acknowledgement: a disconnected viewer can restore the saved artifact.
    Default emission remains best effort.
    """
    wf_logger = get_workflow_logger(workflow_name=workflow_name, chat_id=chat_id)
    enriched_payload, tool_record = _enrich_payload_with_ui_contract(tool_id, payload, wf_logger)
    component_name = str((tool_record or {}).get("component") or tool_id).strip() or tool_id
    resolved_agent_name = agent_name or _resolve_ui_tool_owner(tool_id, enriched_payload, wf_logger)
    resolved_display = _resolve_ui_display(tool_id, display, wf_logger)

    event_id = await _emit_tool_call_core(
        tool_id=tool_id,
        payload=enriched_payload,
        display=resolved_display,
        chat_id=chat_id,
        workflow_name=workflow_name,
        agent_name=resolved_agent_name,
        awaiting_response=False,
        component_name=component_name,
        require_persistence=require_persistence,
    )

    try:
        from logs.tools_logs import get_tool_logger as _get_tool_logger  # type: ignore
        from logs.tools_logs import log_tool_event as _log_tool_event

        _tlog = _get_tool_logger(
            tool_name=tool_id,
            chat_id=chat_id,
            workflow_name=workflow_name,
            ui_event_id=event_id,
        )
        _log_tool_event(
            _tlog,
            action="emit_ui_surface",
            status="done",
            event_id=event_id,
            display=resolved_display,
        )
    except Exception:
        pass

    return event_id

async def emit_tool_progress_event(
    tool_name: str,
    *,
    stage_id: str,
    context_variables: Any,
    completed: int | None = None,
    total: int | None = None,
    correlation_id: str | None = None,
) -> None:
    """Report a declared workflow stage to the shared chat status bar.

    Pass the runtime-injected context from a tool or lifecycle hook. Labels
    and units come from ``ui_config.yaml``; never pass model-generated status
    prose or guessed percentages. Omit both counts for indeterminate work.
    A completed stage does not mark the workflow run complete.
    """
    from mozaiksai.core.utils.context_vars import context_get

    scope = {key: context_get(context_variables, key) for key in ("chat_id", "app_id", "workflow_name")}
    if any(not isinstance(value, str) or not value.strip() for value in scope.values()):
        raise ValueError("Workflow progress requires runtime chat_id, app_id, and workflow_name")
    if not isinstance(tool_name, str) or not tool_name.strip():
        raise ValueError("Workflow progress requires a tool_name")
    chat_id = scope["chat_id"]
    workflow_name = scope["workflow_name"]
    config = workflow_manager.get_config(workflow_name)
    stages = (config.get("progress") or {}).get("stages", [])
    stage = next((entry for entry in stages if entry["id"] == stage_id), None)
    if stage is None:
        raise ValueError(f"Workflow {workflow_name!r} has no declared progress stage {stage_id!r}")
    if completed is not None or total is not None:
        if type(completed) is not int or type(total) is not int:
            raise ValueError("Progress completed and total must both be integers or both omitted")
        if total <= 0 or completed < 0 or completed > total:
            raise ValueError("Progress counts require 0 <= completed <= total and total > 0")

    try:
        from mozaiksai.core.transport.simple_transport import SimpleTransport
        transport = await SimpleTransport.get_instance()
    except Exception as e:
        logger.error("[TOOL_PROGRESS] Transport unavailable: %s", e, exc_info=True)
        return

    # A detached/reconnecting run may have no socket. An attached socket must
    # belong to the same runtime scope before any user-visible event is sent.
    connection = transport.connections.get(chat_id)
    if connection is not None and any(connection.get(key) != scope[key] for key in ("app_id", "workflow_name")):
        raise ValueError("Workflow progress does not match the connected chat scope")

    wf_logger = get_workflow_logger(workflow_name, chat_id=chat_id)
    normalized = {
        "kind": "tool_progress",
        "chat_id": chat_id,
        "workflow_name": workflow_name,
        "tool_name": tool_name,
        "stage_id": stage["id"],
        "stage_label": stage["label"],
        "completed": completed,
        "total": total,
        "unit": stage.get("unit"),
        "timestamp": _dt.datetime.now(_dt.UTC).isoformat(),
    }
    if correlation_id:
        normalized["corr"] = correlation_id

    try:
        await transport.send_event_to_ui(normalized, chat_id)
        wf_logger.debug("Tool progress: %s stage=%s completed=%s total=%s", tool_name, stage_id, completed, total)
    except Exception as e:
        wf_logger.error("[TOOL_PROGRESS] Failed to emit progress event: %s", e, exc_info=True)

async def handle_tool_call_for_ui_interaction(tool_call_event: Any, chat_id: str) -> dict[str, Any] | None:
    """
    Handle AG2 tool call events that require UI interaction.
    
    This function:
    1. Uses configuration-driven detection to check if tool requires UI
    2. Emits a UI tool event if needed
    3. Waits for user response
    4. Returns the response for the agent to continue
    
    Args:
        tool_call_event: AG2 FunctionCallEvent or ToolCallEvent
        chat_id: Current chat session ID
        
    Returns:
        User response data if UI interaction occurred, None otherwise
    """
    wf_logger = get_workflow_logger(workflow_name="tool_interaction", chat_id=chat_id)
    
    # Use configuration-driven detection
    requires_ui, tool_config = workflow_manager.detect_ui_tool_call(tool_call_event)

    # Only orchestrator-manage UI emission when explicitly flagged in tool config.
    # By default, UI tools emit their own UI from within the tool function via use_ui_tool().
    if not requires_ui or not tool_config or not tool_config.get('orchestrator_managed', False):
        wf_logger.debug("Skipping orchestrator-managed UI for this tool (handled by tool function or not a UI tool)")
        return None
    
    # Extract tool information
    tool_name = getattr(tool_call_event, "tool_name", None)
    if not isinstance(tool_name, str) or not tool_name:
        tool_name = "unknown_tool"
    
    effective_component = tool_config.get('component') or tool_config.get('component_type') or 'inline'
    wf_logger.debug("UI_TOOL_PROCESS tool=%s component=%s", tool_name, effective_component)
    
    # Extract tool arguments
    content = getattr(tool_call_event, "content", {})
    if hasattr(content, "arguments"):
        tool_args = getattr(content, "arguments", {})
    elif isinstance(content, dict):
        tool_args = content.get("arguments", {}) or content
    else:
        tool_args = {}

    # If no arguments are present, avoid emitting UI at this stage.
    # Many UI tools construct their payload inside the tool function itself.
    if not tool_args:
        wf_logger.debug("UI_TOOL_EMIT_SUPPRESSED tool=%s (no args)", tool_name)
        return None
    
    # Use configuration-driven component type and display mode
    component_type = effective_component
    display_mode = tool_config.get('mode', 'inline')
    tool_call_component = tool_config.get('tool_id', str(tool_name))
    
    # Prepare UI tool payload
    payload = {
    "tool_name": tool_name,
        "tool_args": tool_args,
        "component_type": component_type,
        "interaction_type": "input",
        "agent_name": getattr(tool_call_event, "sender", None) or getattr(tool_call_event, "agent_name", None),
        "workflow_name": tool_config.get('workflow_name', 'unknown'),
    }
    
    try:
        event_id = await _emit_tool_call_core(
            tool_id=tool_call_component,
            payload=payload,
            display=display_mode,
            chat_id=chat_id,
            workflow_name=tool_config.get('workflow_name', 'tool_interaction')
        )
        wf_logger.debug("UI_TOOL_AWAIT_RESPONSE tool_call=%s", tool_call_component)

        # Wait for user response using internal primitive
        response = await _wait_for_tool_call_response_internal(event_id)
        wf_logger.debug("UI_TOOL_RESPONSE_RECEIVED tool_call=%s", tool_call_component)

        if isinstance(response, dict) and 'ui_event_id' not in response:
            response['ui_event_id'] = event_id
        return response

    except Exception as e:  # pragma: no cover
        wf_logger.error("UI_TOOL_INTERACTION_FAILED tool=%s: %s", tool_name, e, exc_info=True)
        raise UIToolError(f"UI interaction failed: {str(e)}") from e
    
__all__ = [
    "emit_ui_surface",
    "use_ui_tool",
    "handle_tool_call_for_ui_interaction",
    "emit_tool_progress_event",
    "InputTimeoutEvent",
    "UIToolError",
]
