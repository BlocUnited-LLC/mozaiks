from __future__ import annotations

import json
import shutil
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from mozaiksai.control_plane.dry_run import RefinementExecutionPlan, path_has_secret_marker

DEFAULT_STAGING_ROOT = Path(".refinement_staging")
README_FILENAME = "README.md"
PLAN_FILENAME = "refinement_plan.json"
AFFECTED_PATHS_FILENAME = "affected_paths.json"
WORKSPACE_DIRNAME = "workspace"

StagedFileStatus = Literal[
    "copied",
    "missing",
    "skipped_secret",
    "skipped_glob",
    "skipped_unsafe",
]

_GLOB_CHARS = ("*", "?", "[")
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


class RefinementStagedFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    status: StagedFileStatus
    reason: str
    staged_path: str | None = None


class RefinementStagingResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    staging_area: str
    plan_path: str
    affected_paths_path: str
    manifest_path: str
    files: list[RefinementStagedFile] = Field(default_factory=list)
    source_bundle_path: str | None = None
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


def _plan_staging_path(plan: RefinementExecutionPlan, staging_root: Path | None) -> Path:
    raw = plan.staging_area or plan.output_workspace
    if raw:
        candidate = Path(raw)
    else:
        base = staging_root or DEFAULT_STAGING_ROOT
        candidate = base / plan.request_id

    if staging_root is not None:
        root = staging_root.resolve()
        resolved = candidate.resolve()
        if not _is_relative_to(resolved, root):
            raise ValueError("Refinement staging area must stay inside staging_root.")
    return candidate


def validate_staging_path(plan: RefinementExecutionPlan, staging_root: Path | None = None) -> Path:
    if plan.execution_mode != "staged":
        raise ValueError("Refinement staging workspace can only be created for staged execution plans.")
    if plan.mutation_allowed is not False:
        raise ValueError("Refinement staging workspace requires mutation_allowed=false.")
    return _plan_staging_path(plan, staging_root)


def _normalize_bundle_path(path: str) -> tuple[str | None, StagedFileStatus | None, str | None]:
    raw = str(path or "").strip()
    if not raw:
        return None, "skipped_unsafe", "Empty affected path."
    if "\x00" in raw:
        return None, "skipped_unsafe", "Affected path contains a null byte."
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
        return relative_path, "skipped_secret", "Secret-sensitive paths are not copied into staging."
    if any(char in relative_path for char in _GLOB_CHARS):
        return relative_path, "skipped_glob", "Glob paths are recorded but not expanded or copied."
    return relative_path, None, None


def _copy_single_affected_file(
    *,
    staging_area: Path,
    source_bundle_path: Path | None,
    affected_path: str,
) -> RefinementStagedFile:
    relative_path, skip_status, reason = _normalize_bundle_path(affected_path)
    reported_path = relative_path or str(affected_path)
    if skip_status is not None:
        return RefinementStagedFile(path=reported_path, status=skip_status, reason=reason or "Skipped.")

    if source_bundle_path is None:
        return RefinementStagedFile(
            path=reported_path,
            status="missing",
            reason="source_bundle_path was not provided; no file copy was attempted.",
        )

    source_root = source_bundle_path.resolve()
    source_file = (source_root / reported_path).resolve()
    if not _is_relative_to(source_file, source_root):
        return RefinementStagedFile(
            path=reported_path,
            status="skipped_unsafe",
            reason="Resolved source path escapes source_bundle_path.",
        )
    if not source_file.exists() or not source_file.is_file():
        return RefinementStagedFile(path=reported_path, status="missing", reason="Affected file was not found.")
    if source_file.is_symlink():
        return RefinementStagedFile(path=reported_path, status="skipped_unsafe", reason="Symlinks are not copied.")

    destination = staging_area / WORKSPACE_DIRNAME / reported_path
    destination_parent = _resolve_inside(staging_area, destination.parent)
    destination_parent.mkdir(parents=True, exist_ok=True)
    destination_resolved = _resolve_inside(staging_area, destination)
    shutil.copy2(source_file, destination_resolved)
    return RefinementStagedFile(
        path=reported_path,
        status="copied",
        reason="Copied from source_bundle_path for review.",
        staged_path=destination_resolved.as_posix(),
    )


def safe_copy_affected_files(
    *,
    plan: RefinementExecutionPlan,
    staging_area: Path,
    source_bundle_path: Path | None = None,
) -> list[RefinementStagedFile]:
    return [
        _copy_single_affected_file(
            staging_area=staging_area,
            source_bundle_path=source_bundle_path,
            affected_path=path,
        )
        for path in plan.affected_bundle_paths
    ]


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _readme_text(plan: RefinementExecutionPlan) -> str:
    return "\n".join(
        [
            "# Refinement Staging Workspace",
            "",
            f"Request ID: `{plan.request_id}`",
            f"Execution mode: `{plan.execution_mode}`",
            "",
            "This directory is an isolated staging workspace for refinement review.",
            "No source app files were mutated.",
            "Refinement execution has not run.",
            "Human approval is required before applying or promoting any staged output.",
            "",
        ]
    )


def create_refinement_staging_workspace(
    plan: RefinementExecutionPlan,
    *,
    source_bundle_path: Path | str | None = None,
    staging_root: Path | str | None = None,
) -> RefinementStagingResult:
    staging_root_path = Path(staging_root).resolve() if staging_root is not None else None
    staging_area = validate_staging_path(plan, staging_root_path).resolve()
    if staging_root_path is not None and not _is_relative_to(staging_area, staging_root_path):
        raise ValueError("Refinement staging area must stay inside staging_root.")

    staging_area.mkdir(parents=True, exist_ok=True)
    source_path = Path(source_bundle_path).resolve() if source_bundle_path is not None else None

    files = safe_copy_affected_files(
        plan=plan,
        staging_area=staging_area,
        source_bundle_path=source_path,
    )

    plan_path = _resolve_inside(staging_area, staging_area / PLAN_FILENAME)
    affected_paths_path = _resolve_inside(staging_area, staging_area / AFFECTED_PATHS_FILENAME)
    manifest_path = _resolve_inside(staging_area, staging_area / README_FILENAME)

    _write_json(plan_path, plan.model_dump(mode="json"))
    _write_json(
        affected_paths_path,
        {
            "request_id": plan.request_id,
            "affected_bundle_paths": list(plan.affected_bundle_paths),
            "source_bundle_path": source_path.as_posix() if source_path is not None else None,
            "files": [file.model_dump(mode="json") for file in files],
        },
    )
    manifest_path.write_text(_readme_text(plan), encoding="utf-8")

    return RefinementStagingResult(
        request_id=plan.request_id,
        staging_area=staging_area.as_posix(),
        plan_path=plan_path.as_posix(),
        affected_paths_path=affected_paths_path.as_posix(),
        manifest_path=manifest_path.as_posix(),
        files=files,
        source_bundle_path=source_path.as_posix() if source_path is not None else None,
        mutation_allowed=False,
    )


__all__ = [
    "AFFECTED_PATHS_FILENAME",
    "DEFAULT_STAGING_ROOT",
    "PLAN_FILENAME",
    "README_FILENAME",
    "RefinementStagedFile",
    "RefinementStagingResult",
    "WORKSPACE_DIRNAME",
    "create_refinement_staging_workspace",
    "safe_copy_affected_files",
    "validate_staging_path",
]
