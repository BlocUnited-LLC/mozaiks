"""Workflow-local tool that launches the shared create journey."""

from __future__ import annotations

from typing import Annotated, Any, Dict, Optional

from mozaiksai.core.session.launcher import emit_workflow_launch_navigation, launch_transition


_APP_TYPE_OPTIONS = {
    "new": "new_app",
    "new_app": "new_app",
    "new app": "new_app",
    "greenfield": "new_app",
    "from scratch": "new_app",
    "existing": "existing_app",
    "existing_app": "existing_app",
    "existing app": "existing_app",
    "current": "existing_app",
}


async def launch_shared_create(
    app_type: str,
    context_variables: Annotated[Optional[dict[str, Any]], "AG2 context"] = None,
    chat_id: Annotated[Optional[str], "Current chat id"] = None,
) -> dict:
    context = context_variables or {}
    app_id = str(context.get("app_id") or "").strip()
    user_id = str(context.get("user_id") or "").strip()
    source_chat_id = str(chat_id or context.get("chat_id") or "").strip()
    normalized_app_type = _APP_TYPE_OPTIONS.get(str(app_type or "").strip().lower())

    if not normalized_app_type:
        return {
            "success": False,
            "error": "app_type must resolve to 'new_app' or 'existing_app'",
        }

    if not app_id or not user_id:
        return {
            "success": False,
            "error": "Missing app_id or user_id in runtime context",
        }

    launch_result = await launch_transition(
        app_id=app_id,
        user_id=user_id,
        transition_id="app_type_selector",
        option_id=normalized_app_type,
        extra_trigger_meta={
            "source_workflow": "CreateLauncher",
        },
    )

    workflow_launch = launch_result.workflow_launch
    if launch_result.resolution_type != "workflow" or workflow_launch is None:
        return {
            "success": False,
            "error": "Shared create transition did not start a workflow session",
            "resolution_type": launch_result.resolution_type,
        }

    payload: Dict[str, Any] = {
        "success": True,
        "transition_id": launch_result.transition_id,
        "option_id": launch_result.option_id,
        "workflow_id": workflow_launch.workflow_id,
        "requested_workflow_id": workflow_launch.requested_workflow_id,
        "chat_id": workflow_launch.chat_id,
        "websocket_url": workflow_launch.websocket_url,
        "trigger_source": workflow_launch.trigger_source,
        "routing_explanation": workflow_launch.routing_explanation,
        "rerouted_by_dependency": workflow_launch.rerouted_by_dependency,
    }
    payload["navigation_requested"] = await emit_workflow_launch_navigation(
        source_chat_id=source_chat_id,
        workflow_launch=workflow_launch,
    )
    context["create_launch_result"] = payload
    return payload