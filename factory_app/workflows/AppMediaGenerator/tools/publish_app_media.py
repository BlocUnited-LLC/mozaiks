"""Publish the images made in this run as a new draft version of the bound app bundle."""

from __future__ import annotations

import hashlib
import io
import json
import logging
import mimetypes
import secrets
import zipfile
from html import escape
from pathlib import Path, PurePosixPath
from typing import Any

from factory_app.app.modules.app_registry.backend.service import AppRegistryService
from factory_app.workflows._shared.artifact_bundle import read_artifact_bundle
from factory_app.workflows._shared.platform.current_build import resolve_current_build_artifact
from factory_app.workflows.AppMediaGenerator.tools.media_bundle import (
    ASSET_MANIFEST_PATH,
    ERROR_PREFIX,
    is_planned,
    parse_asset_manifest,
    planned_generated_entries,
)
from mozaiksai.core.artifacts.content_store import get_artifact_content_store
from mozaiksai.core.artifacts.models import (
    ArtifactLifecycleStatus,
    BuildRecord,
    canonical_bundle_archive_path,
    resolve_canonical_bundle_entry,
)
from mozaiksai.core.artifacts.store import get_artifact_store
from mozaiksai.core.media.store import get_media_asset_store
from mozaiksai.core.media.types import GeneratedMediaAsset
from mozaiksai.core.workflow.agents.factory import active_workflow_tool_run
from mozaiksai.core.workflow.context.frozen import detach

logger = logging.getLogger(__name__)

WORKFLOW_NAME = "AppMediaGenerator"
ASSET_DIR = "ui/assets"
_EXTENSION_BY_TYPE = {"image/webp": ".webp", "image/png": ".png", "image/jpeg": ".jpg"}
_PLACEHOLDER_SIZE = {"square": (1024, 1024), "landscape": (1536, 1024), "portrait": (1024, 1536)}
# Bundle record metadata that names the parent's own bytes or acceptance and must not carry over.
_PARENT_ONLY_METADATA = (
    "artifact_path", "workspace_dir", "content_ref", "content_backend", "content_digest",
    "accepted_file_manifest", "acceptance", "bundle_mode",
)


def _finish(context: Any, summary: dict[str, Any]) -> dict[str, Any]:
    context.set("app_media_summary", summary)
    context.set("app_media_complete", True)
    return summary


def _latest_asset_by_task(assets: list[GeneratedMediaAsset]) -> dict[str, GeneratedMediaAsset]:
    # Assets arrive newest first; a retried job keeps its most recent image.
    latest: dict[str, GeneratedMediaAsset] = {}
    for asset in assets:
        params = asset.generation_params or {}
        task_id = params.get("task_id")
        if (
            isinstance(task_id, str)
            and params.get("task_batch_id") == "app_media_jobs"
            and asset.media_type.startswith("image/")
            and task_id not in latest
        ):
            latest[task_id] = asset
    return latest


