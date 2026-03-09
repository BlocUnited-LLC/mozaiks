# ==============================================================================
# FILE: core/workflow/ui_tools.py
# DESCRIPTION: Centralized helper utilities for agent-driven UI interactions.
#   - UI tool emission + response handling
#   - (Normalization & event translation removed; orchestration handles streaming payloads)
# ==============================================================================

from __future__ import annotations
import uuid
import logging
from typing import Dict, Any, Optional
import datetime as _dt

from logs.logging_config import get_workflow_logger
from mozaiksai.core.workflow.workflow_manager import workflow_manager
logger = logging.getLogger(__name__)


class UIToolError(Exception):
    """Custom exception for UI tool errors."""
    pass

async def _emit_ui_tool_event_core(
    tool_id: str,
    payload: Dict[str, Any],
    display: str = "inline",
    chat_id: Optional[str] = None,
    workflow_name: str = "unknown",
    agent_name: Optional[str] = None
) -> str:
    """
    Core function to emit a UI tool event to the frontend.

    This function is the standardized way for any agent tool to request
    that a UI component be rendered.

    Args:
        tool_id: The unique identifier for the UI component (e.g., "agent_api_key_input").
        payload: The data required by the UI component (props).
        display: How the component should be displayed ("inline" or "artifact").
        chat_id: The ID of the chat session to send the event to.
        workflow_name: The name of the workflow emitting the event.

    Returns:
        The unique event ID for this interaction.
    """
    event_id = f"{tool_id}_{str(uuid.uuid4())[:8]}"
    wf_logger = get_workflow_logger(workflow_name=workflow_name, chat_id=chat_id)
    chat_logger = get_workflow_logger("ui_tools", chat_id=chat_id)
    
    try:
        from mozaiksai.core.transport.simple_transport import SimpleTransport
        transport = await SimpleTransport.get_instance()
    except Exception as e:
        wf_logger.error(f"❌ [UI_TOOLS] Transport unavailable: {e}")
        raise UIToolError(f"SimpleTransport not available: {e}")

    payload_to_send = {**payload, "workflow_name": workflow_name, "display": display, "mode": payload.get("mode") or display}
    chat_logger.info(
        f"🎯 UI tool event: {tool_id} (event={event_id}, display={display}, payload_keys={list(payload_to_send.keys())[:12]})"
    )
    
    # Extract agent_name from payload if not explicitly provided
    if not agent_name and isinstance(payload_to_send, dict):
        agent_name = payload_to_send.get("agent_name")
    
    try:
        await transport.send_ui_tool_event(
            event_id=event_id,
            chat_id=chat_id,
            tool_name=tool_id,  # use actual tool id
            component_name=tool_id,
            display_type=display,
            payload=payload_to_send,
            agent_name=agent_name,
        )
        wf_logger.info(f"✅ [UI_TOOLS] Emitted UI tool event: {event_id}")
        return event_id
    except Exception as e:
        wf_logger.error(
            f"❌ [UI_TOOLS] Failed to emit UI tool event '{event_id}': {e}",
            exc_info=True,
        )
        raise UIToolError(f"Failed to emit UI tool event: {e}")

async def _wait_for_ui_tool_response_internal(event_id: str, timeout: Optional[float] = None) -> Dict[str, Any]:
    from mozaiksai.core.transport.simple_transport import SimpleTransport  # local import
    transport = await SimpleTransport.get_instance()
    try:
        # Always wait indefinitely for user/UI response; ignore provided timeout to avoid premature cancellations.
        # Explicitly pass timeout=None to ensure no default timeout is applied in the transport layer.
        fut = transport.wait_for_ui_tool_response(event_id, timeout=None)  # type: ignore[attr-defined]
        return await fut
    except Exception as e:  # pragma: no cover
        raise UIToolError(f"UI tool response failure for {event_id}: {e}")

