from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

REVIEW_PACKAGE_SCHEMA_VERSION = "mozaiks.refinement.review_package.v1"


class RefinementReviewChangedFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    change_type: str
    diff_preview: str
    before_size: int = Field(default=0, ge=0)
    after_size: int = Field(default=0, ge=0)


class RefinementReviewAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    enabled: bool
    reason: str | None = None


class RefinementReviewWriteBack(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str
    target: str | None = None
    label: str
    description: str


class RefinementReviewRouteDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str | None = None
    change_class: str | None = None
    refinement_lane: str | None = None
    workflow_sequence: str | None = None
    target_workflow: str | None = None
    execution_mode: str | None = None
    rationale: str | None = None
    confidence: float | None = None
    signals: list[str] = Field(default_factory=list)
    scope_summary: str | None = None


class RefinementReviewPackage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = REVIEW_PACKAGE_SCHEMA_VERSION
    request_id: str | None = None
    app_id: str
    artifact_kind: str
    artifact_key: str
    artifact_version_id: str
    parent_version_id: str | None = None
    review_status: str
    lifecycle_status: str
    validation_status: str
    change_class: str | None = None
    refinement_lane: str | None = None
    workflow_sequence: str | None = None
    target_workflow: str | None = None
    write_back_mode: str
    write_back_target: str | None = None
    write_back_label: str
    write_back_description: str
    affected_paths: list[str] = Field(default_factory=list)
    selected_paths: list[str] = Field(default_factory=list)
    changed_file_count: int = Field(default=0, ge=0)
    changed_files: list[RefinementReviewChangedFile] = Field(default_factory=list)
    coding_summary: str | None = None
    validation_result: dict[str, Any] | None = None
    validation_override_required: bool = False
    validation_blocker: str | None = None
    current_skipped_files: list[str] = Field(default_factory=list)
    parent_skipped_files: list[str] = Field(default_factory=list)
    can_accept: bool = False
    can_reject: bool = False
    can_promote: bool = False
    actions: list[RefinementReviewAction] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)
    route_decision: RefinementReviewRouteDecision
    write_back: RefinementReviewWriteBack


_WRITE_BACK_LABELS: dict[str, tuple[str, str]] = {
    "generated_artifact": (
        "Update app version",
        "Accepted changes become a new Mozaiks app artifact version.",
    ),
    "external_patch": (
        "Create patch proposal",
        "Accepted changes become a patch or pull-request proposal for external source.",
    ),
    "mozaiks_overlay": (
        "Update Mozaiks overlay",
        "Accepted changes update Mozaiks-owned overlay artifacts without claiming external source ownership.",
    ),
    "full_migration_artifact": (
        "Stage migrated module",
        "Accepted changes stage migrated Mozaiks module or app artifacts for review.",
    ),
    "local_workspace": (
        "Apply to local workspace",
        "Accepted changes may be copied into the developer-controlled local workspace.",
    ),
}


def _plain_mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        payload = value.model_dump(by_alias=False, mode="python")  # type: ignore[call-arg]
        return dict(payload) if isinstance(payload, Mapping) else {}
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _plain_value(value: Any) -> Any:
    if hasattr(value, "value"):
        return value.value
    return value


def _text(value: Any) -> str | None:
    value = _plain_value(value)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _float_or_none(value: Any) -> float | None:
    value = _plain_value(value)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _list_of_text(values: Any) -> list[str]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        return []
    normalized: list[str] = []
    for value in values:
        text = _text(value)
        if text:
            normalized.append(text)
    return normalized


def _normalize_changed_files(changed_files: Sequence[Mapping[str, Any]]) -> list[RefinementReviewChangedFile]:
    normalized: list[RefinementReviewChangedFile] = []
    for item in changed_files:
        payload = _plain_mapping(item)
        path = _text(payload.get("path"))
        if not path:
            continue
        normalized.append(
            RefinementReviewChangedFile(
                path=path,
                change_type=_text(payload.get("change_type")) or "modified",
                diff_preview=_text(payload.get("diff_preview")) or "",
                before_size=int(payload.get("before_size") or 0),
                after_size=int(payload.get("after_size") or 0),
            )
        )
    return normalized


def _write_back_for(mode: str | None, target: str | None) -> RefinementReviewWriteBack:
    resolved_mode = mode or "generated_artifact"
    label, description = _WRITE_BACK_LABELS.get(
        resolved_mode,
        ("Review staged output", "Accepted changes stay in the selected staged output lane."),
    )
    return RefinementReviewWriteBack(
        mode=resolved_mode,
        target=target,
        label=label,
        description=description,
    )


def _actions_for(
    *,
    can_accept: bool,
    can_reject: bool,
    can_promote: bool,
    validation_blocker: str | None,
) -> list[RefinementReviewAction]:
    accept_reason = None if can_accept else validation_blocker or "Artifact is not ready to accept."
    promote_reason = None if can_promote else "Only accepted current app bundles with passing validation can be promoted."
    return [
        RefinementReviewAction(id="accept", label="Accept", enabled=can_accept, reason=accept_reason),
        RefinementReviewAction(
            id="reject",
            label="Reject",
            enabled=can_reject,
            reason=None if can_reject else "Only draft artifacts can be rejected.",
        ),
        RefinementReviewAction(id="promote", label="Promote", enabled=can_promote, reason=promote_reason),
        RefinementReviewAction(
            id="retry",
            label="Retry",
            enabled=False,
            reason="Retry is not wired for this review package yet.",
        ),
        RefinementReviewAction(
            id="reroute",
            label="Reroute",
            enabled=False,
            reason="Reroute is not wired for this review package yet.",
        ),
    ]


