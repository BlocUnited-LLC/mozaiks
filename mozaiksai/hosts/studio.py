from __future__ import annotations

"""Studio management host layered on top of mozaiksai.hosts.platform.

Studio is the local/private management and create control plane used by the
CLI and by the hosted Mozaiks product. It adds Studio shell routes and
workflow triggering on top of the headless platform host.
"""

import io
import json
import os
import stat
import zipfile
import zlib
from asyncio import CancelledError, Lock, to_thread
from collections import Counter
from collections.abc import Iterable
from contextlib import AbstractAsyncContextManager, asynccontextmanager, nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from difflib import unified_diff
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal, NamedTuple, TypeVar
from uuid import NAMESPACE_URL, uuid4, uuid5

import yaml
from anyio import CancelScope
from fastapi import BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, TypeAdapter, ValidationError

from factory_app.app.modules.app_registry.backend.schemas import GenesisImportClaim
from factory_app.app.modules.app_registry.backend.service import AppRegistryService
from factory_app.workflows._shared.artifact_bundle import (
    APP_BUNDLE_MAX_FILE_BYTES,
    APP_BUNDLE_MAX_TOTAL_BYTES,
    add_preview_workflow_files,
    read_artifact_bundle,
)
from factory_app.workflows._shared.platform.genesis_import import (
    GenesisImportError,
    accept_existing_app_genesis,
    genesis_acceptance_disclosure,
    read_imported_genesis_review_bundle,
    require_accepted_genesis_baseline,
)
from factory_app.workflows._shared.platform.review_continuation import (
    continue_inline_app_review,
    recover_saved_app_review,
)
from logs.logging_config import get_workflow_logger
from mozaiksai.control_plane import (
    AcceptedStagedAppBundleBuildRecordError,
    RefinementRequest,
    SourceImportRequest,
    accept_staged_refinement_build_record,
    build_refinement_review_package,
    get_orchestration_control_harness,
    index_workspace_app_intelligence,
    resolve_source_import,
    run_current_app_source_validation,
    source_import_scan_policy,
)
from mozaiksai.control_plane.app_context import (
    get_current_app_context_graph,
    get_current_app_context_summary,
)
from mozaiksai.control_plane.app_context_override import (
    AppContextPolicyOverrideDecision,
    apply_app_context_policy_override,
    create_app_context_policy_override,
)
from mozaiksai.control_plane.app_context_policy import (
    AppContextPolicyDecision,
    AppContextPolicyResult,
)
from mozaiksai.control_plane.app_context_refresh import (
    build_context_refresh_plan,
    build_context_refresh_request,
)
from mozaiksai.control_plane.app_context_refresh_execution import (
    ContextRefreshLaunchResult,
    complete_context_refresh,
    launch_context_refresh_plan,
)
from mozaiksai.control_plane.app_intelligence_jobs import (
    AppIntelligenceIndexJob,
    advance_app_intelligence_index_job,
    complete_app_intelligence_index_job,
    create_app_intelligence_index_job,
    fail_app_intelligence_index_job,
    get_app_intelligence_index_job,
    get_latest_app_intelligence_index_job,
    public_app_intelligence_index_job,
    save_app_intelligence_index_job,
)
from mozaiksai.control_plane.contracts import (
    RegistryRunState,
    build_acceptance_evidence,
    safe_artifact_relpath,
)
from mozaiksai.control_plane.dry_run import RefinementDryRunPlan, RefinementExecutionPlan
from mozaiksai.control_plane.implementations.coding_worker import (
    PACK_OWNERSHIP_UNRESOLVED_GUIDANCE,
    TEMPLATE_PACK_OUTPUT_GUIDANCE,
    PackCodeBaseline,
    newer_accepted_work,
    pack_code_baseline_refusal,
    pack_paths_owned_by,
    parent_pack_output_owners,
    parent_workspace_pack_paths,
    resolve_coding_validation_strategy,
)
from mozaiksai.control_plane.review import load_refinement_review_record
from mozaiksai.core.app_context.models import SourceRef
from mozaiksai.core.app_context.refresh import ContextRefreshPlan, ContextRefreshScope
from mozaiksai.core.artifacts import (
    ArtifactLifecycleStatus,
    ArtifactValidationStatus,
    BuildRecord,
    ChangeClassification,
    RefinementSessionStatus,
    get_artifact_store,
)
from mozaiksai.core.artifacts.content_store import (
    ContentNotFoundError,
    read_verified_artifact_bundle,
)
from mozaiksai.core.artifacts.models import (
    CanonicalBundleEntryError,
    ChangeRequestDoc,
    resolve_canonical_bundle_entry,
)
from mozaiksai.core.auth import UserPrincipal, require_user_scope
from mozaiksai.core.auth.anonymous_access import ANONYMOUS_PROVENANCE, STUDIO_PUBLIC_MESSAGE
from mozaiksai.core.auth.dependencies import validate_path_id
from mozaiksai.core.dashboard import load_dashboard_manifest
from mozaiksai.core.data.persistence import ConnectorStore
from mozaiksai.core.metrics import (
    OwnerAnalyticsService,
    PeriodError,
    PeriodWindow,
    resolve_period,
)
from mozaiksai.core.metrics.funnels import FunnelDef
from mozaiksai.core.runtime.app.metrics_loader import (
    MetricsConfigLoadError,
    load_metrics_config,
)
from mozaiksai.core.runtime.app.module_permissions import resolve_module_permissions
from mozaiksai.core.runtime.app.paths import APP_BINARY_ASSET_SUFFIXES, app_bundle_workspace_path
from mozaiksai.core.runtime.app.studio_summary import (
    build_app_overview_summary,
    build_apps_summary,
    build_build_section,
    build_integrations_summary,
    get_missing_studio_surfaces,
    load_build_state_from_db,
    save_build_state_to_db,
)
from mozaiksai.core.runtime.app.subscriptions_loader import SubscriptionsConfig
from mozaiksai.core.runtime.app.theme_contract import APP_THEME_PATH
from mozaiksai.core.runtime.composition.extensions import resolve_startup_service_profile
from mozaiksai.core.sandbox.preview_sessions import preview_sessions_lifespan
from mozaiksai.core.secrets.contract import is_secret_contract_path, validate_secret_contract_text
from mozaiksai.core.session.build_binding import (
    BuildIdentity,
    BuildTargetReference,
    RetryPlanSource,
    RunBuildBinding,
)
from mozaiksai.core.session.launcher import (
    WorkflowLaunchResult,
    launch_prepared_workflow,
    prepare_routed_workflow_launch,
)
from mozaiksai.core.session.model import (
    PendingDecisionAction,
    PendingHarnessDecision,
    RoutingDecision,
    SessionLifecycle,
    TriggerInput,
)
from mozaiksai.core.session.router import configure_session_router, get_session_router
from mozaiksai.core.session.trigger_routing import TriggerRoutingContribution
from mozaiksai.core.startup.validation import require_management_auth_posture
from mozaiksai.core.studio.scope import resolve_studio_scope
from mozaiksai.core.workflow.generator_support.connector_health import run_connector_health_check
from mozaiksai.core.workflow.generator_support.connector_service import (
    compute_connector_health,
    delete_connector,
    get_connector,
    get_connector_backend_summary,
    list_connectors,
    patch_connector,
    save_connector,
)
from mozaiksai.hosts import platform as platform_app
from mozaiksai.hosts.bootstrap import register_repo_host_bootstrap
from mozaiksai.hosts.platform import (
    build_shell_config,
    resolve_app_root,
)
from mozaiksai.hosts.routers.sandbox import create_sandbox_router
from mozaiksai.hosts.runtime import register_app_lifespan
from mozaiksai.hosts.source_path_policy import (
    authorize_http_workflow_source_paths,
    require_http_local_source_mode,
)
from mozaiksai.hosts.studio_model_policy import (
    StudioModelPolicy,
    save_studio_model_policy,
    select_studio_model,
    studio_model_options,
)

app = platform_app.app
register_repo_host_bootstrap(app, "studio")


def _register_management_auth_posture_check(target_app) -> None:
    """Refuse an auth posture Studio cannot serve before any other startup work.

    It wraps the whole composed lifespan, so Studio's own message (no
    ``AUTH_ANON_ACCESS=public`` among the choices) comes before the runtime's
    generic startup checks. Request-level refusal: :func:`require_studio_user`.
    """
    existing_lifespan = target_app.router.lifespan_context

    @asynccontextmanager
    async def _management_auth_posture_lifespan(app_instance):
        require_management_auth_posture()
        async with existing_lifespan(app_instance):
            yield

    target_app.router.lifespan_context = _management_auth_posture_lifespan


_register_management_auth_posture_check(app)


@asynccontextmanager
async def _studio_preview_sessions_lifespan(app_instance):
    if resolve_startup_service_profile() == "worker":
        yield
        return
    async with preview_sessions_lifespan(app_instance):
        yield


register_app_lifespan(app, _studio_preview_sessions_lifespan)
logger = get_workflow_logger("studio_app")

_BUNDLE_MAX_TEXT_FILES = 200
_BUNDLE_MAX_TOTAL_BYTES = 2_000_000
_BUNDLE_MAX_SINGLE_FILE_BYTES = 250_000
_DIFF_PREVIEW_MAX_LINES = 120
_DIFF_PREVIEW_MAX_CHARS = 12_000
_RESTORE_SECRET_PATH_TERMS = (
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
_RESTORE_SKIP_FILENAMES = {
    "refinement_plan.json",
    "affected_paths.json",
    "refinement_review.json",
    "execution_result.json",
}


def _get_app_registry_service() -> AppRegistryService:
    return AppRegistryService()


def _normalize_bundle_entry_name(name: str) -> str | None:
    normalized = str(name or "").replace("\\", "/").strip("/")
    if not normalized or normalized.endswith("/"):
        return None
    parts = [part for part in normalized.split("/") if part]
    if any(part == ".." for part in parts):
        return None
    return "/".join(parts)


def _restore_entry_name(name: str) -> tuple[str | None, str | None]:
    raw = str(name or "").strip()
    if not raw:
        return None, "Empty path."
    if "\x00" in raw:
        return None, "Path contains a null byte."
    if PureWindowsPath(raw).is_absolute() or PureWindowsPath(raw).drive:
        return None, "Absolute or drive-qualified paths are not allowed."
    if raw.startswith(("/", "\\", "//", "\\\\")) or PurePosixPath(raw).is_absolute():
        return None, "Absolute paths are not allowed."

    normalized = raw.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    parts = [part for part in PurePosixPath(normalized).parts if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        return None, "Path traversal is not allowed."

    relative_path = "/".join(parts)
    lowered = relative_path.lower()
    if not is_secret_contract_path(relative_path) and any(term in lowered for term in _RESTORE_SECRET_PATH_TERMS):
        return relative_path, "Secret-sensitive paths are not restored."
    basename = PurePosixPath(relative_path).name.lower()
    if (
        basename in _RESTORE_SKIP_FILENAMES
        or lowered.startswith("backups/")
        or "/backups/" in lowered
        or any(segment == "backups" for segment in PurePosixPath(relative_path).parts)
    ):
        return relative_path, "Staging metadata and backups are not restored."
    return relative_path, None


def _zipinfo_is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0o170000
    return mode == stat.S_IFLNK




_EntryT = TypeVar("_EntryT")


def _strip_shared_root_prefix(entries: list[tuple[str, _EntryT]]) -> list[tuple[str, _EntryT]]:
    if not entries:
        return entries
    split_paths = [path.split("/") for path, _ in entries]
    if not split_paths or any(len(parts) < 2 for parts in split_paths):
        return entries
    first_segment = split_paths[0][0]
    if not all(parts[0] == first_segment for parts in split_paths):
        return entries
    return [("/".join(parts[1:]), content) for parts, (_, content) in zip(split_paths, entries, strict=False)]


def _strip_app_bundle_root_prefix(paths: list[str]) -> dict[str, str]:
    if not paths:
        return {}
    split_paths = [path.split("/") for path in paths]
    if not split_paths or any(len(parts) < 2 for parts in split_paths):
        return {path: path for path in paths}
    first_segment = split_paths[0][0]
    if not first_segment or not all(parts[0] == first_segment for parts in split_paths):
        return {path: path for path in paths}

    stripped = ["/".join(parts[1:]) for parts in split_paths]
    if "app.json" not in stripped:
        return {path: path for path in paths}
    return {path: stripped_path for path, stripped_path in zip(paths, stripped, strict=False)}


def _decode_text_bundle_entries(
    zip_source: Path | bytes, *, preserved_assets: dict[str, bytes] | None = None,
) -> tuple[dict[str, str], list[str]]:
    files: dict[str, str] = {}
    skipped: list[str] = []
    total_text = 0
    total_assets = 0
    entries: list[tuple[str, str | bytes]] = []
    seen: set[str] = set()

    with zipfile.ZipFile(io.BytesIO(zip_source) if isinstance(zip_source, bytes) else zip_source, "r") as archive:
        for info in archive.infolist():
            safe_name = safe_artifact_relpath(info.filename)
            if safe_name is None or safe_name != info.filename.rstrip("/"):
                skipped.append(f"{info.filename}: unsafe_path")
                continue
            if _zipinfo_is_symlink(info):
                skipped.append(f"{safe_name}: symlink")
                continue
            if info.is_dir():
                continue
            if safe_name.casefold() in seen:
                skipped.append(f"{safe_name}: duplicate_path")
                continue
            seen.add(safe_name.casefold())
            if len(entries) >= _BUNDLE_MAX_TEXT_FILES:
                skipped.append(f"{safe_name}: file_limit")
                continue
            suffix = PurePosixPath(safe_name).suffix.lower()
            opaque_asset = preserved_assets is not None and suffix in APP_BINARY_ASSET_SUFFIXES and suffix != ".svg"
            if info.file_size > (APP_BUNDLE_MAX_FILE_BYTES if opaque_asset else _BUNDLE_MAX_SINGLE_FILE_BYTES):
                skipped.append(f"{safe_name}: file_too_large")
                continue
            if (total_text + total_assets + info.file_size > APP_BUNDLE_MAX_TOTAL_BYTES
                    or (not opaque_asset and total_text + info.file_size > _BUNDLE_MAX_TOTAL_BYTES)):
                skipped.append(f"{safe_name}: total_size_limit")
                continue
            raw = archive.read(info)
            if opaque_asset:
                entries.append((safe_name, raw))
                total_assets += len(raw)
                continue
            if b"\x00" in raw:
                skipped.append(f"{safe_name}: binary")
                continue
            try:
                content = raw.decode("utf-8")
            except UnicodeDecodeError:
                skipped.append(f"{safe_name}: non_utf8")
                continue
            total_text += len(raw)
            entries.append((safe_name, content))

    for relative_path, content in _strip_shared_root_prefix(entries):
        if isinstance(content, str):
            files[relative_path] = content
        elif preserved_assets is not None:
            preserved_assets[relative_path] = content
    return files, skipped


async def _inline_refinement_baseline(version) -> tuple[dict[str, str], dict[str, bytes]]:  # noqa: ANN001
    assets: dict[str, bytes] = {}
    try:
        files, skipped = _decode_text_bundle_entries(await _verified_app_bundle(version), preserved_assets=assets)
    except (zipfile.BadZipFile, zlib.error, EOFError, OSError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail="Inline refinement cannot read the verified bundle") from exc
    if skipped:
        raise HTTPException(status_code=409, detail="Inline refinement cannot preserve every file in this bundle")
    return files, assets


def _artifact_bundle_path_from_version(version) -> Path | None:  # noqa: ANN001
    artifact_path = (version.commit_metadata.metadata or {}).get("artifact_path")
    if not artifact_path:
        return None
    path = Path(str(artifact_path))
    return path if path.exists() else None


async def _verified_app_bundle(version, *, max_bytes: int | None = None) -> bytes:  # noqa: ANN001
    try:
        if max_bytes is not None:
            return await read_verified_artifact_bundle(version, max_bytes=max_bytes)
        return await read_verified_artifact_bundle(version)
    except (ValueError, ContentNotFoundError, OSError) as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "Artifact archive identity or content could not be verified. "
                "Save a canonical app bundle before using this version."
            ),
        ) from exc


async def _bundle_source_for_review(version) -> bytes | Path | None:  # noqa: ANN001
    if version.build_family == "app_bundle":
        return await _verified_app_bundle(version)
    return _artifact_bundle_path_from_version(version)


def _version_metadata(version) -> dict[str, Any]:
    commit_metadata = getattr(version, "commit_metadata", None)
    metadata = getattr(commit_metadata, "metadata", None)
    if isinstance(metadata, dict):
        return dict(metadata)
    if isinstance(commit_metadata, dict):
        payload = commit_metadata.get("metadata")
        if isinstance(payload, dict):
            return dict(payload)
    return {}


def _artifact_build_binding(version: BuildRecord, *, build_registry_id: str, target_app_id: str) -> RunBuildBinding:
    metadata = _version_metadata(version)
    try:
        binding = RunBuildBinding.model_validate({key: metadata.get(key) for key in RunBuildBinding.model_fields})
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="Artifact has no verified build identity") from exc
    if binding.build_registry_id != build_registry_id or binding.target_app_id != target_app_id:
        raise HTTPException(status_code=409, detail="Artifact identity does not match its build target")
    return binding


def _refinement_metadata_from_version(version) -> dict[str, Any] | None:  # noqa: ANN001
    metadata = _version_metadata(version)
    refinement = metadata.get("refinement")
    if isinstance(refinement, dict):
        return refinement
    return None


def _load_refinement_review_record_for_version(version):  # noqa: ANN001
    refinement = _refinement_metadata_from_version(version)
    if refinement is None:
        return None
    staging_area = str(refinement.get("staging_area") or "").strip()
    if not staging_area:
        return None
    try:
        return load_refinement_review_record(staging_area)
    except FileNotFoundError:
        return None


def _refinement_acceptance_ready(version) -> bool:  # noqa: ANN001
    refinement = _refinement_metadata_from_version(version)
    if refinement is None:
        return True
    review_record = _load_refinement_review_record_for_version(version)
    review_snapshot = refinement.get("review")
    if not isinstance(review_snapshot, dict):
        return False
    return bool(
        review_record is not None
        and review_record.status == "promotion_ready"
        and review_record.promotion_allowed is True
        and str(review_snapshot.get("status") or "").strip() == "promotion_ready"
        and review_snapshot.get("promotion_allowed") is True
    )


def _validation_override_required(version) -> bool:  # noqa: ANN001
    return version.validation_status in {ArtifactValidationStatus.PENDING, ArtifactValidationStatus.SKIPPED}


def _enforce_artifact_validation_gate(
    version,  # noqa: ANN001
    *,
    action: str,
) -> None:
    if version.validation_status == ArtifactValidationStatus.FAILED:
        raise HTTPException(
            status_code=409,
            detail=f"Artifact cannot be {action}; validation_status='failed'.",
        )
    if version.validation_status == ArtifactValidationStatus.PASSED:
        if action in {"promoted", "restored", "exported"} and version.app_validation_status != "passed":
            raise HTTPException(
                status_code=409,
                detail="This candidate needs passed whole-app build validation before export or activation.",
            )
        return
    raise HTTPException(
        status_code=409,
        detail=(
            f"Artifact cannot be {action}; validation_status='passed' is required."
        ),
    )


def _resolve_bundle_restore_target(version) -> Path:  # noqa: ANN001
    if version.build_family != "app_bundle":
        raise HTTPException(status_code=400, detail=f"Unsupported artifact kind for restore: {version.build_family}")
    target_id = TypeAdapter(BuildIdentity).validate_python(version.app_id)
    version_id = TypeAdapter(BuildIdentity).validate_python(version.id)
    workspace_root = Path(os.getenv("MOZAIKS_WORKSPACES_PATH", ".local/workspaces")).resolve()
    target = (workspace_root / target_id / version_id).resolve()
    active_root = resolve_app_root().resolve()
    if (
        not target.is_relative_to(workspace_root)
        or target.is_relative_to(active_root)
        or active_root.is_relative_to(target)
    ):
        raise HTTPException(status_code=409, detail="Build promotion cannot modify the active host workspace")
    return target


def _restore_bundle_to_target(*, bundle_bytes: bytes, target_dir: Path, workspace_layout: bool = False) -> dict[str, list[str]]:
    restored: list[str] = []
    skipped: list[str] = []
    target_dir.mkdir(parents=True, exist_ok=True)
    target_root = target_dir.resolve()

    with zipfile.ZipFile(io.BytesIO(bundle_bytes), "r") as archive:
        planned: list[tuple[zipfile.ZipInfo, str]] = []
        for info in archive.infolist():
            if info.is_dir():
                continue
            if _zipinfo_is_symlink(info):
                skipped.append(info.filename)
                continue
            safe_name, reason = _restore_entry_name(info.filename)
            if safe_name is None:
                raise HTTPException(status_code=400, detail=f"Unsafe artifact archive entry: {info.filename} ({reason})")
            if reason is not None:
                skipped.append(safe_name)
                continue
            if is_secret_contract_path(safe_name):
                try:
                    validate_secret_contract_text(archive.read(info.filename))
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail="Artifact contains an invalid names-only secret contract") from exc
            if contains_symlink_component(target_root, safe_name):
                raise HTTPException(
                    status_code=400,
                    detail=f"Restore target contains a symlinked component for archive entry: {safe_name}",
                )
            planned.append((info, safe_name))

        if not planned:
            raise HTTPException(status_code=400, detail="Artifact bundle did not contain any restorable files.")

        restore_names = _strip_app_bundle_root_prefix([safe_name for _, safe_name in planned])
        for info, safe_name in planned:
            safe_name = restore_names.get(safe_name, safe_name)
            if workspace_layout:
                safe_name = app_bundle_workspace_path(safe_name)
            if contains_symlink_component(target_root, safe_name):
                raise HTTPException(status_code=400, detail="Restore target contains a symlinked component")
            destination = (target_root / safe_name).resolve()
            if not destination.is_relative_to(target_root):
                raise HTTPException(
                    status_code=400,
                    detail=f"Archive entry escapes restore target: {safe_name}",
                )
            if destination.exists() and destination.is_symlink():
                raise HTTPException(
                    status_code=400,
                    detail=f"Refusing to write through a symlinked target path: {safe_name}",
                )
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(archive.read(info.filename))
            restored.append(safe_name)

    return {"restored": sorted(restored), "skipped": sorted(skipped)}


def _build_diff_preview(*, path: str, before: str | None, after: str | None) -> str:
    before_lines = [] if before is None else before.splitlines(keepends=True)
    after_lines = [] if after is None else after.splitlines(keepends=True)
    diff_lines = list(
        unified_diff(
            before_lines,
            after_lines,
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="",
        )
    )
    if len(diff_lines) > _DIFF_PREVIEW_MAX_LINES:
        diff_lines = diff_lines[:_DIFF_PREVIEW_MAX_LINES] + ["...diff truncated..."]
    preview = "\n".join(diff_lines)
    if len(preview) > _DIFF_PREVIEW_MAX_CHARS:
        preview = preview[:_DIFF_PREVIEW_MAX_CHARS] + "\n...diff truncated..."
    return preview


def _build_bundle_diff_summary(
    *,
    current_files: dict[str, str],
    parent_files: dict[str, str],
) -> list[dict[str, Any]]:
    changed: list[dict[str, Any]] = []
    for path in sorted(set(current_files) | set(parent_files)):
        before = parent_files.get(path)
        after = current_files.get(path)
        if before == after:
            continue
        if before is None:
            change_type = "added"
        elif after is None:
            change_type = "removed"
        else:
            change_type = "modified"
        changed.append(
            {
                "path": path,
                "change_type": change_type,
                "diff_preview": _build_diff_preview(path=path, before=before, after=after),
                "before_size": len(before or ""),
                "after_size": len(after or ""),
            }
        )
    return changed


async def _verified_review_snapshots(*, app_id: str, version, artifact_store) -> dict[str, bytes]:  # noqa: ANN001
    """Verify app archives before changing review state; reuse these bytes in the response."""
    snapshots = {}
    if version.build_family == "app_bundle":
        snapshots[version.id] = await _verified_app_bundle(version)
    if version.parent_build_record_id:
        parent = await artifact_store.get_build_record(
            app_id=app_id, build_record_id=version.parent_build_record_id,
        )
        if parent is not None and parent.build_family == "app_bundle":
            snapshots[parent.id] = await _verified_app_bundle(parent)
    return snapshots


async def _build_artifact_review_payload(
    *,
    app_id: str,
    version,
    artifact_store,
    verified_bundle_snapshots: dict[str, bytes] | None = None,
) -> dict[str, Any]:  # noqa: ANN001
    # Review mutations reuse the selected and parent records' verified snapshots.
    snapshots = verified_bundle_snapshots or {}
    current_zip: bytes | Path | None = snapshots.get(version.id)
    if current_zip is None:
        current_zip = await _bundle_source_for_review(version)
    current_files: dict[str, str] = {}
    current_skipped: list[str] = []
    if current_zip is not None:
        current_files, current_skipped = _decode_text_bundle_entries(current_zip)

    parent_version = None
    parent_files: dict[str, str] = {}
    parent_skipped: list[str] = []
    if version.parent_build_record_id:
        parent_version = await artifact_store.get_build_record(
            app_id=app_id,
            build_record_id=version.parent_build_record_id,
        )
        parent_zip: bytes | Path | None = None
        if parent_version is not None:
            parent_zip = snapshots.get(parent_version.id)
            if parent_zip is None:
                parent_zip = await _bundle_source_for_review(parent_version)
        if parent_zip is not None:
            parent_files, parent_skipped = _decode_text_bundle_entries(parent_zip)

    sessions = await artifact_store.list_refinement_sessions(
        app_id=app_id,
        result_build_record_id=version.id,
        limit=5,
    )
    latest_session = sessions[0] if sessions else None
    change_request = None
    if latest_session is not None:
        change_request = await artifact_store.get_change_request(
            app_id=app_id,
            change_request_id=latest_session.change_request_id,
        )
    if change_request is None and version.parent_build_record_id:
        requests = await artifact_store.list_change_requests(
            app_id=app_id,
            build_record_id=version.parent_build_record_id,
            limit=1,
        )
        change_request = requests[0] if requests else None

    refinement_metadata = _refinement_metadata_from_version(version)
    changed_files = _build_bundle_diff_summary(current_files=current_files, parent_files=parent_files)
    selected_paths = []
    validation_result = _version_metadata(version).get("validation_result")
    coding_summary = None
    if latest_session is not None and isinstance(latest_session.metadata, dict):
        worker_meta = latest_session.metadata.get("coding_worker") or {}
        if isinstance(worker_meta, dict):
            selected_paths = list(worker_meta.get("metadata", {}).get("selected_file_paths") or [])
            if validation_result is None:
                validation_result = worker_meta.get("validation_result")
            coding_summary = (worker_meta.get("plan") or {}).get("summary")
    if not selected_paths:
        selected_paths = list((version.commit_metadata.metadata or {}).get("applied_paths") or [])

    review_status = version.lifecycle_status.value
    if (
        _is_revalidatable_refinement_draft(version)
        and version.validation_status == ArtifactValidationStatus.PASSED
    ):
        review_status = "validated"
    elif latest_session is not None:
        review_status = latest_session.status.value
    elif version.lifecycle_status == ArtifactLifecycleStatus.DRAFT:
        review_status = "validated" if version.validation_status == ArtifactValidationStatus.PASSED else "needs_revision"
    elif version.lifecycle_status == ArtifactLifecycleStatus.ARCHIVED:
        review_status = "rejected"

    can_accept = (
        version.lifecycle_status == ArtifactLifecycleStatus.DRAFT
        and version.validation_status == ArtifactValidationStatus.PASSED
        and _refinement_acceptance_ready(version)
        and _version_metadata(version).get("bundle_mode") != "brownfield_genesis_import"
    )
    can_reject = (
        version.lifecycle_status == ArtifactLifecycleStatus.DRAFT
        and _version_metadata(version).get("bundle_mode") != "brownfield_genesis_import"
    )
    can_promote = (
        version.lifecycle_status == ArtifactLifecycleStatus.CURRENT
        and version.validation_status == ArtifactValidationStatus.PASSED
        and version.app_validation_status == "passed"
        and current_zip is not None
        and _version_metadata(version).get("bundle_mode") != "brownfield_genesis_import"
    )
    validation_override_required = _validation_override_required(version)
    validation_blocker = None
    if version.validation_status == ArtifactValidationStatus.FAILED:
        validation_blocker = "Validation failed. Reject or revise this artifact before accepting it."
    elif validation_override_required:
        validation_blocker = "Required checks have not passed. This draft cannot be activated yet."
    elif version.lifecycle_status == ArtifactLifecycleStatus.CURRENT and version.app_validation_status != "passed":
        validation_blocker = "Whole-app build validation must pass for this candidate before activation."

    review_package = build_refinement_review_package(
        app_id=app_id,
        artifact_version_id=version.id,
        artifact_kind=version.build_family,
        artifact_key=version.build_key,
        parent_version_id=version.parent_build_record_id,
        lifecycle_status=version.lifecycle_status.value,
        validation_status=version.validation_status.value,
        review_status=review_status,
        changed_files=changed_files,
        selected_paths=selected_paths,
        current_skipped_files=current_skipped,
        parent_skipped_files=parent_skipped,
        refinement_metadata=refinement_metadata,
        change_request=change_request,
        latest_session=latest_session,
        coding_summary=coding_summary,
        validation_result=validation_result,
        validation_override_required=validation_override_required,
        validation_blocker=validation_blocker,
        can_accept=can_accept,
        can_reject=can_reject,
        can_promote=can_promote,
    )

    return {
        "artifact_version": version.model_dump(by_alias=False, mode="python"),
        "parent_artifact_version": parent_version.model_dump(by_alias=False, mode="python") if parent_version else None,
        "change_request": change_request.model_dump(by_alias=False, mode="python") if change_request else None,
        "refinement_session": latest_session.model_dump(by_alias=False, mode="python") if latest_session else None,
        "review": {
            **review_package.model_dump(mode="python"),
            "can_revalidate": _is_revalidatable_refinement_draft(version)
            and version.validation_status != ArtifactValidationStatus.PASSED,
        },
    }

