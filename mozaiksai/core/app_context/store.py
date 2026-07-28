"""Persistence helpers for AppContextVersion management state."""

from __future__ import annotations

import hashlib
import json
import logging
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

from mozaiksai.core.artifacts.models import (
    ArtifactLifecycleStatus,
    ArtifactValidationStatus,
    ArtifactVersionDoc,
)
from mozaiksai.core.artifacts.store import ArtifactStore, get_artifact_store

from .indexer import index_file_map, persist_app_context_index
from .models import (
    AdoptionPath,
    AdoptionPhase,
    AdoptionPlan,
    AllowedOperation,
    AppContextGraph,
    AppContextGraphEdge,
    AppContextGraphNode,
    AppContextMode,
    AppContextStaleStatus,
    AppContextSurfaceIndexes,
    AppContextVersion,
    ApplicationInventory,
    ArtifactRef,
    GraphEdgeType,
    GraphNodeType,
    IntegrationInventory,
    IntegrationReadinessStatus,
    OwnershipBoundary,
    OwnershipClass,
    RiskItem,
    RiskReport,
    SourceRef,
    SourceRefKind,
    SurfaceRef,
    ValidationSummary,
)
from .scan_policy import safe_scan_relpath, skip_reason_for_path

APP_CONTEXT_VERSION_ARTIFACT_KIND = "app_context_version"
APP_CONTEXT_VERSION_ARTIFACT_KEY = "app_context_version"

BROWNFIELD_APP_CONTEXT_REQUIRED_ARTIFACT_KINDS = (
    "application_inventory",
    "ownership_boundary",
    "integration_inventory",
    "risk_report",
    "adoption_plan",
    "brownfield_registration",
    "app_context_graph",
)

GREENFIELD_APP_CONTEXT_ARTIFACT_KINDS = (
    "application_inventory",
    "ownership_boundary",
    "integration_inventory",
    "risk_report",
    "adoption_plan",
    "app_context_graph",
)


@dataclass(frozen=True)
class RegisteredAppContextVersion:
    context_version: AppContextVersion
    artifact_version: ArtifactVersionDoc


@dataclass(frozen=True)
class GreenfieldAppContextDrafts:
    app_id: str
    application_inventory: ApplicationInventory
    ownership_boundaries: list[OwnershipBoundary]
    integration_inventory: list[IntegrationInventory]
    risk_report: RiskReport
    adoption_plan: AdoptionPlan
    app_context_graph: AppContextGraph

    def as_artifact_payloads(self) -> dict[str, Any]:
        return {
            "application_inventory": self.application_inventory.model_dump(mode="json"),
            "ownership_boundary": {
                "app_id": self.app_id,
                "ownership_boundaries": [
                    boundary.model_dump(mode="json") for boundary in self.ownership_boundaries
                ],
            },
            "integration_inventory": {
                "app_id": self.app_id,
                "integrations": [
                    integration.model_dump(mode="json") for integration in self.integration_inventory
                ],
            },
            "risk_report": self.risk_report.model_dump(mode="json"),
            "adoption_plan": self.adoption_plan.model_dump(mode="json"),
            "app_context_graph": self.app_context_graph.model_dump(mode="json"),
        }


def build_brownfield_app_context_version(
    *,
    app_id: str,
    artifact_version_refs: dict[str, str],
    source_refs: list[SourceRef],
    ownership_boundaries: list[OwnershipBoundary],
    application_inventory: ApplicationInventory | None = None,
    context_version_id: str | None = None,
    indexed_at: datetime | None = None,
    validation_summary: ValidationSummary | None = None,
    stale_status: AppContextStaleStatus = AppContextStaleStatus.CURRENT,
) -> AppContextVersion:
    """Build a brownfield AppContextVersion from persisted context artifact refs."""
    missing = [
        artifact_kind
        for artifact_kind in BROWNFIELD_APP_CONTEXT_REQUIRED_ARTIFACT_KINDS
        if not str(artifact_version_refs.get(artifact_kind) or "").strip()
    ]
    if missing:
        raise ValueError(f"Missing required app-context artifact refs: {', '.join(missing)}")

    resolved_app_id = str(app_id or "").strip()
    if not resolved_app_id:
        raise ValueError("app_id is required")

    artifact_refs = [
        ArtifactRef(
            artifact_kind=artifact_kind,
            artifact_key=artifact_kind,
            artifact_version_id=str(artifact_version_refs[artifact_kind]),
            lifecycle_status=ArtifactLifecycleStatus.DRAFT.value,
        )
        for artifact_kind in BROWNFIELD_APP_CONTEXT_REQUIRED_ARTIFACT_KINDS
    ]
    artifact_refs.extend(
        ArtifactRef(
            artifact_kind=artifact_kind,
            artifact_key=artifact_kind,
            artifact_version_id=str(version_id),
            lifecycle_status=ArtifactLifecycleStatus.DRAFT.value,
        )
        for artifact_kind, version_id in sorted(artifact_version_refs.items())
        if artifact_kind not in BROWNFIELD_APP_CONTEXT_REQUIRED_ARTIFACT_KINDS
        and str(version_id or "").strip()
    )
    graph_snapshot_ref = artifact_version_refs.get("app_context_graph")
    resolved_indexed_at = indexed_at or datetime.now(UTC)
    resolved_context_version_id = (
        str(context_version_id).strip()
        if context_version_id
        else f"ctx_{resolved_app_id}_{resolved_indexed_at.strftime('%Y%m%d%H%M%S')}"
    )

    return AppContextVersion(
        context_version_id=resolved_context_version_id,
        app_id=resolved_app_id,
        mode=AppContextMode.BROWNFIELD,
        source_refs=source_refs,
        artifact_refs=artifact_refs,
        graph_snapshot_ref=graph_snapshot_ref,
        ownership_boundaries=ownership_boundaries,
        surface_indexes=_surface_indexes_from_inventory(application_inventory),
        indexed_at=resolved_indexed_at,
        stale_status=stale_status,
        validation_summary=validation_summary
        or ValidationSummary(
            status="pending",
            warnings=["Context has not yet been consumed by control-plane validation."],
        ),
    )


