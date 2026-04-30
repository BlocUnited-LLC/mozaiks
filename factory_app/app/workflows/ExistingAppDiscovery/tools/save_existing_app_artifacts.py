"""Persist the ExistingAppDiscovery augmentation artifacts and emit a review card."""

from typing import Annotated, Any, Dict, Optional
import logging

from mozaiksai.core.workflow.ui_tools import emit_ui_surface


logger = logging.getLogger(__name__)


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
    chat_id = context_variables.get("chat_id")

    ui_payload = {
        "app_name": product_spec.get("app_name", "Unknown App"),
        "app_description": product_spec.get("app_description", ""),
        "tech_stack": product_spec.get("tech_stack", ""),
        "brand_theme_summary": product_spec.get("brand_theme_summary", ""),
        "brand_theme_evidence": product_spec.get("brand_theme_evidence") or {},
        "adoption_level": augmentation_plan.get("adoption_level", "embed"),
        "adoption_rationale": augmentation_plan.get("adoption_rationale", ""),
        "theme_adaptation_strategy": augmentation_plan.get("theme_adaptation_strategy", ""),
        "embed_theme_ready": augmentation_plan.get("embed_theme_ready", False),
        "discovery_brief": data.get("discovery_brief", ""),
        "capability_count": len(capability_specs),
        "ai_accessible_count": len(ai_caps),
        "service_surface_count": len(product_spec.get("service_surfaces") or []),
        "route_surface_count": len(product_spec.get("route_surfaces") or []),
        "capabilities": [
            {
                "name": cap.get("label", cap.get("capability_id", "")),
                "agent_ready": cap.get("agent_ready", False),
                "confidence": cap.get("confidence", "unverified"),
                "delivery_surface": cap.get("delivery_surface", ""),
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

    context_variables["existing_product_spec"] = product_spec
    context_variables["capability_specs"] = capability_specs
    context_variables["agent_augmentation_plan"] = augmentation_plan
    context_variables["existing_app_discovery_artifact"] = data

    logger.info(
        "[ExistingAppDiscovery] Artifacts saved for '%s' — capabilities=%s adoption_level=%s",
        product_spec.get("app_name", "unknown"),
        len(capability_specs),
        augmentation_plan.get("adoption_level", "unknown"),
    )

    return {
        "success": True,
        "message": (
            f"Existing app augmentation artifacts created for "
            f"{product_spec.get('app_name', 'your app')}. "
            f"{len(capability_specs)} capabilities mapped, "
            f"{len(ai_caps)} approved for initial AI access."
        ),
    }