configure_session_router(
    trigger_route_resolver=get_orchestration_control_harness(),
)

from factory_app.workflows._shared.platform.ask_context import studio_ask_context
from factory_app.workflows._shared.platform.build_target import bind_factory_session
from mozaiksai.core.runtime.composition.platform_hooks import get_platform_hooks
from mozaiksai.core.utils.path_containment import contains_symlink_component
from mozaiksai.core.utils.sequences import dedupe_strings


def register_studio_platform_hooks(registry: Any | None = None) -> None:
    """Install Studio's platform extension hooks.

    Called once at import time. Exposed as a named function so the wiring is
    assertable without depending on import side effects surviving a registry
    reset.
    """
    (registry or get_platform_hooks()).register_bundle(
        {"chat_session_fields": bind_factory_session, "ask_context": studio_ask_context},
        source="mozaiks.studio",
        prepend=True,
    )


register_studio_platform_hooks()


async def require_studio_user(
    principal: UserPrincipal = Depends(require_user_scope),
) -> UserPrincipal:
    """The caller of a Studio management route, never an anonymous visitor.

    Studio manages workspaces, builds and connectors for signed-in callers.
    Its startup refuses AUTH_ANON_ACCESS=public; this refuses a visitor
    principal on every request too, so a Studio composed without its
    lifespan cannot serve visitors either.
    """
    _refuse_anonymous_visitor(principal)
    return principal


def _refuse_anonymous_visitor(principal: UserPrincipal) -> None:
    if principal.auth_provenance == ANONYMOUS_PROVENANCE:
        raise HTTPException(status_code=403, detail=STUDIO_PUBLIC_MESSAGE)


def _resolve_studio_preview_scope(principal: UserPrincipal) -> tuple[str, str]:
    """Studio scope for the preview sandbox routes and websocket.

    Studio composes them from the shared router factory, whose routes take
    their principal from require_user_scope (or the websocket), so the
    visitor refusal of require_studio_user happens here.
    """
    _refuse_anonymous_visitor(principal)
    return _resolve_studio_scope(principal)


def _resolve_studio_scope(
    principal: UserPrincipal,
    *,
    app_id: str | None = None,
    user_id: str | None = None,
) -> tuple[str, str]:
    """Resolve app/user scope for Studio endpoints in both auth modes."""
    scope = resolve_studio_scope(
        principal,
        app_id=app_id,
        user_id=user_id,
        default_user_id=platform_app._DEFAULT_PROFILE_USER_ID,
        default_app_id=platform_app._resolve_default_app_id(),
    )
    return scope.app_id, scope.user_id


async def _resolve_owned_app_scope(
    principal: UserPrincipal, *, app_id: str | None = None,
) -> tuple[str, str]:
    """Resolve an app-scoped Studio resource through its registry owner."""
    resolved_app_id, user_id = _resolve_studio_scope(principal, app_id=app_id)
    await _get_owned_app_record(resolved_app_id, user_id)
    return resolved_app_id, user_id


@app.get("/api/shell-config")
async def get_studio_shell_config(request: Request):
    return await build_shell_config(surface="studio", client_scope=request.scope)


@app.get("/api/studio/dashboard")
async def get_studio_dashboard_config(
    scope: Literal["workspace", "app"] | None = None,
    app_id: str | None = None,
    principal: UserPrincipal = Depends(require_studio_user),
):
    # The loaded Studio host's shared navigation manifest contains no app data.
    # Explicit targets and customer-app token scopes retain registry checks.
    host_app_id = getattr(app.state, "loaded_app_id", None)
    if app_id or (principal.app_id and principal.app_id != host_app_id):
        resolved_app_id, _ = await _resolve_owned_app_scope(principal, app_id=app_id)
    else:
        resolved_app_id, _ = _resolve_studio_scope(principal)
    manifest = load_dashboard_manifest(resolve_app_root())
    payload = manifest.model_dump(mode="json")
    payload["resolved"] = {
        "scope": scope,
        "app_id": resolved_app_id if scope == "app" or app_id else None,
    }
    if scope:
        payload["surface"] = manifest.surface_for_scope(scope).model_dump(mode="json")
    return payload


@app.get("/api/studio/build/dashboard-link")
async def get_build_dashboard_link(
    request: Request,
    chat_id: BuildIdentity | None = None,
    principal: UserPrincipal = Depends(require_studio_user),
):
    """Resolve the owned build's dashboard from its server-persisted binding."""
    fallback = {"path": "/apps"}
    if chat_id is None:
        return fallback
    host_app_id, user_id = _resolve_studio_scope(principal)
    registry = _get_app_registry_service()
    saved = await registry.repo.get_owned_chat_binding(
        app_id=host_app_id, owner_user_id=user_id, chat_id=chat_id,
    )
    if saved is None:
        raise HTTPException(status_code=404, detail="Build context not found")
    if not saved:
        return fallback
    try:
        binding = RunBuildBinding.model_validate(saved)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Build context not found") from exc
    record = (await registry.get_app_record(
        build_registry_id=binding.build_registry_id, owner_user_id=user_id,
    )).get("app")
    if (not record or record.get("app_id") != binding.target_app_id
            or record.get("chat_app_id") != host_app_id):
        raise HTTPException(status_code=404, detail="Build context not found")
    surface = load_dashboard_manifest(resolve_app_root()).surface_for_scope("app")
    portal = next(portal for portal in surface.portals if portal.id == surface.default_portal and portal.enabled)
    dashboard_app_id = binding.target_app_id
    resolve_app_id = getattr(request.app.state, "resolve_studio_dashboard_app_id", None)
    if resolve_app_id is not None:
        mapped_id = await resolve_app_id(host_app_id=host_app_id, owner_user_id=user_id, binding=binding)
        try:
            dashboard_app_id = TypeAdapter(BuildIdentity).validate_python(mapped_id)
        except ValidationError as exc:
            raise HTTPException(status_code=404, detail="Build context not found") from exc
    path = portal.route.replace(":appId", dashboard_app_id)
    if not path.startswith("/") or path.startswith("//") or "\\" in path:
        raise HTTPException(status_code=500, detail="App dashboard route is invalid")
    return {"path": path}


