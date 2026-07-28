"""Persist the ExistingAppDiscovery augmentation artifacts and emit a review card."""

import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Annotated, Any

from factory_app.workflows.ExistingAppDiscovery.tools.app_context_mapping import (
    APP_CONTEXT_ARTIFACT_KINDS,
    build_existing_app_context_artifacts,
)
from mozaiksai.core.app_context.store import (
    build_brownfield_app_context_version,
    register_app_context_version,
)
from mozaiksai.core.artifacts.models import ArtifactLifecycleStatus, ArtifactValidationStatus
from mozaiksai.core.artifacts.store import get_artifact_store
from mozaiksai.core.workflow.ui_tools import emit_ui_surface

logger = logging.getLogger(__name__)


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    ).encode("utf-8")


def _set_context_value(context_variables: Any, key: str, value: Any) -> None:
    try:
        if hasattr(context_variables, "set"):
            context_variables.set(key, value)
            return
    except Exception:
        pass
    try:
        context_variables[key] = value
    except Exception:
        return


def _get_context_value(context_variables: Any, key: str, default: Any = None) -> Any:
    try:
        if hasattr(context_variables, "get"):
            return context_variables.get(key, default)
    except Exception:
        pass
    try:
        return context_variables[key]
    except Exception:
        return default


def _artifact_version_id(version_doc: Any) -> str | None:
    if hasattr(version_doc, "id"):
        return str(version_doc.id)
    if isinstance(version_doc, dict):
        raw_id = version_doc.get("id") or version_doc.get("_id")
        return str(raw_id) if raw_id else None
    return None


async def _persist_app_context_artifact_drafts(
    *,
    app_id: str,
    chat_id: Any,
    artifact_payloads: dict[str, Any],
    artifact_store: Any | None = None,
) -> dict[str, str]:
    store = artifact_store or get_artifact_store()
    persisted_refs: dict[str, str] = {}
    generated_at = datetime.now(UTC).isoformat()

    for artifact_kind in APP_CONTEXT_ARTIFACT_KINDS:
        payload = artifact_payloads.get(artifact_kind)
        if payload is None:
            continue

        raw = _json_bytes(payload)
        version_doc = await store.create_artifact_version(
            app_id=str(app_id),
            artifact_kind=artifact_kind,
            artifact_key=artifact_kind,
            files_manifest=[
                {
                    "path": f"existing_app_discovery/{artifact_kind}.json",
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "size_bytes": len(raw),
                    "content_type": "application/json",
                }
            ],
            source_workflow="ExistingAppDiscovery",
            source_chat_id=str(chat_id) if chat_id else None,
            lifecycle_status=ArtifactLifecycleStatus.DRAFT,
            validation_status=ArtifactValidationStatus.PENDING,
            commit_metadata={
                "message": f"ExistingAppDiscovery: {artifact_kind}",
                "source_workflow": "ExistingAppDiscovery",
                "source_chat_id": str(chat_id) if chat_id else None,
                "metadata": {
                    "summary_payload": payload,
                    "summary_format": "json",
                    "artifact_contract": "mozaiksai/core/app_context/models.py",
                    "generated_at": generated_at,
                    "source_workflow": "ExistingAppDiscovery",
                },
            },
        )
        version_id = _artifact_version_id(version_doc)
        if version_id:
            persisted_refs[artifact_kind] = version_id

    return persisted_refs


