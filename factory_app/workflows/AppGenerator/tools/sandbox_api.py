
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, WebSocket
from pydantic import BaseModel, Field

from mozaiksai.core.auth import (
    WS_CLOSE_POLICY_VIOLATION,
    require_user_scope,
)


def get_router() -> APIRouter:
    router = APIRouter()

    # Cached resolved manager + validators (per-process)
    _mgr = None
    _is_valid_artifact_id = None
    _is_valid_sandbox_id = None

    def _get_sandbox_api():
        nonlocal _mgr, _is_valid_artifact_id, _is_valid_sandbox_id

        if _mgr is not None:
            return _mgr, _is_valid_artifact_id, _is_valid_sandbox_id

        try:
            from .sandbox_manager import ArtifactSandboxManager, is_valid_artifact_id, is_valid_sandbox_id
        except Exception as exc:
            raise RuntimeError(
                "Artifact sandbox feature unavailable. Install e2b-code-interpreter and its dependencies to enable it."
            ) from exc

        _mgr = ArtifactSandboxManager()
        _is_valid_artifact_id = is_valid_artifact_id
        _is_valid_sandbox_id = is_valid_sandbox_id
        return _mgr, _is_valid_artifact_id, _is_valid_sandbox_id

    class _SandboxCreateResponse(BaseModel):
        sandboxId: str

    class _SyncFile(BaseModel):
        path: str
        content: str

    class _SyncRequest(BaseModel):
        files: List[_SyncFile] = Field(default_factory=list)
        deleted: List[str] = Field(default_factory=list)

    class _OkResponse(BaseModel):
        ok: bool = True

    class _StartResponse(BaseModel):
        status: str
        previewUrl: Optional[str] = None
        message: Optional[str] = None

    class _StatusResponse(BaseModel):
        status: str
        previewUrl: Optional[str] = None
        lastError: Optional[str] = None

    @router.post("/api/artifacts/{artifactId}/sandbox", response_model=_SandboxCreateResponse)
    async def artifacts_create_or_reuse_sandbox(
        artifactId: str,
        _principal=Depends(require_user_scope),
    ):
        try:
            mgr, is_valid_artifact_id, _is_valid_sandbox_id2 = _get_sandbox_api()
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        if not is_valid_artifact_id(artifactId):
            raise HTTPException(status_code=400, detail="Invalid artifactId")
        try:
            st = await mgr.create_or_reuse(artifactId)
            return {"sandboxId": st.sandbox_id}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @router.post("/api/sandbox/{sandboxId}/sync", response_model=_OkResponse)
    async def sandbox_sync_files(
        sandboxId: str,
        req: _SyncRequest,
        _principal=Depends(require_user_scope),
    ):
        try:
            mgr, _is_valid_artifact_id2, is_valid_sandbox_id = _get_sandbox_api()
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        if not is_valid_sandbox_id(sandboxId):
            raise HTTPException(status_code=400, detail="Invalid sandboxId")
        try:
            await mgr.sync(
                sandboxId,
                files=[f.model_dump() for f in (req.files or [])],
                deleted=req.deleted or [],
            )
            return {"ok": True}
        except KeyError:
            raise HTTPException(status_code=404, detail="Sandbox not found")
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @router.post("/api/sandbox/{sandboxId}/start", response_model=_StartResponse)
    async def sandbox_start_app(
        sandboxId: str,
        _principal=Depends(require_user_scope),
    ):
        try:
            mgr, _is_valid_artifact_id2, is_valid_sandbox_id = _get_sandbox_api()
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        if not is_valid_sandbox_id(sandboxId):
            raise HTTPException(status_code=400, detail="Invalid sandboxId")
        try:
            st = await mgr.start(sandboxId)
            msg = st.last_error if st.status == "error" else None
            return {"status": st.status, "previewUrl": st.preview_url, "message": msg}
        except KeyError:
            raise HTTPException(status_code=404, detail="Sandbox not found")
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @router.get("/api/sandbox/{sandboxId}/status", response_model=_StatusResponse)
    async def sandbox_status(
        sandboxId: str,
        _principal=Depends(require_user_scope),
    ):
        try:
            mgr, _is_valid_artifact_id2, is_valid_sandbox_id = _get_sandbox_api()
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        if not is_valid_sandbox_id(sandboxId):
            raise HTTPException(status_code=400, detail="Invalid sandboxId")
        try:
            st = await mgr.status(sandboxId)
            return {"status": st.status, "previewUrl": st.preview_url, "lastError": st.last_error}
        except KeyError:
            raise HTTPException(status_code=404, detail="Sandbox not found")
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @router.post("/api/sandbox/{sandboxId}/stop", response_model=_OkResponse)
    async def sandbox_stop(
        sandboxId: str,
        _principal=Depends(require_user_scope),
    ):
        try:
            mgr, _is_valid_artifact_id2, is_valid_sandbox_id = _get_sandbox_api()
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        if not is_valid_sandbox_id(sandboxId):
            raise HTTPException(status_code=400, detail="Invalid sandboxId")
        try:
            await mgr.stop(sandboxId)
            return {"ok": True}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @router.websocket("/ws/sandbox/{sandboxId}")
    async def ws_sandbox_stream(websocket: WebSocket, sandboxId: str):
        try:
            mgr, _is_valid_artifact_id2, is_valid_sandbox_id = _get_sandbox_api()
        except Exception as exc:
            await websocket.close(code=WS_CLOSE_POLICY_VIOLATION, reason=str(exc))
            return
        if not is_valid_sandbox_id(sandboxId):
            await websocket.close(code=WS_CLOSE_POLICY_VIOLATION, reason="Invalid sandbox ID")
            return

        # Authenticate WebSocket connection
        from mozaiksai.core.auth import authenticate_websocket

        ws_user = await authenticate_websocket(websocket)
        if ws_user is None:
            return  # Connection already closed with 1008

        await websocket.accept()
        await mgr.register_ws(sandboxId, websocket)
        try:
            while True:
                await websocket.receive_text()
        except Exception:
            pass
        finally:
            await mgr.unregister_ws(sandboxId, websocket)

    return router