async def use_ui_tool(
    tool_id: str,
    payload: Dict[str, Any],
    *,
    chat_id: Optional[str],
    workflow_name: str,
    display: Optional[str] = None,
    timeout: float | None = None,
) -> Dict[str, Any]:
    """Single-call convenience: emit then wait for a UI tool response.

    Returns the UI response dict augmented with ui_event_id.
    
    The `display` parameter is now optional and will be auto-resolved from tools.json
    configuration if not provided. This eliminates redundancy between tool declarations
    and tool function implementations.
    """
    wf_logger = get_workflow_logger(workflow_name=workflow_name, chat_id=chat_id)
    start = _dt.datetime.now(_dt.timezone.utc)
    
    # Get the ACTUAL agent owner from tools.json (not from conversation context)
    agent_name = None
    try:
        tool_record = workflow_manager.get_ui_tool_record(tool_id)
        if tool_record:
            agent_name = tool_record.get('agent')  # This is the correct tool owner!
            wf_logger.debug(f"🔍 Tool '{tool_id}' owned by agent: {agent_name}")
    except Exception as e:
        wf_logger.warning(f"⚠️ Failed to resolve tool owner for '{tool_id}': {e}")
    
    # Fallback: Extract from payload if not found in registry
    if not agent_name and isinstance(payload, dict):
        agent_name = payload.get("agent_name")
    
    # Auto-resolve display mode from workflow_manager if not explicitly provided
    resolved_display = display
    if resolved_display is None:
        try:
            tool_record = workflow_manager.get_ui_tool_record(tool_id)
            if tool_record:
                resolved_display = tool_record.get('mode', 'inline')
                wf_logger.debug(f"🔍 Auto-resolved display mode for '{tool_id}': {resolved_display}")
            else:
                resolved_display = 'inline'  # fallback default
                wf_logger.debug(f"⚠️ No tool record found for '{tool_id}', using default display: inline")
        except Exception as e:
            resolved_display = 'inline'  # fallback on error
            wf_logger.warning(f"⚠️ Failed to resolve display mode for '{tool_id}': {e}, using default: inline")
    
    event_id = await _emit_ui_tool_event_core(
        tool_id=tool_id,
        payload=payload,
        display=resolved_display,
        chat_id=chat_id,
        workflow_name=workflow_name,
        agent_name=agent_name,
    )
    
    # Persist UI tool metadata to enable state restoration on reconnect
    if chat_id:
        try:
            from mozaiksai.core.data.persistence import persistence_manager as pm
            from mozaiksai.core.core_config import get_app_id_from_chat_or_context
            
            app_id = get_app_id_from_chat_or_context(chat_id=chat_id)
            if app_id:
                await pm.attach_ui_tool_metadata(
                    chat_id=chat_id,
                    app_id=app_id,
                    event_id=event_id,
                    metadata={
                        "ui_tool_id": tool_id,
                        "event_id": event_id,
                        "display": resolved_display,
                        "ui_tool_completed": False,
                        "payload": payload,
                        "timestamp": _dt.datetime.now(_dt.timezone.utc).isoformat()
                    }
                )
                wf_logger.debug(f"📌 Attached UI tool metadata for {tool_id} (event={event_id})")
        except Exception as meta_err:
            wf_logger.warning(f"⚠️ Failed to attach UI tool metadata: {meta_err}")
    
    try:
        # Try to log via tools logger (optional import)
        from logs.tools_logs import get_tool_logger as _get_tool_logger, log_tool_event as _log_tool_event  # type: ignore
        _tlog = _get_tool_logger(tool_name=tool_id, chat_id=chat_id, workflow_name=workflow_name, ui_event_id=event_id)
        _log_tool_event(_tlog, action="emit_ui", status="done", event_id=event_id, display=resolved_display)
    except Exception:
        pass
    try:
        # Ignore the timeout parameter to prevent user feedback from timing out; wait until the UI responds
        resp = await _wait_for_ui_tool_response_internal(event_id, timeout=None)
        duration_ms = (_dt.datetime.now(_dt.timezone.utc) - start).total_seconds() * 1000.0
        # Assemble log message to keep line length under linter limits
        round_trip_msg = (
            f"⏱️ [UI_TOOLS] Round-trip tool_id={tool_id} "
            f"event={event_id} duration_ms={duration_ms:.2f}"
        )
        wf_logger.info(round_trip_msg)
        if isinstance(resp, dict) and 'ui_event_id' not in resp:
            resp['ui_event_id'] = event_id
        try:
            from logs.tools_logs import get_tool_logger as _get_tool_logger, log_tool_event as _log_tool_event  # type: ignore
            _tlog = _get_tool_logger(tool_name=tool_id, chat_id=chat_id, workflow_name=workflow_name, ui_event_id=event_id)
            _log_tool_event(_tlog, action="ui_response", status=str(resp.get('status', 'unknown')), event_id=event_id)
        except Exception:
            pass
        
        # Auto-vanish inline components after completion
        if resolved_display == 'inline':
            try:
                from mozaiksai.core.transport.simple_transport import SimpleTransport
                transport = await SimpleTransport.get_instance()
                completion_event = {
                    "type": "chat.ui_tool_complete",
                    "data": {
                        "eventId": event_id,
                        "ui_tool_id": tool_id,
                        "display": "inline",
                        "status": resp.get("status", "completed"),
                        "summary": f"{tool_id} completed"
                    },
                    "timestamp": _dt.datetime.now(_dt.timezone.utc).isoformat()
                }
                await transport.send_event_to_ui(completion_event, chat_id=chat_id)
                wf_logger.debug(f"🧹 Sent completion event for inline tool: {event_id}")
            except Exception as vanish_err:
                wf_logger.warning(f"⚠️ Failed to send completion event for {event_id}: {vanish_err}")
            
            # Persist completion state to enable state restoration on reconnect
            if chat_id:
                try:
                    from mozaiksai.core.data.persistence import persistence_manager as pm
                    from mozaiksai.core.core_config import get_app_id_from_chat_or_context
                    
                    app_id = get_app_id_from_chat_or_context(chat_id=chat_id)
                    if app_id:
                        await pm.update_ui_tool_completion(
                            chat_id=chat_id,
                            app_id=app_id,
                            event_id=event_id,
                            completed=True,
                            status=resp.get("status", "completed")
                        )
                        wf_logger.debug(f"✅ Updated UI tool completion status for {tool_id} (event={event_id})")
                except Exception as persist_err:
                    wf_logger.warning(f"⚠️ Failed to persist UI tool completion: {persist_err}")
        
        return resp
    except Exception as e:
        duration_ms = (_dt.datetime.now(_dt.timezone.utc) - start).total_seconds() * 1000.0
        fail_msg = (
            f"❌ [UI_TOOLS] Round-trip failed tool_id={tool_id} "
            f"event={event_id} duration_ms={duration_ms:.2f} err={e}"
        )
        wf_logger.error(fail_msg)
        raise