async def save_existing_app_artifacts(
    context_variables: Annotated[Any | None, "Runtime context"] = None,
) -> dict[str, Any]:
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
        "adoption_plan_available": bool(augmentation_plan),
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

    # ------------------------------------------------------------------
    # Derive canonical app-context artifacts for the App Intelligence Plane.
    # Workflow-local discovery output remains evidence; AppContext is the handoff.
    # ------------------------------------------------------------------
    try:
        app_context_artifacts = build_existing_app_context_artifacts(
            data,
            context_variables=context_variables,
        )
        app_context_payloads = app_context_artifacts.as_artifact_payloads()
        source_context_bundle = _get_context_value(context_variables, "source_context_bundle")
        if isinstance(source_context_bundle, dict) and source_context_bundle:
            app_context_payloads["source_context_bundle"] = source_context_bundle
        app_intelligence_snapshot = _get_context_value(context_variables, "app_intelligence_snapshot")
        if isinstance(app_intelligence_snapshot, dict) and app_intelligence_snapshot:
            app_context_payloads["app_intelligence_snapshot"] = app_intelligence_snapshot
        _set_context_value(context_variables, "application_inventory", app_context_payloads["application_inventory"])
        _set_context_value(context_variables, "ownership_boundary", app_context_payloads["ownership_boundary"])
        _set_context_value(
            context_variables,
            "integration_inventory",
            app_context_payloads["integration_inventory"],
        )
        _set_context_value(context_variables, "risk_report", app_context_payloads["risk_report"])
        _set_context_value(context_variables, "adoption_plan", app_context_payloads["adoption_plan"])
        _set_context_value(
            context_variables,
            "brownfield_registration",
            app_context_payloads["brownfield_registration"],
        )
        if "app_context_graph" in app_context_payloads:
            _set_context_value(
                context_variables,
                "app_context_graph",
                app_context_payloads["app_context_graph"],
            )
        if "source_context_bundle" in app_context_payloads:
            _set_context_value(
                context_variables,
                "source_context_bundle",
                app_context_payloads["source_context_bundle"],
            )
        if "app_intelligence_snapshot" in app_context_payloads:
            _set_context_value(
                context_variables,
                "app_intelligence_snapshot",
                app_context_payloads["app_intelligence_snapshot"],
            )
        _set_context_value(
            context_variables,
            "brownfield_app_context_artifacts",
            app_context_payloads,
        )

        try:
            artifact_store = get_artifact_store()
            persisted_refs = await _persist_app_context_artifact_drafts(
                app_id=app_context_artifacts.app_id,
                chat_id=chat_id,
                artifact_payloads=app_context_payloads,
                artifact_store=artifact_store,
            )
            _set_context_value(
                context_variables,
                "brownfield_app_context_artifact_version_refs",
                persisted_refs,
            )

            app_context_version = build_brownfield_app_context_version(
                app_id=app_context_artifacts.app_id,
                artifact_version_refs=persisted_refs,
                source_refs=app_context_artifacts.application_inventory.source_refs,
                ownership_boundaries=app_context_artifacts.ownership_boundaries,
                application_inventory=app_context_artifacts.application_inventory,
                context_version_id=app_context_artifacts.brownfield_registration.context_version_id,
            )
            registered_context = await register_app_context_version(
                app_context_version,
                artifact_store=artifact_store,
                source_workflow="ExistingAppDiscovery",
                source_chat_id=str(chat_id) if chat_id else None,
                make_current=True,
            )
            app_context_version_payload = registered_context.context_version.model_dump(mode="json")
            _set_context_value(
                context_variables,
                "app_context_version",
                app_context_version_payload,
            )
            _set_context_value(
                context_variables,
                "app_context_version_artifact_version_id",
                registered_context.artifact_version.id,
            )
            _set_context_value(
                context_variables,
                "current_app_context_version_id",
                registered_context.context_version.context_version_id,
            )
        except Exception as exc:
            logger.warning(
                "[ExistingAppDiscovery] Draft app-context ArtifactVersion persistence failed: %s",
                exc,
            )
            _set_context_value(
                context_variables,
                "brownfield_app_context_artifact_persistence_error",
                str(exc),
            )
    except Exception as exc:
        logger.warning(
            "[ExistingAppDiscovery] Could not derive app-context contract drafts: %s",
            exc,
        )

    logger.info(
        "[ExistingAppDiscovery] Artifacts saved for '%s' — capabilities=%s "
        "adoption_level=%s migration_complexity=%s",
        product_spec.get("app_name", "unknown"),
        len(capability_specs),
        adoption_level,
        migration_complexity or "n/a",
    )

    summary_parts = [
        f"Existing app augmentation artifacts created for "
        f"{product_spec.get('app_name', 'your app')}. "
        f"{len(capability_specs)} capabilities mapped, "
        f"{len(ai_caps)} approved for initial AI access."
    ]
    return {
        "success": True,
        "message": "".join(summary_parts),
    }
