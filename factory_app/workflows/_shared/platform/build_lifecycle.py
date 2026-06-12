
import asyncio
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from logs.logging_config import get_core_logger

logger = get_core_logger("platform_build_lifecycle")


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


async def mark_attempt(**kwargs: Any) -> None:
    from factory_app.workflows.AppGenerator.tools.platform.build_events_outbox import (
        mark_attempt as _mark_attempt,
    )

    await _mark_attempt(**kwargs)


async def get_outbox_event(**kwargs: Any) -> Optional[Dict[str, Any]]:
    from factory_app.workflows.AppGenerator.tools.platform.build_events_outbox import (
        get_outbox_event as _get_outbox_event,
    )

    return await _get_outbox_event(**kwargs)


async def upsert_outbox_event(**kwargs: Any) -> str:
    from factory_app.workflows.AppGenerator.tools.platform.build_events_outbox import (
        upsert_outbox_event as _upsert_outbox_event,
    )

    return await _upsert_outbox_event(**kwargs)


def _build_events_client():
    from factory_app.workflows.AppGenerator.tools.platform.build_events_client import (
        BuildEventsClient,
    )

    return BuildEventsClient()


def get_mongo_client():
    from mozaiksai.core.core_config import get_mongo_client as _get_mongo_client

    return _get_mongo_client()


def build_app_scope_filter(app_id: str) -> Dict[str, Any]:
    from mozaiksai.core.multitenant import build_app_scope_filter as _build_app_scope_filter

    return _build_app_scope_filter(app_id)


def coalesce_app_id(*, app_id: Optional[str] = None) -> Optional[str]:
    from mozaiksai.core.multitenant import coalesce_app_id as _coalesce_app_id

    return _coalesce_app_id(app_id=app_id)


def load_global_pack_graph():
    from mozaiksai.core.workflow.pack.config import load_global_pack_graph as _load_global_pack_graph

    return _load_global_pack_graph()


def get_workflow_sequence(pack: Any, journey_id: str):
    from mozaiksai.core.workflow.pack.config import get_workflow_sequence as _get_workflow_sequence

    return _get_workflow_sequence(pack, journey_id)


def normalize_step_groups(steps: Any):
    from mozaiksai.core.workflow.pack.config import normalize_step_groups as _normalize_step_groups

    return _normalize_step_groups(steps)


def _runtime_chat_sessions_collection():
    from mozaiksai.core.data.persistence.namespaces import (
        SYSTEM_DATABASE,
        RuntimeCollections,
    )

    return SYSTEM_DATABASE, RuntimeCollections.CHAT_SESSIONS


def _normalize_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value or "").strip()
    return text or None


def _env_csv_set(name: str, default_csv: str) -> set[str]:
    raw = os.getenv(name)
    if raw is None:
        raw = default_csv
    items = [p.strip() for p in str(raw or "").split(",")]
    return {p.lower() for p in items if p}


def build_workflow_names() -> set[str]:
    # Default to the workflow that produces the deployable app bundle.
    return _env_csv_set("MOZAIKS_BUILD_WORKFLOW_NAMES", "AppGenerator")


def is_build_workflow(workflow_name: str) -> bool:
    wf = str(workflow_name or "").strip().lower()
    if not wf:
        return False
    return wf in build_workflow_names()


def runtime_public_base_url() -> str:
    # Used to build absolute artifact URLs in platform callbacks.
    for key in (
        "MOZAIKS_RUNTIME_PUBLIC_BASE_URL",
        "RUNTIME_PUBLIC_BASE_URL",
        "PUBLIC_RUNTIME_BASE_URL",
    ):
        raw = os.getenv(key)
        if raw and str(raw).strip():
            return str(raw).strip().rstrip("/")
    return ""


def build_export_download_url(*, app_id: str, build_id: str) -> str:
    path = f"/api/apps/{app_id}/builds/{build_id}/export"
    base = runtime_public_base_url()
    return f"{base}{path}" if base else path


async def _get_chat_session_context(*, app_id: str, chat_id: str) -> Dict[str, Any]:
    resolved_app_id = coalesce_app_id(app_id=app_id)
    resolved_chat_id = _normalize_text(chat_id)
    if not resolved_app_id or not resolved_chat_id:
        return {}

    client = get_mongo_client()
    system_database, chat_sessions_collection = _runtime_chat_sessions_collection()
    coll = client[system_database][chat_sessions_collection]
    doc = await coll.find_one(
        {"_id": str(resolved_chat_id), **build_app_scope_filter(str(resolved_app_id))},
        {
            "build_registry_id": 1,
            "journey_instance_id": 1,
            "journey_key": 1,
            "journey_position": 1,
            "journey_total_steps": 1,
            "last_artifact": 1,
        },
    )
    return dict(doc) if isinstance(doc, dict) else {}