def build_greenfield_app_context_from_app_bundle(
    *,
    app_bundle_artifact: ArtifactVersionDoc | Any,
    files_manifest: list[dict[str, Any]] | None = None,
    app_id: str | None = None,
    indexed_at: datetime | None = None,
) -> GreenfieldAppContextDrafts:
    """Derive greenfield app context from an app_bundle ArtifactVersion manifest."""
    resolved_app_id = str(app_id or getattr(app_bundle_artifact, "app_id", "") or "").strip()
    if not resolved_app_id:
        raise ValueError("app_id is required")
    if getattr(app_bundle_artifact, "artifact_kind", None) != "app_bundle":
        raise ValueError("greenfield app context requires an app_bundle artifact")

    artifact_id = str(getattr(app_bundle_artifact, "id", "") or "")
    if not artifact_id:
        raise ValueError("app_bundle artifact id is required")

    resolved_indexed_at = indexed_at or datetime.now(UTC)
    manifest_entries = _manifest_entries(
        files_manifest if files_manifest is not None else getattr(app_bundle_artifact, "files_manifest", [])
    )
    manifest_by_path = _manifest_entries_by_path(manifest_entries)
    normalized_paths = sorted(manifest_by_path)
    source_ref = SourceRef(
        source_ref_id="src_app_bundle",
        kind=SourceRefKind.GENERATED_BUNDLE,
        uri=f"artifact-version://{artifact_id}",
        checksum=_bundle_manifest_checksum(manifest_entries),
        indexed_at=resolved_indexed_at,
        metadata={
            "artifact_kind": "app_bundle",
            "artifact_key": str(getattr(app_bundle_artifact, "artifact_key", "app_bundle")),
            "artifact_version_id": artifact_id,
        },
    )

    integrations = _greenfield_integrations(normalized_paths)
    inventory = ApplicationInventory(
        app_id=resolved_app_id,
        source_refs=[source_ref],
        services=[],
        routes=_greenfield_routes(normalized_paths, source_ref.source_ref_id, manifest_by_path),
        api_endpoints=_greenfield_api_endpoints(normalized_paths, source_ref.source_ref_id),
        pages=_greenfield_pages(normalized_paths, source_ref.source_ref_id, manifest_by_path),
        components=_greenfield_components(normalized_paths, source_ref.source_ref_id),
        data_entities=_greenfield_data_entities(normalized_paths, source_ref.source_ref_id, manifest_by_path),
        modules=_greenfield_modules(normalized_paths, source_ref.source_ref_id, manifest_by_path),
        workflows=_greenfield_workflows(normalized_paths, source_ref.source_ref_id, manifest_by_path),
        integrations=integrations,
        deployment_boundaries=_greenfield_config_surfaces(normalized_paths, source_ref.source_ref_id),
    )
    ownership_boundaries = _greenfield_ownership_boundaries(
        paths=normalized_paths,
        integrations=integrations,
        source_ref_id=source_ref.source_ref_id,
    )
    risk_report = _greenfield_risk_report(normalized_paths, integrations)
    adoption_plan = AdoptionPlan(
        recommended_path=AdoptionPath.AUGMENT,
        phases=[
            AdoptionPhase(
                phase_id="generated_app_refinement",
                label="Generated app refinement",
                goals=["Continue governed refinement of Mozaiks-generated app surfaces."],
                actions=["Use current AppContextVersion for impact analysis before staging changes."],
                required_human_decisions=["Approve staged patches before promotion."],
            )
        ],
        candidate_overlays=[f"app_bundle:{artifact_id}"],
        human_decisions_required=["Approve generated app changes before promotion."],
        not_in_scope=["Existing source takeover or external system mutation."],
    )
    graph = _greenfield_app_context_graph(
        app_id=resolved_app_id,
        source_ref=source_ref,
        artifact_version_id=artifact_id,
        paths=normalized_paths,
        manifest_by_path=manifest_by_path,
        inventory=inventory,
        risk_report=risk_report,
        indexed_at=resolved_indexed_at,
    )
    return GreenfieldAppContextDrafts(
        app_id=resolved_app_id,
        application_inventory=inventory,
        ownership_boundaries=ownership_boundaries,
        integration_inventory=integrations,
        risk_report=risk_report,
        adoption_plan=adoption_plan,
        app_context_graph=graph,
    )


async def register_greenfield_app_context_version(
    *,
    app_bundle_artifact: ArtifactVersionDoc | Any,
    artifact_store: ArtifactStore | None = None,
    files_manifest: list[dict[str, Any]] | None = None,
    source_workflow: str | None = None,
    source_chat_id: str | None = None,
    make_current: bool = True,
) -> RegisteredAppContextVersion:
    """Persist greenfield app-context artifacts and register a current AppContextVersion."""
    store = artifact_store or get_artifact_store()
    drafts = build_greenfield_app_context_from_app_bundle(
        app_bundle_artifact=app_bundle_artifact,
        files_manifest=files_manifest,
    )
    source_file_map = _greenfield_source_file_map_from_artifact(app_bundle_artifact)
    source_index = (
        index_file_map(
            app_id=drafts.app_id,
            artifact_version_id=str(app_bundle_artifact.id),
            artifact_kind="app_bundle",
            artifact_key=str(getattr(app_bundle_artifact, "artifact_key", "app_bundle")),
            file_map=source_file_map,
            source="greenfield_app_bundle",
            source_ref=drafts.application_inventory.source_refs[0],
        )
        if source_file_map
        else None
    )
    payloads = drafts.as_artifact_payloads()
    artifact_version_refs: dict[str, str] = {}
    if source_index is not None:
        persisted_source_index = await persist_app_context_index(
            index=source_index,
            artifact_store=store,
            source_workflow=source_workflow,
            source_chat_id=source_chat_id,
            graph_path="app_context/greenfield/app_context_graph.json",
        )
        artifact_version_refs["source_context_bundle"] = persisted_source_index.source_context_artifact.id
        artifact_version_refs["app_context_graph"] = persisted_source_index.graph_artifact.id
        artifact_version_refs["app_intelligence_snapshot"] = persisted_source_index.intelligence_artifact.id

    for artifact_kind in GREENFIELD_APP_CONTEXT_ARTIFACT_KINDS:
        if artifact_kind == "app_context_graph" and source_index is not None:
            continue
        payload = payloads[artifact_kind]
        created = await _persist_context_payload_artifact(
            app_id=drafts.app_id,
            artifact_kind=artifact_kind,
            artifact_key=artifact_kind,
            payload=payload,
            path=f"app_context/greenfield/{artifact_kind}.json",
            artifact_store=store,
            source_workflow=source_workflow,
            source_chat_id=source_chat_id,
        )
        artifact_version_refs[artifact_kind] = created.id

    app_bundle_ref = ArtifactRef(
        artifact_kind="app_bundle",
        artifact_key=str(getattr(app_bundle_artifact, "artifact_key", "app_bundle")),
        artifact_version_id=str(app_bundle_artifact.id),
        lifecycle_status=_lifecycle_value(getattr(app_bundle_artifact, "lifecycle_status", None)),
        source_ref_id="src_app_bundle",
    )
    context_version = AppContextVersion(
        context_version_id=_surface_id(
            "ctx",
            f"{drafts.app_id}_{getattr(app_bundle_artifact, 'id', 'app_bundle')}",
        ),
        app_id=drafts.app_id,
        mode=AppContextMode.GREENFIELD,
        source_refs=drafts.application_inventory.source_refs,
        artifact_refs=[
            app_bundle_ref,
            *[
                ArtifactRef(
                    artifact_kind=artifact_kind,
                    artifact_key=artifact_kind,
                    artifact_version_id=version_id,
                    lifecycle_status=ArtifactLifecycleStatus.DRAFT.value,
                )
                for artifact_kind, version_id in artifact_version_refs.items()
            ],
        ],
        graph_snapshot_ref=artifact_version_refs.get("app_context_graph"),
        ownership_boundaries=drafts.ownership_boundaries,
        surface_indexes=_surface_indexes_from_inventory(drafts.application_inventory),
        stale_status=AppContextStaleStatus.CURRENT,
        validation_summary=ValidationSummary(
            status="pending",
            warnings=[
                "Greenfield app context has not yet been consumed by control-plane validation.",
                *(
                    []
                    if source_index is not None
                    else ["Greenfield source context was unavailable; registration used manifest-only graph context."]
                ),
            ],
        ),
    )
    return await register_app_context_version(
        context_version,
        artifact_store=store,
        source_workflow=source_workflow,
        source_chat_id=source_chat_id,
        make_current=make_current,
    )


def _greenfield_source_file_map_from_artifact(app_bundle_artifact: ArtifactVersionDoc | Any) -> dict[str, str]:
    metadata = _artifact_metadata(app_bundle_artifact)
    workspace_dir = metadata.get("workspace_dir")
    if workspace_dir:
        root = Path(str(workspace_dir)).expanduser()
        if root.exists() and root.is_dir():
            return _read_greenfield_source_dir(root)

    artifact_path = metadata.get("artifact_path")
    if artifact_path:
        path = Path(str(artifact_path)).expanduser()
        if path.exists() and path.is_file() and path.suffix.lower() == ".zip":
            return _read_greenfield_source_zip(path)

    return {}


def _artifact_metadata(artifact: ArtifactVersionDoc | Any) -> dict[str, Any]:
    commit_metadata = getattr(artifact, "commit_metadata", None)
    metadata = getattr(commit_metadata, "metadata", None)
    if isinstance(metadata, dict):
        return dict(metadata)
    if isinstance(artifact, dict):
        raw_commit = artifact.get("commit_metadata")
        if isinstance(raw_commit, dict) and isinstance(raw_commit.get("metadata"), dict):
            return dict(raw_commit["metadata"])
    return {}


def _read_greenfield_source_dir(root: Path) -> dict[str, str]:
    file_map: dict[str, str] = {}
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        try:
            rel = path.relative_to(root).as_posix()
        except Exception:
            continue
        normalized = _normalize_indexed_source_path(rel)
        if normalized is None:
            continue
        try:
            file_map[normalized] = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
    return file_map


