from __future__ import annotations

import base64
import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware

from mozaiks.core.api.schemas import (
    ArtifactView,
    PreviewRequiredSecretsResponse,
    PreviewRunCreateRequest,
    PreviewRunCreateResponse,
    PreviewSecretUpsertRequest,
    RunCreateRequest,
    RunCreateResponse,
    RunEventItem,
    RunEventsPage,
    RunResumeRequest,
    RunResumeResponse,
    UIToolSubmissionRequest,
    UIToolSubmissionResponse,
    RunView,
    WorkflowSummary,
)
from mozaiks.core.bootstrap import register_runtime_components
from mozaiks.core.engine import AIEngineFacade, AIUnavailableError
from mozaiks.core.persistence import (
    CheckpointStorePort,
    EventStorePort,
    PersistedEvent,
    SqlAlchemyCheckpointStore,
    SqlAlchemyEventStore,
)
from mozaiks.core.registries import InMemoryPluginRegistry, InMemoryWorkflowRegistry
from mozaiks.core.runtime.context import CoreRuntimeContext, EventStoreControlPlane, EventStoreLedger, StandardLogger, SystemClock
from mozaiks.core.secrets import InMemorySecretsStore, scope_for_run
from mozaiks.core.streaming import RunStreamHub
from mozaiks.core.tools import ToolCatalog
from mozaiks.contracts import (
    ARTIFACT_CREATED_EVENT_TYPE,
    DOMAIN_EVENT_SCHEMA_VERSION,
    DomainEvent,
    EventEnvelope,
    ResumeRequest,
    RunRequest,
)
from mozaiks.contracts.ports import SandboxPort, SecretsPort


@dataclass(frozen=True)
class Principal:
    user_id: str
    roles: tuple[str, ...]


TRANSPORT_SNAPSHOT_EVENT_TYPE = "transport.snapshot"
TRANSPORT_REPLAY_BOUNDARY_EVENT_TYPE = "transport.replay_boundary"
UI_TOOL_INPUT_SUBMITTED_EVENT_TYPE = "ui.tool.input.submitted"
UI_TOOL_COMPLETED_EVENT_TYPE = "ui.tool.completed"
UI_TOOL_FAILED_EVENT_TYPE = "ui.tool.failed"


@dataclass(frozen=True)
class _RunnerConsumeSummary:
    final_result: dict[str, Any]
    saw_ui_tool_completed: bool
    saw_ui_tool_failed: bool
    matched_ui_tool_id: str | None


