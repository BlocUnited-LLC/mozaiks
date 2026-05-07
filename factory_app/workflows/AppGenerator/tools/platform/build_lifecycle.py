
import asyncio
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from mozaiksai.core.core_config import get_mongo_client
from mozaiksai.core.data.persistence.namespaces import SYSTEM_DATABASE, RuntimeCollections
from mozaiksai.core.multitenant import build_app_scope_filter, coalesce_app_id
from mozaiksai.core.workflow.pack.config import get_workflow_sequence, load_global_pack_graph, normalize_step_groups
from logs.logging_config import get_core_logger

from .build_events_client import BuildEventsClient, _utc_iso
from .build_events_outbox import mark_attempt, upsert_outbox_event

logger = get_core_logger("platform_build_lifecycle")


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
    coll = client[SYSTEM_DATABASE][RuntimeCollections.CHAT_SESSIONS]
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

    # Nested common shapes from AppGenerator validation tools.
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
    execution_id: str,
    chat_id: Optional[str] = None,
) -> Dict[str, Any]:
    resolved_app_id = coalesce_app_id(app_id=app_id)
    if not resolved_app_id:
        return {}

    resolved_execution_id = _normalize_text(execution_id)
    resolved_chat_id = _normalize_text(chat_id) or resolved_execution_id
    session_doc = await _get_chat_session_context(
        app_id=str(resolved_app_id),
        chat_id=str(resolved_chat_id or ""),
    )

    build_registry_id = _normalize_text(session_doc.get("build_registry_id"))
    journey_instance_id = _normalize_text(session_doc.get("journey_instance_id"))
    journey_key = _normalize_text(session_doc.get("journey_key"))

    journey_position_raw = session_doc.get("journey_position")
    try:
        journey_position = int(journey_position_raw) if journey_position_raw is not None else None
    except Exception:
        journey_position = None

    build_id = journey_instance_id or build_registry_id or resolved_execution_id
    return {
        "app_id": str(resolved_app_id),
        "build_id": build_id,
        "build_registry_id": build_registry_id,
        "journey_instance_id": journey_instance_id,
        "journey_key": journey_key,
        "journey_position": journey_position,
        "chat_id": resolved_chat_id,
        "execution_id": resolved_execution_id,
    }


async def _deliver_now(*, outbox_id: str, app_id: str, payload: Dict[str, Any]) -> None:
    try:
        client = BuildEventsClient()
        result = await client.post_build_event(app_id=app_id, payload=payload)
        await mark_attempt(
            outbox_id=outbox_id,
            ok=result.ok,
            status_code=result.status_code,
            error=result.error,
        )
    except Exception as exc:  # pragma: no cover
        try:
            await mark_attempt(outbox_id=outbox_id, ok=False, error=str(exc))
        except Exception:
            pass


def _spawn_delivery(outbox_id: str, app_id: str, payload: Dict[str, Any]) -> None:
    try:
        asyncio.create_task(_deliver_now(outbox_id=outbox_id, app_id=app_id, payload=payload))
    except Exception:
        pass


async def emit_build_started(
    *,
    app_id: str,
    execution_id: str,
    chat_id: Optional[str] = None,
    user_id: Optional[str],
    workflow_name: str,
) -> None:
    try:
        context = await _resolve_build_event_context(
            app_id=app_id,
            execution_id=execution_id,
            chat_id=chat_id,
        )
        resolved_app_id = context.get("app_id")
        bid = _normalize_text(context.get("build_id"))
        if not resolved_app_id or not bid:
            return
        if not _should_emit_build_started(
            workflow_name=workflow_name,
            journey_key=context.get("journey_key"),
            journey_position=context.get("journey_position"),
        ):
            return

        payload: Dict[str, Any] = {
            "event_type": "build_started",
            "appId": str(resolved_app_id),
            "buildId": bid,
            "status": "building",
            "workflowName": str(workflow_name),
            "chatId": context.get("chat_id"),
            "executionId": context.get("execution_id"),
            "eventId": uuid.uuid4().hex,
            "ts": _utc_iso(),
            "idempotencyKey": _idempotency_key(
                app_id=str(resolved_app_id),
                build_id=bid,
                event_type="build_started",
            ),
        }
        if context.get("journey_key"):
            payload["journeyId"] = context["journey_key"]
        if context.get("journey_instance_id"):
            payload["journeyInstanceId"] = context["journey_instance_id"]
        if context.get("build_registry_id"):
            payload["buildRegistryId"] = context["build_registry_id"]

        outbox_id = await upsert_outbox_event(
            app_id=str(resolved_app_id),
            build_id=bid,
            event_type="build_started",
            status="building",
            payload=payload,
            user_id=user_id,
            workflow_name=workflow_name,
            idempotency_key=payload.get("idempotencyKey"),
        )
        _spawn_delivery(outbox_id, str(resolved_app_id), payload)
    except Exception as exc:  # pragma: no cover
        logger.debug("build_started notify skipped: %s", exc)


