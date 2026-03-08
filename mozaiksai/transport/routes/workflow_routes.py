"""Workflow configuration / discovery routes."""
from __future__ import annotations

import logging
import mimetypes
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from mozaiksai.runtime.auth import UserPrincipal, ServicePrincipal, require_any_auth, require_internal
from mozaiksai.kernel.workflow_manager import (
    workflow_status_summary,
    get_workflow_transport,
    get_workflow_tools,
)
from mozaiksai.runtime.extensions.platform_hooks import get_platform_hooks
from logs.logging_config import get_workflow_logger

logger = logging.getLogger(__name__)
wf_logger = get_workflow_logger("workflow_routes")

router = APIRouter(tags=["workflows"])


@router.get("/api/workflows/{workflow_name}/transport")
async def get_workflow_transport_info(
    workflow_name: str,
    principal: UserPrincipal = Depends(require_any_auth),
):
    """Get transport information for a specific workflow."""
    transport = get_workflow_transport(workflow_name)
    return {
        "workflow_name": workflow_name,
        "transport": transport,
        "endpoints": {
            "websocket": f"/ws/{workflow_name}/{{app_id}}/{{chat_id}}/{{user_id}}",
            "input": "/chat/{{app_id}}/{{chat_id}}/{{user_id}}/input",
        },
    }


@router.get("/api/workflows/{workflow_name}/tools")
async def get_workflow_tools_info(
    workflow_name: str,
    principal: UserPrincipal = Depends(require_any_auth),
):
    """Get UI tools manifest for a specific workflow."""
    tools = get_workflow_tools(workflow_name)
    return {"workflow_name": workflow_name, "tools": tools}


@router.get("/api/workflows/{workflow_name}/ui-tools")
async def get_workflow_ui_tools_manifest(
    workflow_name: str,
    principal: UserPrincipal = Depends(require_any_auth),
):
    """Get UI tools manifest with schemas for frontend development."""
    try:
        from mozaiksai.kernel.workflow_manager import workflow_manager

        ui_tools = workflow_manager.get_workflow_tools(workflow_name)
        manifest = []
        for rec in ui_tools:
            manifest.append(
                {
                    "ui_tool_id": rec.get("tool_id"),
                    "component": rec.get("component"),
                    "mode": rec.get("mode"),
                    "agent": rec.get("agent"),
                    "workflow": workflow_name,
                }
            )
        return {
            "workflow_name": workflow_name,
            "ui_tools_count": len(manifest),
            "ui_tools": manifest,
        }
    except Exception as e:
        logger.error(f"Error getting UI tools manifest for {workflow_name}: {e}")
        return {
            "workflow_name": workflow_name,
            "ui_tools_count": 0,
            "ui_tools": [],
            "error": str(e),
        }


@router.get("/api/workflows")
async def get_workflows(
    principal: UserPrincipal = Depends(require_any_auth),
):
    """Get all workflows for frontend (alias for /api/workflows/config)."""
    try:
        from mozaiksai.kernel.workflow_manager import workflow_manager

        workflow_names = sorted(workflow_manager.get_all_workflow_names())
        ordered_names = get_platform_hooks().call_workflow_ordering(workflow_names)

        configs: dict = {}
        for workflow_name in ordered_names:
            configs[workflow_name] = workflow_manager.get_config(workflow_name)

        wf_logger.info(
            "WORKFLOWS_REQUESTED: Workflows requested by frontend",
            workflow_count=len(configs),
        )
        return configs
    except Exception as e:
        logger.error(f"Failed to get workflows: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve workflows")


@router.get("/api/workflows/config")
async def get_workflow_configs(
    principal: UserPrincipal = Depends(require_any_auth),
):
    """Get all workflow configurations for frontend."""
    try:
        from mozaiksai.kernel.workflow_manager import workflow_manager

        workflow_names = sorted(workflow_manager.get_all_workflow_names())
        ordered_names = get_platform_hooks().call_workflow_ordering(workflow_names)

        configs: dict = {}
        for workflow_name in ordered_names:
            configs[workflow_name] = workflow_manager.get_config(workflow_name)

        wf_logger.info(
            "WORKFLOW_CONFIGS_REQUESTED: Workflow configurations requested by frontend",
            workflow_count=len(configs),
        )
        return configs
    except Exception as e:
        logger.error(f"Failed to get workflow configs: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to retrieve workflow configurations"
        )


@router.get("/api/download/workflow-file")
async def download_workflow_file(
    file_path: str,
    service: ServicePrincipal = Depends(require_internal),
):
    """Download a single workflow file.

    Args:
        file_path: Absolute path to the file to download.
    """
    try:
        if not file_path:
            raise HTTPException(
                status_code=400, detail="file_path query parameter is required"
            )

        file = Path(file_path)
        # Resolve workflows base relative to the project root (two levels up from this file)
        workflows_base = Path(__file__).resolve().parent.parent.parent.parent / "workflows"

        if not file.is_absolute():
            file = workflows_base / file

        try:
            file_resolved = file.resolve()
            workflows_base_resolved = workflows_base.resolve()
            if not str(file_resolved).startswith(str(workflows_base_resolved)):
                raise HTTPException(
                    status_code=403,
                    detail="Access denied: File outside workflow directories",
                )
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(status_code=403, detail="Invalid file path")

        if not file.exists():
            raise HTTPException(status_code=404, detail="File not found")

        if not file.is_file():
            raise HTTPException(status_code=400, detail="Path is not a file")

        mime_type, _ = mimetypes.guess_type(file.name)
        if not mime_type:
            _MIME_MAP = {
                ".json": "application/json",
                ".env": "text/plain",
                ".py": "text/x-python",
                ".js": "text/javascript",
                ".jsx": "text/javascript",
            }
            mime_type = _MIME_MAP.get(file.suffix, "application/octet-stream")

        return FileResponse(
            path=str(file_resolved),
            filename=file.name,
            media_type=mime_type,
            headers={
                "Content-Disposition": f'attachment; filename="{file.name}"',
                "X-Content-Type-Options": "nosniff",
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"File download failed: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to download file: {e}")
