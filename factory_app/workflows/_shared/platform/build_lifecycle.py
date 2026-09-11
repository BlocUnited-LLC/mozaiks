"""Factory build events over the server-owned session and Studio registry."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

from logs.logging_config import get_core_logger
from mozaiksai.core.session.build_binding import RunBuildBinding
from mozaiksai.core.studio.build_events import BuildLifecycleEvent

logger = get_core_logger("platform_build_lifecycle")


async def mark_attempt(**kwargs: Any) -> None:
    from factory_app.workflows.AppGenerator.tools.platform.build_events_outbox import (
        mark_attempt as update,
    )
    await update(**kwargs)


async def get_outbox_event(**kwargs: Any) -> dict[str, Any] | None:
    from factory_app.workflows.AppGenerator.tools.platform.build_events_outbox import (
        get_outbox_event as read,
    )
    return await read(**kwargs)


async def upsert_outbox_event(**kwargs: Any) -> str:
    from factory_app.workflows.AppGenerator.tools.platform.build_events_outbox import (
        upsert_outbox_event as write,
    )
    return await write(**kwargs)


def _build_events_client():
    from factory_app.workflows.AppGenerator.tools.platform.build_events_client import (
        BuildEventsClient,
    )
    return BuildEventsClient()


def _app_registry_service():
    from factory_app.app.modules.app_registry.backend.service import AppRegistryService
    return AppRegistryService()


def load_global_pack_graph():
    from mozaiksai.core.workflow.pack.config import load_global_pack_graph as load
    return load()


async def _get_chat_session_context(
    *, app_id: str, user_id: str, chat_id: str, workflow_name: str,
) -> dict[str, Any]:
    from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager
    from mozaiksai.core.multitenant import build_app_scope_filter

    if not all((app_id, user_id, chat_id, workflow_name)):
        raise ValueError("Build lifecycle requires execution app, owner, chat, and workflow identity")
    coll = await AG2PersistenceManager()._coll()
    doc = await coll.find_one(
        {"_id": chat_id, **build_app_scope_filter(app_id), "user_id": user_id, "workflow_name": workflow_name},
        {"run_build_binding": 1, "journey_instance_id": 1, "journey_key": 1,
         "journey_position": 1, "journey_total_steps": 1},
    )
    if not isinstance(doc, dict):
        raise ValueError("Build session is not available to this execution")
    return doc


async def _resolve_build_event_context(
    *, app_id: str, user_id: str | None, workflow_name: str,
    execution_id: str | None, chat_id: str | None, **_: Any,
) -> dict[str, Any]:
    session = await _get_chat_session_context(
        app_id=app_id, user_id=user_id or "", chat_id=chat_id or "", workflow_name=workflow_name,
    )
    binding = RunBuildBinding.model_validate(session.get("run_build_binding"))
    response = await _app_registry_service().get_app_record(
        build_registry_id=binding.build_registry_id, owner_user_id=user_id,
    )
    record = response.get("app")
    if not record or record.get("chat_app_id") != app_id or record.get("app_id") != binding.target_app_id:
        raise ValueError("Registered build target is not available in this execution")
    run = record.get("current_build_run") or {}
    if run.get("build_id") and run["build_id"] != binding.build_id:
        raise ValueError("Build lifecycle event belongs to a superseded build")
    return {
        "app_id": app_id, "user_id": user_id, "chat_id": chat_id,
        "execution_id": execution_id,
        **binding.model_dump(),
        "journey_instance_id": session.get("journey_instance_id"),
        "journey_key": session.get("journey_key"),
        "journey_position": session.get("journey_position"),
        "journey_total_steps": session.get("journey_total_steps"),
        "session_context": session, "registry_record": record,
    }


def _get_journey_workflow_groups(journey_id: str | None) -> list[tuple[int, list[str]]]:
    from mozaiksai.core.workflow.pack.config import get_workflow_sequence, normalize_step_groups

    pack = load_global_pack_graph()
    journey = get_workflow_sequence(pack, journey_id) if pack and journey_id else None
    if journey is None:
        return []
    return [(index, group) for index, group in enumerate(normalize_step_groups(journey.steps)) if group]


def _should_emit_build_started(*, workflow_name: str, journey_key: str | None, journey_position: int | None) -> bool:
    groups = _get_journey_workflow_groups(journey_key)
    return (journey_position == groups[0][0] and workflow_name in groups[0][1]) if groups else True


def _should_emit_build_completed(*, workflow_name: str, journey_key: str | None, journey_position: int | None) -> bool:
    groups = _get_journey_workflow_groups(journey_key)
    return (journey_position == groups[-1][0] and workflow_name in groups[-1][1]) if groups else True


def build_export_download_url(*, build_registry_id: str, artifact_version_id: str) -> str:
    return f"/api/studio/build/artifacts/{artifact_version_id}/download?" + urlencode(
        {"build_registry_id": build_registry_id},
    )


async def get_build_artifacts(*, context: dict[str, Any]) -> dict[str, Any]:
    from mozaiksai.core.artifacts import get_artifact_store

    run = context["registry_record"].get("current_build_run") or {}
    artifact_id = run.get("artifact_version_id")
    if run.get("build_id") != context["build_id"] or not artifact_id:
        return {}
    version = await get_artifact_store().get_build_record(
        app_id=context["target_app_id"], build_record_id=artifact_id,
    )
    if version is None:
        raise ValueError("The registered build artifact is missing")
    metadata = version.commit_metadata.metadata or {}
    if metadata.get("build_id") != context["build_id"] or metadata.get("build_registry_id") != context["build_registry_id"]:
        raise ValueError("The registered artifact does not belong to this build")
    return {
        "artifactVersionId": version.id,
        "bundlePath": run.get("bundle_path"),
        "exportDownloadUrl": build_export_download_url(
            build_registry_id=context["build_registry_id"], artifact_version_id=version.id,
        ),
    }


def _base_payload(*, context: dict[str, Any], workflow_name: str) -> dict[str, Any]:
    return {
        "appId": context["app_id"], "targetAppId": context["target_app_id"],
        "buildId": context["build_id"], "buildRegistryId": context["build_registry_id"],
        "phase": context["phase"],
        "journeyId": context.get("journey_key"), "journeyInstanceId": context.get("journey_instance_id"),
        "journeyPosition": context.get("journey_position"), "journeyTotalSteps": context.get("journey_total_steps"),
        "chatId": context["chat_id"], "executionId": context.get("execution_id"),
        "workflowName": workflow_name, "userId": context["user_id"],
        "appName": context["registry_record"].get("name"),
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }


async def _materialize_local_app_registry_event(
    *, context: dict[str, Any], payload: dict[str, Any], lifecycle_state: str,
) -> None:
    run = {
        "build_id": context["build_id"], "phase": context["phase"],
        "workflow_sequence": context.get("journey_key"),
        "active_chat_id": context["chat_id"], "active_workflow_id": payload["workflowName"],
    }
    artifacts = payload.get("artifacts") or {}
    if artifacts.get("artifactVersionId"):
        run["artifact_version_id"] = artifacts["artifactVersionId"]
        run["bundle_path"] = artifacts.get("bundlePath")
    result = await _app_registry_service().update_build_status(
        owner_user_id=context["user_id"], build_registry_id=context["build_registry_id"],
        status=lifecycle_state, workflow_sequence=context.get("journey_key"),
        active_chat_id=context["chat_id"], active_workflow_id=payload["workflowName"],
        current_build_run=run,
        expected_build_id=context["build_id"],
    )
    if not result.get("success"):
        raise ValueError("Registered build target disappeared during lifecycle update")


async def _deliver_outbox_event_once(*, outbox_event_id: str) -> None:
    doc = await get_outbox_event(outbox_id=outbox_event_id)
    if not isinstance(doc, dict) or not doc.get("app_id") or not isinstance(doc.get("payload"), dict):
        return
    try:
        result = await _build_events_client().post_build_event(app_id=doc["app_id"], payload=doc["payload"])
        await mark_attempt(outbox_id=outbox_event_id, ok=result.ok, status_code=result.status_code, error=result.error)
    except Exception as exc:
        logger.exception("Failed to deliver build lifecycle outbox event %s", outbox_event_id)
        await mark_attempt(outbox_id=outbox_event_id, ok=False, error=str(exc))


def _spawn_delivery(*, outbox_event_id: str) -> None:
    asyncio.get_running_loop().create_task(_deliver_outbox_event_once(outbox_event_id=outbox_event_id))


async def _emit_event(
    *, event_type: str, status: str, context: dict[str, Any], workflow_name: str,
    payload: dict[str, Any],
) -> str:
    idempotency_key = f"build:{context['app_id']}:{context['build_id']}:{event_type}"
    payload = BuildLifecycleEvent.model_validate({
        **payload, "eventType": event_type, "status": status, "idempotencyKey": idempotency_key,
    }).model_dump(mode="json", by_alias=True, exclude_none=True)
    event_id = await upsert_outbox_event(
        app_id=context["app_id"], build_id=context["build_id"], event_type=event_type,
        status=status, user_id=context["user_id"], workflow_name=workflow_name,
        idempotency_key=idempotency_key, payload=payload,
    )
    _spawn_delivery(outbox_event_id=event_id)
    return event_id


def _generation_evidence(context_variables: Any) -> dict[str, Any]:
    from factory_app.eval import collect_generation_evidence

    if hasattr(context_variables, "to_dict"):
        context_variables = context_variables.to_dict()
    return collect_generation_evidence(context_variables or {}).model_dump(mode="json")


def _emit_telemetry(*, workflow_name: str, status: str, context: dict[str, Any]) -> None:
    from mozaiksai.core.telemetry import build_workflow_payload, emit_build_telemetry

    payload = build_workflow_payload(
        workflow_name=workflow_name, final_status=status, build_registry_id=context["build_registry_id"],
    )
    asyncio.get_running_loop().create_task(emit_build_telemetry(payload))


async def emit_build_started(
    *, app_id: str, workflow_name: str, execution_id: str | None = None,
    chat_id: str | None = None, user_id: str | None = None, **_: Any,
) -> str | None:
    context = await _resolve_build_event_context(
        app_id=app_id, user_id=user_id, workflow_name=workflow_name, execution_id=execution_id, chat_id=chat_id,
    )
    payload = _base_payload(context=context, workflow_name=workflow_name)
    # Every workflow advances the directory pointer, even within the same build.
    await _materialize_local_app_registry_event(context=context, payload=payload, lifecycle_state="building")
    if not _should_emit_build_started(
        workflow_name=workflow_name, journey_key=context["journey_key"], journey_position=context["journey_position"],
    ):
        return None
    return await _emit_event(
        event_type="build.started", status="started", context=context, workflow_name=workflow_name, payload=payload,
    )


async def emit_build_completed(
    *, app_id: str, workflow_name: str, execution_id: str | None = None,
    chat_id: str | None = None, user_id: str | None = None, context_variables: Any = None, **_: Any,
) -> str | None:
    context = await _resolve_build_event_context(
        app_id=app_id, user_id=user_id, workflow_name=workflow_name, execution_id=execution_id, chat_id=chat_id,
    )
    if not _should_emit_build_completed(
        workflow_name=workflow_name, journey_key=context["journey_key"], journey_position=context["journey_position"],
    ):
        return None
    payload = _base_payload(context=context, workflow_name=workflow_name)
    payload["buildEvidence"] = _generation_evidence(context_variables)
    payload["artifacts"] = await get_build_artifacts(context=context) or None
    await _materialize_local_app_registry_event(context=context, payload=payload, lifecycle_state="review")
    event_id = await _emit_event(
        event_type="build.completed", status="completed", context=context, workflow_name=workflow_name, payload=payload,
    )
    _emit_telemetry(workflow_name=workflow_name, status="completed", context=context)
    return event_id


async def emit_build_failed(
    *, app_id: str, workflow_name: str, execution_id: str | None = None,
    chat_id: str | None = None, user_id: str | None = None, context_variables: Any = None,
    error: str | None = None, **_: Any,
) -> str | None:
    context = await _resolve_build_event_context(
        app_id=app_id, user_id=user_id, workflow_name=workflow_name, execution_id=execution_id, chat_id=chat_id,
    )
    payload = _base_payload(context=context, workflow_name=workflow_name)
    payload["buildEvidence"] = _generation_evidence(context_variables)
    payload["error"] = error
    await _materialize_local_app_registry_event(context=context, payload=payload, lifecycle_state="needs_revision")
    event_id = await _emit_event(
        event_type="build.failed", status="failed", context=context, workflow_name=workflow_name, payload=payload,
    )
    _emit_telemetry(workflow_name=workflow_name, status="failed", context=context)
    return event_id


__all__ = ["emit_build_started", "emit_build_completed", "emit_build_failed",
           "get_build_artifacts", "build_export_download_url"]