async def _get_last_artifact_payload(*, app_id: str, chat_id: str) -> Optional[Dict[str, Any]]:
    doc = await _get_chat_session_context(app_id=app_id, chat_id=chat_id)
    last_artifact = doc.get("last_artifact")
    if not isinstance(last_artifact, dict):
        return None
    payload = last_artifact.get("payload")
    return payload if isinstance(payload, dict) else None


def _extract_preview_url(payload: Dict[str, Any]) -> Optional[str]:
    for key in ("previewUrl", "preview_url", "app_validation_preview_url"):
        raw = payload.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()

    nested = payload.get("app_validation_result") or payload.get("app_validation")
    if isinstance(nested, dict):
        for key in ("previewUrl", "preview_url"):
            raw = nested.get(key)
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
    return None


async def get_build_artifacts(
    *,
    app_id: str,
    build_id: str,
    chat_id: Optional[str] = None,
    export_build_id: Optional[str] = None,
) -> Dict[str, Optional[str]]:
    payload = None
    try:
        artifact_chat_id = _normalize_text(chat_id) or str(build_id)
        payload = await _get_last_artifact_payload(app_id=app_id, chat_id=artifact_chat_id)
    except Exception:
        payload = None

    preview_url = _extract_preview_url(payload) if isinstance(payload, dict) else None
    export_url = build_export_download_url(
        app_id=str(app_id),
        build_id=str(_normalize_text(export_build_id) or build_id),
    )
    return {"previewUrl": preview_url, "exportDownloadUrl": export_url}


def _idempotency_key(*, app_id: str, build_id: str, event_type: str) -> str:
    return f"build:{app_id}:{build_id}:{event_type}"


def _get_journey_workflow_groups(journey_id: Optional[str]) -> list[tuple[int, list[str]]]:
    normalized_journey_id = _normalize_text(journey_id)
    if not normalized_journey_id:
        return []

    pack = load_global_pack_graph()
    if pack is None:
        return []

    journey = get_workflow_sequence(pack, normalized_journey_id)
    if journey is None:
        return []

    return [
        (index, group)
        for index, group in enumerate(normalize_step_groups(journey.steps))
        if group
    ]


def _should_emit_build_started(*, workflow_name: str, journey_key: Optional[str], journey_position: Optional[int]) -> bool:
    workflow = _normalize_text(workflow_name)
    if not workflow:
        return False

    groups = _get_journey_workflow_groups(journey_key)
    if groups:
        first_index, first_group = groups[0]
        return journey_position == first_index and workflow in first_group
    return is_build_workflow(workflow)


def _should_emit_build_completed(*, workflow_name: str, journey_key: Optional[str], journey_position: Optional[int]) -> bool:
    workflow = _normalize_text(workflow_name)
    if not workflow:
        return False

    groups = _get_journey_workflow_groups(journey_key)
    if groups:
        last_index, last_group = groups[-1]
        return journey_position == last_index and workflow in last_group
    return is_build_workflow(workflow)


def _should_emit_build_failed(*, workflow_name: str, journey_key: Optional[str]) -> bool:
    workflow = _normalize_text(workflow_name)
    if not workflow:
        return False

    groups = _get_journey_workflow_groups(journey_key)
    if groups:
        return any(workflow in group for _, group in groups)
    return is_build_workflow(workflow)


