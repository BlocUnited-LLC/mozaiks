"""Validate the workflow partition before dispatch or human review."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any

from factory_app.workflows._shared.workflow_integration import (
    validate_pricing_workflow_features,
    workflow_result_write_plans,
)
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.outputs.structured import load_workflow_structured_outputs


def selection_fingerprint(selection: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(selection, sort_keys=True).encode()).hexdigest()


def validate_selection(raw_selection: Any, context_variables: Any) -> dict[str, Any]:
    models, _ = load_workflow_structured_outputs("AgentGenerator")
    selection = models["PatternSelection"].model_validate(detach(raw_selection)).model_dump(mode="json")
    workflows = selection["workflows"]
    if selection["is_multi_workflow"] != (len(workflows) > 1):
        raise ValueError("is_multi_workflow must match the workflow count")
    if not workflows and not (selection["pack_partition_reason"] or "").strip():
        raise ValueError("An empty workflow partition requires a rationale")
    names = [item["name"] for item in workflows]
    if len(set(names)) != len(names):
        raise ValueError("Workflow names must be unique")
    for item in workflows:
        workflow_result_write_plans(item)
        if item["initial_agent"] != "WorkflowBundleBuilderAgent" or item["pattern_id"] not in range(1, 10):
            raise ValueError("Workflow dispatch must use the supported builder and pattern")
        if set(item.get("depends_on") or []) - (set(names) - {item["name"]}):
            raise ValueError("Workflow dependencies must reference other declared workflows")
    surface_map = detach(context_variables.get("design_surface_map"))
    if surface_map is not None:
        design_models, _ = load_workflow_structured_outputs("DesignDocs")
        surfaces = design_models["DesignSurfaceMap"].model_validate(surface_map).model_dump(mode="json")["surfaces"]
        has_workflows = any(item.get("surface_kind") == "workflow" for item in surfaces)
        if bool(workflows) != has_workflows:
            expected = "at least one declared AI workflow" if has_workflows else "workflows: [] (no AI workflows)"
            raise ValueError(f"The canonical design surface map requires {expected}")
    validate_pricing_workflow_features(context_variables)
    return selection


def pattern_selection(
    *,
    PatternSelection: Annotated[dict[str, Any] | None, "Pattern selection payload"] = None,
    context_variables: Annotated[Any | None, "Runtime context"] = None,
) -> dict[str, Any]:
    if context_variables is None:
        raise ValueError("Pattern selection requires runtime context")
    raw = PatternSelection
    if raw is None:
        output = detach(context_variables.get("structured_output"))
        raw = output.get("PatternSelection") if isinstance(output, dict) else None
    try:
        selection = validate_selection(raw, context_variables)
    except ValueError as error:
        context_variables.set("pattern_selection_feedback", str(error))
        context_variables.set("PatternSelection", None)
        context_variables.set("workflow_plan_review", None)
        return {"outcome": "invalid", "error": str(error)}
    context_variables.set("pattern_selection_feedback", "")
    context_variables.set("PatternSelection", selection)
    for key in ("is_multi_workflow", "pack_name", "pack_partition_reason"):
        context_variables.set(key, selection[key])
    specs = []
    for item in selection["workflows"]:
        plans = item.get("result_write_plans")
        if plans:
            instructions = []
            for plan in plans:
                write = plan["result_write"]
                instructions.append(
                    f"For {plan['event_type']}, the generated prompt and structured output for {plan['agent']} "
                    f"must request exactly these required fields: {', '.join(write['fields'])}. "
                    f"The host supplies the record key {write['record_key']}. "
                    f"{plan['module_id']}.{write['action']} is the receiving app module action. "
                    "Factory installs the workflow adapter that calls it."
                )
            instructions.append(
                "Declare the host-seeded event_payload as object state with model_visible: true and "
                "writer_ids: [transition_router]. Expose it in the result agent's variables. Its prompt "
                "reads the needed fields at event_payload.<field> using the triggering event's declared payload. "
                "Leave the result agent out of generated tool bindings, tool implementations and outcome_plans. "
                "Use tools: [] when no other agent needs a tool and outcome_plans: [] when "
                "no other operation needs an outcome. "
                "Give the result agent one after_turn success continuation. Factory adds the failure routing."
            )
            brief = "\n".join(["[WORKFLOW RESULT PLAN]", *instructions, json.dumps(plans, sort_keys=True)])
            item = {**item, "initial_message": f"{item['initial_message']}\n\n{brief}"}
        specs.append(item)
    context_variables.set("workflows_spec", specs)
    context_variables.set("workflow_plan_review", None)
    return {"outcome": "selected", "workflow_count": len(selection["workflows"])}