async def emit_tool_progress_event(
    tool_name: str,
    progress_percent: float,
    status_message: str,
    chat_id: Optional[str] = None,
    correlation_id: Optional[str] = None
) -> None:
    """
    X1: Emit a chat.tool_progress event for long-running tools.
    
    Args:
        tool_name: Name of the tool reporting progress
        progress_percent: Progress as a percentage (0-100)
        status_message: Human-readable status description  
        chat_id: Chat session ID
        correlation_id: Optional correlation ID linking to original tool call
    """
    try:
        from mozaiksai.core.transport.simple_transport import SimpleTransport
        transport = await SimpleTransport.get_instance()
        wf_logger = get_workflow_logger("tool_progress", chat_id=chat_id)
    except Exception as e:
        logger.error(f"❌ [TOOL_PROGRESS] Transport unavailable: {e}")
        return
    
    # Validate progress percentage
    progress_percent = max(0.0, min(100.0, float(progress_percent)))
    
    normalized = {
        "kind": "tool_progress",
        "chat_id": chat_id,
        "tool_name": tool_name,
        "progress_percent": progress_percent,
        "status_message": status_message,
        "timestamp": _dt.datetime.now(_dt.timezone.utc).isoformat()
    }
    if correlation_id:
        normalized["corr"] = correlation_id

    try:
        await transport.send_event_to_ui(normalized, chat_id)
        wf_logger.info(f"📈 Tool progress: {tool_name} at {progress_percent:.1f}% - {status_message}")
    except Exception as e:
        wf_logger.error(f"❌ [TOOL_PROGRESS] Failed to emit progress event: {e}")

async def handle_tool_call_for_ui_interaction(tool_call_event: Any, chat_id: str) -> Optional[Dict[str, Any]]:
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
    requires_ui, tool_config = workflow_manager.detect_ui_tool_event(tool_call_event)

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
    wf_logger.info(f"🎯 Processing UI tool '{tool_name}' with component: {effective_component}")
    
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
        wf_logger.info(f"🔇 Orchestrator UI emission suppressed for '{tool_name}' (no args provided in call)")
        return None
    
    # Use configuration-driven component type and display mode
    component_type = effective_component
    display_mode = tool_config.get('mode', 'inline')
    ui_tool_id = tool_config.get('tool_id', str(tool_name))
    
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
        event_id = await _emit_ui_tool_event_core(
            tool_id=ui_tool_id,
            payload=payload,
            display=display_mode,
            chat_id=chat_id,
            workflow_name=tool_config.get('workflow_name', 'tool_interaction')
        )
        wf_logger.info(f"⏳ Waiting for user interaction on UI tool '{ui_tool_id}'")

        # Wait for user response using internal primitive
        response = await _wait_for_ui_tool_response_internal(event_id)
        wf_logger.info(f"✅ Received user response for tool '{ui_tool_id}'")

        if isinstance(response, dict) and 'ui_event_id' not in response:
            response['ui_event_id'] = event_id
        return response

    except Exception as e:  # pragma: no cover
        wf_logger.error(f"❌ UI tool interaction failed for '{tool_name}': {str(e)}")
        raise UIToolError(f"UI interaction failed: {str(e)}")
    
__all__ = [
    "use_ui_tool",
    "handle_tool_call_for_ui_interaction",
    "emit_tool_progress_event",
    "UIToolError",
]