async def _resolve_build_event_context(
    *,
    app_id: str,
    execution_id: Optional[str],
    chat_id: Optional[str],
    build_id: Optional[str] = None,
    build_registry_id: Optional[str] = None,
    journey_instance_id: Optional[str] = None,
    journey_key: Optional[str] = None,
    journey_position: Optional[int] = None,
    journey_total_steps: Optional[int] = None,
) -> Dict[str, Any]:
    resolved_app_id = _normalize_text(app_id)
    if not resolved_app_id:
        raise ValueError("app_id is required")

    resolved_chat_id = _normalize_text(chat_id)
    session_ctx = {}
    if resolved_chat_id:
        try:
            session_ctx = await _get_chat_session_context(
                app_id=str(resolved_app_id),
                chat_id=str(resolved_chat_id),
            )
        except Exception:
            session_ctx = {}

    resolved_build_id = (
        _normalize_text(build_id)
        or _normalize_text(session_ctx.get("journey_instance_id"))
        or _normalize_text(resolved_chat_id)
        or str(uuid.uuid4())
    )
    resolved_build_registry_id = (
        _normalize_text(build_registry_id)
        or _normalize_text(session_ctx.get("build_registry_id"))
        or resolved_build_id
    )

    resolved_journey_instance_id = (
        _normalize_text(journey_instance_id)
        or _normalize_text(session_ctx.get("journey_instance_id"))
        or resolved_build_id
    )
    resolved_journey_key = (
        _normalize_text(journey_key)
        or _normalize_text(session_ctx.get("journey_key"))
    )
    resolved_journey_position = journey_position
    if resolved_journey_position is None:
        raw_position = session_ctx.get("journey_position")
        if raw_position is not None:
            try:
                resolved_journey_position = int(raw_position)
            except Exception:
                resolved_journey_position = None
    resolved_total_steps = journey_total_steps
    if resolved_total_steps is None:
        raw_total = session_ctx.get("journey_total_steps")
        if raw_total is not None:
            try:
                resolved_total_steps = int(raw_total)
            except Exception:
                resolved_total_steps = None

    return {
        "app_id": str(resolved_app_id),
        "build_id": str(resolved_build_id),
        "build_registry_id": str(resolved_build_registry_id),
        "journey_instance_id": str(resolved_journey_instance_id),
        "journey_key": resolved_journey_key,
        "journey_position": resolved_journey_position,
        "journey_total_steps": resolved_total_steps,
        "chat_id": resolved_chat_id,
        "execution_id": _normalize_text(execution_id) or str(uuid.uuid4()),
    }


def _base_payload(
    *,
    context: Dict[str, Any],
    workflow_name: str,
    user_id: Optional[str],
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "appId": context["app_id"],
        "buildId": context["build_id"],
        "buildRegistryId": context["build_registry_id"],
        "journeyId": context.get("journey_key"),
        "journeyInstanceId": context["journey_instance_id"],
        "journeyPosition": context.get("journey_position"),
        "journeyTotalSteps": context.get("journey_total_steps"),
        "chatId": context.get("chat_id"),
        "executionId": context["execution_id"],
        "workflowName": workflow_name,
        "userId": _normalize_text(user_id),
        "timestamp": _utc_iso(),
    }
    return payload


async def _deliver_outbox_event_once(*, outbox_event_id: str) -> None:
    doc = await get_outbox_event(outbox_id=outbox_event_id)
    if not isinstance(doc, dict):
        return

    app_id = _normalize_text(doc.get("app_id"))
    payload = doc.get("payload")
    if not app_id or not isinstance(payload, dict):
        return

    try:
        client = _build_events_client()
        result = await client.post_build_event(app_id=app_id, payload=payload)
        await mark_attempt(
            outbox_id=outbox_event_id,
            ok=result.ok,
            status_code=result.status_code,
            error=result.error,
        )
    except Exception as exc:
        logger.exception("Failed to deliver build lifecycle outbox event %s", outbox_event_id)
        await mark_attempt(outbox_id=outbox_event_id, ok=False, error=str(exc))


def _spawn_delivery(*, outbox_event_id: str) -> None:
    async def _deliver() -> None:
        await _deliver_outbox_event_once(outbox_event_id=outbox_event_id)

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_deliver())
    except RuntimeError:
        return


async def emit_build_started(
    *,
    app_id: str,
    execution_id: Optional[str] = None,
    chat_id: Optional[str] = None,
    user_id: Optional[str] = None,
    workflow_name: str,
    build_id: Optional[str] = None,
    build_registry_id: Optional[str] = None,
    journey_instance_id: Optional[str] = None,
    journey_key: Optional[str] = None,
    journey_position: Optional[int] = None,
    journey_total_steps: Optional[int] = None,
    **_: Any,
) -> Optional[str]:
    context = await _resolve_build_event_context(
        app_id=app_id,
        execution_id=execution_id,
        chat_id=chat_id,
        build_id=build_id,
        build_registry_id=build_registry_id,
        journey_instance_id=journey_instance_id,
        journey_key=journey_key,
        journey_position=journey_position,
        journey_total_steps=journey_total_steps,
    )
    if not _should_emit_build_started(
        workflow_name=workflow_name,
        journey_key=context.get("journey_key"),
        journey_position=context.get("journey_position"),
    ):
        return None

    payload = _base_payload(context=context, workflow_name=workflow_name, user_id=user_id)
    outbox_event_id = await upsert_outbox_event(
        app_id=context["app_id"],
        build_id=context["build_id"],
        event_type="build.started",
        status="started",
        user_id=user_id,
        workflow_name=workflow_name,
        idempotency_key=_idempotency_key(
            app_id=context["app_id"],
            build_id=context["build_id"],
            event_type="build.started",
        ),
        payload=payload,
    )
    _spawn_delivery(outbox_event_id=outbox_event_id)
    return outbox_event_id