_STATUS_BY_EVENT_TYPE: dict[str, str] = {
    "process.created": "created",
    "process.started": "running",
    "process.running": "running",
    "process.resume_requested": "running",
    "process.resumed": "running",
    "process.completed": "completed",
    "process.failed": "failed",
    "process.cancelled": "cancelled",
    "task.started": "running",
    "task.retrying": "running",
    "task.completed": "running",
    "task.failed": "failed",
    UI_TOOL_COMPLETED_EVENT_TYPE: "running",
    UI_TOOL_FAILED_EVENT_TYPE: "failed",
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _event_payload_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {}


def _event_metadata_dict(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return dict(value)
    return None


def _as_string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    normalized = str(value).strip()
    return normalized or None


def _normalize_artifact_payload(payload: dict[str, Any], *, run_id: str) -> dict[str, Any]:
    normalized = dict(payload)
    nested = normalized.get("artifact")
    nested_artifact = nested if isinstance(nested, dict) else {}

    artifact_id = (
        _as_string(normalized.get("artifact_id"))
        or _as_string(nested_artifact.get("artifact_id"))
        or str(uuid.uuid4())
    )
    uri = (
        _as_string(normalized.get("uri"))
        or _as_string(normalized.get("artifact_uri"))
        or _as_string(nested_artifact.get("artifact_uri"))
        or f"run://{run_id}/artifacts/{artifact_id}"
    )
    checksum = _as_string(normalized.get("checksum")) or _as_string(nested_artifact.get("checksum"))
    if checksum is None:
        checksum = hashlib.sha256(uri.encode("utf-8")).hexdigest()
    media_type = (
        _as_string(normalized.get("media_type"))
        or _as_string(nested_artifact.get("media_type"))
        or "application/octet-stream"
    )
    artifact_type = _as_string(normalized.get("artifact_type")) or "generic"
    version = _as_string(normalized.get("version")) or DOMAIN_EVENT_SCHEMA_VERSION

    artifact = {
        "artifact_id": artifact_id,
        "artifact_uri": uri,
        "media_type": media_type,
        "checksum": checksum,
        "version": version,
        "run_id": run_id,
    }
    if "size_bytes" in normalized:
        artifact["size_bytes"] = normalized["size_bytes"]
    elif "size_bytes" in nested_artifact:
        artifact["size_bytes"] = nested_artifact["size_bytes"]

    normalized["artifact_id"] = artifact_id
    normalized["artifact_type"] = artifact_type
    normalized["uri"] = uri
    normalized["checksum"] = checksum
    normalized["version"] = version
    normalized["run_id"] = run_id
    normalized["media_type"] = media_type
    normalized["artifact"] = artifact
    return normalized


def _preview_url_artifact_payload(run_id: str) -> dict[str, Any]:
    preview_url = f"/preview/runs/{run_id}"
    checksum = hashlib.sha256(preview_url.encode("utf-8")).hexdigest()
    return _normalize_artifact_payload(
        {
            "artifact_id": f"preview-url-{run_id}",
            "artifact_type": "preview.url",
            "uri": preview_url,
            "checksum": checksum,
            "media_type": "text/uri-list",
            "content": preview_url,
            "metadata": {"source": "core.preview"},
        },
        run_id=run_id,
    )


def _core_event(
    *,
    run_id: str,
    event_type: str,
    payload: dict[str, Any],
    metadata: dict[str, Any] | None = None,
) -> EventEnvelope:
    final_payload = dict(payload)
    if event_type == ARTIFACT_CREATED_EVENT_TYPE:
        final_payload = _normalize_artifact_payload(final_payload, run_id=run_id)
    return EventEnvelope(
        event_type=event_type,
        seq=0,
        occurred_at=_utc_now(),
        run_id=run_id,
        schema_version=DOMAIN_EVENT_SCHEMA_VERSION,
        payload=final_payload,
        metadata=_event_metadata_dict(metadata),
    )


def _domain_to_envelope(*, run_id: str, domain_event: DomainEvent) -> EventEnvelope:
    if domain_event.run_id and domain_event.run_id != run_id:
        raise ValueError(f"AI runner emitted event for unexpected run_id '{domain_event.run_id}'")

    payload = _event_payload_dict(domain_event.payload)
    if domain_event.event_type == ARTIFACT_CREATED_EVENT_TYPE:
        payload = _normalize_artifact_payload(payload, run_id=run_id)

    return EventEnvelope(
        event_type=domain_event.event_type,
        seq=domain_event.seq,
        occurred_at=domain_event.occurred_at,
        run_id=run_id,
        schema_version=domain_event.schema_version,
        payload=payload,
        metadata=_event_metadata_dict(domain_event.metadata),
    )


def _extract_result(event: EventEnvelope) -> dict[str, Any] | None:
    event_type = event.event_type.lower()
    if event_type != "process.completed":
        return None

    payload = dict(event.payload)
    for key in ("result", "outputs"):
        value = payload.get(key)
        if isinstance(value, dict):
            return value
        if value is not None:
            return {"value": value}
    return payload or {}


def _status_from_event_type(event_type: str | None, *, default: str = "created") -> str:
    if not event_type:
        return default
    return _STATUS_BY_EVENT_TYPE.get(event_type.lower(), default)


def _extract_required_secret_keys(metadata: dict[str, Any]) -> list[str]:
    keys: list[str] = []

    def append_key(value: Any, *, required: bool = True) -> None:
        if not required:
            return
        if isinstance(value, str):
            normalized = value.strip()
            if normalized and normalized not in keys:
                keys.append(normalized)

    raw_required = metadata.get("required_secrets")
    if isinstance(raw_required, list):
        for item in raw_required:
            if isinstance(item, str):
                append_key(item)
            elif isinstance(item, dict):
                append_key(item.get("key"), required=bool(item.get("required", True)))
    elif isinstance(raw_required, dict):
        nested = raw_required.get("keys")
        if isinstance(nested, list):
            for item in nested:
                append_key(item)

    raw_secret_refs = metadata.get("secrets")
    if isinstance(raw_secret_refs, list):
        for item in raw_secret_refs:
            if isinstance(item, dict):
                append_key(item.get("key"), required=bool(item.get("required", True)))

    return keys


def _ui_submission_checkpoint_key(submission_id: str) -> str:
    return f"ui_submission:{submission_id}"


def _extract_ui_tool_id(payload: dict[str, Any]) -> str | None:
    for key in ("ui_tool_id", "tool_id"):
        value = _as_string(payload.get(key))
        if value is not None:
            return value
    return None


def _extract_artifact_linkage(payload: dict[str, Any]) -> dict[str, Any] | None:
    artifact_id = _as_string(payload.get("artifact_id"))
    uri = _as_string(payload.get("uri")) or _as_string(payload.get("artifact_uri"))
    checksum = _as_string(payload.get("checksum"))
    artifact_type = _as_string(payload.get("artifact_type"))
    version = _as_string(payload.get("version"))

    nested = payload.get("artifact")
    if isinstance(nested, dict):
        artifact_id = artifact_id or _as_string(nested.get("artifact_id"))
        uri = uri or _as_string(nested.get("uri")) or _as_string(nested.get("artifact_uri"))
        checksum = checksum or _as_string(nested.get("checksum"))
        artifact_type = artifact_type or _as_string(nested.get("artifact_type"))
        version = version or _as_string(nested.get("version"))

    if artifact_id is None and uri is None and checksum is None:
        return None

    return {
        "artifact_id": artifact_id,
        "artifact_type": artifact_type,
        "uri": uri,
        "checksum": checksum,
        "version": version,
    }


def _event_matches_ui_tool(*, event: EventEnvelope, ui_tool_id: str) -> bool:
    payload = _event_payload_dict(event.payload)
    event_tool_id = _extract_ui_tool_id(payload)
    if event_tool_id is None:
        return False
    return event_tool_id == ui_tool_id


def _snapshot_payload(
    *,
    run: Any,
    status: str,
    last_seq: int,
    checkpoint_key: str,
    checkpoint_payload: dict[str, Any],
    latest_event: PersistedEvent | None,
    ui_tool_state: dict[str, Any],
) -> dict[str, Any]:
    latest_event_json = latest_event.event.model_dump(mode="json") if latest_event is not None else None
    return {
        "version": DOMAIN_EVENT_SCHEMA_VERSION,
        "run_id": run.run_id,
        "last_seq": last_seq,
        "checkpoint": {
            "checkpoint_key": checkpoint_key,
            "payload": dict(checkpoint_payload),
        },
        "state": {
            "run": {
                "run_id": run.run_id,
                "workflow_name": run.workflow_name,
                "workflow_version": run.workflow_version,
                "status": status,
                "metadata": dict(run.metadata),
                "last_seq": last_seq,
            },
            "latest_event": latest_event_json,
            "ui_tool": dict(ui_tool_state),
        },
    }


async def get_principal(
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
    x_roles: str | None = Header(default=None, alias="X-Roles"),
) -> Principal:
    user = (x_user_id or "anonymous").strip() or "anonymous"
    roles = tuple(part.strip() for part in (x_roles or "").split(",") if part.strip())
    return Principal(user_id=user, roles=roles)


def create_app(
    *,
    event_store: EventStorePort | None = None,
    checkpoint_store: CheckpointStorePort | None = None,
    ai_engine: AIEngineFacade | None = None,
    secrets_store: SecretsPort | None = None,
    sandbox: SandboxPort | None = None,
) -> FastAPI:
    workflow_registry = InMemoryWorkflowRegistry()
    plugin_registry = InMemoryPluginRegistry()
    tool_catalog = ToolCatalog()
    stream_hub = RunStreamHub()
    store = event_store or SqlAlchemyEventStore()
    checkpoints = checkpoint_store or SqlAlchemyCheckpointStore()
    engine = ai_engine or AIEngineFacade()
    secrets = secrets_store or InMemorySecretsStore()
    sandbox_adapter = sandbox  # Sandbox implementations injected by app layer

    register_runtime_components(
        workflow_registry=workflow_registry,
        plugin_registry=plugin_registry,
        tool_catalog=tool_catalog,
    )

    app = FastAPI(title="Mozaiks Core Runtime", version="0.4.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=True,
    )

    app.state.event_store = store
    app.state.checkpoint_store = checkpoints
    app.state.ai_engine = engine
    app.state.workflow_registry = workflow_registry
    app.state.plugin_registry = plugin_registry
    app.state.tool_catalog = tool_catalog
    app.state.stream_hub = stream_hub
    app.state.secrets = secrets
    app.state.sandbox = sandbox_adapter

    async def _persist_and_publish(run_id: str, envelope: EventEnvelope) -> PersistedEvent:
        persisted = await store.append_event(run_id=run_id, event=envelope)
        await stream_hub.publish(run_id=run_id, event=persisted)
        return persisted

    async def _persist_checkpoint(*, run_id: str, event: PersistedEvent) -> None:
        payload = {
            "last_seq": event.seq,
            "event_type": event.event.event_type,
            "occurred_at": event.event.occurred_at.isoformat(),
            "payload": dict(event.event.payload),
        }
        await checkpoints.save_checkpoint(run_id=run_id, checkpoint_key="latest", payload=payload)

        for key in ("checkpoint_key", "checkpoint_id"):
            checkpoint_key = event.event.payload.get(key)
            if isinstance(checkpoint_key, str) and checkpoint_key.strip():
                await checkpoints.save_checkpoint(
                    run_id=run_id,
                    checkpoint_key=checkpoint_key.strip(),
                    payload=payload,
                )

    async def _emit_core_event(
        *,
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> PersistedEvent:
        envelope = _core_event(
            run_id=run_id,
            event_type=event_type,
            payload=payload,
            metadata=metadata,
        )
        persisted = await _persist_and_publish(run_id, envelope)
        await _persist_checkpoint(run_id=run_id, event=persisted)
        return persisted

    async def _list_all_events(*, run_id: str, batch_size: int = 250) -> list[PersistedEvent]:
        events: list[PersistedEvent] = []
        cursor = 0
        size = max(1, batch_size)
        while True:
            batch = await store.list_events(run_id=run_id, after_seq=cursor, limit=size)
            if not batch:
                break
            events.extend(batch)
            cursor = batch[-1].seq
            if len(batch) < size:
                break
        return events

    async def _derive_ui_tool_state(run_id: str) -> dict[str, Any]:
        events = await _list_all_events(run_id=run_id)
        active_ui_tool_id: str | None = None
        completion_state = "none"
        submission_id: str | None = None
        last_event_type: str | None = None
        artifact_linkage: dict[str, Any] | None = None

        for item in events:
            payload = _event_payload_dict(item.event.payload)
            event_type = item.event.event_type

            if event_type == ARTIFACT_CREATED_EVENT_TYPE:
                linkage = _extract_artifact_linkage(payload)
                if linkage is not None:
                    artifact_linkage = linkage
                continue

            tool_id = _extract_ui_tool_id(payload)
            if tool_id is None:
                continue

            normalized = event_type.lower()
            if normalized == "ui.tool.requested":
                active_ui_tool_id = tool_id
                completion_state = "pending"
                last_event_type = event_type
            elif normalized == UI_TOOL_INPUT_SUBMITTED_EVENT_TYPE:
                active_ui_tool_id = tool_id
                completion_state = "submitted"
                submission_id = _as_string(payload.get("submission_id")) or submission_id
                last_event_type = event_type
            elif normalized == UI_TOOL_COMPLETED_EVENT_TYPE:
                active_ui_tool_id = None
                completion_state = "completed"
                submission_id = _as_string(payload.get("submission_id")) or submission_id
                last_event_type = event_type
            elif normalized == UI_TOOL_FAILED_EVENT_TYPE:
                active_ui_tool_id = None
                completion_state = "failed"
                submission_id = _as_string(payload.get("submission_id")) or submission_id
                last_event_type = event_type

            linkage = _extract_artifact_linkage(payload)
            if linkage is not None:
                artifact_linkage = linkage

        return {
            "active_ui_tool_id": active_ui_tool_id,
            "completion_state": completion_state,
            "submission_id": submission_id,
            "last_event_type": last_event_type,
            "artifact_linkage": artifact_linkage,
        }

    async def _derive_status(run_id: str, *, default: str = "created") -> str:
        latest = await store.get_latest_event(run_id=run_id)
        if latest is None:
            return default
        return _status_from_event_type(latest.event.event_type, default=default)

    def _build_runtime_context(
        *,
        run_id: str,
        workflow_name: str,
        workflow_version: str,
        metadata: dict[str, Any],
        app_id: str | None,
        user_id: str | None,
        chat_id: str | None,
    ) -> CoreRuntimeContext:
        tenant_raw = metadata.get("tenant_id")
        tenant_id = tenant_raw if isinstance(tenant_raw, str) and tenant_raw.strip() else None

        runtime_metadata: dict[str, object] = {}
        if app_id is not None:
            runtime_metadata["app_id"] = app_id
        if user_id is not None:
            runtime_metadata["user_id"] = user_id
        if chat_id is not None:
            runtime_metadata["chat_id"] = chat_id

        return CoreRuntimeContext(
            run_id=run_id,
            tenant_id=tenant_id,
            workflow_name=workflow_name,
            workflow_version=workflow_version,
            ledger=EventStoreLedger(store),
            control_plane=EventStoreControlPlane(store),
            clock=SystemClock(),
            logger=StandardLogger("mozaiks.core.runtime"),
            sandbox=sandbox_adapter,
            secrets=secrets,
            metadata=runtime_metadata,
        )

    async def _consume_runner_events(
        *,
        run_id: str,
        stream: Any,
        ui_tool_id: str | None = None,
    ) -> _RunnerConsumeSummary:
        final_result: dict[str, Any] = {}
        saw_ui_tool_completed = False
        saw_ui_tool_failed = False
        matched_ui_tool_id: str | None = None

        async for domain_event in stream:
            envelope = _domain_to_envelope(run_id=run_id, domain_event=domain_event)
            persisted = await _persist_and_publish(run_id, envelope)
            await _persist_checkpoint(run_id=run_id, event=persisted)

            maybe_result = _extract_result(persisted.event)
            if maybe_result is not None:
                final_result = maybe_result

            normalized = persisted.event.event_type.lower()
            if normalized in {UI_TOOL_COMPLETED_EVENT_TYPE, UI_TOOL_FAILED_EVENT_TYPE}:
                if ui_tool_id is None or _event_matches_ui_tool(event=persisted.event, ui_tool_id=ui_tool_id):
                    matched_ui_tool_id = _extract_ui_tool_id(_event_payload_dict(persisted.event.payload))
                    if normalized == UI_TOOL_COMPLETED_EVENT_TYPE:
                        saw_ui_tool_completed = True
                    else:
                        saw_ui_tool_failed = True

        return _RunnerConsumeSummary(
            final_result=final_result,
            saw_ui_tool_completed=saw_ui_tool_completed,
            saw_ui_tool_failed=saw_ui_tool_failed,
            matched_ui_tool_id=matched_ui_tool_id,
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/workflows", response_model=list[WorkflowSummary])
    async def list_workflows(_: Principal = Depends(get_principal)) -> list[WorkflowSummary]:
        return [
            WorkflowSummary(
                name=item.name,
                version=item.version,
                description=item.description,
                tags=list(item.tags),
                metadata=dict(item.metadata),
            )
            for item in workflow_registry.list_specs()
        ]

    @app.post("/v1/preview/runs/{run_id}/secrets")
    async def set_preview_run_secret(
        run_id: str,
        request: PreviewSecretUpsertRequest,
        _: Principal = Depends(get_principal),
    ) -> dict[str, str]:
        run = await store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")

        scope = scope_for_run(run_id)
        await secrets.set_secret(scope=scope, key=request.key, value=request.value)
        return {"run_id": run_id, "scope": scope, "key": request.key}

    @app.get("/v1/preview/runs/{run_id}/secrets/required", response_model=PreviewRequiredSecretsResponse)
    async def list_required_preview_run_secrets(
        run_id: str,
        _: Principal = Depends(get_principal),
    ) -> PreviewRequiredSecretsResponse:
        run = await store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")

        scope = scope_for_run(run_id)
        required_keys = _extract_required_secret_keys(run.metadata)
        return PreviewRequiredSecretsResponse(run_id=run_id, scope=scope, required_keys=required_keys)

    @app.post("/v1/preview/runs", response_model=PreviewRunCreateResponse, status_code=status.HTTP_201_CREATED)
    async def create_preview_run(
        request: PreviewRunCreateRequest,
        principal: Principal = Depends(get_principal),
    ) -> PreviewRunCreateResponse:
        available, reason = engine.availability()
        if not available:
            raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=reason or "AI is unavailable")

        run_id = str(uuid.uuid4())
        metadata = dict(request.metadata)
        metadata["preview"] = True
        metadata.setdefault("requested_by", principal.user_id)
        metadata.setdefault("roles", list(principal.roles))

        await store.create_run(
            run_id=run_id,
            workflow_name=request.workflow_name,
            workflow_version=request.workflow_version,
            status="created",
            metadata=metadata,
        )

        await _emit_core_event(
            run_id=run_id,
            event_type="process.created",
            payload={
                "workflow_name": request.workflow_name,
                "workflow_version": request.workflow_version,
                "preview": True,
            },
        )
        await _emit_core_event(
            run_id=run_id,
            event_type=ARTIFACT_CREATED_EVENT_TYPE,
            payload=_preview_url_artifact_payload(run_id),
            metadata={"source": "core.preview"},
        )
        await _emit_core_event(
            run_id=run_id,
            event_type="process.running",
            payload={"reason": "dispatched_to_ai_runner", "preview": True},
        )

        runtime_context = _build_runtime_context(
            run_id=run_id,
            workflow_name=request.workflow_name,
            workflow_version=request.workflow_version,
            metadata=metadata,
            app_id=request.app_id,
            user_id=principal.user_id,
            chat_id=request.chat_id,
        )

        try:
            stream = engine.run(
                RunRequest(
                    run_id=run_id,
                    workflow_name=request.workflow_name,
                    workflow_version=request.workflow_version,
                    payload=dict(request.payload),
                    metadata=metadata,
                    app_id=request.app_id,
                    user_id=principal.user_id,
                    chat_id=request.chat_id,
                    tool_specs=tool_catalog.list_specs(),
                ),
                runtime_context=runtime_context,
            )
            await _consume_runner_events(run_id=run_id, stream=stream)
        except AIUnavailableError as exc:
            await _emit_core_event(
                run_id=run_id,
                event_type="process.failed",
                payload={"reason": str(exc)},
            )
            raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)) from exc
        except Exception as exc:
            await _emit_core_event(
                run_id=run_id,
                event_type="process.failed",
                payload={"reason": str(exc)},
            )
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Preview run execution failed") from exc

        return PreviewRunCreateResponse(
            run_id=run_id,
            preview_url=f"/preview/runs/{run_id}",
            status=await _derive_status(run_id),
        )

    @app.post("/v1/runs", response_model=RunCreateResponse, status_code=status.HTTP_201_CREATED)
    async def create_run(
        request: RunCreateRequest,
        principal: Principal = Depends(get_principal),
    ) -> RunCreateResponse:
        definition = workflow_registry.get_version(request.workflow_name, request.workflow_version)
        if definition is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Unknown workflow '{request.workflow_name}' version '{request.workflow_version}'",
            )

        available, reason = engine.availability()
        if not available:
            raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=reason or "AI is unavailable")

        run_id = str(uuid.uuid4())
        metadata = dict(request.metadata)
        metadata.setdefault("requested_by", principal.user_id)
        metadata.setdefault("roles", list(principal.roles))

        await store.create_run(
            run_id=run_id,
            workflow_name=definition.name,
            workflow_version=definition.version,
            status="created",
            metadata=metadata,
        )
        await _emit_core_event(
            run_id=run_id,
            event_type="process.created",
            payload={"workflow_name": definition.name, "workflow_version": definition.version},
        )
        await _emit_core_event(
            run_id=run_id,
            event_type="process.running",
            payload={"reason": "dispatched_to_ai_runner"},
        )

        try:
            runtime_context = _build_runtime_context(
                run_id=run_id,
                workflow_name=definition.name,
                workflow_version=definition.version,
                metadata=metadata,
                app_id=request.app_id,
                user_id=principal.user_id,
                chat_id=request.chat_id,
            )
            stream = engine.run(
                RunRequest(
                    run_id=run_id,
                    workflow_name=definition.name,
                    workflow_version=definition.version,
                    payload=dict(request.input),
                    metadata=metadata,
                    app_id=request.app_id,
                    user_id=principal.user_id,
                    chat_id=request.chat_id,
                    tool_specs=tool_catalog.list_specs(),
                ),
                runtime_context=runtime_context,
            )
            consume_summary = await _consume_runner_events(run_id=run_id, stream=stream)
            result = consume_summary.final_result
        except AIUnavailableError as exc:
            await _emit_core_event(
                run_id=run_id,
                event_type="process.failed",
                payload={"reason": str(exc)},
            )
            raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)) from exc
        except Exception as exc:
            await _emit_core_event(
                run_id=run_id,
                event_type="process.failed",
                payload={"reason": str(exc)},
            )
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Run execution failed") from exc

        current = await store.get_run(run_id)
        if current is None:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Run record was not found")

        return RunCreateResponse(
            run_id=current.run_id,
            created_at=current.created_at,
            status=await _derive_status(run_id),
            workflow_name=current.workflow_name,
            workflow_version=current.workflow_version,
            metadata=current.metadata,
            result=result,
        )

    @app.post("/v1/runs/{run_id}/resume", response_model=RunResumeResponse)
    async def resume_run(
        run_id: str,
        request: RunResumeRequest,
        principal: Principal = Depends(get_principal),
    ) -> RunResumeResponse:
        run = await store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")

        terminal_statuses = {"completed", "failed", "cancelled"}
        derived = await _derive_status(run_id, default=run.status)
        if derived in terminal_statuses:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Run already terminal; resume not allowed",
            )

        available, reason = engine.availability()
        if not available:
            raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=reason or "AI is unavailable")

        try:
            caps = engine.runner_capabilities()
        except AIUnavailableError as exc:
            raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)) from exc

        if not caps.get("supports_resume") or not caps.get("supports_checkpoints"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="AI runner does not support resume/checkpoints",
            )

        restored_checkpoint = await checkpoints.load_checkpoint(run_id=run_id, checkpoint_key=request.checkpoint_key)
        if restored_checkpoint is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Checkpoint '{request.checkpoint_key}' was not found for run '{run_id}'",
            )

        latest = await store.get_latest_event(run_id=run_id)
        latest_seq = max(int(run.last_seq), latest.seq if latest is not None else 0)
        checkpoint_last_seq = int(restored_checkpoint.get("last_seq", 0))
        base_last_seq = max(latest_seq, checkpoint_last_seq)
        status_before_resume = await _derive_status(run_id, default=run.status)
        ui_tool_state = await _derive_ui_tool_state(run_id)

        snapshot = await _emit_core_event(
            run_id=run_id,
            event_type=TRANSPORT_SNAPSHOT_EVENT_TYPE,
            payload=_snapshot_payload(
                run=run,
                status=status_before_resume,
                last_seq=base_last_seq,
                checkpoint_key=request.checkpoint_key,
                checkpoint_payload=restored_checkpoint,
                latest_event=latest,
                ui_tool_state=ui_tool_state,
            ),
            metadata={"source": "core.resume"},
        )

        resume_requested = await _emit_core_event(
            run_id=run_id,
            event_type="process.resume_requested",
            payload={"checkpoint_key": request.checkpoint_key, "last_seq": snapshot.seq},
        )

        try:
            runtime_context = _build_runtime_context(
                run_id=run_id,
                workflow_name=run.workflow_name,
                workflow_version=run.workflow_version,
                metadata=run.metadata,
                app_id=request.app_id,
                user_id=principal.user_id,
                chat_id=request.chat_id,
            )
            stream = engine.resume(
                ResumeRequest(
                    run_id=run_id,
                    last_seq=resume_requested.seq,
                    workflow_name=run.workflow_name,
                    workflow_version=run.workflow_version,
                    checkpoint_id=request.checkpoint_key,
                    metadata=dict(request.metadata),
                    app_id=request.app_id,
                    user_id=principal.user_id,
                    chat_id=request.chat_id,
                ),
                runtime_context=runtime_context,
            )
            consume_summary = await _consume_runner_events(run_id=run_id, stream=stream)
            result = consume_summary.final_result
        except AIUnavailableError as exc:
            await _emit_core_event(
                run_id=run_id,
                event_type="process.failed",
                payload={"reason": str(exc)},
            )
            raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)) from exc
        except Exception as exc:
            await _emit_core_event(
                run_id=run_id,
                event_type="process.failed",
                payload={"reason": str(exc)},
            )
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Run resume failed") from exc

        current = await store.get_run(run_id)
        if current is None:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Run record was not found")

        return RunResumeResponse(
            run_id=current.run_id,
            created_at=current.created_at,
            status=await _derive_status(run_id),
            workflow_name=current.workflow_name,
            workflow_version=current.workflow_version,
            metadata=current.metadata,
            resumed_from=request.checkpoint_key,
            checkpoint_restored=True,
            result=result,
        )

    @app.post("/v1/runs/{run_id}/ui-tools/{ui_tool_id}/submit", response_model=UIToolSubmissionResponse)
    async def submit_ui_tool_input(
        run_id: str,
        ui_tool_id: str,
        request: UIToolSubmissionRequest,
        principal: Principal = Depends(get_principal),
    ) -> UIToolSubmissionResponse:
        run = await store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")

        terminal_statuses = {"completed", "failed", "cancelled"}
        derived = await _derive_status(run_id, default=run.status)
        if derived in terminal_statuses:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Run already terminal; UI tool submission not allowed",
            )

        submission_id = _as_string(request.submission_id)
        if submission_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="submission_id must be a non-empty string",
            )

        submission_checkpoint_key = _ui_submission_checkpoint_key(submission_id)
        existing_submission = await checkpoints.load_checkpoint(
            run_id=run_id,
            checkpoint_key=submission_checkpoint_key,
        )
        if isinstance(existing_submission, dict):
            completion_state = _as_string(existing_submission.get("completion_state")) or "submitted"
            outcome_event_type = _as_string(existing_submission.get("outcome_event_type")) or UI_TOOL_INPUT_SUBMITTED_EVENT_TYPE
            artifact_linkage_value = existing_submission.get("artifact_linkage")
            artifact_linkage = artifact_linkage_value if isinstance(artifact_linkage_value, dict) else None
            if artifact_linkage is None:
                ui_tool_state = await _derive_ui_tool_state(run_id)
                state_linkage = ui_tool_state.get("artifact_linkage")
                artifact_linkage = state_linkage if isinstance(state_linkage, dict) else None
            result_value = existing_submission.get("result")
            result = result_value if isinstance(result_value, dict) else None
            current = await store.get_run(run_id)
            if current is None:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Run record was not found")
            return UIToolSubmissionResponse(
                run_id=current.run_id,
                created_at=current.created_at,
                status=await _derive_status(run_id, default=current.status),
                workflow_name=current.workflow_name,
                workflow_version=current.workflow_version,
                metadata=current.metadata,
                ui_tool_id=ui_tool_id,
                submission_id=submission_id,
                resumed_from=_as_string(existing_submission.get("checkpoint_key")) or request.checkpoint_key,
                outcome_event_type=outcome_event_type,
                completion_state=completion_state,
                idempotent_replay=True,
                artifact_linkage=artifact_linkage,
                result=result,
            )

        available, reason = engine.availability()
        if not available:
            raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=reason or "AI is unavailable")

        try:
            caps = engine.runner_capabilities()
        except AIUnavailableError as exc:
            raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)) from exc

        if not caps.get("supports_resume") or not caps.get("supports_checkpoints"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="AI runner does not support resume/checkpoints",
            )

        restored_checkpoint = await checkpoints.load_checkpoint(run_id=run_id, checkpoint_key=request.checkpoint_key)
        if restored_checkpoint is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Checkpoint '{request.checkpoint_key}' was not found for run '{run_id}'",
            )

        latest = await store.get_latest_event(run_id=run_id)
        latest_seq = max(int(run.last_seq), latest.seq if latest is not None else 0)
        checkpoint_last_seq = int(restored_checkpoint.get("last_seq", 0))
        base_last_seq = max(latest_seq, checkpoint_last_seq)
        status_before_resume = await _derive_status(run_id, default=run.status)
        ui_tool_state = await _derive_ui_tool_state(run_id)

        snapshot = await _emit_core_event(
            run_id=run_id,
            event_type=TRANSPORT_SNAPSHOT_EVENT_TYPE,
            payload=_snapshot_payload(
                run=run,
                status=status_before_resume,
                last_seq=base_last_seq,
                checkpoint_key=request.checkpoint_key,
                checkpoint_payload=restored_checkpoint,
                latest_event=latest,
                ui_tool_state=ui_tool_state,
            ),
            metadata={"source": "core.ui_tool_submission"},
        )

        submitted_payload: dict[str, Any] = {
            "run_id": run_id,
            "ui_tool_id": ui_tool_id,
            "submission_id": submission_id,
            "input": dict(request.payload),
            "checkpoint_key": request.checkpoint_key,
            "last_seq": snapshot.seq,
        }
        existing_linkage = ui_tool_state.get("artifact_linkage")
        if isinstance(existing_linkage, dict):
            submitted_payload["artifact_linkage"] = dict(existing_linkage)

        submitted = await _emit_core_event(
            run_id=run_id,
            event_type=UI_TOOL_INPUT_SUBMITTED_EVENT_TYPE,
            payload=submitted_payload,
            metadata={"source": "client.ui_tool"},
        )

        await checkpoints.save_checkpoint(
            run_id=run_id,
            checkpoint_key=submission_checkpoint_key,
            payload={
                "submission_id": submission_id,
                "ui_tool_id": ui_tool_id,
                "checkpoint_key": request.checkpoint_key,
                "completion_state": "submitted",
                "outcome_event_type": submitted.event.event_type,
                "last_seq": submitted.seq,
            },
        )

        completion_state = "submitted"
        outcome_event_type = submitted.event.event_type
        result: dict[str, Any] | None = None

        try:
            runtime_context = _build_runtime_context(
                run_id=run_id,
                workflow_name=run.workflow_name,
                workflow_version=run.workflow_version,
                metadata=run.metadata,
                app_id=request.app_id,
                user_id=principal.user_id,
                chat_id=request.chat_id,
            )
            resume_metadata = dict(request.metadata)
            resume_metadata["ui_tool_submission"] = {
                "submission_id": submission_id,
                "ui_tool_id": ui_tool_id,
                "payload": dict(request.payload),
            }
            stream = engine.resume(
                ResumeRequest(
                    run_id=run_id,
                    last_seq=submitted.seq,
                    workflow_name=run.workflow_name,
                    workflow_version=run.workflow_version,
                    checkpoint_id=request.checkpoint_key,
                    metadata=resume_metadata,
                    app_id=request.app_id,
                    user_id=principal.user_id,
                    chat_id=request.chat_id,
                ),
                runtime_context=runtime_context,
            )
            consume_summary = await _consume_runner_events(run_id=run_id, stream=stream, ui_tool_id=ui_tool_id)
            result = consume_summary.final_result or None
            if consume_summary.saw_ui_tool_failed:
                completion_state = "failed"
                outcome_event_type = UI_TOOL_FAILED_EVENT_TYPE
            elif consume_summary.saw_ui_tool_completed:
                completion_state = "completed"
                outcome_event_type = UI_TOOL_COMPLETED_EVENT_TYPE
            else:
                fallback_payload: dict[str, Any] = {
                    "run_id": run_id,
                    "ui_tool_id": ui_tool_id,
                    "submission_id": submission_id,
                    "success": True,
                    "output": result or {},
                }
                linkage_state = await _derive_ui_tool_state(run_id)
                fallback_linkage = linkage_state.get("artifact_linkage")
                if isinstance(fallback_linkage, dict):
                    fallback_payload["artifact_linkage"] = dict(fallback_linkage)
                fallback = await _emit_core_event(
                    run_id=run_id,
                    event_type=UI_TOOL_COMPLETED_EVENT_TYPE,
                    payload=fallback_payload,
                    metadata={"source": "core.ui_tool_submission"},
                )
                completion_state = "completed"
                outcome_event_type = fallback.event.event_type
        except AIUnavailableError as exc:
            failed = await _emit_core_event(
                run_id=run_id,
                event_type=UI_TOOL_FAILED_EVENT_TYPE,
                payload={
                    "run_id": run_id,
                    "ui_tool_id": ui_tool_id,
                    "submission_id": submission_id,
                    "error": str(exc),
                },
                metadata={"source": "core.ui_tool_submission"},
            )
            completion_state = "failed"
            outcome_event_type = failed.event.event_type
            result = {"error": str(exc)}
        except Exception as exc:
            failed = await _emit_core_event(
                run_id=run_id,
                event_type=UI_TOOL_FAILED_EVENT_TYPE,
                payload={
                    "run_id": run_id,
                    "ui_tool_id": ui_tool_id,
                    "submission_id": submission_id,
                    "error": str(exc),
                },
                metadata={"source": "core.ui_tool_submission"},
            )
            completion_state = "failed"
            outcome_event_type = failed.event.event_type
            result = {"error": str(exc)}

        ui_tool_state_after = await _derive_ui_tool_state(run_id)
        artifact_linkage_value = ui_tool_state_after.get("artifact_linkage")
        artifact_linkage = artifact_linkage_value if isinstance(artifact_linkage_value, dict) else None

        current = await store.get_run(run_id)
        if current is None:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Run record was not found")

        await checkpoints.save_checkpoint(
            run_id=run_id,
            checkpoint_key=submission_checkpoint_key,
            payload={
                "submission_id": submission_id,
                "ui_tool_id": ui_tool_id,
                "checkpoint_key": request.checkpoint_key,
                "completion_state": completion_state,
                "outcome_event_type": outcome_event_type,
                "artifact_linkage": artifact_linkage,
                "result": result,
                "last_seq": int(current.last_seq),
            },
        )

        return UIToolSubmissionResponse(
            run_id=current.run_id,
            created_at=current.created_at,
            status=await _derive_status(run_id, default=current.status),
            workflow_name=current.workflow_name,
            workflow_version=current.workflow_version,
            metadata=current.metadata,
            ui_tool_id=ui_tool_id,
            submission_id=submission_id,
            resumed_from=request.checkpoint_key,
            outcome_event_type=outcome_event_type,
            completion_state=completion_state,
            idempotent_replay=False,
            artifact_linkage=artifact_linkage,
            result=result,
        )

    @app.get("/v1/runs/{run_id}", response_model=RunView)
    async def get_run(run_id: str, _: Principal = Depends(get_principal)) -> RunView:
        run = await store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
        return RunView(
            run_id=run.run_id,
            created_at=run.created_at,
            status=await _derive_status(run_id, default=run.status),
            workflow_name=run.workflow_name,
            workflow_version=run.workflow_version,
            metadata=run.metadata,
        )

    @app.get("/v1/runs/{run_id}/events", response_model=RunEventsPage)
    async def get_run_events(
        run_id: str,
        after_seq: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=500),
        _: Principal = Depends(get_principal),
    ) -> RunEventsPage:
        run = await store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")

        rows = await store.list_events(run_id=run_id, after_seq=after_seq, limit=limit + 1)
        has_more = len(rows) > limit
        page_items = rows[:limit]
        next_after_seq = page_items[-1].seq if has_more and page_items else None

        return RunEventsPage(
            run_id=run_id,
            events=[RunEventItem(seq=item.seq, event=item.event) for item in page_items],
            next_after_seq=next_after_seq,
        )

    @app.get("/v1/artifacts/{artifact_id}", response_model=ArtifactView)
    async def get_artifact_by_id(
        artifact_id: str,
        download: bool = Query(default=False),
        _: Principal = Depends(get_principal),
    ) -> ArtifactView | Response:
        record = await store.get_artifact(artifact_id=artifact_id)
        if record is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")
        if download and record.content_base64 is not None:
            raw = base64.b64decode(record.content_base64.encode("ascii"))
            return Response(
                content=raw,
                media_type=record.media_type or "application/octet-stream",
                headers={
                    "X-Artifact-Id": record.artifact_id,
                    "ETag": record.checksum,
                    "X-Artifact-Run-Id": record.run_id,
                },
            )
        return ArtifactView.from_record(record)

    @app.get("/v1/artifacts", response_model=ArtifactView)
    async def get_artifact_by_uri_checksum(
        uri: str = Query(..., min_length=1),
        checksum: str = Query(..., min_length=1),
        _: Principal = Depends(get_principal),
    ) -> ArtifactView:
        record = await store.get_artifact(uri=uri, checksum=checksum)
        if record is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")
        return ArtifactView.from_record(record)

    @app.websocket("/v1/runs/{run_id}/stream")
    async def stream_run_events(
        websocket: WebSocket,
        run_id: str,
        after_seq: int = Query(default=0, ge=0),
    ) -> None:
        await websocket.accept()
        run = await store.get_run(run_id)
        if run is None:
            await websocket.close(code=1008, reason="Run not found")
            return

        cursor = after_seq
        try:
            async with stream_hub.subscribe(run_id) as queue:
                # Subscribe first to avoid replay/live race windows.
                while True:
                    batch = await store.list_events(run_id=run_id, after_seq=cursor, limit=250)
                    if not batch:
                        break
                    for item in batch:
                        await websocket.send_json({"seq": item.seq, "event": item.event.model_dump(mode="json")})
                        cursor = item.seq
                    if len(batch) < 250:
                        break

                replay_boundary = EventEnvelope(
                    event_type=TRANSPORT_REPLAY_BOUNDARY_EVENT_TYPE,
                    seq=cursor,
                    occurred_at=_utc_now(),
                    run_id=run_id,
                    schema_version=DOMAIN_EVENT_SCHEMA_VERSION,
                    payload={
                        "version": DOMAIN_EVENT_SCHEMA_VERSION,
                        "run_id": run_id,
                        "last_seq": cursor,
                        "requested_last_seq": after_seq,
                        "replay_complete": True,
                    },
                    metadata={"transport_only": True},
                )
                await websocket.send_json(
                    {
                        "seq": replay_boundary.seq,
                        "event": replay_boundary.model_dump(mode="json"),
                    }
                )

                while True:
                    item = await queue.get()
                    if item.seq <= cursor:
                        continue
                    await websocket.send_json({"seq": item.seq, "event": item.event.model_dump(mode="json")})
                    cursor = item.seq
        except WebSocketDisconnect:
            return

    return app
