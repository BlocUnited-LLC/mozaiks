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
from factory_app.workflows.ExistingAppDiscovery.tools.emit_app_intelligence_overview import (
    emit_app_intelligence_enriched_overview_card,
)
from mozaiksai.core.app_context.store import (
    build_brownfield_app_context_version,
    register_app_context_version,
)
from mozaiksai.core.artifacts.models import ArtifactLifecycleStatus, ArtifactValidationStatus
from mozaiksai.core.artifacts.store import get_artifact_store

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
    analysis_summary = str(data.get("analysis_summary") or data.get("app_summary") or data.get("discovery_brief") or "").strip()
    ai_caps = augmentation_plan.get("ai_accessible_capabilities") or []
    chat_id = context_variables.get("chat_id")

    logger.info(
        "[ExistingAppDiscovery] Final discovery artifact save requested: chat_id=%s app=%s capability_specs=%d ai_caps=%d analysis_summary_present=%s structured_output_present=%s",
        chat_id or "NONE",
        product_spec.get("app_name") or "unknown",
        len(capability_specs),
        len(ai_caps),
        bool(analysis_summary),
        True,
    )

    adoption_level = augmentation_plan.get("adoption_level", "embed")
    migration_complexity = augmentation_plan.get("migration_complexity")

    # ------------------------------------------------------------------
    # Persist context variables
    # ------------------------------------------------------------------
    context_variables["existing_product_spec"] = product_spec
    context_variables["capability_specs"] = capability_specs
    context_variables["agent_augmentation_plan"] = augmentation_plan
    _set_context_value(context_variables, "analysis_summary", analysis_summary)
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

    # Re-emit the overview card with agent-enriched capability data (descriptions, categories)
    # and the synthesized analysis summary. This updates the artifact panel with the full v2
    # payload now that the assembler has produced the canonical discovery result.
    try:
        logger.info(
            "[ExistingAppDiscovery] Re-emitting overview with agent output: chat_id=%s capability_specs=%d",
            chat_id or "NONE",
            len(capability_specs),
        )
        _set_context_value(context_variables, "discovery_brief", analysis_summary or data.get("discovery_brief") or "")
        _set_context_value(context_variables, "analysis_summary", analysis_summary)
        _set_context_value(context_variables, "existing_app_discovery_artifact", data)
        await emit_app_intelligence_enriched_overview_card(
            context_variables=context_variables,
            capability_specs=capability_specs,
        )
    except Exception as exc:
        logger.warning("[ExistingAppDiscovery] Enriched overview card re-emission failed: %s", exc)

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
