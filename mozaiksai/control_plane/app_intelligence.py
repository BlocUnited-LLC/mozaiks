"""Index local app workspaces into the App Intelligence plane."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import zipfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mozaiksai.core.app_context import (
    AllowedOperation,
    AppContextIndex,
    AppContextMode,
    AppContextStaleStatus,
    AppContextVersion,
    ArtifactRef,
    OwnershipBoundary,
    OwnershipClass,
    SourceRef,
    SourceRefKind,
    ValidationSummary,
    collect_source_scan_file_map,
    default_context_graph_scan_policy,
    detect_frameworks_from_file_map,
    index_source_scan,
    persist_app_context_index,
    redact_source_text,
    register_app_context_version,
)
from mozaiksai.core.artifacts import (
    ArtifactFileManifestEntry,
    ArtifactLifecycleStatus,
    ArtifactStore,
    ArtifactValidationStatus,
    get_artifact_store,
)

APP_INTELLIGENCE_WORKSPACE_ARTIFACT_KEY = "app_intelligence_workspace"
APP_INTELLIGENCE_INDEX_SOURCE_WORKFLOW = "app_intelligence_index"
APP_INTELLIGENCE_SOURCE_REF_ID = "src_app_intelligence_workspace"
AppIntelligenceProgressCallback = Callable[[str, dict[str, Any]], Awaitable[None] | None]


@dataclass(frozen=True)
class AppIntelligenceContextRegistrationResult:
    app_id: str
    source_context_artifact_version_id: str
    app_intelligence_artifact_version_id: str
    app_context_version_id: str
    app_context_artifact_version_id: str
    graph_artifact_version_id: str
    indexed_file_count: int
    scan_health: dict[str, Any]
    health_report: dict[str, Any]
    warnings: list[str]


@dataclass(frozen=True)
class AppIntelligenceIndexResult:
    app_id: str
    app_bundle_artifact_version_id: str
    source_context_artifact_version_id: str
    app_intelligence_artifact_version_id: str
    app_context_version_id: str
    app_context_artifact_version_id: str
    graph_artifact_version_id: str
    artifact_path: str
    indexed_file_count: int
    scan_health: dict[str, Any]
    health_report: dict[str, Any]
    framework_detection: dict[str, Any]
    warnings: list[str]


async def index_workspace_app_intelligence(
    *,
    app_id: str,
    workspace_root: str | Path,
    artifact_store: ArtifactStore | None = None,
    workspace_key: str = APP_INTELLIGENCE_WORKSPACE_ARTIFACT_KEY,
    source_workflow: str = APP_INTELLIGENCE_INDEX_SOURCE_WORKFLOW,
    source_chat_id: str | None = None,
    scan_policy: dict[str, Any] | None = None,
    generated_artifacts_root: str | Path | None = None,
    make_current: bool = True,
    progress_callback: AppIntelligenceProgressCallback | None = None,
) -> AppIntelligenceIndexResult:
    """Create current App Intelligence artifacts and an AppContextVersion.

    The index is the canonical app-context bootstrap for generated and existing
    app workspaces. It produces a safe app_bundle evidence artifact,
    SourceContextBundle, AppContextGraph, AppIntelligenceSnapshot, and current
    AppContextVersion before agents plan or edit source.
    """
    resolved_app_id = str(app_id or "").strip()
    if not resolved_app_id:
        raise ValueError("app_id is required")
    root = Path(workspace_root).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"workspace_root does not exist or is not a directory: {root}")

    await _emit_progress(
        progress_callback,
        "workspace_scan",
        {"message": "Scanning selected source files.", "workspace_root": root.as_posix()},
    )
    policy = default_context_graph_scan_policy(scan_policy)
    scan_result = collect_source_scan_file_map([("", root)], policy=policy)
    if not scan_result.file_map:
        raise ValueError("App Intelligence indexing found no supported source files")

    await _emit_progress(
        progress_callback,
        "source_index",
        {
            "message": "Building redacted source context.",
            "selected_file_count": len(scan_result.file_map),
            "warnings": list(scan_result.warnings),
        },
    )
    indexed_at = datetime.now(UTC)
    safe_file_map = _redacted_scan_file_map(scan_result.file_map)
    framework_detection = detect_frameworks_from_file_map(safe_file_map).model_dump(mode="json")
    bundle_root = _generated_artifacts_root(generated_artifacts_root)
    index_token = indexed_at.strftime("%Y%m%d%H%M%S")
    index_dir = bundle_root / "app_intelligence" / _safe_segment(resolved_app_id) / index_token
    artifact_path = index_dir / "artifact.zip"
    files_manifest, bundle_sha256, bundle_size_bytes = _write_file_map_zip(
        file_map=safe_file_map,
        artifact_path=artifact_path,
    )

    store = artifact_store or get_artifact_store()
    app_bundle_artifact = await store.create_build_record(
        app_id=resolved_app_id,
        build_family="app_bundle",
        build_key=workspace_key,
        files_manifest=[entry.model_dump(mode="python") for entry in files_manifest],
        source_workflow=source_workflow,
        source_chat_id=source_chat_id,
        lifecycle_status=ArtifactLifecycleStatus.CURRENT if make_current else ArtifactLifecycleStatus.DRAFT,
        validation_status=ArtifactValidationStatus.PASSED,
        commit_metadata={
            "message": "App Intelligence workspace index",
            "source_workflow": source_workflow,
            "source_chat_id": source_chat_id,
            "metadata": {
                "artifact_path": artifact_path.as_posix(),
                "bundle_mode": "app_intelligence_workspace_index",
                "source_workspace_root": root.as_posix(),
                "bundle_sha256": bundle_sha256,
                "bundle_size_bytes": bundle_size_bytes,
                "framework_detection": framework_detection,
            },
        },
    )

    await _emit_progress(
        progress_callback,
        "symbol_parse",
        {
            "message": "Parsing symbols, imports, and source chunks.",
            "selected_file_count": len(safe_file_map),
        },
    )
    source_ref = SourceRef(
        source_ref_id=APP_INTELLIGENCE_SOURCE_REF_ID,
        kind=SourceRefKind.APP_ROOT,
        uri=root.as_uri(),
        ref=index_token,
        checksum=_file_map_checksum(safe_file_map),
        indexed_at=indexed_at,
        metadata={
            "build_family": "app_bundle",
            "build_key": workspace_key,
            "build_record_id": app_bundle_artifact.id,
            "scan_policy": policy.policy_id,
        },
    )
    source_index = index_source_scan(
        app_id=resolved_app_id,
        scan_result=scan_result,
        artifact_version_id=app_bundle_artifact.id,
        artifact_kind="app_bundle",
        artifact_key=workspace_key,
        source_ref=source_ref,
        indexed_at=indexed_at,
    )
    snapshot_detection = dict(source_index.app_intelligence_snapshot.architecture.get("framework_detection") or {})
    if snapshot_detection:
        framework_detection = snapshot_detection
    app_bundle_ref = ArtifactRef(
        artifact_kind="app_bundle",
        artifact_key=workspace_key,
        artifact_version_id=app_bundle_artifact.id,
        lifecycle_status=app_bundle_artifact.lifecycle_status.value,
        source_ref_id=source_ref.source_ref_id,
    )
    await _emit_progress(
        progress_callback,
        "context_graph",
        {
            "message": "Persisting graph relationships.",
            "node_count": len(source_index.app_context_graph.nodes),
            "edge_count": len(source_index.app_context_graph.edges),
        },
    )
    registration = await register_app_intelligence_context(
        source_index=source_index,
        artifact_store=store,
        source_workflow=source_workflow,
        source_chat_id=source_chat_id,
        make_current=make_current,
        context_version_id=_context_version_id(resolved_app_id, app_bundle_artifact.id),
        mode=AppContextMode.HYBRID,
        additional_artifact_refs=[app_bundle_ref],
        ownership_boundaries=_ownership_boundaries(safe_file_map, source_ref.source_ref_id),
        graph_path="app_context/app_intelligence/app_context_graph.json",
    )
    await _emit_progress(
        progress_callback,
        "app_intelligence",
        {
            "message": "Registering current app context version.",
            "app_context_version_id": registration.app_context_version_id,
            "indexed_file_count": registration.indexed_file_count,
        },
    )
    return AppIntelligenceIndexResult(
        app_id=resolved_app_id,
        app_bundle_artifact_version_id=app_bundle_artifact.id,
        source_context_artifact_version_id=registration.source_context_artifact_version_id,
        app_intelligence_artifact_version_id=registration.app_intelligence_artifact_version_id,
        app_context_version_id=registration.app_context_version_id,
        app_context_artifact_version_id=registration.app_context_artifact_version_id,
        graph_artifact_version_id=registration.graph_artifact_version_id,
        artifact_path=artifact_path.as_posix(),
        indexed_file_count=registration.indexed_file_count,
        scan_health=registration.scan_health,
        health_report=registration.health_report,
        framework_detection=framework_detection,
        warnings=registration.warnings,
    )


async def _emit_progress(
    callback: AppIntelligenceProgressCallback | None,
    phase_id: str,
    details: dict[str, Any],
) -> None:
    if callback is None:
        return
    result = callback(phase_id, dict(details or {}))
    if hasattr(result, "__await__"):
        await result  # type: ignore[misc]


async def register_app_intelligence_context(
    *,
    source_index: AppContextIndex,
    artifact_store: ArtifactStore | None = None,
    source_workflow: str | None = None,
    source_chat_id: str | None = None,
    make_current: bool = True,
    context_version_id: str | None = None,
    mode: AppContextMode = AppContextMode.HYBRID,
    additional_artifact_refs: list[ArtifactRef] | None = None,
    ownership_boundaries: list[OwnershipBoundary] | None = None,
    source_context_path: str = "app_context/source_context/source_context_bundle.json",
    graph_path: str = "app_context/app_intelligence/app_context_graph.json",
    intelligence_path: str = "app_context/intelligence/app_intelligence_snapshot.json",
) -> AppIntelligenceContextRegistrationResult:
    """Persist an AppContextIndex and register it as current App Intelligence.

    Workflow preload, Studio indexing, CLI dogfooding, and future hosted
    source-control ingestion should converge here once they have a bounded
    SourceScanResult and AppContextIndex. Raw source stays in the persisted
    SourceContextBundle; agents receive catalogs and retrieve exact files only
    through source-context tools.
    """
    if not source_index.app_id:
        raise ValueError("source_index.app_id is required")
    if not source_index.safe_file_map:
        raise ValueError("source_index contains no indexed files")

    store = artifact_store or get_artifact_store()
    persisted_index = await persist_app_context_index(
        index=source_index,
        artifact_store=store,
        source_workflow=source_workflow,
        source_chat_id=source_chat_id,
        source_context_path=source_context_path,
        graph_path=graph_path,
        intelligence_path=intelligence_path,
    )
    source_ref = source_index.source_ref
    source_ref_id = source_ref.source_ref_id

    context_version = AppContextVersion(
        context_version_id=context_version_id
        or _context_version_id(source_index.app_id, persisted_index.intelligence_artifact.id),
        app_id=source_index.app_id,
        mode=mode,
        source_refs=[source_ref],
        artifact_refs=[
            *list(additional_artifact_refs or []),
            ArtifactRef(
                artifact_kind="source_context_bundle",
                artifact_key="source_context_bundle",
                artifact_version_id=persisted_index.source_context_artifact.id,
                lifecycle_status=persisted_index.source_context_artifact.lifecycle_status.value,
                source_ref_id=source_ref_id,
            ),
            ArtifactRef(
                artifact_kind="app_context_graph",
                artifact_key="app_context_graph",
                artifact_version_id=persisted_index.graph_artifact.id,
                lifecycle_status=persisted_index.graph_artifact.lifecycle_status.value,
                source_ref_id=source_ref_id,
            ),
            ArtifactRef(
                artifact_kind="app_intelligence_snapshot",
                artifact_key="app_intelligence_snapshot",
                artifact_version_id=persisted_index.intelligence_artifact.id,
                lifecycle_status=persisted_index.intelligence_artifact.lifecycle_status.value,
                source_ref_id=source_ref_id,
            ),
        ],
        graph_snapshot_ref=persisted_index.graph_artifact.id,
        ownership_boundaries=ownership_boundaries
        if ownership_boundaries is not None
        else _ownership_boundaries(source_index.safe_file_map, source_ref_id),
        stale_status=AppContextStaleStatus.CURRENT,
        validation_summary=ValidationSummary(
            status="passed",
            evidence_refs=[
                *[
                    ref.artifact_version_id
                    for ref in additional_artifact_refs or []
                    if ref.artifact_version_id
                ],
                persisted_index.source_context_artifact.id,
                persisted_index.graph_artifact.id,
                persisted_index.intelligence_artifact.id,
            ],
            warnings=[
                *list(source_index.scan_result.warnings),
                *source_index.health_report.get("warnings", []),
                *source_index.health_report.get("blockers", []),
            ],
        ),
    )
    registered = await register_app_context_version(
        context_version,
        artifact_store=store,
        source_workflow=source_workflow,
        source_chat_id=source_chat_id,
        make_current=make_current,
    )
    return AppIntelligenceContextRegistrationResult(
        app_id=source_index.app_id,
        source_context_artifact_version_id=persisted_index.source_context_artifact.id,
        app_intelligence_artifact_version_id=persisted_index.intelligence_artifact.id,
        app_context_version_id=registered.context_version.context_version_id,
        app_context_artifact_version_id=registered.artifact_version.id,
        graph_artifact_version_id=persisted_index.graph_artifact.id,
        indexed_file_count=len(source_index.safe_file_map),
        scan_health=source_index.scan_health,
        health_report=source_index.health_report,
        warnings=list(source_index.scan_result.warnings),
    )


def _write_file_map_zip(
    *,
    file_map: dict[str, str],
    artifact_path: Path,
) -> tuple[list[ArtifactFileManifestEntry], str, int]:
    try:
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RuntimeError(
            f"APP_INTELLIGENCE_INDEX_ZIP_FAILED: could not create index directory {artifact_path.parent} — {exc}"
        ) from exc
    manifest: list[ArtifactFileManifestEntry] = []
    try:
        with zipfile.ZipFile(artifact_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(file_map):
                raw = str(file_map[path]).encode("utf-8")
                archive.writestr(path, raw)
                manifest.append(
                    ArtifactFileManifestEntry(
                        path=path,
                        sha256=hashlib.sha256(raw).hexdigest(),
                        size_bytes=len(raw),
                        content_type=mimetypes.guess_type(path)[0] or "text/plain",
                    )
                )
    except OSError as exc:
        raise RuntimeError(
            f"APP_INTELLIGENCE_INDEX_ZIP_FAILED: could not write index zip at {artifact_path} — {exc}"
        ) from exc
    bundle = artifact_path.read_bytes()
    return manifest, hashlib.sha256(bundle).hexdigest(), len(bundle)


def _redacted_scan_file_map(file_map: dict[str, str]) -> dict[str, str]:
    return {
        path: redact_source_text(path=path, content=content)
        for path, content in sorted((file_map or {}).items())
    }


def _ownership_boundaries(file_map: dict[str, str], source_ref_id: str) -> list[OwnershipBoundary]:
    roots: list[str] = []
    for path in sorted(file_map):
        top = path.split("/", 1)[0]
        if top and top not in roots:
            roots.append(top)
    allowed = [AllowedOperation.INSPECT, AllowedOperation.INDEX, AllowedOperation.STAGE_PATCH]
    return [
        OwnershipBoundary(
            path_or_artifact=f"{root}/",
            ownership=OwnershipClass.MIGRATED_OWNED,
            source_ref=source_ref_id,
            allowed_operations=allowed,
            requires_review=True,
            notes="App Intelligence indexed source selected for graph-aware scoped refinement.",
        )
        for root in roots[:24]
    ]


def _generated_artifacts_root(base: str | Path | None) -> Path:
    if base is not None:
        candidate = Path(base)
    else:
        candidate = Path(os.getenv("MOZAIKS_GENERATED_ARTIFACTS_PATH", "generated").strip() or "generated")
    if not candidate.is_absolute():
        candidate = _repo_root() / candidate
    return candidate.resolve()


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in [here] + list(here.parents):
        if (parent / "mozaiksai").is_dir():
            return parent
    return here.parents[-1]


def _safe_segment(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip(".-") or "app"


def _context_version_id(app_id: str, artifact_version_id: str) -> str:
    raw = f"{app_id}:{artifact_version_id}"
    token = re.sub(r"[^A-Za-z0-9_]+", "_", raw).strip("_").lower()
    return f"ctx_{token[:80]}"


def _file_map_checksum(file_map: dict[str, str]) -> str:
    payload = json.dumps(
        {path: file_map[path] for path in sorted(file_map)},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


__all__ = [
    "APP_INTELLIGENCE_INDEX_SOURCE_WORKFLOW",
    "APP_INTELLIGENCE_SOURCE_REF_ID",
    "APP_INTELLIGENCE_WORKSPACE_ARTIFACT_KEY",
    "AppIntelligenceContextRegistrationResult",
    "AppIntelligenceIndexResult",
    "index_workspace_app_intelligence",
    "register_app_intelligence_context",
]
