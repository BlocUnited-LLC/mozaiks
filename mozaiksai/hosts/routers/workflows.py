"""Workflow catalog router.

Routes:
    GET  /api/workflows         — list all available workflows
    GET  /api/workflows/config  — full workflow config objects
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from mozaiksai.core.auth import UserPrincipal, require_any_auth
from mozaiksai.core.runtime.composition.platform_hooks import get_platform_hooks

router = APIRouter(tags=["workflows"])


@router.get("/api/workflows")
async def get_workflows(
    principal: UserPrincipal = Depends(require_any_auth),
):
    _ = principal
    from mozaiksai.core.workflow.workflow_manager import workflow_manager

    ordered_names = get_platform_hooks().call_workflow_ordering(sorted(workflow_manager.get_all_workflow_names()))
    workflows = []
    for workflow_name in ordered_names:
        config = workflow_manager.get_config(workflow_name)
        startup_mode = str(config.get("workflow_startup_mode") or "").strip() or "AgentDriven"
        structured_outputs = workflow_manager.get_structured_output_registry(workflow_name)
        structured_output_components = {
            agent_name: model_name
            for agent_name, model_name in structured_outputs.items()
            if model_name
        }
        workflows.append({
            "name": workflow_name,
            "display_name": config.get("display_name") or config.get("name") or workflow_name,
            "initial_agent": config.get("initial_agent"),
            "visual_agents": config.get("visual_agents") or [],
            "startup_mode": startup_mode,
            "workflow_startup_mode": startup_mode,
            "structured_outputs": structured_outputs,
            "structured_output_components": structured_output_components,
            "status": "ready",
        })
    return {"workflows": workflows}


@router.get("/api/workflows/config")
async def get_workflow_configs(
    principal: UserPrincipal = Depends(require_any_auth),
):
    _ = principal
    from mozaiksai.core.workflow.workflow_manager import workflow_manager

    ordered_names = get_platform_hooks().call_workflow_ordering(sorted(workflow_manager.get_all_workflow_names()))
    return {workflow_name: workflow_manager.get_config(workflow_name) for workflow_name in ordered_names}


__all__ = ["router"]
