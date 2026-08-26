from typing import Annotated, Any


async def get_feature_context(
    feature_name: Annotated[str, "Feature to get context for"],
    context_variables: Annotated[Any | None, "Runtime context"] = None,
) -> dict[str, Any]:
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
