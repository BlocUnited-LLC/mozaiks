from __future__ import annotations

from typing import Any

from mozaiksai.control_plane.app_validation import run_current_app_source_validation
from mozaiksai.control_plane.contracts import ControlPlaneToolContext
from mozaiksai.core.artifacts.store import ArtifactStore

from ._shared import normalize_context


async def run_app_source_validation(
    *,
    allowed_kinds: list[str] | None = None,
    include_install: bool = False,
    max_commands: int = 4,
    timeout_seconds: int = 120,
    confirm_execution: bool = False,
    context: ControlPlaneToolContext | dict[str, Any] | None = None,
    artifact_store: ArtifactStore | None = None,
) -> dict[str, Any]:
    """Run framework-aware validation for the current App Intelligence source."""
    tool_context = normalize_context(context)
    app_id = str(tool_context.app_id or "").strip()
    if not app_id:
        return {
            "present": False,
            "reason": "missing_app_id",
            "validation_status": "skipped",
            "warnings": ["App source validation requires app_id."],
        }

    result = await run_current_app_source_validation(
        app_id=app_id,
        artifact_store=artifact_store,
        allowed_kinds=allowed_kinds,
        include_install=include_install,
        max_commands=max_commands,
        timeout_seconds=timeout_seconds,
        confirm_execution=confirm_execution,
    )
    return {
        "present": True,
        "validation": result.model_dump(mode="json"),
    }


__all__ = ["run_app_source_validation"]
