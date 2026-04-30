
import asyncio
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from mozaiksai.core.core_config import get_mongo_client
from mozaiksai.core.multitenant import build_app_scope_filter, coalesce_app_id
from logs.logging_config import get_core_logger

from .build_events_client import BuildEventsClient, _utc_iso
from .build_events_outbox import mark_attempt, upsert_outbox_event

logger = get_core_logger("platform_build_lifecycle")


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


async def _get_last_artifact_payload(*, app_id: str, build_id: str) -> Optional[Dict[str, Any]]:
    resolved_app_id = coalesce_app_id(app_id=app_id)
    if not resolved_app_id:
        return None

    client = get_mongo_client()
    coll = client["MozaiksAI"]["ChatSessions"]
    doc = await coll.find_one(
        {"_id": str(build_id), **build_app_scope_filter(str(resolved_app_id))},
        {"last_artifact": 1},
    )
    if not isinstance(doc, dict):
        return None
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


async def get_build_artifacts(*, app_id: str, build_id: str) -> Dict[str, Optional[str]]:
    payload = None
    try:
        payload = await _get_last_artifact_payload(app_id=app_id, build_id=build_id)
    except Exception:
        payload = None

    preview_url = _extract_preview_url(payload) if isinstance(payload, dict) else None
    export_url = build_export_download_url(app_id=str(app_id), build_id=str(build_id))
    return {"previewUrl": preview_url, "exportDownloadUrl": export_url}


def _idempotency_key(*, app_id: str, build_id: str, event_type: str) -> str:
    return f"build:{app_id}:{build_id}:{event_type}"


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


async def emit_build_started(*, app_id: str, build_id: str, user_id: Optional[str], workflow_name: str) -> None:
    try:
        resolved_app_id = coalesce_app_id(app_id=app_id)
        if not resolved_app_id:
            return
        bid = str(build_id or "").strip()
        if not bid:
            return

        payload: Dict[str, Any] = {
            "event_type": "build_started",
            "appId": str(resolved_app_id),
            "buildId": bid,
            "status": "building",
            "eventId": uuid.uuid4().hex,
            "ts": _utc_iso(),
            "idempotencyKey": _idempotency_key(
                app_id=str(resolved_app_id),
                build_id=bid,
                event_type="build_started",
            ),
        }

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


async def emit_build_completed(*, app_id: str, build_id: str, user_id: Optional[str], workflow_name: str) -> None:
    try:
        resolved_app_id = coalesce_app_id(app_id=app_id)
        if not resolved_app_id:
            return
        bid = str(build_id or "").strip()
        if not bid:
            return

        artifacts = await get_build_artifacts(app_id=str(resolved_app_id), build_id=bid)
        payload: Dict[str, Any] = {
            "event_type": "build_completed",
            "appId": str(resolved_app_id),
            "buildId": bid,
            "status": "built",
            "completedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "artifacts": artifacts,
            "eventId": uuid.uuid4().hex,
            "ts": _utc_iso(),
            "idempotencyKey": _idempotency_key(
                app_id=str(resolved_app_id),
                build_id=bid,
                event_type="build_completed",
            ),
        }

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
    build_id: str,
    user_id: Optional[str],
    workflow_name: str,
    message: str,
    details: Optional[str] = None,
) -> None:
    try:
        resolved_app_id = coalesce_app_id(app_id=app_id)
        if not resolved_app_id:
            return
        bid = str(build_id or "").strip()
        if not bid:
            return

        err: Dict[str, Any] = {"message": str(message or "Build failed")[:1000]}
        if details and isinstance(details, str):
            err["details"] = details[:4000]

        # Best-effort: include whatever artifacts exist at the time of failure.
        try:
            artifacts = await get_build_artifacts(app_id=str(resolved_app_id), build_id=bid)
        except Exception:
            artifacts = {
                "previewUrl": None,
                "exportDownloadUrl": build_export_download_url(app_id=str(resolved_app_id), build_id=bid),
            }

        payload: Dict[str, Any] = {
            "event_type": "build_failed",
            "appId": str(resolved_app_id),
            "buildId": bid,
            "status": "error",
            "completedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "artifacts": artifacts,
            "error": err,
            "eventId": uuid.uuid4().hex,
            "ts": _utc_iso(),
            "idempotencyKey": _idempotency_key(
                app_id=str(resolved_app_id),
                build_id=bid,
                event_type="build_failed",
            ),
        }

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
