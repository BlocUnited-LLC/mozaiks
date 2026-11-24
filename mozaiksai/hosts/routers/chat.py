"""Chat routes router.

Routes:
    POST /api/chat/upload
    POST /api/chat/upload/{app_id}/{user_id}
    GET  /api/chats/{app_id}/{workflow_name}
    GET  /api/chats/exists/{app_id}/{workflow_name}/{chat_id}
    GET  /api/chats/meta/{app_id}/{workflow_name}/{chat_id}
"""
from __future__ import annotations

import os
from collections import Counter
from typing import Annotated, Any, get_args

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from logs.logging_config import get_workflow_logger
from mozaiksai.core.auth import UserPrincipal, require_user_scope
from mozaiksai.core.auth.dependencies import (
    is_shared_development_identity,
    validate_path_app_id,
    validate_path_chat_id,
    validate_path_id,
    validate_user_id_against_principal,
)
from mozaiksai.core.chat_attachments.attachments import handle_chat_upload
from mozaiksai.core.multitenant import build_app_scope_filter
from mozaiksai.core.runtime.persistence.distributed_lock import (
    ChatLeaseLostError,
    ChatLockAuthorityUnavailableError,
    LockAcquisitionError,
    assert_chat_mutable,
    chat_execution_lease,
)
from mozaiksai.core.session.build_binding import RefinementModel, refinement_model_config
from mozaiksai.core.transport.ui_tools import project_ui_tool_response_state
from mozaiksai.hosts import runtime as runtime_app

router = APIRouter(tags=["chat"])
logger = get_workflow_logger("chat_router")


class WorkflowModelSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str | None = Field(max_length=512)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_ask_carrier_session(session: dict[str, Any] | None) -> bool:
    if not isinstance(session, dict):
        return False
    return str(session.get("transport_purpose") or "").strip().lower() == "ask_carrier"


