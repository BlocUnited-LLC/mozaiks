"""AgentGenerator build lifecycle hooks.

Extends the shared platform build lifecycle with workflow_bundle artifact persistence.
Each workflow that produces a canonical artifact family overrides emit_build_completed
here to also call persist_summary_artifact() for its owned family.
"""

from __future__ import annotations

from typing import Any

from factory_app.workflows._shared.platform.build_lifecycle import (  # noqa: F401
    build_export_download_url,
    emit_build_failed,
    emit_build_started,
    get_build_artifacts,
    runtime_public_base_url,
)
from factory_app.workflows._shared.platform.build_lifecycle import (
    emit_build_completed as _shared_emit_build_completed,
)
from factory_app.workflows._shared.workflow_integration import (
    apply_workflow_integration_context,
    normalize_workflow_integration_metadata,
)


async def _read_build_mode(*, app_id: str, chat_id: str) -> str | None:
    """Read build_mode from the persisted chat session context variables."""
    try:
        from mozaiksai.core.core_config import get_mongo_client
        from mozaiksai.core.data.persistence.namespaces import SYSTEM_DATABASE, RuntimeCollections
        from mozaiksai.core.multitenant import build_app_scope_filter

        client = get_mongo_client()
        coll = client[SYSTEM_DATABASE][RuntimeCollections.CHAT_SESSIONS]
        doc = await coll.find_one(
            {"_id": str(chat_id), **build_app_scope_filter(str(app_id))},
            {"context_variables.build_mode": 1, "build_mode": 1},
        )
        if isinstance(doc, dict):
            raw_top_level = doc.get("build_mode")
            if isinstance(raw_top_level, str) and raw_top_level.strip():
                return raw_top_level.strip().lower()
            ctx = doc.get("context_variables")
            if isinstance(ctx, dict):
                raw = ctx.get("build_mode")
                if isinstance(raw, str):
                    return raw.strip().lower() or None
    except Exception:
        pass
    return None


async def _read_workflow_integration_metadata(*, app_id: str, chat_id: str) -> dict[str, Any] | None:
    """Read normalized workflow integration metadata from persisted chat context."""
    try:
        from mozaiksai.core.core_config import get_mongo_client
        from mozaiksai.core.data.persistence.namespaces import SYSTEM_DATABASE, RuntimeCollections
        from mozaiksai.core.multitenant import build_app_scope_filter

        client = get_mongo_client()
        coll = client[SYSTEM_DATABASE][RuntimeCollections.CHAT_SESSIONS]
        doc = await coll.find_one(
            {"_id": str(chat_id), **build_app_scope_filter(str(app_id))},
            {
                "context_variables.workflow_integration_metadata": 1,
                "context_variables.generated_workflow_name": 1,
                "context_variables.generated_workflow_capability_id": 1,
                "context_variables.generated_workflow_startup_mode": 1,
                "context_variables.generated_workflow_trigger_events": 1,
            },
        )
        if not isinstance(doc, dict):
            return None
        ctx = doc.get("context_variables")
        if not isinstance(ctx, dict):
            return None
        metadata = normalize_workflow_integration_metadata(ctx.get("workflow_integration_metadata"))
        if metadata:
            return metadata
        scratch: dict[str, Any] = {}
        for key in (
            "generated_workflow_name",
            "generated_workflow_capability_id",
            "generated_workflow_startup_mode",
            "generated_workflow_trigger_events",
        ):
            if key in ctx:
                scratch[key] = ctx[key]
        if scratch:
            apply_workflow_integration_context(scratch, {
                "workflow_name": scratch.get("generated_workflow_name"),
                "capability_id": scratch.get("generated_workflow_capability_id"),
                "startup_mode": scratch.get("generated_workflow_startup_mode"),
                "trigger_events": scratch.get("generated_workflow_trigger_events") or [],
                "source": "chat_context",
            })
            return normalize_workflow_integration_metadata(scratch.get("workflow_integration_metadata"))
    except Exception:
        pass
    return None


async def _persist_workflow_bundle_artifact(
    *,
    app_id: str,
    chat_id: str | None,
    user_id: str | None,
    workflow_name: str,
    build_mode: str | None,
    artifact_store: Any | None = None,
    workflow_integration_metadata: dict[str, Any] | None = None,
) -> None:
    """Persist a versioned workflow_bundle summary artifact after AgentGenerator completes.

    Pass ``artifact_store`` to direct artifact writes to a specific store instance
    without touching process-level state.  Pass ``workflow_integration_metadata``
    to supply the payload directly and skip the MongoDB chat-context read.
    """
    from mozaiksai.core.artifacts.summary_artifacts import persist_summary_artifact

    resolved_chat_id = (chat_id or "").strip() or None
    if workflow_integration_metadata is None:
        workflow_integration_metadata = (
            await _read_workflow_integration_metadata(app_id=app_id, chat_id=resolved_chat_id)
            if resolved_chat_id
            else None
        )
    await persist_summary_artifact(
        app_id=app_id,
        artifact_kind="workflow_bundle",
        artifact_key="workflow_bundle",
        summary_payload={
            "source_workflow": workflow_name,
            "source_chat_id": resolved_chat_id,
            "workflow_integration_metadata": workflow_integration_metadata,
        },
        source_workflow=workflow_name,
        source_chat_id=resolved_chat_id,
        author_user_id=(user_id or "").strip() or None,
        revision_mode=build_mode == "revision",
        input_artifact_kinds=("design_docs",),
        artifact_store=artifact_store,
    )


async def emit_build_completed(
    *,
    app_id: str,
    execution_id: str | None = None,
    chat_id: str | None = None,
    user_id: str | None = None,
    workflow_name: str,
    **kwargs: Any,
) -> str | None:
    """Emit build.completed and persist the workflow_bundle summary artifact."""
    outbox_event_id = await _shared_emit_build_completed(
        app_id=app_id,
        execution_id=execution_id,
        chat_id=chat_id,
        user_id=user_id,
        workflow_name=workflow_name,
        **kwargs,
    )

    try:
        build_mode = await _read_build_mode(app_id=app_id, chat_id=chat_id or "")
        await _persist_workflow_bundle_artifact(
            app_id=app_id,
            chat_id=chat_id,
            user_id=user_id,
            workflow_name=workflow_name,
            build_mode=build_mode,
        )
    except Exception as exc:
        from logs.logging_config import get_core_logger
        get_core_logger("agentgenerator_build_lifecycle").warning(
            "[AgentGenerator] workflow_bundle artifact persistence failed: %s", exc
        )

    return outbox_event_id


__all__ = [
    "emit_build_started",
    "emit_build_completed",
    "emit_build_failed",
    "get_build_artifacts",
    "runtime_public_base_url",
    "build_export_download_url",
]
