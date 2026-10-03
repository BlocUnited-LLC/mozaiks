"""Record confirmed discovery plans through the workflow's canonical models."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from mozaiksai.core.workflow.outputs.structured import load_workflow_structured_outputs

if TYPE_CHECKING:
    AgentAugmentationPlan = BaseModel
    ModuleDecompositionPlan = BaseModel
else:
    _models, _ = load_workflow_structured_outputs("ExistingAppDiscovery")
    AgentAugmentationPlan = _models["AgentAugmentationPlan"]
    ModuleDecompositionPlan = _models["ModuleDecompositionPlan"]


def _validated_plan(model_name: str, plan: Any) -> dict[str, Any]:
    models, _ = load_workflow_structured_outputs("ExistingAppDiscovery")
    payload = plan.model_dump(mode="json") if isinstance(plan, BaseModel) else plan
    return models[model_name].model_validate(payload).model_dump(mode="json")


def record_adoption_plan(plan: AgentAugmentationPlan, context_variables: Any) -> dict[str, Any]:
    """Record the complete adoption plan only after the user confirms its scope."""
    context_variables["plan_complete"] = False
    context_variables["decomposition_complete"] = False
    context_variables["module_decomposition_plan"] = None
    payload = _validated_plan("AgentAugmentationPlan", plan)
    if not context_variables.get("identity_complete") or not context_variables.get("capabilities_complete"):
        raise ValueError("Confirm the app identity and capability map before recording an adoption plan")
    context_variables["agent_augmentation_plan"] = payload
    for key, value in payload.items():
        # Adapter requirements live in the plan, not a separate context key.
        if key != "new_adapters_required":
            context_variables[key] = value
    context_variables["plan_complete"] = True
    return {"success": True, "adoption_level": payload["adoption_level"]}


def record_module_decomposition(plan: ModuleDecompositionPlan, context_variables: Any) -> dict[str, Any]:
    """Record confirmed workflow-local decomposition for the approved adoption path."""
    context_variables["decomposition_complete"] = False
    context_variables["module_decomposition_plan"] = None
    payload = _validated_plan("ModuleDecompositionPlan", plan)
    approved = context_variables.get("agent_augmentation_plan") or {}
    level = context_variables.get("adoption_level")
    if (
        not context_variables.get("plan_complete")
        or level not in {"ecosystem", "gradual_modernization"}
        or approved.get("adoption_level") != level
        or payload["adoption_level"] != level
    ):
        raise ValueError("Decomposition must match a recorded ecosystem or gradual modernization plan")
    context_variables["module_decomposition_plan"] = json.dumps(payload)
    context_variables["decomposition_complete"] = True
    return {"success": True, "adoption_level": level}