@app.get("/api/studio/overview")
async def get_app_overview(
    app_id: str | None = None,
    build_registry_id: str | None = None,
    principal: UserPrincipal = Depends(require_studio_user),
):
    if build_registry_id is not None:
        resolved_app_id, user_id = await _resolve_studio_artifact_scope(
            principal, build_registry_id=build_registry_id, app_id=app_id,
        )
    else:
        resolved_app_id, user_id = _resolve_studio_scope(principal, app_id=app_id)
    app_root = resolve_app_root()
    missing_surfaces = get_missing_studio_surfaces(app_root)
    if missing_surfaces:
        raise HTTPException(
            status_code=500,
            detail=f"App overview is missing required surfaces: {', '.join(missing_surfaces)}",
        )

    try:
        record = (await _get_app_registry_service().get_app_record(app_id=resolved_app_id, owner_user_id=user_id)).get("app")
        if not isinstance(record, dict):
            raise HTTPException(status_code=404, detail=f"App record not found: {resolved_app_id}")
        return build_app_overview_summary(
            app_root,
            surface="shell-overview",
            local_only=True,
            app_record=record,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to build app overview summary") from exc


@app.get("/api/studio/apps")
async def get_workspace_apps(
    principal: UserPrincipal = Depends(require_studio_user),
):
    _, user_id = _resolve_studio_scope(principal)
    app_root = resolve_app_root()
    missing_surfaces = get_missing_studio_surfaces(app_root)
    if missing_surfaces:
        raise HTTPException(
            status_code=500,
            detail=f"Apps surface is missing required surfaces: {', '.join(missing_surfaces)}",
        )

    try:
        apps = (await _get_app_registry_service().list_apps(owner_user_id=user_id)).get("apps") or []
        return build_apps_summary(
            app_root,
            surface="shell-apps",
            local_only=True,
            app_records=apps,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to build apps summary") from exc


class CreateWorkspaceAppRequest(BaseModel):
    model_config = {"extra": "forbid"}

    name: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=1000)


@app.post("/api/studio/apps")
async def create_workspace_app(
    body: CreateWorkspaceAppRequest,
    principal: UserPrincipal = Depends(require_studio_user),
):
    host_app_id, user_id = _resolve_studio_scope(principal)
    return await _get_app_registry_service().create_app_record(
        owner_user_id=user_id, name=body.name, description=body.description,
        chat_app_id=host_app_id,
    )


@app.delete("/api/studio/apps/{build_registry_id}")
async def delete_workspace_app(
    build_registry_id: str,
    principal: UserPrincipal = Depends(require_studio_user),
):
    _, user_id = _resolve_studio_scope(principal)
    try:
        result = await _get_app_registry_service().delete_app(build_registry_id=build_registry_id, owner_user_id=user_id)
        if not result.get("success"):
            raise HTTPException(status_code=404, detail="App registry record not found")
        return result
    except HTTPException:
        raise
    except ValueError as exc:
        logger.warning("delete_app validation error: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to delete app record")
        raise HTTPException(status_code=500, detail="Failed to delete app record") from exc


_owner_analytics_service: OwnerAnalyticsService | None = None


def _get_owner_analytics_service() -> OwnerAnalyticsService:
    global _owner_analytics_service
    if _owner_analytics_service is None:
        _owner_analytics_service = OwnerAnalyticsService()
    return _owner_analytics_service


def _analytics_period(period: str) -> PeriodWindow:
    try:
        return resolve_period(period)
    except PeriodError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _analytics_app_row(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "app_id": record.get("app_id"),
        "name": record.get("name") or record.get("app_id"),
        "lifecycle_state": record.get("lifecycle_state"),
    }


async def _get_owned_app_record(app_id: str, user_id: str) -> dict[str, Any]:
    """Resolve one app record through the ownership boundary or 404."""

    try:
        result = await _get_app_registry_service().get_app_record(
            app_id=app_id, owner_user_id=user_id
        )
        record = result.get("app")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Studio app record lookup failed")
        raise HTTPException(status_code=500, detail="Failed to resolve app record") from exc
    if not isinstance(record, dict):
        raise HTTPException(status_code=404, detail=f"App record not found: {app_id}")
    return record


class StudioDashboardBinding(BaseModel):
    """Host projection of an owned Factory app into the Studio dashboard."""

    dashboard_app_id: BuildIdentity
    build_registry_id: BuildIdentity
    target_app_id: BuildIdentity


def _dashboard_host_scope(principal: UserPrincipal) -> tuple[str, str]:
    host_app_id = getattr(app.state, "loaded_app_id", None) or platform_app._resolve_default_app_id()
    return _resolve_studio_scope(principal, app_id=host_app_id)


async def _get_owned_dashboard_app_record(principal: UserPrincipal, dashboard_app_id: str) -> dict[str, Any]:
    """Authorize the dashboard selection before using its Factory target for reads or writes."""
    host_app_id, user_id = _dashboard_host_scope(principal)
    bindings_for = getattr(app.state, "list_studio_app_bindings", None)
    unavailable = HTTPException(status_code=404, detail="Dashboard app not found")
    if bindings_for is None:
        if getattr(app.state, "resolve_studio_dashboard_app_id", None) is not None:
            raise unavailable
        record = await _get_owned_app_record(dashboard_app_id, user_id)
        # A local registration may describe the loaded host itself.
        if (record.get("chat_app_id") or record.get("app_id")) != host_app_id:
            raise unavailable
        return record

    rows = await bindings_for(host_app_id=host_app_id, owner_user_id=user_id, dashboard_app_id=dashboard_app_id)
    if len(rows) != 1:
        raise unavailable
    try:
        binding = StudioDashboardBinding.model_validate(rows[0])
    except ValidationError as exc:
        raise unavailable from exc
    if binding.dashboard_app_id != dashboard_app_id:
        raise unavailable
    registry = _get_app_registry_service()
    record = (await registry.get_app_record(build_registry_id=binding.build_registry_id, owner_user_id=user_id)).get("app")
    if (
        not isinstance(record, dict)
        or record.get("build_registry_id") != binding.build_registry_id
        or record.get("chat_app_id") != host_app_id
        or record.get("app_id") != binding.target_app_id
    ):
        raise unavailable
    return record


async def _dashboard_ids_by_target(
    principal: UserPrincipal, records: list[dict[str, Any]],
) -> dict[str, str]:
    """Project links in one read, retaining metrics even when an older app has no portal."""
    bindings_for = getattr(app.state, "list_studio_app_bindings", None)
    host_app_id, user_id = _dashboard_host_scope(principal)
    if bindings_for is None:
        if getattr(app.state, "resolve_studio_dashboard_app_id", None) is not None:
            return {}
        return {
            r["app_id"]: r["app_id"] for r in records
            if (r.get("chat_app_id") or r.get("app_id")) == host_app_id
        }
    targets = {
        r["build_registry_id"]: r["app_id"] for r in records
        if r.get("chat_app_id") == host_app_id and r.get("build_registry_id")
    }
    if not targets:
        return {}
    rows = await bindings_for(
        host_app_id=host_app_id, owner_user_id=user_id, build_registry_ids=list(targets),
    )
    projected: list[StudioDashboardBinding] = []
    for row in rows:
        try:
            binding = StudioDashboardBinding.model_validate(row)
        except ValidationError:
            continue
        if targets.get(binding.build_registry_id) == binding.target_app_id:
            projected.append(binding)
    target_counts = Counter(b.target_app_id for b in projected)
    route_counts = Counter(b.dashboard_app_id for b in projected)
    return {
        b.target_app_id: b.dashboard_app_id for b in projected
        if target_counts[b.target_app_id] == 1 and route_counts[b.dashboard_app_id] == 1
    }


def _analytics_funnel_for_record(record: dict[str, Any]) -> FunnelDef | None:
    """Load the app-declared funnel from the app's bundle, when present.

    Registered apps carry their bundle path; the Studio host's own app falls
    back to the resolved app root. A declared-but-invalid config is treated
    as no funnel here (the bundle's own load path is where it fails closed).
    """

    roots: list[Path] = []
    bundle_path = record.get("bundle_path")
    if bundle_path:
        roots.append(Path(str(bundle_path)))
    elif record.get("app_id") == platform_app._resolve_default_app_id():
        roots.append(resolve_app_root())
    for root in roots:
        if not root.exists():
            continue
        try:
            config = load_metrics_config(root)
        except MetricsConfigLoadError:
            logger.warning(
                "ANALYTICS_METRICS_CONFIG_INVALID app_id=%s root=%s",
                record.get("app_id"),
                root,
                exc_info=True,
            )
            return None
        if config is not None:
            return config.default_funnel
    return None


@app.get("/api/studio/analytics/portfolio")
async def get_studio_analytics_portfolio(
    period: str = "30d",
    principal: UserPrincipal = Depends(require_studio_user),
):
    """World View analytics across every app the caller owns."""

    _, user_id = _resolve_studio_scope(principal)
    window = _analytics_period(period)
    try:
        records = (await _get_app_registry_service().list_apps(owner_user_id=user_id)).get(
            "apps"
        ) or []
    except Exception as exc:
        logger.exception("Analytics portfolio app listing failed")
        raise HTTPException(status_code=500, detail="Failed to list apps for analytics") from exc

    records = [record for record in records if isinstance(record, dict) and record.get("app_id")]
    rows = [_analytics_app_row(record) for record in records]
    try:
        dashboard_ids = await _dashboard_ids_by_target(principal, records)
        result = await _get_owner_analytics_service().portfolio(rows, window)
        for row in result["apps"]:
            row["dashboard_app_id"] = dashboard_ids.get(row["app_id"])
        return result
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Analytics portfolio assembly failed")
        raise HTTPException(status_code=500, detail="Failed to build portfolio analytics") from exc


@app.get("/api/studio/analytics/apps/{app_id}")
async def get_studio_analytics_app(
    app_id: str,
    period: str = "30d",
    reference: Literal["target", "dashboard"] = "target",
    principal: UserPrincipal = Depends(require_studio_user),
):
    """App View analytics for one owned app, including movement and funnel."""

    _, user_id = _resolve_studio_scope(principal)
    window = _analytics_period(period)
    record = (
        await _get_owned_dashboard_app_record(principal, app_id)
        if reference == "dashboard" else await _get_owned_app_record(app_id, user_id)
    )
    funnel = _analytics_funnel_for_record(record)
    try:
        return await _get_owner_analytics_service().app_analytics(
            _analytics_app_row(record), window, funnel=funnel
        )
    except Exception as exc:
        logger.exception("Analytics app assembly failed")
        raise HTTPException(status_code=500, detail="Failed to build app analytics") from exc


@app.get("/api/studio/analytics/apps/{app_id}/metrics/{metric_id}")
async def get_studio_analytics_metric_detail(
    app_id: str,
    metric_id: str,
    period: str = "30d",
    reference: Literal["target", "dashboard"] = "target",
    principal: UserPrincipal = Depends(require_studio_user),
):
    """Drill-down detail for one metric on one owned app."""

    _, user_id = _resolve_studio_scope(principal)
    window = _analytics_period(period)
    record = (
        await _get_owned_dashboard_app_record(principal, app_id)
        if reference == "dashboard" else await _get_owned_app_record(app_id, user_id)
    )
    try:
        peers = (await _get_app_registry_service().list_apps(owner_user_id=user_id)).get(
            "apps"
        ) or []
    except Exception:
        peers = []
    peer_rows = [
        _analytics_app_row(peer)
        for peer in peers
        if isinstance(peer, dict) and peer.get("app_id")
    ]
    try:
        return await _get_owner_analytics_service().metric_detail(
            metric_id,
            window,
            app=_analytics_app_row(record),
            peer_apps=peer_rows,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown metric: {metric_id}") from exc
    except Exception as exc:
        logger.exception("Analytics metric detail assembly failed")
        raise HTTPException(status_code=500, detail="Failed to build metric detail") from exc


@app.get("/api/studio/apps/{dashboard_app_id}/billing")
async def get_dashboard_app_billing(
    dashboard_app_id: str, principal: UserPrincipal = Depends(require_studio_user),
) -> dict[str, Any]:
    record = await _get_owned_dashboard_app_record(principal, dashboard_app_id)
    version = await _current_dashboard_app_bundle(record)
    result = {"dashboard_app_id": dashboard_app_id, "app_name": record.get("name") or record["app_id"]}
    if version is None:
        return {**result, "status": "no_build"}
    if version.validation_status != ArtifactValidationStatus.PASSED or version.app_validation_status != "passed":
        return {**result, "status": "validation_required"}

    try:
        files, diagnostics = await read_artifact_bundle(version)
    except (ValueError, OSError, ContentNotFoundError, zipfile.BadZipFile) as exc:
        raise HTTPException(status_code=409, detail="The selected build archive could not be verified.") from exc
    if any(item.get("blocking") for item in diagnostics):
        raise HTTPException(status_code=409, detail="The selected build archive could not be verified.")
    bundle_path = "config/subscriptions.yaml"
    matches = [path for path in (bundle_path, app_bundle_workspace_path(bundle_path)) if path in files]
    if not matches:
        return {**result, "status": "no_catalog"}
    if len(matches) != 1:
        raise HTTPException(status_code=409, detail="The selected build contains conflicting subscription catalogs.")
    try:
        catalog = SubscriptionsConfig.model_validate(yaml.safe_load(files[matches[0]]))
    except (ValidationError, yaml.YAMLError) as exc:
        raise HTTPException(status_code=422, detail="The selected build's subscription catalog is invalid.") from exc

    # V1 has one top-level plan list. V2 groups plans by product.
    groups = catalog.products or [catalog]
    return {
        **result, "status": "configured", "artifact_version_id": version.id,
        "products": [{
            "product_id": getattr(group, "product_id", None),
            "label": group.label,
            "default_plan_id": group.default_plan_id,
            "plans": [
                plan.model_dump(include={"plan_id", "label", "description", "price"})
                for plan in group.plans
            ],
        } for group in groups],
    }


@app.get("/api/studio/integrations")
async def get_app_integrations(
    app_id: str | None = None,
    principal: UserPrincipal = Depends(require_studio_user),
):
    app_id, _ = await _resolve_owned_app_scope(principal, app_id=app_id)
    return await build_integrations_summary(app_id=app_id)


async def _connector_scope(
    principal: UserPrincipal, *, app_id: str | None, dashboard_app_id: str | None,
) -> tuple[str, str]:
    if dashboard_app_id is None:
        return await _resolve_owned_app_scope(principal, app_id=app_id)
    if app_id is not None:
        raise HTTPException(status_code=400, detail="Choose one app selector")
    record = await _get_owned_dashboard_app_record(principal, dashboard_app_id)
    return record["app_id"], record["owner_user_id"]


def _selected_connector_record(record: dict[str, Any]) -> dict[str, Any]:
    # The app Health page reads these same connectors from /api/studio/integrations,
    # which sends lifecycle_status and the last check's error_code. Without them
    # this list called a connector "Ready" that Health reported as not working.
    fields = (
        "service", "display_name", "notes", "status", "lifecycle_status", "secret_available", "ready",
        "expires_at", "updated_at",
    )
    result = {key: record[key] for key in fields if key in record}
    health = record.get("health") or {}
    result["health"] = {
        key: health[key]
        for key in ("status", "message", "last_checked_at", "missing_fields", "error_code", "health_check_supported")
        if key in health
    }
    return result


@app.get("/api/studio/integrations/connectors")
async def get_integration_connectors(
    app_id: str | None = None,
    principal: UserPrincipal = Depends(require_studio_user),
    dashboard_app_id: str | None = None,
):
    app_id, _ = await _connector_scope(principal, app_id=app_id, dashboard_app_id=dashboard_app_id)
    connectors = await list_connectors(scope=ConnectorStore.SCOPE_APP, scope_id=app_id)
    if dashboard_app_id is None:
        return {
            "app_id": app_id,
            "connectors": [_redact_connector_record(connector) for connector in connectors],
        }
    from factory_app.app.modules.workspace_integrations.backend.repo import (
        IntegrationDeclarationsRepo,
    )
    from factory_app.app.modules.workspace_integrations.backend.schemas import INTEGRATIONS_CATALOG

    actions = getattr(app.state, "module_action_surfaces", {}).get("workspace_integrations", {})
    can_remove = (
        "delete_app_integration_need" in actions
        and actions["delete_app_integration_need"] not in {"internal", "admin_internal"}
        and (
            principal.has_local_development_access
            or "workspace_integrations.manage" in resolve_module_permissions(principal, app=app)
        )
    )
    declarations = await IntegrationDeclarationsRepo().get_for_app(app_id=app_id)
    return {
        "app_id": app_id,
        "connectors": [_selected_connector_record(connector) for connector in connectors],
        "catalog": [{key: entry[key] for key in ("id", "name", "category")} for entry in INTEGRATIONS_CATALOG],
        "declarations": [
            {
                **{key: row.get(key) for key in (
                    "service", "catalog_id", "display_name", "kind", "purpose", "required_at", "optional",
                )},
                "removable": bool(row.get("removable") and can_remove),
            }
            for row in declarations if not row.get("removed")
        ],
        "credential_storage_available": bool((await get_connector_backend_summary()).get("configured")),
    }


class IntegrationConnectorPatchRequest(BaseModel):
    display_name: str | None = None
    notes: str | None = None
    status: Literal["metadata_only", "active", "expiring", "expired", "revoked"] | None = None
    expires_at: str | None = None
    public_config: dict[str, Any] | None = None
    required_fields: list[dict[str, Any]] | None = None
    secret_value: str | None = None
    ttl_days: int | None = Field(default=30, ge=1, le=3650)


class IntegrationConnectorCreateRequest(IntegrationConnectorPatchRequest):
    service: str = Field(..., description="Connector service identifier, such as email_provider or analytics_provider")


IntegrationConnectorPatchRequest.model_rebuild()
IntegrationConnectorCreateRequest.model_rebuild()


class AppContextRefreshPlanRequest(BaseModel):
    reason: str = Field(..., min_length=1)
    refresh_scope: ContextRefreshScope = ContextRefreshScope.DISCOVERY_INDEXING
    source_refs: list[SourceRef] | None = None
    current_context_version_id: str | None = None
    requested_by: str | None = None


class AppContextRefreshLaunchRequest(BaseModel):
    plan: ContextRefreshPlan
    confirm_launch: bool = False
    reason: str | None = None
    refresh_scope: ContextRefreshScope = ContextRefreshScope.DISCOVERY_INDEXING
    request_id: str | None = None


class AppContextRefreshCompleteRequest(BaseModel):
    plan: ContextRefreshPlan
    previous_context_version_id: str | None = None
    launch_result: ContextRefreshLaunchResult | None = None
    workflow_context_variables: dict[str, Any] = Field(default_factory=dict)


class AppIntelligenceIndexRequest(BaseModel):
    source_kind: Literal["local_workspace", "git_repository"] = "local_workspace"
    workspace_root: str | None = Field(default=None, min_length=1)
    repo_url: str | None = Field(default=None, min_length=1)
    branch: str | None = Field(default=None, max_length=240)
    monorepo_path: str | None = Field(default=None, max_length=1000)
    auth_connector_id: str | None = Field(default=None, max_length=160)
    ignored_paths: list[str] = Field(default_factory=list)
    workspace_key: str = "app_intelligence_workspace"
    make_current: bool = True
    scan_policy: dict[str, Any] | None = None


class AppSourceValidationRequest(BaseModel):
    allowed_kinds: list[Literal["install", "lint", "test", "build", "typecheck"]] = Field(default_factory=list)
    include_install: bool = False
    max_commands: int = Field(default=4, ge=1, le=12)
    timeout_seconds: int = Field(default=120, ge=5, le=900)
    confirm_execution: bool = False


class AppContextPolicyOverrideRequest(BaseModel):
    request_id: str = Field(..., min_length=1)
    context_version_id: str | None = None
    original_policy_decision: AppContextPolicyDecision
    override_decision: AppContextPolicyOverrideDecision
    reason: str = Field(..., min_length=1)
    reviewer: str = Field(..., min_length=1)
    applies_to_paths: list[str] = Field(default_factory=list)
    change_class: str | None = None
    refinement_lane: str | None = None
    policy_result: AppContextPolicyResult | None = None
    apply_to_plan: bool = False
    plan: dict[str, Any] | None = None


_SECRET_RESPONSE_KEYS = {
    "secret_value",
    "secret",
    "secret_hash",
    "secret_salt",
    "api_key",
    "apikey",
    "token",
    "access_token",
    "refresh_token",
    "auth_token",
    "bearer_token",
    "client_secret",
    "client_secret_hash",
    "password",
    "private_key",
    "private_cert",
}


def _redact_secret_fields(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).strip().lower() in _SECRET_RESPONSE_KEYS:
                continue
            redacted[key] = _redact_secret_fields(item)
        return redacted
    if isinstance(value, list):
        return [_redact_secret_fields(item) for item in value]
    return value


def _redact_connector_record(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(record, dict):
        return None
    enriched = dict(record)
    if not isinstance(enriched.get("health"), dict):
        enriched["health"] = compute_connector_health(enriched, checked_by="manual")
    return _redact_secret_fields(enriched)


def _redact_secret_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None
    return _redact_secret_fields(result)


def _context_refresh_policy(reason: str, warnings: list[str] | None = None) -> AppContextPolicyResult:
    return AppContextPolicyResult(
        decision=AppContextPolicyDecision.BLOCK_REQUIRES_CONTEXT_REFRESH,
        allowed=False,
        blocking=True,
        risk_level="high",
        reasons=[reason],
        warnings=list(warnings or []),
        requires_context_refresh=True,
    )


def _plan_from_operator_payload(payload: dict[str, Any]) -> RefinementExecutionPlan | RefinementDryRunPlan:
    try:
        return RefinementExecutionPlan.model_validate(payload)
    except ValidationError:
        return RefinementDryRunPlan.model_validate(payload)


@app.get("/api/studio/apps/{app_id}/context")
async def get_studio_app_context_status(
    app_id: str,
    principal: UserPrincipal = Depends(require_studio_user),
):
    validate_path_id(app_id, "app_id")
    resolved_app_id, _ = await _resolve_owned_app_scope(principal, app_id=app_id)
    summary = await get_current_app_context_summary(
        app_id=resolved_app_id,
        artifact_store=get_artifact_store(),
    )
    graph_lookup = await get_current_app_context_graph(
        app_id=resolved_app_id,
        app_context_summary=summary,
        artifact_store=get_artifact_store(),
    )
    graph = graph_lookup.graph
    graph_status = {
        "available": graph is not None,
        "graph_id": graph.graph_id if graph is not None else None,
        "stale_status": graph.stale_status.value if graph is not None else None,
        "graph_hash": graph.graph_hash if graph is not None else None,
        "indexed_at": graph.indexed_at.isoformat() if graph is not None else None,
        "node_count": len(graph.nodes) if graph is not None else 0,
        "edge_count": len(graph.edges) if graph is not None else 0,
        "source_ref_count": len(graph.source_refs) if graph is not None else 0,
        "warnings": list(graph_lookup.warnings),
    }
    latest_job = await get_latest_app_intelligence_index_job(app_id=resolved_app_id)
    context_readiness = _studio_context_readiness(
        summary=summary,
        graph_status=graph_status,
        latest_job=latest_job,
    )
    return _redact_secret_fields(
        {
            "app_id": resolved_app_id,
            "app_context_summary": summary.model_dump(mode="json"),
            "context_graph_status": graph_status,
            "context_readiness": context_readiness,
            "index_job": public_app_intelligence_index_job(latest_job),
            "stale_status": summary.stale_status,
            "warnings": [*list(summary.warnings), *list(graph_lookup.warnings)],
            "artifact_refs": [ref.model_dump(mode="json") for ref in summary.artifact_refs],
            "ownership": summary.ownership.model_dump(mode="json"),
        }
    )


@app.post("/api/studio/apps/{app_id}/context/app-intelligence/index", status_code=202)
async def index_studio_app_intelligence_context(
    app_id: str,
    body: AppIntelligenceIndexRequest,
    background_tasks: BackgroundTasks,
    principal: UserPrincipal = Depends(require_studio_user),
):
    validate_path_id(app_id, "app_id")
    resolved_app_id, user_id = await _resolve_owned_app_scope(principal, app_id=app_id)
    _authorize_http_source_import(body, principal=principal)
    return await _start_studio_app_intelligence_index_job(
        app_id=resolved_app_id,
        user_id=user_id,
        body=body,
        background_tasks=background_tasks,
    )


@app.post("/api/studio/apps/{app_id}/context/source-import", status_code=202)
async def import_studio_app_source_context(
    app_id: str,
    body: AppIntelligenceIndexRequest,
    background_tasks: BackgroundTasks,
    principal: UserPrincipal = Depends(require_studio_user),
):
    validate_path_id(app_id, "app_id")
    resolved_app_id, user_id = await _resolve_owned_app_scope(principal, app_id=app_id)
    _authorize_http_source_import(body, principal=principal)
    return await _start_studio_app_intelligence_index_job(
        app_id=resolved_app_id,
        user_id=user_id,
        body=body,
        background_tasks=background_tasks,
    )


@app.get("/api/studio/apps/{app_id}/context/app-intelligence/index/latest")
async def get_latest_studio_app_intelligence_index_job(
    app_id: str,
    principal: UserPrincipal = Depends(require_studio_user),
):
    validate_path_id(app_id, "app_id")
    resolved_app_id, _ = await _resolve_owned_app_scope(principal, app_id=app_id)
    job = await get_latest_app_intelligence_index_job(app_id=resolved_app_id)
    return _redact_secret_fields({"app_id": resolved_app_id, "index_job": public_app_intelligence_index_job(job)})


@app.get("/api/studio/apps/{app_id}/context/app-intelligence/index/{job_id}")
async def get_studio_app_intelligence_index_job(
    app_id: str,
    job_id: str,
    principal: UserPrincipal = Depends(require_studio_user),
):
    validate_path_id(app_id, "app_id")
    validate_path_id(job_id, "job_id")
    resolved_app_id, _ = await _resolve_owned_app_scope(principal, app_id=app_id)
    job = await get_app_intelligence_index_job(app_id=resolved_app_id, job_id=job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="App Intelligence index job not found.")
    return _redact_secret_fields({"app_id": resolved_app_id, "index_job": public_app_intelligence_index_job(job)})


@app.post("/api/studio/apps/{app_id}/context/validation/run")
async def run_studio_app_source_validation(
    app_id: str,
    body: AppSourceValidationRequest,
    principal: UserPrincipal = Depends(require_studio_user),
):
    validate_path_id(app_id, "app_id")
    if not body.confirm_execution:
        raise HTTPException(status_code=400, detail="confirm_execution=true is required to run app validation.")
    resolved_app_id, _ = await _resolve_owned_app_scope(principal, app_id=app_id)
    try:
        result = await run_current_app_source_validation(
            app_id=resolved_app_id,
            artifact_store=get_artifact_store(),
            allowed_kinds=body.allowed_kinds,
            include_install=body.include_install,
            max_commands=body.max_commands,
            timeout_seconds=body.timeout_seconds,
            confirm_execution=body.confirm_execution,
        )
    except ValueError as exc:
        logger.warning("run_app_source_validation validation error app=%s: %s", app_id, exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _redact_secret_fields(
        {
            "app_id": resolved_app_id,
            "validation": result.model_dump(mode="json"),
        }
    )


def _authorize_http_source_import(
    body: AppIntelligenceIndexRequest, *, principal: UserPrincipal,
) -> None:
    """Keep server filesystem paths out of authenticated Studio requests."""
    if body.source_kind == "local_workspace":
        require_http_local_source_mode(principal)


async def _start_studio_app_intelligence_index_job(
    *,
    app_id: str,
    user_id: str | None,
    body: AppIntelligenceIndexRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    try:
        _validate_app_intelligence_index_request(body)
        job = create_app_intelligence_index_job(
            app_id=app_id,
            requested_by=user_id,
            source_kind=body.source_kind,
            workspace_root=body.workspace_root,
            repo_url=body.repo_url,
            branch=body.branch,
            monorepo_path=body.monorepo_path,
            auth_connector_id=body.auth_connector_id,
            ignored_paths=body.ignored_paths,
            workspace_key=body.workspace_key,
            make_current=body.make_current,
            scan_policy=body.scan_policy,
        )
        job = await save_app_intelligence_index_job(job)
    except ValueError as exc:
        logger.warning("create_app_intelligence_index_job validation error app=%s: %s", app_id, exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    background_tasks.add_task(_run_studio_app_intelligence_index_job, app_id, job.job_id)
    return _redact_secret_fields(
        {
            "app_id": app_id,
            "accepted": True,
            "index_job": public_app_intelligence_index_job(job),
        }
    )


def _validate_app_intelligence_index_request(body: AppIntelligenceIndexRequest) -> None:
    if body.source_kind == "local_workspace" and not body.workspace_root:
        raise ValueError("workspace_root is required for local workspace indexing")
    if body.source_kind == "git_repository" and not body.repo_url:
        raise ValueError("repo_url is required for repository import")


async def _run_studio_app_intelligence_index_job(app_id: str, job_id: str) -> None:
    job = await get_app_intelligence_index_job(app_id=app_id, job_id=job_id)
    if job is None:
        return
    try:
        if job.source_kind == "git_repository":
            job = advance_app_intelligence_index_job(job, phase_id="repo_clone")
            job = await save_app_intelligence_index_job(job)

        import_result = await to_thread(
            resolve_source_import,
            app_id=job.app_id,
            request=_source_import_request_from_job(job),
        )
        job = job.model_copy(
            update={
                "workspace_root": import_result.selected_root,
                "import_result": import_result.model_dump(mode="python"),
                "warnings": dedupe_strings([*job.warnings, *import_result.warnings]),
            }
        )
        job = await save_app_intelligence_index_job(job)

        async def progress_callback(phase_id: str, details: dict[str, Any]) -> None:
            nonlocal job
            job = advance_app_intelligence_index_job(
                job,
                phase_id=phase_id,
                message=str((details or {}).get("message") or ""),
                details=details,
            )
            job = await save_app_intelligence_index_job(job)

        result = await index_workspace_app_intelligence(
            app_id=job.app_id,
            workspace_root=import_result.selected_root,
            artifact_store=get_artifact_store(),
            workspace_key=job.workspace_key,
            source_workflow="studio_app_intelligence_index",
            source_chat_id=job.requested_by,
            scan_policy=source_import_scan_policy(job.scan_policy, ignored_paths=import_result.ignored_paths),
            make_current=job.make_current,
            progress_callback=progress_callback,
        )
        job = complete_app_intelligence_index_job(
            job,
            app_intelligence=_app_intelligence_result_payload(result),
            context_readiness=_index_result_readiness(result),
            warnings=result.warnings,
        )
        await save_app_intelligence_index_job(job)
    except Exception as exc:
        logger.exception("Studio App Intelligence index job failed job_id=%s", job_id)
        failed = fail_app_intelligence_index_job(job, error=str(exc), warnings=job.warnings)
        await save_app_intelligence_index_job(failed)


def _source_import_request_from_job(job: AppIntelligenceIndexJob) -> SourceImportRequest:
    return SourceImportRequest(
        source_kind=job.source_kind,
        workspace_root=job.workspace_root,
        repo_url=job.repo_url,
        branch=job.branch,
        monorepo_path=job.monorepo_path,
        auth_connector_id=job.auth_connector_id,
        ignored_paths=list(job.ignored_paths),
    )


def _app_intelligence_result_payload(result: Any) -> dict[str, Any]:
    return {
        "app_bundle_artifact_version_id": result.app_bundle_artifact_version_id,
        "source_context_artifact_version_id": result.source_context_artifact_version_id,
        "app_intelligence_artifact_version_id": result.app_intelligence_artifact_version_id,
        "app_context_version_id": result.app_context_version_id,
        "app_context_artifact_version_id": result.app_context_artifact_version_id,
        "graph_artifact_version_id": result.graph_artifact_version_id,
        "artifact_path": result.artifact_path,
        "indexed_file_count": result.indexed_file_count,
        "scan_health": result.scan_health,
        "health_report": result.health_report,
        "framework_detection": result.framework_detection,
        "warnings": result.warnings,
    }


def _index_result_readiness(result: Any) -> dict[str, Any]:
    frameworks = dict(result.framework_detection or {})
    graph_health = dict(result.health_report or {})
    status = "ready" if result.indexed_file_count > 0 and not graph_health.get("blockers") else "degraded"
    return {
        "status": status,
        "indexed_file_count": result.indexed_file_count,
        "primary_framework_id": frameworks.get("primary_framework_id"),
        "primary_framework_label": frameworks.get("primary_framework_label"),
        "framework_count": len(frameworks.get("frameworks") or []),
        "validation_command_count": len(frameworks.get("validation_commands") or []),
        "warnings": dedupe_strings([*list(result.warnings or []), *list(graph_health.get("warnings") or [])]),
    }


def _job_readiness(job: AppIntelligenceIndexJob | None) -> dict[str, Any] | None:
    if job is None:
        return None
    if job.context_readiness:
        return dict(job.context_readiness)
    if job.status in {"queued", "running"}:
        return {"status": job.status, "progress_percent": job.progress_percent, "current_phase": job.current_phase}
    if job.status == "failed":
        return {"status": "failed", "error": job.error, "warnings": list(job.warnings)}
    return {"status": job.status, "progress_percent": job.progress_percent}


def _studio_context_readiness(*, summary: Any, graph_status: dict[str, Any], latest_job: AppIntelligenceIndexJob | None) -> dict[str, Any]:
    job_readiness = _job_readiness(latest_job)
    if job_readiness and latest_job and latest_job.status in {"queued", "running", "failed"}:
        return job_readiness
    if graph_status.get("available"):
        stale_status = getattr(summary.stale_status, "value", summary.stale_status)
        return {
            "status": "ready" if stale_status == "current" else "stale",
            "graph_id": graph_status.get("graph_id"),
            "indexed_at": graph_status.get("indexed_at"),
            "node_count": graph_status.get("node_count"),
            "edge_count": graph_status.get("edge_count"),
            "warnings": dedupe_strings([*list(summary.warnings), *list(graph_status.get("warnings") or [])]),
        }
    return job_readiness or {"status": "missing", "warnings": list(summary.warnings)}




@app.post("/api/studio/apps/{app_id}/context/refresh-plan")
async def create_studio_app_context_refresh_plan(
    app_id: str,
    body: AppContextRefreshPlanRequest,
    principal: UserPrincipal = Depends(require_studio_user),
):
    validate_path_id(app_id, "app_id")
    resolved_app_id, user_id = await _resolve_owned_app_scope(principal, app_id=app_id)
    summary = await get_current_app_context_summary(
        app_id=resolved_app_id,
        artifact_store=get_artifact_store(),
    )
    if body.current_context_version_id is not None:
        summary = summary.model_copy(update={"context_version_id": body.current_context_version_id})
    try:
        request = build_context_refresh_request(
            app_id=resolved_app_id,
            app_context_summary=summary,
            policy_result=_context_refresh_policy(body.reason, summary.warnings),
            reason=body.reason,
            source_refs=body.source_refs,
            requested_by=body.requested_by or user_id,
            refresh_scope=body.refresh_scope,
        )
        plan = build_context_refresh_plan(
            policy_result=_context_refresh_policy(body.reason, summary.warnings),
            app_context_summary=summary,
            request=request,
        )
    except ValueError as exc:
        logger.warning("build_context_refresh_plan validation error app=%s: %s", app_id, exc)
        raise HTTPException(status_code=400, detail="Invalid context refresh parameters.") from exc
    return _redact_secret_fields(
        {
            "app_id": resolved_app_id,
            "context_refresh_request": request.model_dump(mode="json"),
            "context_refresh_plan": plan.model_dump(mode="json"),
            "launched": False,
        }
    )


@app.post("/api/studio/apps/{app_id}/context/refresh-launch")
async def launch_studio_app_context_refresh(
    app_id: str,
    body: AppContextRefreshLaunchRequest,
    principal: UserPrincipal = Depends(require_studio_user),
):
    validate_path_id(app_id, "app_id")
    if not body.confirm_launch:
        raise HTTPException(status_code=400, detail="confirm_launch=true is required to launch context refresh.")
    resolved_app_id, user_id = await _resolve_owned_app_scope(principal, app_id=app_id)
    try:
        result = await launch_context_refresh_plan(
            body.plan,
            app_id=resolved_app_id,
            user_id=user_id,
            refresh_scope=body.refresh_scope,
            reason=body.reason,
            request_id=body.request_id,
            session_router=get_session_router(),
        )
    except ValueError as exc:
        logger.warning("launch_context_refresh_plan validation error app=%s: %s", app_id, exc)
        raise HTTPException(status_code=400, detail="Invalid context refresh launch parameters.") from exc
    return _redact_secret_fields(
        {
            "app_id": resolved_app_id,
            "context_refresh_launch": result.model_dump(mode="json"),
        }
    )


@app.post("/api/studio/apps/{app_id}/context/refresh-complete")
async def complete_studio_app_context_refresh(
    app_id: str,
    body: AppContextRefreshCompleteRequest,
    principal: UserPrincipal = Depends(require_studio_user),
):
    validate_path_id(app_id, "app_id")
    resolved_app_id, _ = await _resolve_owned_app_scope(principal, app_id=app_id)
    try:
        result = await complete_context_refresh(
            body.plan,
            app_id=resolved_app_id,
            artifact_store=get_artifact_store(),
            previous_context_version_id=body.previous_context_version_id,
            launch_result=body.launch_result,
            workflow_context_variables=dict(body.workflow_context_variables or {}),
        )
    except ValueError as exc:
        logger.warning("complete_context_refresh validation error app=%s: %s", app_id, exc)
        raise HTTPException(status_code=400, detail="Invalid context refresh completion parameters.") from exc
    return _redact_secret_fields(
        {
            "app_id": resolved_app_id,
            "context_refresh_result": result.model_dump(mode="json"),
        }
    )


@app.post("/api/studio/apps/{app_id}/context/override")
async def create_studio_app_context_policy_override(
    app_id: str,
    body: AppContextPolicyOverrideRequest,
    principal: UserPrincipal = Depends(require_studio_user),
):
    validate_path_id(app_id, "app_id")
    resolved_app_id, _ = await _resolve_owned_app_scope(principal, app_id=app_id)
    policy_result = body.policy_result or AppContextPolicyResult(
        decision=body.original_policy_decision,
        allowed=False,
        blocking=True,
    )
    try:
        override = create_app_context_policy_override(
            policy_result=policy_result,
            app_id=resolved_app_id,
            request_id=body.request_id,
            context_version_id=body.context_version_id,
            override_decision=body.override_decision,
            reason=body.reason,
            reviewer=body.reviewer,
            applies_to_paths=body.applies_to_paths,
            applies_to_change_class=body.change_class,
            applies_to_refinement_lane=body.refinement_lane,
        )
        applied_plan = None
        if body.apply_to_plan:
            if body.plan is None:
                raise ValueError("plan is required when apply_to_plan=true")
            applied_plan = apply_app_context_policy_override(
                _plan_from_operator_payload(body.plan),
                override,
            )
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid policy override request") from exc

    return _redact_secret_fields(
        {
            "app_id": resolved_app_id,
            "app_context_policy_override": override.model_dump(mode="json"),
            "applied_plan": applied_plan.model_dump(mode="json") if applied_plan is not None else None,
            "mutation_allowed": False,
        }
    )


async def _connector_write_result(
    app_id: str, service: str, dashboard_app_id: str | None,
    record: dict[str, Any] | None, secret_result: dict[str, Any] | None,
) -> dict[str, Any]:
    if dashboard_app_id is None:
        return {
            "app_id": app_id,
            "connector": _redact_connector_record(record),
            "secret_result": _redact_secret_result(secret_result),
        }
    saved = await get_connector(scope=ConnectorStore.SCOPE_APP, scope_id=app_id, service=service)
    if saved is None:
        raise HTTPException(status_code=404, detail="Connector not found")
    return {"app_id": app_id, "connector": _selected_connector_record(saved)}


@app.post("/api/studio/integrations/connectors")
async def create_or_update_integration_connector(
    body: IntegrationConnectorCreateRequest,
    app_id: str | None = None,
    principal: UserPrincipal = Depends(require_studio_user),
    dashboard_app_id: str | None = None,
):
    app_id, user_id = await _connector_scope(principal, app_id=app_id, dashboard_app_id=dashboard_app_id)
    # An empty identifier or a single-dot path segment has no per-service edit route.
    if body.service.strip() in {"", "."}:
        raise HTTPException(status_code=400, detail="Invalid service format")
    if dashboard_app_id is not None:
        validate_path_id(body.service, "service")
    secret_result: dict[str, Any] | None = None
    if body.secret_value:
        secret_result = await save_connector(
            scope=ConnectorStore.SCOPE_APP,
            scope_id=app_id,
            user_id=user_id,
            service=body.service,
            secret_value=body.secret_value,
            display_name=body.display_name,
            ttl_days=body.ttl_days or 30,
            allow_metadata_only=dashboard_app_id is None,
            public_config=body.public_config,
            required_fields=body.required_fields,
        )
        if dashboard_app_id is not None and not secret_result.get("success"):
            raise HTTPException(status_code=503, detail="Credential could not be saved")

    record = await patch_connector(
        scope=ConnectorStore.SCOPE_APP,
        scope_id=app_id,
        service=body.service,
        user_id=user_id,
        display_name=body.display_name,
        notes=body.notes,
        status=body.status,
        expires_at=body.expires_at,
        public_config=body.public_config,
        required_fields=body.required_fields,
        create_if_missing=not body.secret_value,
    )
    return await _connector_write_result(app_id, body.service, dashboard_app_id, record, secret_result)


@app.patch("/api/studio/integrations/connectors/{service}")
async def patch_integration_connector(
    service: str,
    body: IntegrationConnectorPatchRequest,
    app_id: str | None = None,
    principal: UserPrincipal = Depends(require_studio_user),
    dashboard_app_id: str | None = None,
):
    validate_path_id(service, "service")
    app_id, user_id = await _connector_scope(principal, app_id=app_id, dashboard_app_id=dashboard_app_id)
    store = ConnectorStore()
    existing = await store.get(scope=ConnectorStore.SCOPE_APP, scope_id=app_id, service=service)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Connector not found: {service}")

    secret_result: dict[str, Any] | None = None
    if body.secret_value:
        secret_result = await save_connector(
            scope=ConnectorStore.SCOPE_APP,
            scope_id=app_id,
            user_id=user_id,
            service=service,
            secret_value=body.secret_value,
            display_name=body.display_name,
            ttl_days=body.ttl_days or 30,
            allow_metadata_only=dashboard_app_id is None,
            public_config=body.public_config,
            required_fields=body.required_fields,
        )
        if dashboard_app_id is not None and not secret_result.get("success"):
            raise HTTPException(status_code=503, detail="Credential could not be saved")
    record = await patch_connector(
        scope=ConnectorStore.SCOPE_APP,
        scope_id=app_id,
        service=service,
        user_id=user_id,
        display_name=body.display_name,
        notes=body.notes,
        status=body.status,
        expires_at=body.expires_at,
        public_config=body.public_config,
        required_fields=body.required_fields,
    )
    return await _connector_write_result(app_id, service, dashboard_app_id, record, secret_result)


@app.post("/api/studio/integrations/connectors/{service}/health-check")
async def check_integration_connector_health(
    service: str,
    app_id: str | None = None,
    principal: UserPrincipal = Depends(require_studio_user),
    dashboard_app_id: str | None = None,
):
    validate_path_id(service, "service")
    app_id, _ = await _connector_scope(principal, app_id=app_id, dashboard_app_id=dashboard_app_id)
    result = await run_connector_health_check(app_id=app_id, service=service, checked_by="manual")
    return {
        "app_id": app_id,
        "service": service,
        "health": _redact_secret_fields(
            {
                "status": result.get("status"),
                "last_checked_at": result.get("last_checked_at"),
                "message": result.get("message"),
                "missing_fields": result.get("missing_fields") or [],
                "checked_by": result.get("checked_by") or "manual",
                "safe_details": result.get("health_details") or {},
                "error_code": result.get("error_code"),
                "health_check_supported": bool(result.get("health_check_supported")),
                "frontend_safe": True,
            }
        ),
    }


@app.delete("/api/studio/integrations/connectors/{service}")
async def remove_integration_connector(
    service: str,
    app_id: str | None = None,
    principal: UserPrincipal = Depends(require_studio_user),
    dashboard_app_id: str | None = None,
):
    validate_path_id(service, "service")
    app_id, _ = await _connector_scope(principal, app_id=app_id, dashboard_app_id=dashboard_app_id)
    result = await delete_connector(scope=ConnectorStore.SCOPE_APP, scope_id=app_id, service=service)
    if dashboard_app_id is not None and result.get("error"):
        raise HTTPException(status_code=503, detail="Connector could not be removed")
    if not result.get("deleted"):
        raise HTTPException(status_code=404, detail=f"Connector not found: {service}")
    return {
        "app_id": app_id,
        **result,
    }


async def _resolve_studio_artifact_scope(
    principal: UserPrincipal, *, build_registry_id: str | None, app_id: str | None = None,
) -> tuple[str, str]:
    host_app_id, user_id = _resolve_studio_scope(principal, app_id=app_id)
    try:
        reference = BuildTargetReference(build_registry_id=build_registry_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid build target") from exc
    if reference.build_registry_id is None:
        raise HTTPException(status_code=400, detail="Select a registered build target")
    result = await _get_app_registry_service().get_app_record(
        build_registry_id=reference.build_registry_id, owner_user_id=user_id,
    )
    record = result.get("app")
    if not record or record.get("chat_app_id") != host_app_id:
        raise HTTPException(status_code=404, detail="Build target not found")
    return record["app_id"], user_id


async def _current_dashboard_app_bundle(record: dict[str, Any]) -> BuildRecord | None:
    """Resolve only the selected registry's current generated app artifact."""
    run = record.get("current_build_run") or {}
    version_id = run.get("artifact_version_id")
    if not version_id:
        return None
    target_app_id = record["app_id"]
    version = await get_artifact_store().get_build_record(app_id=target_app_id, build_record_id=version_id)
    if not version or version.id != version_id or version.app_id != target_app_id or version.build_family != "app_bundle":
        raise HTTPException(status_code=409, detail="The current build has no generated app bundle.")
    if _version_metadata(version).get("bundle_mode") == "brownfield_genesis_import":
        raise HTTPException(status_code=409, detail="A source import needs a generated app build.")
    if version.lifecycle_status not in {ArtifactLifecycleStatus.DRAFT, ArtifactLifecycleStatus.CURRENT}:
        raise HTTPException(status_code=409, detail="The current build no longer has an active app bundle.")
    registry_id = record.get("build_registry_id")
    if not registry_id:
        raise HTTPException(status_code=409, detail="The selected app has no registered build.")
    binding = _artifact_build_binding(version, build_registry_id=registry_id, target_app_id=target_app_id)
    if binding.build_id != run.get("build_id"):
        raise HTTPException(status_code=409, detail="The app bundle does not match the current build.")
    return version


async def _app_bundle_download(version: BuildRecord, *, target_app_id: str, artifact_version_id: str) -> Response:
    _enforce_artifact_validation_gate(version, action="exported")
    return Response(
        await _verified_app_bundle(version, max_bytes=APP_BUNDLE_MAX_TOTAL_BYTES), media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{target_app_id}-{artifact_version_id}.zip"'},
    )


def _check_launch_archive(version: BuildRecord) -> None:
    try:
        entry = resolve_canonical_bundle_entry(version)
    except CanonicalBundleEntryError as exc:
        raise HTTPException(status_code=409, detail="The build has no verified archive entry.") from exc
    if entry.size_bytes is not None and entry.size_bytes > APP_BUNDLE_MAX_TOTAL_BYTES:
        raise HTTPException(status_code=409, detail="The app archive exceeds the export size limit.")
    metadata = _version_metadata(version)
    if metadata.get("content_digest") and metadata.get("content_ref"):
        raise HTTPException(status_code=409, detail="The app archive has conflicting content references.")
    if not any(metadata.get(key) for key in ("content_digest", "content_ref", "artifact_path")):
        raise HTTPException(status_code=409, detail="The app archive has no stored content.")


@app.get("/api/studio/apps/{dashboard_app_id}/launch")
async def get_dashboard_app_launch(
    dashboard_app_id: str, principal: UserPrincipal = Depends(require_studio_user),
) -> dict[str, Any]:
    record = await _get_owned_dashboard_app_record(principal, dashboard_app_id)
    version = await _current_dashboard_app_bundle(record)
    if version is None:
        return {"status": "no_build"}
    try:
        _enforce_artifact_validation_gate(version, action="exported")
    except HTTPException:
        return {"status": "validation_required"}
    _check_launch_archive(version)
    return {"status": "ready", "artifact_version_id": version.id, "filename": f"{version.app_id}-{version.id}.zip"}


@app.get("/api/studio/apps/{dashboard_app_id}/launch/{artifact_version_id}/download")
async def download_dashboard_app_bundle(
    dashboard_app_id: str, artifact_version_id: str,
    principal: UserPrincipal = Depends(require_studio_user),
) -> Response:
    validate_path_id(artifact_version_id, "artifact_version_id")
    record = await _get_owned_dashboard_app_record(principal, dashboard_app_id)
    version = await _current_dashboard_app_bundle(record)
    if version is None or version.id != artifact_version_id:
        raise HTTPException(status_code=409, detail="The selected build has changed. Refresh Launch before downloading.")
    _check_launch_archive(version)
    return await _app_bundle_download(version, target_app_id=version.app_id, artifact_version_id=artifact_version_id)


@app.get("/api/studio/build/artifacts/{artifact_version_id}/download")
async def download_build_artifact(
    artifact_version_id: str,
    build_registry_id: str,
    app_id: str | None = None,
    principal: UserPrincipal = Depends(require_studio_user),
):
    validate_path_id(artifact_version_id, "artifact_version_id")
    execution_app_id, owner_user_id = _resolve_studio_scope(principal, app_id=app_id)
    target_app_id, _ = await _resolve_studio_artifact_scope(
        principal, build_registry_id=build_registry_id, app_id=app_id,
    )
    version = await get_artifact_store().get_build_record(
        app_id=target_app_id, build_record_id=artifact_version_id,
    )
    if version is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    if _version_metadata(version).get("bundle_mode") == "brownfield_genesis_import":
        try:
            source_bytes = await read_imported_genesis_review_bundle(
                version, owner_user_id=owner_user_id,
                execution_app_id=execution_app_id, build_registry_id=build_registry_id,
                registry_service=_get_app_registry_service(),
            )
        except (GenesisImportError, ValueError, OSError, ContentNotFoundError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return Response(
            source_bytes, media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{target_app_id}-{artifact_version_id}.zip"'},
        )
    _artifact_build_binding(version, build_registry_id=build_registry_id, target_app_id=target_app_id)
    if version.build_family == "app_bundle":
        return await _app_bundle_download(version, target_app_id=target_app_id, artifact_version_id=artifact_version_id)
    zip_path = _artifact_bundle_path_from_version(version)
    if zip_path is None or not zip_path.is_file():
        raise HTTPException(status_code=404, detail="Artifact archive is unavailable")
    return FileResponse(zip_path, media_type="application/zip", filename=f"{target_app_id}-{artifact_version_id}.zip")


@app.get("/api/studio/build/history")
async def get_build_history(
    principal: UserPrincipal = Depends(require_studio_user),
    app_id: str | None = None,
    build_registry_id: str | None = None,
    build_family: str | None = None,
    build_key: str | None = None,
    build_record_id: str | None = None,
    limit: int = 25,
):
    """Return recent artifact versions and change requests for the current workspace."""
    app_id, _ = await _resolve_studio_artifact_scope(principal, app_id=app_id, build_registry_id=build_registry_id)
    artifact_store = get_artifact_store()
    versions = await artifact_store.list_build_records(
        app_id=app_id,
        build_family=build_family,
        build_key=build_key,
        limit=min(limit, 100),
    )
    change_requests = await artifact_store.list_change_requests(
        app_id=app_id,
        build_record_id=build_record_id,
        limit=min(limit, 100),
    )
    return {
        "app_id": app_id,
        "artifact_versions": [v.model_dump(by_alias=False, mode="python") for v in versions],
        "change_requests": [cr.model_dump(by_alias=False, mode="python") for cr in change_requests],
    }


@app.get("/api/studio/build/artifacts/{artifact_version_id}/bundle")
async def get_build_artifact_bundle(
    artifact_version_id: str,
    app_id: str | None = None,
    build_registry_id: str | None = None,
    principal: UserPrincipal = Depends(require_studio_user),
):
    validate_path_id(artifact_version_id, "artifact_version_id")
    host_app_id, _ = _resolve_studio_scope(principal, app_id=app_id)
    app_id, owner_user_id = await _resolve_studio_artifact_scope(principal, app_id=host_app_id, build_registry_id=build_registry_id)
    artifact_store = get_artifact_store()
    version = await artifact_store.get_build_record(
        app_id=app_id,
        build_record_id=artifact_version_id,
    )
    if not version:
        raise HTTPException(status_code=404, detail=f"Artifact version not found: {artifact_version_id}")
    if version.build_family not in {"app_bundle", "workflow_bundle"}:
        raise HTTPException(status_code=400, detail=f"Unsupported artifact kind for bundle workbench: {version.build_family}")

    zip_source = await _bundle_source_for_review(version)
    if zip_source is None:
        raise HTTPException(
            status_code=400,
            detail="This artifact version does not have a bundle path that the build surface can inspect.",
        )

    try:
        generated_files, skipped_files = _decode_text_bundle_entries(zip_source)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to load artifact bundle") from exc

    review_payload = await _build_artifact_review_payload(
        app_id=app_id,
        version=version,
        artifact_store=artifact_store,
    )
    app_record = (await _get_app_registry_service().get_app_record(
        app_id=app_id, owner_user_id=owner_user_id,
    )).get("app") or {}
    workbench_title = str(app_record.get("name") or "").strip() or (
        "Saved app" if version.build_family == "app_bundle" else "Saved workflow"
    )

    return {
        "app_id": app_id,
        "artifact_version_id": version.id,
        "build_family": version.build_family,
        "build_key": version.build_key,
        "source_workflow": version.source_workflow,
        "bundle_path": _version_metadata(version).get("artifact_path"),
        "generated_files": generated_files,
        "skipped_files": skipped_files,
        "workbench_ui": {"component": "AppWorkbench", "workflow_name": "AppGenerator"},
        "workbench": {
            "app_id": host_app_id,
            "target_app_id": app_id,
            "build_registry_id": build_registry_id,
            "title": workbench_title,
            "artifact_version_id": version.id,
            "build_family": version.build_family,
            "build_key": version.build_key,
            "generated_files": generated_files,
            "validation_result": _version_metadata(version).get("validation_result") or _version_metadata(version).get("app_validation_result"),
            "app_validation_status": version.app_validation_status or "pending",
            "app_validation_strategy_used": version.app_validation_strategy,
            "integration_test_result": _version_metadata(version).get("app_bundle_acceptance"),
        },
        "review": review_payload["review"],
        "refinement_session": review_payload["refinement_session"],
        "change_request": review_payload["change_request"],
    }


@app.get("/api/studio/build/artifacts/{artifact_version_id}/review")
async def get_build_artifact_review(
    artifact_version_id: str,
    app_id: str | None = None,
    build_registry_id: str | None = None,
    principal: UserPrincipal = Depends(require_studio_user),
):
    validate_path_id(artifact_version_id, "artifact_version_id")
    app_id, user_id = await _resolve_studio_artifact_scope(principal, app_id=app_id, build_registry_id=build_registry_id)
    artifact_store = get_artifact_store()
    version = await artifact_store.get_build_record(
        app_id=app_id,
        build_record_id=artifact_version_id,
    )
    if not version:
        raise HTTPException(status_code=404, detail=f"Artifact version not found: {artifact_version_id}")
    payload = await _build_artifact_review_payload(
        app_id=app_id,
        version=version,
        artifact_store=artifact_store,
    )
    if _version_metadata(version).get("bundle_mode") == "brownfield_genesis_import":
        row = (await _get_app_registry_service().get_app_record(
            owner_user_id=user_id, build_registry_id=build_registry_id,
        )).get("app")
        claim = row.get("genesis_import") if isinstance(row, dict) else None
        payload["genesis_import"] = claim if isinstance(claim, dict) and claim.get("build_record_id") == version.id else None
        if payload["genesis_import"] is not None:
            try:
                payload["genesis_acceptance_disclosure"] = genesis_acceptance_disclosure(
                    GenesisImportClaim.model_validate({
                        key: claim.get(key) for key in GenesisImportClaim.model_fields if key != "status"
                    })
                )
            except ValueError:
                payload["genesis_acceptance_disclosure"] = None
    return {"app_id": app_id, **payload}


class BuildArtifactAcceptanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    notes: str | None = Field(default=None, max_length=2000)


#: Per-app serialization of app bundle acceptances, so the app-owned pack code
#: rechecks and the acceptance they guard cannot interleave in this process.
_WORKSPACE_ACCEPTANCE_LOCKS: dict[str, Lock] = {}


def _changed_workspace_pack_paths(version) -> list[str]:  # noqa: ANN001
    scope = _version_metadata(version).get("refinement_scope")
    paths = scope.get("changed_workspace_pack_paths") if isinstance(scope, dict) else None
    return sorted(str(path) for path in paths) if isinstance(paths, list) else []


def _workspace_acceptance_guard(app_id: str, version) -> AbstractAsyncContextManager[Any]:  # noqa: ANN001
    if version.build_family != "app_bundle" and not _changed_workspace_pack_paths(version):
        return nullcontext()
    return _WORKSPACE_ACCEPTANCE_LOCKS.setdefault(app_id, Lock())


#: Largest app-owned pack file that acceptance compares; a larger one refuses
#: acceptance instead of being left out of the comparison.
_APP_OWNED_CODE_COMPARE_MAX_BYTES = 8_000_000
#: Largest total of the named entries read from one archive; past it, acceptance refuses too.
_APP_OWNED_CODE_COMPARE_MAX_TOTAL_BYTES = 16_000_000
_PACK_PROVENANCE_PATH = ".mozaiks/pack_provenance.json"


def _app_bundle_entries(zip_source: bytes, paths: set[str]) -> dict[str, bytes]:
    """Read the named app-relative entries of a verified app bundle in full.

    ``_decode_text_bundle_entries`` drops entries past its file count and size
    limits, so a comparison through it can miss an app-owned file. Here every
    named entry present is read; one that cannot be compared exactly (a
    duplicate, a symbolic link, or an entry over the compare bound) refuses,
    as do named entries whose sizes together pass the total compare bound.
    """
    with zipfile.ZipFile(io.BytesIO(zip_source), "r") as archive:
        named = [
            (name, info) for info in archive.infolist()
            if not info.is_dir() and (name := _normalize_bundle_entry_name(info.filename)) is not None
        ]
        found: dict[str, list[zipfile.ZipInfo]] = {}
        for path, info in _strip_shared_root_prefix(named):
            if path in paths:
                found.setdefault(path, []).append(info)
        for path, infos in found.items():
            if (
                len(infos) != 1 or _zipinfo_is_symlink(infos[0])
                or infos[0].file_size > _APP_OWNED_CODE_COMPARE_MAX_BYTES
            ):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"App-owned capability code ({path}) in this version or the current build could not be "
                        "compared. No version was accepted."
                    ),
                )
        # Checked before reading: a read never yields more than the entry's declared size.
        if sum(infos[0].file_size for infos in found.values()) > _APP_OWNED_CODE_COMPARE_MAX_TOTAL_BYTES:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"App-owned capability code ({', '.join(sorted(found))}) in this version or the current build "
                    "is too large to compare in full. No version was accepted."
                ),
            )
        entries = {path: archive.read(infos[0]) for path, infos in found.items()}
    return entries


async def _saved_parent_theme(version) -> dict[str, Any]:  # noqa: ANN001
    """Get visual context from the same verified app archive the owner selected."""
    entries = _app_bundle_entries(await _verified_app_bundle(version), {APP_THEME_PATH})
    if APP_THEME_PATH not in entries:
        return {}
    try:
        theme = json.loads(entries[APP_THEME_PATH])
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(status_code=409, detail="The saved build's theme contains invalid JSON.") from exc
    if not isinstance(theme, dict):
        raise HTTPException(status_code=409, detail="The saved build's theme must be a JSON object.")
    return theme


def _recorded_workspace_pack_paths(provenance_entry: bytes | None) -> set[str]:
    try:
        provenance = json.loads(provenance_entry or b"{}")
    except ValueError:
        return set()
    packs = provenance.get("packs") if isinstance(provenance, dict) else None
    return {
        str(entry.get("path")) for pack in packs or [] if isinstance(pack, dict)
        for entry in pack.get("materialized_owned_files") or []
        if isinstance(entry, dict) and entry.get("owner") == "workspace" and entry.get("path")
    }


#: The most CURRENT records of one build family and key acceptance reads.
_CURRENT_BUILDS_SCAN_LIMIT = 32


async def _current_builds_in_scope(version, *, app_id: str, artifact_store: Any) -> list[Any]:  # noqa: ANN001
    """The CURRENT records of ``version``'s build family and key, sorted by id; a failed read refuses.

    That is the scope in which accepting ``version`` supersedes the current
    build (``ArtifactStore.accept_build_record``), so a CURRENT record under
    another key of the same family, such as the App Intelligence index a
    promotion saves under ``app_intelligence_workspace``, is not the app's
    build here.
    """
    try:
        current = await artifact_store.list_build_records(
            app_id=app_id, build_family=version.build_family, build_key=version.build_key,
            lifecycle_status=ArtifactLifecycleStatus.CURRENT, limit=_CURRENT_BUILDS_SCAN_LIMIT + 1,
        )
    except Exception as exc:
        logger.warning("current_builds_unverified app=%s version=%s", app_id, version.id, exc_info=True)
        raise HTTPException(
            status_code=409,
            detail="The app's current build could not be read. No version was accepted.",
        ) from exc
    if len(current) > _CURRENT_BUILDS_SCAN_LIMIT:
        raise HTTPException(
            status_code=409,
            detail=f"The app has more than {_CURRENT_BUILDS_SCAN_LIMIT} current builds to check. No version was accepted.",
        )
    return sorted(current, key=lambda record: record.id)


async def _require_current_app_owned_pack_code(version, *, app_id: str, artifact_store: Any) -> None:  # noqa: ANN001
    """Reject a stale version that would replace app-owned pack code accepted since its parent.

    Acceptance replaces the whole bundle. A version made from a build that is
    no longer the app's accepted current build (the CURRENT record of its
    build family and key) carries that older build's app-owned pack code, such
    as a customized Messaging handler, so accepting it would silently revert an
    authorized change accepted since.
    """
    if version.build_family != "app_bundle":
        return
    for current in await _current_builds_in_scope(version, app_id=app_id, artifact_store=artifact_store):
        if current.id not in {version.id, version.parent_build_record_id}:
            await _require_same_app_owned_pack_code(version, current)


async def _require_same_app_owned_pack_code(version, current) -> None:  # noqa: ANN001
    """Refuse ``version`` when its app-owned pack code differs from the current build's."""
    current_zip = await _verified_app_bundle(current)
    version_zip = await _verified_app_bundle(version)
    paths: set[str] = set()
    for archive, record in ((current_zip, current), (version_zip, version)):
        provenance = _app_bundle_entries(archive, {_PACK_PROVENANCE_PATH}).get(_PACK_PROVENANCE_PATH)
        paths |= _recorded_workspace_pack_paths(provenance)
        declared = parent_workspace_pack_paths(_version_metadata(record).get("capability_packs") or [])
        if declared is None:
            # Its pack selection cannot be verified, so the app-owned paths it declares are unknown.
            if record is current:
                way_forward = (
                    f"Accept an AppGenerator revision made from build {record.id} first (it records a build whose "
                    "packs verify), then make further changes from that build."
                )
            else:
                way_forward = (
                    f"Make the change from the current build {current.id} instead, or accept an AppGenerator "
                    "revision of it first."
                )
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Build {record.id}'s capability packs cannot be resolved to verified sources, so its app-owned "
                    f"capability code cannot be compared with the other build's. {way_forward} No version was "
                    "accepted."
                ),
            )
        paths |= set(declared)
    current_files = _app_bundle_entries(current_zip, paths)
    version_files = _app_bundle_entries(version_zip, paths)
    changed = sorted(path for path in paths if current_files.get(path) != version_files.get(path))
    if changed:
        raise HTTPException(
            status_code=409,
            detail=(
                "This version was made from an earlier build and would replace app-owned capability code "
                f"accepted since ({', '.join(changed)}). Refine the current build instead. No version was accepted."
            ),
        )


async def _require_accepted_workspace_parent(
    version, *, app_id: str, owner_user_id: str, build_registry_id: str | None, artifact_store: Any,  # noqa: ANN001
) -> None:
    """Recheck at acceptance the authority an app-owned pack code change was admitted under.

    The server-recorded scope must still be an explicit or user-confirmed
    selection of the changed paths in this registered build. The app must have
    no accepted current build (a CURRENT record of the child's build family
    and key), or that build must still be the child's parent, and the
    registry's current build run must still name this child and its build.
    Otherwise nothing is accepted.
    """
    changed = _changed_workspace_pack_paths(version)
    metadata = _version_metadata(version)
    scope = metadata.get("refinement_scope") or {}
    if (
        scope.get("scope_origin") not in {"explicit", "confirmed"}
        or not set(changed) <= set(scope.get("selected_file_paths") or [])
        or not build_registry_id
        or metadata.get("build_registry_id") != build_registry_id
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "This change to app-owned capability code was not made in an authorized selected-file "
                "refinement of this build. No version was accepted."
            ),
        )
    current = await _current_builds_in_scope(version, app_id=app_id, artifact_store=artifact_store)
    if not version.parent_build_record_id or any(
        record.id != version.parent_build_record_id for record in current
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "This change to app-owned capability code was made from a build that is no longer the "
                "app's accepted current build. Refine the current build again. No version was accepted."
            ),
        )
    # The run that stored this child marked its own parent stale, so no build
    # is CURRENT while the child awaits review. The registry's compare-and-set
    # run chain then decides: the child must still be the latest registered
    # run, admitted against the parent that was current when its run began.
    registry_record = (await _get_app_registry_service().get_app_record(
        owner_user_id=owner_user_id, build_registry_id=build_registry_id,
    )).get("app") or {}
    run = registry_record.get("current_build_run") or {}
    if run.get("artifact_version_id") != version.id or run.get("build_id") != metadata.get("build_id"):
        raise HTTPException(
            status_code=409,
            detail=(
                "A newer build run started for this app after this change to app-owned capability code was "
                "made. Refine the current build again. No version was accepted."
            ),
        )