async def emit_build_completed(
    *,
    app_id: str,
    execution_id: Optional[str] = None,
    chat_id: Optional[str] = None,
    user_id: Optional[str] = None,
    workflow_name: str,
    build_id: Optional[str] = None,
    build_registry_id: Optional[str] = None,
    journey_instance_id: Optional[str] = None,
    journey_key: Optional[str] = None,
    journey_position: Optional[int] = None,
    journey_total_steps: Optional[int] = None,
    **_: Any,
) -> Optional[str]:
    context = await _resolve_build_event_context(
        app_id=app_id,
        execution_id=execution_id,
        chat_id=chat_id,
        build_id=build_id,
        build_registry_id=build_registry_id,
        journey_instance_id=journey_instance_id,
        journey_key=journey_key,
        journey_position=journey_position,
        journey_total_steps=journey_total_steps,
    )
    if not _should_emit_build_completed(
        workflow_name=workflow_name,
        journey_key=context.get("journey_key"),
        journey_position=context.get("journey_position"),
    ):
        return None

    payload = _base_payload(context=context, workflow_name=workflow_name, user_id=user_id)
    payload["artifacts"] = await get_build_artifacts(
        app_id=context["app_id"],
        build_id=context["build_id"],
        chat_id=context.get("chat_id"),
        export_build_id=context.get("build_registry_id"),
    )
    outbox_event_id = await upsert_outbox_event(
        app_id=context["app_id"],
        build_id=context["build_id"],
        event_type="build.completed",
        status="completed",
        user_id=user_id,
        workflow_name=workflow_name,
        idempotency_key=_idempotency_key(
            app_id=context["app_id"],
            build_id=context["build_id"],
            event_type="build.completed",
        ),
        payload=payload,
    )
    _spawn_delivery(outbox_event_id=outbox_event_id)

    # Opt-in anonymized OSS telemetry — fire-and-forget, never raises.
    try:
        from mozaiksai.core.telemetry import build_workflow_payload, emit_build_telemetry
        _telemetry_payload = build_workflow_payload(
            workflow_name=workflow_name,
            final_status="completed",
            build_registry_id=context.get("build_registry_id", ""),
        )
        asyncio.create_task(emit_build_telemetry(_telemetry_payload))
    except Exception:
        pass

    return outbox_event_id


async def emit_build_failed(
    *,
    app_id: str,
    execution_id: Optional[str] = None,
    chat_id: Optional[str] = None,
    user_id: Optional[str] = None,
    workflow_name: str,
    error: Optional[str] = None,
    build_id: Optional[str] = None,
    build_registry_id: Optional[str] = None,
    journey_instance_id: Optional[str] = None,
    journey_key: Optional[str] = None,
    journey_position: Optional[int] = None,
    journey_total_steps: Optional[int] = None,
    **_: Any,
) -> Optional[str]:
    context = await _resolve_build_event_context(
        app_id=app_id,
        execution_id=execution_id,
        chat_id=chat_id,
        build_id=build_id,
        build_registry_id=build_registry_id,
        journey_instance_id=journey_instance_id,
        journey_key=journey_key,
        journey_position=journey_position,
        journey_total_steps=journey_total_steps,
    )
    if not _should_emit_build_failed(
        workflow_name=workflow_name,
        journey_key=context.get("journey_key"),
    ):
        return None

    payload = _base_payload(context=context, workflow_name=workflow_name, user_id=user_id)
    payload["error"] = _normalize_text(error)
    outbox_event_id = await upsert_outbox_event(
        app_id=context["app_id"],
        build_id=context["build_id"],
        event_type="build.failed",
        status="failed",
        user_id=user_id,
        workflow_name=workflow_name,
        idempotency_key=_idempotency_key(
            app_id=context["app_id"],
            build_id=context["build_id"],
            event_type="build.failed",
        ),
        payload=payload,
    )
    _spawn_delivery(outbox_event_id=outbox_event_id)

    # Opt-in anonymized OSS telemetry — fire-and-forget, never raises.
    try:
        from mozaiksai.core.telemetry import build_workflow_payload, emit_build_telemetry
        _telemetry_payload = build_workflow_payload(
            workflow_name=workflow_name,
            final_status="failed",
            build_registry_id=context.get("build_registry_id", ""),
        )
        asyncio.create_task(emit_build_telemetry(_telemetry_payload))
    except Exception:
        pass

    return outbox_event_id


__all__ = [
    "emit_build_started",
    "emit_build_completed",
    "emit_build_failed",
    "get_build_artifacts",
    "runtime_public_base_url",
    "build_export_download_url",
]
