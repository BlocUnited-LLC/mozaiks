"""Validate the workflow partition before dispatch or human review."""

from __future__ import annotations

from typing import Annotated, Any

from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.outputs.structured import load_workflow_structured_outputs


def validate_selection(selection: Any, context_variables: Any) -> dict[str, Any]:
    models, _ = load_workflow_structured_outputs("AgentGenerator")
    selection = models["PatternSelection"].model_validate(detach(selection)).model_dump(mode="json")
    workflows = selection["workflows"]
    if selection["is_multi_workflow"] != (len(workflows) > 1):
        raise ValueError("is_multi_workflow must match the workflow count")
    if not workflows and not (selection["pack_partition_reason"] or "").strip():
        raise ValueError("An empty workflow partition requires a rationale")
    names = [item["name"] for item in workflows]
    if len(set(names)) != len(names):
        raise ValueError("Workflow names must be unique")
    for item in workflows:
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
    context_variables.set("workflows_spec", selection["workflows"])
    context_variables.set("workflow_plan_review", None)
    return {"outcome": "selected", "workflow_count": len(selection["workflows"])}

