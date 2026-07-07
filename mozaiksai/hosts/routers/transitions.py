"""Workflow transitions and session-decision router.

Routes:
    GET  /api/transitions/{transition_id}  — fetch a single transition by id
    POST /api/transitions/resolve          — resolve and launch a transition
    GET  /api/session/state                — current session snapshot
    POST /api/session/decisions/pending    — mark a pending harness decision
    POST /api/session/decisions/resolve    — resolve a pending harness decision
"""
from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from mozaiksai.core.auth import UserPrincipal, require_user_scope
from mozaiksai.core.auth.dependencies import resolve_scope_from_principal
from mozaiksai.core.session.launcher import launch_transition

router = APIRouter(tags=["transitions"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_pack_graph_or_404():
    from mozaiksai.core.workflow.pack.config import load_global_pack_graph

    pack = load_global_pack_graph()
    if pack is None:
        raise HTTPException(status_code=404, detail="No extension registry found")
    return pack


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class TransitionResolveRequest(BaseModel):
    transition_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    option_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    journey_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,128}$")
    context_variables: dict[str, Any] = Field(default_factory=dict)
    app_id: str | None = None
    user_id: str | None = None


class PendingDecisionActionPayload(BaseModel):
    action_id: str
    label: str
    action_type: str = "run_workflow"
    workflow_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionPendingDecisionRequest(BaseModel):
    decision_id: str
    decision_type: str
    message: str
    rationale: str
    confidence: float = 0.0
    recommended_workflow_id: str | None = None
    selected_paths: list[str] = Field(default_factory=list)
    clarification_question: str | None = None
    change_request_id: str | None = None
    revision_id: str | None = None
    requires_confirmation: bool = False
    trigger_source: str = "refinement"
    requested_workflow_id: str | None = None
    journey_id: str | None = None
    context_variables: dict[str, Any] = Field(default_factory=dict)
    trigger_payload: dict[str, Any] = Field(default_factory=dict)
    actions: list[PendingDecisionActionPayload] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    workflow_id: str | None = None
    chat_id: str | None = None


class SessionPendingDecisionResolveRequest(BaseModel):
    decision_id: str
    action_id: str | None = None
    accepted: bool = True


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/api/transitions/{transition_id}")
async def get_transition_by_id(transition_id: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", transition_id):
        raise HTTPException(status_code=400, detail="Invalid transition id")

    pack = _load_pack_graph_or_404()
    from mozaiksai.core.workflow.pack.config import get_transition

    transition = get_transition(pack, transition_id)
    if transition is None:
        raise HTTPException(status_code=404, detail=f"Transition '{transition_id}' not found")
    return transition.model_dump(exclude_none=True)


@router.post("/api/transitions/resolve")
async def resolve_transition_route(
    body: TransitionResolveRequest,
    principal: UserPrincipal = Depends(require_user_scope),
):
    from logs.logging_config import get_workflow_logger

    _logger = get_workflow_logger("transitions_router")
    try:
        app_id, user_id = resolve_scope_from_principal(principal, app_id=body.app_id, user_id=body.user_id)
        launch_result = await launch_transition(
            app_id=app_id,
            user_id=user_id,
            transition_id=body.transition_id,
            option_id=body.option_id,
            journey_id=body.journey_id,
            context_variables=body.context_variables or {},
        )
    except ValueError as route_err:
        raise HTTPException(status_code=400, detail=str(route_err)) from route_err
    except Exception as route_err:
        _logger.error("Transition resolution failed: %s", route_err, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to resolve transition") from route_err

    if launch_result.resolution_type == "transition":
        if launch_result.transition is None or not launch_result.next_transition_id:
            raise HTTPException(status_code=500, detail="Transition launch did not return the next transition")
        return {
            "resolution_type": "transition",
            "transition_id": body.transition_id,
            "option_id": launch_result.option_id,
            "journey_id": launch_result.journey_id,
            "next_transition_id": launch_result.next_transition_id,
            "transition": launch_result.transition.model_dump(exclude_none=True),
            "context_variables": launch_result.context_variables,
        }

    workflow_launch = launch_result.workflow_launch
    if workflow_launch is None:
        raise HTTPException(status_code=500, detail="Workflow transition launch did not start a workflow")

    if launch_result.resolution_type == "chat_session":
        return {
            "resolution_type": "chat_session",
            "chat_id": workflow_launch.chat_id,
            "workflow_id": workflow_launch.workflow_id,
            "option_id": launch_result.option_id,
            "journey_id": workflow_launch.journey_id,
            "websocket_url": workflow_launch.websocket_url,
            "context_variables": launch_result.context_variables,
        }

    return {
        "resolution_type": "workflow",
        "chat_id": workflow_launch.chat_id,
        "workflow_id": workflow_launch.workflow_id,
        "option_id": launch_result.option_id,
        "requested_workflow_id": workflow_launch.requested_workflow_id,
        "journey_id": workflow_launch.journey_id,
        "websocket_url": workflow_launch.websocket_url,
        "routing_explanation": workflow_launch.routing_explanation,
        "rerouted_by_dependency": workflow_launch.rerouted_by_dependency,
    }


@router.get("/api/session/state")
async def get_session_state(
    principal: UserPrincipal = Depends(require_user_scope),
):
    from mozaiksai.core.session import get_session_router

    snapshot = await get_session_router().get_session_snapshot(app_id=principal.app_id, user_id=principal.user_id)
    return {"session_state": snapshot}


@router.post("/api/session/decisions/pending")
async def mark_session_pending_decision(
    body: SessionPendingDecisionRequest,
    principal: UserPrincipal = Depends(require_user_scope),
):
    from mozaiksai.core.session import (
        PendingDecisionAction,
        PendingHarnessDecision,
        get_session_router,
    )

    snapshot = await get_session_router().mark_pending_harness_decision(
        app_id=principal.app_id,
        user_id=principal.user_id,
        pending_decision=PendingHarnessDecision(
            decision_id=body.decision_id,
            decision_type=body.decision_type,
            message=body.message,
            rationale=body.rationale,
            confidence=body.confidence,
            recommended_workflow_id=body.recommended_workflow_id,
            selected_paths=list(body.selected_paths or []),
            clarification_question=body.clarification_question,
            change_request_id=body.change_request_id,
            revision_id=body.revision_id,
            requires_confirmation=body.requires_confirmation,
            trigger_source=body.trigger_source,
            requested_workflow_id=body.requested_workflow_id,
            journey_id=body.journey_id,
            context_variables=dict(body.context_variables or {}),
            trigger_payload=dict(body.trigger_payload or {}),
            actions=[
                PendingDecisionAction(
                    action_id=action.action_id,
                    label=action.label,
                    action_type=action.action_type,
                    workflow_id=action.workflow_id,
                    metadata=dict(action.metadata or {}),
                )
                for action in body.actions
            ],
            metadata=dict(body.metadata or {}),
        ),
        workflow_id=body.workflow_id,
        chat_id=body.chat_id,
    )
    return {"session_state": snapshot}


@router.post("/api/session/decisions/resolve")
async def resolve_session_pending_decision(
    body: SessionPendingDecisionResolveRequest,
    principal: UserPrincipal = Depends(require_user_scope),
):
    from mozaiksai.core.session import get_session_router

    snapshot = await get_session_router().resolve_pending_harness_decision(
        app_id=principal.app_id,
        user_id=principal.user_id,
        decision_id=body.decision_id,
        action_id=body.action_id,
        accepted=body.accepted,
    )
    return {"session_state": snapshot}


__all__ = ["router"]
