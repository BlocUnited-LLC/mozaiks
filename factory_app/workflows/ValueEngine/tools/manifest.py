"""
Value Manifest Tool - Save and update the app's value direction.

Auto-invoked after GapAnalysisAgent outputs ConceptBlueprint structured output.
Reads the structured output from context and:
1. Persists the manifest to MongoDB
2. Emits the ConceptBlueprint UI artifact

Tools are dumb. LLMs reason. This tool just saves and emits.
"""

from typing import Annotated, Any, Dict, List, Optional
from datetime import datetime, UTC
import logging

logger = logging.getLogger(__name__)

# Use mozaiks runtime persistence
try:
    from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager
    _HAS_PERSISTENCE = True
except ImportError:
    _HAS_PERSISTENCE = False
    AG2PersistenceManager = None

from mozaiksai.core.workflow.ui_tools import emit_ui_surface


def _set_context_value(context_variables: Optional[Any], key: str, value: Any) -> None:
    """Set a context variable across supported container implementations."""
    if context_variables is None or not isinstance(key, str) or not key:
        return
    try:
        if hasattr(context_variables, "set"):
            context_variables.set(key, value)
            return
    except Exception:
        pass
    try:
        if hasattr(context_variables, "__setitem__"):
            context_variables[key] = value
    except Exception:
        return


async def save_value_manifest(
    context_variables: Annotated[Optional[Any], "Runtime context with structured output"] = None,
) -> Dict[str, Any]:
    """
    Auto-invoked after GapAnalysisAgent outputs ConceptBlueprint.

    Reads the structured output from context_variables and:
    1. Persists the manifest to MongoDB
    2. Emits the ConceptBlueprint UI artifact

    Returns:
        {
            "success": bool,
            "manifest_id": str,
            "app_id": str,
            "message": str
        }
    """
    # Extract context
    chat_id = None
    app_id = None
    workflow_name = "ValueEngine"
    structured_output = None

    if context_variables is not None and hasattr(context_variables, "get"):
        chat_id = context_variables.get("chat_id")
        app_id = context_variables.get("app_id")
        workflow_name = context_variables.get("workflow_name", "ValueEngine")
        # Structured output from GapAnalysisAgent
        structured_output = context_variables.get("structured_output")
        # Also check for direct ConceptBlueprint output
        if not structured_output:
            structured_output = context_variables.get("ConceptBlueprint")

    if not structured_output:
        return {"success": False, "error": "No structured output (ConceptBlueprint) in context"}

    if not app_id:
        return {"success": False, "error": "app_id required in context"}

    # Extract fields from structured output
    app_name = structured_output.get("app_name", "Unnamed App")
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
        "approved_scope": core_features,  # core_features = approved scope
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
    }

    # Persist to MongoDB via mozaiks runtime
    if _HAS_PERSISTENCE and AG2PersistenceManager:
        try:
            pm = AG2PersistenceManager()
            await pm._ensure_client()
            if pm.client:
                coll = pm.client["autogen_ai_agents"]["ValueManifests"]
                await coll.update_one(
                    {"app_id": str(app_id)},
                    {"$set": manifest, "$setOnInsert": {"created_at": now.isoformat()}},
                    upsert=True,
                )
                logger.info(f"[ValueEngine] Manifest saved for app_id={app_id}")
        except Exception as e:
            logger.warning(f"[ValueEngine] Persistence failed: {e}")

    # Build UI payload matching ConceptBlueprint.js expectations
    ui_payload = {
        "title": f"Concept Blueprint: {app_name}",
        "app_id": str(app_id),
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

    # Emit UI artifact through the sanctioned one-way UI helper.
    try:
        await emit_ui_surface(
            "ConceptBlueprint",
            ui_payload,
            chat_id=str(chat_id) if chat_id else None,
            workflow_name=str(workflow_name or "ValueEngine"),
            agent_name="GapAnalysisAgent",
        )
    except Exception as e:
        logger.debug(f"[ValueEngine] UI event failed: {e}")

    # Store context values for downstream routing and tools.
    _set_context_value(context_variables, "value_manifest", manifest)
    _set_context_value(context_variables, "concept_blueprint", structured_output)
    _set_context_value(context_variables, "concept_overview", concept_overview)
    _set_context_value(context_variables, "api_endpoints", api_endpoints)
    _set_context_value(context_variables, "surface_candidate_hints", surface_candidate_hints)
    _set_context_value(context_variables, "concept_presented", True)

    return {
        "success": True,
        "manifest_id": manifest_id,
        "app_id": str(app_id),
        "message": f"ConceptBlueprint saved for {app_name}",
    }


async def get_value_manifest(
    app_id: Annotated[str, "Application ID"],
    context_variables: Annotated[Optional[Any], "Runtime context"] = None,
) -> Dict[str, Any]:
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
    if _HAS_PERSISTENCE and AG2PersistenceManager:
        try:
            pm = AG2PersistenceManager()
            await pm._ensure_client()
            if pm.client:
                coll = pm.client["autogen_ai_agents"]["ValueManifests"]
                manifest = await coll.find_one({"app_id": str(app_id)})
                if manifest:
                    manifest.pop("_id", None)
                    return {"success": True, "manifest": manifest}
        except Exception as e:
            logger.warning(f"[ValueEngine] Load manifest failed: {e}")

    return {"success": False, "error": f"No manifest found for app_id={app_id}"}