def _read_greenfield_source_zip(path: Path) -> dict[str, str]:
    file_map: dict[str, str] = {}
    try:
        with zipfile.ZipFile(path, "r") as archive:
            for info in sorted(archive.infolist(), key=lambda item: item.filename):
                if info.is_dir():
                    continue
                normalized = _normalize_indexed_source_path(info.filename)
                if normalized is None:
                    continue
                try:
                    file_map[normalized] = archive.read(info.filename).decode("utf-8", errors="ignore")
                except Exception:
                    continue
    except zipfile.BadZipFile:
        return {}
    return file_map


def _normalize_indexed_source_path(path: Any) -> str | None:
    normalized = _normalize_app_bundle_path(path)
    safe = safe_scan_relpath(normalized)
    if safe is None or skip_reason_for_path(safe):
        return None
    return safe


async def register_app_context_version(
    context_version: AppContextVersion,
    *,
    artifact_store: ArtifactStore | None = None,
    source_workflow: str | None = None,
    source_chat_id: str | None = None,
    make_current: bool = False,
) -> RegisteredAppContextVersion:
    """Persist an AppContextVersion as an ArtifactVersion."""
    store = artifact_store or get_artifact_store()
    payload = context_version.model_dump(mode="json")
    raw = _json_bytes(payload)

    artifact_version = await store.create_artifact_version(
        app_id=context_version.app_id,
        artifact_kind=APP_CONTEXT_VERSION_ARTIFACT_KIND,
        artifact_key=APP_CONTEXT_VERSION_ARTIFACT_KEY,
        files_manifest=[
            {
                "path": f"app_context/{context_version.context_version_id}.json",
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size_bytes": len(raw),
                "content_type": "application/json",
            }
        ],
        source_workflow=source_workflow,
        source_chat_id=source_chat_id,
        lifecycle_status=ArtifactLifecycleStatus.DRAFT,
        validation_status=ArtifactValidationStatus.PENDING,
        commit_metadata={
            "message": f"AppContextVersion: {context_version.context_version_id}",
            "source_workflow": source_workflow,
            "source_chat_id": source_chat_id,
            "metadata": {
                "summary_payload": payload,
                "summary_format": "json",
                "artifact_contract": "mozaiksai/core/app_context/models.py",
                "source_workflow": source_workflow,
            },
        },
    )

    if make_current:
        current = await set_current_app_context_version(
            app_id=context_version.app_id,
            artifact_version_id=artifact_version.id,
            artifact_store=store,
        )
        if current is not None:
            artifact_version = current

    return RegisteredAppContextVersion(
        context_version=context_version,
        artifact_version=artifact_version,
    )


async def set_current_app_context_version(
    *,
    app_id: str,
    artifact_version_id: str,
    artifact_store: ArtifactStore | None = None,
) -> ArtifactVersionDoc | None:
    """Mark one AppContextVersion artifact as CURRENT for an app."""
    store = artifact_store or get_artifact_store()
    artifact = await store.get_artifact_version(
        app_id=app_id,
        artifact_version_id=artifact_version_id,
    )
    if artifact is None:
        return None
    if artifact.artifact_kind != APP_CONTEXT_VERSION_ARTIFACT_KIND:
        raise ValueError(
            "current app context selection requires an app_context_version artifact"
        )
    return await store.accept_artifact_version(
        app_id=app_id,
        artifact_version_id=artifact_version_id,
    )


async def get_app_context_version(
    *,
    app_id: str,
    context_version_id: str | None = None,
    artifact_version_id: str | None = None,
    artifact_store: ArtifactStore | None = None,
) -> AppContextVersion | None:
    """Load an AppContextVersion by canonical context id or artifact version id."""
    resolved_app_id = str(app_id or "").strip()
    if not resolved_app_id:
        raise ValueError("app_id is required")
    resolved_context_version_id = str(context_version_id or "").strip()
    resolved_artifact_version_id = str(artifact_version_id or "").strip()
    if not resolved_context_version_id and not resolved_artifact_version_id:
        raise ValueError("context_version_id or artifact_version_id is required")

    store = artifact_store or get_artifact_store()
    if resolved_artifact_version_id:
        artifact = await store.get_artifact_version(
            app_id=resolved_app_id,
            artifact_version_id=resolved_artifact_version_id,
        )
        if artifact is None:
            return None
        if artifact.artifact_kind != APP_CONTEXT_VERSION_ARTIFACT_KIND:
            raise ValueError("artifact_version_id does not reference an app_context_version artifact")
        context_version = _app_context_version_from_artifact(artifact)
        if (
            context_version is not None
            and resolved_context_version_id
            and context_version.context_version_id != resolved_context_version_id
        ):
            return None
        return context_version

    versions = await store.list_artifact_versions(
        app_id=resolved_app_id,
        artifact_kind=APP_CONTEXT_VERSION_ARTIFACT_KIND,
        artifact_key=APP_CONTEXT_VERSION_ARTIFACT_KEY,
        limit=100,
    )
    for artifact in versions:
        context_version = _app_context_version_from_artifact(artifact)
        if context_version is not None and context_version.context_version_id == resolved_context_version_id:
            return context_version
    return None


async def get_current_app_context_version(
    *,
    app_id: str,
    artifact_store: ArtifactStore | None = None,
) -> AppContextVersion | None:
    """Load the CURRENT AppContextVersion payload for an app."""
    store = artifact_store or get_artifact_store()
    versions = await store.list_artifact_versions(
        app_id=app_id,
        artifact_kind=APP_CONTEXT_VERSION_ARTIFACT_KIND,
        artifact_key=APP_CONTEXT_VERSION_ARTIFACT_KEY,
        lifecycle_status=ArtifactLifecycleStatus.CURRENT,
        limit=1,
    )
    if not versions:
        return None

    payload = _summary_payload(versions[0])
    if payload is None:
        return None
    return AppContextVersion.model_validate(payload)


def _app_context_version_from_artifact(artifact: ArtifactVersionDoc | Any) -> AppContextVersion | None:
    payload = _summary_payload(artifact)
    if payload is None:
        return None
    return AppContextVersion.model_validate(payload)


def _surface_indexes_from_inventory(
    inventory: ApplicationInventory | None,
) -> AppContextSurfaceIndexes:
    if inventory is None:
        return AppContextSurfaceIndexes()
    return AppContextSurfaceIndexes(
        routes=inventory.routes,
        pages=inventory.pages,
        components=inventory.components,
        api_endpoints=inventory.api_endpoints,
        data_entities=inventory.data_entities,
        integrations=inventory.integrations,
        modules=inventory.modules,
        workflows=inventory.workflows,
        deployment_boundaries=inventory.deployment_boundaries,
    )


async def _persist_context_payload_artifact(
    *,
    app_id: str,
    artifact_kind: str,
    artifact_key: str,
    payload: Any,
    path: str,
    artifact_store: ArtifactStore,
    source_workflow: str | None,
    source_chat_id: str | None,
) -> ArtifactVersionDoc:
    raw = _json_bytes(payload)
    return await artifact_store.create_artifact_version(
        app_id=app_id,
        artifact_kind=artifact_kind,
        artifact_key=artifact_key,
        files_manifest=[
            {
                "path": path,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size_bytes": len(raw),
                "content_type": "application/json",
            }
        ],
        source_workflow=source_workflow,
        source_chat_id=source_chat_id,
        lifecycle_status=ArtifactLifecycleStatus.DRAFT,
        validation_status=ArtifactValidationStatus.PENDING,
        commit_metadata={
            "message": f"AppContext: {artifact_kind}",
            "source_workflow": source_workflow,
            "source_chat_id": source_chat_id,
            "metadata": {
                "summary_payload": payload,
                "summary_format": "json",
                "artifact_contract": "mozaiksai/core/app_context/models.py",
                "source_workflow": source_workflow,
            },
        },
    )


