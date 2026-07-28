"""Read-only app-context summaries for Refinement Engine planning."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from mozaiksai.core.app_context.intelligence import AppIntelligenceSnapshot
from mozaiksai.core.app_context.models import (
    AppContextGraph,
    AppContextStaleStatus,
    AppContextVersion,
    ArtifactRef,
    OwnershipClass,
    SourceRef,
)
from mozaiksai.core.app_context.source_corpus import SourceCorpusBundle
from mozaiksai.core.app_context.store import (
    get_app_context_version,
    get_current_app_context_version,
)
from mozaiksai.core.artifacts.store import ArtifactStore

APP_CONTEXT_MISSING_WARNING = (
    "No current app context version is registered; refinement impact uses route and artifact evidence only."
)
APP_CONTEXT_STALE_WARNING = (
    "Current app context is stale/unknown; refinement impact may be incomplete."
)
APP_CONTEXT_GRAPH_LOAD_WARNING = (
    "Current AppContextGraph could not be loaded; graph impact hints are unavailable."
)
SOURCE_CONTEXT_LOAD_WARNING = (
    "Current SourceContextBundle could not be loaded; source-code retrieval is unavailable."
)
APP_INTELLIGENCE_LOAD_WARNING = (
    "Current AppIntelligenceSnapshot could not be loaded; app intelligence summaries are unavailable."
)


class AppContextRefSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref_id: str
    kind: str
    target: str
    lifecycle_status: str | None = None


class AppContextOwnershipSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_boundaries: int = 0
    by_ownership: dict[str, int] = Field(default_factory=dict)
    requires_review_count: int = 0


class AppContextOwnershipBoundarySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path_or_artifact: str
    ownership: str
    requires_review: bool = True


class AppContextSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_id: str | None = None
    available: bool = False
    context_version_id: str | None = None
    mode: str | None = None
    stale_status: str | None = None
    stale_reasons: list[str] = Field(default_factory=list)
    source_refs: list[AppContextRefSummary] = Field(default_factory=list)
    artifact_refs: list[AppContextRefSummary] = Field(default_factory=list)
    ownership: AppContextOwnershipSummary = Field(default_factory=AppContextOwnershipSummary)
    ownership_boundaries: list[AppContextOwnershipBoundarySummary] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class AppContextGraphLookupResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    graph: AppContextGraph | None = None
    warnings: list[str] = Field(default_factory=list)


class SourceContextBundleLookupResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    bundle: SourceCorpusBundle | None = None
    warnings: list[str] = Field(default_factory=list)


class AppIntelligenceSnapshotLookupResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    snapshot: AppIntelligenceSnapshot | None = None
    warnings: list[str] = Field(default_factory=list)


async def get_current_app_context_summary(
    *,
    app_id: str | None,
    artifact_store: ArtifactStore | None = None,
) -> AppContextSummary:
    """Fetch and summarize current app context without affecting routing."""
    resolved_app_id = str(app_id or "").strip() or None
    if not resolved_app_id:
        return AppContextSummary(app_id=None, available=False, warnings=[APP_CONTEXT_MISSING_WARNING])

    try:
        context_version = await get_current_app_context_version(
            app_id=resolved_app_id,
            artifact_store=artifact_store,
        )
    except Exception as exc:
        return AppContextSummary(
            app_id=resolved_app_id,
            available=False,
            warnings=[f"{APP_CONTEXT_MISSING_WARNING} Context lookup failed: {exc}"],
        )

    if context_version is None:
        return AppContextSummary(
            app_id=resolved_app_id,
            available=False,
            warnings=[APP_CONTEXT_MISSING_WARNING],
        )
    return summarize_app_context_version(context_version)


async def get_current_app_context_graph(
    *,
    app_id: str | None,
    app_context_summary: AppContextSummary | dict[str, Any] | None = None,
    artifact_store: ArtifactStore | None = None,
) -> AppContextGraphLookupResult:
    """Best-effort load of the current AppContextGraph artifact payload."""
    resolved_app_id = str(app_id or "").strip() or None
    if not resolved_app_id:
        return AppContextGraphLookupResult()

    summary = (
        app_context_summary
        if isinstance(app_context_summary, AppContextSummary)
        else AppContextSummary.model_validate(app_context_summary)
        if app_context_summary is not None
        else await get_current_app_context_summary(app_id=resolved_app_id, artifact_store=artifact_store)
    )
    graph_ref = next((ref for ref in summary.artifact_refs if ref.kind == "app_context_graph"), None)
    return await _load_app_context_graph_ref(
        app_id=resolved_app_id,
        graph_ref=graph_ref,
        artifact_store=artifact_store,
    )


async def get_current_source_context_bundle(
    *,
    app_id: str | None,
    app_context_summary: AppContextSummary | dict[str, Any] | None = None,
    artifact_store: ArtifactStore | None = None,
) -> SourceContextBundleLookupResult:
    """Best-effort load of the current SourceContextBundle artifact payload."""
    resolved_app_id = str(app_id or "").strip() or None
    if not resolved_app_id:
        return SourceContextBundleLookupResult()

    summary = (
        app_context_summary
        if isinstance(app_context_summary, AppContextSummary)
        else AppContextSummary.model_validate(app_context_summary)
        if app_context_summary is not None
        else await get_current_app_context_summary(app_id=resolved_app_id, artifact_store=artifact_store)
    )
    bundle_ref = next((ref for ref in summary.artifact_refs if ref.kind == "source_context_bundle"), None)
    return await _load_source_context_bundle_ref(
        app_id=resolved_app_id,
        bundle_ref=bundle_ref,
        artifact_store=artifact_store,
    )


async def get_current_app_intelligence_snapshot(
    *,
    app_id: str | None,
    app_context_summary: AppContextSummary | dict[str, Any] | None = None,
    artifact_store: ArtifactStore | None = None,
) -> AppIntelligenceSnapshotLookupResult:
    """Best-effort load of the current AppIntelligenceSnapshot artifact payload."""
    resolved_app_id = str(app_id or "").strip() or None
    if not resolved_app_id:
        return AppIntelligenceSnapshotLookupResult()

    summary = (
        app_context_summary
        if isinstance(app_context_summary, AppContextSummary)
        else AppContextSummary.model_validate(app_context_summary)
        if app_context_summary is not None
        else await get_current_app_context_summary(app_id=resolved_app_id, artifact_store=artifact_store)
    )
    snapshot_ref = next((ref for ref in summary.artifact_refs if ref.kind == "app_intelligence_snapshot"), None)
    return await _load_app_intelligence_snapshot_ref(
        app_id=resolved_app_id,
        snapshot_ref=snapshot_ref,
        artifact_store=artifact_store,
    )


async def get_app_context_graph_for_version(
    *,
    app_id: str | None,
    context_version_id: str | None,
    artifact_store: ArtifactStore | None = None,
) -> AppContextGraphLookupResult:
    """Best-effort load of an AppContextGraph for a specific AppContextVersion."""
    resolved_app_id = str(app_id or "").strip() or None
    resolved_context_version_id = str(context_version_id or "").strip() or None
    if not resolved_app_id or not resolved_context_version_id:
        return AppContextGraphLookupResult()

    try:
        context_version = await get_app_context_version(
            app_id=resolved_app_id,
            context_version_id=resolved_context_version_id,
            artifact_store=artifact_store,
        )
    except Exception as exc:
        return AppContextGraphLookupResult(
            warnings=[f"{APP_CONTEXT_GRAPH_LOAD_WARNING} Context version lookup failed: {exc}"]
        )
    if context_version is None:
        return AppContextGraphLookupResult(
            warnings=[
                f"{APP_CONTEXT_GRAPH_LOAD_WARNING} Context version '{resolved_context_version_id}' was not found."
            ]
        )
    graph_ref = next(
        (ref for ref in summarize_app_context_version(context_version).artifact_refs if ref.kind == "app_context_graph"),
        None,
    )
    return await _load_app_context_graph_ref(
        app_id=resolved_app_id,
        graph_ref=graph_ref,
        artifact_store=artifact_store,
    )


async def get_source_context_bundle_for_version(
    *,
    app_id: str | None,
    context_version_id: str | None,
    artifact_store: ArtifactStore | None = None,
) -> SourceContextBundleLookupResult:
    """Best-effort load of a SourceContextBundle for a specific AppContextVersion."""
    resolved_app_id = str(app_id or "").strip() or None
    resolved_context_version_id = str(context_version_id or "").strip() or None
    if not resolved_app_id or not resolved_context_version_id:
        return SourceContextBundleLookupResult()

    try:
        context_version = await get_app_context_version(
            app_id=resolved_app_id,
            context_version_id=resolved_context_version_id,
            artifact_store=artifact_store,
        )
    except Exception as exc:
        return SourceContextBundleLookupResult(
            warnings=[f"{SOURCE_CONTEXT_LOAD_WARNING} Context version lookup failed: {exc}"]
        )
    if context_version is None:
        return SourceContextBundleLookupResult(
            warnings=[
                f"{SOURCE_CONTEXT_LOAD_WARNING} Context version '{resolved_context_version_id}' was not found."
            ]
        )
    bundle_ref = next(
        (
            ref
            for ref in summarize_app_context_version(context_version).artifact_refs
            if ref.kind == "source_context_bundle"
        ),
        None,
    )
    return await _load_source_context_bundle_ref(
        app_id=resolved_app_id,
        bundle_ref=bundle_ref,
        artifact_store=artifact_store,
    )


async def get_app_intelligence_snapshot_for_version(
    *,
    app_id: str | None,
    context_version_id: str | None,
    artifact_store: ArtifactStore | None = None,
) -> AppIntelligenceSnapshotLookupResult:
    """Best-effort load of an AppIntelligenceSnapshot for a specific context version."""
    resolved_app_id = str(app_id or "").strip() or None
    resolved_context_version_id = str(context_version_id or "").strip() or None
    if not resolved_app_id or not resolved_context_version_id:
        return AppIntelligenceSnapshotLookupResult()

    try:
        context_version = await get_app_context_version(
            app_id=resolved_app_id,
            context_version_id=resolved_context_version_id,
            artifact_store=artifact_store,
        )
    except Exception as exc:
        return AppIntelligenceSnapshotLookupResult(
            warnings=[f"{APP_INTELLIGENCE_LOAD_WARNING} Context version lookup failed: {exc}"]
        )
    if context_version is None:
        return AppIntelligenceSnapshotLookupResult(
            warnings=[
                f"{APP_INTELLIGENCE_LOAD_WARNING} Context version '{resolved_context_version_id}' was not found."
            ]
        )
    snapshot_ref = next(
        (
            ref
            for ref in summarize_app_context_version(context_version).artifact_refs
            if ref.kind == "app_intelligence_snapshot"
        ),
        None,
    )
    return await _load_app_intelligence_snapshot_ref(
        app_id=resolved_app_id,
        snapshot_ref=snapshot_ref,
        artifact_store=artifact_store,
    )


async def _load_app_context_graph_ref(
    *,
    app_id: str,
    graph_ref: AppContextRefSummary | None,
    artifact_store: ArtifactStore | None = None,
) -> AppContextGraphLookupResult:
    if graph_ref is None:
        return AppContextGraphLookupResult()

    try:
        store = artifact_store or ArtifactStore()
        artifact = await store.get_artifact_version(
            app_id=app_id,
            artifact_version_id=graph_ref.ref_id,
        )
    except Exception as exc:
        return AppContextGraphLookupResult(
            warnings=[f"{APP_CONTEXT_GRAPH_LOAD_WARNING} Lookup failed: {exc}"]
        )

    if artifact is None:
        return AppContextGraphLookupResult(
            warnings=[f"{APP_CONTEXT_GRAPH_LOAD_WARNING} Artifact '{graph_ref.ref_id}' was not found."]
        )
    payload = _artifact_summary_payload(artifact)
    if payload is None:
        return AppContextGraphLookupResult(
            warnings=[
                f"{APP_CONTEXT_GRAPH_LOAD_WARNING} Artifact '{graph_ref.ref_id}' has no summary payload."
            ]
        )
    try:
        return AppContextGraphLookupResult(graph=AppContextGraph.model_validate(payload))
    except Exception as exc:
        return AppContextGraphLookupResult(
            warnings=[f"{APP_CONTEXT_GRAPH_LOAD_WARNING} Payload validation failed: {exc}"]
        )


async def _load_source_context_bundle_ref(
    *,
    app_id: str,
    bundle_ref: AppContextRefSummary | None,
    artifact_store: ArtifactStore | None = None,
) -> SourceContextBundleLookupResult:
    if bundle_ref is None:
        return SourceContextBundleLookupResult()

    try:
        store = artifact_store or ArtifactStore()
        artifact = await store.get_artifact_version(
            app_id=app_id,
            artifact_version_id=bundle_ref.ref_id,
        )
    except Exception as exc:
        return SourceContextBundleLookupResult(
            warnings=[f"{SOURCE_CONTEXT_LOAD_WARNING} Lookup failed: {exc}"]
        )

    if artifact is None:
        return SourceContextBundleLookupResult(
            warnings=[f"{SOURCE_CONTEXT_LOAD_WARNING} Artifact '{bundle_ref.ref_id}' was not found."]
        )
    payload = _artifact_summary_payload(artifact)
    if payload is None:
        return SourceContextBundleLookupResult(
            warnings=[
                f"{SOURCE_CONTEXT_LOAD_WARNING} Artifact '{bundle_ref.ref_id}' has no summary payload."
            ]
        )
    try:
        return SourceContextBundleLookupResult(bundle=SourceCorpusBundle.model_validate(payload))
    except Exception as exc:
        return SourceContextBundleLookupResult(
            warnings=[f"{SOURCE_CONTEXT_LOAD_WARNING} Payload validation failed: {exc}"]
        )


async def _load_app_intelligence_snapshot_ref(
    *,
    app_id: str,
    snapshot_ref: AppContextRefSummary | None,
    artifact_store: ArtifactStore | None = None,
) -> AppIntelligenceSnapshotLookupResult:
    if snapshot_ref is None:
        return AppIntelligenceSnapshotLookupResult()

    try:
        store = artifact_store or ArtifactStore()
        artifact = await store.get_artifact_version(
            app_id=app_id,
            artifact_version_id=snapshot_ref.ref_id,
        )
    except Exception as exc:
        return AppIntelligenceSnapshotLookupResult(
            warnings=[f"{APP_INTELLIGENCE_LOAD_WARNING} Lookup failed: {exc}"]
        )

    if artifact is None:
        return AppIntelligenceSnapshotLookupResult(
            warnings=[f"{APP_INTELLIGENCE_LOAD_WARNING} Artifact '{snapshot_ref.ref_id}' was not found."]
        )
    payload = _artifact_summary_payload(artifact)
    if payload is None:
        return AppIntelligenceSnapshotLookupResult(
            warnings=[
                f"{APP_INTELLIGENCE_LOAD_WARNING} Artifact '{snapshot_ref.ref_id}' has no summary payload."
            ]
        )
    try:
        return AppIntelligenceSnapshotLookupResult(snapshot=AppIntelligenceSnapshot.model_validate(payload))
    except Exception as exc:
        return AppIntelligenceSnapshotLookupResult(
            warnings=[f"{APP_INTELLIGENCE_LOAD_WARNING} Payload validation failed: {exc}"]
        )


def summarize_app_context_version(context_version: AppContextVersion) -> AppContextSummary:
    warnings: list[str] = []
    if context_version.stale_status in {
        AppContextStaleStatus.STALE,
        AppContextStaleStatus.PARTIALLY_STALE,
        AppContextStaleStatus.UNSAFE,
        AppContextStaleStatus.UNKNOWN,
    }:
        warnings.append(APP_CONTEXT_STALE_WARNING)

    return AppContextSummary(
        app_id=context_version.app_id,
        available=True,
        context_version_id=context_version.context_version_id,
        mode=context_version.mode.value,
        stale_status=context_version.stale_status.value,
        stale_reasons=list(context_version.stale_reasons),
        source_refs=[_source_ref_summary(ref) for ref in context_version.source_refs],
        artifact_refs=[_artifact_ref_summary(ref) for ref in context_version.artifact_refs],
        ownership=_ownership_summary(context_version),
        ownership_boundaries=[
            AppContextOwnershipBoundarySummary(
                path_or_artifact=boundary.path_or_artifact,
                ownership=boundary.ownership.value,
                requires_review=boundary.requires_review,
            )
            for boundary in context_version.ownership_boundaries
        ],
        warnings=warnings,
    )


def _source_ref_summary(ref: SourceRef) -> AppContextRefSummary:
    return AppContextRefSummary(
        ref_id=ref.source_ref_id,
        kind=ref.kind.value,
        target=ref.uri,
    )


def _artifact_ref_summary(ref: ArtifactRef) -> AppContextRefSummary:
    return AppContextRefSummary(
        ref_id=ref.artifact_version_id,
        kind=ref.artifact_kind,
        target=ref.artifact_key or ref.artifact_kind,
        lifecycle_status=ref.lifecycle_status,
    )


def _ownership_summary(context_version: AppContextVersion) -> AppContextOwnershipSummary:
    by_ownership = {ownership.value: 0 for ownership in OwnershipClass}
    requires_review_count = 0
    for boundary in context_version.ownership_boundaries:
        by_ownership[boundary.ownership.value] = by_ownership.get(boundary.ownership.value, 0) + 1
        if boundary.requires_review:
            requires_review_count += 1
    return AppContextOwnershipSummary(
        total_boundaries=len(context_version.ownership_boundaries),
        by_ownership={key: value for key, value in by_ownership.items() if value},
        requires_review_count=requires_review_count,
    )


def app_context_warnings(summary: AppContextSummary | None) -> list[str]:
    if summary is None:
        return []
    return list(summary.warnings)


def app_context_summary_dump(summary: AppContextSummary | None) -> dict[str, Any] | None:
    if summary is None:
        return None
    return summary.model_dump(mode="json")


def _artifact_summary_payload(artifact: Any) -> Any:
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


__all__ = [
    "APP_CONTEXT_MISSING_WARNING",
    "APP_CONTEXT_GRAPH_LOAD_WARNING",
    "APP_CONTEXT_STALE_WARNING",
    "APP_INTELLIGENCE_LOAD_WARNING",
    "AppIntelligenceSnapshotLookupResult",
    "AppContextGraphLookupResult",
    "AppContextOwnershipBoundarySummary",
    "AppContextOwnershipSummary",
    "AppContextRefSummary",
    "AppContextSummary",
    "SOURCE_CONTEXT_LOAD_WARNING",
    "SourceContextBundleLookupResult",
    "app_context_summary_dump",
    "app_context_warnings",
    "get_app_context_graph_for_version",
    "get_current_app_context_graph",
    "get_current_app_intelligence_snapshot",
    "get_current_app_context_summary",
    "get_current_source_context_bundle",
    "get_app_intelligence_snapshot_for_version",
    "get_source_context_bundle_for_version",
    "summarize_app_context_version",
]
