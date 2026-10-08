"""Human outcome feedback over the existing authenticated workflow UI transport.

This is a collection contract, not framework telemetry or an evaluation engine.
Applications own persistence, access, retention, and interpretation of evidence.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

_Identifier = Annotated[str, Field(min_length=1, max_length=256, pattern=r"^\S+$")]


class WorkflowFeedbackResponse(BaseModel):
    """The only values a responding browser may supply."""

    model_config = ConfigDict(extra="forbid", strict=True)

    status: Literal["submitted", "skipped"]
    rating: Annotated[StrictInt, Field(ge=1, le=5)] | None = None
    helpful: StrictBool | None = None
    outcome: Literal["succeeded", "partial", "failed", "unknown"] | None = None

    @model_validator(mode="after")
    def check_answers(self) -> WorkflowFeedbackResponse:
        answered = any(value is not None for value in (self.rating, self.helpful, self.outcome))
        if self.status == "submitted" and not answered:
            raise ValueError("Submitted feedback requires at least one answer")
        if self.status == "skipped" and answered:
            raise ValueError("Skipped feedback cannot contain answers")
        return self


class WorkflowFeedbackEvidence(BaseModel):
    """Private app evidence; callers must independently authorize its origin."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: Literal["mozaiks.workflow_feedback.v1"] = "mozaiks.workflow_feedback.v1"
    evidence_kind: Literal["human_feedback"] = "human_feedback"
    app_id: _Identifier
    chat_id: _Identifier
    workflow_name: _Identifier
    agent_name: _Identifier
    outcome_id: _Identifier
    user_id: _Identifier
    ui_event_id: _Identifier
    observed_at: str
    response: WorkflowFeedbackResponse


class WorkflowFeedbackRenderReceipt(BaseModel):
    """Server-attributed client report that the invitation was visible."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: Literal["mozaiks.workflow_feedback_render.v1"] = "mozaiks.workflow_feedback_render.v1"
    evidence_kind: Literal["client_visible_ack"] = "client_visible_ack"
    app_id: _Identifier
    chat_id: _Identifier
    workflow_name: _Identifier
    agent_name: _Identifier
    outcome_id: _Identifier
    user_id: _Identifier
    ui_event_id: _Identifier
    observed_at: str


async def collect_workflow_feedback(
    context_variables: Any,
    *,
    tool_id: str,
    agent_name: str,
    outcome_id: str,
) -> WorkflowFeedbackEvidence:
    """Collect optional feedback after delivering an identifiable result.

    Invoke only from a registered workflow tool, with runtime-injected context.
    ``agent_name`` identifies the declared producer of the rated result;
    ``outcome_id`` is its stable server-owned artifact/action identifier.
    Neither value may be accepted from the rating request. Persist the result
    through an app-owned declared module action, never framework telemetry.
    """
    from mozaiksai.core.workflow.agents.factory import active_workflow_tool_run
    from mozaiksai.core.workflow.ui_tools import use_ui_tool

    workflow_name, app_id, chat_id, user_id = active_workflow_tool_run()
    scope = dict(app_id=app_id, chat_id=chat_id, workflow_name=workflow_name, user_id=user_id)
    if any(context_variables.get(key) != value for key, value in scope.items()):
        raise PermissionError("workflow_feedback_scope_mismatch")
    # Validate trusted attribution before emitting an interaction. The temporary
    # event identifier is never returned; the transport supplies the real one.
    subject = WorkflowFeedbackEvidence(
        app_id=app_id, chat_id=chat_id, workflow_name=workflow_name, user_id=user_id,
        agent_name=agent_name, outcome_id=outcome_id,
        ui_event_id="pending", observed_at=datetime.now(UTC).isoformat(),
        response=WorkflowFeedbackResponse(status="skipped"),
    )
    raw = await use_ui_tool(
        tool_id,
        {"outcome_id": subject.outcome_id, "agent_name": subject.agent_name},
        chat_id=subject.chat_id,
        workflow_name=subject.workflow_name,
    )
    response_payload = dict(raw)
    event_id = response_payload.pop("ui_event_id", None)
    WorkflowFeedbackResponse.model_validate(response_payload)
    if active_workflow_tool_run() != (workflow_name, app_id, chat_id, user_id):
        raise PermissionError("workflow_feedback_scope_mismatch")
    evidence = await resolve_workflow_feedback(
        app_id=app_id, chat_id=chat_id, user_id=user_id,
        ui_event_id=event_id, outcome_id=subject.outcome_id,
    )
    if evidence is None or evidence.agent_name != subject.agent_name or evidence.workflow_name != workflow_name:
        raise PermissionError("authenticated_workflow_feedback_receipt_missing")
    return evidence


async def resolve_workflow_feedback(
    *, app_id: str, chat_id: str, user_id: str, ui_event_id: str, outcome_id: str,
) -> WorkflowFeedbackEvidence | None:
    """Resolve a persisted UI receipt within a caller-authorized session scope.

    A sink derives app/user from its authenticated context and chat_id from
    workflow dispatch provenance. It must not forward client-supplied scope.
    Missing or mismatched evidence returns None; database failure propagates.
    """
    from mozaiksai.core.data.persistence import AG2PersistenceManager

    receipt = await AG2PersistenceManager().get_workflow_feedback_receipt(
        app_id=app_id, chat_id=chat_id, user_id=user_id, ui_event_id=ui_event_id,
    )
    if receipt is None:
        return None
    evidence = WorkflowFeedbackEvidence.model_validate(receipt)
    if (
        evidence.app_id != app_id or evidence.chat_id != chat_id or evidence.user_id != user_id
        or evidence.ui_event_id != ui_event_id or evidence.outcome_id != outcome_id
    ):
        return None
    return evidence


__all__ = [
    "WorkflowFeedbackResponse", "WorkflowFeedbackEvidence", "WorkflowFeedbackRenderReceipt",
    "collect_workflow_feedback",
    "resolve_workflow_feedback",
]
