
import asyncio
import hashlib
import os
import re
import uuid
from datetime import UTC, datetime
from typing import Any, cast

from logs.logging_config import get_core_logger

logger = get_core_logger("platform_build_lifecycle")


def _utc_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


async def mark_attempt(**kwargs: Any) -> None:
    from factory_app.workflows.AppGenerator.tools.platform.build_events_outbox import (
        mark_attempt as _mark_attempt,
    )

    await _mark_attempt(**kwargs)


async def get_outbox_event(**kwargs: Any) -> dict[str, Any] | None:
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


def _app_registry_service():
    from factory_app.app.modules.app_registry.backend.service import (
        AppRegistryService,
    )

    return AppRegistryService()


def get_mongo_client():
    from mozaiksai.core.core_config import get_mongo_client as _get_mongo_client

    return _get_mongo_client()


def build_app_scope_filter(app_id: str) -> dict[str, Any]:
    from mozaiksai.core.multitenant import build_app_scope_filter as _build_app_scope_filter

    return _build_app_scope_filter(app_id)


def coalesce_app_id(*, app_id: str | None = None) -> str | None:
    from mozaiksai.core.multitenant import coalesce_app_id as _coalesce_app_id

    return _coalesce_app_id(app_id=app_id)


def load_global_pack_graph():
    from mozaiksai.core.workflow.pack.config import (
        load_global_pack_graph as _load_global_pack_graph,
    )

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


def _normalize_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value or "").strip()
    return text or None


def _slugify(value: Any) -> str:
    text = str(value or "").strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return slug or "imported-app"


def _repo_display_name(value: Any) -> str | None:
    repo = _normalize_text(value)
    if not repo:
        return None
    repo = repo.removesuffix(".git").rstrip("/")
    tail = repo.split("/")[-1].strip()
    return tail or repo


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


async def _get_chat_session_context(*, app_id: str, chat_id: str) -> dict[str, Any]:
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
            "app_name": 1,
            "app_description": 1,
            "existing_experience_summary": 1,
            "brownfield_build_path": 1,
            "github_repo": 1,
            "frontend_github_repo": 1,
            "backend_github_repo": 1,
            "repo_summary": 1,
            "frontend_repo_summary": 1,
            "backend_repo_summary": 1,
            "app_intelligence_summary": 1,
            "app_intelligence_status": 1,
            "current_app_context_version_id": 1,
            "app_context_version_artifact_version_id": 1,
            "source_context_artifact_version_id": 1,
            "app_intelligence_artifact_version_id": 1,
            "graph_artifact_version_id": 1,
            "app_intelligence_registration": 1,
        },
    )
    return dict(doc) if isinstance(doc, dict) else {}


async def _record_chat_session_build_registry_id(
    *,
    app_id: str,
    chat_id: str | None,
    build_registry_id: str | None,
) -> None:
    resolved_app_id = coalesce_app_id(app_id=app_id)
    resolved_chat_id = _normalize_text(chat_id)
    resolved_record_id = _normalize_text(build_registry_id)
    if not resolved_app_id or not resolved_chat_id or not resolved_record_id:
        return
    try:
        client = get_mongo_client()
        system_database, chat_sessions_collection = _runtime_chat_sessions_collection()
        coll = client[system_database][chat_sessions_collection]
        await coll.update_one(
            {"_id": resolved_chat_id, **build_app_scope_filter(str(resolved_app_id))},
            {"$set": {"build_registry_id": resolved_record_id, "last_updated_at": datetime.now(UTC)}},
        )
    except Exception as exc:
        logger.debug("Failed to persist local build registry pointer: %s", exc)


async def _get_last_artifact_payload(*, app_id: str, chat_id: str) -> dict[str, Any] | None:
    doc = await _get_chat_session_context(app_id=app_id, chat_id=chat_id)
    last_artifact = doc.get("last_artifact")
    if not isinstance(last_artifact, dict):
        return None
    payload = last_artifact.get("payload")
    return payload if isinstance(payload, dict) else None


def _extract_preview_url(payload: dict[str, Any]) -> str | None:
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
    chat_id: str | None = None,
    export_build_id: str | None = None,
) -> dict[str, str | None]:
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


def _get_journey_workflow_groups(journey_id: str | None) -> list[tuple[int, list[str]]]:
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


def _should_emit_build_started(*, workflow_name: str, journey_key: str | None, journey_position: int | None) -> bool:
    workflow = _normalize_text(workflow_name)
    if not workflow:
        return False

    groups = _get_journey_workflow_groups(journey_key)
    if groups:
        first_index, first_group = groups[0]
        return journey_position == first_index and workflow in first_group
    return is_build_workflow(workflow)


def _should_emit_build_completed(*, workflow_name: str, journey_key: str | None, journey_position: int | None) -> bool:
    workflow = _normalize_text(workflow_name)
    if not workflow:
        return False

    groups = _get_journey_workflow_groups(journey_key)
    if groups:
        last_index, last_group = groups[-1]
        return journey_position == last_index and workflow in last_group
    return is_build_workflow(workflow)


