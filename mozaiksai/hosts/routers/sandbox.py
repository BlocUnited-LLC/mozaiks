"""Artifact preview sandbox API.

Studio-facing endpoints that let the AppWorkbench create, sync, start, and
watch an ephemeral preview session for a generated app artifact. Backed by
`mozaiksai.core.sandbox.preview_sessions` over the SandboxPort seam
(e2b or local Docker).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, WebSocket
from pydantic import BaseModel, Field

from mozaiksai.core.auth import (
    WS_CLOSE_POLICY_VIOLATION,
    authenticate_websocket,
    require_user_scope,
)
from mozaiksai.core.sandbox import (
    get_artifact_preview_sessions,
    is_valid_artifact_id,
    is_valid_sandbox_id,
)

_logger = logging.getLogger(__name__)

router = APIRouter()


class _SandboxCreateResponse(BaseModel):
    sandboxId: str


class _SyncFile(BaseModel):
    path: str
    content: str


class _SyncRequest(BaseModel):
    files: list[_SyncFile] = Field(default_factory=list)
    deleted: list[str] = Field(default_factory=list)


class _OkResponse(BaseModel):
    ok: bool = True


class _StartResponse(BaseModel):
    status: str
    previewUrl: str | None = None
    message: str | None = None


class _StatusResponse(BaseModel):
    status: str
    previewUrl: str | None = None
    lastError: str | None = None


@router.post("/api/artifacts/{artifactId}/sandbox", response_model=_SandboxCreateResponse)
async def artifacts_create_or_reuse_sandbox(
    artifactId: str,
    _principal=Depends(require_user_scope),
):
    if not is_valid_artifact_id(artifactId):
        raise HTTPException(status_code=400, detail="Invalid artifactId")
    mgr = get_artifact_preview_sessions()
    try:
        st = await mgr.create_or_reuse(artifactId)
        return {"sandboxId": st.sandbox_id}
    except RuntimeError as exc:
        _logger.warning("sandbox_unavailable: artifactId=%s error=%s", artifactId, exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        _logger.error(
            "sandbox_create_failed: artifactId=%s error=%s", artifactId, exc, exc_info=True
        )
        raise HTTPException(status_code=500, detail="Failed to create sandbox") from exc


@router.post("/api/sandbox/{sandboxId}/sync", response_model=_OkResponse)
async def sandbox_sync_files(
    sandboxId: str,
    req: _SyncRequest,
    _principal=Depends(require_user_scope),
):
    if not is_valid_sandbox_id(sandboxId):
        raise HTTPException(status_code=400, detail="Invalid sandboxId")
    mgr = get_artifact_preview_sessions()
    try:
        await mgr.sync(
            sandboxId,
            files=[f.model_dump() for f in (req.files or [])],
            deleted=req.deleted or [],
        )
        return {"ok": True}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Sandbox not found") from exc
    except Exception as exc:
        _logger.error(
            "sandbox_sync_failed: sandboxId=%s error=%s", sandboxId, exc, exc_info=True
        )
        raise HTTPException(status_code=500, detail="Sandbox sync failed") from exc


@router.post("/api/sandbox/{sandboxId}/start", response_model=_StartResponse)
async def sandbox_start_app(
    sandboxId: str,
    _principal=Depends(require_user_scope),
):
    if not is_valid_sandbox_id(sandboxId):
        raise HTTPException(status_code=400, detail="Invalid sandboxId")
    mgr = get_artifact_preview_sessions()
    try:
        st = await mgr.start(sandboxId)
        msg = st.last_error if st.status == "error" else None
        return {"status": st.status, "previewUrl": st.preview_url, "message": msg}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Sandbox not found") from exc
    except Exception as exc:
        _logger.error(
            "sandbox_start_failed: sandboxId=%s error=%s", sandboxId, exc, exc_info=True
        )
        raise HTTPException(status_code=500, detail="Failed to start sandbox") from exc


@router.get("/api/sandbox/{sandboxId}/status", response_model=_StatusResponse)
async def sandbox_status(
    sandboxId: str,
    _principal=Depends(require_user_scope),
):
    if not is_valid_sandbox_id(sandboxId):
        raise HTTPException(status_code=400, detail="Invalid sandboxId")
    mgr = get_artifact_preview_sessions()
    try:
        st = await mgr.status(sandboxId)
        return {"status": st.status, "previewUrl": st.preview_url, "lastError": st.last_error}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Sandbox not found") from exc
    except Exception as exc:
        _logger.error(
            "sandbox_status_failed: sandboxId=%s error=%s", sandboxId, exc, exc_info=True
        )
        raise HTTPException(status_code=500, detail="Failed to get sandbox status") from exc


@router.post("/api/sandbox/{sandboxId}/stop", response_model=_OkResponse)
async def sandbox_stop(
    sandboxId: str,
    _principal=Depends(require_user_scope),
):
    if not is_valid_sandbox_id(sandboxId):
        raise HTTPException(status_code=400, detail="Invalid sandboxId")
    mgr = get_artifact_preview_sessions()
    try:
        await mgr.stop(sandboxId)
        return {"ok": True}
    except Exception as exc:
        _logger.error(
            "sandbox_stop_failed: sandboxId=%s error=%s", sandboxId, exc, exc_info=True
        )
        raise HTTPException(status_code=500, detail="Failed to stop sandbox") from exc


@router.websocket("/ws/sandbox/{sandboxId}")
async def ws_sandbox_stream(websocket: WebSocket, sandboxId: str):
    if not is_valid_sandbox_id(sandboxId):
        await websocket.close(code=WS_CLOSE_POLICY_VIOLATION, reason="Invalid sandbox ID")
        return

    ws_user = await authenticate_websocket(websocket)
    if ws_user is None:
        return  # Connection already closed with 1008

    mgr = get_artifact_preview_sessions()
    await websocket.accept()
    await mgr.register_ws(sandboxId, websocket)
    try:
        while True:
            await websocket.receive_text()
    except Exception:
        pass
    finally:
        await mgr.unregister_ws(sandboxId, websocket)
