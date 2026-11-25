"""Register a validated media overlay as a new, build-bound app bundle draft."""

from __future__ import annotations

import hashlib
import io
import secrets
import zipfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any

from mozaiksai.control_plane.contracts import safe_artifact_relpath
from mozaiksai.core.artifacts.content_store import get_artifact_content_store
from mozaiksai.core.artifacts.models import (
    ArtifactLifecycleStatus,
    ArtifactValidationStatus,
    BuildRecord,
    canonical_bundle_archive_path,
    resolve_canonical_bundle_entry,
)
from mozaiksai.core.artifacts.store import get_artifact_store
from mozaiksai.core.session.build_binding import RunBuildBinding
from mozaiksai.core.utils.path_containment import contains_symlink_component, resolve_inside


def _archive(bundle_name: str, files: dict[str, str | bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            content = files[path]
            archive.writestr(
                f"{bundle_name}/{path}",
                content if isinstance(content, bytes) else content.encode("utf-8"),
            )
    return buffer.getvalue()


def _stage(directory: Path, files: dict[str, str | bytes]) -> None:
    destinations: list[tuple[Path, str | bytes]] = []
    seen: set[str] = set()
    for path, content in files.items():
        if safe_artifact_relpath(path) != path or path.casefold() in seen:
            raise ValueError("app_media_source_unsafe_asset_path")
        if contains_symlink_component(directory, path):
            raise ValueError("app_media_source_unsafe_asset_path")
        target = resolve_inside(directory, directory / PurePosixPath(path))
        seen.add(path.casefold())
        destinations.append((target, content))
    for target, content in destinations:
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8", newline="")


async def register_media_draft(
    *,
    parent: BuildRecord,
    files: dict[str, str | bytes],
    overlay: dict[str, Any],
    validation: dict[str, Any],
    chat_id: str,
    user_id: str,
    workflow_name: str,
    binding: RunBuildBinding,
    pending_review_assignment: bool = False,
    canonical_inputs_version: dict[str, str] | None = None,
    approved_compilation: Any = None,
    scope_guard: Callable[[], None] | None = None,
) -> tuple[BuildRecord, str]:
    if scope_guard is not None:
        scope_guard()
    source = parent.commit_metadata.metadata
    bundle_name = source["bundle_name"]
    archive_path = canonical_bundle_archive_path(bundle_name)
    parent_workspace = source.get("workspace_dir")
    if not isinstance(parent_workspace, str) or not parent_workspace:
        raise ValueError("app_media_source_workspace_missing")
    stage_root = Path(parent_workspace).parent / f"media_{secrets.token_hex(6)}"
    app_dir = stage_root / "app"
    _stage(app_dir, files)
    raw = _archive(bundle_name, files)
    zip_path = stage_root / f"{bundle_name}.zip"
    zip_path.write_bytes(raw)
    sha = hashlib.sha256(raw).hexdigest()

    # Per-candidate scope, file hashes and review evidence stay on their own build record.
    metadata: dict[str, Any] = {"bundle_name": bundle_name, **binding.model_dump()}
    if "capability_packs" in source:
        metadata["capability_packs"] = source["capability_packs"]
    if pending_review_assignment:
        metadata["requires_current_review_assignment"] = True
    metadata.update({
        "artifact_path": str(zip_path.resolve()),
        "workspace_dir": str(app_dir.resolve()),
        "media_overlay": overlay,
        "validation_result": validation,
        "validation_status": validation["validation_status"],
        "validation_strategy": validation["validation_strategy"],
        "app_bundle_acceptance": validation["app_bundle_acceptance_result"],
        "validation_evidence": validation["app_bundle_acceptance_result"].get("validation_evidence"),
        "app_validation_result": validation["app_validation_result"],
    })
    if approved_compilation is not None:
        metadata["compilation_inputs"] = approved_compilation.for_child(sha)
    content_store = get_artifact_content_store()
    if content_store.backend_name != "local":
        metadata["content_ref"] = await content_store.put_bundle(
            raw, app_id=parent.app_id, artifact_version_id=f"pending_{sha[:16]}",
        )
        metadata["content_backend"] = content_store.backend_name

    if scope_guard is not None:
        scope_guard()
    record = await get_artifact_store().create_build_record(
        app_id=parent.app_id,
        build_family=parent.build_family,
        build_key=parent.build_key,
        parent_build_record_id=parent.id,
        canonical_inputs_version=dict(
            parent.canonical_inputs_version if canonical_inputs_version is None else canonical_inputs_version
        ),
        files_manifest=[{
            "path": archive_path,
            "sha256": sha,
            "size_bytes": len(raw),
            "content_type": "application/zip",
        }],
        source_workflow=workflow_name,
        source_chat_id=chat_id,
        lifecycle_status=ArtifactLifecycleStatus.DRAFT,
        validation_status=ArtifactValidationStatus.PENDING if pending_review_assignment else validation["validation_status"],
        app_validation_status=validation["app_validation_result"]["validation_status"],
        app_validation_strategy=validation["validation_strategy"],
        commit_metadata={
            "message": f"{workflow_name}: {bundle_name}",
            "author_user_id": user_id,
            "source_workflow": workflow_name,
            "source_chat_id": chat_id,
            "metadata": metadata,
        },
    )
    if resolve_canonical_bundle_entry(record).sha256 != sha:
        raise RuntimeError("media bundle record digest does not match the archive just written")
    return record, str(app_dir.resolve())
