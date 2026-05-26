from __future__ import annotations

import json
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from mozaiksai.control_plane.dry_run import RefinementExecutionPlan, path_has_secret_marker
from mozaiksai.control_plane.staging import WORKSPACE_DIRNAME, RefinementStagingResult

EXECUTION_RESULT_FILENAME = "execution_result.json"

ScopedRefinementFileStatus = Literal[
    "updated",
    "skipped_missing",
    "skipped_secret",
    "skipped_unsafe",
]

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


class ScopedRefinementChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    new_content: str
    reason: str = ""


class ScopedRefinementChangedFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    status: ScopedRefinementFileStatus
    reason: str
    staged_path: str | None = None


class ScopedRefinementResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    staging_area: str
    changed_files: list[ScopedRefinementChangedFile] = Field(default_factory=list)
    execution_mode: Literal["scoped_staging"] = "scoped_staging"
    source_mutated: Literal[False] = False
    mutation_scope: Literal["staging_only"] = "staging_only"
    mutation_allowed: Literal[False] = False
    result_path: str


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


def _normalize_change_path(path: str) -> tuple[str | None, ScopedRefinementFileStatus | None, str | None]:
    raw = str(path or "").strip()
    if not raw:
        return None, "skipped_unsafe", "Empty change path."
    if "\x00" in raw:
        return None, "skipped_unsafe", "Change path contains a null byte."
    if PureWindowsPath(raw).is_absolute() or PureWindowsPath(raw).drive:
        return None, "skipped_unsafe", "Absolute or drive-qualified paths are not allowed."
    if raw.startswith(("/", "\\", "//", "\\\\")) or PurePosixPath(raw).is_absolute():
        return None, "skipped_unsafe", "Absolute paths are not allowed."

    normalized = raw.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    parts = [part for part in PurePosixPath(normalized).parts if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        return None, "skipped_unsafe", "Path traversal is not allowed."

    relative_path = "/".join(parts)
    lowered = relative_path.lower()
    if path_has_secret_marker(relative_path) or any(term in lowered for term in _SECRET_PATH_TERMS):
        return relative_path, "skipped_secret", "Secret-sensitive paths are not updated by scoped execution."
    if any(char in relative_path for char in _GLOB_CHARS):
        return relative_path, "skipped_unsafe", "Glob paths are not valid scoped execution targets."
    return relative_path, None, None


def _safe_allowed_directories(paths: set[str]) -> set[str]:
    directories: set[str] = set()
    for path in paths:
        parent = PurePosixPath(path).parent.as_posix()
        if parent and parent != ".":
            directories.add(parent)
    return directories


def _is_allowed_new_file(path: str, affected_paths: set[str]) -> bool:
    parent = PurePosixPath(path).parent.as_posix()
    return bool(parent and parent != "." and parent in _safe_allowed_directories(affected_paths))


def _change_is_in_scope(
    *,
    path: str,
    affected_paths: set[str],
    allow_new_files: bool,
) -> bool:
    if path in affected_paths:
        return True
    return allow_new_files and _is_allowed_new_file(path, affected_paths)


def _stage_file_path(staging_area: Path, relative_path: str) -> Path:
    return _resolve_inside(staging_area, staging_area / WORKSPACE_DIRNAME / relative_path)


def _apply_change(
    *,
    staging_area: Path,
    affected_paths: set[str],
    copied_paths: set[str],
    change: ScopedRefinementChange,
    allow_new_files: bool,
) -> ScopedRefinementChangedFile:
    relative_path, skip_status, reason = _normalize_change_path(change.path)
    reported_path = relative_path or change.path
    if skip_status is not None:
        return ScopedRefinementChangedFile(path=reported_path, status=skip_status, reason=reason or "Skipped.")
    if not _change_is_in_scope(path=relative_path, affected_paths=affected_paths, allow_new_files=allow_new_files):
        return ScopedRefinementChangedFile(
            path=relative_path,
            status="skipped_unsafe",
            reason="Change path is not in affected_bundle_paths.",
        )
    if relative_path not in copied_paths:
        return ScopedRefinementChangedFile(
            path=relative_path,
            status="skipped_missing",
            reason="Affected file was not copied into the staging workspace.",
        )

    target = _stage_file_path(staging_area, relative_path)
    if not target.exists() or not target.is_file():
        return ScopedRefinementChangedFile(
            path=relative_path,
            status="skipped_missing",
            reason="Staged file does not exist; this first scoped execution slice updates existing staged files only.",
        )
    if target.is_symlink():
        return ScopedRefinementChangedFile(path=relative_path, status="skipped_unsafe", reason="Symlinks are not updated.")

    target.write_text(change.new_content, encoding="utf-8")
    return ScopedRefinementChangedFile(
        path=relative_path,
        status="updated",
        reason=change.reason or "Scoped refinement change applied to staging workspace.",
        staged_path=target.as_posix(),
    )


def apply_scoped_refinement_changes(
    *,
    plan: RefinementExecutionPlan,
    staging_result: RefinementStagingResult,
    changes: list[ScopedRefinementChange | dict[str, str]],
    allow_new_files: bool = False,
) -> ScopedRefinementResult:
    if plan.execution_mode != "staged":
        raise ValueError("Scoped refinement execution requires a staged execution plan.")
    if plan.mutation_allowed is not False or staging_result.mutation_allowed is not False:
        raise ValueError("Scoped refinement execution requires mutation_allowed=false on plan and staging result.")
    if plan.request_id != staging_result.request_id:
        raise ValueError("Scoped refinement execution requires matching plan and staging request_id.")

    staging_area = Path(staging_result.staging_area).resolve()
    workspace_area = _resolve_inside(staging_area, staging_area / WORKSPACE_DIRNAME)
    workspace_area.mkdir(parents=True, exist_ok=True)
    affected_paths = {
        path
        for path in (
            _normalize_change_path(affected_path)[0]
            for affected_path in plan.affected_bundle_paths
        )
        if path
    }
    copied_paths = {file.path for file in staging_result.files if file.status == "copied"}
    parsed_changes = [
        change if isinstance(change, ScopedRefinementChange) else ScopedRefinementChange.model_validate(change)
        for change in changes
    ]
    changed_files = [
        _apply_change(
            staging_area=staging_area,
            affected_paths=affected_paths,
            copied_paths=copied_paths,
            change=change,
            allow_new_files=allow_new_files,
        )
        for change in parsed_changes
    ]

    result_path = _resolve_inside(staging_area, staging_area / EXECUTION_RESULT_FILENAME)
    result = ScopedRefinementResult(
        request_id=plan.request_id,
        staging_area=staging_area.as_posix(),
        changed_files=changed_files,
        result_path=result_path.as_posix(),
    )
    result_path.write_text(json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


__all__ = [
    "EXECUTION_RESULT_FILENAME",
    "ScopedRefinementChange",
    "ScopedRefinementChangedFile",
    "ScopedRefinementResult",
    "apply_scoped_refinement_changes",
]
