"""Factory policy over Studio's registry and the shared session hook contract."""

from __future__ import annotations

from typing import Any

from factory_app.app.modules.app_registry.backend.service import AppRegistryService
from mozaiksai.core.session.build_binding import RunBuildBinding


def require_build_binding(context_variables: Any) -> RunBuildBinding:
    """Read the server-owned binding; never fall back to execution app_id."""
    raw = context_variables.get("run_build_binding") if context_variables is not None else None
    return RunBuildBinding.model_validate(raw)


async def bind_factory_session(
    *,
    app_id: str,
    user_id: str,
    workflow_name: str,
    chat_id: str,
    phase: str,
    trigger_source: str,
    build_registry_id: str | None,
    source_chat_id: str | None,
    session_fields: dict[str, Any],
) -> dict[str, Any]:
    from mozaiksai.core.workflow.workflow_manager import workflow_manager

    config = workflow_manager.get_config(workflow_name) or {}
    definitions = (config.get("context_variables") or {}).get("definitions") or {}
    source = (definitions.get("run_build_binding") or {}).get("source") or {}
    if source.get("type") != "runtime" and not (
        build_registry_id or session_fields.get("run_build_binding") or trigger_source == "refinement"
    ):
        return {}
    binding = await AppRegistryService().resolve_build_binding(
        owner_user_id=user_id,
        app_id=app_id,
        chat_id=chat_id,
        workflow_name=workflow_name,
        build_registry_id=build_registry_id,
        source_chat_id=source_chat_id,
        persisted_binding=session_fields.get("run_build_binding"),
        resume=phase == "resume",
        refinement=trigger_source == "refinement",
        allow_create=workflow_name in {"ValueEngine", "ExistingAppDiscovery"},
    )
    return {"run_build_binding": binding.model_dump()}
