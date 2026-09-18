"""Studio-owned artifact previews; all operations require the artifact owner."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from mozaiksai.core.auth import (
    WS_CLOSE_POLICY_VIOLATION,
    UserPrincipal,
    accept_websocket,
    authenticate_websocket,
    require_user_scope,
)
from mozaiksai.core.sandbox import (
    get_artifact_preview_sessions,
    is_valid_artifact_id,
    is_valid_sandbox_id,
)
from mozaiksai.core.sandbox.preview_sessions import PreviewCapacityError

_logger = logging.getLogger(__name__)
_Status = Literal["starting", "running", "error"]


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
    status: _Status
    previewUrl: str | None = None
    message: str | None = None


class _StatusResponse(BaseModel):
    status: _Status
    previewUrl: str | None = None
    lastError: str | None = None


def create_sandbox_router(
    *,
    resolve_scope: Callable[[UserPrincipal], tuple[str, str]],
    resolve_artifact: Callable[[UserPrincipal, str, str], Awaitable[tuple[str, dict[str, str | bytes]]]],
) -> APIRouter:
    router = APIRouter()

    async def owned_session(sandboxId: str, principal: UserPrincipal = Depends(require_user_scope)):
        if not is_valid_sandbox_id(sandboxId):
            raise HTTPException(status_code=400, detail="Invalid sandboxId")
        app_id, user_id = resolve_scope(principal)
        manager = get_artifact_preview_sessions()
        try:
            await manager.require_owner(sandboxId, app_id=app_id, user_id=user_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Sandbox not found") from exc
        return manager

    @router.post("/api/artifacts/{artifactId}/sandbox", response_model=_SandboxCreateResponse)
    async def create_preview(
        artifactId: str, build_registry_id: str,
        principal: UserPrincipal = Depends(require_user_scope),
    ):
        if not is_valid_artifact_id(artifactId):
            raise HTTPException(status_code=400, detail="Invalid artifactId")
        app_id, user_id = resolve_scope(principal)
        target_app_id, files = await resolve_artifact(principal, artifactId, build_registry_id)
        manager = get_artifact_preview_sessions()
        try:
            state = await manager.create_or_reuse(
                artifactId, app_id=app_id, user_id=user_id,
                target_app_id=target_app_id, build_registry_id=build_registry_id,
            )
            if not state.last_files:
                try:
                    await manager.sync(state.sandbox_id, files=[{"path": path, "content": content} for path, content in files.items()], deleted=[])
                except Exception:
                    await manager.stop(state.sandbox_id)
                    raise
            return {"sandboxId": state.sandbox_id}
        except PreviewCapacityError as exc:
            raise HTTPException(status_code=429, detail=str(exc), headers={"Retry-After": "15"}) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            # Provider exceptions can contain command arguments or credentials.
            _logger.warning("preview_create_failed artifact=%s exception=%s", artifactId, type(exc).__name__)
            raise HTTPException(status_code=503, detail="Preview sandbox unavailable; check the local provider and configured preview image") from exc

    @router.post("/api/sandbox/{sandboxId}/sync", response_model=_OkResponse)
    async def sync_preview(sandboxId: str, req: _SyncRequest, manager=Depends(owned_session)):
        try:
            await manager.sync(sandboxId, files=[file.model_dump() for file in req.files], deleted=req.deleted)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Sandbox not found") from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Preview file sync failed") from exc
        return {"ok": True}

    @router.post("/api/sandbox/{sandboxId}/start", response_model=_StartResponse)
    async def start_preview(sandboxId: str, manager=Depends(owned_session)):
        try:
            state = await manager.start(sandboxId)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Sandbox not found") from exc
        return {"status": state.status, "previewUrl": state.preview_url, "message": state.last_error}

    @router.get("/api/sandbox/{sandboxId}/status", response_model=_StatusResponse)
    async def preview_status(sandboxId: str, manager=Depends(owned_session)):
        try:
            state = await manager.status(sandboxId)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Sandbox not found") from exc
        return {"status": state.status, "previewUrl": state.preview_url, "lastError": state.last_error}

    @router.post("/api/sandbox/{sandboxId}/stop", response_model=_OkResponse)
    async def stop_preview(sandboxId: str, manager=Depends(owned_session)):
        await manager.stop(sandboxId)
        return {"ok": True}

    @router.websocket("/ws/sandbox/{sandboxId}")
    async def stream_preview(websocket: WebSocket, sandboxId: str):
        if not is_valid_sandbox_id(sandboxId):
            await websocket.close(code=WS_CLOSE_POLICY_VIOLATION, reason="Invalid sandbox ID")
            return
        ws_user = await authenticate_websocket(websocket)
        if ws_user is None:
            return
        manager = get_artifact_preview_sessions()
        try:
            app_id, user_id = resolve_scope(UserPrincipal(**asdict(ws_user)))
            await manager.require_owner(sandboxId, app_id=app_id, user_id=user_id)
        except (KeyError, HTTPException):
            await websocket.close(code=WS_CLOSE_POLICY_VIOLATION, reason="Sandbox not found")
            return
        await accept_websocket(websocket)
        try:
            await manager.register_ws(sandboxId, websocket)
            while True:
                await websocket.receive_text()
        except (WebSocketDisconnect, KeyError):
            pass
        finally:
            await manager.unregister_ws(sandboxId, websocket)

    return router