#: The most saved builds acceptance follows from a child back to the app's current build.
_ACCEPTANCE_LINEAGE_MAX_DEPTH = 32


def _commit_metadata_with_acceptance(version, *, accepted_by: str) -> dict[str, Any]:  # noqa: ANN001
    """The version's commit metadata with the acceptance evidence this acceptance writes.

    Acceptance is the authorized transition that makes a build current, so it
    writes the evidence (``contracts.build_acceptance_evidence``, transition
    ``studio_accept``): the version's app, build family, build key and record
    id, the authenticated principal and the time. The staged-refinement
    acceptance writes the same shape. Staling later overwrites the CURRENT
    status, but the evidence stays, so admission can tell a build that was
    accepted from a draft that never was.
    """
    commit_metadata = version.commit_metadata
    payload = (
        commit_metadata.model_dump(mode="python") if hasattr(commit_metadata, "model_dump")
        else dict(commit_metadata or {})
    )
    metadata = dict(payload.get("metadata") or {})
    metadata["acceptance"] = build_acceptance_evidence(
        version, accepted_by=accepted_by,
        accepted_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"), transition="studio_accept",
    )
    payload["metadata"] = metadata
    return payload


async def _require_current_build_in_lineage(version, *, app_id: str, artifact_store: Any) -> None:  # noqa: ANN001
    """Refuse an app bundle made from a build older than the app's latest accepted work.

    Acceptance replaces the whole bundle. Within the version's build family
    and key (the scope acceptance supersedes in), no build outside the
    version's lineage may be CURRENT, or have been accepted after the
    lineage's nearest accepted build (``coding_worker.newer_accepted_work``).
    An accepted build is marked stale once a refinement of it is stored, so
    acceptance evidence, not only the CURRENT status, shows it. Otherwise the
    version was made before a newer build was accepted, such as a sibling
    candidate, and accepting it would revert that build. A chained draft of
    the latest accepted build is still accepted. A missing ancestor, a lineage
    past the bound, a failed read, or an acceptance whose order cannot be
    established refuses too.

    A version with no parent (AppGenerator's first build, saved as a draft)
    is checked the same way: its lineage holds no accepted build, so it is
    accepted only while no build of its scope is CURRENT and none was
    accepted (or cannot be shown never accepted) before it.
    """
    if version.build_family != "app_bundle":
        return
    unverified = "This version's lineage could not be verified against the app's accepted builds"
    try:
        newer = await newer_accepted_work(
            artifact_store, app_id=app_id, record=version, max_depth=_ACCEPTANCE_LINEAGE_MAX_DEPTH,
        )
    except Exception as exc:
        logger.warning("acceptance_lineage_unverified app=%s version=%s", app_id, version.id, exc_info=True)
        raise HTTPException(status_code=409, detail=f"{unverified}. No version was accepted.") from exc
    if newer is None:
        return
    if newer.kind == "unverified":
        raise HTTPException(status_code=409, detail=f"{unverified} ({newer.reason}). No version was accepted.")
    latest = "current build" if newer.kind == "current" else "latest accepted build"
    made = (
        f"This version was made from an earlier build than the app's {latest}" if version.parent_build_record_id
        else f"This version is a new build with no parent, made outside the line of the app's {latest}"
    )
    raise HTTPException(
        status_code=409,
        detail=(
            f"{made} ({newer.reason}), and accepting it would replace changes accepted since. Refine that build "
            "instead. No version was accepted."
        ),
    )


class GenesisAcceptanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm_exact_source_review: Literal[True]
    reviewed_bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewed_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    disclosure_version: Literal["authored_app_root_disclosure_v1"]
    disclosure_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


