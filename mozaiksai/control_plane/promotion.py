from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from mozaiksai.control_plane.dry_run import RefinementExecutionPlan, path_has_secret_marker
from mozaiksai.control_plane.promotion_policy import (
    PromotionPolicyDecision,
    evaluate_refinement_promotion_policy,
)
from mozaiksai.control_plane.review import (
    RefinementReviewRecord,
    load_refinement_review_record,
    mark_refinement_promoted,
)
from mozaiksai.control_plane.scoped_execution import (
    EXECUTION_RESULT_FILENAME,
    ScopedRefinementChangedFile,
    ScopedRefinementResult,
)
from mozaiksai.control_plane.staging import WORKSPACE_DIRNAME, RefinementStagingResult
from mozaiksai.control_plane.validation_evidence import (
    ValidationEvidence,
    normalize_validation_evidence,
)

PROMOTION_BACKUP_DIRNAME = "backups"

RefinementPromotionFileStatus = Literal["promoted", "skipped", "blocked", "failed"]

_SECRET_PATH_TERMS = (
    ".env",
    "secret",
    "secrets",
    "vault",
    "credential",
    "credentials",
    "private_key",
    "private-key",
    "id_rsa",
    "id_dsa",
    ".pem",
    ".key",
)
_GLOB_CHARS = ("*", "?", "[")


class RefinementPromotionError(ValueError):
    """Raised when a staged refinement promotion cannot proceed."""


class RefinementPromotionFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    status: RefinementPromotionFileStatus
    reason: str
    backup_path: str | None = None


class RefinementPromotionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    source_bundle_path: str
    staging_area: str
    promoted_files: list[RefinementPromotionFile] = Field(default_factory=list)
    policy_decisions: list[PromotionPolicyDecision] = Field(default_factory=list)
    dry_run: bool = True
    mutation_applied: bool = False
    review_status_before: str
    review_status_after: str


@dataclass(frozen=True)
class _PromotionTarget:
    path: str
    source_file: Path
    staged_file: Path
    backup_file: Path | None


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
        raise RefinementPromotionError(f"Refusing to write outside allowed workspace: {child}")
    return child_resolved