def _should_emit_build_failed(*, workflow_name: str, journey_key: str | None) -> bool:
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
    execution_id: str | None,
    chat_id: str | None,
    build_id: str | None = None,
    build_registry_id: str | None = None,
    journey_instance_id: str | None = None,
    journey_key: str | None = None,
    journey_position: int | None = None,
    journey_total_steps: int | None = None,
) -> dict[str, Any]:
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
    build_registry_id_source = (
        "explicit"
        if _normalize_text(build_registry_id)
        else "session"
        if _normalize_text(session_ctx.get("build_registry_id"))
        else "build_id_fallback"
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
        "build_registry_id_source": build_registry_id_source,
        "journey_instance_id": str(resolved_journey_instance_id),
        "journey_key": resolved_journey_key,
        "journey_position": resolved_journey_position,
        "journey_total_steps": resolved_total_steps,
        "chat_id": resolved_chat_id,
        "execution_id": _normalize_text(execution_id) or str(uuid.uuid4()),
        "session_context": session_ctx,
    }


def _base_payload(
    *,
    context: dict[str, Any],
    workflow_name: str,
    user_id: str | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
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


def _first_context_text(session_ctx: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        text = _normalize_text(session_ctx.get(key))
        if text:
            return text
    return None


def _first_repo_summary(session_ctx: dict[str, Any]) -> dict[str, Any]:
    for key in ("repo_summary", "frontend_repo_summary", "backend_repo_summary"):
        value = session_ctx.get(key)
        if isinstance(value, dict) and value:
            return value
    return {}


def _registry_app_id_for_imported_app(
    *,
    owner_user_id: str,
    repo_identifier: str | None,
    display_name: str | None,
    chat_id: str | None,
    build_id: str,
) -> str:
    seed = f"{owner_user_id}:{repo_identifier or display_name or chat_id or build_id}".encode()
    digest = hashlib.sha1(seed).hexdigest()[:10]
    return f"{_slugify(display_name or repo_identifier or 'imported-app')}-{digest}"


def _build_context_profile_for_event(*, context: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    session_ctx: dict[str, Any] = cast(dict[str, Any], context.get("session_context")) if isinstance(context.get("session_context"), dict) else {}
    repo_identifier = _first_context_text(session_ctx, "github_repo", "frontend_github_repo", "backend_github_repo")
    profile: dict[str, Any] = {
        "source": "factory_app",
        "workflow_sequence": _normalize_text(payload.get("journeyId")) or "build",
        "workflow_name": _normalize_text(payload.get("workflowName")),
    }
    if repo_identifier:
        profile["github_repo"] = repo_identifier
    for key in (
        "brownfield_build_path",
        "current_app_context_version_id",
        "app_context_version_artifact_version_id",
        "source_context_artifact_version_id",
        "app_intelligence_artifact_version_id",
        "graph_artifact_version_id",
    ):
        text = _normalize_text(session_ctx.get(key))
        if text:
            profile[key] = text
    registration = session_ctx.get("app_intelligence_registration")
    if isinstance(registration, dict):
        refs = {
            key: value
            for key, value in registration.items()
            if key.endswith("_id") and _normalize_text(value)
        }
        if refs:
            profile["app_intelligence_refs"] = refs
    return {key: value for key, value in profile.items() if value is not None}


def _local_registry_payload(
    *,
    context: dict[str, Any],
    payload: dict[str, Any],
    lifecycle_state: str,
) -> dict[str, Any] | None:
    session_ctx: dict[str, Any] = cast(dict[str, Any], context.get("session_context")) if isinstance(context.get("session_context"), dict) else {}
    workflow_name = _normalize_text(payload.get("workflowName")) or "Workflow"
    owner_user_id = _normalize_text(payload.get("userId")) or "anonymous"
    chat_id = _normalize_text(payload.get("chatId"))
    build_id = _normalize_text(payload.get("buildId")) or _normalize_text(context.get("build_id")) or str(uuid.uuid4())
    repo_summary = _first_repo_summary(session_ctx)
    repo_identifier = (
        _first_context_text(session_ctx, "github_repo", "frontend_github_repo", "backend_github_repo")
        or _normalize_text(repo_summary.get("source"))
    )
    display_name = (
        _first_context_text(session_ctx, "app_name")
        or _repo_display_name(repo_identifier)
        or _repo_display_name(repo_summary.get("repo_name"))
    )

    is_imported_app = workflow_name == "ExistingAppDiscovery" or (
        _normalize_text(payload.get("journeyId")) or ""
    ).startswith("brownfield_")
    record_id_is_real = bool(_normalize_text(context.get("build_registry_id"))) and (
        str(context.get("build_registry_id_source") or "") in {"explicit", "session"}
    )
    if not is_imported_app and not record_id_is_real:
        return None

    current_build_run = {
        "build_id": build_id,
        "workflow_sequence": _normalize_text(payload.get("journeyId")) or "build",
        "status": lifecycle_state,
        "active_chat_id": chat_id,
        "active_workflow_id": workflow_name,
    }
    artifacts = payload.get("artifacts")
    if isinstance(artifacts, dict):
        artifact_version_id = _normalize_text(artifacts.get("artifactVersionId") or artifacts.get("artifact_version_id"))
        bundle_path = _normalize_text(artifacts.get("bundlePath") or artifacts.get("bundle_path"))
        if artifact_version_id:
            current_build_run["artifact_version_id"] = artifact_version_id
        if bundle_path:
            current_build_run["bundle_path"] = bundle_path

    description = (
        _first_context_text(session_ctx, "app_description", "existing_experience_summary")
        or (
            f"Mozaiks imported source context from {repo_identifier} so agents can build with this existing app."
            if repo_identifier
            else "Mozaiks imported source context so agents can build with this existing app."
        )
    )

    registry_app_id = (
        _normalize_text(session_ctx.get("registry_app_id"))
        or (
            _registry_app_id_for_imported_app(
                owner_user_id=owner_user_id,
                repo_identifier=repo_identifier,
                display_name=display_name,
                chat_id=chat_id,
                build_id=build_id,
            )
            if is_imported_app
            else _normalize_text(payload.get("appId"))
        )
    )
    if not registry_app_id:
        return None

    return {
        "owner_user_id": owner_user_id,
        "name": display_name,
        "description": description,
        "status": lifecycle_state,
        "app_id": registry_app_id,
        "chat_app_id": _normalize_text(payload.get("appId")),
        "active_chat_id": chat_id,
        "active_workflow_id": workflow_name,
        "name_source": "imported_app" if is_imported_app and display_name else "provisional",
        "build_context_profile": _build_context_profile_for_event(context=context, payload=payload),
        "current_build_run": current_build_run,
    }


async def _materialize_local_app_registry_event(
    *,
    context: dict[str, Any],
    payload: dict[str, Any],
    lifecycle_state: str,
) -> None:
    registry_payload = _local_registry_payload(
        context=context,
        payload=payload,
        lifecycle_state=lifecycle_state,
    )
    if not registry_payload:
        return

    try:
        service = _app_registry_service()
        build_registry_id = _normalize_text(context.get("build_registry_id"))
        existing = None
        if build_registry_id and context.get("build_registry_id_source") != "build_id_fallback":
            existing_response = await service.get_app_record(build_registry_id=build_registry_id)
            existing = existing_response.get("app") if isinstance(existing_response, dict) else None

        if isinstance(existing, dict) and existing.get("build_registry_id"):
            await service.update_build_status(
                build_registry_id=str(existing["build_registry_id"]),
                status=lifecycle_state,
                workflow_sequence=registry_payload["current_build_run"].get("workflow_sequence"),
                active_chat_id=registry_payload.get("active_chat_id"),
                active_workflow_id=registry_payload.get("active_workflow_id"),
                current_build_run=registry_payload["current_build_run"],
            )
            return

        result = await service.create_app_record(**registry_payload)
        app = result.get("app") if isinstance(result, dict) else None
        record_id = _normalize_text(app.get("build_registry_id")) if isinstance(app, dict) else None
        if record_id:
            await _record_chat_session_build_registry_id(
                app_id=str(payload.get("appId") or context.get("app_id") or ""),
                chat_id=payload.get("chatId"),
                build_registry_id=record_id,
            )
            logger.info(
                "LOCAL_APP_REGISTRY_MATERIALIZED workflow=%s status=%s app_id=%s build_registry_id=%s",
                payload.get("workflowName"),
                lifecycle_state,
                registry_payload.get("app_id"),
                record_id,
            )
    except Exception as exc:
        logger.warning("Local app registry materialization failed: %s", exc)


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
    execution_id: str | None = None,
    chat_id: str | None = None,
    user_id: str | None = None,
    workflow_name: str,
    build_id: str | None = None,
    build_registry_id: str | None = None,
    journey_instance_id: str | None = None,
    journey_key: str | None = None,
    journey_position: int | None = None,
    journey_total_steps: int | None = None,
    **_: Any,
) -> str | None:
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
    await _materialize_local_app_registry_event(
        context=context,
        payload=payload,
        lifecycle_state="building",
    )
    _spawn_delivery(outbox_event_id=outbox_event_id)
    return outbox_event_id


async def emit_build_completed(
    *,
    app_id: str,
    execution_id: str | None = None,
    chat_id: str | None = None,
    user_id: str | None = None,
    workflow_name: str,
    build_id: str | None = None,
    build_registry_id: str | None = None,
    journey_instance_id: str | None = None,
    journey_key: str | None = None,
    journey_position: int | None = None,
    journey_total_steps: int | None = None,
    **_: Any,
) -> str | None:
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
    await _materialize_local_app_registry_event(
        context=context,
        payload=payload,
        lifecycle_state="review",
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
    execution_id: str | None = None,
    chat_id: str | None = None,
    user_id: str | None = None,
    workflow_name: str,
    error: str | None = None,
    build_id: str | None = None,
    build_registry_id: str | None = None,
    journey_instance_id: str | None = None,
    journey_key: str | None = None,
    journey_position: int | None = None,
    journey_total_steps: int | None = None,
    **_: Any,
) -> str | None:
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
    await _materialize_local_app_registry_event(
        context=context,
        payload=payload,
        lifecycle_state="needs_revision",
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
