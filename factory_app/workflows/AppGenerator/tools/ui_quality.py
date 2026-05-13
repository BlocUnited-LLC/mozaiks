"""UI quality gate for AppGenerator schema output.

The save_app_schema tool performs the primitive-level audit and stores warnings
in workflow context. This tool turns those warnings into deterministic routing
state so AG2 handoffs can send noisy UI back to AppSchemaAgent before assembly.
"""

from typing import Annotated, Any, Dict, List, Optional

from autogen.tools.dependency_injection import Field


def _context_get(context_variables: Optional[Any], key: str, default: Any = None) -> Any:
    if context_variables is None:
        return default
    if hasattr(context_variables, "get"):
        try:
            value = context_variables.get(key)
            return default if value is None else value
        except Exception:
            pass
    data = getattr(context_variables, "data", None)
    if isinstance(data, dict):
        return data.get(key, default)
    if isinstance(context_variables, dict):
        return context_variables.get(key, default)
    return default


def _context_set(context_variables: Optional[Any], key: str, value: Any) -> None:
    if context_variables is None:
        return
    if hasattr(context_variables, "set"):
        try:
            context_variables.set(key, value)
            return
        except Exception:
            pass
    data = getattr(context_variables, "data", None)
    if isinstance(data, dict):
        data[key] = value
        return
    if isinstance(context_variables, dict):
        context_variables[key] = value


def _normalize_warnings(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    if isinstance(value, list):
        warnings: List[str] = []
        for item in value:
            if isinstance(item, str):
                text = item.strip()
            else:
                text = str(item).strip()
            if text:
                warnings.append(text)
        return warnings
    return [str(value).strip()] if str(value).strip() else []


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def review_ui_quality(
    max_revision_attempts: Annotated[
        int,
        Field(
            description=(
                "Maximum number of AppSchemaAgent revision loops allowed before "
                "blocking for user/operator review."
            )
        ),
    ] = 2,
    context_variables: Annotated[
        Optional[Any],
        Field(description="AG2-injected workflow context variables."),
    ] = None,
) -> Dict[str, Any]:
    """Convert UI audit warnings into handoff-routing state.

    Returns:
        A deterministic status payload:
        - passed: no warnings remain; AssemblyAgent may continue.
        - needs_revision: warnings remain and another AppSchemaAgent pass is allowed.
        - blocked: warnings remain after the allowed revision attempts.
    """

    warnings = _normalize_warnings(
        _context_get(context_variables, "app_ui_quality_warnings", [])
    )
    app_schema_ready = bool(_context_get(context_variables, "app_schema_ready", False))
    app_manifest = _context_get(context_variables, "app_manifest")
    app_pages = _context_get(context_variables, "app_pages", [])
    custom_route_bundle = _context_get(context_variables, "app_custom_route_bundle")
    has_declarative_pages = isinstance(app_pages, list) and len(app_pages) > 0
    has_custom_routes = isinstance(custom_route_bundle, dict) and bool(
        custom_route_bundle.get("route_manifest")
        or custom_route_bundle.get("page_files")
    )

    if not app_schema_ready:
        warnings.append("AppSchemaAgent did not persist the app schema via save_app_schema.")
    if not isinstance(app_manifest, dict) or not app_manifest:
        warnings.append("AppSchemaAgent did not persist app_manifest.")
    if not has_declarative_pages and not has_custom_routes:
        warnings.append("AppSchemaAgent did not persist any declarative pages or custom routes.")

    prior_attempts = _as_int(
        _context_get(context_variables, "app_ui_quality_revision_count", 0), 0
    )
    max_attempts = max(0, _as_int(max_revision_attempts, 2))

    if not warnings:
        status = "passed"
        revision_count = prior_attempts
        revision_request = None
    elif prior_attempts < max_attempts:
        status = "needs_revision"
        revision_count = prior_attempts + 1
        revision_request = (
            "Revise the app schema before assembly. Remove or simplify these UI "
            "quality issues, then emit the full AppSchemaOutput again:\n- "
            + "\n- ".join(warnings)
        )
    else:
        status = "blocked"
        revision_count = prior_attempts
        revision_request = (
            "UI quality warnings remain after the allowed automated revision "
            "attempts. User/operator review is required before assembly:\n- "
            + "\n- ".join(warnings)
        )

    result = {
        "status": status,
        "warnings": warnings,
        "revision_count": revision_count,
        "max_revision_attempts": max_attempts,
        "revision_request": revision_request,
    }

    _context_set(context_variables, "app_ui_quality_status", status)
    _context_set(context_variables, "app_ui_quality_warnings", warnings)
    _context_set(context_variables, "app_ui_quality_revision_count", revision_count)
    _context_set(context_variables, "app_ui_quality_revision_request", revision_request)
    _context_set(context_variables, "app_ui_quality_result", result)

    return result


__all__ = ["review_ui_quality"]