def _risk_notes(
    *,
    validation_blocker: str | None,
    current_skipped_files: Sequence[str],
    parent_skipped_files: Sequence[str],
    review_snapshot: Mapping[str, Any],
    write_back_mode: str,
) -> list[str]:
    notes: list[str] = []
    review_note = _text(review_snapshot.get("notes"))
    if review_note:
        notes.append(review_note)
    if validation_blocker:
        notes.append(validation_blocker)
    if current_skipped_files:
        notes.append(f"{len(current_skipped_files)} current bundle file(s) were skipped during text diff generation.")
    if parent_skipped_files:
        notes.append(f"{len(parent_skipped_files)} parent bundle file(s) were skipped during text diff generation.")
    if write_back_mode == "external_patch":
        notes.append("External source must receive a patch or pull-request proposal; chat acceptance must not mutate it directly.")
    return notes


def build_refinement_review_package(
    *,
    app_id: str,
    artifact_version_id: str,
    artifact_kind: str,
    artifact_key: str,
    parent_version_id: str | None,
    lifecycle_status: str,
    validation_status: str,
    review_status: str,
    changed_files: Sequence[Mapping[str, Any]],
    selected_paths: Sequence[str],
    current_skipped_files: Sequence[str],
    parent_skipped_files: Sequence[str],
    refinement_metadata: Mapping[str, Any] | None = None,
    change_request: Any = None,
    latest_session: Any = None,
    coding_summary: str | None = None,
    validation_result: Mapping[str, Any] | None = None,
    validation_override_required: bool = False,
    validation_blocker: str | None = None,
    can_accept: bool = False,
    can_reject: bool = False,
    can_promote: bool = False,
) -> RefinementReviewPackage:
    refinement = _plain_mapping(refinement_metadata)
    review_snapshot = _plain_mapping(refinement.get("review"))
    change_request_payload = _plain_mapping(change_request)
    change_intent = _plain_mapping(change_request_payload.get("change_intent"))
    impact_set = _plain_mapping(change_request_payload.get("impact_set"))
    router_decision = _plain_mapping(change_request_payload.get("router_decision"))
    latest_session_payload = _plain_mapping(latest_session)

    request_id = (
        _text(refinement.get("request_id"))
        or _text(change_request_payload.get("id"))
        or _text(change_request_payload.get("_id"))
        or _text(latest_session_payload.get("change_request_id"))
    )
    change_class = (
        _text(refinement.get("change_class"))
        or _text(change_intent.get("change_class"))
        or _text(change_request_payload.get("classification"))
    )
    refinement_lane = _text(refinement.get("refinement_lane"))
    workflow_sequence = _text(refinement.get("workflow_sequence")) or _text(impact_set.get("workflow_sequence"))
    target_workflow = _text(refinement.get("target_workflow")) or _text(impact_set.get("restart_from"))
    affected_paths = (
        _list_of_text(refinement.get("affected_bundle_paths"))
        or _list_of_text(impact_set.get("affected_bundle_paths"))
        or list(selected_paths)
    )
    write_back = _write_back_for(_text(review_snapshot.get("write_back_mode")), _text(review_snapshot.get("write_back_target")))
    route_decision = RefinementReviewRouteDecision(
        request_id=request_id,
        change_class=change_class,
        refinement_lane=refinement_lane,
        workflow_sequence=workflow_sequence,
        target_workflow=target_workflow,
        execution_mode=_text(router_decision.get("execution_mode")) or _text(latest_session_payload.get("provider")),
        rationale=_text(change_intent.get("rationale")),
        confidence=_float_or_none(change_intent.get("confidence")),
        signals=_list_of_text(change_intent.get("signals")),
        scope_summary=_text(impact_set.get("scope_summary")),
    )
    normalized_changed_files = _normalize_changed_files(changed_files)
    normalized_validation_result = dict(validation_result) if isinstance(validation_result, Mapping) else None
    risks = _risk_notes(
        validation_blocker=validation_blocker,
        current_skipped_files=current_skipped_files,
        parent_skipped_files=parent_skipped_files,
        review_snapshot=review_snapshot,
        write_back_mode=write_back.mode,
    )

    return RefinementReviewPackage(
        request_id=request_id,
        app_id=app_id,
        artifact_kind=artifact_kind,
        artifact_key=artifact_key,
        artifact_version_id=artifact_version_id,
        parent_version_id=parent_version_id,
        review_status=review_status,
        lifecycle_status=lifecycle_status,
        validation_status=validation_status,
        change_class=change_class,
        refinement_lane=refinement_lane,
        workflow_sequence=workflow_sequence,
        target_workflow=target_workflow,
        write_back_mode=write_back.mode,
        write_back_target=write_back.target,
        write_back_label=write_back.label,
        write_back_description=write_back.description,
        affected_paths=affected_paths,
        selected_paths=list(selected_paths),
        changed_file_count=len(normalized_changed_files),
        changed_files=normalized_changed_files,
        coding_summary=coding_summary,
        validation_result=normalized_validation_result,
        validation_override_required=validation_override_required,
        validation_blocker=validation_blocker,
        current_skipped_files=list(current_skipped_files),
        parent_skipped_files=list(parent_skipped_files),
        can_accept=can_accept,
        can_reject=can_reject,
        can_promote=can_promote,
        actions=_actions_for(
            can_accept=can_accept,
            can_reject=can_reject,
            can_promote=can_promote,
            validation_blocker=validation_blocker,
        ),
        risk_notes=risks,
        route_decision=route_decision,
        write_back=write_back,
    )


__all__ = [
    "REVIEW_PACKAGE_SCHEMA_VERSION",
    "RefinementReviewAction",
    "RefinementReviewChangedFile",
    "RefinementReviewPackage",
    "RefinementReviewRouteDecision",
    "RefinementReviewWriteBack",
    "build_refinement_review_package",
]
