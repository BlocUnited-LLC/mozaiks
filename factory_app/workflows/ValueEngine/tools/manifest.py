"""Persist ConceptBlueprint drafts and collect explicit, draft-bound reviews."""

import logging
from collections.abc import MutableMapping
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, StrictBool, StrictStr, ValidationError

logger = logging.getLogger(__name__)

from factory_app.app.modules.app_registry.backend.policy import is_generic_app_name
from mozaiksai.core.artifacts import persist_summary_artifact
from mozaiksai.core.data.persistence.artifact_store import BuilderArtifactStore
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.ui_tools import use_ui_tool


class ConceptReviewResponse(BaseModel):
    action: Literal["approve", "request_changes", "cancel"]
    approved: StrictBool
    review_id: StrictStr
    rationale: StrictStr = Field(default="", max_length=4000)


def _set_context_value(context_variables: Any | None, key: str, value: Any) -> None:
    """Preserve runtime authority failures instead of silently dropping writes."""
    if context_variables is None:
        return
    if callable(getattr(context_variables, "set", None)):
        context_variables.set(key, value)
    elif isinstance(context_variables, MutableMapping):
        context_variables[key] = value
    else:
        raise TypeError("Concept review requires writable runtime context")


def _build_concept_record(
    *,
    app_id: str,
    manifest_id: str,
    structured_output: dict[str, Any],
    app_name: str,
    concept_overview: str,
    api_endpoints: list[Any],
    capability_pack_hints: list[Any],
    surface_candidate_hints: list[Any],
    agentic_capabilities: list[Any],
    now_iso: str,
) -> dict[str, Any]:
    """Build the canonical persisted concept artifact used by downstream workflows."""

    return {
        "concept_id": manifest_id,
        "app_id": str(app_id),
        "app_name": app_name,
        "ConceptOverview": concept_overview,
        "ApiEndpoints": api_endpoints,
        "Blueprint": structured_output,
        "capability_pack_hints": capability_pack_hints,
        "surface_candidate_hints": surface_candidate_hints,
        "agentic_capabilities": agentic_capabilities,
        "updated_at": now_iso,
        "status": "draft",
    }


def _concept_record_to_manifest(doc: dict[str, Any], app_id: str) -> dict[str, Any]:
    """Normalize the canonical Concepts record into the runtime manifest shape."""

    blueprint = doc.get("Blueprint")
    if not isinstance(blueprint, dict):
        blueprint = {}

    app_name = str(doc.get("app_name") or blueprint.get("app_name") or "Unnamed App")
    concept_overview = str(doc.get("ConceptOverview") or blueprint.get("concept_overview") or "")
    api_endpoints = doc.get("ApiEndpoints")
    if not isinstance(api_endpoints, list):
        api_endpoints = list(blueprint.get("api_endpoints") or [])

    return {
        "manifest_id": str(doc.get("concept_id") or f"concept_{app_id}"),
        "app_id": str(app_id),
        "app_name": app_name,
        "concept_overview": concept_overview,
        "api_endpoints": api_endpoints,
        "capability_pack_hints": list(doc.get("capability_pack_hints") or blueprint.get("capability_pack_hints") or []),
        "surface_candidate_hints": list(doc.get("surface_candidate_hints") or blueprint.get("surface_candidate_hints") or []),
        "agentic_capabilities": list(doc.get("agentic_capabilities") or blueprint.get("agentic_capabilities") or []),
        "blueprint": blueprint,
        "updated_at": doc.get("updated_at"),
        "status": doc.get("status") or "draft",
    }


