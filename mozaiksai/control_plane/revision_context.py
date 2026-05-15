from __future__ import annotations

from typing import Any, Optional

from mozaiksai.core.artifacts import (
    ArtifactStore,
    ArtifactVersionDoc,
    ChangeRequestDoc,
    extract_summary_payload,
    get_artifact_store,
)
from mozaiksai.core.session.persistence import SessionStateStore
from mozaiksai.control_plane.contracts import ControlPlaneToolContext
from mozaiksai.control_plane.loader import load_selected_control_plane_pack
from mozaiksai.control_plane.schema import LoadedControlPlanePack


def _load_pack(pack_loader: Any) -> LoadedControlPlanePack:
    pack = pack_loader()
    return pack if isinstance(pack, LoadedControlPlanePack) else LoadedControlPlanePack.model_validate(pack)


def _artifact_summary(
    artifact: ArtifactVersionDoc,
    *,
    source: str,
    input_artifacts: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    summary = {
        "present": True,
        "source": source,
        "artifact_version_id": artifact.id,
        "artifact_kind": artifact.artifact_kind,
        "artifact_key": artifact.artifact_key,
        "version_number": artifact.version_number,
        "lineage_root_id": artifact.lineage_root_id,
        "parent_version_id": artifact.parent_version_id,
        "lifecycle_status": artifact.lifecycle_status.value,
        "validation_status": artifact.validation_status.value,
        "source_workflow": artifact.source_workflow,
        "source_chat_id": artifact.source_chat_id,
        "files_count": len(artifact.files_manifest or []),
        "canonical_inputs_version": dict(artifact.canonical_inputs_version or {}),
        "updated_at": artifact.updated_at,
    }
    summary_payload = extract_summary_payload(artifact)
    if summary_payload is not None:
        summary["summary_payload"] = summary_payload
    if input_artifacts:
        summary["input_artifacts"] = dict(input_artifacts)
    return summary


def _change_request_summary(change_request: ChangeRequestDoc) -> dict[str, Any]:
    return {
        "present": True,
        "change_request_id": change_request.id,
        "artifact_kind": change_request.artifact_kind,
        "artifact_key": change_request.artifact_key,
        "artifact_version_id": change_request.artifact_version_id,
        "classification": change_request.classification.value,
        "raw_user_request": change_request.raw_user_request,
        "created_by_user_id": change_request.created_by_user_id,
        "created_at": change_request.created_at,
        "change_intent": change_request.change_intent.model_dump(mode="python"),
        "impact_set": change_request.impact_set.model_dump(mode="python"),
        "router_decision": dict(change_request.router_decision or {}),
    }


async def _resolve_current_artifact(
    *,
    artifact_store: ArtifactStore,
    app_id: str,
    context: ControlPlaneToolContext,
) -> tuple[Optional[ArtifactVersionDoc], str]:
    if context.artifact_version_id:
        artifact = await artifact_store.get_artifact_version(
            app_id=app_id,
            artifact_version_id=str(context.artifact_version_id),
        )
        return artifact, "request_version" if artifact is not None else "request_version_missing"

    if context.artifact_kind:
        versions = await artifact_store.list_artifact_versions(
            app_id=app_id,
            artifact_kind=str(context.artifact_kind),
            artifact_key=str(context.artifact_key or "").strip() or None,
            limit=1,
        )
        if versions:
            return versions[0], "latest_for_request_kind"
    return None, "not_found"


async def _resolve_input_artifacts(
    *,
    artifact_store: ArtifactStore,
    app_id: str,
    artifact: ArtifactVersionDoc,
) -> dict[str, Any]:
    resolved_inputs: dict[str, Any] = {}
    for raw_kind, raw_version_id in dict(artifact.canonical_inputs_version or {}).items():
        artifact_kind = str(raw_kind or "").strip()
        artifact_version_id = str(raw_version_id or "").strip()
        if not artifact_kind or not artifact_version_id:
            continue
        input_artifact = await artifact_store.get_artifact_version(
            app_id=app_id,
            artifact_version_id=artifact_version_id,
        )
        if input_artifact is None:
            resolved_inputs[artifact_kind] = {
                "present": False,
                "artifact_kind": artifact_kind,
                "artifact_version_id": artifact_version_id,
                "source": "canonical_input_missing",
            }
            continue
        resolved_inputs[artifact_kind] = _artifact_summary(
            input_artifact,
            source="canonical_input",
        )
    return resolved_inputs


def _routing_summary(pack: LoadedControlPlanePack, artifact_kind: Optional[str]) -> dict[str, Any]:
    artifact = pack.routing_for_artifact(str(artifact_kind or "").strip().lower())
    current_routes: dict[str, Any] | None = None
    if artifact is not None:
        def route_summary(route: Any) -> dict[str, Any]:
            return {
                "workflow_sequence": route.workflow_sequence,
                "route_to": route.route_to,
            }

        current_routes = {
            "artifact_kind": artifact.artifact_kind,
            "label": artifact.label or artifact.artifact_kind,
            "routes": {
                "patch": route_summary(artifact.routes.patch),
                "design": route_summary(artifact.routes.design),
                "feature": route_summary(artifact.routes.feature),
                "core": route_summary(artifact.routes.core),
            },
        }
    return {
        "default_artifact_kind": pack.manifest.routing.default_artifact_kind,
        "known_artifact_kinds": [artifact.artifact_kind for artifact in pack.manifest.routing.artifacts],
        "current_artifact": current_routes,
    }


def _session_summary(state: Any) -> dict[str, Any]:
    if state is None:
        return {"present": False}
    return {
        "present": True,
        "session_id": state.session_id,
        "lifecycle_state": state.lifecycle_state.value,
        "sequence_status": state.sequence_status.value,
        "current_workflow_id": state.current_workflow_id,
        "current_chat_id": state.current_chat_id,
        "journey_key": state.journey_key,
        "journey_position": state.journey_position,
        "journey_total_steps": state.journey_total_steps,
        "active_revision_id": state.active_revision_id,
        "active_change_request_id": state.active_change_request_id,
        "current_revision_scope": state.current_revision_scope,
        "revision_origin_workflow": state.revision_origin_workflow,
        "restart_from_workflow": state.restart_from_workflow,
        "artifact_version_refs": dict(state.artifact_version_refs or {}),
        "stale_layers": dict(state.stale_layers or {}),
        "recent_revision_history": [
            {
                "revision_id": entry.revision_id,
                "change_request_id": entry.change_request_id,
                "scope": entry.scope,
                "origin_workflow": entry.origin_workflow,
                "target_workflow": entry.target_workflow,
                "from_version_refs": dict(entry.from_version_refs or {}),
                "to_version_refs": dict(entry.to_version_refs or {}),
                "timestamp": entry.timestamp,
            }
            for entry in list(state.revision_history or [])[-3:]
        ],
        "updated_at": state.updated_at,
    }


async def assemble_revision_context(
    *,
    context: ControlPlaneToolContext | dict[str, Any] | None = None,
    session_store: Optional[SessionStateStore] = None,
    artifact_store: Optional[ArtifactStore] = None,
    pack_loader: Any = load_selected_control_plane_pack,
) -> dict[str, Any]:
    tool_context = (
        context if isinstance(context, ControlPlaneToolContext) else ControlPlaneToolContext.model_validate(dict(context or {}))
    )
    app_id = str(tool_context.app_id or "").strip()
    user_id = str(tool_context.user_id or "").strip()
    if not app_id:
        return {"present": False, "reason": "missing_app_id"}

    pack = _load_pack(pack_loader)
    session_state = None
    if user_id:
        session_state = await (session_store or SessionStateStore()).load(app_id=app_id, user_id=user_id)

    store = artifact_store or get_artifact_store()
    current_artifact, current_artifact_source = await _resolve_current_artifact(
        artifact_store=store,
        app_id=app_id,
        context=tool_context,
    )

    tracked_artifacts: list[dict[str, Any]] = []
    tracked_artifact_kinds: set[str] = set()
    pending_artifact_kinds: list[str] = []
    for raw_kind in (
        [artifact.artifact_kind for artifact in pack.manifest.routing.artifacts]
        + list((session_state.artifact_version_refs or {}).keys() if session_state is not None else [])
        + ([str(tool_context.artifact_kind)] if tool_context.artifact_kind else [])
    ):
        kind = str(raw_kind or "").strip().lower()
        if kind:
            pending_artifact_kinds.append(kind)

    while pending_artifact_kinds:
        artifact_kind = str(pending_artifact_kinds.pop(0) or "").strip().lower()
        if not artifact_kind or artifact_kind in tracked_artifact_kinds:
            continue
        tracked_artifact_kinds.add(artifact_kind)
        resolved_artifact: Optional[ArtifactVersionDoc] = None
        source = "latest_for_kind"
        session_version_id = None
        if session_state is not None:
            session_version_id = str((session_state.artifact_version_refs or {}).get(artifact_kind) or "").strip() or None
        if current_artifact is not None and current_artifact.artifact_kind == artifact_kind:
            resolved_artifact = current_artifact
            source = current_artifact_source
        elif session_version_id:
            resolved_artifact = await store.get_artifact_version(app_id=app_id, artifact_version_id=session_version_id)
            source = "session_ref"
        if resolved_artifact is None:
            versions = await store.list_artifact_versions(
                app_id=app_id,
                artifact_kind=artifact_kind,
                limit=1,
            )
            resolved_artifact = versions[0] if versions else None
        if resolved_artifact is None:
            tracked_artifacts.append(
                {
                    "present": False,
                    "artifact_kind": artifact_kind,
                    "source": source,
                }
            )
        else:
            for raw_input_kind in dict(resolved_artifact.canonical_inputs_version or {}).keys():
                input_kind = str(raw_input_kind or "").strip().lower()
                if input_kind and input_kind not in tracked_artifact_kinds:
                    pending_artifact_kinds.append(input_kind)
            tracked_artifacts.append(
                _artifact_summary(
                    resolved_artifact,
                    source=source,
                    input_artifacts=await _resolve_input_artifacts(
                        artifact_store=store,
                        app_id=app_id,
                        artifact=resolved_artifact,
                    ),
                )
            )

    selected_change_request_id = str(
        (tool_context.extra or {}).get("change_request_id")
        or (session_state.active_change_request_id if session_state is not None else "")
        or ""
    ).strip() or None
    active_change_request = None
    if selected_change_request_id:
        active_change_request = await store.get_change_request(app_id=app_id, change_request_id=selected_change_request_id)

    recent_change_requests: list[dict[str, Any]] = []
    if current_artifact is not None:
        for change_request in await store.list_change_requests(
            app_id=app_id,
            artifact_version_id=current_artifact.id,
            limit=3,
        ):
            recent_change_requests.append(
                {
                    "change_request_id": change_request.id,
                    "classification": change_request.classification.value,
                    "created_at": change_request.created_at,
                    "raw_user_request": change_request.raw_user_request,
                }
            )

    return {
        "present": True,
        "app_id": app_id,
        "user_id": user_id or None,
        "requested_workflow_id": tool_context.requested_workflow_id,
        "source_surface": tool_context.source_surface,
        "artifact_kind": tool_context.artifact_kind,
        "artifact_key": tool_context.artifact_key,
        "artifact_version_id": tool_context.artifact_version_id,
        "routing": _routing_summary(pack, tool_context.artifact_kind),
        "session": _session_summary(session_state),
        "current_artifact": (
            _artifact_summary(
                current_artifact,
                source=current_artifact_source,
                input_artifacts=await _resolve_input_artifacts(
                    artifact_store=store,
                    app_id=app_id,
                    artifact=current_artifact,
                ),
            )
            if current_artifact is not None
            else {
                "present": False,
                "artifact_kind": tool_context.artifact_kind,
                "artifact_key": tool_context.artifact_key,
                "artifact_version_id": tool_context.artifact_version_id,
                "source": current_artifact_source,
            }
        ),
        "tracked_artifacts": tracked_artifacts,
        "active_change_request": (
            _change_request_summary(active_change_request)
            if active_change_request is not None
            else {"present": False, "change_request_id": selected_change_request_id}
        ),
        "recent_change_requests": recent_change_requests,
    }


__all__ = ["assemble_revision_context"]
