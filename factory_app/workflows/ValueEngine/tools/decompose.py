"""
Build Plan Tool - Save and retrieve the build plan.

The approved concept may later be compiled into a BuildPlan by an LLM-driven
planning step or orchestration layer. This tool only persists the already
reasoned plan - NO inference or heuristic logic.
"""

from typing import Annotated, Any, Dict, List, Optional
import logging

logger = logging.getLogger(__name__)

from mozaiksai.core.artifacts import persist_summary_artifact

# Use mozaiks runtime persistence
try:
    from mozaiksai.core.data.persistence.artifact_store import BuilderArtifactStore
    _HAS_PERSISTENCE = True
except ImportError:
    _HAS_PERSISTENCE = False
    BuilderArtifactStore = None


def _normalize_object_list(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    items: List[Dict[str, Any]] = []
    for entry in value:
        if isinstance(entry, dict):
            items.append(dict(entry))
    return items


def _set_context_value(context_variables: Any, key: str, value: Any) -> None:
    if context_variables is None:
        return
    if hasattr(context_variables, "set"):
        context_variables.set(key, value)
        return
    if hasattr(context_variables, "__setitem__"):
        context_variables[key] = value


async def save_build_plan(
    tasks: Annotated[List[Dict[str, Any]], "List of BuildTaskSpec objects from LLM"],
    capability_packs: Annotated[
        Optional[List[Dict[str, Any]]],
        "Optional list of CapabilityPackSpec objects already selected for the app",
    ] = None,
    required_inputs: Annotated[Optional[List[str]], "User inputs needed before build"] = None,
    context_variables: Annotated[Optional[Any], "Runtime context"] = None,
) -> Dict[str, Any]:
    """
    Save the BuildPlan produced after concept approval.

    The LLM has already reasoned about:
    - Which capability packs define the product
    - Which features need AI workflows vs app modules
    - Task dependencies
    - Required user inputs

    This tool just saves it. No heuristics.

    Returns:
        {
            "success": bool,
            "build_plan_id": str,
            "message": str
        }
    """
    # Extract context
    app_id = None
    manifest = None
    chat_id = None
    user_id = None
    build_mode = None
    workflow_name = "ValueEngine"

    if context_variables and hasattr(context_variables, "get"):
        app_id = context_variables.get("app_id")
        manifest = context_variables.get("value_manifest")
        chat_id = context_variables.get("chat_id")
        user_id = context_variables.get("user_id")
        build_mode = context_variables.get("build_mode")
        workflow_name = context_variables.get("workflow_name", "ValueEngine")

    if not app_id:
        return {"success": False, "error": "app_id required in context"}

    if not manifest:
        return {"success": False, "error": "No value_manifest in context. Call save_value_manifest first."}

    if not tasks:
        return {"success": False, "error": "No tasks provided. Nothing to build."}

    build_plan = {
        "build_plan_id": f"plan_{app_id}",
        "app_id": str(app_id),
        "app_name": manifest.get("app_name", "App"),
        "concept_blueprint": dict(manifest),
        "capability_packs": _normalize_object_list(capability_packs),
        "surface_candidate_hints": _normalize_object_list(
            manifest.get("surface_candidate_hints", [])
        ),
        "tasks": tasks,
        "required_inputs": required_inputs or [],
        "total_tasks": len(tasks),
    }

    # Persist to MongoDB via mozaiks runtime
    if _HAS_PERSISTENCE and BuilderArtifactStore:
        try:
            store = BuilderArtifactStore()
            await store.save_build_plan(app_id=str(app_id), build_plan=build_plan)
            logger.info(f"[ValueEngine] BuildPlan saved for app_id={app_id}")
        except Exception as e:
            logger.warning(f"[ValueEngine] Persistence failed: {e}")

    try:
        await persist_summary_artifact(
            app_id=str(app_id),
            artifact_kind="build_plan",
            artifact_key="build_plan",
            summary_payload=build_plan,
            source_workflow=str(workflow_name or "ValueEngine"),
            source_chat_id=str(chat_id) if chat_id else None,
            author_user_id=str(user_id) if user_id else None,
            revision_mode=str(build_mode or "").strip().lower() == "revision",
            input_artifact_kinds=("concept",),
        )
    except Exception as exc:
        logger.warning("[ValueEngine] Generic build-plan artifact persistence failed: %s", exc)

    # Store in context for downstream build sequence/task-batch consumers.
    _set_context_value(context_variables, "build_plan", build_plan)
    _set_context_value(context_variables, "capability_packs", build_plan["capability_packs"])
    _set_context_value(
        context_variables,
        "surface_candidate_hints",
        build_plan["surface_candidate_hints"],
    )

    return {
        "success": True,
        "build_plan_id": build_plan["build_plan_id"],
        "message": (
            f"BuildPlan saved: {len(tasks)} tasks across "
            f"{len(build_plan['capability_packs'])} capability packs"
        ),
    }


async def get_build_plan(
    app_id: Annotated[str, "Application ID"],
    context_variables: Annotated[Optional[Any], "Runtime context"] = None,
) -> Dict[str, Any]:
    """
    Retrieve the BuildPlan for an app.

    Used by the build sequence to load the planned generator tasks.
    """
    # Check context first
    if context_variables and hasattr(context_variables, "get"):
        cached = context_variables.get("build_plan")
        if cached and cached.get("app_id") == str(app_id):
            return {"success": True, "build_plan": cached}

    # Load from MongoDB
    if _HAS_PERSISTENCE and BuilderArtifactStore:
        try:
            store = BuilderArtifactStore()
            plan = await store.get_build_plan(app_id=str(app_id))
            if plan:
                return {"success": True, "build_plan": plan}
        except Exception as e:
            logger.warning(f"[ValueEngine] Load build plan failed: {e}")

    return {"success": False, "error": f"No build plan found for app_id={app_id}"}


async def get_feature_context(
    feature_name: Annotated[str, "Feature to get context for"],
    context_variables: Annotated[Optional[Any], "Runtime context"] = None,
) -> Dict[str, Any]:
    """
    Get context for a specific feature from the manifest.

    Used by downstream generator workflows to understand
    what they're building.
    """
    manifest = None
    if context_variables and hasattr(context_variables, "get"):
        manifest = context_variables.get("value_manifest")

    if not manifest:
        return {"success": False, "error": "No manifest in context"}

    # Find relevant endpoints for this feature
    api_endpoints = manifest.get("api_endpoints", [])
    feature_endpoints = [
        ep for ep in api_endpoints
        if feature_name.lower() in ep.get("path", "").lower()
        or feature_name.lower() in ep.get("description", "").lower()
    ]

    return {
        "success": True,
        "feature_name": feature_name,
        "app_name": manifest.get("app_name"),
        "value_proposition": manifest.get("value_proposition"),
        "target_users": manifest.get("target_users", []),
        "constraints": manifest.get("constraints", []),
        "relevant_endpoints": feature_endpoints,
    }
