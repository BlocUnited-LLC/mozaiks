"""Security assessment source ownership over the existing Factory build binding."""

from __future__ import annotations

from typing import Any

from factory_app.workflows._shared.platform.current_build import resolve_current_build_artifact
from mozaiksai.core.artifacts.models import BuildRecord
from mozaiksai.core.session.build_binding import RunBuildBinding
from mozaiksai.core.workflow.agents.factory import active_workflow_tool_run
from mozaiksai.core.workflow.context.frozen import detach


def context_get(context: Any, key: str, default: Any = None) -> Any:
    return detach(context.get(key, default)) if context is not None else default


def context_set(context: Any, key: str, value: Any) -> None:
    if isinstance(context, dict):
        context[key] = value
    elif context is not None:
        context.set(key, value)


async def resolve_security_artifact(context: Any) -> tuple[RunBuildBinding, BuildRecord]:
    workflow, app_id, _, user_id = active_workflow_tool_run()
    if workflow != "SecurityReadiness":
        raise ValueError("security_source_workflow_mismatch")
    binding, _, artifact = await resolve_current_build_artifact(
        context, app_id=app_id, user_id=user_id, error_prefix="security_source",
    )
    return binding, artifact


def source_failure(context: Any, error: str, diagnostics: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    result = {
        "success": False, "status": "not_assessed", "persisted": False,
        "mode": context_get(context, "security_readiness_mode", "advisory"),
        "checked_file_count": 0, "finding_count": 0, "findings": [],
        "source_error": error, "source_diagnostics": diagnostics or [],
    }
    context_set(context, "security_readiness_findings", [])
    context_set(context, "security_readiness_summary", result)
    context_set(context, "security_readiness_recorded", False)
    return result


def source_exception(context: Any, exc: Exception) -> dict[str, Any]:
    code = str(exc)
    if code.startswith(("security_source_", "artifact_bundle_", "workflow_tool_invocation_")):
        return source_failure(context, code)
    return source_failure(context, "security_source_unavailable", [
        {"code": type(exc).__name__, "blocking": True},
    ])
