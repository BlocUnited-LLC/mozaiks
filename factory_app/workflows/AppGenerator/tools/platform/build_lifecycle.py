"""AppGenerator build lifecycle hooks.

Extends the shared platform build lifecycle with app_bundle artifact persistence.
Each workflow that produces a canonical artifact family overrides emit_build_completed
here to also call persist_summary_artifact() for its owned family.
"""

from __future__ import annotations

from typing import Any, Optional

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


async def _read_build_mode(*, app_id: str, chat_id: str) -> Optional[str]:
    """Read build_mode from the persisted chat session context variables."""
    try:
        from mozaiksai.core.core_config import get_mongo_client
        from mozaiksai.core.multitenant import build_app_scope_filter
        from mozaiksai.core.data.persistence.namespaces import SYSTEM_DATABASE, RuntimeCollections

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


async def _persist_app_bundle_artifact(
    *,
    app_id: str,
    chat_id: Optional[str],
    user_id: Optional[str],
    workflow_name: str,
    build_mode: Optional[str],
) -> None:
    """Persist a versioned app_bundle summary artifact after AppGenerator completes."""
    from mozaiksai.core.artifacts.summary_artifacts import persist_summary_artifact

    resolved_chat_id = (chat_id or "").strip() or None
    await persist_summary_artifact(
        app_id=app_id,
        artifact_kind="app_bundle",
        artifact_key="app_bundle",
        summary_payload={
            "source_workflow": workflow_name,
            "source_chat_id": resolved_chat_id,
        },
        source_workflow=workflow_name,
        source_chat_id=resolved_chat_id,
        author_user_id=(user_id or "").strip() or None,
        revision_mode=build_mode == "revision",
        input_artifact_kinds=("design_docs", "workflow_bundle", "brand"),
    )


async def emit_build_completed(
    *,
    app_id: str,
    execution_id: Optional[str] = None,
    chat_id: Optional[str] = None,
    user_id: Optional[str] = None,
    workflow_name: str,
    **kwargs: Any,
) -> Optional[str]:
    """Emit build.completed and persist the app_bundle summary artifact."""
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
        await _persist_app_bundle_artifact(
            app_id=app_id,
            chat_id=chat_id,
            user_id=user_id,
            workflow_name=workflow_name,
            build_mode=build_mode,
        )
    except Exception as exc:
        from logs.logging_config import get_core_logger
        get_core_logger("appgenerator_build_lifecycle").warning(
            "[AppGenerator] app_bundle artifact persistence failed: %s", exc
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

