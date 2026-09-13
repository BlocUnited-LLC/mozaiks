"""Review an exact workflow partition through the runtime's correlated UI tool."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, StrictBool, StrictStr

from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.outputs.structured import load_workflow_structured_outputs
from mozaiksai.core.workflow.ui_tools import use_ui_tool

from .pattern_selection import validate_selection


class WorkflowPlanReviewResponse(BaseModel):
    action: Literal["approve", "request_changes", "cancel"]
    approved: StrictBool
    review_id: StrictStr
    rationale: StrictStr = Field(default="", max_length=4000)


def _fingerprint(selection: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(selection, sort_keys=True).encode()).hexdigest()


async def mermaid_sequence_diagram(
    *,
    MermaidSequenceDiagram: dict[str, Any] | None = None,
    context_variables: Any | None = None,
) -> dict[str, Any]:
    if context_variables is None:
        raise ValueError("Workflow plan review requires runtime context")
    selection = validate_selection(context_variables.get("PatternSelection"), context_variables)
    models, _ = load_workflow_structured_outputs("AgentGenerator")
    raw = MermaidSequenceDiagram
    if raw is None:
        output = detach(context_variables.get("structured_output"))
        raw = output.get("MermaidSequenceDiagram") if isinstance(output, dict) else None
    diagram = models["MermaidSequenceDiagram"].model_validate(detach(raw)).model_dump(mode="json")
    review_id = f"workflow_review_{uuid4().hex}"
    fingerprint = _fingerprint(selection)
    review = {"review_id": review_id, "selection_hash": fingerprint, "status": "pending"}
    context_variables.set("workflow_plan_review", review)
    context_variables.set("workflow_review_feedback", "")
    workflows = selection["workflows"]
    response = WorkflowPlanReviewResponse.model_validate(await use_ui_tool(
        tool_id="WorkflowPlanReview",
        payload={
            **diagram,
            "review_id": review_id,
            "title": selection["pack_name"],
            "summary": selection["pack_partition_reason"] or "Review the workflows to generate.",
            "workflow_count": len(workflows),
            "checkpoints": [f"{item['name']}: {item['description']}" for item in workflows]
            or ["No AI workflows. App modules and pages provide the requested functionality."],
        },
        chat_id=context_variables.get("chat_id"),
        workflow_name=context_variables.get("workflow_name") or "AgentGenerator",
    ))
    current = validate_selection(context_variables.get("PatternSelection"), context_variables)
    current_review = detach(context_variables.get("workflow_plan_review"))
    if current_review != review or response.review_id != review_id or _fingerprint(current) != fingerprint:
        raise ValueError("Workflow approval does not match the current draft")
    if response.approved is not (response.action == "approve"):
        raise ValueError("Workflow review action and approval disagree")
    outcome = {"approve": "approved", "request_changes": "changes_requested", "cancel": "cancelled"}[response.action]
    context_variables.set("workflow_review_feedback", response.rationale)
    context_variables.set("workflow_plan_review", {**review, "status": outcome, "rationale": response.rationale})
    if outcome == "approved" and not workflows:
        from factory_app.workflows._shared.platform.build_target import require_build_binding

        from .generate_and_download import _record_context_and_artifacts

        binding = require_build_binding(context_variables)
        await _record_context_and_artifacts(
            app_id=binding.target_app_id,
            user_id=context_variables.get("user_id"),
            chat_id=context_variables.get("chat_id"),
            pack_name=selection["pack_name"],
            bundle_entries=[],
            zip_path=None,
            context_variables=context_variables,
        )
        outcome = "no_workflows"
    return {"outcome": outcome, "review_id": review_id, "workflow_count": len(workflows)}
