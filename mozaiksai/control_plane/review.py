from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from mozaiksai.control_plane.staging import RefinementStagingResult

REVIEW_FILENAME = "refinement_review.json"

RefinementReviewStatus = Literal[
    "staged",
    "review_pending",
    "approved",
    "rejected",
    "promotion_ready",
    "promoted",
]
RefinementWriteBackMode = Literal[
    "generated_artifact",
    "external_patch",
    "mozaiks_overlay",
    "full_migration_artifact",
    "local_workspace",
]

_INITIAL_REVIEW_STATUSES = {"staged", "review_pending"}
_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "staged": {"review_pending"},
    "review_pending": {"approved", "rejected"},
    "approved": {"promotion_ready"},
    "rejected": set(),
    "promotion_ready": {"promoted"},
    "promoted": set(),
}
_SECRET_NOTE_RE = re.compile(
    r"(?i)\b(api[_-]?key|token|password|secret|credential|private[_-]?key)\b\s*[:=]\s*([^\s,;]+)"
)


class RefinementReviewTransitionError(ValueError):
    """Raised when a staged refinement review transition is not allowed."""


class RefinementReviewRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    status: RefinementReviewStatus = "review_pending"
    reviewer: str | None = None
    reviewed_at: str | None = None
    decision: str | None = None
    notes: str | None = None
    promotion_allowed: bool = False
    source_bundle_path: str | None = None
    staging_area: str
    affected_bundle_paths: list[str] = Field(default_factory=list)
    write_back_mode: RefinementWriteBackMode = "generated_artifact"
    write_back_target: str | None = None
    mutation_allowed: Literal[False] = False