async def _handle_chat_upload(
    *,
    file: UploadFile,
    app_id: str,
    user_id: str,
    chat_id: str,
    intent: str,
    bundle_path: str | None,
) -> dict[str, Any]:
    if not app_id or not user_id or not chat_id:
        raise HTTPException(status_code=400, detail="app_id, user_id, and chat_id are required")
    validate_path_id(app_id, "app_id")
    validate_path_id(chat_id, "chat_id")

    allowed_raw = os.getenv("CHAT_ATTACHMENTS_ALLOWED_WORKFLOWS", "").strip()
    try:
        coll = await runtime_app._chat_coll()
        res = await handle_chat_upload(
            chat_coll=coll,
            file_obj=file,
            app_id=app_id,
            user_id=user_id,
            chat_id=chat_id,
            intent=intent,
            bundle_path=bundle_path,
            allowed_workflows_env=allowed_raw,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Chat session not found") from exc
    except ValueError as exc:
        message = str(exc)
        if message.startswith("File too large"):
            raise HTTPException(status_code=413, detail=message) from exc
        raise HTTPException(status_code=400, detail=message) from exc
    except Exception as exc:
        logger.exception("UPLOAD_FAILED")
        raise HTTPException(status_code=500, detail="Upload failed") from exc

    try:
        if runtime_app.simple_transport:
            workflow_name = None
            try:
                doc = await coll.find_one(
                    {"_id": chat_id, "user_id": user_id, **build_app_scope_filter(app_id)},
                    {"workflow_name": 1},
                )
                if doc:
                    workflow_name = doc.get("workflow_name")
            except Exception:
                workflow_name = None

            await runtime_app.simple_transport.send_event_to_ui(
                {
                    "kind": "attachment_uploaded",
                    "chat_id": chat_id,
                    "app_id": app_id,
                    "user_id": user_id,
                    "workflow_name": workflow_name,
                    "attachment": res.attachment,
                },
                chat_id,
            )
    except Exception as exc:
        logger.debug("attachment_uploaded WS emit failed for chat %s: %s", chat_id, exc)

    return {
        "success": True,
        "chat_id": chat_id,
        "app_id": app_id,
        "user_id": user_id,
        "attachment": res.attachment,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/api/chat/upload")
async def upload_chat_file(
    request: Request,
    file: UploadFile = File(...),
    appId: str | None = Form(None),
    userId: str = Form(...),
    chatId: str = Form(...),
    intent: str = Form("context"),
    bundle_path: str | None = Form(None),
    principal: UserPrincipal = Depends(require_user_scope),
):
    _ = request
    resolved_app_id = (appId or "").strip()
    if not resolved_app_id:
        raise HTTPException(status_code=400, detail="appId is required")
    user_id = validate_user_id_against_principal(principal, body_user_id=userId)
    return await _handle_chat_upload(
        file=file,
        app_id=resolved_app_id,
        user_id=user_id,
        chat_id=chatId,
        intent=intent,
        bundle_path=bundle_path,
    )


@router.post("/api/chat/upload/{app_id}/{user_id}")
async def upload_chat_file_scoped(
    app_id: str,
    user_id: str,
    file: UploadFile = File(...),
    chatId: str = Form(...),
    intent: str = Form("context"),
    bundle_path: str | None = Form(None),
    principal: UserPrincipal = Depends(require_user_scope),
):
    validate_path_id(app_id, "app_id")
    user_id = validate_user_id_against_principal(principal, path_user_id=user_id)
    return await _handle_chat_upload(
        file=file,
        app_id=app_id,
        user_id=user_id,
        chat_id=chatId,
        intent=intent,
        bundle_path=bundle_path,
    )


@router.get("/api/chats/{app_id}/{workflow_name}")
async def list_chats(
    app_id: str,
    workflow_name: str,
    principal: UserPrincipal = Depends(require_user_scope),
):
    validate_path_id(app_id, "app_id")
    validate_path_id(workflow_name, "workflow_name")
    try:
        coll = await runtime_app._chat_coll()
        query: dict[str, Any] = {"workflow_name": workflow_name, **build_app_scope_filter(app_id)}
        if not is_shared_development_identity(principal):
            query["user_id"] = principal.user_id
        docs = await coll.find(query).sort("created_at", -1).to_list(length=20)
        return {"chat_ids": [doc.get("_id") for doc in docs]}
    except Exception as exc:
        logger.error("Failed to list chats for app %s workflow %s: %s", app_id, workflow_name, exc)
        raise HTTPException(status_code=500, detail="Failed to list chats") from exc


@router.get("/api/chats/exists/{app_id}/{workflow_name}/{chat_id}")
async def chat_exists(
    app_id: str,
    workflow_name: str,
    chat_id: str,
    principal: UserPrincipal = Depends(require_user_scope),
):
    validate_path_id(app_id, "app_id")
    validate_path_id(workflow_name, "workflow_name")
    validate_path_id(chat_id, "chat_id")
    try:
        coll = await runtime_app._chat_coll()
        query: dict[str, Any] = {"_id": chat_id, "workflow_name": workflow_name, **build_app_scope_filter(app_id)}
        if not is_shared_development_identity(principal):
            query["user_id"] = principal.user_id
        doc = await coll.find_one(query, {"_id": 1, "transport_purpose": 1})
        if doc and _is_ask_carrier_session(doc):
            return {"exists": False}
        return {"exists": doc is not None}
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to check chat existence") from exc


@router.get("/api/chats/meta/{app_id}/{workflow_name}/{chat_id}")
async def chat_meta(
    app_id: str,
    workflow_name: str,
    chat_id: str,
    principal: UserPrincipal = Depends(require_user_scope),
):
    validate_path_id(app_id, "app_id")
    validate_path_id(workflow_name, "workflow_name")
    validate_path_id(chat_id, "chat_id")
    try:
        from mozaiksai.core.data.persistence.persistence_manager import extract_last_artifact
        from mozaiksai.core.workflow.workflow_manager import get_workflow_manager

        has_children = False

        coll = await runtime_app._chat_coll()
        projection = {
            "cache_seed": 1,
            "workflow_ui_state.last_artifact": 1,
            "run_build_binding.refinement_model": 1,
            "status": 1,
            "_id": 1,
            "workflow_name": 1,
        }
        failure_key = get_workflow_manager().get_config(workflow_name).get("failure_message_key")
        if isinstance(failure_key, str) and failure_key:
            projection[failure_key] = 1
        query: dict[str, Any] = {"_id": chat_id, "workflow_name": workflow_name, **build_app_scope_filter(app_id)}
        if not is_shared_development_identity(principal):
            query["user_id"] = principal.user_id
        doc = await coll.find_one(query, projection)
        if not doc:
            return {"exists": False}

        binding = doc.get("run_build_binding")
        refinement_model = binding.get("refinement_model") if isinstance(binding, dict) else None

        failure_message = doc.get(failure_key) if isinstance(failure_key, str) and doc.get("status") == 2 else None
        if not isinstance(failure_message, str) or not failure_message.strip():
            failure_message = None

        run_history = await runtime_app.persistence_manager.load_run_history(
            chat_id=chat_id,
            app_id=app_id,
        )
        run_history_count = len(run_history)

        artifact_instance_id = None
        artifact_state = None
        try:
            from mozaiksai.core.workflow import session_manager

            workflow_session = await session_manager.get_workflow_session(chat_id, app_id)
            if workflow_session and workflow_session.get("artifact_instance_id"):
                artifact_instance_id = workflow_session["artifact_instance_id"]
                artifact_doc = await session_manager.get_artifact_instance(artifact_instance_id, app_id)
                if artifact_doc:
                    artifact_state = artifact_doc.get("state")
        except Exception as artifact_err:
            logger.warning("[CHAT_META] Failed to retrieve artifact instance for chat %s: %s", chat_id, artifact_err)

        return {
            "exists": True,
            "chat_id": chat_id,
            "workflow_name": workflow_name,
            "has_children": has_children,
            "cache_seed": doc.get("cache_seed"),
            "status": doc.get("status"),
            "failure_message": failure_message,
            **({"refinement_model": refinement_model} if refinement_model in get_args(RefinementModel) else {}),
            "run_history_count": run_history_count,
            "last_artifact": project_ui_tool_response_state(
                extract_last_artifact(doc), chat_id=chat_id, transport=runtime_app.simple_transport,
            ),
            "artifact_instance_id": artifact_instance_id,
            "artifact_state": artifact_state,
            "app_id": app_id,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to load chat meta") from exc


async def _workflow_model_options(
    app_id: str, workflow_name: str, chat_id: str, principal: UserPrincipal,
    selection: WorkflowModelSelection | None = None,
    user_hint: str | None = None,
) -> dict[str, Any]:
    from mozaiksai.core.workflow.llm_config import workflow_model_catalog

    validate_path_id(app_id, "app_id")
    validate_path_id(workflow_name, "workflow_name")
    validate_path_id(chat_id, "chat_id")
    validate_path_app_id(principal, app_id)
    validate_path_chat_id(principal, chat_id)
    owner = validate_user_id_against_principal(principal, path_user_id=user_hint or principal.user_id)
    scope = {
        "_id": chat_id, **build_app_scope_filter(app_id), "workflow_name": workflow_name,
        "user_id": owner, "transport_purpose": {"$ne": "ask_carrier"},
    }
    coll = await runtime_app._chat_coll()
    doc = await coll.find_one(scope, {"workflow_model": 1, "run_build_binding.refinement_model": 1, "status": 1})
    if doc is None:
        raise HTTPException(404, "Workflow chat not found.")
    bound = (doc.get("run_build_binding") or {}).get("refinement_model")
    if bound is not None:
        if selection is not None:
            raise HTTPException(409, "The model for this build is already set.")
        provider = refinement_model_config(bound)
        model_id = f"{provider['api_type']}:{provider['model']}"
        return {
            "models": [{"id": model_id, "label": provider["model"]}],
            "default": model_id, "selected": model_id, "locked": True,
        }

    default, catalog = await workflow_model_catalog()
    selected = doc.get("workflow_model")
    counts = Counter(entry["model"] for entry in catalog.values())
    options = [
        {"id": key, "label": entry["model"] if counts[entry["model"]] == 1 else f"{entry['model']} ({key.split(':', 1)[0]})"}
        for key, entry in catalog.items()
    ]
    if selection is not None:
        selected = selection.model
        if selected is not None and selected not in catalog:
            raise HTTPException(422, "Choose a model configured for this workflow host.")
        try:
            async with chat_execution_lease(app_id=app_id, chat_id=chat_id):
                from mozaiksai.core.adapters.ag2_knowledge_store import MongoAG2KnowledgeStore
                from mozaiksai.core.adapters.ag2_network_runner import model_change_checkpoint

                # Re-read under the lease, including simultaneous saves from another tab.
                current = await coll.find_one(scope, {"workflow_model": 1, "status": 1, "run_build_binding.refinement_model": 1})
                if current is None:
                    raise HTTPException(404, "Workflow chat not found.")
                if current.get("status") != 0 or (current.get("run_build_binding") or {}).get("refinement_model"):
                    raise HTTPException(409, "This workflow chat's model is fixed.")
                if selected == current.get("workflow_model"):
                    return {"models": options, "default": default, "selected": selected, "locked": False}
                transport = runtime_app.simple_transport
                if transport and transport._input_request_registries.get(chat_id):
                    raise HTTPException(409, "Finish the current workflow interaction before changing models.")
                live = transport.get_live_ag2_workflow_run(chat_id) if transport else None
                checkpoint = await model_change_checkpoint(MongoAG2KnowledgeStore(app_id=app_id, chat_id=chat_id), workflow_name)
                started = await runtime_app.persistence_manager.chat_has_resumable_run(chat_id, app_id, workflow_name)
                if (
                    checkpoint != ("idle" if started else "new")
                    or live is not None and (not started or not await live.can_rebind_model())
                ):
                    raise HTTPException(409, "Finish the current workflow interaction before changing models.")
                assert_chat_mutable(app_id=app_id, chat_id=chat_id)
                result = await coll.update_one(
                    {
                        **scope,
                        "status": 0,
                        "run_build_binding.refinement_model": None,
                        "workflow_ui_state.pending_input_request": None,
                    },
                    {"$set": {"workflow_model": selected, "workflow_model_rebind_pending": started}},
                )
                if not result.matched_count:
                    raise HTTPException(409, "Finish the current interaction or reload this workflow chat.")
        except LockAcquisitionError as exc:
            raise HTTPException(409, "Wait for the current workflow turn to finish before changing models.") from exc
        except (ChatLockAuthorityUnavailableError, ChatLeaseLostError) as exc:
            raise HTTPException(503, "Model selection is temporarily unavailable.") from exc
    return {
        "models": options,
        "default": default, "selected": selected, "locked": False,
    }


@router.get("/api/chats/model/{app_id}/{workflow_name}/{chat_id}")
async def get_workflow_model(
    app_id: str, workflow_name: str, chat_id: str, principal: UserPrincipal = Depends(require_user_scope),
    user_hint: Annotated[str | None, Header(alias="X-User-ID")] = None,
):
    return await _workflow_model_options(app_id, workflow_name, chat_id, principal, user_hint=user_hint)


@router.put("/api/chats/model/{app_id}/{workflow_name}/{chat_id}")
async def set_workflow_model(
    app_id: str, workflow_name: str, chat_id: str, selection: WorkflowModelSelection,
    principal: UserPrincipal = Depends(require_user_scope),
    user_hint: Annotated[str | None, Header(alias="X-User-ID")] = None,
):
    return await _workflow_model_options(app_id, workflow_name, chat_id, principal, selection, user_hint)


__all__ = ["router"]