def _placeholder_svg(entry: dict[str, Any]) -> str:
    width, height = _PLACEHOLDER_SIZE.get(entry.get("aspect") or "", (1024, 1024))
    label = escape(str(entry.get("alt") or entry.get("brief") or entry["asset_id"]))[:120]
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-label="Photo to upload: {label}">'
        f'<rect width="100%" height="100%" fill="#eef0f3"/>'
        f'<rect x="24" y="24" width="{width - 48}" height="{height - 48}" fill="none" '
        f'stroke="#9aa3af" stroke-width="4" stroke-dasharray="24 16" rx="24"/>'
        f'<text x="50%" y="47%" text-anchor="middle" font-family="sans-serif" font-size="44" '
        f'fill="#4b5563">Photo to upload</text>'
        f'<text x="50%" y="55%" text-anchor="middle" font-family="sans-serif" font-size="28" '
        f'fill="#6b7280">{label}</text></svg>'
    )


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
    for path, content in files.items():
        target = directory / PurePosixPath(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8", newline="")


async def _register_media_version(
    *,
    parent: BuildRecord,
    files: dict[str, str | bytes],
    overlay: dict[str, Any],
    chat_id: str,
    user_id: str,
) -> tuple[BuildRecord, str]:
    metadata = dict(parent.commit_metadata.metadata)
    bundle_name = metadata["bundle_name"]
    parent_workspace = metadata.get("workspace_dir")
    if not isinstance(parent_workspace, str) or not parent_workspace:
        raise ValueError(f"{ERROR_PREFIX}_workspace_missing")
    stage_root = Path(parent_workspace).parent / f"media_{secrets.token_hex(6)}"
    app_dir = stage_root / "app"
    _stage(app_dir, files)
    raw = _archive(bundle_name, files)
    zip_path = stage_root / f"{bundle_name}.zip"
    zip_path.write_bytes(raw)
    sha = hashlib.sha256(raw).hexdigest()

    for key in _PARENT_ONLY_METADATA:
        metadata.pop(key, None)
    metadata.update({
        "artifact_path": str(zip_path.resolve()),
        "workspace_dir": str(app_dir.resolve()),
        "media_overlay": overlay,
    })
    content_store = get_artifact_content_store()
    if content_store.backend_name != "local":
        metadata["content_ref"] = await content_store.put_bundle(
            raw, app_id=parent.app_id, artifact_version_id=f"pending_{sha[:16]}",
        )
        metadata["content_backend"] = content_store.backend_name

    record = await get_artifact_store().create_build_record(
        app_id=parent.app_id,
        build_family=parent.build_family,
        build_key=parent.build_key,
        parent_build_record_id=parent.id,
        canonical_inputs_version=dict(parent.canonical_inputs_version),
        files_manifest=[{
            "path": canonical_bundle_archive_path(bundle_name),
            "sha256": sha,
            "size_bytes": len(raw),
            "content_type": "application/zip",
        }],
        source_workflow=WORKFLOW_NAME,
        source_chat_id=chat_id,
        lifecycle_status=ArtifactLifecycleStatus.DRAFT,
        validation_status=parent.validation_status,
        app_validation_status=parent.app_validation_status,
        app_validation_strategy=parent.app_validation_strategy,
        sandbox_session_id=parent.sandbox_session_id,
        sandbox_provider=parent.sandbox_provider,
        commit_metadata={
            "message": f"{WORKFLOW_NAME}: {bundle_name}",
            "author_user_id": user_id,
            "source_workflow": WORKFLOW_NAME,
            "source_chat_id": chat_id,
            "metadata": metadata,
        },
    )
    if resolve_canonical_bundle_entry(record).sha256 != sha:
        raise RuntimeError("media bundle record digest does not match the archive just written")
    return record, str(app_dir.resolve())


async def publish_app_media(context_variables: Any = None) -> dict[str, Any]:
    if context_variables is None:
        raise ValueError("Publishing app media requires runtime context")
    workflow, app_id, chat_id, user_id = active_workflow_tool_run()
    if workflow != WORKFLOW_NAME:
        raise ValueError(f"{ERROR_PREFIX}_workflow_mismatch")
    try:
        binding, record, parent = await resolve_current_build_artifact(
            context_variables, app_id=app_id, user_id=user_id, error_prefix=ERROR_PREFIX,
        )
        files, diagnostics = await read_artifact_bundle(parent, include_binary=True)
        if any(item.get("blocking") for item in diagnostics):
            raise ValueError(f"{ERROR_PREFIX}_bundle_unreadable")
        manifest = parse_asset_manifest(files)
    except ValueError as exc:
        return _finish(context_variables, {"published": False, "error": str(exc)})

    requested = {entry["asset_id"] for entry in planned_generated_entries(manifest)}
    assets = _latest_asset_by_task(
        await get_media_asset_store().list_generated_assets_for_chat(app_id=app_id, chat_id=chat_id, limit=200)
    )
    generated: list[str] = []
    placeholders: list[str] = []
    missing: list[str] = []
    overlay_assets: list[dict[str, Any]] = []
    for entry in (manifest or {}).get("assets", []):
        if not is_planned(entry):
            continue
        asset_id = entry["asset_id"]
        asset = assets.get(asset_id) if asset_id in requested else None
        if asset is not None:
            data = await get_media_asset_store().get_asset_content(asset)
            suffix = _EXTENSION_BY_TYPE.get(asset.media_type) or mimetypes.guess_extension(asset.media_type) or ".png"
            path = f"{ASSET_DIR}/{asset_id}{suffix}"
            files[path] = data
            entry["path"] = path
            entry["tags"] = sorted({*(entry.get("tags") or []), "ai_generated"})
            generated.append(asset_id)
            overlay_assets.append({
                "asset_id": asset_id, "path": path, "kind": "generated", "media_asset_id": asset.asset_id,
                "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data), "model": asset.model,
            })
        elif entry.get("source") == "uploaded":
            path = f"{ASSET_DIR}/placeholders/{asset_id}.svg"
            files[path] = _placeholder_svg(entry)
            entry["path"] = path
            entry["tags"] = sorted({*(entry.get("tags") or []), "placeholder"})
            placeholders.append(asset_id)
            overlay_assets.append({"asset_id": asset_id, "path": path, "kind": "placeholder"})
        else:
            missing.append(asset_id)

    if not overlay_assets:
        return _finish(context_variables, {
            "published": False, "generated": [], "placeholders": [], "missing": missing,
            "artifact_version_id": parent.id, "reason": "no_media_changes",
        })

    manifest_text = json.dumps(manifest, indent=2) + "\n"
    files[ASSET_MANIFEST_PATH] = manifest_text
    overlay = {
        "parent_artifact_version_id": parent.id,
        "source_chat_id": chat_id,
        "assets": overlay_assets,
        "missing": missing,
        "asset_manifest_sha256": hashlib.sha256(manifest_text.encode("utf-8")).hexdigest(),
    }
    try:
        child, bundle_path = await _register_media_version(
            parent=parent, files=files, overlay=overlay, chat_id=chat_id, user_id=user_id,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        logger.warning("App media version registration failed: %s", exc)
        return _finish(context_variables, {"published": False, "error": f"{ERROR_PREFIX}_publish_failed"})

    run = record.get("current_build_run") or {}
    workflow_sequence = str(
        detach(context_variables.get("workflow_sequence")) or run.get("workflow_sequence") or "build"
    )
    update = await AppRegistryService().update_build_status(
        owner_user_id=user_id,
        build_registry_id=binding.build_registry_id,
        status="review",
        bundle_path=bundle_path,
        artifact_version_id=child.id,
        workflow_sequence=workflow_sequence,
        active_chat_id=chat_id,
        active_workflow_id=WORKFLOW_NAME,
        current_build_run={
            "build_id": binding.build_id,
            "workflow_sequence": workflow_sequence,
            "status": "review",
            "active_chat_id": chat_id,
            "active_workflow_id": WORKFLOW_NAME,
            "artifact_version_id": child.id,
            "bundle_path": bundle_path,
        },
        expected_build_id=binding.build_id,
    )
    if not update.get("success"):
        return _finish(context_variables, {"published": False, "error": f"{ERROR_PREFIX}_build_superseded"})

    context_variables.set("artifact_version_id", child.id)
    context_variables.set("bundle_path", bundle_path)
    context_variables.set("lifecycle_state", "review")
    return _finish(context_variables, {
        "published": True,
        "generated": generated,
        "placeholders": placeholders,
        "missing": missing,
        "artifact_version_id": child.id,
        "parent_artifact_version_id": parent.id,
    })
