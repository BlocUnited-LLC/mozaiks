"""Health, metrics, and observability routes."""
from __future__ import annotations

import logging
from datetime import datetime, UTC

from fastapi import APIRouter, Depends, HTTPException, Request

from mozaiksai.runtime.auth import UserPrincipal, require_any_auth
from mozaiksai.engine.capabilities import get_ag2_capability_report
from mozaiksai.runtime.observability.run_registry import get_run_registry_summary
from mozaiksai.runtime.observability.performance_manager import get_performance_manager
from mozaiksai.kernel.workflow_manager import workflow_status_summary, get_workflow_tools
from logs.logging_config import get_workflow_logger

logger = logging.getLogger(__name__)
wf_logger = get_workflow_logger("health_routes")
performance_logger = get_workflow_logger("performance.health_routes")

router = APIRouter(tags=["health"])


@router.get("/health/active-runs")
async def health_active_runs(
    principal: UserPrincipal = Depends(require_any_auth),
):
    """Return summary of active runs (in-memory registry)."""
    try:
        return get_run_registry_summary()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/metrics/perf/aggregate")
async def metrics_perf_aggregate(
    principal: UserPrincipal = Depends(require_any_auth),
):
    """Return aggregate in-memory performance counters (no DB hits)."""
    try:
        perf_mgr = await get_performance_manager()
        return await perf_mgr.aggregate()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to collect aggregate metrics: {e}")


@router.get("/metrics/perf/chats")
async def metrics_perf_chats(
    principal: UserPrincipal = Depends(require_any_auth),
):
    """Return per-chat in-memory performance snapshots."""
    try:
        perf_mgr = await get_performance_manager()
        return await perf_mgr.snapshot_all()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to collect chat metrics: {e}")


@router.get("/metrics/perf/chats/{chat_id}")
async def metrics_perf_chat(
    chat_id: str,
    principal: UserPrincipal = Depends(require_any_auth),
):
    try:
        perf_mgr = await get_performance_manager()
        snap = await perf_mgr.snapshot_chat(chat_id)
        if not snap:
            raise HTTPException(status_code=404, detail="Chat not tracked")
        return snap
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to collect chat metric: {e}")


@router.get("/api/events/metrics")
async def get_event_metrics(
    request: Request,
    principal: UserPrincipal = Depends(require_any_auth),
):
    """Get unified event dispatcher metrics."""
    try:
        dispatcher = request.app.state.event_dispatcher
        metrics = dispatcher.get_metrics()

        return {
            "status": "success",
            "data": metrics,
            "timestamp": datetime.now(UTC).isoformat(),
        }
    except Exception as e:
        logger.error(f"Failed to get event metrics: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve event metrics")


@router.get("/api/health")
async def health_check(
    request: Request,
):
    """Health check endpoint."""
    health_start = datetime.now(UTC)
    try:
        mongo_client = request.app.state.mongo_client
        simple_transport = request.app.state.simple_transport

        mongo_ping_start = datetime.now(UTC)
        if mongo_client is None:
            raise HTTPException(status_code=503, detail="MongoDB client not initialized")
        await mongo_client.admin.command("ping")
        mongo_ping_time = (datetime.now(UTC) - mongo_ping_start).total_seconds() * 1000

        status = workflow_status_summary()
        registered_workflows = status.get("registered_workflows") or []
        if not isinstance(registered_workflows, list):
            registered_workflows = []

        total_tools = 0
        for wf_name in registered_workflows:
            try:
                wf_tools = get_workflow_tools(wf_name)
                if isinstance(wf_tools, list):
                    total_tools += len(wf_tools)
            except Exception:
                continue

        connection_info = {
            "websocket_connections": len(simple_transport.connections) if simple_transport else 0,
            "total_connections": len(simple_transport.connections) if simple_transport else 0,
        }

        health_time = (datetime.now(UTC) - health_start).total_seconds() * 1000
        performance_logger.info(
            "health_check_duration",
            extra={
                "metric_name": "health_check_duration",
                "value": float(health_time),
                "unit": "ms",
                "mongodb_ping_ms": float(mongo_ping_time),
                "active_connections": connection_info["total_connections"],
                "workflows_count": len(registered_workflows),
            },
        )

        health_data = {
            "status": "healthy",
            "mongodb": "connected",
            "mongodb_ping_ms": round(mongo_ping_time, 2),
            "ag2": get_ag2_capability_report(),
            "simple_transport": "initialized" if simple_transport else "not_initialized",
            "active_connections": connection_info,
            "workflows": registered_workflows,
            "transport_groups": status.get("transport_groups", {}),
            "tools_available": total_tools > 0,
            "total_tools": total_tools,
            "health_check_time_ms": round(health_time, 2),
        }

        wf_logger.debug(f"Health check passed - Response time: {health_time:.1f}ms")
        return health_data
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        raise HTTPException(status_code=500, detail=f"Health check failed: {e}")
