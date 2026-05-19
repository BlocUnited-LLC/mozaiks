"""Persist the ExistingAppDiscovery augmentation artifacts and emit a review card."""

import json
import os
from pathlib import Path
from typing import Annotated, Any, Dict, Optional
import logging

from mozaiksai.core.workflow.ui_tools import emit_ui_surface


logger = logging.getLogger(__name__)

_DECOMPOSITION_ADOPTION_LEVELS = {"native_migration", "ecosystem"}

_DEFAULT_GENERATED_ROOT = Path("generated")


def _generated_root() -> Path:
    env = os.environ.get("MOZAIKS_GENERATED_ARTIFACTS_PATH")
    return Path(env) if env else _DEFAULT_GENERATED_ROOT


async def save_existing_app_artifacts(
    context_variables: Annotated[Optional[Any], "Runtime context"] = None,
) -> Dict[str, Any]:
    """Persist the canonical existing-app discovery artifacts and emit a UI summary."""
    if not context_variables:
        return {"success": False, "error": "No context provided"}

    data = context_variables.get("structured_output")
    if not data:
        return {
            "success": False,
            "error": "No structured output from DiscoveryArtifactAssemblerAgent",
        }

    product_spec = data.get("existing_product_spec") or {}
    capability_specs = data.get("capability_specs") or []
    augmentation_plan = data.get("agent_augmentation_plan") or {}
    ai_caps = augmentation_plan.get("ai_accessible_capabilities") or []
    module_decomposition_plan = data.get("module_decomposition_plan")
    chat_id = context_variables.get("chat_id")

    adoption_level = augmentation_plan.get("adoption_level", "embed")
    migration_complexity = augmentation_plan.get("migration_complexity")

    # ------------------------------------------------------------------
    # UI payload — includes new detection signals for UI surface display
    # ------------------------------------------------------------------
    ui_payload = {
        "app_name": product_spec.get("app_name", "Unknown App"),
        "app_description": product_spec.get("app_description", ""),
        "tech_stack": product_spec.get("tech_stack", ""),
        "brand_theme_summary": product_spec.get("brand_theme_summary", ""),
        "brand_theme_evidence": product_spec.get("brand_theme_evidence") or {},
        "storage_pattern": product_spec.get("storage_pattern", "unknown"),
        "storage_migration_required": product_spec.get("storage_migration_required", False),
        "detected_connectors": product_spec.get("detected_connectors") or [],
        "mozaiks_vocabulary_detected": product_spec.get("mozaiks_vocabulary_detected", False),
        "mozaiks_authored_app": product_spec.get("mozaiks_authored_app", False),
        "adoption_level": adoption_level,
        "migration_complexity": migration_complexity,
        "adoption_rationale": augmentation_plan.get("adoption_rationale", ""),
        "new_adapters_required": augmentation_plan.get("new_adapters_required") or [],
        "theme_adaptation_strategy": augmentation_plan.get("theme_adaptation_strategy", ""),
        "embed_theme_ready": augmentation_plan.get("embed_theme_ready", False),
        "discovery_brief": data.get("discovery_brief", ""),
        "capability_count": len(capability_specs),
        "ai_accessible_count": len(ai_caps),
        "service_surface_count": len(product_spec.get("service_surfaces") or []),
        "route_surface_count": len(product_spec.get("route_surfaces") or []),
        "has_decomposition_plan": bool(module_decomposition_plan),
        "capabilities": [
            {
                "name": cap.get("label", cap.get("capability_id", "")),
                "agent_ready": cap.get("agent_ready", False),
                "confidence": cap.get("confidence", "unverified"),
                "delivery_surface": cap.get("delivery_surface", ""),
                "migration_priority": cap.get("migration_priority"),
                "connector_requirements": cap.get("connector_requirements") or [],
            }
            for cap in capability_specs
        ],
        "unresolved_questions": [
            {
                "question": item.get("question", ""),
                "priority": item.get("priority", "medium"),
            }
            for item in data.get("unresolved_questions") or []
        ],
        "auth_model": product_spec.get("auth_model", ""),
        "auth_delegation_model": augmentation_plan.get("auth_delegation_model", ""),
        "ui_surface_preference": augmentation_plan.get("ui_surface_preference", ""),
        "initial_workflows": augmentation_plan.get("initial_workflows") or [],
        "ecosystem_bindings": augmentation_plan.get("ecosystem_bindings") or [],
        "artifact_version": data.get("artifact_version", "1.0"),
    }

    await emit_ui_surface(
        "DiscoveryBriefCard",
        ui_payload,
        chat_id=str(chat_id) if chat_id else None,
        workflow_name="ExistingAppDiscovery",
        agent_name="DiscoveryArtifactAssemblerAgent",
    )

    # ------------------------------------------------------------------
    # Persist context variables
    # ------------------------------------------------------------------
    context_variables["existing_product_spec"] = product_spec
    context_variables["capability_specs"] = capability_specs
    context_variables["agent_augmentation_plan"] = augmentation_plan
    context_variables["existing_app_discovery_artifact"] = data
    if module_decomposition_plan is not None:
        context_variables["module_decomposition_plan"] = module_decomposition_plan

    # ------------------------------------------------------------------
    # Write ModuleDecompositionPlan to generated/ for native_migration and ecosystem
    # ------------------------------------------------------------------
    plan_path_written: Optional[str] = None
    if adoption_level in _DECOMPOSITION_ADOPTION_LEVELS and module_decomposition_plan:
        try:
            safe_chat_id = str(chat_id) if chat_id else "unknown"
            out_dir = _generated_root() / "existing_app_discovery" / safe_chat_id
            out_dir.mkdir(parents=True, exist_ok=True)
            plan_path = out_dir / "module_decomposition_plan.json"

            # module_decomposition_plan may arrive as a serialized JSON string or dict
            if isinstance(module_decomposition_plan, str):
                parsed = json.loads(module_decomposition_plan)
            else:
                parsed = module_decomposition_plan

            plan_path.write_text(json.dumps(parsed, indent=2), encoding="utf-8")
            plan_path_written = str(plan_path)
            logger.info(
                "[ExistingAppDiscovery] ModuleDecompositionPlan written to %s",
                plan_path_written,
            )
        except Exception as exc:
            logger.warning(
                "[ExistingAppDiscovery] Could not write module_decomposition_plan: %s", exc
            )

    logger.info(
        "[ExistingAppDiscovery] Artifacts saved for '%s' — capabilities=%s "
        "adoption_level=%s migration_complexity=%s decomposition_plan=%s",
        product_spec.get("app_name", "unknown"),
        len(capability_specs),
        adoption_level,
        migration_complexity or "n/a",
        "yes" if plan_path_written else "no",
    )

    summary_parts = [
        f"Existing app augmentation artifacts created for "
        f"{product_spec.get('app_name', 'your app')}. "
        f"{len(capability_specs)} capabilities mapped, "
        f"{len(ai_caps)} approved for initial AI access."
    ]
    if plan_path_written:
        summary_parts.append(f" Module decomposition plan written to {plan_path_written}.")

    return {
        "success": True,
        "message": "".join(summary_parts),
    }