@app.post("/api/studio/build/artifacts/{artifact_version_id}/accept-genesis")
async def accept_imported_genesis_artifact(
    artifact_version_id: str,
    body: GenesisAcceptanceRequest,
    app_id: str | None = None,
    build_registry_id: str | None = None,
    principal: UserPrincipal = Depends(require_studio_user),
):
    validate_path_id(artifact_version_id, "artifact_version_id")
    if not build_registry_id:
        raise HTTPException(status_code=400, detail="Factory build_registry_id is required")
    execution_app_id, _ = _resolve_studio_scope(principal, app_id=app_id)
    target_app_id, owner_user_id = await _resolve_studio_artifact_scope(
        principal, app_id=app_id, build_registry_id=build_registry_id,
    )
    registry = _get_app_registry_service()
    try:
        accepted = await accept_existing_app_genesis(
            owner_user_id=owner_user_id, execution_app_id=execution_app_id,
            build_registry_id=build_registry_id, build_record_id=artifact_version_id,
            reviewed_bundle_sha256=body.reviewed_bundle_sha256,
            reviewed_manifest_sha256=body.reviewed_manifest_sha256,
            disclosure_version=body.disclosure_version,
            disclosure_sha256=body.disclosure_sha256,
            registry_service=registry, record_store=get_artifact_store(),
        )
    except (GenesisImportError, ValueError, OSError, ContentNotFoundError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if accepted.app_id != target_app_id:
        raise HTTPException(status_code=409, detail="Accepted Genesis target changed")
    return {
        "accepted": True, "app_id": target_app_id,
        "artifact_version": accepted.model_dump(mode="python"),
        "genesis_import": (await registry.get_app_record(
            owner_user_id=owner_user_id, build_registry_id=build_registry_id,
        ))["app"]["genesis_import"],
    }


def _is_revalidatable_refinement_draft(version) -> bool:  # noqa: ANN001
    return (
        version.build_family == "app_bundle"
        and version.lifecycle_status == ArtifactLifecycleStatus.DRAFT
        and _version_metadata(version).get("bundle_mode") == "staged_refinement_bundle"
        and bool(version.parent_build_record_id)
        and _refinement_metadata_from_version(version) is None
    )


@app.post("/api/studio/build/artifacts/{artifact_version_id}/revalidate")
async def revalidate_refinement_draft(
    artifact_version_id: str,
    build_registry_id: BuildIdentity,
    principal: UserPrincipal = Depends(require_studio_user),
):
    from factory_app.workflows._shared.artifact_bundle import read_artifact_bundle
    from mozaiksai.core.validation.compilation_inputs import resolve_app_compilation_inputs
    from mozaiksai.core.validation.generated_app import validate_generated_app_candidate

    validate_path_id(artifact_version_id, "artifact_version_id")
    app_id, owner = await _resolve_studio_artifact_scope(
        principal, app_id=None, build_registry_id=build_registry_id,
    )
    store = get_artifact_store()
    draft = await store.get_build_record(app_id=app_id, build_record_id=artifact_version_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found")
    metadata = _version_metadata(draft)
    if (
        not _is_revalidatable_refinement_draft(draft)
        or metadata.get("build_registry_id") != build_registry_id
        or draft.commit_metadata.author_user_id != owner
    ):
        raise HTTPException(status_code=409, detail="This build is not eligible for draft revalidation")
    if draft.validation_status == ArtifactValidationStatus.PASSED:
        return {
            "validation_status": "passed",
            **await get_build_artifact_review(
                artifact_version_id, build_registry_id=build_registry_id, principal=principal,
            ),
        }
    await _require_current_build_in_lineage(draft, app_id=app_id, artifact_store=store)
    try:
        files, diagnostics = await read_artifact_bundle(draft, include_binary=True)
        compilation = await resolve_app_compilation_inputs(
            app_id=app_id, build_record_id=draft.id, owner_user_id=owner,
            build_registry_id=build_registry_id, artifact_store=store,
        )
    except (ValueError, OSError, ContentNotFoundError, zipfile.BadZipFile) as exc:
        raise HTTPException(status_code=409, detail="Draft archive identity or compilation inputs could not be verified") from exc
    if diagnostics:
        raise HTTPException(status_code=409, detail="Draft contains files that cannot be revalidated")
    result = await validate_generated_app_candidate(
        files=files, app_id=app_id, validation_strategy="docker",
        capability_packs=metadata.get("capability_packs", []), approved_compilation=compilation,
    )
    acceptance = result.get("app_bundle_acceptance_result")
    build = result.get("app_validation_result")
    if not isinstance(acceptance, dict) or not isinstance(build, dict):
        raise HTTPException(status_code=503, detail="Draft validation evidence is incomplete")
    if result["validation_status"] == "passed" and (
        acceptance.get("passed") is not True or build.get("validation_status") != "passed"
    ):
        raise HTTPException(status_code=503, detail="Draft validation evidence is inconsistent")
    async with _workspace_acceptance_guard(app_id, draft):
        await _verified_app_bundle(draft)
        await _require_current_build_in_lineage(draft, app_id=app_id, artifact_store=store)
        if not await store.record_draft_revalidation(record=draft, validation=result):
            raise HTTPException(status_code=409, detail="Draft changed during validation. Refresh the review.")
    return {
        "validation_status": result["validation_status"],
        **await get_build_artifact_review(
            artifact_version_id, build_registry_id=build_registry_id, principal=principal,
        ),
    }


@app.post("/api/studio/build/artifacts/{artifact_version_id}/accept")
async def accept_build_artifact_version(
    artifact_version_id: str,
    body: BuildArtifactAcceptanceRequest | None = None,
    app_id: str | None = None,
    build_registry_id: str | None = None,
    principal: UserPrincipal = Depends(require_studio_user),
):
    validate_path_id(artifact_version_id, "artifact_version_id")
    app_id, owner_user_id = await _resolve_studio_artifact_scope(principal, app_id=app_id, build_registry_id=build_registry_id)
    artifact_store = get_artifact_store()
    version = await artifact_store.get_build_record(app_id=app_id, build_record_id=artifact_version_id)
    if not version:
        raise HTTPException(status_code=404, detail=f"Artifact version not found: {artifact_version_id}")
    if _version_metadata(version).get("bundle_mode") == "brownfield_genesis_import":
        raise HTTPException(status_code=409, detail="Imported Genesis requires exact-source owner review through accept-genesis")
    if version.lifecycle_status == ArtifactLifecycleStatus.ARCHIVED:
        raise HTTPException(status_code=409, detail="Rejected artifact versions cannot be accepted.")
    if version.lifecycle_status != ArtifactLifecycleStatus.DRAFT:
        raise HTTPException(status_code=409, detail="Only draft artifact versions can be accepted.")
    _enforce_artifact_validation_gate(
        version,
        action="accepted",
    )
    verified_bundle_snapshots = await _verified_review_snapshots(
        app_id=app_id, version=version, artifact_store=artifact_store,
    )

    refinement_metadata = _refinement_metadata_from_version(version)
    refinement_review_record = _load_refinement_review_record_for_version(version)
    if refinement_metadata is not None and refinement_review_record is None:
        raise HTTPException(
            status_code=409,
            detail="Staged refinement drafts require a promotion_ready review record before acceptance.",
        )
    async with _workspace_acceptance_guard(app_id, version):
        current_version = version
        if _changed_workspace_pack_paths(version) or version.build_family == "app_bundle":
            # Re-read under the guard: another acceptance may have just completed.
            current_version = await artifact_store.get_build_record(app_id=app_id, build_record_id=artifact_version_id)
            if current_version is None or current_version.lifecycle_status != ArtifactLifecycleStatus.DRAFT:
                raise HTTPException(status_code=409, detail="Only draft artifact versions can be accepted.")
        if _changed_workspace_pack_paths(version):
            await _require_accepted_workspace_parent(
                current_version, app_id=app_id, owner_user_id=owner_user_id,
                build_registry_id=build_registry_id, artifact_store=artifact_store,
            )
        await _require_current_app_owned_pack_code(version, app_id=app_id, artifact_store=artifact_store)
        await _require_current_build_in_lineage(version, app_id=app_id, artifact_store=artifact_store)
        metadata = _version_metadata(current_version)
        if metadata.get("requires_current_review_assignment") is True:
            registry = (await _get_app_registry_service().get_app_record(
                build_registry_id=build_registry_id, owner_user_id=owner_user_id,
            )).get("app") or {}
            run = registry.get("current_build_run") or {}
            if (
                registry.get("app_id") != app_id or registry.get("lifecycle_state") != "review"
                or current_version.commit_metadata.author_user_id != owner_user_id
                or metadata.get("build_registry_id") != build_registry_id
                or run.get("build_id") != metadata.get("build_id")
                or run.get("artifact_version_id") != current_version.id
                or run.get("active_chat_id") != current_version.source_chat_id
            ):
                raise HTTPException(status_code=409, detail="Selected artifact is not the current build under review")
        if refinement_review_record is not None:
            try:
                accepted_result = await accept_staged_refinement_build_record(
                    app_id=app_id,
                    draft_build_record_id=artifact_version_id,
                    review_record=refinement_review_record,
                    request_id=refinement_review_record.request_id,
                    record_store=artifact_store,
                    accepted_by=principal.user_id,
                    notes=body.notes if body is not None else None,
                )
            except (AcceptedStagedAppBundleBuildRecordError, ValueError) as exc:
                logger.warning("accept_staged_refinement conflict app=%s version=%s: %s", app_id, artifact_version_id, exc)
                raise HTTPException(status_code=409, detail="Conflict accepting artifact version.") from exc
            accepted = accepted_result.build_record
        else:
            accepted = await artifact_store.accept_build_record(
                app_id=app_id, build_record_id=artifact_version_id,
                commit_metadata=_commit_metadata_with_acceptance(current_version, accepted_by=principal.user_id),
            )
    if accepted is None:
        raise HTTPException(status_code=404, detail=f"Artifact version not found: {artifact_version_id}")

    for session in await artifact_store.list_refinement_sessions(
        app_id=app_id,
        result_build_record_id=artifact_version_id,
        limit=20,
    ):
        await artifact_store.update_refinement_session(
            app_id=app_id,
            session_id=session.id,
            status=RefinementSessionStatus.ACCEPTED,
            ended_at=datetime.now(UTC),
        )

    payload = await _build_artifact_review_payload(
        app_id=app_id,
        version=accepted,
        artifact_store=artifact_store,
        verified_bundle_snapshots=verified_bundle_snapshots,
    )
    return {"accepted": True, "app_id": app_id, **payload}


@app.post("/api/studio/build/artifacts/{artifact_version_id}/reject")
async def reject_build_artifact_version(
    artifact_version_id: str,
    app_id: str | None = None,
    build_registry_id: str | None = None,
    principal: UserPrincipal = Depends(require_studio_user),
):
    validate_path_id(artifact_version_id, "artifact_version_id")
    app_id, _ = await _resolve_studio_artifact_scope(principal, app_id=app_id, build_registry_id=build_registry_id)
    artifact_store = get_artifact_store()
    version = await artifact_store.get_build_record(app_id=app_id, build_record_id=artifact_version_id)
    if not version:
        raise HTTPException(status_code=404, detail=f"Artifact version not found: {artifact_version_id}")
    if _version_metadata(version).get("bundle_mode") == "brownfield_genesis_import":
        raise HTTPException(status_code=409, detail="Imported Genesis cannot use generic artifact rejection")
    if version.lifecycle_status != ArtifactLifecycleStatus.DRAFT:
        raise HTTPException(status_code=409, detail="Only draft artifact versions can be rejected.")
    verified_bundle_snapshots = await _verified_review_snapshots(
        app_id=app_id, version=version, artifact_store=artifact_store,
    )

    rejected = await artifact_store.reject_artifact_version(
        app_id=app_id,
        artifact_version_id=artifact_version_id,
        reason="Rejected in build review.",
    )
    if not rejected:
        raise HTTPException(status_code=500, detail="Artifact version could not be rejected.")

    for session in await artifact_store.list_refinement_sessions(
        app_id=app_id,
        result_build_record_id=artifact_version_id,
        limit=20,
    ):
        await artifact_store.update_refinement_session(
            app_id=app_id,
            session_id=session.id,
            status=RefinementSessionStatus.REJECTED,
            ended_at=datetime.now(UTC),
        )

    refreshed = await artifact_store.get_build_record(app_id=app_id, build_record_id=artifact_version_id)
    payload = await _build_artifact_review_payload(
        app_id=app_id,
        version=refreshed,
        artifact_store=artifact_store,
        verified_bundle_snapshots=verified_bundle_snapshots,
    )
    return {"rejected": True, "app_id": app_id, **payload}


class BuildArtifactPromotionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


@app.post("/api/studio/build/artifacts/{artifact_version_id}/promote")
async def promote_build_artifact_version(
    artifact_version_id: str,
    background_tasks: BackgroundTasks,
    body: BuildArtifactPromotionRequest | None = None,
    app_id: str | None = None,
    build_registry_id: str | None = None,
    principal: UserPrincipal = Depends(require_studio_user),
):
    validate_path_id(artifact_version_id, "artifact_version_id")
    app_id, user_id = await _resolve_studio_artifact_scope(principal, app_id=app_id, build_registry_id=build_registry_id)
    artifact_store = get_artifact_store()
    version = await artifact_store.get_build_record(app_id=app_id, build_record_id=artifact_version_id)
    if not version:
        raise HTTPException(status_code=404, detail=f"Artifact version not found: {artifact_version_id}")
    if _version_metadata(version).get("bundle_mode") == "brownfield_genesis_import":
        raise HTTPException(status_code=409, detail="Accepted imported Genesis is a source baseline, not a deployment candidate")
    if version.lifecycle_status != ArtifactLifecycleStatus.CURRENT:
        raise HTTPException(status_code=409, detail="Only accepted current artifact versions can be promoted.")
    if version.build_family != "app_bundle":
        raise HTTPException(status_code=400, detail=f"Unsupported artifact kind for restore: {version.build_family}")
    _enforce_artifact_validation_gate(
        version,
        action="promoted",
    )

    metadata = _version_metadata(version)
    promotion_build_registry_id = build_registry_id
    if metadata.get("build_registry_id") != promotion_build_registry_id:
        raise HTTPException(status_code=409, detail="Artifact does not belong to the selected build")
    app_registry_result = None
    app_registry_service = None
    if promotion_build_registry_id:
        app_registry_service = _get_app_registry_service()
        registry_record = (
            await app_registry_service.get_app_record(build_registry_id=promotion_build_registry_id, owner_user_id=user_id)
        ).get("app")
        if not registry_record:
            raise HTTPException(
                status_code=404,
                detail=f"App registry record not found: {promotion_build_registry_id}",
            )
        if str(registry_record.get("app_id") or "").strip() != app_id:
            raise HTTPException(
                status_code=409,
                detail="App registry record does not match the artifact app id.",
            )
        if str(registry_record.get("lifecycle_state") or "").strip() != "review":
            raise HTTPException(
                status_code=409,
                detail="Only app registry records in review can be promoted.",
            )
        current_run = registry_record.get("current_build_run") or {}
        if (
            current_run.get("artifact_version_id") != artifact_version_id
            or current_run.get("build_id") != metadata.get("build_id")
        ):
            raise HTTPException(status_code=409, detail="Selected artifact is not the current build under review")
    refinement_metadata = _refinement_metadata_from_version(version)

    verified_bundle_snapshots = await _verified_review_snapshots(
        app_id=app_id, version=version, artifact_store=artifact_store,
    )
    bundle_bytes = verified_bundle_snapshots[version.id]
    target_dir = _resolve_bundle_restore_target(version)
    try:
        restore_summary = _restore_bundle_to_target(bundle_bytes=bundle_bytes, target_dir=target_dir, workspace_layout=True)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to promote artifact") from exc

    if promotion_build_registry_id and app_registry_service is not None:
        try:
            app_registry_result = await app_registry_service.promote_build(
                build_registry_id=promotion_build_registry_id,
                promoted_by=user_id,
                bundle_path=str(target_dir / "app"),
                expected_build_id=metadata["build_id"],
                expected_artifact_version_id=artifact_version_id,
            )
        except ValueError as exc:
            logger.warning("promote_build conflict app=%s build=%s: %s", app_id, promotion_build_registry_id, exc)
            raise HTTPException(status_code=409, detail="Conflict promoting build.") from exc
        if not app_registry_result["success"]:
            raise HTTPException(status_code=409, detail="Build changed during promotion. Refresh before retrying.")

    for session in await artifact_store.list_refinement_sessions(
        app_id=app_id,
        result_build_record_id=artifact_version_id,
        limit=20,
    ):
        await artifact_store.update_refinement_session(
            app_id=app_id,
            session_id=session.id,
            status=RefinementSessionStatus.PROMOTED,
            ended_at=datetime.now(UTC),
        )

    # Index the selected workspace, never the host that performed the promotion.
    app_intelligence_refresh: dict[str, Any] | None = None
    try:
        app_intelligence_refresh = await _start_studio_app_intelligence_index_job(
            app_id=app_id,
            user_id=user_id,
            body=AppIntelligenceIndexRequest(workspace_root=str(target_dir)),
            background_tasks=background_tasks,
        )
    except Exception as exc:
        logger.warning("POST_PROMOTE_APP_INTELLIGENCE_REFRESH_FAILED app=%s: %s", app_id, exc)
        app_intelligence_refresh = {"status": "failed", "error": str(exc)}

    payload = await _build_artifact_review_payload(
        app_id=app_id,
        version=version,
        artifact_store=artifact_store,
        verified_bundle_snapshots=verified_bundle_snapshots,
    )
    logger.info(
        "Promoted app_id=%s artifact_version_id=%s (%s/%s) restored=%s skipped=%s refinement_request_id=%s",
        app_id,
        artifact_version_id,
        version.build_family,
        version.build_key,
        len(restore_summary["restored"]),
        len(restore_summary["skipped"]),
        str((refinement_metadata or {}).get("request_id") or "").strip() or None,
    )
    return {
        "promoted": True,
        "app_id": app_id,
        "artifact_version_id": artifact_version_id,
        "build_family": version.build_family,
        "build_key": version.build_key,
        "target_path": str(target_dir),
        "restart_required": True,
        "restored_files": restore_summary["restored"],
        "skipped_files": restore_summary["skipped"],
        "app_registry": app_registry_result,
        "app_intelligence_refresh": app_intelligence_refresh,
        **payload,
    }


class BuildRestoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact_version_id: str = Field(..., description="Accepted artifact version to materialize in a separate workspace")


@app.post("/api/studio/build/restore")
async def restore_artifact_version(
    body: BuildRestoreRequest,
    app_id: str | None = None,
    build_registry_id: str | None = None,
    principal: UserPrincipal = Depends(require_studio_user),
):
    """Materialize a selected app version outside the running host workspace."""
    app_id, _ = await _resolve_studio_artifact_scope(principal, app_id=app_id, build_registry_id=build_registry_id)
    validate_path_id(body.artifact_version_id, "artifact_version_id")
    artifact_store = get_artifact_store()

    version = await artifact_store.get_build_record(
        app_id=app_id,
        build_record_id=body.artifact_version_id,
    )
    if not version:
        raise HTTPException(status_code=404, detail=f"Artifact version not found: {body.artifact_version_id}")

    if _version_metadata(version).get("build_registry_id") != build_registry_id:
        raise HTTPException(status_code=409, detail="Artifact does not belong to the selected build")
    if version.lifecycle_status not in {ArtifactLifecycleStatus.CURRENT, ArtifactLifecycleStatus.SUPERSEDED}:
        raise HTTPException(status_code=409, detail="Only previously accepted artifacts can be restored")
    _enforce_artifact_validation_gate(version, action="restored")

    bundle_bytes = await _verified_app_bundle(version)
    target_dir = _resolve_bundle_restore_target(version)

    try:
        _restore_bundle_to_target(bundle_bytes=bundle_bytes, target_dir=target_dir, workspace_layout=True)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to extract artifact") from exc

    logger.info(
        "Restored app_id=%s artifact_version_id=%s to a separate workspace (%s/%s)",
        app_id,
        body.artifact_version_id,
        version.build_family,
        version.build_key,
    )

    return {
        "restored": True,
        "artifact_version_id": body.artifact_version_id,
        "build_family": version.build_family,
        "build_key": version.build_key,
        "target_path": str(target_dir),
        "active_version_changed": False,
    }


@app.get("/api/studio/build")
async def get_build_surface(
    app_id: str | None = None,
    build_registry_id: str | None = None,
    principal: UserPrincipal = Depends(require_studio_user),
):
    if build_registry_id is not None:
        app_id, user_id = await _resolve_studio_artifact_scope(
            principal, build_registry_id=build_registry_id, app_id=app_id,
        )
    else:
        app_id, user_id = _resolve_studio_scope(principal, app_id=app_id)
    app_root = resolve_app_root()
    missing_surfaces = get_missing_studio_surfaces(app_root)
    if missing_surfaces:
        raise HTTPException(
            status_code=500,
            detail=f"Build surface is missing required surfaces: {', '.join(missing_surfaces)}",
        )

    try:
        record = (await _get_app_registry_service().get_app_record(app_id=app_id, owner_user_id=user_id)).get("app")
        if not isinstance(record, dict):
            raise HTTPException(status_code=404, detail=f"App record not found: {app_id}")
        build_state = await load_build_state_from_db(app_id)
        home_summary = build_app_overview_summary(
            app_root,
            surface="shell-build",
            local_only=True,
            app_record=record,
        )
        home_summary["studio"] = {**home_summary["studio"], "surface": "shell-build", "route": f"/apps/{app_id}/build"}
        return {
            **home_summary,
            "build": build_build_section(home_summary, build_state),
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to build build summary") from exc


class BuildSaveRequest(BaseModel):
    request_text: str = Field(..., description="Persisted build request text")
    request_kind: Literal["greenfield_app", "brownfield_app", "refinement"] | None = Field(
        None,
        description="High-level request kind for the current build draft",
    )
    change_class: Literal["patch", "design", "feature", "core"] | None = Field(
        None,
        description="Refinement change class when the current draft is a refinement request",
    )


@app.put("/api/studio/build")
async def save_build_surface(
    request: BuildSaveRequest,
    app_id: str | None = None,
    principal: UserPrincipal = Depends(require_studio_user),
):
    app_id, user_id = _resolve_studio_scope(principal, app_id=app_id)
    if request.change_class and request.request_kind != "refinement":
        raise HTTPException(status_code=400, detail="change_class is only valid when request_kind is 'refinement'")

    app_root = resolve_app_root()
    missing_surfaces = get_missing_studio_surfaces(app_root)
    if missing_surfaces:
        raise HTTPException(
            status_code=500,
            detail=f"Build surface is missing required surfaces: {', '.join(missing_surfaces)}",
        )

    try:
        record_result = await _get_app_registry_service().ensure_status_for_app(
            app_id=app_id,
            owner_user_id=user_id,
            status="draft",
            default_name=app_id,
        )
        record = record_result.get("app")
        build_state = await save_build_state_to_db(
            app_id,
            request_text=request.request_text,
            request_kind=request.request_kind,
            change_class=request.change_class,
        )
        home_summary = build_app_overview_summary(
            app_root,
            surface="shell-build",
            local_only=True,
            app_record=record if isinstance(record, dict) else None,
        )
        home_summary["studio"] = {**home_summary["studio"], "surface": "shell-build", "route": f"/apps/{app_id}/build"}
        return {
            **home_summary,
            "build": build_build_section(home_summary, build_state),
        }
    except HTTPException:
        raise
    except ValueError as exc:
        logger.warning("build state validation error app=%s: %s", app_id, exc)
        raise HTTPException(status_code=400, detail="Invalid build state parameters.") from exc
    except Exception as exc:
        logger.exception("Failed to persist build state")
        raise HTTPException(status_code=500, detail="Failed to persist build state") from exc


class WorkflowTriggerRequest(BaseModel):
    workflow_id: str | None = None
    trigger_source: str = "chat"
    journey_id: str | None = None
    context_variables: dict[str, Any] = Field(default_factory=dict)
    trigger_payload: dict[str, Any] = Field(default_factory=dict)
    action_id: str | None = None
    artifact_key: str | None = None
    app_id: str | None = None
    user_id: str | None = None
    build_registry_id: BuildIdentity | None = None
    source_chat_id: BuildIdentity | None = None
    retry_failed: StrictBool = False


@dataclass(slots=True, kw_only=True)
class _DownstreamRetry(TriggerRoutingContribution):
    binding: RunBuildBinding
    failed_chat_id: str
    parent_chat_id: str
    journey_scope: dict[str, Any]
    plan_source: RetryPlanSource | None = None


def _retry_chat_id(app_id: str, user_id: str, failed_chat_id: str, binding: RunBuildBinding) -> str:
    # One destination per failed attempt lets the same request finish a claim
    # whose process disappeared before it published the replacement session.
    scope = ["mozaiks/studio/stage-retry", app_id, user_id, failed_chat_id, binding.model_dump()]
    return str(uuid5(NAMESPACE_URL, json.dumps(scope, separators=(",", ":"), sort_keys=True)))


async def _select_retry_plan(
    sessions: Any, *, failed: dict[str, Any], scope: dict[str, Any], binding: RunBuildBinding,
) -> RetryPlanSource | None:
    """Inspect only the failed attempt and its direct, server-minted retry ancestry."""
    from factory_app.workflows.AppGenerator.tools.retry_plan import (
        PLAN_SOURCE_PROJECTION,
        admitted_source,
    )

    if binding.phase != "refinement" or not binding.refinement_model or not all(scope.values()):
        return None
    lookup = {key: value for key, value in scope.items() if not isinstance(value, dict)}
    child_created = None
    chat_id = failed["_id"]
    # A bounded _id lookup per native retry, never a scan for similar sessions.
    for _ in range(16):
        row = await sessions.find_one({"_id": chat_id, **lookup}, {
            **dict.fromkeys(scope, 1), **PLAN_SOURCE_PROJECTION, "trigger_meta": 1, "created_at": 1, "failed_at": 1, "retry_plan_source": 1,
        })
        if (not row or any(row.get(key) != value for key, value in scope.items())
                or any(row.get(key) for key in ("workflow_model", "workflow_model_rebind_pending", "workflow_run_model"))
                or not isinstance(row.get("failed_at"), datetime) or not isinstance(row.get("created_at"), datetime)
                or row["created_at"] > row["failed_at"] or (child_created is not None and row["failed_at"] > child_created)):
            return None
        # A reused plan originated in an earlier WAL; keep following native edges.
        if row.get("app_plan_ready") and not row.get("retry_plan_source"):
            return admitted_source(row)
        trigger = row.get("trigger_meta") or {}
        parent_id = trigger.get("retry_of_chat_id")
        if (not isinstance(parent_id, str) or trigger.get("trigger_source") != "manual"
                or _retry_chat_id(scope["app_id"], scope["user_id"], parent_id, binding) != chat_id):
            return None
        child_created, chat_id = row["created_at"], parent_id
    return None


def _registered_retry_cursor(
    record: dict[str, Any], binding: RunBuildBinding, workflow_id: str, journey_id: str | None,
) -> tuple[str, str] | None:
    """Use the active pointer only when both projections identify the same build stage."""
    current = record.get("current_build_run") or {}
    active, status = record.get("active_chat_id"), record.get("lifecycle_state")
    if (not isinstance(active, str) or not isinstance(status, str)
            or record.get("active_workflow_id") != workflow_id or current.get("active_workflow_id") != workflow_id
            or current.get("active_chat_id") != active or current.get("status") != status
            or current.get("phase") != binding.phase or current.get("build_id") != binding.build_id
            or current.get("workflow_sequence") != journey_id):
        return None
    return status, active


def _matches_failed_revision(snapshot: dict[str, Any], source: dict[str, Any], change: ChangeRequestDoc) -> bool:
    review = source.get("workflow_plan_review") or {}
    if not isinstance(review, dict) or source.get("workflow_review_outcome") == "cancelled" or review.get("status") == "cancelled":
        return False
    active = (snapshot.get("active_change_request_id"), snapshot.get("active_revision_id"))
    if active == (change.id, source["revision_id"]):
        return True
    # fail_active_revision closes a failed revision, including its two active
    # IDs. Re-open only the current, reviewed export that exhausted its repairs.
    # A cancelled plan and a superseding revision cannot use this evidence.
    history = snapshot.get("revision_history") or []
    if (active != (None, None) or snapshot.get("sequence_status") != "stale"
            or snapshot.get("sequence_completed_at") or snapshot.get("pending_harness_decision") or snapshot.get("pending_transition_id")
            or snapshot.get("current_chat_id") != source["_id"] or snapshot.get("current_workflow_id") != "AgentGenerator"
            or source.get("workflow_name") != "AgentGenerator" or not source.get("journey_parent_chat_id")
            or snapshot.get("journey_position") != source.get("journey_position") or not history):
        return False
    if (history[-1].get("change_request_id") != change.id or history[-1].get("revision_id") != source["revision_id"]
            or history[-1].get("target_workflow") != change.router_decision.get("workflow_id")
            or history[-1].get("scope") != change.classification.value
            or (history[-1].get("to_version_refs") or {}).get(change.build_family) != change.build_record_id
            or (snapshot.get("artifact_version_refs") or {}).get(change.build_family) != change.build_record_id):
        return False
    from factory_app.workflows.AgentGenerator.tools.pattern_selection import selection_fingerprint

    repair = source.get("workflow_bundle_repair_result") or {}
    selection = source.get("PatternSelection")
    return (
        isinstance(repair, dict) and repair.get("status") == "blocked" and repair.get("reason") == "workflow_bundle_repair_attempts_exhausted"
        and repair.get("repairable") is False and type(repair.get("attempt")) is int
        and repair["attempt"] > 0 and repair["attempt"] == repair.get("max_attempts")
        and source.get("workflow_bundle_export_outcome") == "blocked"
        and source.get("workflow_bundle_validation_status") == "failed" and bool(source.get("workflow_bundle_validation_errors"))
        and source.get("workflow_review_outcome") == "approved" and review.get("status") == "approved"
        and isinstance(selection, dict) and isinstance(selection.get("workflows"), list) and bool(selection["workflows"])
        and review.get("selection_hash") == selection_fingerprint(selection)
    )


async def _verify_reviewed_workflow_export(record: BuildRecord, parent: dict[str, Any], binding: RunBuildBinding) -> None:
    from factory_app.workflows._shared.workflow_integration import (
        read_verified_workflow_archive,
        workflow_integration_metadata_from_artifact,
        workflow_result_write_plans,
    )
    from factory_app.workflows.AgentGenerator.tools.pattern_selection import selection_fingerprint

    metadata = record.commit_metadata.metadata
    review, selection = metadata.get("workflow_plan_review"), parent.get("PatternSelection")
    if (record.build_family != "workflow_bundle" or record.id != parent.get("artifact_version_id")
            or any(metadata.get(k) != v for k, v in binding.model_dump().items())
            or parent.get("workflow_bundle_export_outcome") != "ready" or parent.get("workflow_bundle_validation_status") != "passed"
            or parent.get("workflow_review_outcome") != "approved"
            or not isinstance(review, dict) or review.get("status") != "approved" or review != parent.get("workflow_plan_review")
            or not isinstance(selection, dict) or not isinstance(selection.get("workflows"), list)
            or not all(isinstance(w, dict) and isinstance(w.get("name"), str) for w in selection["workflows"])
            or review.get("selection_hash") != selection_fingerprint(selection)):
        raise ValueError("The predecessor has no matching reviewed workflow export")
    await to_thread(read_verified_workflow_archive, record)
    catalog = workflow_integration_metadata_from_artifact(record)
    if not catalog:
        raise ValueError("The reviewed workflow export has no integration contract")
    expected = {w["name"]: workflow_result_write_plans(w) for w in selection["workflows"]}
    actual = {w["workflow_name"]: workflow_result_write_plans(w) for w in catalog["workflows"]}
    if (actual != expected or len(actual) != len(catalog["workflows"])
            or len(expected) != len(selection["workflows"])):
        raise ValueError("The workflow export differs from the reviewed result plan")


async def _verified_retry_predecessor(
    *, source: dict[str, Any], change: ChangeRequestDoc, binding: RunBuildBinding,
    app_id: str, user_id: str, workflow_id: str, sessions: Any,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
    """Resolve a completed, owner-scoped prefix before admitting a fresh stage."""
    from mozaiksai.core.multitenant import build_app_scope_filter
    from mozaiksai.core.workflow.pack.config import get_workflow_sequence, load_global_pack_graph

    journey_id = change.impact_set.workflow_sequence
    registry = await _get_app_registry_service().get_app_record(build_registry_id=binding.build_registry_id, owner_user_id=user_id)
    record = registry.get("app") or {}
    if (_registered_retry_cursor(record, binding, workflow_id, journey_id) not in {
            ("needs_revision", source["_id"]), ("building", _retry_chat_id(app_id, user_id, source["_id"], binding))}
            or binding.refinement_model != change.refinement_request.refinement_model):
        raise ValueError("The failed stage no longer matches the active registered refinement")

    snapshot = await get_session_router().for_target(binding.target_app_id).get_session_snapshot(app_id=app_id, user_id=user_id) or {}
    instance = TypeAdapter(BuildIdentity).validate_python(source.get("journey_instance_id"))
    if (snapshot.get("journey_instance_id") != instance or snapshot.get("journey_key") != journey_id
            or not _matches_failed_revision(snapshot, source, change)):
        raise ValueError("The failed stage does not belong to the active refinement journey")

    pack = load_global_pack_graph()
    journey = get_workflow_sequence(pack, journey_id) if pack is not None else None
    position = source.get("journey_position")
    if (journey is None or type(position) is not int or not 0 < position < len(journey.steps)
            or any(len(step.workflows) != 1 for step in journey.steps[:position + 1])
            or sum(workflow_id in step.workflows for step in journey.steps) != 1
            or journey.steps[position].workflows != [workflow_id]
            or journey.steps[0].workflows != [change.router_decision.get("workflow_id")]
            or change.router_decision.get("workflow_sequence") != journey_id
            or change.router_decision.get("execution_mode") != "workflow"):
        raise ValueError("The failed stage is not a uniquely authorized step of the configured refinement")

    workflow_handoff = (workflow_id == "AppGenerator" and journey.steps[position - 1].workflows == ["AgentGenerator"]
                        and change.build_family == "app_bundle")
    stage_artifact = change.build_record_id
    if workflow_handoff:
        request = source.get("refinement_request_meta") or {}
        stage_artifact = TypeAdapter(BuildIdentity).validate_python(source.get("workflow_bundle_artifact_version_id"))
        if (not source.get("journey_parent_chat_id") or source.get("revision_source_artifact_version_id") != change.build_record_id
                or not isinstance(request, dict) or request.get("build_record_id") != change.build_record_id
                or request.get("build_family") != change.build_family):
            raise ValueError("Saved app stage does not identify its original app and upstream workflow")
    scope = {"run_build_binding": binding.model_dump(), "session_router_session_id": snapshot["session_id"],
             "journey_instance_id": instance, "journey_key": journey_id, "journey_total_steps": len(journey.steps),
             "revision_id": source["revision_id"], "change_request_id": change.id}
    if source.get("artifact_version_id") != stage_artifact or any(source.get(key) != value for key, value in scope.items()):
        raise ValueError("Saved refinement stage scope does not match its authorized source")
    owned_build = {"user_id": user_id, **build_app_scope_filter(app_id), "run_build_binding": binding.model_dump()}
    child = source
    immediate_parent = None
    workflow_record = None
    subscription_record = None
    upstream_records: dict[str, str] = {}
    for index in reversed(range(position)):
        name = journey.steps[index].workflows[0]
        is_workflow_parent = workflow_handoff and index == position - 1
        expected_artifact = stage_artifact if is_workflow_parent else change.build_record_id
        query = {**owned_build, "workflow_name": name, "status": 1}
        if "journey_parent_chat_id" in child:
            parent = await sessions.find_one({**query, "_id": child["journey_parent_chat_id"]})
        else:
            # Check uniqueness inside the protected build binding before using
            # the historical session's mutable journey and revision annotations.
            parents = await sessions.find(query).limit(2).to_list(length=2)
            parent = parents[0] if len(parents) == 1 else None
        if (not parent or parent.get("journey_position") != index or parent.get("artifact_version_id") != expected_artifact
                or any(parent.get(k) != v for k, v in scope.items())):
            raise ValueError("Missing or ambiguous completed predecessor for the failed stage")
        if (not isinstance(parent.get("completed_at"), datetime) or not isinstance(child.get("created_at"), datetime)
                or parent["completed_at"] > child["created_at"]):
            raise ValueError("The completed predecessor does not precede this stage")
        meta = child.get("trigger_meta") or {}
        if meta.get("trigger_source") == "manual" and meta.get("retry_of_chat_id"):
            prior = await sessions.find_one({**owned_build, **scope, "artifact_version_id": stage_artifact if is_workflow_parent else change.build_record_id,
                                             "_id": meta["retry_of_chat_id"], "workflow_name": child["workflow_name"],
                                             "journey_position": index + 1, "status": 2}, {"_id": 1})
            if not prior:
                raise ValueError("Missing failed source for the saved stage retry")
        elif meta != {"trigger_source": "run_complete"}:
            raise ValueError("Workflow stage has no verified journey launch")
        if "journey_parent_chat_id" not in child or is_workflow_parent or name in {"DesignDocs", "SubscriptionContractDesigner"}:
            # Older handoffs did not store a parent edge. A matching position alone
            # cannot authorize a retry. Require independent output from this very chat.
            outputs = await get_artifact_store().list_build_records(
                app_id=binding.target_app_id, source_chat_id=parent["_id"], limit=2,
            )
            if len(outputs) != 1:
                raise ValueError("Missing or ambiguous saved output from the completed predecessor")
            output = outputs[0]
            commit = output.commit_metadata
            if (not isinstance(parent.get("created_at"), datetime)
                    or not parent["created_at"] <= output.created_at <= parent["completed_at"]
                    or output.app_id != binding.target_app_id or output.source_chat_id != parent["_id"]
                    or output.source_workflow != name or commit.author_user_id != user_id
                    or commit.source_chat_id != parent["_id"] or commit.source_workflow != name
                    or output.lifecycle_status in {ArtifactLifecycleStatus.ARCHIVED, ArtifactLifecycleStatus.DELETED}
                    or output.validation_status == ArtifactValidationStatus.FAILED):
                raise ValueError("Saved output is not an eligible result of the completed predecessor")
            if name == "DesignDocs":
                from factory_app.workflows.DesignDocs.tools.save_design_doc import (
                    verify_design_projections,
                )

                if output.build_family != "design_docs":
                    raise ValueError("The completed design stage has no verified design output")
                await verify_design_projections(output, user_id=user_id, build_id=binding.build_id)
                upstream_records["design_docs"] = output.id
            elif name == "SubscriptionContractDesigner":
                from mozaiksai.core.artifacts.summary_artifacts import verified_summary_payload

                if output.build_family != "subscription_contract" or parent.get("subscription_contract") != verified_summary_payload(output):
                    raise ValueError("Completed predecessor's launch input differs from its saved output")
                subscription_record = output
                upstream_records["subscription_contract"] = output.id
            elif is_workflow_parent:
                await _verify_reviewed_workflow_export(output, parent, binding)
                workflow_record = output
        if immediate_parent is None:
            immediate_parent = parent
        child = parent

    root_meta = child.get("trigger_meta") or {}
    if (root_meta.get("trigger_source") not in {"manual", "refinement"} or root_meta.get("rerouted_by_dependency") is not False
            or root_meta.get("resolved_workflow_id") != child["workflow_name"]
            or root_meta.get("journey_id") != journey_id or root_meta.get("workflow_sequence") != journey_id
            or root_meta.get("build_family") != change.build_family):
        raise ValueError("The completed journey root does not prove the saved refinement launch")
    assert immediate_parent is not None
    if subscription_record is not None and "design_docs" in upstream_records:
        from factory_app.workflows._shared.export_inputs import verify_subscription_design_input

        verify_subscription_design_input(subscription_record, upstream_records["design_docs"])
    if workflow_record is not None and workflow_record.canonical_inputs_version != upstream_records:
        raise ValueError("The workflow export references different upstream artifacts than its verified journey")
    projected_inputs = {f"{family}_artifact_version_id": record_id for family, record_id in upstream_records.items()}
    return root_meta, immediate_parent, projected_inputs


async def _failed_workflow_retry_contribution(
    body: WorkflowTriggerRequest, *, app_id: str, user_id: str,
) -> TriggerRoutingContribution:
    from mozaiksai.core.multitenant import build_app_scope_filter
    from mozaiksai.core.session.launcher import _PERSISTENCE_MANAGER, validate_context_for_workflow

    if body.trigger_source != "manual" or not body.source_chat_id or not body.workflow_id:
        raise ValueError("Failed-workflow retry requires a manual trigger and source workflow session")
    if (body.context_variables or body.trigger_payload or body.journey_id is not None
            or body.action_id is not None or body.artifact_key is not None):
        raise ValueError("Failed-workflow retry cannot override saved launch intent")
    sessions = await _PERSISTENCE_MANAGER._coll()
    source = await sessions.find_one(
        {"_id": body.source_chat_id, "user_id": user_id, "workflow_name": body.workflow_id,
         **build_app_scope_filter(app_id)},
        {"status": 1, "run_build_binding": 1, "trigger_meta": 1, "change_request_id": 1,
         "revision_id": 1, "coding_participation": 1, "workflow_name": 1, "created_at": 1,
         "session_router_session_id": 1, "journey_position": 1, "journey_key": 1,
         "journey_instance_id": 1, "journey_total_steps": 1, "journey_parent_chat_id": 1, "artifact_version_id": 1,
         "refinement_request_meta": 1, "revision_source_artifact_version_id": 1, "workflow_bundle_artifact_version_id": 1,
         "workflow_bundle_repair_result": 1, "workflow_bundle_export_outcome": 1, "workflow_bundle_validation_status": 1,
         "workflow_bundle_validation_errors": 1, "workflow_review_outcome": 1, "workflow_plan_review": 1, "PatternSelection": 1},
    )
    if not source or source.get("status") != 2:
        raise ValueError("Source workflow session is not an owned failed run")
    binding = await _get_app_registry_service().resolve_build_binding(
        owner_user_id=user_id, app_id=app_id, chat_id=body.source_chat_id, workflow_name=body.workflow_id,
        build_registry_id=body.build_registry_id, source_chat_id=body.source_chat_id,
        persisted_binding=source.get("run_build_binding"),
    )
    trigger_meta = source.get("trigger_meta") or {}
    if binding.phase == "genesis":
        seed = {}
        if source.get("coding_participation") is not None:
            seed["coding_participation"] = TypeAdapter(Literal["guided", "autonomous"]).validate_python(
                source["coding_participation"],
            )
        return TriggerRoutingContribution(
            workflow_id=body.workflow_id, journey_id=trigger_meta.get("journey_id"),
            context_seed=validate_context_for_workflow(body.workflow_id, seed),
            explanation="Retry failed workflow", require_exact_route=True,
        )

    change_id = TypeAdapter(BuildIdentity).validate_python(source.get("change_request_id"))
    revision_id = TypeAdapter(BuildIdentity).validate_python(source.get("revision_id"))
    store = get_artifact_store()
    change = await store.get_change_request(app_id=binding.target_app_id, change_request_id=change_id)
    if (change is None or change.id != change_id or change.app_id != binding.target_app_id
            or change.created_by_user_id != user_id):
        raise ValueError("Saved refinement request is not available to this owner")
    request = RefinementRequest.model_validate(change.refinement_request.model_dump(mode="python"))
    predecessor = None
    verified_inputs: dict[str, str] = {}
    if (trigger_meta.get("trigger_source") == "run_complete" or trigger_meta.get("retry_of_chat_id")
            or body.workflow_id != change.router_decision.get("workflow_id")):
        trigger_meta, predecessor, verified_inputs = await _verified_retry_predecessor(
            source=source, change=change, binding=binding, app_id=app_id, user_id=user_id,
            workflow_id=body.workflow_id, sessions=sessions,
        )
    journey_id = trigger_meta.get("workflow_sequence") or trigger_meta.get("journey_id")
    if not journey_id or journey_id != change.impact_set.workflow_sequence:
        raise ValueError("Saved refinement journey does not match the failed run")
    if (change.router_decision.get("is_full_restart")
            or journey_id in {"full_rebuild", "conceptual_replan"} or request.request_kind != "refinement"):
        raise ValueError("Failed-workflow retry does not support saved rebuild or restart intent")
    baseline_id = TypeAdapter(BuildIdentity).validate_python(request.build_record_id)
    if (
        not request.raw_user_request.strip() or request.raw_user_request != change.raw_user_request
        or request.app_id not in {None, app_id} or request.user_id not in {None, user_id}
        or request.target_app_id not in {None, binding.target_app_id}
        or request.build_record_id != change.build_record_id
        or request.build_record_id != trigger_meta.get("build_record_id")
        or request.build_family != change.build_family or request.normalized_build_key() != change.build_key
        or change.classification != change.change_intent.change_class
        or change.classification != trigger_meta.get("change_class")
    ):
        raise ValueError("Saved refinement request does not match the failed run")
    baseline = await store.get_build_record(app_id=binding.target_app_id, build_record_id=baseline_id)
    if (
        baseline is None or baseline.id != request.build_record_id or baseline.app_id != binding.target_app_id
        or baseline.build_family != request.build_family or baseline.build_key != request.normalized_build_key()
        or baseline.lifecycle_status in {ArtifactLifecycleStatus.ARCHIVED, ArtifactLifecycleStatus.DELETED}
        or baseline.commit_metadata.author_user_id != user_id
    ):
        raise ValueError("Selected retry baseline is unavailable or retired")
    metadata = _version_metadata(baseline)
    if metadata.get("build_registry_id") != binding.build_registry_id or metadata.get("target_app_id") != binding.target_app_id:
        raise ValueError("Selected retry baseline does not belong to the registered target")
    try:
        await require_accepted_genesis_baseline(
            baseline, owner_user_id=user_id, execution_app_id=app_id,
            build_registry_id=binding.build_registry_id,
            registry_service=_get_app_registry_service(),
        )
    except (GenesisImportError, ValueError, OSError, ContentNotFoundError) as exc:
        raise ValueError("Selected retry Genesis baseline has no accepted owner review") from exc

    # Reuse the accepted request and routing facts, never the failed run's
    # generated output, validation, counters, or mutable workspace copy.
    extra = {"change_request_id": change_id, "revision_id": revision_id,
             "files_manifest": [entry.model_dump(mode="python") for entry in baseline.files_manifest]}
    if metadata.get("workspace_dir"):
        extra.update(lifecycle_state="review", bundle_path=metadata["workspace_dir"])
    request = request.model_copy(update={"app_id": app_id, "user_id": user_id, "target_app_id": binding.target_app_id, "extra": extra})
    seed = {
        "build_mode": "revision", "revision_scope": change.classification.value,
        "artifact_kind": request.build_family, "artifact_version_id": baseline.id,
        "refinement_request": request.raw_user_request, "refinement_request_meta": request.model_dump(mode="python"),
        "change_intent": change.change_intent.model_dump(mode="python"), "impact_set": change.impact_set.model_dump(mode="python"),
        "change_request_id": change_id, "revision_id": revision_id,
        "revision_origin_workflow": request.requested_workflow_id or body.workflow_id,
        "workflow_sequence": journey_id, "screen": request.source_surface,
    }
    if change.change_intent.branding_intent in {"visual_redesign", "new_logo"}:
        seed["parent_theme_config"] = await _saved_parent_theme(baseline)
    if extra.get("lifecycle_state"):
        seed["lifecycle_state"] = extra["lifecycle_state"]
    contribution = TriggerRoutingContribution(
        workflow_id=body.workflow_id, journey_id=journey_id, require_exact_route=True,
        explanation="Retry failed workflow with its saved refinement request and selected baseline",
    )
    if predecessor is not None:
        from mozaiksai.core.workflow.pack.journey_orchestrator import _project_launch_context

        seed = {**_project_launch_context({**predecessor, **verified_inputs}, body.workflow_id), **seed,
                "artifact_version_id": source["artifact_version_id"]}
        journey_scope = {key: source[key] for key in (
            "journey_instance_id", "journey_key", "journey_position", "journey_total_steps", "session_router_session_id",
        )}
        plan_source = None
        if (body.workflow_id == "AppGenerator" and verified_inputs.get("design_docs_artifact_version_id")
                and verified_inputs.get("subscription_contract_artifact_version_id")):
            scope = {key: seed[key] for key in (
                "refinement_request", "change_intent", "impact_set", "change_request_id", "revision_id", "artifact_version_id",
            )}
            plan_source = await _select_retry_plan(
                sessions, failed=source, binding=binding, scope={
                    **scope, **journey_scope, **verified_inputs, "revision_source_artifact_version_id": baseline.id,
                    "workflow_bundle_artifact_version_id": source["artifact_version_id"], "workflow_name": body.workflow_id,
                    "app_id": app_id, "user_id": user_id, "status": 2, "journey_parent_chat_id": predecessor["_id"],
                    "run_build_binding": binding.model_dump(),
                },
            )
        contribution = _DownstreamRetry(
            workflow_id=body.workflow_id, journey_id=journey_id, require_exact_route=True,
            explanation="Retry the verified downstream stage with its completed predecessor's launch inputs",
            binding=binding, failed_chat_id=source["_id"], parent_chat_id=predecessor["_id"], plan_source=plan_source,
            journey_scope=journey_scope,
        )
    contribution.context_seed = validate_context_for_workflow(body.workflow_id, seed)
    return contribution


async def _launch_downstream_retry(retry: _DownstreamRetry, *, app_id: str, user_id: str) -> WorkflowLaunchResult:
    from mozaiksai.core.runtime.persistence.distributed_lock import (
        ChatLeaseLostError,
        ChatLockAuthorityUnavailableError,
        LockAcquisitionError,
        chat_execution_lease,
    )

    chat_id = _retry_chat_id(app_id, user_id, retry.failed_chat_id, retry.binding)
    try:
        async with chat_execution_lease(app_id=app_id, chat_id=chat_id):
            return await _publish_downstream_retry(retry, app_id=app_id, user_id=user_id, chat_id=chat_id)
    except LockAcquisitionError as exc:
        raise HTTPException(409, "The replacement workflow chat is busy") from exc
    except (ChatLeaseLostError, ChatLockAuthorityUnavailableError) as exc:
        raise HTTPException(503, "The retry publication lease is unavailable") from exc


async def _publish_downstream_retry(retry: _DownstreamRetry, *, app_id: str, user_id: str, chat_id: str) -> WorkflowLaunchResult:
    from mozaiksai.core.runtime.persistence.distributed_lock import assert_chat_mutable
    from mozaiksai.core.session.launcher import _PERSISTENCE_MANAGER
    from mozaiksai.core.session.persistence import SessionStateStore

    assert retry.workflow_id is not None
    registry = _get_app_registry_service()
    selector = {"build_registry_id": retry.binding.build_registry_id, "owner_user_id": user_id,
                "expected_build_id": retry.binding.build_id, "active_workflow_id": retry.workflow_id}
    sessions = await _PERSISTENCE_MANAGER._coll()
    expected = {"user_id": user_id, "app_id": app_id, "workflow_name": retry.workflow_id, "status": 0,
                "journey_parent_chat_id": retry.parent_chat_id,
                **{key: retry.context_seed[key] for key in ("artifact_version_id", "change_request_id", "revision_id")}}
    extra_run_authority = ("workflow_model", "workflow_model_rebind_pending", "workflow_run_model", "workflow_result_grant")
    projection = dict.fromkeys((*expected, *retry.journey_scope, "retry_plan_source", "trigger_meta", "run_build_binding", *extra_run_authority), 1)
    existing = await sessions.find_one({"_id": chat_id}, projection)
    # A session published before plan reuse was available retains its original startup behavior.
    plan_source = retry.plan_source if existing is None or "retry_plan_source" in existing else None
    if existing is not None and (
        existing.get("retry_plan_source") != (plan_source.model_dump() if plan_source else None)
        or any(existing.get(key) != value for key, value in expected.items())
        or existing.get("run_build_binding") not in (None, retry.binding.model_dump())
        or any(existing.get(key) is not None for key in extra_run_authority)
        or (existing.get("trigger_meta") or {}).get("retry_of_chat_id") != retry.failed_chat_id
        or any(key in existing and existing[key] != value for key, value in retry.journey_scope.items())
    ):
        raise HTTPException(409, "The replacement chat does not match this stage retry")
    with CancelScope(shield=True):
        assert_chat_mutable(app_id=app_id, chat_id=chat_id)
        claim = await registry.update_build_status(
            **selector, expected_active_chat_id=retry.failed_chat_id, expected_lifecycle_state="needs_revision",
            active_chat_id=chat_id, status="building",
        )
        if not claim["success"]:
            record = (await registry.get_app_record(build_registry_id=retry.binding.build_registry_id, owner_user_id=user_id)).get("app") or {}
            if _registered_retry_cursor(record, retry.binding, retry.workflow_id, retry.journey_id) != ("building", chat_id):
                raise HTTPException(409, "Another session already replaced this failed stage")
        assert_chat_mutable(app_id=app_id, chat_id=chat_id)
        if existing is not None and existing.get("run_build_binding") and all(key in existing for key in retry.journey_scope):
            # Journey annotations are the last write in bind_workflow_session. A
            # replay after an acknowledged bind must not clear an active review.
            decision = RoutingDecision(workflow_id=retry.workflow_id, requested_workflow_id=retry.workflow_id,
                                       journey_id=retry.journey_id, explanation=retry.explanation)
            return WorkflowLaunchResult(
                chat_id=chat_id, app_id=app_id, user_id=user_id, workflow_id=retry.workflow_id,
                requested_workflow_id=retry.workflow_id, journey_id=retry.journey_id,
                websocket_url=f"/ws/{retry.workflow_id}/{app_id}/{chat_id}/{user_id}", trigger_source="manual",
                routing_explanation=retry.explanation, rerouted_by_dependency=False, validated_context=dict(retry.context_seed),
                trigger_meta=existing["trigger_meta"], routing_decision=decision,
            )
        try:
            launch = await prepare_routed_workflow_launch(
                app_id=app_id, user_id=user_id, chat_id=chat_id, workflow_id=retry.workflow_id, trigger_source="manual",
                build_registry_id=retry.binding.build_registry_id, source_chat_id=retry.failed_chat_id,
                session_router=get_session_router(), routing_contribution=retry,
                extra_trigger_meta={"retry_of_chat_id": retry.failed_chat_id, "workflow_sequence": retry.journey_id,
                                    "change_class": retry.context_seed["revision_scope"], "build_family": retry.context_seed["artifact_kind"],
                                    "build_record_id": retry.context_seed["refinement_request_meta"]["build_record_id"]},
            )
            assert_chat_mutable(app_id=app_id, chat_id=chat_id)
            return await launch_prepared_workflow(
                launch, journey_parent_chat_id=retry.parent_chat_id, retry_plan_source=plan_source,
            )
        except Exception:
            assert_chat_mutable(app_id=app_id, chat_id=chat_id)
            # An inserted child carries the protected parent. Leave its claim
            # intact so another submission can finish the binding in place.
            if await sessions.find_one({"_id": chat_id}, {"_id": 1}) is not None:
                raise
            restored = await registry.update_build_status(
                **selector, expected_active_chat_id=chat_id, expected_lifecycle_state="building",
                active_chat_id=retry.failed_chat_id, status="needs_revision",
            )
            if restored["success"]:
                await SessionStateStore(_PERSISTENCE_MANAGER).restore_failed_stage_cursor(
                    app_id=app_id, user_id=user_id, target_app_id=retry.binding.target_app_id,
                    change_id=retry.context_seed["change_request_id"], revision_id=retry.context_seed["revision_id"],
                    workflow_id=retry.workflow_id, replacement_chat_id=chat_id, failed_chat_id=retry.failed_chat_id,
                )
            raise


async def _complete_inline_refinement(*, binding: RunBuildBinding, user_id: str, result: Any) -> None:
    artifact_id = (result.metadata or {}).get("build_record_id")
    if result.status == "validated" and not artifact_id:
        raise ValueError("Inline refinement has no saved output to review")
    version = None
    if artifact_id:
        version = await get_artifact_store().get_build_record(
            app_id=binding.target_app_id, build_record_id=artifact_id,
        )
        if version is None or any(
            _version_metadata(version).get(key) != value for key, value in binding.model_dump().items()
        ):
            raise ValueError("Inline refinement output does not belong to its registered run")
        if result.status == "validated" and (
            version.validation_status != ArtifactValidationStatus.PASSED
            or version.app_validation_status != "passed"
        ):
            raise ValueError("Inline refinement output has not passed validation")
    result_status = "review" if version is not None and result.status == "validated" else "needs_revision"
    saved = await _get_app_registry_service().update_build_status(
        owner_user_id=user_id, build_registry_id=binding.build_registry_id,
        expected_build_id=binding.build_id, status=result_status,
        artifact_version_id=version.id if version else None,
        bundle_path=_version_metadata(version).get("workspace_dir") if version else None,
    )
    if not saved["success"]:
        raise ValueError("Inline refinement was superseded before its output could be registered")


async def _fail_inline_refinement(*, binding: RunBuildBinding, user_id: str) -> None:
    try:
        with CancelScope(shield=True):
            await _get_app_registry_service().update_build_status(
                owner_user_id=user_id, build_registry_id=binding.build_registry_id,
                expected_build_id=binding.build_id, status="needs_revision",
            )
    except Exception:
        logger.exception("Could not record inline refinement failure for build=%s", binding.build_id)


class _InlineRunEffect(NamedTuple):
    """What an inline run left of the build it was made from, as the artifact store shows it."""

    #: The run changed the build or left its outcome unresolved, so the build is invalidated.
    changed: bool
    #: Why the outcome is unresolved (a key of ``_UNRESOLVED_CHANGE_DETAILS``), or None.
    unresolved: str | None = None
    #: The draft the run stored or named, when there is one.
    draft_build_record_id: str | None = None


#: What an open draft that cannot be accepted still blocks, and the next step.
#: The run marked its build stale, and nothing makes a stale build current
#: again, rejection included. Ordinary refinements of that build go on, and a
#: validated one can be accepted; only an app-owned pack-code change from it
#: needs every draft made from it closed (coding admission's stale-parent
#: rule), so rejecting this draft is the next step for that change. A
#: refinement of the draft marks it stale, after which it can be neither
#: rejected nor accepted; admission then counts it as closed once nothing made
#: from it is open, unless a change request made from it took a workflow
#: route, whose outcome is never on record: then only a newly accepted build
#: lets that code change again.
_OPEN_DRAFT_NEXT_STEP = (
    "The build it was made from is now stale and stays stale, even after this draft is rejected; it can "
    "still be refined, and a validated refinement of it accepted. Only changing that build's app-owned "
    "capability code (such as a pack's workspace handler) needs this draft closed first: reject the draft. "
    "If the draft is refined instead, it can no longer be rejected; it stops blocking that change once "
    "nothing made from it is open, unless a refinement of it took a workflow route (even one whose launch "
    "failed), which keeps it open: then accept a new build of the app before changing its app-owned "
    "capability code."
)
#: What the trigger tells the user when an inline run's outcome is unresolved.
_UNRESOLVED_CHANGE_DETAILS = {
    "validation_pending": (
        f"Validation of this draft did not complete, so it cannot be accepted. {_OPEN_DRAFT_NEXT_STEP}"
    ),
    "validation_skipped": (
        f"Validation of this draft was skipped, so it cannot be accepted. {_OPEN_DRAFT_NEXT_STEP}"
    ),
    "child_lookup_failed": (
        "Whether this run stored a draft could not be read, so the build it was made from is invalidated "
        "and the run's outcome is unresolved. Review the app's saved builds."
    ),
    "reported_child_missing": (
        "This run named a draft the artifact store did not return, so the build it was made from is "
        "invalidated and the run's outcome is unresolved. Review the app's saved builds."
    ),
}


async def _inline_run_baseline_effect(
    artifact_store: Any, *, app_id: str, parent_build_record_id: str | None, binding: RunBuildBinding, result: Any,
) -> _InlineRunEffect:
    """What an inline run left of the build it was made from, read from the store.

    An inline coding or surface run's only durable output is a DRAFT child of
    the baseline whose commit metadata carries the run's build_id, and the run
    can store one even when its result reports a failure. The store, not the
    result's status, is the evidence. Records of another run or another parent
    are not this run's output.

    * A child whose validation PASSED and that is not rejected awaits review:
      a change.
    * A child whose validation is PENDING or SKIPPED is unresolved, not
      "nothing changed". Acceptance admits only PASSED
      (``_enforce_artifact_validation_gate``) and nothing validates a stored
      child again, so it can never be accepted, but until it is rejected it is
      the run's open output. It counts as a change, and the trigger reports it
      as unresolved (``_OPEN_DRAFT_NEXT_STEP``): the build stays stale, its
      ordinary refinements go on, and before an app-owned pack-code change
      from it the draft must be rejected, after which coding admission can
      admit that change as a retry bound to the same build.
    * A FAILED child is terminal and durable (refused at acceptance, never
      validated again), and a rejected one is closed: no change.
    * A lookup that fails, or a child the result names that the store does
      not return, is unresolved: a change.
    """
    reported = str(((getattr(result, "metadata", None) or {}).get("build_record_id")) or "").strip()
    parent_id = parent_build_record_id or ""
    try:
        listed = await artifact_store.list_run_child_build_records(
            app_id=app_id, parent_build_record_id=parent_id, build_id=binding.build_id,
        )
    except Exception:
        logger.warning("inline_run_child_lookup_failed build=%s", binding.build_id, exc_info=True)
        return _InlineRunEffect(True, "child_lookup_failed", reported or None)
    children = [
        child for child in listed
        if child.parent_build_record_id == parent_id
        and _version_metadata(child).get("build_id") == binding.build_id
    ]
    if reported and reported not in {child.id for child in children}:
        return _InlineRunEffect(True, "reported_child_missing", reported)
    open_children = [
        child for child in children
        if child.lifecycle_status not in {ArtifactLifecycleStatus.ARCHIVED, ArtifactLifecycleStatus.DELETED}
    ]
    for child in open_children:
        if child.validation_status == ArtifactValidationStatus.PASSED:
            return _InlineRunEffect(True, None, child.id)
    for child in open_children:
        if child.validation_status in {ArtifactValidationStatus.PENDING, ArtifactValidationStatus.SKIPPED}:
            return _InlineRunEffect(
                True, f"validation_{ArtifactValidationStatus(child.validation_status).value}", child.id,
            )
    return _InlineRunEffect(False)


def _unresolved_change(
    effect: _InlineRunEffect | None, *, baseline_build_record_id: str | None, invalidation_recorded: bool,
    preserve_branding_source: bool = False,
) -> dict[str, Any] | None:
    """The trigger's explicit report of an inline run whose outcome is unresolved, or None."""
    if effect is None or effect.unresolved is None:
        return None
    detail = _UNRESOLVED_CHANGE_DETAILS[effect.unresolved]
    if preserve_branding_source:
        detail = (
            "The color draft did not complete validation. It cannot be accepted. The selected build is unchanged."
            if effect.unresolved in {"validation_pending", "validation_skipped"} else
            "The color draft could not be verified. Review the app's saved builds. The selected build is unchanged."
        )
    return {
        "reason": effect.unresolved,
        "draft_build_record_id": effect.draft_build_record_id,
        "baseline_build_record_id": baseline_build_record_id,
        "baseline_invalidation_recorded": invalidation_recorded,
        "detail": detail,
    }


async def _registry_run_state(
    *, user_id: str, build_registry_id: str | None, source_version: Any,
) -> RegistryRunState | None:
    """Studio's read of the app registry before it begins a run from a STALE build.

    Coding admission admits a STALE parent only when no other run of the app
    was building when this one began (``coding_worker._stale_parent_refusal``),
    so it gets this server-derived read, never a value from the client. It is a
    read before ``begin_refinement_run`` (and, for an app-owned pack scope,
    before classification, for the same check), not a compare-and-set;
    acceptance's registry run check is what binds a child to the latest run.
    For a parent that is not STALE no read is made (None), and STALE admission
    refuses.
    """
    if source_version is None or source_version.lifecycle_status != ArtifactLifecycleStatus.STALE:
        return None
    try:
        record = (await _get_app_registry_service().get_app_record(
            owner_user_id=user_id, build_registry_id=build_registry_id,
        )).get("app")
    except Exception:
        logger.warning("registry_run_state_unverified registry=%s", build_registry_id, exc_info=True)
        return "unverified"
    if not isinstance(record, dict):
        return "unverified"
    return "run_in_flight" if record.get("lifecycle_state") == "building" else "idle"


async def _persist_trigger_invalidation(
    orchestration_control: Any, *, refinement_request: Any, routing_decision: Any,
    change_request_id: str | None, artifact_store: Any,
) -> bool:
    """Ask the invalidation owner to mark the selected build stale; False when that write failed."""
    try:
        await orchestration_control.persist_revision_invalidation(
            refinement_request=refinement_request,
            routing_decision=routing_decision,
            change_request_id=change_request_id,
            artifact_store=artifact_store,
        )
    except Exception as invalidation_err:
        logger.warning("Failed to persist artifact invalidation: %s", invalidation_err)
        return False
    return True


#: A rejected or removed build is never a baseline, whatever it was at the proposal.
_REMOVED_BASELINE_STATUSES = frozenset({ArtifactLifecycleStatus.ARCHIVED, ArtifactLifecycleStatus.DELETED})


def _utc_instant(value: Any) -> datetime | None:
    """A stored timestamp as an aware UTC instant (the store may return naive UTC)."""
    if not isinstance(value, datetime):
        return None
    return (value if value.tzinfo is not None else value.replace(tzinfo=UTC)).astimezone(UTC)


def _baseline_state(version) -> dict[str, Any]:  # noqa: ANN001
    """The selected build's lifecycle state, saved on the proposal's ChangeRequest.

    Every write to a build record (staling, superseding, accepting, rejecting)
    changes its status, its invalidation reason or its ``updated_at``, so a
    confirmation compares this snapshot to tell whether the build changed after
    the proposal.
    """
    def instant(value: Any) -> str | None:
        moment = _utc_instant(value)
        return moment.isoformat(timespec="milliseconds") if moment is not None else None

    return {
        "build_record_id": version.id,
        "lifecycle_status": ArtifactLifecycleStatus(version.lifecycle_status).value,
        "invalidation_reason": version.invalidation_reason,
        "stale_at": instant(version.stale_at),
        "updated_at": instant(version.updated_at),
    }


async def _require_unchanged_confirmation_baseline(
    source_version, change: Any, *, app_id: str, artifact_store: Any,  # noqa: ANN001
) -> None:
    """Refuse a confirmation whose build changed after the proposal, or was overtaken by newer accepted work.

    The proposal saved the build's state on its ChangeRequest
    (``router_decision.baseline_state``, server-written, unlike the pending
    decision). A build that was already STALE when the decision was proposed,
    and has not been written since, is still what the user agreed to change.
    A ChangeRequest with no saved state (recorded before Studio kept that
    state: every proposal saves it, and a workflow launch keeps it when it
    rewrites the routing) cannot show whether its build changed, so its
    decision is refused; requesting the change again saves the state, and that
    proposal can be confirmed.

    Newer accepted work is read as acceptance does
    (``coding_worker.newer_accepted_work``): within the build's family and key,
    a build outside its lineage that is CURRENT, or that was accepted after
    the lineage's nearest accepted build (the build itself when it was
    accepted), even if that build has since been marked stale by a refinement
    of its own. A failed read, or an order that cannot be established,
    refuses too.

    An unchanged baseline grants nothing by itself: the confirmed request then
    follows the existing route for its situation. A scope that includes
    app-owned pack code goes through the baseline rule
    (``coding_worker.pack_code_baseline_refusal``) before any provider call and
    again at coding admission, which admit a STALE parent only under
    ``coding_worker._stale_parent_refusal``; a workflow route launches as an
    unconfirmed one would.
    """
    router_decision = getattr(change, "router_decision", None)
    recorded = router_decision.get("baseline_state") if isinstance(router_decision, dict) else None
    if source_version.lifecycle_status in _REMOVED_BASELINE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail="The build this decision was proposed for was rejected or removed. Request the change again from a saved build.",
        )
    if not isinstance(recorded, dict):
        raise HTTPException(
            status_code=409,
            detail=(
                "The change request this decision was proposed with saved no state of its build (it was recorded "
                "before Studio kept that state), so whether the build changed since the proposal cannot be "
                "checked. Request the change again."
            ),
        )
    if recorded != _baseline_state(source_version):
        raise HTTPException(
            status_code=409,
            detail="The build this decision was proposed for has changed since the proposal. Request the change again.",
        )
    unchecked = "The build this decision was proposed for could not be checked against the app's current build"
    try:
        newer = await newer_accepted_work(
            artifact_store, app_id=app_id, record=source_version, max_depth=_ACCEPTANCE_LINEAGE_MAX_DEPTH,
        )
    except Exception as exc:
        logger.warning("confirmation_lineage_check_failed build=%s", source_version.id, exc_info=True)
        raise HTTPException(status_code=409, detail=f"{unchecked}. Request the change again.") from exc
    if newer is None:
        return
    if newer.kind == "unverified":
        raise HTTPException(status_code=409, detail=f"{unchecked} ({newer.reason}). Request the change again.")
    raise HTTPException(
        status_code=409,
        detail=(
            "A newer build of this app was accepted outside the line of builds this decision was proposed for "
            f"({newer.reason}). Request the change again from the app's latest accepted build."
        ),
    )


def _refuse_template_pack_patch(template_pack_paths: frozenset[str], paths: Iterable[str]) -> None:
    """Refuse a patch that would apply to template-owned pack files of the saved parent only.

    Called only where the decision will apply a patch: an explicit or
    confirmed file scope, an auto-applied inferred scope, or a contract surface
    plan about to run. ``paths`` are every file the patch may edit. When each
    one is template-owned (for example a UI-only refinement of a pack template
    page), candidate admission, which keeps every template-owned output
    byte-identical to the verified parent, could only refuse the candidate, so
    the coding or surface provider call, validation and the registry run are
    skipped. An explicit or confirmed scope is refused before classification
    (and before a pending decision is used); on the inferred and surface paths
    the classifier and the scope proposer or surface planner have already run.

    A decision that applies nothing (a clarification, a workflow route) is
    never refused here, nor a scope that holds template-owned files beside
    files the patch may change: admission stays the backstop for any changed
    template-owned output. Ownership is the parent's verified pack ownership;
    when it cannot be verified, :func:`_refuse_unverifiable_pack_patch`
    refuses the patch instead.
    """
    editable = set(paths)
    if editable and editable <= template_pack_paths:
        raise HTTPException(
            status_code=409,
            detail=(
                f"This change would edit only template-owned capability pack files ({', '.join(sorted(editable))}). "
                f"{TEMPLATE_PACK_OUTPUT_GUIDANCE}. No files were changed."
            ),
        )


def _refuse_unverifiable_pack_patch(pack_owners: dict[str, str] | None, paths: Iterable[str]) -> None:
    """Refuse a patch, before the provider runs, when the saved parent's pack ownership cannot be verified.

    Called where :func:`_refuse_template_pack_patch` is, only where the
    decision will apply a patch. ``pack_owners`` is None when the parent
    selected capability packs whose sources cannot be resolved and verified,
    for example a build recorded before builds pinned workspace packs, or a
    workspace pack edited in place since. Candidate admission refuses every
    candidate of such a parent (``PACK_OWNERSHIP_UNRESOLVED``), so the coding
    or surface provider call, validation and the registry run are skipped. A
    decision that applies nothing, such as a clarification or a workflow route
    (the AppGenerator revision that records a verifiable build), is never
    refused here.
    """
    if pack_owners is None and set(paths):
        raise HTTPException(
            status_code=409,
            detail=f"This change cannot be checked: {PACK_OWNERSHIP_UNRESOLVED_GUIDANCE}. No files were changed.",
        )


async def _refuse_pack_code_from_an_unadmitted_baseline(
    workspace_pack_paths: frozenset[str], paths: Iterable[str], *, source_version: Any, artifact_app_id: str,
    user_id: str, build_registry_id: str | None,
) -> None:
    """Refuse an app-owned pack change that candidate admission would refuse for its baseline, before any provider call.

    Called with an explicit or confirmed file scope: the only scope that can
    carry an app-owned pack change, and one that always applies when it
    proceeds (to the coding worker, or to the contract surface run for a
    design or feature change). When it includes app-owned pack paths of the
    saved build, that build must pass admission's baseline rule
    (``coding_worker.pack_code_baseline_refusal``, ``STALE_REFINEMENT_BASELINE``):
    it is the app's accepted current build or, while none is current, a stale
    build admission admits. A draft, such as the app's first build before the
    owner accepts it, is refused with the way forward: accept it first.

    The refusal comes before classification, before a pending decision is
    used, and before the registry run and any coding or surface provider
    call. Candidate admission runs the same rule on the candidate as the
    backstop, for anything that changed while the provider ran. Reads only; a
    failed read refuses.
    """
    changed = sorted(set(paths) & workspace_pack_paths)
    if not changed:
        return
    registry_run_state = await _registry_run_state(
        user_id=user_id, build_registry_id=build_registry_id, source_version=source_version,
    )
    refusal = await pack_code_baseline_refusal(get_artifact_store(), PackCodeBaseline(
        artifact_app_id=artifact_app_id, build_family=source_version.build_family,
        build_record_id=source_version.id, user_id=user_id, registry_run_state=registry_run_state,
    ))
    if refusal is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"App-owned capability pack code ({', '.join(changed)}) cannot be changed from build "
                f"{source_version.id}: {refusal.reason}. No files were changed."
            ),
        )


def _require_server_proposed_scope(pending: dict[str, Any], change: Any, request: RefinementRequest) -> None:
    """Bind a confirmation to the scope this endpoint proposed for the change request.

    The pending decision can also be written through the session decision
    API, so it does not prove what the server proposed. The ChangeRequest
    the proposal wrote does: its harness decision must name exactly the
    pending decision's files, for the same saved build.
    """
    router_decision = getattr(change, "router_decision", None)
    proposed = router_decision.get("harness_decision") if isinstance(router_decision, dict) else None
    if (
        change is None
        or getattr(change, "build_record_id", None) != request.build_record_id
        or not isinstance(proposed, dict)
        or list(pending.get("selected_paths") or []) != list(proposed.get("selected_paths") or [])
    ):
        raise HTTPException(
            status_code=409,
            detail="The proposed scope does not match the change request it was proposed for. Submit the change again.",
        )


def _confirmed_refinement_decision(
    snapshot: dict[str, Any] | None,
    request: RefinementRequest,
    *,
    action_id: str,
    change_request_id: str | None,
    revision_id: str | None,
    source_chat_id: str | None,
) -> tuple[dict[str, Any], RefinementRequest]:
    """Bind an approval to the existing target-scoped pending decision."""
    pending = (snapshot or {}).get("pending_harness_decision") or {}
    metadata = pending.get("metadata") or {}
    if "source_chat_id" in metadata and metadata["source_chat_id"] != source_chat_id:
        raise HTTPException(status_code=409, detail="The decision belongs to a different review conversation. Reopen its original review.")
    stored_request = (pending.get("trigger_payload") or {}).get("refinement_request")
    actions = pending.get("actions") or []
    if (
        not isinstance(stored_request, dict)
        or not change_request_id or pending.get("change_request_id") != change_request_id
        or not revision_id or pending.get("revision_id") != revision_id
        or not pending.get("decision_id")
        or not any(action.get("action_id") == action_id for action in actions)
    ):
        raise HTTPException(status_code=409, detail="The proposed scope is no longer current. Submit the change again.")
    try:
        previous = RefinementRequest.model_validate(stored_request)
    except ValidationError as exc:
        raise HTTPException(status_code=409, detail="The proposed scope is unavailable. Submit the change again.") from exc
    if request.refinement_model is None:
        request = request.model_copy(update={"refinement_model": previous.refinement_model})
    if any(
        getattr(previous, field) != getattr(request, field)
        for field in (
            "app_id", "target_app_id", "user_id", "request_kind", "declared_change_class",
            "build_family", "build_key", "build_record_id", "raw_user_request",
            "requested_workflow_id", "source_surface", "refinement_model",
        )
    ):
        raise HTTPException(status_code=409, detail="The proposed scope belongs to a different request or artifact. Submit the change again.")
    return pending, request


async def require_studio_model_admin(
    principal: UserPrincipal = Depends(require_studio_user),
) -> UserPrincipal:
    if not principal.is_authenticated or not principal.has_role("admin"):
        raise HTTPException(403, "Workspace administrator access required.")
    return principal


@app.get("/api/studio/refinement/models")
async def list_refinement_models(
    principal: UserPrincipal = Depends(require_studio_user),
) -> dict[str, Any]:
    return await studio_model_options(principal)


@app.get("/api/studio/settings/ai-models")
async def read_studio_model_policy(
    principal: UserPrincipal = Depends(require_studio_model_admin),
) -> dict[str, Any]:
    return await studio_model_options(principal, admin=True)


@app.put("/api/studio/settings/ai-models", status_code=204)
async def update_studio_model_policy(
    body: StudioModelPolicy,
    principal: UserPrincipal = Depends(require_studio_model_admin),
) -> Response:
    await save_studio_model_policy(principal, body)
    return Response(status_code=204)


@app.post("/api/workflows/trigger")
async def trigger_workflow(
    body: WorkflowTriggerRequest,
    principal: UserPrincipal = Depends(require_studio_user),
):
    app_id, user_id = _resolve_studio_scope(principal, app_id=body.app_id, user_id=body.user_id)
    authorize_http_workflow_source_paths(body.context_variables, principal=principal)
    retry_contribution = None
    if body.retry_failed:
        try:
            retry_contribution = await _failed_workflow_retry_contribution(body, app_id=app_id, user_id=user_id)
            if isinstance(retry_contribution, _DownstreamRetry):
                result = await _launch_downstream_retry(retry_contribution, app_id=app_id, user_id=user_id)
                return _workflow_trigger_response(result)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    build_registry_id = body.build_registry_id
    artifact_app_id = app_id
    session_router = get_session_router()
    trigger_payload = dict(body.trigger_payload or {})
    orchestration_control = get_orchestration_control_harness()
    refinement_request = None
    refinement_decision = None
    harness_decision = None
    resolved_change_class = None
    resolved_artifact_kind = None
    resolved_artifact_version_id = None
    coding_request = None
    coding_result = None
    coding_session = None
    contract_surface_plan = None
    surface_result = None
    inline_binding = None
    baseline_files = None
    baseline_assets: dict[str, bytes] = {}
    branding_intent = None
    proposal_baseline_state = None
    persisted_change_request_id = str(trigger_payload.get("change_request_id") or "").strip() or None
    persisted_revision_id = str(trigger_payload.get("revision_id") or "").strip() or None
    action_payload = trigger_payload.get("harness_action")
    action_id = str(action_payload.get("action_id") or "").strip() if isinstance(action_payload, dict) else None
    workflow_continuation = action_id in {"run_recommended_workflow", "confirm_recommended_workflow"}

    if body.trigger_source == "refinement":
        from mozaiksai.core.runtime.composition.platform_hooks import get_platform_hooks
        from mozaiksai.core.session.launcher import _PERSISTENCE_MANAGER
        from mozaiksai.core.usage.context import AuxiliaryUsageContext

        protected_context = {
            "run_build_binding", "build_registry_id", "build_id", "target_app_id",
            "artifact_root", "bundle_path", "lifecycle_state", "artifact_version_id",
            "app_id", "user_id", "chat_id",
        }
        if protected_context.intersection(body.context_variables):
            raise HTTPException(status_code=400, detail="Refinement context cannot override saved build identity or paths")
        if body.source_chat_id:
            source = await _get_app_registry_service().repo.get_owned_chat_binding(
                app_id=app_id, owner_user_id=user_id, chat_id=body.source_chat_id,
            )
            if not source:
                raise HTTPException(status_code=404, detail="Source build session not found")
            binding = RunBuildBinding.model_validate(source)
            if build_registry_id and build_registry_id != binding.build_registry_id:
                raise HTTPException(status_code=400, detail="Source session does not match selected build")
            build_registry_id = binding.build_registry_id
        artifact_app_id, _ = await _resolve_studio_artifact_scope(
            principal, app_id=app_id, build_registry_id=build_registry_id,
        )
        session_router = session_router.for_target(artifact_app_id)
        allowed, reason = await get_platform_hooks().call_chat_prereqs(
            app_id, user_id, body.workflow_id or "", _PERSISTENCE_MANAGER,
        )
        if not allowed:
            raise HTTPException(status_code=403, detail=reason or "Workflow prerequisites not met")
        session_snapshot = None
        if action_id:
            try:
                session_snapshot = await session_router.get_session_snapshot(
                    app_id=app_id,
                    user_id=user_id,
                )
            except Exception as session_err:
                logger.warning("Failed to load prelaunch revision session state: %s", session_err)
                session_snapshot = None
            if isinstance(session_snapshot, dict):
                if not str(trigger_payload.get("change_request_id") or "").strip():
                    active_change_request_id = str(
                        session_snapshot.get("active_change_request_id") or ""
                    ).strip()
                    if active_change_request_id:
                        trigger_payload["change_request_id"] = active_change_request_id
                if not str(trigger_payload.get("revision_id") or "").strip():
                    active_revision_id = str(
                        session_snapshot.get("active_revision_id") or ""
                    ).strip()
                    if active_revision_id:
                        trigger_payload["revision_id"] = active_revision_id
        usage_context = AuxiliaryUsageContext(
            app_id=app_id, user_id=user_id,
            tenant_id=principal.tenant_id, workspace_id=principal.workspace_id,
        )
        try:
            refinement_request = orchestration_control.request_from_payload(
                usage_context=usage_context,
                payload=trigger_payload,
                app_id=app_id,
                target_app_id=artifact_app_id,
                user_id=user_id,
                requested_workflow_id=body.workflow_id,
                default_source_surface=(
                    str((body.context_variables or {}).get("screen") or "").strip() or None
                ),
            )
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail="Invalid refinement_request") from exc

        if refinement_request is None:
            raise HTTPException(
                status_code=400,
                detail="refinement triggers require trigger_payload.refinement_request.",
            )
        if action_id is None:
            selection = await select_studio_model(
                principal, refinement_request.refinement_model, require_available=False,
            )
            if selection is not None:
                refinement_request = refinement_request.model_copy(update={"refinement_model": selection})
        source_version = await get_artifact_store().get_build_record(
            app_id=artifact_app_id, build_record_id=refinement_request.build_record_id,
        ) if refinement_request.build_record_id else None
        if source_version is None or source_version.build_family != refinement_request.build_family:
            raise HTTPException(status_code=404, detail="Selected refinement artifact not found")
        proposal_baseline_state = _baseline_state(source_version)
        metadata = _version_metadata(source_version)
        try:
            await require_accepted_genesis_baseline(
                source_version, owner_user_id=user_id, execution_app_id=app_id,
                build_registry_id=build_registry_id or "",
                registry_service=_get_app_registry_service(),
            )
        except (GenesisImportError, ValueError, OSError, ContentNotFoundError) as exc:
            raise HTTPException(status_code=409, detail="Selected imported Genesis source has no accepted owner review") from exc
        # The saved parent's verified pack ownership. App-owned pack code: an
        # inferred scope that would change it waits for confirmation, and
        # changing it needs the current build. Template-owned pack files: no
        # refinement changes them, so a patch that would apply to them only is
        # refused before the provider runs (candidate admission stays the
        # backstop for every candidate).
        pack_owners = parent_pack_output_owners(metadata.get("capability_packs") or [])
        workspace_pack_paths = pack_paths_owned_by(pack_owners, "workspace")
        template_pack_paths = pack_paths_owned_by(pack_owners, "templates")
        # Filesystem roots and baseline contracts come from the saved version,
        # not from caller context or copied workbench metadata.
        safe_extra = {
            key: value for key, value in refinement_request.extra.items()
            if key in {"change_request_id", "revision_id"}
        }
        safe_extra["files_manifest"] = [entry.model_dump(mode="python") for entry in source_version.files_manifest]
        if metadata.get("workspace_dir"):
            safe_extra.update(lifecycle_state="review", bundle_path=metadata["workspace_dir"])
        refinement_request = refinement_request.model_copy(update={"extra": safe_extra})
        persisted_revision_id = (
            str(refinement_request.extra.get("revision_id") or "").strip() or persisted_revision_id
        )
        persisted_change_request_id = (
            str(refinement_request.extra.get("change_request_id") or "").strip()
            or persisted_change_request_id
        )
        change = None
        if persisted_change_request_id:
            change = await get_artifact_store().get_change_request(
                app_id=artifact_app_id, change_request_id=persisted_change_request_id,
            )
            if change is None or change.created_by_user_id != user_id:
                raise HTTPException(status_code=404, detail="Refinement change request not found")
        if persisted_revision_id:
            snapshot = await session_router.get_session_snapshot(app_id=app_id, user_id=user_id)
            if not snapshot or snapshot.get("active_revision_id") != persisted_revision_id:
                raise HTTPException(status_code=409, detail="Refinement revision is no longer active")

        pending_decision = None
        if action_id:
            pending_decision, refinement_request = _confirmed_refinement_decision(
                session_snapshot, refinement_request, action_id=action_id,
                change_request_id=persisted_change_request_id, revision_id=persisted_revision_id,
                source_chat_id=body.source_chat_id,
            )
            if body.context_variables != (pending_decision.get("context_variables") or {}):
                raise HTTPException(status_code=409, detail="The decision belongs to a different request context. Submit the change again.")
            _require_server_proposed_scope(pending_decision, change, refinement_request)
            await _require_unchanged_confirmation_baseline(
                source_version, change, app_id=artifact_app_id, artifact_store=get_artifact_store(),
            )
        coding_payload = trigger_payload.get("coding_request")
        if workflow_continuation:
            # Explicit workflow continuation must not try inline generation first.
            coding_payload = None
            trigger_payload.pop("coding_request", None)
        if coding_payload is not None and not isinstance(coding_payload, dict):
            raise HTTPException(status_code=400, detail="Invalid coding_request")
        if action_id == "apply_proposed_scope":
            confirmed_paths = (pending_decision or {}).get("selected_paths")
            if not isinstance(confirmed_paths, list) or not confirmed_paths or any(
                not isinstance(path, str) or not path for path in confirmed_paths
            ):
                raise HTTPException(status_code=409, detail="The proposed scope has no available files. Submit the change again.")
            supplied_files = coding_payload.get("files") if isinstance(coding_payload, dict) else None
            if supplied_files is not None and (
                not isinstance(supplied_files, dict) or set(supplied_files) != set(confirmed_paths)
            ):
                raise HTTPException(status_code=409, detail="The submitted files do not match the proposed scope.")
            coding_payload = {**(coding_payload or {}), "files": dict.fromkeys(confirmed_paths, "")}
            trigger_payload["coding_request"] = coding_payload
        if isinstance(coding_payload, dict) and "files" in coding_payload and not isinstance(coding_payload["files"], dict):
            raise HTTPException(status_code=400, detail="Refinement file scope must be an object")
        explicit_paths = list((coding_payload or {}).get("files") or {})
        if explicit_paths:
            if not orchestration_control.coding_enabled():
                raise HTTPException(status_code=409, detail="Selected-file refinement is unavailable. The file scope has not been widened.")
            baseline_files, baseline_assets = await _inline_refinement_baseline(source_version)
            if any(path not in baseline_files for path in explicit_paths):
                raise HTTPException(status_code=400, detail="Refinement file scope is not in the selected artifact")
            coding_payload = {**coding_payload, "files": {path: baseline_files[path] for path in explicit_paths}}
            trigger_payload["coding_request"] = coding_payload
        # An explicit or confirmed file scope always applies when it proceeds.
        _refuse_unverifiable_pack_patch(pack_owners, explicit_paths)
        _refuse_template_pack_patch(template_pack_paths, explicit_paths)
        await _refuse_pack_code_from_an_unadmitted_baseline(
            workspace_pack_paths, explicit_paths, source_version=source_version, artifact_app_id=artifact_app_id,
            user_id=user_id, build_registry_id=build_registry_id,
        )
        if orchestration_control.coding_enabled() and isinstance(coding_payload, dict):
            try:
                resolve_coding_validation_strategy(coding_payload.get("validation_strategy"))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        if pending_decision is not None:
            try:
                await session_router.resolve_pending_harness_decision(
                    app_id=app_id, user_id=user_id,
                    decision_id=pending_decision["decision_id"], action_id=action_id,
                    expected_pending_decision=pending_decision,
                )
            except ValueError as exc:
                raise HTTPException(status_code=409, detail="This decision has already been handled or replaced. Submit the change again.") from exc
            refinement_request = refinement_request.model_copy(update={"extra": {
                **refinement_request.extra, "harness_action": {"action_id": action_id},
            }})
        try:
            refinement_decision = await orchestration_control.route_refinement_request(refinement_request)
        except Exception as exc:
            logger.error("refinement_classification_failed: %s", exc, exc_info=True)
            raise HTTPException(status_code=503, detail="Refinement classification unavailable") from exc
        if workflow_continuation and pending_decision is not None and (
            refinement_decision.workflow_id != pending_decision.get("recommended_workflow_id")
            or refinement_decision.workflow_sequence != pending_decision.get("journey_id")
        ):
            raise HTTPException(status_code=409, detail="The recommended workflow has changed. Submit the change again to review the new route.")
        harness_decision = orchestration_control.build_harness_decision(refinement_decision)
        branding_intent = refinement_decision.change_intent.branding_intent
        branding_workflow = branding_intent in {"visual_redesign", "new_logo"}
        if branding_intent == "theme_color_patch" and explicit_paths and explicit_paths != [APP_THEME_PATH]:
            raise HTTPException(
                status_code=409,
                detail="The selected files do not match the app's color settings. Clear the file limit or refine the selected files.",
            )
        allow_surface = (
            not workflow_continuation
            and not branding_workflow
            and not refinement_decision.change_intent.requires_workflow_revision
            and orchestration_control.contract_surface_enabled()
            and refinement_decision.change_intent.change_class.value in {"design", "feature"}
            and refinement_request.build_family == "app_bundle"
        )
        selected_surface_request = bool(explicit_paths) and allow_surface
        if explicit_paths and refinement_decision.change_intent.change_class.value != "patch" and not selected_surface_request:
            raise HTTPException(
                status_code=409,
                detail="This change needs a broader plan than the selected files allow. Narrow the request, or clear the file limit and submit it for a broader review. No files were changed.",
            )
        if branding_workflow:
            saved_context_keys = {
                "parent_theme_config", "parent_shell_config", "refinement_request",
                "build_record_id", "refinement_request_meta", "build_mode",
            }
            if saved_context_keys.intersection(body.context_variables):
                raise HTTPException(status_code=400, detail="The branding workflow uses the selected build and its saved refinement request.")
            refinement_decision.context_seed["parent_theme_config"] = await _saved_parent_theme(source_version)
        if allow_surface:
            inline_binding = None
            try:
                if baseline_files is None:
                    baseline_files, baseline_assets = await _inline_refinement_baseline(source_version)
                contract_surface_plan, harness_decision = (
                    await orchestration_control.prepare_contract_surface_request(
                        refinement_request=refinement_request,
                        routing_decision=refinement_decision,
                        workspace_files=baseline_files,
                        allowed_paths=explicit_paths or None,
                    )
                )
                if contract_surface_plan is not None and contract_surface_plan.requires_schema_migration:
                    contract_surface_plan = None
                    harness_decision = orchestration_control.build_harness_decision(refinement_decision)
                if selected_surface_request and contract_surface_plan is None:
                    raise HTTPException(
                        status_code=409,
                        detail="This change could not be resolved within the selected files. Narrow the request, or clear the file limit to review a broader plan. No files were changed.",
                    )
                if contract_surface_plan is not None:
                    if pack_owners is None or template_pack_paths:
                        surface_paths = [
                            path for surface in contract_surface_plan.surfaces for path in surface.affected_paths
                        ]
                        _refuse_unverifiable_pack_patch(pack_owners, surface_paths)
                        _refuse_template_pack_patch(template_pack_paths, surface_paths)
                    registry_run_state = await _registry_run_state(
                        user_id=user_id, build_registry_id=build_registry_id, source_version=source_version,
                    )
                    inline_binding = await _get_app_registry_service().begin_refinement_run(
                        owner_user_id=user_id, app_id=app_id, build_registry_id=build_registry_id,
                        workflow_name=refinement_decision.workflow_id,
                    )
                    if refinement_request.refinement_model is not None:
                        inline_binding = inline_binding.model_copy(update={
                            "refinement_model": refinement_request.refinement_model,
                        })
                    refinement_request = refinement_request.model_copy(update={
                        "usage_context": usage_context.model_copy(update={"run_build_binding": inline_binding}),
                    })
                    surface_result = await orchestration_control.execute_surface_plan(
                        plan=contract_surface_plan,
                        refinement_request=refinement_request,
                        routing_decision=refinement_decision,
                        workspace_files=baseline_files,
                        allowed_paths=explicit_paths or None,
                    )
                    finalized = await orchestration_control.finalize_surface_output(
                        plan=contract_surface_plan, result=surface_result, refinement_request=refinement_request,
                        routing_decision=refinement_decision, workspace_files=baseline_files,
                        run_build_binding=inline_binding, allowed_paths=explicit_paths or None,
                        registry_run_state=registry_run_state,
                        preserved_assets=baseline_assets,
                    )
                    await _complete_inline_refinement(binding=inline_binding, user_id=user_id, result=finalized)
                    surface_result = surface_result.model_copy(update={
                        "status": {"validated": "success", "planned": "partial"}.get(finalized.status, "failed"),
                        "metadata": {**surface_result.metadata, **finalized.metadata, "validation_result": finalized.validation_result},
                    })
            except (HTTPException, CancelledError):
                if inline_binding is not None:
                    await _fail_inline_refinement(binding=inline_binding, user_id=user_id)
                raise
            except Exception as exc:
                if inline_binding is not None:
                    await _fail_inline_refinement(binding=inline_binding, user_id=user_id)
                    logger.exception("surface_regeneration_failed build=%s", inline_binding.build_id)
                    raise HTTPException(status_code=503, detail="Surface refinement unavailable") from exc
                if selected_surface_request:
                    raise HTTPException(
                        status_code=409,
                        detail="This change could not be verified within the selected files. No files were changed. Submit a narrower request or review a broader plan.",
                    ) from exc
                logger.warning("contract_surface_planner_failed, falling back to workflow: %s", exc)
                contract_surface_plan = None
                surface_result = None
                harness_decision = orchestration_control.build_harness_decision(refinement_decision)
        resolved_change_class = refinement_decision.change_intent.change_class.value
        resolved_artifact_kind = refinement_request.build_family
        resolved_artifact_version_id = refinement_request.build_record_id
        if surface_result is None and not workflow_continuation and orchestration_control.coding_enabled() and isinstance(trigger_payload.get("coding_request"), dict):
            coding_payload = dict(trigger_payload["coding_request"])
            coding_request = orchestration_control.build_coding_request(
                refinement_request=refinement_request,
                routing_decision=refinement_decision,
                payload=coding_payload,
            )
            if coding_request is not None:
                inline_binding = None
                try:
                    coding_request, coding_decision = await orchestration_control.prepare_coding_request(
                        coding_request, confirmation_required_paths=workspace_pack_paths,
                    )
                    harness_decision = coding_decision
                    if coding_request is not None:
                        # Only an applying decision (auto_patch) returns a request.
                        _refuse_unverifiable_pack_patch(pack_owners, coding_request.files)
                        _refuse_template_pack_patch(template_pack_paths, coding_request.files)
                        if baseline_files is None:
                            baseline_files, baseline_assets = await _inline_refinement_baseline(source_version)
                        if baseline_assets.keys() & coding_request.files.keys():
                            raise HTTPException(status_code=409, detail="Binary assets cannot be edited by the coding worker")
                        registry_run_state = await _registry_run_state(
                            user_id=user_id, build_registry_id=build_registry_id, source_version=source_version,
                        )
                        inline_binding = await _get_app_registry_service().begin_refinement_run(
                            owner_user_id=user_id, app_id=app_id,
                            build_registry_id=build_registry_id,
                            workflow_name=refinement_decision.workflow_id,
                        )
                        if refinement_request.refinement_model is not None:
                            inline_binding = inline_binding.model_copy(update={
                                "refinement_model": refinement_request.refinement_model,
                            })
                        # Server-derived, set after the request is built: the client
                        # payload cannot carry it.
                        coding_request = coding_request.model_copy(update={
                            "run_build_binding": inline_binding, "baseline_files": baseline_files,
                            "registry_run_state": registry_run_state,
                        })
                        coding_result = await orchestration_control.execute_coding_request(
                            coding_request, **({"preserved_assets": baseline_assets} if baseline_assets else {}),
                        )
                        await _complete_inline_refinement(
                            binding=inline_binding, user_id=user_id, result=coding_result,
                        )
                        harness_decision = orchestration_control.build_coding_result_decision(coding_request, coding_result)
                except (HTTPException, CancelledError):
                    if inline_binding is not None:
                        await _fail_inline_refinement(binding=inline_binding, user_id=user_id)
                    raise
                except Exception as exc:
                    if inline_binding is not None:
                        await _fail_inline_refinement(binding=inline_binding, user_id=user_id)
                    logger.error("coding_worker_failed: %s", exc, exc_info=True)
                    raise HTTPException(status_code=503, detail="Coding worker unavailable") from exc
        trigger_payload = {
            "refinement_request": refinement_request.model_dump(mode="python"),
        }
        if persisted_change_request_id is not None:
            trigger_payload["change_request_id"] = persisted_change_request_id
        if persisted_revision_id is not None:
            trigger_payload["revision_id"] = persisted_revision_id

    artifact_store = get_artifact_store() if refinement_request is not None and refinement_decision is not None else None

    if (
        persisted_change_request_id is None
        and refinement_request is not None
        and refinement_decision is not None
        and artifact_store is not None
    ):
        try:
            persisted_change_request = await artifact_store.create_change_request(
                app_id=artifact_app_id,
                build_family=refinement_request.build_family,
                build_key=body.artifact_key or refinement_request.normalized_build_key(),
                build_record_id=refinement_request.build_record_id,
                raw_user_request=refinement_request.raw_user_request,
                classification=ChangeClassification(refinement_decision.change_intent.change_class.value),
                refinement_request=refinement_request.model_dump(mode="python"),
                change_intent=refinement_decision.change_intent.model_dump(mode="python"),
                impact_set=refinement_decision.impact_set.model_dump(mode="python"),
                router_decision={
                    "workflow_id": refinement_decision.workflow_id,
                    "workflow_sequence": refinement_decision.workflow_sequence,
                    "requested_workflow_id": body.workflow_id,
                    "explanation": refinement_decision.explanation,
                    "is_full_restart": refinement_decision.is_full_restart,
                    "rerouted_by_dependency": False,
                    "execution_mode": (
                        "coding_worker"
                        if coding_result is not None and coding_result.eligible
                        else "surface_regeneration"
                        if surface_result is not None
                        else "harness_decision"
                        if harness_decision is not None
                        and (
                            harness_decision.requires_confirmation
                            or harness_decision.decision_type in {"clarify_scope", "fallback_workflow"}
                        )
                        else "workflow"
                    ),
                    "harness_decision": (
                        harness_decision.model_dump(mode="python") if harness_decision is not None else None
                    ),
                    # The selected build as this request found it, before any run:
                    # a confirmation of a decision proposed here compares it.
                    "baseline_state": proposal_baseline_state,
                },
                created_by_user_id=user_id,
            )
            persisted_change_request_id = persisted_change_request.id
        except Exception as persist_err:
            logger.warning("Failed to persist ChangeRequest: %s", persist_err)
    if persisted_change_request_id is not None:
        trigger_payload["change_request_id"] = persisted_change_request_id

    # The ChangeRequest above, the session's revision state and the run
    # records below keep the history of every attempt. The selected build is
    # invalidated only by an operation that changed it or left its outcome
    # unresolved: an inline run whose stored, unrejected child passed
    # validation, is pending or was skipped, or whose child lookup failed
    # (checked here, in the store), or a workflow that starts rebuilding its
    # inputs. Scoped branding changes stage a child for review and leave the selected
    # app bundle in its original lifecycle until acceptance. A returned decision,
    # a failed launch, or an inline run that stored nothing or only a child that
    # failed validation leaves it as it was. An unresolved outcome is reported explicitly in the response.
    invalidation_pending = (
        persisted_change_request_id is not None
        and refinement_request is not None
        and refinement_decision is not None
    )
    preserve_branding_source = (
        refinement_request is not None and refinement_request.build_family == "app_bundle"
        and branding_intent in {"theme_color_patch", "visual_redesign", "new_logo"}
    )
    inline_effect = None
    invalidation_recorded = False
    if (
        invalidation_pending
        and inline_binding is not None
        and (coding_result is not None or surface_result is not None)
    ):
        inline_effect = await _inline_run_baseline_effect(
            artifact_store, app_id=artifact_app_id,
            parent_build_record_id=refinement_request.build_record_id, binding=inline_binding,
            result=coding_result if coding_result is not None else surface_result,
        )
        if inline_effect.changed and not preserve_branding_source:
            invalidation_recorded = await _persist_trigger_invalidation(
                orchestration_control, refinement_request=refinement_request, routing_decision=refinement_decision,
                change_request_id=persisted_change_request_id, artifact_store=artifact_store,
            )
            invalidation_pending = False
    unresolved_change = _unresolved_change(
        inline_effect, baseline_build_record_id=refinement_request.build_record_id if refinement_request else None,
        invalidation_recorded=invalidation_recorded,
        preserve_branding_source=preserve_branding_source,
    )

    confirmed_action = action_id if refinement_request is not None else None

    should_return_harness_decision = (
        harness_decision is not None
        and coding_result is None
        and (
            (
                harness_decision.decision_type == "clarify_scope"
                and confirmed_action not in {"apply_proposed_scope"}
            )
            or (
                harness_decision.decision_type == "fallback_workflow"
                and confirmed_action not in {"run_recommended_workflow", "confirm_recommended_workflow"}
            )
            or (
                harness_decision.requires_confirmation
                and confirmed_action not in {"confirm_recommended_workflow", "run_recommended_workflow"}
            )
        )
    )

    if should_return_harness_decision:
        decision_metadata = {
            **(harness_decision.metadata or {}), "build_registry_id": build_registry_id,
            "source_chat_id": body.source_chat_id,
        }
        try:
            pending_workflow_id = harness_decision.recommended_workflow_id or refinement_decision.workflow_id
            pending_journey_id = (
                refinement_decision.workflow_sequence if refinement_decision is not None else body.journey_id
            )
            pending_revision_id = (
                persisted_revision_id
                or str((refinement_decision.context_seed or {}).get("revision_id") or "").strip()
                or uuid4().hex
            )
            trigger_payload["revision_id"] = pending_revision_id
            pending_decision = RoutingDecision(
                workflow_id=pending_workflow_id,
                requested_workflow_id=body.workflow_id or pending_workflow_id,
                journey_id=pending_journey_id,
                context_seed=dict(refinement_decision.context_seed or {}),
                explanation=refinement_decision.explanation if refinement_decision is not None else "",
                is_full_restart=bool(refinement_decision.is_full_restart),
                rerouted_by_dependency=False,
                lifecycle_state=(
                    SessionLifecycle.STALE
                    if refinement_decision is not None and refinement_decision.is_full_restart
                    else SessionLifecycle.ACTIVE
                ),
            )
            if persisted_change_request_id:
                pending_decision.context_seed["change_request_id"] = persisted_change_request_id
            pending_decision.context_seed["revision_id"] = pending_revision_id
            pending_harness_decision = PendingHarnessDecision(
                decision_id=(
                    persisted_change_request_id
                    or pending_revision_id
                    or uuid4().hex
                ),
                decision_type=harness_decision.decision_type,
                message=harness_decision.message,
                rationale=harness_decision.rationale,
                confidence=float(harness_decision.confidence),
                recommended_workflow_id=harness_decision.recommended_workflow_id,
                selected_paths=list(harness_decision.selected_paths or []),
                clarification_question=harness_decision.clarification_question,
                change_request_id=persisted_change_request_id,
                revision_id=pending_revision_id,
                requires_confirmation=bool(harness_decision.requires_confirmation),
                trigger_source=body.trigger_source,
                requested_workflow_id=body.workflow_id,
                journey_id=pending_journey_id,
                context_variables=dict(body.context_variables or {}),
                trigger_payload=dict(trigger_payload or {}),
                actions=[
                    PendingDecisionAction(
                        action_id=action.action_id,
                        label=action.label,
                        action_type=action.action_type,
                        workflow_id=action.workflow_id,
                        metadata=dict(action.metadata or {}),
                    )
                    for action in harness_decision.actions
                ],
                metadata=decision_metadata,
            )
            pending_snapshot = await session_router.persist_revision_intent(
                trigger=TriggerInput(
                    app_id=app_id,
                    user_id=user_id,
                    trigger_source=body.trigger_source,
                    workflow_id=body.workflow_id,
                    journey_id=pending_journey_id,
                    context_variables=body.context_variables or {},
                    trigger_payload=trigger_payload,
                ),
                decision=pending_decision,
                pending_harness_decision=pending_harness_decision,
            )
            pending_revision_id = str(pending_snapshot.get("active_revision_id") or "").strip()
            if pending_revision_id:
                persisted_revision_id = pending_revision_id
                trigger_payload["revision_id"] = pending_revision_id
        except Exception as session_err:
            logger.warning("Failed to persist prelaunch revision intent: %s", session_err)
        return {
            "execution_mode": "harness_decision",
            "build_registry_id": build_registry_id,
            "change_request_id": persisted_change_request_id,
            "revision_id": persisted_revision_id,
            "chat_id": None,
            "workflow_id": harness_decision.recommended_workflow_id or refinement_decision.workflow_id,
            "requested_workflow_id": body.workflow_id,
            "websocket_url": None,
            "trigger_source": body.trigger_source,
            "routing_explanation": refinement_decision.explanation if refinement_decision is not None else "",
            "rerouted_by_dependency": False,
            "harness_decision": {**harness_decision.model_dump(mode="python"), "metadata": decision_metadata},
        }

    review_continuation = None
    review_continuation_error = None
    if inline_binding is not None and (coding_result is not None or surface_result is not None):
        try:
            review_continuation = await continue_inline_app_review(
                app_id=app_id, user_id=user_id, source_chat_id=body.source_chat_id,
                binding=inline_binding, baseline_id=refinement_request.build_record_id,
                result=coding_result if coding_result is not None else surface_result,
                registry=_get_app_registry_service(), artifact_store=artifact_store,
                persistence=_PERSISTENCE_MANAGER, session_router=session_router,
            )
        except Exception:
            logger.exception("inline_review_continuation_failed build=%s", inline_binding.build_id)
            review_continuation_error = (
                "The edit finished, but its review chat could not be reopened. "
                "Open the app's saved builds to review the result."
            )

    if coding_result is not None and coding_result.eligible:
        if artifact_store is not None and persisted_change_request_id is not None and refinement_request.build_record_id:
            try:
                session_status = {
                    "validated": RefinementSessionStatus.VALIDATED,
                    "failed": RefinementSessionStatus.FAILED,
                }.get(coding_result.status, RefinementSessionStatus.PENDING)
                coding_session = await artifact_store.create_refinement_session(
                    app_id=artifact_app_id,
                    build_record_id=refinement_request.build_record_id,
                    change_request_id=persisted_change_request_id,
                    result_build_record_id=coding_result.metadata.get("build_record_id"),
                    provider="control_plane_coding",
                    status=session_status,
                    preview_url=((coding_result.validation_result or {}).get("preview_url")),
                    metadata={
                        "coding_worker": coding_result.model_dump(mode="python"),
                        "workflow_id": refinement_decision.workflow_id,
                        **({"unresolved_change": unresolved_change} if unresolved_change else {}),
                    },
                )
            except Exception as persist_err:
                logger.warning("Failed to persist RefinementSession: %s", persist_err)

        return {
            "execution_mode": "coding_worker",
            "chat_id": None,
            "workflow_id": refinement_decision.workflow_id,
            "requested_workflow_id": body.workflow_id or refinement_decision.workflow_id,
            "websocket_url": None,
            "trigger_source": body.trigger_source,
            "routing_explanation": refinement_decision.explanation,
            "rerouted_by_dependency": False,
            "refinement_session_id": coding_session.id if coding_session is not None else None,
            "harness_decision": harness_decision.model_dump(mode="python") if harness_decision is not None else None,
            "coding_worker": coding_result.model_dump(mode="python"),
            "unresolved_change": unresolved_change,
            "review_continuation": review_continuation,
            "review_continuation_error": review_continuation_error,
        }

    if surface_result is not None:
        surface_session = None
        if (
            artifact_store is not None
            and persisted_change_request_id is not None
            and refinement_request.build_record_id
        ):
            try:
                surface_session_status = {
                    "success": RefinementSessionStatus.VALIDATED,
                    "partial": RefinementSessionStatus.PENDING,
                    "failed": RefinementSessionStatus.FAILED,
                }.get(surface_result.status, RefinementSessionStatus.PENDING)
                surface_session = await artifact_store.create_refinement_session(
                    app_id=artifact_app_id,
                    build_record_id=refinement_request.build_record_id,
                    change_request_id=persisted_change_request_id,
                    provider="contract_surface_regeneration",
                    status=surface_session_status,
                    result_build_record_id=surface_result.metadata.get("build_record_id"),
                    metadata={
                        "surface_result": surface_result.model_dump(mode="python"),
                        "workflow_id": refinement_decision.workflow_id,
                        **({"unresolved_change": unresolved_change} if unresolved_change else {}),
                    },
                )
            except Exception as persist_err:
                logger.warning("Failed to persist surface RefinementSession: %s", persist_err)
        return {
            "execution_mode": "surface_regeneration",
            "chat_id": None,
            "workflow_id": refinement_decision.workflow_id,
            "requested_workflow_id": body.workflow_id or refinement_decision.workflow_id,
            "websocket_url": None,
            "trigger_source": body.trigger_source,
            "routing_explanation": refinement_decision.explanation,
            "rerouted_by_dependency": False,
            "refinement_session_id": surface_session.id if surface_session is not None else None,
            "harness_decision": harness_decision.model_dump(mode="python") if harness_decision is not None else None,
            "surface_result": surface_result.model_dump(mode="python"),
            "unresolved_change": unresolved_change,
            "review_continuation": review_continuation,
            "review_continuation_error": review_continuation_error,
        }

    try:
        launch = await prepare_routed_workflow_launch(
            workflow_id=body.workflow_id,
            app_id=app_id,
            user_id=user_id,
            trigger_source=body.trigger_source,
            journey_id=(refinement_decision.workflow_sequence if refinement_decision is not None else body.journey_id),
            context_variables=body.context_variables or {},
            trigger_payload=trigger_payload,
            build_registry_id=build_registry_id,
            source_chat_id=body.source_chat_id,
            session_router=session_router,
            **({"refinement_model": refinement_request.refinement_model}
               if refinement_request is not None and refinement_request.refinement_model is not None else {}),
            routing_contribution=(
                TriggerRoutingContribution(
                    workflow_id=refinement_decision.workflow_id,
                    journey_id=refinement_decision.workflow_sequence,
                    context_seed=refinement_decision.context_seed,
                    explanation=refinement_decision.explanation,
                    is_full_restart=refinement_decision.is_full_restart,
                    lifecycle_state=SessionLifecycle.STALE if refinement_decision.is_full_restart else SessionLifecycle.ACTIVE,
                ) if refinement_decision is not None else retry_contribution
            ),
            extra_trigger_meta={
                "action_id": body.action_id,
                "change_class": resolved_change_class or (retry_contribution.context_seed.get("revision_scope") if retry_contribution else None),
                "build_record_id": resolved_artifact_version_id or (retry_contribution.context_seed.get("artifact_version_id") if retry_contribution else None),
                "build_family": resolved_artifact_kind or (retry_contribution.context_seed.get("artifact_kind") if retry_contribution else None),
                "workflow_sequence": refinement_decision.workflow_sequence if refinement_decision is not None else (retry_contribution.journey_id if retry_contribution else None),
            },
        )
    except ValueError as route_err:
        raise HTTPException(status_code=400, detail=str(route_err)) from route_err
    except Exception as route_err:
        logger.error("SessionRouter routing failed: %s", route_err, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to route workflow trigger") from route_err

    resolved_workflow_id = launch.workflow_id
    routing_decision = launch.routing_decision

    if persisted_change_request_id is not None and artifact_store is not None:
        try:
            await artifact_store.update_change_request_router_decision(
                app_id=artifact_app_id,
                change_request_id=persisted_change_request_id,
                router_decision={
                    "workflow_id": resolved_workflow_id,
                    "workflow_sequence": refinement_decision.workflow_sequence if refinement_decision is not None else None,
                    "requested_workflow_id": routing_decision.requested_workflow_id,
                    "explanation": routing_decision.explanation,
                    "is_full_restart": routing_decision.is_full_restart,
                    "rerouted_by_dependency": routing_decision.rerouted_by_dependency,
                    "execution_mode": "workflow",
                    "harness_decision": (
                        harness_decision.model_dump(mode="python") if harness_decision is not None else None
                    ),
                    # The build's state when the change began stays on record: it is what shows
                    # whether the build this route stales was a draft never accepted.
                    **({"baseline_state": proposal_baseline_state} if proposal_baseline_state is not None else {}),
                },
            )
        except Exception as persist_err:
            logger.warning("Failed to update ChangeRequest routing decision: %s", persist_err)

    if body.trigger_source == "manual" and launch.workflow_id == "AppReview" and build_registry_id:
        from mozaiksai.core.session.launcher import _PERSISTENCE_MANAGER

        try:
            recovered_review = await recover_saved_app_review(
                app_id=app_id, user_id=user_id,
                binding=RunBuildBinding.model_validate(launch.session_fields.get("run_build_binding")),
                registry=_get_app_registry_service(), artifact_store=get_artifact_store(),
                persistence=_PERSISTENCE_MANAGER, session_router=session_router,
                has_launch_overrides=bool(
                    body.context_variables or body.trigger_payload or body.source_chat_id or body.journey_id
                    or body.action_id or body.artifact_key or body.retry_failed
                ),
            )
        except ValueError as recovery_error:
            raise HTTPException(status_code=400, detail=str(recovery_error)) from recovery_error
        except Exception as recovery_error:
            logger.exception("Saved app review recovery failed build=%s", build_registry_id)
            raise HTTPException(status_code=500, detail="The saved review could not resume. Retry Continue in chat.") from recovery_error
        if recovered_review is not None:
            return {
                "execution_mode": "workflow", "chat_id": recovered_review["chat_id"],
                "workflow_id": "AppReview", "requested_workflow_id": launch.requested_workflow_id,
                "journey_id": launch.journey_id, "websocket_url": recovered_review["websocket_url"],
                "trigger_source": body.trigger_source, "routing_explanation": "Resume the saved app review",
                "rerouted_by_dependency": False, "harness_decision": None,
            }

    workflow_launch = await launch_prepared_workflow(launch)
    if invalidation_pending and not preserve_branding_source:
        # The launched run revises the selected build; a launch that failed
        # above raised before this and left the build as it was.
        await _persist_trigger_invalidation(
            orchestration_control, refinement_request=refinement_request, routing_decision=refinement_decision,
            change_request_id=persisted_change_request_id, artifact_store=artifact_store,
        )

    return _workflow_trigger_response(
        workflow_launch, harness_decision=harness_decision.model_dump(mode="python") if harness_decision is not None else None,
    )


def _workflow_trigger_response(workflow_launch: WorkflowLaunchResult, *, harness_decision: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "execution_mode": "workflow",
        "chat_id": workflow_launch.chat_id,
        "workflow_id": workflow_launch.workflow_id,
        "requested_workflow_id": workflow_launch.requested_workflow_id,
        "journey_id": workflow_launch.journey_id,
        "websocket_url": workflow_launch.websocket_url,
        "trigger_source": workflow_launch.trigger_source,
        "routing_explanation": workflow_launch.routing_explanation,
        "rerouted_by_dependency": workflow_launch.rerouted_by_dependency,
        "harness_decision": harness_decision,
    }


async def _resolve_preview_build(principal: UserPrincipal, build_registry_id: str) -> str:
    target_app_id, _ = await _resolve_studio_artifact_scope(principal, build_registry_id=build_registry_id)
    return target_app_id


async def _resolve_preview_artifact(
    principal: UserPrincipal, artifact_version_id: str, build_registry_id: str,
) -> tuple[str, dict[str, str | bytes]]:
    from mozaiksai.core.validation.compilation_inputs import resolve_pinned_workflow_bundle

    target_app_id, owner_user_id = await _resolve_studio_artifact_scope(principal, build_registry_id=build_registry_id)
    store = get_artifact_store()
    version = await store.get_build_record(app_id=target_app_id, build_record_id=artifact_version_id)
    if version is None or version.id != artifact_version_id or version.app_id != target_app_id:
        raise HTTPException(status_code=404, detail="Artifact not found")
    if version.build_family != "app_bundle":
        raise HTTPException(status_code=422, detail="Preview requires an app bundle")
    _artifact_build_binding(version, build_registry_id=build_registry_id, target_app_id=target_app_id)
    try:
        files, diagnostics = await read_artifact_bundle(version, include_binary=True)
        if any(item.get("blocking") for item in diagnostics):
            raise HTTPException(status_code=422, detail="Artifact contains unsafe or oversized preview files")
        pinned = await resolve_pinned_workflow_bundle(
            version, app_id=target_app_id, owner_user_id=owner_user_id, build_registry_id=build_registry_id, artifact_store=store,
        )
        if pinned is not None:
            await to_thread(add_preview_workflow_files, files, *pinned)
    except (OSError, ValueError, ContentNotFoundError, zipfile.BadZipFile) as exc:
        raise HTTPException(status_code=422, detail="Artifact archive is unavailable or failed integrity validation") from exc
    return target_app_id, files


app.include_router(create_sandbox_router(
    resolve_scope=_resolve_studio_preview_scope, resolve_build=_resolve_preview_build,
    resolve_artifact=_resolve_preview_artifact,
))


app.router.routes[:] = sorted(
    app.router.routes,
    key=lambda route: (
        0
        if getattr(route, "path", None) == "/api/shell-config"
        and getattr(getattr(route, "endpoint", None), "__module__", "") == __name__
        else 1
    ),
)