def _normalize_bundle_path(path: str) -> tuple[str | None, RefinementPromotionFileStatus | None, str | None]:
    raw = str(path or "").strip()
    if not raw:
        return None, "skipped", "Empty promotion path."
    if "\x00" in raw:
        return None, "skipped", "Promotion path contains a null byte."
    if PureWindowsPath(raw).is_absolute() or PureWindowsPath(raw).drive:
        return None, "skipped", "Absolute or drive-qualified paths are not allowed."
    if raw.startswith(("/", "\\", "//", "\\\\")) or PurePosixPath(raw).is_absolute():
        return None, "skipped", "Absolute paths are not allowed."

    normalized = raw.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    parts = [part for part in PurePosixPath(normalized).parts if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        return None, "skipped", "Path traversal is not allowed."

    relative_path = "/".join(parts)
    lowered = relative_path.lower()
    if path_has_secret_marker(relative_path) or any(term in lowered for term in _SECRET_PATH_TERMS):
        return relative_path, "skipped", "Secret-sensitive paths are not promoted."
    if any(char in relative_path for char in _GLOB_CHARS):
        return relative_path, "skipped", "Glob paths are not promoted."
    return relative_path, None, None


def _contains_symlink_component(root: Path, relative_path: str) -> bool:
    if root.is_symlink():
        return True
    current = root
    for part in PurePosixPath(relative_path).parts:
        current = current / part
        if current.exists() and current.is_symlink():
            return True
    return False


def _load_execution_result(
    execution_result: ScopedRefinementResult,
    *,
    staging_area: Path,
) -> ScopedRefinementResult:
    result_path = _resolve_inside(staging_area, Path(execution_result.result_path))
    if result_path.name != EXECUTION_RESULT_FILENAME:
        raise RefinementPromotionError("execution_result.result_path must point to execution_result.json.")
    if not result_path.exists():
        raise RefinementPromotionError(f"Execution result not found: {result_path}")

    disk_result = ScopedRefinementResult.model_validate_json(result_path.read_text(encoding="utf-8"))
    if disk_result.model_dump(mode="json") != execution_result.model_dump(mode="json"):
        raise RefinementPromotionError("Execution result on disk does not match the provided execution_result.")
    return disk_result


def _validate_review_record(
    review_record: RefinementReviewRecord,
    *,
    staging_area: Path,
) -> RefinementReviewRecord:
    disk_record = load_refinement_review_record(staging_area)
    if disk_record.model_dump(mode="json") != review_record.model_dump(mode="json"):
        raise RefinementPromotionError("Review record on disk does not match the provided review_record.")
    if disk_record.status != "promotion_ready":
        raise RefinementPromotionError(
            f"Cannot promote staged refinement from '{disk_record.status}'. Allowed: promotion_ready."
        )
    if disk_record.promotion_allowed is not True:
        raise RefinementPromotionError("Promotion requires review.promotion_allowed=true.")
    return disk_record


def _prepare_promotion_file(
    *,
    change: ScopedRefinementChangedFile,
    copied_paths: set[str],
    source_root: Path,
    staging_area: Path,
    dry_run: bool,
    backup: bool,
) -> tuple[RefinementPromotionFile, _PromotionTarget | None]:
    relative_path, skip_status, reason = _normalize_bundle_path(change.path)
    reported_path = relative_path or change.path
    if skip_status is not None:
        return RefinementPromotionFile(path=reported_path, status=skip_status, reason=reason or "Skipped."), None

    if relative_path not in copied_paths:
        return (
            RefinementPromotionFile(
                path=relative_path,
                status="skipped",
                reason="Affected file was not copied into the staging workspace.",
            ),
            None,
        )

    if change.status != "updated":
        return (
            RefinementPromotionFile(
                path=relative_path,
                status="skipped",
                reason=change.reason or "Scoped execution did not update this file.",
            ),
            None,
        )

    source_file = source_root / relative_path
    staged_file = staging_area / WORKSPACE_DIRNAME / relative_path

    if not staged_file.exists() or not staged_file.is_file():
        return (
            RefinementPromotionFile(
                path=relative_path,
                status="skipped",
                reason="Staged file does not exist; only copied staged files can be promoted.",
            ),
            None,
        )
    if staged_file.is_symlink() or _contains_symlink_component(staging_area / WORKSPACE_DIRNAME, relative_path):
        return (
            RefinementPromotionFile(
                path=relative_path,
                status="skipped",
                reason="Symlinked staged files are not promoted.",
            ),
            None,
        )

    if not source_file.exists() or not source_file.is_file():
        return (
            RefinementPromotionFile(
                path=relative_path,
                status="skipped",
                reason="Source file does not exist; new files are not promoted in this slice.",
            ),
            None,
        )
    if source_file.is_symlink() or _contains_symlink_component(source_root, relative_path):
        return (
            RefinementPromotionFile(
                path=relative_path,
                status="skipped",
                reason="Symlinked source files are not promoted.",
            ),
            None,
        )

    backup_file = None
    if backup:
        backup_file = _resolve_inside(staging_area, staging_area / PROMOTION_BACKUP_DIRNAME / relative_path)

    reason_text = (
        "Would be promoted from staging workspace to source bundle."
        if dry_run
        else "Staged file is ready to promote."
    )
    return (
        RefinementPromotionFile(
            path=relative_path,
            status="promoted",
            reason=reason_text,
            backup_path=backup_file.as_posix() if backup_file is not None else None,
        ),
        _PromotionTarget(
            path=relative_path,
            source_file=source_file.resolve(),
            staged_file=staged_file.resolve(),
            backup_file=backup_file,
        ),
    )


def promote_refinement_staging(
    plan: RefinementExecutionPlan,
    staging_result: RefinementStagingResult,
    review_record: RefinementReviewRecord,
    execution_result: ScopedRefinementResult,
    *,
    source_bundle_path: Path | str,
    confirm: bool,
    dry_run: bool = True,
    backup: bool = True,
    validation_evidence: ValidationEvidence | dict[str, object] | list[str] | None = None,
) -> RefinementPromotionResult:
    if plan.execution_mode != "staged":
        raise RefinementPromotionError("Refinement promotion requires a staged execution plan.")
    if plan.mutation_allowed is not False or staging_result.mutation_allowed is not False:
        raise RefinementPromotionError("Refinement promotion requires mutation_allowed=false on plan and staging result.")
    if review_record.mutation_allowed is not False:
        raise RefinementPromotionError("Refinement promotion requires mutation_allowed=false on the review record.")
    if execution_result.mutation_allowed is not False or execution_result.source_mutated is not False:
        raise RefinementPromotionError("Refinement promotion requires a non-mutating scoped execution result.")
    if execution_result.execution_mode != "scoped_staging" or execution_result.mutation_scope != "staging_only":
        raise RefinementPromotionError("Refinement promotion requires a scoped staging execution result.")

    if plan.request_id != staging_result.request_id:
        raise RefinementPromotionError("Plan and staging request_id values must match for promotion.")
    if plan.request_id != review_record.request_id or plan.request_id != execution_result.request_id:
        raise RefinementPromotionError("Plan, review, staging, and execution request_id values must match.")

    staging_area = Path(staging_result.staging_area)
    if not staging_area.exists() or not staging_area.is_dir():
        raise RefinementPromotionError(f"Staging area does not exist: {staging_area}")
    if staging_area.is_symlink():
        raise RefinementPromotionError("Staging area cannot be a symlink.")

    staging_area = staging_area.resolve()
    workspace_area = _resolve_inside(staging_area, staging_area / WORKSPACE_DIRNAME)
    if not workspace_area.exists() or not workspace_area.is_dir():
        raise RefinementPromotionError(f"Staging workspace does not exist: {workspace_area}")
    if workspace_area.is_symlink():
        raise RefinementPromotionError("Staging workspace cannot be a symlink.")

    source_root = Path(source_bundle_path)
    if not source_root.exists() or not source_root.is_dir():
        raise RefinementPromotionError(f"Source bundle path does not exist or is not a directory: {source_root}")
    if source_root.is_symlink():
        raise RefinementPromotionError("Source bundle path cannot be a symlink.")
    source_root = source_root.resolve()

    if staging_result.source_bundle_path is not None and Path(staging_result.source_bundle_path).resolve() != source_root:
        raise RefinementPromotionError("Provided source bundle path does not match the staging result.")
    if review_record.source_bundle_path is not None and Path(review_record.source_bundle_path).resolve() != source_root:
        raise RefinementPromotionError("Provided source bundle path does not match the review record.")

    loaded_review = _validate_review_record(review_record, staging_area=staging_area)
    loaded_execution = _load_execution_result(execution_result, staging_area=staging_area)
    normalized_validation_evidence = normalize_validation_evidence(validation_evidence)

    copied_paths = {file.path for file in staging_result.files if file.status == "copied"}

    if not confirm and not dry_run:
        raise RefinementPromotionError("Promotion requires confirm=true when dry_run=false.")

    promoted_files: list[RefinementPromotionFile] = []
    policy_decisions: list[PromotionPolicyDecision] = []
    mutation_applied = False
    for changed_file in loaded_execution.changed_files:
        promotion_file, target = _prepare_promotion_file(
            change=changed_file,
            copied_paths=copied_paths,
            source_root=source_root,
            staging_area=staging_area,
            dry_run=dry_run,
            backup=backup,
        )
        if target is None or promotion_file.status != "promoted":
            promoted_files.append(promotion_file)
            continue

        policy_decision = evaluate_refinement_promotion_policy(
            plan=plan,
            review_record=loaded_review,
            execution_result=loaded_execution,
            change=changed_file,
            validation_evidence=normalized_validation_evidence,
        )
        policy_decisions.append(policy_decision)
        if not policy_decision.allowed:
            promoted_files.append(
                promotion_file.model_copy(
                    update={
                        "status": "blocked",
                        "reason": policy_decision.reason,
                    }
                )
            )
            continue

        if dry_run:
            promoted_files.append(promotion_file)
            continue

        try:
            backup_path = None
            if backup and target.backup_file is not None:
                target.backup_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target.source_file, target.backup_file)
                backup_path = target.backup_file.as_posix()

            shutil.copy2(target.staged_file, target.source_file)
            mutation_applied = True
            promoted_files.append(
                promotion_file.model_copy(
                    update={
                        "reason": "Promoted staged file to source bundle.",
                        "backup_path": backup_path,
                    }
                )
            )
        except Exception as exc:  # pragma: no cover - defensive path for operational failures
            promoted_files.append(
                promotion_file.model_copy(
                    update={
                        "status": "failed",
                        "reason": f"Failed to promote staged file: {exc}",
                    }
                )
            )

    review_status_after = loaded_review.status
    if mutation_applied and not any(file.status == "failed" for file in promoted_files):
        promoted_record = mark_refinement_promoted(staging_area)
        review_status_after = promoted_record.status

    return RefinementPromotionResult(
        request_id=plan.request_id,
        source_bundle_path=source_root.as_posix(),
        staging_area=staging_area.as_posix(),
        promoted_files=promoted_files,
        policy_decisions=policy_decisions,
        dry_run=dry_run,
        mutation_applied=mutation_applied,
        review_status_before=loaded_review.status,
        review_status_after=review_status_after,
    )


__all__ = [
    "PROMOTION_BACKUP_DIRNAME",
    "RefinementPromotionError",
    "RefinementPromotionFile",
    "RefinementPromotionFileStatus",
    "RefinementPromotionResult",
    "promote_refinement_staging",
]