async def save_value_manifest(
    context_variables: Annotated[Any | None, "Runtime context with structured output"] = None,
) -> dict[str, Any]:
    """Return a declared tool outcome only after persistence and structured review."""
    _set_context_value(context_variables, "concept_review_outcome", "blocked")
    # A prior approval must never carry over to a newly generated proposal.
    _set_context_value(context_variables, "value_manifest", None)
    # Extract context
    chat_id = None
    app_id = None
    user_id = None
    workflow_name = "ValueEngine"
    build_mode = None
    structured_output = None

    if context_variables is not None and hasattr(context_variables, "get"):
        chat_id = context_variables.get("chat_id")
        app_id = context_variables.get("app_id")
        user_id = context_variables.get("user_id")
        workflow_name = context_variables.get("workflow_name", "ValueEngine")
        build_mode = context_variables.get("build_mode")
        # Structured output from GapAnalysisAgent
        structured_output = detach(context_variables.get("structured_output"))
    if not isinstance(structured_output, dict) or not structured_output:
        return {"success": False, "outcome": "blocked", "error": "ConceptBlueprint structured output is required"}
    if not all(isinstance(value, str) and value.strip() for value in (app_id, chat_id, user_id)):
        return {"success": False, "outcome": "blocked", "error": "App, chat, and user identities are required for review"}

    # Extract fields from structured output
    app_name = str(structured_output.get("app_name") or "").strip()
    if is_generic_app_name(app_name):
        return {
            "success": False,
            "outcome": "blocked",
            "error": "ConceptBlueprint.app_name must be a specific product name before the app registry can be named.",
        }
    value_proposition = structured_output.get("value_proposition", "")
    concept_overview = structured_output.get("concept_overview", "")
    target_user = structured_output.get("target_user", "")
    target_users = structured_output.get("target_users", [])
    core_jobs = structured_output.get("core_jobs", [])
    core_features = structured_output.get("core_features", [])
    deferred_features = structured_output.get("deferred_features", [])
    unique_differentiators = structured_output.get("unique_differentiators", [])
    brand_intent = structured_output.get("brand_intent")
    constraints = structured_output.get("constraints", [])
    api_endpoints = structured_output.get("api_endpoints", [])
    app_ui_requirements = structured_output.get("app_ui_requirements", [])
    capability_pack_hints = structured_output.get("capability_pack_hints", [])
    surface_candidate_hints = structured_output.get("surface_candidate_hints", [])
    agentic_capabilities = structured_output.get("agentic_capabilities", [])

    manifest_id = f"manifest_{app_id}"
    now = datetime.now(UTC)
    review_id = f"concept_review_{uuid4().hex}"

    # Build manifest for persistence
    manifest = {
        "manifest_id": manifest_id,
        "app_id": str(app_id),
        "app_name": app_name,
        "value_proposition": value_proposition,
        "concept_overview": concept_overview,
        "target_user": target_user,
        "target_users": target_users,
        "core_jobs": core_jobs,
        "approved_scope": [],
        "deferred_scope": deferred_features,
        "unique_differentiators": unique_differentiators,
        "brand_intent": brand_intent if isinstance(brand_intent, dict) else None,
        "constraints": constraints,
        "api_endpoints": api_endpoints,
        "app_ui_requirements": app_ui_requirements,
        "capability_pack_hints": capability_pack_hints,
        "surface_candidate_hints": surface_candidate_hints,
        "agentic_capabilities": agentic_capabilities,
        "updated_at": now.isoformat(),
        "status": "draft",  # Will be "approved" after user confirms
        "review_id": review_id,
    }
    concept_record = _build_concept_record(
        app_id=str(app_id),
        manifest_id=manifest_id,
        structured_output=structured_output,
        app_name=app_name,
        concept_overview=concept_overview,
        api_endpoints=api_endpoints,
        capability_pack_hints=capability_pack_hints,
        surface_candidate_hints=surface_candidate_hints,
        agentic_capabilities=agentic_capabilities,
        now_iso=now.isoformat(),
    )

    concept_record.update(review_id=review_id, review_owner_user_id=user_id)
    store = BuilderArtifactStore()
    try:
        await store.save_concept(app_id=str(app_id), concept_record=concept_record, created_at=now.isoformat())
        await persist_summary_artifact(
            app_id=str(app_id),
            artifact_kind="concept",
            artifact_key="concept",
            summary_payload=manifest,
            source_workflow=str(workflow_name or "ValueEngine"),
            source_chat_id=str(chat_id) if chat_id else None,
            author_user_id=str(user_id) if user_id else None,
            revision_mode=str(build_mode or "").strip().lower() == "revision",
        )
    except Exception as exc:
        logger.warning("[ValueEngine] Draft persistence failed: %s", exc)
        return {"success": False, "outcome": "blocked", "error": "Concept draft could not be persisted"}

    # Build UI payload matching ConceptBlueprint.js expectations
    ui_payload = {
        "title": f"Concept Blueprint: {app_name}",
        "app_id": str(app_id),
        "review_id": review_id,
        "concept_overview": concept_overview,
        "blueprint": {
            "app_name": app_name,
            "value_proposition": value_proposition,
            "target_user": target_user,
            "target_users": target_users,
            "mvp_scope": {
                "core_features": core_features,
                "deferred_features": deferred_features,
            },
            "unique_differentiators": unique_differentiators,
            "brand_intent": brand_intent if isinstance(brand_intent, dict) else None,
            "app_ui_requirements": app_ui_requirements,
            "capability_pack_hints": capability_pack_hints,
            "surface_candidate_hints": surface_candidate_hints,
            "agentic_capabilities": agentic_capabilities,
        },
        "api_endpoints": api_endpoints,
    }

    # Store context values for downstream routing and tools.
    _set_context_value(context_variables, "value_manifest", manifest)
    _set_context_value(context_variables, "concept_blueprint", structured_output)
    _set_context_value(context_variables, "concept_overview", concept_overview)
    _set_context_value(context_variables, "api_endpoints", api_endpoints)
    _set_context_value(context_variables, "surface_candidate_hints", surface_candidate_hints)
    _set_context_value(context_variables, "concept_presented", True)

    try:
        response = ConceptReviewResponse.model_validate(await use_ui_tool(
            "save_value_manifest", ui_payload, chat_id=str(chat_id),
            workflow_name=str(workflow_name or "ValueEngine"), display="artifact",
        ))
        if response.review_id != review_id or response.approved is not (response.action == "approve"):
            return {"success": False, "outcome": "blocked", "error": "Concept review does not match this draft"}
        outcome = {"approve": "approved", "request_changes": "changes_requested", "cancel": "cancelled"}[response.action]
        if not await store.finish_concept_review(
            app_id=str(app_id), review_id=review_id, status=outcome,
            reviewed_by=str(user_id), feedback=response.rationale,
        ):
            return {"success": False, "outcome": "blocked", "error": "Concept draft is no longer awaiting this review"}
        reviewed_manifest = {
            **manifest, "status": outcome, "review_feedback": response.rationale,
            "approved_scope": core_features if outcome == "approved" else [],
        }
        await persist_summary_artifact(
            app_id=str(app_id), artifact_kind="concept", artifact_key="concept", summary_payload=reviewed_manifest,
            source_workflow=str(workflow_name or "ValueEngine"), source_chat_id=str(chat_id),
            author_user_id=str(user_id), revision_mode=str(build_mode or "").strip().lower() == "revision",
        )
    except ValidationError:
        return {"success": False, "outcome": "blocked", "error": "Invalid structured concept review"}
    except Exception as exc:
        logger.warning("[ValueEngine] Concept review failed: %s", exc)
        return {"success": False, "outcome": "blocked", "error": "Concept review could not be completed"}
    _set_context_value(context_variables, "value_manifest", reviewed_manifest)
    _set_context_value(context_variables, "concept_review_feedback", response.rationale)
    _set_context_value(context_variables, "app_name", app_name)
    return {
        "success": outcome == "approved",
        "outcome": outcome,
        "manifest_id": manifest_id,
        "app_id": str(app_id),
        "message": f"Concept review: {outcome}",
    }


async def get_value_manifest(
    app_id: Annotated[str, "Application ID"],
    context_variables: Annotated[Any | None, "Runtime context"] = None,
) -> dict[str, Any]:
    """
    Retrieve the Value Manifest for an app.

    Used by AgentGenerator/AppGenerator to understand what to build.
    """
    # Check context first
    if context_variables and hasattr(context_variables, "get"):
        cached = context_variables.get("value_manifest")
        if cached and cached.get("app_id") == str(app_id):
            return {"success": True, "manifest": cached}

    # Load from MongoDB
    try:
        store = BuilderArtifactStore()
        concept = await store.get_concept(app_id=str(app_id))
        if concept:
            return {"success": True, "manifest": _concept_record_to_manifest(concept, str(app_id))}
    except Exception as e:
        logger.warning("[ValueEngine] Load manifest failed: %s", e)

    return {"success": False, "error": f"No manifest found for app_id={app_id}"}