async def emit_build_completed(
    *,
    app_id: str,
    execution_id: str,
    chat_id: Optional[str] = None,
    user_id: Optional[str],
    workflow_name: str,
) -> None:
    try:
        context = await _resolve_build_event_context(
            app_id=app_id,
            execution_id=execution_id,
            chat_id=chat_id,
        )
        resolved_app_id = context.get("app_id")
        bid = _normalize_text(context.get("build_id"))
        if not resolved_app_id or not bid:
            return
        if not _should_emit_build_completed(
            workflow_name=workflow_name,
            journey_key=context.get("journey_key"),
            journey_position=context.get("journey_position"),
        ):
            return

        artifacts = await get_build_artifacts(
            app_id=str(resolved_app_id),
            build_id=bid,
            chat_id=context.get("chat_id"),
            export_build_id=context.get("build_registry_id") or bid,
        )
        payload: Dict[str, Any] = {
            "event_type": "build_completed",
            "appId": str(resolved_app_id),
            "buildId": bid,
            "status": "built",
            "completedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "artifacts": artifacts,
            "workflowName": str(workflow_name),
            "chatId": context.get("chat_id"),
            "executionId": context.get("execution_id"),
            "eventId": uuid.uuid4().hex,
            "ts": _utc_iso(),
            "idempotencyKey": _idempotency_key(
                app_id=str(resolved_app_id),
                build_id=bid,
                event_type="build_completed",
            ),
        }
        if context.get("journey_key"):
            payload["journeyId"] = context["journey_key"]
        if context.get("journey_instance_id"):
            payload["journeyInstanceId"] = context["journey_instance_id"]
        if context.get("build_registry_id"):
            payload["buildRegistryId"] = context["build_registry_id"]

        outbox_id = await upsert_outbox_event(
            app_id=str(resolved_app_id),
            build_id=bid,
            event_type="build_completed",
            status="built",
            payload=payload,
            user_id=user_id,
            workflow_name=workflow_name,
            idempotency_key=payload.get("idempotencyKey"),
        )
        _spawn_delivery(outbox_id, str(resolved_app_id), payload)
    except Exception as exc:  # pragma: no cover
        logger.debug("build_completed notify skipped: %s", exc)


async def emit_build_failed(
    *,
    app_id: str,
    execution_id: str,
    chat_id: Optional[str] = None,
    user_id: Optional[str],
    workflow_name: str,
    message: str,
    details: Optional[str] = None,
) -> None:
    try:
        context = await _resolve_build_event_context(
            app_id=app_id,
            execution_id=execution_id,
            chat_id=chat_id,
        )
        resolved_app_id = context.get("app_id")
        bid = _normalize_text(context.get("build_id"))
        if not resolved_app_id or not bid:
            return
        if not _should_emit_build_failed(
            workflow_name=workflow_name,
            journey_key=context.get("journey_key"),
        ):
            return

        err: Dict[str, Any] = {"message": str(message or "Build failed")[:1000]}
        if details and isinstance(details, str):
            err["details"] = details[:4000]

        # Best-effort: include whatever artifacts exist at the time of failure.
        try:
            artifacts = await get_build_artifacts(
                app_id=str(resolved_app_id),
                build_id=bid,
                chat_id=context.get("chat_id"),
                export_build_id=context.get("build_registry_id") or bid,
            )
        except Exception:
            artifacts = {
                "previewUrl": None,
                "exportDownloadUrl": build_export_download_url(
                    app_id=str(resolved_app_id),
                    build_id=str(context.get("build_registry_id") or bid),
                ),
            }

        payload: Dict[str, Any] = {
            "event_type": "build_failed",
            "appId": str(resolved_app_id),
            "buildId": bid,
            "status": "error",
            "completedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "artifacts": artifacts,
            "error": err,
            "workflowName": str(workflow_name),
            "chatId": context.get("chat_id"),
            "executionId": context.get("execution_id"),
            "eventId": uuid.uuid4().hex,
            "ts": _utc_iso(),
            "idempotencyKey": _idempotency_key(
                app_id=str(resolved_app_id),
                build_id=bid,
                event_type="build_failed",
            ),
        }
        if context.get("journey_key"):
            payload["journeyId"] = context["journey_key"]
        if context.get("journey_instance_id"):
            payload["journeyInstanceId"] = context["journey_instance_id"]
        if context.get("build_registry_id"):
            payload["buildRegistryId"] = context["build_registry_id"]

        outbox_id = await upsert_outbox_event(
            app_id=str(resolved_app_id),
            build_id=bid,
            event_type="build_failed",
            status="error",
            payload=payload,
            user_id=user_id,
            workflow_name=workflow_name,
            idempotency_key=payload.get("idempotencyKey"),
        )
        _spawn_delivery(outbox_id, str(resolved_app_id), payload)
    except Exception as exc:  # pragma: no cover
        logger.debug("build_failed notify skipped: %s", exc)


__all__ = [
    "is_build_workflow",
    "emit_build_started",
    "emit_build_completed",
    "emit_build_failed",
    "get_build_artifacts",
    "build_export_download_url",
    "get_hooks",
]


def get_hooks() -> Dict[str, Any]:
    """Return lifecycle hooks for the runtime_extensions system.
    
    This is the entrypoint called by the runtime when this workflow declares:
        runtime_extensions:
          - kind: lifecycle_hooks
            entrypoint: workflows.AppGenerator.tools.platform.build_lifecycle:get_hooks
    
    Returns a dict with callables for:
        - is_build_workflow: Check if this workflow is a "build" type
        - on_start: Called when workflow starts
        - on_complete: Called when workflow completes successfully
        - on_fail: Called when workflow fails
    """
    return {
        "is_build_workflow": is_build_workflow,
        "on_start": emit_build_started,
        "on_complete": emit_build_completed,
        "on_fail": emit_build_failed,
    }