def _is_relative_to(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _resolve_inside(parent: Path, child: Path) -> Path:
    parent_resolved = parent.resolve()
    child_resolved = child.resolve()
    if not _is_relative_to(child_resolved, parent_resolved):
        raise ValueError(f"Refusing to write outside staging area: {child}")
    return child_resolved


def _review_path(staging_area: Path | str) -> Path:
    staging_path = Path(staging_area).resolve()
    return _resolve_inside(staging_path, staging_path / REVIEW_FILENAME)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def redact_review_notes(notes: str | None) -> str | None:
    if notes is None:
        return None
    normalized = str(notes)
    return _SECRET_NOTE_RE.sub(lambda match: f"{match.group(1)}=<redacted>", normalized)


def _affected_paths_from_result(staging_result: RefinementStagingResult) -> list[str]:
    try:
        payload = json.loads(Path(staging_result.affected_paths_path).read_text(encoding="utf-8"))
    except Exception:
        return [file.path for file in staging_result.files]
    raw_paths = payload.get("affected_bundle_paths")
    if isinstance(raw_paths, list):
        return [str(path) for path in raw_paths]
    return [file.path for file in staging_result.files]


def _write_review_record(record: RefinementReviewRecord) -> Path:
    path = _review_path(record.staging_area)
    path.write_text(json.dumps(record.model_dump(mode="json"), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def load_refinement_review_record(staging_area: Path | str) -> RefinementReviewRecord:
    path = _review_path(staging_area)
    if not path.exists():
        raise FileNotFoundError(f"Refinement review record not found: {path}")
    return RefinementReviewRecord.model_validate_json(path.read_text(encoding="utf-8"))


def create_refinement_review_record(
    staging_result: RefinementStagingResult,
    *,
    status: RefinementReviewStatus = "review_pending",
    reviewer: str | None = None,
    notes: str | None = None,
    write_back_mode: RefinementWriteBackMode = "generated_artifact",
    write_back_target: str | None = None,
) -> RefinementReviewRecord:
    if status not in _INITIAL_REVIEW_STATUSES:
        allowed = ", ".join(sorted(_INITIAL_REVIEW_STATUSES))
        raise RefinementReviewTransitionError(f"Initial review status must be one of: {allowed}.")

    record = RefinementReviewRecord(
        request_id=staging_result.request_id,
        status=status,
        reviewer=reviewer,
        reviewed_at=None,
        decision=None,
        notes=redact_review_notes(notes),
        promotion_allowed=False,
        source_bundle_path=staging_result.source_bundle_path,
        staging_area=staging_result.staging_area,
        affected_bundle_paths=_affected_paths_from_result(staging_result),
        write_back_mode=write_back_mode,
        write_back_target=str(write_back_target).strip() or None if write_back_target is not None else None,
        mutation_allowed=False,
    )
    _write_review_record(record)
    return record


def _transition_review_record(
    *,
    staging_area: Path | str,
    target_status: RefinementReviewStatus,
    reviewer: str | None = None,
    notes: str | None = None,
    decision: str | None = None,
    promotion_allowed: bool = False,
) -> RefinementReviewRecord:
    record = load_refinement_review_record(staging_area)
    allowed = _ALLOWED_TRANSITIONS.get(record.status, set())
    if target_status not in allowed:
        allowed_text = ", ".join(sorted(allowed)) or "no further transitions"
        raise RefinementReviewTransitionError(
            f"Cannot transition staged refinement review from '{record.status}' to '{target_status}'. "
            f"Allowed: {allowed_text}."
        )

    updated = record.model_copy(
        update={
            "status": target_status,
            "reviewer": reviewer or record.reviewer,
            "reviewed_at": _now_iso(),
            "decision": decision or target_status,
            "notes": redact_review_notes(notes) if notes is not None else record.notes,
            "promotion_allowed": promotion_allowed,
            "mutation_allowed": False,
        }
    )
    _write_review_record(updated)
    return updated


def approve_refinement_staging(
    staging_area: Path | str,
    *,
    reviewer: str | None = None,
    notes: str | None = None,
) -> RefinementReviewRecord:
    return _transition_review_record(
        staging_area=staging_area,
        target_status="approved",
        reviewer=reviewer,
        notes=notes,
        decision="approved",
        promotion_allowed=False,
    )


def mark_refinement_review_pending(
    staging_area: Path | str,
    *,
    reviewer: str | None = None,
    notes: str | None = None,
) -> RefinementReviewRecord:
    return _transition_review_record(
        staging_area=staging_area,
        target_status="review_pending",
        reviewer=reviewer,
        notes=notes,
        decision="review_pending",
        promotion_allowed=False,
    )


def reject_refinement_staging(
    staging_area: Path | str,
    *,
    reviewer: str | None = None,
    notes: str | None = None,
) -> RefinementReviewRecord:
    return _transition_review_record(
        staging_area=staging_area,
        target_status="rejected",
        reviewer=reviewer,
        notes=notes,
        decision="rejected",
        promotion_allowed=False,
    )


def mark_refinement_promotion_ready(
    staging_area: Path | str,
    *,
    reviewer: str | None = None,
    notes: str | None = None,
) -> RefinementReviewRecord:
    return _transition_review_record(
        staging_area=staging_area,
        target_status="promotion_ready",
        reviewer=reviewer,
        notes=notes,
        decision="promotion_ready",
        promotion_allowed=True,
    )


def mark_refinement_promoted(
    staging_area: Path | str,
    *,
    reviewer: str | None = None,
    notes: str | None = None,
) -> RefinementReviewRecord:
    record = load_refinement_review_record(staging_area)
    if record.status != "promotion_ready":
        raise RefinementReviewTransitionError(
            f"Cannot promote staged refinement from '{record.status}'. Allowed: promotion_ready."
        )
    if record.promotion_allowed is not True:
        raise RefinementReviewTransitionError("Cannot promote staged refinement unless promotion_allowed is true.")
    return _transition_review_record(
        staging_area=staging_area,
        target_status="promoted",
        reviewer=reviewer,
        notes=notes,
        decision="promoted",
        promotion_allowed=False,
    )


__all__ = [
    "REVIEW_FILENAME",
    "RefinementReviewRecord",
    "RefinementReviewStatus",
    "RefinementReviewTransitionError",
    "RefinementWriteBackMode",
    "approve_refinement_staging",
    "create_refinement_review_record",
    "load_refinement_review_record",
    "mark_refinement_promotion_ready",
    "mark_refinement_promoted",
    "mark_refinement_review_pending",
    "redact_review_notes",
    "reject_refinement_staging",
]
