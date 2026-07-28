"""Register a local workspace snapshot as a Context Graph-backed refinement target."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mozaiksai.core.app_context import (
    AllowedOperation,
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


@dataclass(frozen=True)
class WorkspaceSnapshotRegistrationResult:
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
    warnings: list[str]


async def register_workspace_snapshot(
    *,
    app_id: str,
    workspace_root: str | Path,
    artifact_store: ArtifactStore | None = None,
    artifact_key: str = "workspace_snapshot",
    source_workflow: str = "workspace_snapshot",
    source_chat_id: str | None = None,
    scan_policy: dict[str, Any] | None = None,
    generated_artifacts_root: str | Path | None = None,
    make_current: bool = True,
) -> WorkspaceSnapshotRegistrationResult:
    """Create a current app artifact and AppContextVersion from a local workspace.

    The snapshot is a code-context artifact, not a direct source mutation path. It
    gives the Refinement Engine a durable artifact_version_id plus a deterministic
    Context Graph for scoped patch planning.
    """
    resolved_app_id = str(app_id or "").strip()
    if not resolved_app_id:
        raise ValueError("app_id is required")
    root = Path(workspace_root).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"workspace_root does not exist or is not a directory: {root}")

    policy = default_context_graph_scan_policy(scan_policy)
    scan_result = collect_source_scan_file_map([("", root)], policy=policy)
    if not scan_result.file_map:
        raise ValueError("workspace snapshot found no supported source files")

    indexed_at = datetime.now(UTC)
    safe_file_map = _redacted_scan_file_map(scan_result.file_map)
    bundle_root = _generated_artifacts_root(generated_artifacts_root)
    snapshot_token = indexed_at.strftime("%Y%m%d%H%M%S")
    snapshot_dir = bundle_root / "workspace_snapshots" / _safe_segment(resolved_app_id) / snapshot_token
    artifact_path = snapshot_dir / "artifact.zip"
    files_manifest, bundle_sha256, bundle_size_bytes = _write_file_map_zip(
        file_map=safe_file_map,
        artifact_path=artifact_path,
    )

    store = artifact_store or get_artifact_store()
    app_bundle_artifact = await store.create_artifact_version(
        app_id=resolved_app_id,
        artifact_kind="app_bundle",
        artifact_key=artifact_key,
        files_manifest=[entry.model_dump(mode="python") for entry in files_manifest],
        source_workflow=source_workflow,
        source_chat_id=source_chat_id,
        lifecycle_status=ArtifactLifecycleStatus.CURRENT if make_current else ArtifactLifecycleStatus.DRAFT,
        validation_status=ArtifactValidationStatus.PASSED,
        commit_metadata={
            "message": "Workspace code-context snapshot",
            "source_workflow": source_workflow,
            "source_chat_id": source_chat_id,
            "metadata": {
                "artifact_path": artifact_path.as_posix(),
                "bundle_mode": "workspace_code_context_snapshot",
                "source_workspace_root": root.as_posix(),
                "bundle_sha256": bundle_sha256,
                "bundle_size_bytes": bundle_size_bytes,
            },
        },
    )

    source_ref = SourceRef(
        source_ref_id="src_workspace_snapshot",
        kind=SourceRefKind.APP_ROOT,
        uri=root.as_uri(),
        ref=snapshot_token,
        checksum=_file_map_checksum(safe_file_map),
        indexed_at=indexed_at,
        metadata={
            "artifact_kind": "app_bundle",
            "artifact_key": artifact_key,
            "artifact_version_id": app_bundle_artifact.id,
            "scan_policy": policy.policy_id,
        },
    )
    source_index = index_source_scan(
        app_id=resolved_app_id,
        scan_result=scan_result,
        artifact_version_id=app_bundle_artifact.id,
        artifact_kind="app_bundle",
        artifact_key=artifact_key,
        source_ref=source_ref,
        indexed_at=indexed_at,
    )
    scan_health = source_index.scan_health
    health_report = source_index.health_report
    persisted_index = await persist_app_context_index(
        index=source_index,
        artifact_store=store,
        source_workflow=source_workflow,
        source_chat_id=source_chat_id,
        graph_path="app_context/workspace_snapshot/app_context_graph.json",
    )

    context_version = AppContextVersion(
        context_version_id=_context_version_id(resolved_app_id, app_bundle_artifact.id),
        app_id=resolved_app_id,
        mode=AppContextMode.HYBRID,
        source_refs=[source_ref],
        artifact_refs=[
            ArtifactRef(
                artifact_kind="app_bundle",
                artifact_key=artifact_key,
                artifact_version_id=app_bundle_artifact.id,
                lifecycle_status=app_bundle_artifact.lifecycle_status.value,
                source_ref_id=source_ref.source_ref_id,
            ),
            ArtifactRef(
                artifact_kind="source_context_bundle",
                artifact_key="source_context_bundle",
                artifact_version_id=persisted_index.source_context_artifact.id,
                lifecycle_status=persisted_index.source_context_artifact.lifecycle_status.value,
                source_ref_id=source_ref.source_ref_id,
            ),
            ArtifactRef(
                artifact_kind="app_context_graph",
                artifact_key="app_context_graph",
                artifact_version_id=persisted_index.graph_artifact.id,
                lifecycle_status=persisted_index.graph_artifact.lifecycle_status.value,
                source_ref_id=source_ref.source_ref_id,
            ),
            ArtifactRef(
                artifact_kind="app_intelligence_snapshot",
                artifact_key="app_intelligence_snapshot",
                artifact_version_id=persisted_index.intelligence_artifact.id,
                lifecycle_status=persisted_index.intelligence_artifact.lifecycle_status.value,
                source_ref_id=source_ref.source_ref_id,
            ),
        ],
        graph_snapshot_ref=persisted_index.graph_artifact.id,
        ownership_boundaries=_ownership_boundaries(safe_file_map, source_ref.source_ref_id),
        stale_status=AppContextStaleStatus.CURRENT,
        validation_summary=ValidationSummary(
            status="passed",
            evidence_refs=[
                app_bundle_artifact.id,
                persisted_index.source_context_artifact.id,
                persisted_index.graph_artifact.id,
                persisted_index.intelligence_artifact.id,
            ],
            warnings=[*list(scan_result.warnings), *health_report.get("warnings", []), *health_report.get("blockers", [])],
        ),
    )
    registered = await register_app_context_version(
        context_version,
        artifact_store=store,
        source_workflow=source_workflow,
        source_chat_id=source_chat_id,
        make_current=make_current,
    )
    return WorkspaceSnapshotRegistrationResult(
        app_id=resolved_app_id,
        app_bundle_artifact_version_id=app_bundle_artifact.id,
        source_context_artifact_version_id=persisted_index.source_context_artifact.id,
        app_intelligence_artifact_version_id=persisted_index.intelligence_artifact.id,
        app_context_version_id=registered.context_version.context_version_id,
        app_context_artifact_version_id=registered.artifact_version.id,
        graph_artifact_version_id=persisted_index.graph_artifact.id,
        artifact_path=artifact_path.as_posix(),
        indexed_file_count=len(scan_result.file_map),
        scan_health=scan_health,
        health_report=health_report,
        warnings=list(scan_result.warnings),
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
            f"WORKSPACE_SNAPSHOT_ZIP_FAILED: could not create snapshot directory {artifact_path.parent} — {exc}"
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
            f"WORKSPACE_SNAPSHOT_ZIP_FAILED: could not write snapshot zip at {artifact_path} — {exc}"
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
            notes="Workspace snapshot source selected for graph-aware scoped refinement.",
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


__all__ = ["WorkspaceSnapshotRegistrationResult", "register_workspace_snapshot"]