def _summary_payload(artifact: ArtifactVersionDoc | Any) -> Any:
    metadata = getattr(getattr(artifact, "commit_metadata", None), "metadata", None)
    if isinstance(metadata, dict):
        return metadata.get("summary_payload")
    if isinstance(artifact, dict):
        raw_commit = artifact.get("commit_metadata")
        if isinstance(raw_commit, dict):
            raw_metadata = raw_commit.get("metadata")
            if isinstance(raw_metadata, dict):
                return raw_metadata.get("summary_payload")
    return None


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    ).encode("utf-8")


def _manifest_entries(entries: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for entry in entries or []:
        if isinstance(entry, dict):
            result.append(dict(entry))
        elif hasattr(entry, "model_dump"):
            result.append(entry.model_dump(mode="python"))
        else:
            path = getattr(entry, "path", None)
            if path:
                result.append({"path": str(path)})
    return result


def _manifest_entries_by_path(entries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for entry in entries:
        path = _normalize_app_bundle_path(entry.get("path"))
        if path:
            result[path] = entry
    return result


def _normalize_app_bundle_path(path: Any) -> str | None:
    raw = str(path or "").replace("\\", "/").strip("/")
    if not raw:
        return None
    known_prefixes = (
        "ui/",
        "modules/",
        "workflows/",
        "services/",
        "config/",
        "brand/",
        "admin/",
        "data/",
    )
    for prefix in known_prefixes:
        marker = f"/{prefix}"
        if raw.startswith(prefix):
            return raw
        if marker in raw:
            return f"{prefix}{raw.split(marker, 1)[1]}"
    if raw.endswith("/app.json") or raw == "app.json":
        return "app.json"
    return raw


def _surface_id(prefix: str, value: str) -> str:
    clean = "".join(char.lower() if char.isalnum() else "_" for char in value).strip("_")
    while "__" in clean:
        clean = clean.replace("__", "_")
    return f"{prefix}_{clean or 'surface'}"[:120]


def _greenfield_pages(
    paths: list[str],
    source_ref_id: str,
    manifest_by_path: dict[str, dict[str, Any]],
) -> list[SurfaceRef]:
    return [
        SurfaceRef(
            surface_id=_surface_id("page", path),
            kind="page",
            label=path.rsplit("/", 1)[-1].removesuffix(".yaml"),
            location=path,
            source_ref_id=source_ref_id,
            metadata=_page_metadata(path, manifest_by_path),
        )
        for path in paths
        if path.startswith("ui/pages/") and path.endswith((".yaml", ".yml", ".json"))
    ]


def _greenfield_routes(
    paths: list[str],
    source_ref_id: str,
    manifest_by_path: dict[str, dict[str, Any]],
) -> list[SurfaceRef]:
    routes: list[SurfaceRef] = []
    for path in paths:
        if path == "ui/route_manifest.json":
            routes.append(
                SurfaceRef(
                    surface_id="route_manifest",
                    kind="route_manifest",
                    label="Route manifest",
                    location=path,
                    source_ref_id=source_ref_id,
                )
            )
        elif path.startswith("ui/pages/") and path.endswith((".yaml", ".yml", ".json")):
            routes.append(
                SurfaceRef(
                    surface_id=_surface_id("route", path),
                    kind="route_candidate",
                    label=path.rsplit("/", 1)[-1].split(".", 1)[0],
                    location=path,
                    source_ref_id=source_ref_id,
                    metadata={"derived_from": "ui_pages_path"},
                )
            )
    page_paths = {
        path.rsplit("/", 1)[-1].split(".", 1)[0]: path
        for path in paths
        if path.startswith("ui/pages/") and path.endswith((".yaml", ".yml", ".json"))
    }
    for entry in _route_manifest_page_entries(manifest_by_path):
        route_path = _first_string(entry, ("path", "route", "url"))
        if not route_path:
            continue
        page_path = _route_entry_page_path(entry, page_paths)
        routes.append(
            SurfaceRef(
                surface_id=_surface_id("route", route_path),
                kind="route",
                label=_first_string(entry, ("label", "title", "name")) or route_path,
                location=route_path,
                source_ref_id=source_ref_id,
                metadata={
                    "route_path": route_path,
                    "source_path": "ui/route_manifest.json",
                    "page_path": page_path,
                    "component": _first_string(entry, ("component", "component_id", "componentId")),
                    "component_path": _route_entry_component_path(entry, paths),
                },
            )
        )
    return routes


def _greenfield_components(paths: list[str], source_ref_id: str) -> list[SurfaceRef]:
    suffixes = (".jsx", ".tsx", ".js", ".ts")
    return [
        SurfaceRef(
            surface_id=_surface_id("component", path),
            kind="component",
            label=path.rsplit("/", 1)[-1],
            location=path,
            source_ref_id=source_ref_id,
            metadata={"path": path},
        )
        for path in paths
        if path.startswith(("ui/components/", "ui/pages/custom/")) and path.endswith(suffixes)
    ]


def _greenfield_modules(
    paths: list[str],
    source_ref_id: str,
    manifest_by_path: dict[str, dict[str, Any]],
) -> list[SurfaceRef]:
    modules: list[SurfaceRef] = []
    for path in paths:
        parts = path.split("/")
        if len(parts) >= 3 and parts[0] == "modules" and parts[2] == "module.yaml":
            module_id = _module_id_from_module_yaml(path, manifest_by_path) or parts[1]
            modules.append(
                SurfaceRef(
                    surface_id=_surface_id("module", module_id),
                    kind="module",
                    label=module_id,
                    location=path,
                    source_ref_id=source_ref_id,
                    metadata=_module_metadata(path, module_id, manifest_by_path),
                )
            )
    return modules


def _greenfield_workflows(
    paths: list[str],
    source_ref_id: str,
    manifest_by_path: dict[str, dict[str, Any]],
) -> list[SurfaceRef]:
    workflows: dict[str, SurfaceRef] = {}
    for path in paths:
        parts = path.split("/")
        if len(parts) >= 3 and parts[0] == "workflows" and parts[2] in {"orchestrator.yaml", "agents.yaml"}:
            workflows.setdefault(
                parts[1],
                SurfaceRef(
                    surface_id=_surface_id("workflow", parts[1]),
                    kind="workflow",
                    label=parts[1],
                    location=path,
                    source_ref_id=source_ref_id,
                    metadata=_workflow_metadata(parts[1], paths, manifest_by_path),
                ),
            )
    return list(workflows.values())


def _greenfield_api_endpoints(paths: list[str], source_ref_id: str) -> list[SurfaceRef]:
    return [
        SurfaceRef(
            surface_id=_surface_id("api", path),
            kind="api_endpoint_candidate",
            label=path.rsplit("/", 1)[-1],
            location=path,
            source_ref_id=source_ref_id,
        )
        for path in paths
        if path.startswith(("services/routes/", "api/")) and path.endswith((".py", ".yaml", ".yml", ".json"))
    ]


def _greenfield_data_entities(
    paths: list[str],
    source_ref_id: str,
    manifest_by_path: dict[str, dict[str, Any]],
) -> list[SurfaceRef]:
    entities: list[SurfaceRef] = []
    explicit_entities = _data_contract_entities(manifest_by_path)
    for entity in explicit_entities:
        entity_id = str(entity.get("entity_id") or "").strip()
        if not entity_id:
            continue
        entities.append(
            SurfaceRef(
                surface_id=_surface_id("data", entity_id),
                kind="data_entity",
                label=entity_id,
                location="data/contract.json",
                source_ref_id=source_ref_id,
                metadata={
                    "path": "data/contract.json",
                    "module_ids": sorted(entity.get("module_ids") or []),
                    "operations_by_module": entity.get("operations_by_module") or {},
                },
            )
        )
    if "data/contract.json" in paths:
        entities.append(
            SurfaceRef(
                surface_id="data_contract",
                kind="data_contract",
                label="Data contract",
                location="data/contract.json",
                source_ref_id=source_ref_id,
            )
        )
    for path in paths:
        if path.startswith("data/migrations/") and path.endswith(".json"):
            entities.append(
                SurfaceRef(
                    surface_id=_surface_id("database_migration", path),
                    kind="database_migration",
                    label=path.rsplit("/", 1)[-1],
                    location=path,
                    source_ref_id=source_ref_id,
                )
            )
    return entities


def _greenfield_integrations(paths: list[str]) -> list[IntegrationInventory]:
    integration_ids: set[str] = set()
    config_ids: set[str] = set()
    for path in paths:
        if path.startswith("services/integrations/") and path.endswith("_client.py"):
            integration_ids.add(path.rsplit("/", 1)[-1].removesuffix("_client.py"))
        elif path == "config/integrations.json":
            config_ids.add("integrations")
        elif path.startswith("config/integrations/") and path.endswith(".json"):
            config_ids.add(path.rsplit("/", 1)[-1].split(".", 1)[0])

    return [
        IntegrationInventory(
            integration_id=integration_id,
            provider_type="generated_integration",
            config_required=True,
            secret_required=False,
            frontend_safe_config={},
            readiness_status=IntegrationReadinessStatus.READY
            if "integrations" in config_ids or integration_id in config_ids
            else IntegrationReadinessStatus.CONFIG_REQUIRED,
            owner=OwnershipClass.EXTERNAL_SYSTEM.value,
        )
        for integration_id in sorted(integration_ids)
    ]


def _greenfield_config_surfaces(paths: list[str], source_ref_id: str) -> list[SurfaceRef]:
    config_paths = [
        path
        for path in paths
        if path in {"app.json", "config/shell.json", "brand/theme_config.json"}
    ]
    return [
        SurfaceRef(
            surface_id=_surface_id("config", path),
            kind="app_config",
            label=path,
            location=path,
            source_ref_id=source_ref_id,
        )
        for path in config_paths
    ]


def _entry_content(entry: dict[str, Any] | None) -> str | None:
    if not isinstance(entry, dict):
        return None
    for key in ("content", "text", "body", "raw_content"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _load_json_manifest(path: str, manifest_by_path: dict[str, dict[str, Any]]) -> Any:
    content = _entry_content(manifest_by_path.get(path))
    if not content:
        return None
    try:
        return json.loads(content)
    except Exception as exc:
        logger.warning("APP_CONTEXT_JSON_MANIFEST_PARSE_FAILED path=%s: %s", path, exc)
        return None


def _load_yaml_manifest(path: str, manifest_by_path: dict[str, dict[str, Any]]) -> Any:
    content = _entry_content(manifest_by_path.get(path))
    if not content:
        return None
    try:
        return yaml.safe_load(content)
    except Exception as exc:
        logger.warning("APP_CONTEXT_YAML_MANIFEST_PARSE_FAILED path=%s: %s", path, exc)
        return None


def _route_manifest_page_entries(manifest_by_path: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    data = _load_json_manifest("ui/route_manifest.json", manifest_by_path)
    if isinstance(data, list):
        return [entry for entry in data if isinstance(entry, dict)]
    if isinstance(data, dict):
        for key in ("pages", "routes", "entries"):
            value = data.get(key)
            if isinstance(value, list):
                return [entry for entry in value if isinstance(entry, dict)]
    return []


def _route_entry_page_path(entry: dict[str, Any], page_paths: dict[str, str]) -> str | None:
    explicit_path = _first_string(entry, ("page_path", "pagePath", "schema_path", "schemaPath"))
    if explicit_path:
        normalized = _normalize_app_bundle_path(explicit_path)
        if normalized and normalized in page_paths.values():
            return normalized

    page_id = _first_string(entry, ("page", "page_id", "pageId", "name", "component"))
    if page_id:
        normalized_id = _normalize_identifier(page_id)
        if normalized_id in page_paths:
            return page_paths[normalized_id]

    route_path = _first_string(entry, ("path", "route", "url"))
    if route_path:
        route_slug = _normalize_identifier(str(route_path).strip("/").split("/")[-1])
        if route_slug in page_paths:
            return page_paths[route_slug]
    return None


def _route_entry_component_path(entry: dict[str, Any], paths: list[str]) -> str | None:
    component = _first_string(entry, ("component", "component_id", "componentId"))
    if not component:
        return None
    component_stem = _normalize_identifier(component)
    suffixes = (".jsx", ".tsx", ".js", ".ts")
    for path in paths:
        if not path.startswith(("ui/components/", "ui/pages/custom/")) or not path.endswith(suffixes):
            continue
        stem = _normalize_identifier(path.rsplit("/", 1)[-1].rsplit(".", 1)[0])
        if stem == component_stem:
            return path
    return None


def _page_metadata(path: str, manifest_by_path: dict[str, dict[str, Any]]) -> dict[str, Any]:
    data = (
        _load_json_manifest(path, manifest_by_path)
        if path.endswith(".json")
        else _load_yaml_manifest(path, manifest_by_path)
    )
    endpoints = sorted(_dedupe(_collect_string_values(data, {"api_endpoint", "apiEndpoint", "endpoint"})))
    module_ids = set(_collect_string_values(data, {"module_id", "moduleId", "module"}))
    for endpoint in endpoints:
        module_id = _module_id_from_endpoint(endpoint)
        if module_id:
            module_ids.add(module_id)
    route = _first_string(data, ("route", "path", "url")) if isinstance(data, dict) else None
    metadata: dict[str, Any] = {
        "path": path,
        "source_path": path,
    }
    if endpoints:
        metadata["api_endpoints"] = endpoints
    if module_ids:
        metadata["module_ids"] = sorted(_normalize_identifier(module_id) for module_id in module_ids)
    if route:
        metadata["route_path"] = route
    return metadata


def _module_id_from_module_yaml(path: str, manifest_by_path: dict[str, dict[str, Any]]) -> str | None:
    data = _load_yaml_manifest(path, manifest_by_path)
    if not isinstance(data, dict):
        return None
    module = data.get("module")
    if isinstance(module, dict):
        module_id = _first_string(module, ("id", "module_id", "moduleId"))
        if module_id:
            return _normalize_identifier(module_id)
    module_id = _first_string(data, ("module_id", "moduleId", "id"))
    return _normalize_identifier(module_id) if module_id else None


def _module_metadata(
    path: str,
    module_id: str,
    manifest_by_path: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    data = _load_yaml_manifest(path, manifest_by_path)
    metadata: dict[str, Any] = {
        "path": path,
        "source_path": path,
        "module_id": module_id,
    }
    integration_ids = sorted(_dedupe(_integration_ids_from_structured_value(data)))
    action_ids = sorted(_dedupe(_action_ids_from_module_yaml(data)))
    if integration_ids:
        metadata["integration_ids"] = integration_ids
    if action_ids:
        metadata["action_ids"] = action_ids
    return metadata


def _workflow_metadata(
    workflow_id: str,
    paths: list[str],
    manifest_by_path: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    workflow_paths = [
        path
        for path in paths
        if path.startswith(f"workflows/{workflow_id}/") and path.endswith((".yaml", ".yml", ".json"))
    ]
    module_ids: set[str] = set()
    for path in workflow_paths:
        data = (
            _load_json_manifest(path, manifest_by_path)
            if path.endswith(".json")
            else _load_yaml_manifest(path, manifest_by_path)
        )
        module_ids.update(
            _collect_string_values(data, {"module_id", "moduleId", "module", "modules", "module_ids", "moduleIds"})
        )
    metadata: dict[str, Any] = {
        "path": workflow_paths[0] if workflow_paths else f"workflows/{workflow_id}",
        "paths": workflow_paths,
    }
    if module_ids:
        metadata["module_ids"] = sorted(_normalize_identifier(module_id) for module_id in module_ids)
    return metadata


def _data_contract_entities(manifest_by_path: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    data = _load_json_manifest("data/contract.json", manifest_by_path)
    if not isinstance(data, dict):
        return []

    entities: dict[str, dict[str, Any]] = {}
    for item in _explicit_entity_items(data):
        entity_id = _entity_id(item)
        if not entity_id:
            continue
        record = entities.setdefault(
            entity_id,
            {
                "entity_id": entity_id,
                "module_ids": set(),
                "operations_by_module": {},
            },
        )
        module_ids = {
            _normalize_identifier(module_id)
            for module_id in _collect_string_values(
                item,
                {"module_id", "moduleId", "module", "owner_module", "ownerModule", "modules"},
            )
        }
        operations = _operation_names(item)
        for module_id in module_ids:
            record["module_ids"].add(module_id)
            if operations:
                record["operations_by_module"][module_id] = sorted(operations)

    for module_id, value in _module_entity_access(data).items():
        normalized_module_id = _normalize_identifier(module_id)
        for entity_id, operations in value.items():
            record = entities.setdefault(
                entity_id,
                {
                    "entity_id": entity_id,
                    "module_ids": set(),
                    "operations_by_module": {},
                },
            )
            record["module_ids"].add(normalized_module_id)
            if operations:
                record["operations_by_module"][normalized_module_id] = sorted(operations)

    return [
        {
            "entity_id": entity_id,
            "module_ids": sorted(record["module_ids"]),
            "operations_by_module": dict(sorted(record["operations_by_module"].items())),
        }
        for entity_id, record in sorted(entities.items())
    ]


def _explicit_entity_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for key in ("entities", "data_entities", "collections", "models", "tables"):
        value = data.get(key)
        if isinstance(value, list):
            items.extend(item for item in value if isinstance(item, dict))
        elif isinstance(value, dict):
            for entity_id, entity_value in value.items():
                if isinstance(entity_value, dict):
                    items.append({"entity_id": entity_id, **entity_value})
                else:
                    items.append({"entity_id": entity_id})
    return items


def _module_entity_access(data: dict[str, Any]) -> dict[str, dict[str, set[str]]]:
    access: dict[str, dict[str, set[str]]] = {}
    modules = data.get("modules")
    if not isinstance(modules, dict):
        return access
    for module_id, module_data in modules.items():
        if not isinstance(module_data, dict):
            continue
        module_access: dict[str, set[str]] = {}
        for operation_key, operation in (("reads", "read"), ("read", "read"), ("writes", "write"), ("write", "write")):
            for entity_id in _strings_from_value(module_data.get(operation_key)):
                module_access.setdefault(_normalize_identifier(entity_id), set()).add(operation)
        for entity_id in _strings_from_value(module_data.get("entities")):
            module_access.setdefault(_normalize_identifier(entity_id), set())
        if module_access:
            access[str(module_id)] = module_access
    return access


def _entity_id(item: dict[str, Any]) -> str | None:
    value = _first_string(item, ("entity_id", "entityId", "id", "name", "collection", "table"))
    return _normalize_identifier(value) if value else None


def _operation_names(value: Any) -> set[str]:
    operations = {_normalize_operation(item) for item in _collect_string_values(value, {"operations", "access"})}
    for key, operation in (("reads", "read"), ("read", "read"), ("writes", "write"), ("write", "write")):
        raw = value.get(key) if isinstance(value, dict) else None
        if raw is True:
            operations.add(operation)
    return {operation for operation in operations if operation in {"read", "write"}}


def _normalize_operation(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"reads", "read"}:
        return "read"
    if normalized in {"writes", "write"}:
        return "write"
    return normalized


def _integration_ids_from_structured_value(value: Any) -> list[str]:
    ids: list[str] = []
    if isinstance(value, dict):
        for key, subvalue in value.items():
            normalized_key = str(key).strip()
            if normalized_key in {
                "integration",
                "integrations",
                "integration_id",
                "integration_ids",
                "connector",
                "connector_id",
                "connector_ids",
                "provider",
                "provider_id",
            }:
                ids.extend(_strings_from_value(subvalue))
            else:
                ids.extend(_integration_ids_from_structured_value(subvalue))
    elif isinstance(value, list):
        for item in value:
            ids.extend(_integration_ids_from_structured_value(item))
    return [_normalize_identifier(item) for item in ids if _normalize_identifier(item)]


def _action_ids_from_module_yaml(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return []
    actions = value.get("actions")
    ids: list[str] = []
    if isinstance(actions, list):
        for action in actions:
            if isinstance(action, dict):
                action_id = _first_string(action, ("id", "action_id", "actionId", "name"))
                if action_id:
                    ids.append(_normalize_identifier(action_id))
    return ids


def _module_id_from_endpoint(endpoint: str) -> str | None:
    marker = "/api/modules/"
    if marker not in endpoint:
        return None
    tail = endpoint.split(marker, 1)[1]
    module_id = tail.split("/", 1)[0]
    return _normalize_identifier(module_id) if module_id else None


def _collect_string_values(value: Any, keys: set[str]) -> list[str]:
    values: list[str] = []
    if isinstance(value, dict):
        for key, subvalue in value.items():
            if str(key) in keys:
                values.extend(_strings_from_value(subvalue))
            else:
                values.extend(_collect_string_values(subvalue, keys))
    elif isinstance(value, list):
        for item in value:
            values.extend(_collect_string_values(item, keys))
    return values


def _strings_from_value(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for subvalue in value for item in _strings_from_value(subvalue)]
    if isinstance(value, dict):
        for key in ("id", "name", "entity_id", "module_id", "integration_id"):
            item = value.get(key)
            if isinstance(item, str):
                return [item]
    return []


def _first_string(value: Any, keys: tuple[str, ...]) -> str | None:
    if not isinstance(value, dict):
        return None
    for key in keys:
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            return item.strip()
    return None


def _normalize_identifier(value: str) -> str:
    return _surface_id("", value).strip("_")


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _greenfield_ownership_boundaries(
    *,
    paths: list[str],
    integrations: list[IntegrationInventory],
    source_ref_id: str,
) -> list[OwnershipBoundary]:
    boundaries = [
        OwnershipBoundary(
            path_or_artifact=path,
            ownership=OwnershipClass.GENERATED_OVERLAY,
            source_ref=source_ref_id,
            allowed_operations=[
                AllowedOperation.INSPECT,
                AllowedOperation.INDEX,
                AllowedOperation.STAGE_PATCH,
                AllowedOperation.PROMOTE,
            ],
            requires_review=True,
            notes="Generated app bundle artifact.",
        )
        for path in paths
    ]
    boundaries.extend(
        OwnershipBoundary(
            path_or_artifact=f"integration:{integration.integration_id}",
            ownership=OwnershipClass.EXTERNAL_SYSTEM,
            source_ref=source_ref_id,
            allowed_operations=[AllowedOperation.INSPECT, AllowedOperation.EXPLAIN],
            requires_review=True,
            notes="External system referenced by generated integration surface.",
        )
        for integration in integrations
    )
    return boundaries


def _greenfield_risk_report(paths: list[str], integrations: list[IntegrationInventory]) -> RiskReport:
    risks: list[RiskItem] = []
    if any(integration.readiness_status is IntegrationReadinessStatus.CONFIG_REQUIRED for integration in integrations):
        risks.append(
            RiskItem(
                risk_id="risk_integration_config_missing",
                description="Generated integration client exists without matching integration config evidence.",
                severity="medium",
                mitigation="Confirm integration config before promoting integration-dependent changes.",
            )
        )
    has_migrations = any(path.startswith("data/migrations/") for path in paths)
    if has_migrations and "data/contract.json" not in paths:
        risks.append(
            RiskItem(
                risk_id="risk_data_migration_without_contract",
                description="Data migration files exist without data contract evidence.",
                severity="medium",
                mitigation="Review the data contract before applying migration plans.",
            )
        )
    has_custom_pages = any(path.startswith("ui/pages/custom/") for path in paths)
    if has_custom_pages and "ui/route_manifest.json" not in paths and "ui/index.js" not in paths:
        risks.append(
            RiskItem(
                risk_id="risk_custom_route_without_manifest",
                description="Custom UI page paths exist without route manifest or UI index evidence.",
                severity="low",
                mitigation="Confirm route registration before promotion.",
            )
        )
    return RiskReport(
        risks=risks,
        recommended_next_steps=["Use current AppContextVersion before staging refinements."],
    )


def _greenfield_app_context_graph(
    *,
    app_id: str,
    source_ref: SourceRef,
    artifact_version_id: str,
    paths: list[str],
    manifest_by_path: dict[str, dict[str, Any]],
    inventory: ApplicationInventory,
    risk_report: RiskReport,
    indexed_at: datetime,
) -> AppContextGraph:
    nodes: dict[str, AppContextGraphNode] = {}
    edges: dict[str, AppContextGraphEdge] = {}

    def add_node(node: AppContextGraphNode) -> None:
        nodes.setdefault(node.node_id, node)

    def add_edge(edge: AppContextGraphEdge) -> None:
        edges.setdefault(edge.edge_id or f"{edge.edge_type.value}:{edge.source_node_id}:{edge.target_node_id}", edge)

    add_node(
        AppContextGraphNode(
            node_id="app",
            node_type=GraphNodeType.APP,
            label=app_id,
            source_ref_id=source_ref.source_ref_id,
            artifact_version_id=artifact_version_id,
            stale_status=AppContextStaleStatus.CURRENT,
        )
    )
    add_node(
        AppContextGraphNode(
            node_id=source_ref.source_ref_id,
            node_type=GraphNodeType.SOURCE_REF,
            label=source_ref.uri,
            source_ref_id=source_ref.source_ref_id,
            artifact_version_id=artifact_version_id,
            stale_status=AppContextStaleStatus.CURRENT,
        )
    )
    add_node(
        AppContextGraphNode(
            node_id="generated_overlay:app_bundle",
            node_type=GraphNodeType.GENERATED_OVERLAY,
            label="Generated app bundle",
            source_ref_id=source_ref.source_ref_id,
            artifact_version_id=artifact_version_id,
            stale_status=AppContextStaleStatus.CURRENT,
        )
    )
    add_edge(_graph_edge("app", source_ref.source_ref_id, GraphEdgeType.CONTAINS, source_ref.source_ref_id, artifact_version_id))
    add_edge(_graph_edge("app", "generated_overlay:app_bundle", GraphEdgeType.CONTAINS, source_ref.source_ref_id, artifact_version_id))
    add_edge(
        _graph_edge(
            "generated_overlay:app_bundle",
            source_ref.source_ref_id,
            GraphEdgeType.GENERATED_FROM,
            source_ref.source_ref_id,
            artifact_version_id,
            metadata={"source_path": "app_bundle"},
        )
    )

    file_node_by_path: dict[str, str] = {}

    def add_surface(surface: SurfaceRef, node_type: GraphNodeType, prefix: str) -> None:
        node_id = f"{prefix}:{surface.surface_id}"
        metadata = {
            **surface.metadata,
            "path": surface.metadata.get("path") or surface.location,
            "source_path": surface.metadata.get("source_path") or surface.location,
        }
        add_node(
            AppContextGraphNode(
                node_id=node_id,
                node_type=node_type,
                label=surface.label or surface.location,
                source_ref_id=source_ref.source_ref_id,
                artifact_version_id=artifact_version_id,
                stale_status=AppContextStaleStatus.CURRENT,
                metadata={key: value for key, value in metadata.items() if value not in (None, "", [])},
            )
        )
        add_edge(_graph_edge("app", node_id, GraphEdgeType.CONTAINS, source_ref.source_ref_id, artifact_version_id))
        source_path = metadata.get("source_path") or metadata.get("path")
        file_node_id = file_node_by_path.get(str(source_path))
        if file_node_id:
            add_edge(
                _graph_edge(
                    node_id,
                    file_node_id,
                    GraphEdgeType.GENERATED_FROM,
                    source_ref.source_ref_id,
                    artifact_version_id,
                    metadata={"source_path": source_path},
                )
            )

    for path in paths:
        node_id = _file_node_id(path)
        file_node_by_path[path] = node_id
        add_node(
            AppContextGraphNode(
                node_id=node_id,
                node_type=GraphNodeType.FILE,
                label=path,
                source_ref_id=source_ref.source_ref_id,
                artifact_version_id=artifact_version_id,
                stale_status=AppContextStaleStatus.CURRENT,
                checksum=_manifest_checksum(manifest_by_path.get(path)),
                metadata={"path": path, "source_path": path},
            )
        )
        add_edge(
            _graph_edge(
                "generated_overlay:app_bundle",
                node_id,
                GraphEdgeType.CONTAINS,
                source_ref.source_ref_id,
                artifact_version_id,
                metadata={"source_path": path},
            )
        )

    for route in inventory.routes:
        add_surface(route, GraphNodeType.ROUTE, "route")
    for page in inventory.pages:
        add_surface(page, GraphNodeType.PAGE, "page")
    for component in inventory.components:
        add_surface(component, GraphNodeType.COMPONENT, "component")
    for module in inventory.modules:
        add_surface(module, GraphNodeType.MODULE, "module")
    for workflow in inventory.workflows:
        add_surface(workflow, GraphNodeType.WORKFLOW, "workflow")
    for integration in inventory.integrations:
        node_id = f"integration:{integration.integration_id}"
        integration_path = _integration_client_path(integration.integration_id, paths)
        add_node(
            AppContextGraphNode(
                node_id=node_id,
                node_type=GraphNodeType.INTEGRATION,
                label=integration.integration_id,
                source_ref_id=source_ref.source_ref_id,
                artifact_version_id=artifact_version_id,
                stale_status=AppContextStaleStatus.CURRENT,
                metadata={
                    "path": integration_path,
                    "source_path": integration_path,
                    "ownership": OwnershipClass.EXTERNAL_SYSTEM.value,
                },
            )
        )
        add_edge(
            _graph_edge(
                "app",
                node_id,
                GraphEdgeType.USES_INTEGRATION,
                source_ref.source_ref_id,
                artifact_version_id,
                metadata={"source_path": integration_path or "app_bundle"},
            )
        )
        if integration_path and integration_path in file_node_by_path:
            add_edge(
                _graph_edge(
                    node_id,
                    file_node_by_path[integration_path],
                    GraphEdgeType.GENERATED_FROM,
                    source_ref.source_ref_id,
                    artifact_version_id,
                    metadata={"source_path": integration_path},
                )
            )
    for entity in inventory.data_entities:
        add_surface(entity, GraphNodeType.DATA_ENTITY, "data")
    for risk in risk_report.risks:
        node_id = f"risk:{risk.risk_id}"
        add_node(
            AppContextGraphNode(
                node_id=node_id,
                node_type=GraphNodeType.RISK,
                label=risk.description,
                source_ref_id=source_ref.source_ref_id,
                artifact_version_id=artifact_version_id,
                stale_status=AppContextStaleStatus.CURRENT,
            )
        )
        add_edge(
            _graph_edge(
                node_id,
                "app",
                GraphEdgeType.BLOCKS,
                source_ref.source_ref_id,
                artifact_version_id,
                metadata={"source_path": "risk_report"},
            )
        )

    _add_greenfield_relationship_edges(
        inventory=inventory,
        paths=paths,
        file_node_by_path=file_node_by_path,
        source_ref_id=source_ref.source_ref_id,
        artifact_version_id=artifact_version_id,
        add_node=add_node,
        add_edge=add_edge,
    )

    payload = {
        "nodes": [node.model_dump(mode="json") for node in nodes.values()],
        "edges": [edge.model_dump(mode="json") for edge in edges.values()],
    }
    return AppContextGraph(
        graph_id=f"graph_{app_id}_{artifact_version_id}",
        app_id=app_id,
        source_refs=[source_ref],
        indexed_at=indexed_at,
        nodes=list(nodes.values()),
        edges=list(edges.values()),
        stale_status=AppContextStaleStatus.CURRENT,
        graph_hash=f"sha256:{hashlib.sha256(_json_bytes(payload)).hexdigest()}",
    )


def _add_greenfield_relationship_edges(
    *,
    inventory: ApplicationInventory,
    paths: list[str],
    file_node_by_path: dict[str, str],
    source_ref_id: str,
    artifact_version_id: str,
    add_node: Any,
    add_edge: Any,
) -> None:
    page_node_by_path = {page.location: f"page:{page.surface_id}" for page in inventory.pages if page.location}
    component_node_by_path = {
        component.location: f"component:{component.surface_id}"
        for component in inventory.components
        if component.location
    }
    module_node_by_id = {
        _normalize_identifier(module.label or module.surface_id): f"module:{module.surface_id}"
        for module in inventory.modules
    }
    integration_node_by_id = {
        _normalize_identifier(integration.integration_id): f"integration:{integration.integration_id}"
        for integration in inventory.integrations
    }
    data_node_by_id = {
        _normalize_identifier(entity.label or entity.surface_id): f"data:{entity.surface_id}"
        for entity in inventory.data_entities
        if entity.kind == "data_entity"
    }

    for route in inventory.routes:
        route_node = f"route:{route.surface_id}"
        page_path = route.metadata.get("page_path")
        page_node = page_node_by_path.get(page_path)  # type: ignore[arg-type]
        if page_node:
            add_edge(
                _graph_edge(
                    route_node,
                    page_node,
                    GraphEdgeType.RENDERS,
                    source_ref_id,
                    artifact_version_id,
                    metadata={"source_path": route.metadata.get("source_path") or "ui/route_manifest.json"},
                )
            )
        component_path = route.metadata.get("component_path")
        component_node = component_node_by_path.get(component_path)  # type: ignore[arg-type]
        if page_node and component_node:
            add_edge(
                _graph_edge(
                    page_node,
                    component_node,
                    GraphEdgeType.RENDERS,
                    source_ref_id,
                    artifact_version_id,
                    metadata={"source_path": route.metadata.get("source_path") or "ui/route_manifest.json"},
                )
            )

    for page in inventory.pages:
        page_node = f"page:{page.surface_id}"
        for module_id in page.metadata.get("module_ids", []):
            module_node = module_node_by_id.get(_normalize_identifier(module_id))
            if not module_node:
                continue
            add_edge(
                _graph_edge(
                    page_node,
                    module_node,
                    GraphEdgeType.CALLS,
                    source_ref_id,
                    artifact_version_id,
                    metadata={
                        "source_path": page.location,
                        "api_endpoints": page.metadata.get("api_endpoints", []),
                    },
                )
            )

    for module in inventory.modules:
        module_node = f"module:{module.surface_id}"
        for integration_id in module.metadata.get("integration_ids", []):
            integration_node = integration_node_by_id.get(_normalize_identifier(integration_id))
            if integration_node:
                add_edge(
                    _graph_edge(
                        module_node,
                        integration_node,
                        GraphEdgeType.USES_INTEGRATION,
                        source_ref_id,
                        artifact_version_id,
                        metadata={"source_path": module.location},
                    )
                )
        for action_id in module.metadata.get("action_ids", []):
            action_node = f"api:{_surface_id('action', f'{module.label}_{action_id}')}"
            action_path = _module_handler_path(module.label or module.surface_id, paths)
            add_node(
                AppContextGraphNode(
                    node_id=action_node,
                    node_type=GraphNodeType.API_ENDPOINT,
                    label=action_id,
                    source_ref_id=source_ref_id,
                    artifact_version_id=artifact_version_id,
                    stale_status=AppContextStaleStatus.CURRENT,
                    metadata={
                        "path": action_path,
                        "source_path": module.location,
                        "module_id": module.label or module.surface_id,
                        "action_id": action_id,
                    },
                )
            )
            add_edge(
                _graph_edge(
                    module_node,
                    action_node,
                    GraphEdgeType.CALLS,
                    source_ref_id,
                    artifact_version_id,
                    metadata={"source_path": module.location, "action_id": action_id, "path": action_path},
                )
            )

    for entity in inventory.data_entities:
        if entity.kind != "data_entity":
            continue
        entity_node = data_node_by_id.get(_normalize_identifier(entity.label or entity.surface_id))
        if not entity_node:
            continue
        operations_by_module = entity.metadata.get("operations_by_module")
        if not isinstance(operations_by_module, dict):
            continue
        for module_id, operations in sorted(operations_by_module.items()):
            module_node = module_node_by_id.get(_normalize_identifier(module_id))
            if not module_node:
                continue
            operation_values = {str(operation).lower() for operation in operations or []}
            if "read" in operation_values:
                add_edge(
                    _graph_edge(
                        module_node,
                        entity_node,
                        GraphEdgeType.READS,
                        source_ref_id,
                        artifact_version_id,
                        metadata={"source_path": entity.location},
                    )
                )
            if "write" in operation_values:
                add_edge(
                    _graph_edge(
                        module_node,
                        entity_node,
                        GraphEdgeType.WRITES,
                        source_ref_id,
                        artifact_version_id,
                        metadata={"source_path": entity.location},
                    )
                )

    for workflow in inventory.workflows:
        workflow_node = f"workflow:{workflow.surface_id}"
        for module_id in workflow.metadata.get("module_ids", []):
            module_node = module_node_by_id.get(_normalize_identifier(module_id))
            if module_node:
                add_edge(
                    _graph_edge(
                        workflow_node,
                        module_node,
                        GraphEdgeType.DEPENDS_ON,
                        source_ref_id,
                        artifact_version_id,
                        metadata={"source_path": workflow.location},
                    )
                )

    for path, file_node_id in file_node_by_path.items():
        if path in page_node_by_path:
            add_edge(
                _graph_edge(
                    page_node_by_path[path],
                    file_node_id,
                    GraphEdgeType.GENERATED_FROM,
                    source_ref_id,
                    artifact_version_id,
                    metadata={"source_path": path},
                )
            )


def _file_node_id(path: str) -> str:
    return f"file:{_surface_id('path', path)}"


def _manifest_checksum(entry: dict[str, Any] | None) -> str | None:
    if not isinstance(entry, dict):
        return None
    checksum = entry.get("sha256")
    if isinstance(checksum, str) and checksum.strip():
        return checksum
    content = _entry_content(entry)
    if content is None:
        return None
    return f"sha256:{hashlib.sha256(content.encode('utf-8')).hexdigest()}"


def _integration_client_path(integration_id: str, paths: list[str]) -> str | None:
    target = f"services/integrations/{integration_id}_client.py"
    if target in paths:
        return target
    return None


def _module_handler_path(module_id: str, paths: list[str]) -> str | None:
    target = f"modules/{_normalize_identifier(module_id)}/backend/handler.py"
    if target in paths:
        return target
    return None


def _graph_edge(
    source_node_id: str,
    target_node_id: str,
    edge_type: GraphEdgeType,
    source_ref_id: str,
    artifact_version_id: str,
    metadata: dict[str, Any] | None = None,
) -> AppContextGraphEdge:
    return AppContextGraphEdge(
        edge_id=_surface_id("edge", f"{edge_type.value}:{source_node_id}:{target_node_id}"),
        edge_type=edge_type,
        source_node_id=source_node_id,
        target_node_id=target_node_id,
        source_ref_id=source_ref_id,
        artifact_version_id=artifact_version_id,
        stale_status=AppContextStaleStatus.CURRENT,
        metadata={key: value for key, value in (metadata or {}).items() if value not in (None, "", [])},
    )


def _bundle_manifest_checksum(entries: list[dict[str, Any]]) -> str:
    return f"sha256:{hashlib.sha256(_json_bytes(entries)).hexdigest()}"


def _lifecycle_value(value: Any) -> str | None:
    if value is None:
        return None
    raw = getattr(value, "value", value)
    return str(raw) if str(raw).strip() else None


__all__ = [
    "APP_CONTEXT_VERSION_ARTIFACT_KIND",
    "APP_CONTEXT_VERSION_ARTIFACT_KEY",
    "BROWNFIELD_APP_CONTEXT_REQUIRED_ARTIFACT_KINDS",
    "GREENFIELD_APP_CONTEXT_ARTIFACT_KINDS",
    "GreenfieldAppContextDrafts",
    "RegisteredAppContextVersion",
    "build_brownfield_app_context_version",
    "build_greenfield_app_context_from_app_bundle",
    "get_app_context_version",
    "get_current_app_context_version",
    "register_greenfield_app_context_version",
    "register_app_context_version",
    "set_current_app_context_version",
]
