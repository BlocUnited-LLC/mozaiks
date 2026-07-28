"""Explicit launch helper for app-context refresh plans."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from mozaiksai.core.app_context.models import (
    AppContextStaleStatus,
    AppContextVersion,
    ArtifactRef,
    SourceRef,
)
from mozaiksai.core.app_context.refresh import (
    BROWNFIELD_DISCOVERY_REFRESH_SEQUENCE,
    ContextRefreshPlan,
    ContextRefreshResult,
    ContextRefreshResultStatus,
    ContextRefreshScope,
)
from mozaiksai.core.workflow.pack.config import get_workflow_sequence, load_global_pack_graph
from mozaiksai.core.workflow.pack.schema import normalize_step_groups

EXISTING_APP_DISCOVERY_WORKFLOW = "ExistingAppDiscovery"
CONTEXT_REFRESH_TRIGGER_SOURCE = "context_refresh"
DEFAULT_CONTEXT_REFRESH_REASON = "Refresh App Intelligence context before retrying refinement."


class ContextRefreshLaunchStatus(StrEnum):
    QUEUED = "queued"
    LAUNCHED = "launched"
    FAILED = "failed"


class ContextRefreshLaunchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ContextRefreshLaunchStatus
    workflow_sequence: str
    workflow_id: str | None = None
    chat_id: str | None = None
    websocket_url: str | None = None
    app_id: str
    user_id: str | None = None
    current_context_version_id: str | None = None
    expected_artifacts: list[str] = Field(default_factory=list)
    context_variables: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


WorkflowLauncher = Callable[..., Awaitable[Any]]


async def launch_context_refresh_plan(
    plan: ContextRefreshPlan | dict[str, Any],
    *,
    app_id: str | None = None,
    user_id: str,
    refresh_scope: ContextRefreshScope | str = ContextRefreshScope.DISCOVERY_INDEXING,
    reason: str | None = None,
    request_id: str | None = None,
    session_router: Any | None = None,
    workflow_launcher: WorkflowLauncher | None = None,
) -> ContextRefreshLaunchResult:
    """Explicitly launch the registered non-mutating brownfield refresh sequence."""
    refresh_plan = _normalize_plan(plan)
    resolved_app_id = _validate_refresh_plan(refresh_plan, app_id=app_id)
    resolved_user_id = str(user_id or "").strip()
    if not resolved_user_id:
        raise ValueError("user_id is required to launch a context refresh plan")

    workflow_id = _resolve_refresh_workflow_id(refresh_plan.workflow_sequence)
    resolved_reason = str(reason or "").strip() or DEFAULT_CONTEXT_REFRESH_REASON
    resolved_scope = ContextRefreshScope(refresh_scope)
    context_variables = _build_refresh_context_variables(
        refresh_plan,
        refresh_scope=resolved_scope,
        reason=resolved_reason,
    )
    trigger_payload = {
        "context_refresh_plan": refresh_plan.model_dump(mode="json"),
    }
    if request_id:
        trigger_payload["context_refresh_request_id"] = request_id  # type: ignore[assignment]

    if workflow_launcher is None:
        from mozaiksai.core.session.launcher import launch_routed_workflow

        launcher = launch_routed_workflow
    else:
        launcher = workflow_launcher  # type: ignore[assignment]
    try:
        launch = await launcher(
            workflow_id=workflow_id,
            app_id=resolved_app_id,
            user_id=resolved_user_id,
            trigger_source=CONTEXT_REFRESH_TRIGGER_SOURCE,
            context_variables=context_variables,
            trigger_payload=trigger_payload,
            journey_id=refresh_plan.workflow_sequence,
            session_router=session_router,
            extra_trigger_meta={
                "context_refresh_request_id": request_id,
                "current_context_version_id": refresh_plan.current_context_version_id,
                "workflow_sequence": refresh_plan.workflow_sequence,
            },
        )
    except Exception as exc:
        return ContextRefreshLaunchResult(
            status=ContextRefreshLaunchStatus.FAILED,
            workflow_sequence=refresh_plan.workflow_sequence,
            workflow_id=workflow_id,
            app_id=resolved_app_id,
            user_id=resolved_user_id,
            current_context_version_id=refresh_plan.current_context_version_id,
            expected_artifacts=list(refresh_plan.expected_artifacts),
            context_variables=context_variables,
            error=str(exc),
        )

    return ContextRefreshLaunchResult(
        status=ContextRefreshLaunchStatus.LAUNCHED,
        workflow_sequence=refresh_plan.workflow_sequence,
        workflow_id=str(getattr(launch, "workflow_id", "") or workflow_id),
        chat_id=str(getattr(launch, "chat_id", "") or "") or None,
        websocket_url=str(getattr(launch, "websocket_url", "") or "") or None,
        app_id=resolved_app_id,
        user_id=resolved_user_id,
        current_context_version_id=refresh_plan.current_context_version_id,
        expected_artifacts=list(refresh_plan.expected_artifacts),
        context_variables=context_variables,
    )


async def complete_context_refresh(
    plan: ContextRefreshPlan | dict[str, Any],
    *,
    artifact_store: Any,
    app_id: str | None = None,
    previous_context_version_id: str | None = None,
    launch_result: ContextRefreshLaunchResult | dict[str, Any] | None = None,
    workflow_context_variables: dict[str, Any] | None = None,
) -> ContextRefreshResult:
    """Build a read-only completion result after ExistingAppDiscovery refresh finishes."""
    refresh_plan = _normalize_plan(plan)
    resolved_app_id = _validate_refresh_plan(refresh_plan, app_id=app_id)
    previous_id = _resolve_previous_context_version_id(
        plan=refresh_plan,
        previous_context_version_id=previous_context_version_id,
        launch_result=launch_result,
        workflow_context_variables=workflow_context_variables,
    )
    warnings = list(refresh_plan.warnings)

    normalized_launch_result = _normalize_launch_result(launch_result)
    if normalized_launch_result is not None and normalized_launch_result.status is ContextRefreshLaunchStatus.FAILED:
        if normalized_launch_result.error:
            warnings.append(normalized_launch_result.error)
        return ContextRefreshResult(
            app_id=resolved_app_id,
            previous_context_version_id=previous_id,
            new_context_version_id=None,
            status=ContextRefreshResultStatus.FAILED,
            stale_resolved=False,
            warnings=_dedupe(warnings),
            artifacts_created=[],
        )

    from mozaiksai.core.app_context.store import get_current_app_context_version

    current_context = await get_current_app_context_version(
        app_id=resolved_app_id,
        artifact_store=artifact_store,
    )
    if current_context is None:
        warnings.append("No current AppContextVersion is available after context refresh.")
        return ContextRefreshResult(
            app_id=resolved_app_id,
            previous_context_version_id=previous_id,
            new_context_version_id=None,
            status=ContextRefreshResultStatus.MISSING_CONTEXT,
            stale_resolved=False,
            warnings=_dedupe(warnings),
            artifacts_created=[],
        )

    artifacts_created = await _artifacts_from_current_context(
        app_id=resolved_app_id,
        context_version=current_context,
        artifact_store=artifact_store,
    )
    missing_expected = _missing_expected_artifacts(
        expected_artifacts=refresh_plan.expected_artifacts,
        artifacts_created=artifacts_created,
    )
    if missing_expected:
        warnings.append(
            "Context refresh result is missing expected artifacts: "
            + ", ".join(missing_expected)
        )

    new_id = current_context.context_version_id
    status = ContextRefreshResultStatus.COMPLETED
    if previous_id and new_id == previous_id:
        status = ContextRefreshResultStatus.NO_CHANGE
        warnings.append("Context refresh did not create a new AppContextVersion.")

    stale_status = _stale_status_value(current_context)
    if stale_status != AppContextStaleStatus.CURRENT.value:
        warnings.append(f"New AppContextVersion stale_status is {stale_status!r}.")

    stale_resolved = (
        status is ContextRefreshResultStatus.COMPLETED
        and bool(new_id)
        and new_id != previous_id
        and stale_status == AppContextStaleStatus.CURRENT.value
        and not missing_expected
    )
    return ContextRefreshResult(
        app_id=resolved_app_id,
        previous_context_version_id=previous_id,
        new_context_version_id=new_id,
        status=status,
        stale_resolved=stale_resolved,
        warnings=_dedupe(warnings),
        artifacts_created=artifacts_created,
    )


def _normalize_plan(plan: ContextRefreshPlan | dict[str, Any]) -> ContextRefreshPlan:
    if isinstance(plan, ContextRefreshPlan):
        return plan
    return ContextRefreshPlan.model_validate(plan)


def _validate_refresh_plan(plan: ContextRefreshPlan, *, app_id: str | None) -> str:
    if plan.workflow_sequence != BROWNFIELD_DISCOVERY_REFRESH_SEQUENCE:
        raise ValueError(
            "Context refresh execution only supports "
            f"{BROWNFIELD_DISCOVERY_REFRESH_SEQUENCE!r}."
        )
    if bool(getattr(plan, "mutation_allowed", True)):
        raise ValueError("Context refresh execution requires mutation_allowed=false.")

    resolved_app_id = str(app_id or plan.app_id or "").strip()
    if not resolved_app_id:
        raise ValueError("app_id is required to launch a context refresh plan")
    if str(plan.app_id or "").strip() != resolved_app_id:
        raise ValueError("Context refresh plan app_id does not match launch app_id.")
    return resolved_app_id


def _resolve_refresh_workflow_id(workflow_sequence: str) -> str:
    pack = load_global_pack_graph()
    if pack is None:
        raise ValueError("Global workflow extension registry is not available.")

    sequence = get_workflow_sequence(pack, workflow_sequence)
    if sequence is None:
        raise ValueError(f"Unknown context refresh workflow_sequence {workflow_sequence!r}.")

    workflows = [
        workflow_id
        for group in normalize_step_groups(sequence.steps)
        for workflow_id in group
    ]
    if workflows != [EXISTING_APP_DISCOVERY_WORKFLOW]:
        raise ValueError(
            "brownfield_discovery_refresh must contain ExistingAppDiscovery only."
        )
    return EXISTING_APP_DISCOVERY_WORKFLOW


def _build_refresh_context_variables(
    plan: ContextRefreshPlan,
    *,
    refresh_scope: ContextRefreshScope,
    reason: str,
) -> dict[str, Any]:
    source_refs = [_source_ref_payload(source_ref) for source_ref in plan.target_source_refs]
    refresh_request = {
        "app_id": plan.app_id,
        "current_context_version_id": plan.current_context_version_id,
        "reason": reason,
        "source_refs": source_refs,
        "refresh_scope": refresh_scope.value,
    }
    return {
        "context_refresh_request": refresh_request,
        "current_context_version_id": plan.current_context_version_id,
        "source_refs": source_refs,
        "refresh_scope": refresh_scope.value,
        "refresh_reason": reason,
        "expected_artifacts": list(plan.expected_artifacts),
    }


def _source_ref_payload(source_ref: SourceRef) -> dict[str, Any]:
    return source_ref.model_dump(mode="json")


def _normalize_launch_result(
    value: ContextRefreshLaunchResult | dict[str, Any] | None,
) -> ContextRefreshLaunchResult | None:
    if value is None or isinstance(value, ContextRefreshLaunchResult):
        return value
    return ContextRefreshLaunchResult.model_validate(value)


def _resolve_previous_context_version_id(
    *,
    plan: ContextRefreshPlan,
    previous_context_version_id: str | None,
    launch_result: ContextRefreshLaunchResult | dict[str, Any] | None,
    workflow_context_variables: dict[str, Any] | None,
) -> str | None:
    explicit = str(previous_context_version_id or "").strip()
    if explicit:
        return explicit
    normalized_launch_result = _normalize_launch_result(launch_result)
    if normalized_launch_result is not None:
        launch_previous = str(normalized_launch_result.current_context_version_id or "").strip()
        if launch_previous:
            return launch_previous
    context_previous = str((workflow_context_variables or {}).get("current_context_version_id") or "").strip()
    if context_previous:
        return context_previous
    return plan.current_context_version_id


async def _artifacts_from_current_context(
    *,
    app_id: str,
    context_version: AppContextVersion,
    artifact_store: Any,
) -> list[ArtifactRef]:
    refs = list(context_version.artifact_refs)
    current_artifact_ref = await _current_context_artifact_ref(
        app_id=app_id,
        artifact_store=artifact_store,
    )
    if current_artifact_ref is not None:
        refs.append(current_artifact_ref)
    return _dedupe_artifact_refs(refs)


async def _current_context_artifact_ref(
    *,
    app_id: str,
    artifact_store: Any,
) -> ArtifactRef | None:
    from mozaiksai.core.app_context.store import (
        APP_CONTEXT_VERSION_ARTIFACT_KEY,
        APP_CONTEXT_VERSION_ARTIFACT_KIND,
    )
    from mozaiksai.core.artifacts.models import ArtifactLifecycleStatus

    versions = await artifact_store.list_artifact_versions(
        app_id=app_id,
        artifact_kind=APP_CONTEXT_VERSION_ARTIFACT_KIND,
        artifact_key=APP_CONTEXT_VERSION_ARTIFACT_KEY,
        lifecycle_status=ArtifactLifecycleStatus.CURRENT,
        limit=1,
    )
    if not versions:
        return None
    artifact = versions[0]
    return ArtifactRef(
        artifact_kind=str(getattr(artifact, "artifact_kind", APP_CONTEXT_VERSION_ARTIFACT_KIND)),
        artifact_key=str(getattr(artifact, "artifact_key", APP_CONTEXT_VERSION_ARTIFACT_KEY)),
        artifact_version_id=str(getattr(artifact, "id", "") or getattr(artifact, "_id", "")),
        lifecycle_status=_artifact_lifecycle_value(getattr(artifact, "lifecycle_status", None)),
    )


def _dedupe_artifact_refs(refs: list[ArtifactRef]) -> list[ArtifactRef]:
    deduped: list[ArtifactRef] = []
    seen: set[tuple[str, str | None, str]] = set()
    for ref in refs:
        key = (ref.artifact_kind, ref.artifact_key, ref.artifact_version_id)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(ref)
    return deduped


def _missing_expected_artifacts(
    *,
    expected_artifacts: list[str],
    artifacts_created: list[ArtifactRef],
) -> list[str]:
    present = {ref.artifact_kind for ref in artifacts_created}
    return [
        artifact_kind
        for artifact_kind in expected_artifacts
        if artifact_kind and artifact_kind not in present
    ]


def _stale_status_value(context_version: AppContextVersion) -> str:
    status = context_version.stale_status
    if isinstance(status, AppContextStaleStatus):
        return status.value
    return str(status or "").strip() or AppContextStaleStatus.UNKNOWN.value


def _artifact_lifecycle_value(value: Any) -> str | None:
    raw = getattr(value, "value", value)
    return str(raw) if raw is not None else None


def _dedupe(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


__all__ = [
    "CONTEXT_REFRESH_TRIGGER_SOURCE",
    "ContextRefreshLaunchResult",
    "ContextRefreshLaunchStatus",
    "EXISTING_APP_DISCOVERY_WORKFLOW",
    "complete_context_refresh",
    "launch_context_refresh_plan",
]
