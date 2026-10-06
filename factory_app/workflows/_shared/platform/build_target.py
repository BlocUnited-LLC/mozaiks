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

    if phase == "route":
        if not build_registry_id:
            raise ValueError("A registered build target is required for routing")
        record = (await AppRegistryService().get_app_record(
            owner_user_id=user_id, build_registry_id=build_registry_id,
        )).get("app")
        if not record or record.get("chat_app_id") != app_id:
            raise ValueError("Registered build target is not available in this host")
        return {"target_app_id": record["app_id"]}

    config = workflow_manager.get_config(workflow_name) or {}
    definitions = (config.get("context_variables") or {}).get("definitions") or {}
    source = (definitions.get("run_build_binding") or {}).get("source") or {}
    if source.get("type") == "runtime" and not (
        build_registry_id or session_fields.get("run_build_binding") or source_chat_id or trigger_source == "refinement"
    ):
        raise ValueError("A registered build target is required for this workflow")
    if source.get("type") != "runtime" and not (
        build_registry_id or session_fields.get("run_build_binding") or trigger_source == "refinement"
    ):
        return {}
    registry = AppRegistryService()
    binding = await registry.resolve_build_binding(
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
    fields: dict[str, Any] = {"run_build_binding": binding.model_dump()}
    if workflow_name == "AppReview" and phase == "prepare":
        from factory_app.workflows.AppReview.tools.review_context import (
            load_registered_review_context,
        )

        fields.update(await load_registered_review_context(
            binding=binding, app_id=app_id, user_id=user_id, registry=registry,
        ))
    return fields
