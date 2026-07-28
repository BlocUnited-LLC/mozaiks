"""Canonical AppContext source indexing service.

This module is the shared build path for Mozaiks code context.  It turns a
bounded source scan into a redacted source corpus, an AppContextGraph, and the
health metadata downstream workflows need.  Persistence is optional and kept
behind a small artifact-store helper so callers do not rebuild this stack in
parallel.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mozaiksai.core.artifacts.models import (
    ArtifactLifecycleStatus,
    ArtifactValidationStatus,
    ArtifactVersionDoc,
)
from mozaiksai.core.artifacts.store import ArtifactStore, get_artifact_store

from .context_graph import build_context_graph_from_file_map, context_graph_parser_status
from .health import evaluate_context_graph_health
from .intelligence import AppIntelligenceSnapshot, build_app_intelligence_snapshot
from .models import AppContextGraph, SourceRef, SourceRefKind
from .scan_policy import (
    SourceScanPolicy,
    SourceScanResult,
    collect_source_scan_file_map,
    default_context_graph_scan_policy,
)
from .source_corpus import (
    SourceCorpusBundle,
    build_source_corpus_bundle,
    source_corpus_file_map,
)

SOURCE_CONTEXT_ARTIFACT_KIND = "source_context_bundle"
SOURCE_CONTEXT_ARTIFACT_KEY = "source_context_bundle"
APP_CONTEXT_GRAPH_ARTIFACT_KIND = "app_context_graph"
APP_CONTEXT_GRAPH_ARTIFACT_KEY = "app_context_graph"
APP_INTELLIGENCE_ARTIFACT_KIND = "app_intelligence_snapshot"
APP_INTELLIGENCE_ARTIFACT_KEY = "app_intelligence_snapshot"
APP_CONTEXT_INDEX_SCHEMA_VERSION = "mozaiks.app_context.index.v1"


@dataclass(frozen=True)
class AppContextIndex:
    app_id: str
    artifact_version_id: str
    artifact_kind: str
    artifact_key: str
    source_ref: SourceRef
    source_corpus: SourceCorpusBundle
    app_context_graph: AppContextGraph
    app_intelligence_snapshot: AppIntelligenceSnapshot
    scan_result: SourceScanResult
    parser_status: dict[str, Any]
    health_report: dict[str, Any]
    indexed_at: datetime

    @property
    def safe_file_map(self) -> dict[str, str]:
        return source_corpus_file_map(self.source_corpus)

    @property
    def scan_health(self) -> dict[str, Any]:
        health = dict(self.scan_result.health or {})
        health["parser_status"] = dict(self.parser_status)
        health["source_context_bundle_id"] = self.source_corpus.bundle_id
        health["source_context_chunk_count"] = len(self.source_corpus.chunks)
        health["source_context_symbol_count"] = len(self.source_corpus.symbols)
        health["source_context_import_count"] = len(self.source_corpus.imports)
        health["app_context_index_schema_version"] = APP_CONTEXT_INDEX_SCHEMA_VERSION
        return health


@dataclass(frozen=True)
class PersistedAppContextIndex:
    index: AppContextIndex
    source_context_artifact: ArtifactVersionDoc
    graph_artifact: ArtifactVersionDoc
    intelligence_artifact: ArtifactVersionDoc


def index_source_scan(
    *,
    app_id: str,
    scan_result: SourceScanResult,
    artifact_version_id: str,
    artifact_kind: str = "app_bundle",
    artifact_key: str | None = None,
    source_ref: SourceRef | None = None,
    indexed_at: datetime | None = None,
    parser_status: dict[str, Any] | None = None,
) -> AppContextIndex:
    """Build the canonical source corpus and graph for a selected source scan."""
    resolved_app_id = str(app_id or "").strip()
    resolved_artifact_version_id = str(artifact_version_id or "").strip()
    resolved_artifact_kind = str(artifact_kind or "app_bundle").strip()
    resolved_artifact_key = str(artifact_key or resolved_artifact_kind).strip()
    if not resolved_app_id:
        raise ValueError("app_id is required")
    if not resolved_artifact_version_id:
        raise ValueError("artifact_version_id is required")
    if not scan_result.file_map:
        raise ValueError("source scan found no supported source files")

    resolved_indexed_at = indexed_at or datetime.now(UTC)
    resolved_parser_status = dict(parser_status or context_graph_parser_status())
    resolved_source_ref = source_ref or SourceRef(
        source_ref_id=_stable_source_ref_id(resolved_artifact_version_id),
        kind=SourceRefKind.ARTIFACT_VERSION,
        uri=f"artifact-version://{resolved_artifact_version_id}",
        ref=resolved_artifact_kind,
        checksum=_source_scan_checksum(scan_result),
        indexed_at=resolved_indexed_at,
        metadata={
            "artifact_kind": resolved_artifact_kind,
            "artifact_key": resolved_artifact_key,
            "artifact_version_id": resolved_artifact_version_id,
            "index_schema_version": APP_CONTEXT_INDEX_SCHEMA_VERSION,
        },
    )
    source_corpus = build_source_corpus_bundle(
        app_id=resolved_app_id,
        scan_result=scan_result,
        source_refs=[resolved_source_ref],
        parser_status=resolved_parser_status,
        indexed_at=resolved_indexed_at,
    )
    safe_file_map = source_corpus_file_map(source_corpus)
    graph = build_context_graph_from_file_map(
        app_id=resolved_app_id,
        artifact_version_id=resolved_artifact_version_id,
        artifact_kind=resolved_artifact_kind,
        artifact_key=resolved_artifact_key,
        file_map=safe_file_map,
        source_ref=resolved_source_ref,
        indexed_at=resolved_indexed_at,
    )
    intelligence_snapshot = build_app_intelligence_snapshot(
        app_id=resolved_app_id,
        source_corpus=source_corpus,
        app_context_graph=graph,
        indexed_at=resolved_indexed_at,
        warnings=list(scan_result.warnings),
        metadata={
            "artifact_kind": resolved_artifact_kind,
            "artifact_key": resolved_artifact_key,
            "artifact_version_id": resolved_artifact_version_id,
            "index_schema_version": APP_CONTEXT_INDEX_SCHEMA_VERSION,
        },
    )
    scan_health = dict(scan_result.health or {})
    scan_health.update(
        {
            "parser_status": resolved_parser_status,
            "source_context_bundle_id": source_corpus.bundle_id,
            "source_context_chunk_count": len(source_corpus.chunks),
            "source_context_symbol_count": len(source_corpus.symbols),
            "source_context_import_count": len(source_corpus.imports),
            "app_context_index_schema_version": APP_CONTEXT_INDEX_SCHEMA_VERSION,
        }
    )
    return AppContextIndex(
        app_id=resolved_app_id,
        artifact_version_id=resolved_artifact_version_id,
        artifact_kind=resolved_artifact_kind,
        artifact_key=resolved_artifact_key,
        source_ref=resolved_source_ref,
        source_corpus=source_corpus,
        app_context_graph=graph,
        app_intelligence_snapshot=intelligence_snapshot,
        scan_result=scan_result,
        parser_status=resolved_parser_status,
        health_report=evaluate_context_graph_health(scan_health).model_dump(mode="json"),
        indexed_at=resolved_indexed_at,
    )


def index_file_map(
    *,
    app_id: str,
    file_map: dict[str, str],
    artifact_version_id: str,
    artifact_kind: str = "app_bundle",
    artifact_key: str | None = None,
    source: str = "file_map",
    scan_policy: SourceScanPolicy | dict[str, Any] | None = None,
    source_ref: SourceRef | None = None,
    indexed_at: datetime | None = None,
) -> AppContextIndex:
    """Index an in-memory artifact/workspace file map through the canonical path."""
    from .scan_policy import select_source_file_map

    policy = (
        default_context_graph_scan_policy(scan_policy)
        if isinstance(scan_policy, dict)
        else scan_policy
    )
    scan_result = select_source_file_map(file_map, policy=policy, source=source)
    return index_source_scan(
        app_id=app_id,
        scan_result=scan_result,
        artifact_version_id=artifact_version_id,
        artifact_kind=artifact_kind,
        artifact_key=artifact_key,
        source_ref=source_ref,
        indexed_at=indexed_at,
    )


def index_workspace_root(
    *,
    app_id: str,
    workspace_root: str | Path,
    artifact_version_id: str,
    artifact_kind: str = "app_bundle",
    artifact_key: str | None = None,
    scan_policy: SourceScanPolicy | dict[str, Any] | None = None,
    source_ref: SourceRef | None = None,
    indexed_at: datetime | None = None,
) -> AppContextIndex:
    """Index a local app workspace/repo root through the canonical path."""
    policy = (
        default_context_graph_scan_policy(scan_policy)
        if isinstance(scan_policy, dict)
        else scan_policy
    )
    root = Path(workspace_root).expanduser().resolve()
    scan_result = collect_source_scan_file_map([("", root)], policy=policy)
    return index_source_scan(
        app_id=app_id,
        scan_result=scan_result,
        artifact_version_id=artifact_version_id,
        artifact_kind=artifact_kind,
        artifact_key=artifact_key,
        source_ref=source_ref,
        indexed_at=indexed_at,
    )


async def persist_app_context_index(
    *,
    index: AppContextIndex,
    artifact_store: ArtifactStore | None = None,
    source_workflow: str | None = None,
    source_chat_id: str | None = None,
    source_context_path: str = "app_context/source_context/source_context_bundle.json",
    graph_path: str = "app_context/graph/app_context_graph.json",
    intelligence_path: str = "app_context/intelligence/app_intelligence_snapshot.json",
) -> PersistedAppContextIndex:
    """Persist source corpus and graph artifacts for an index result."""
    store = artifact_store or get_artifact_store()
    metadata = {
        "scan_health": index.scan_health,
        "context_graph_health_report": index.health_report,
        "index_schema_version": APP_CONTEXT_INDEX_SCHEMA_VERSION,
    }
    source_context_artifact = await persist_app_context_payload_artifact(
        store=store,
        app_id=index.app_id,
        artifact_kind=SOURCE_CONTEXT_ARTIFACT_KIND,
        artifact_key=SOURCE_CONTEXT_ARTIFACT_KEY,
        payload=index.source_corpus.model_dump(mode="json"),
        path=source_context_path,
        source_workflow=source_workflow,
        source_chat_id=source_chat_id,
        metadata=metadata,
    )
    graph_artifact = await persist_app_context_payload_artifact(
        store=store,
        app_id=index.app_id,
        artifact_kind=APP_CONTEXT_GRAPH_ARTIFACT_KIND,
        artifact_key=APP_CONTEXT_GRAPH_ARTIFACT_KEY,
        payload=index.app_context_graph.model_dump(mode="json"),
        path=graph_path,
        source_workflow=source_workflow,
        source_chat_id=source_chat_id,
        metadata=metadata,
    )
    intelligence_artifact = await persist_app_context_payload_artifact(
        store=store,
        app_id=index.app_id,
        artifact_kind=APP_INTELLIGENCE_ARTIFACT_KIND,
        artifact_key=APP_INTELLIGENCE_ARTIFACT_KEY,
        payload=index.app_intelligence_snapshot.model_dump(mode="json"),
        path=intelligence_path,
        source_workflow=source_workflow,
        source_chat_id=source_chat_id,
        metadata=metadata,
    )
    return PersistedAppContextIndex(
        index=index,
        source_context_artifact=source_context_artifact,
        graph_artifact=graph_artifact,
        intelligence_artifact=intelligence_artifact,
    )


async def persist_app_context_payload_artifact(
    *,
    store: ArtifactStore,
    app_id: str,
    artifact_kind: str,
    artifact_key: str,
    payload: Any,
    path: str,
    source_workflow: str | None,
    source_chat_id: str | None,
    metadata: dict[str, Any] | None = None,
) -> ArtifactVersionDoc:
    """Persist a JSON AppContext payload artifact with canonical metadata."""
    raw = _json_bytes(payload)
    return await store.create_artifact_version(
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
        validation_status=ArtifactValidationStatus.PASSED,
        commit_metadata={
            "message": f"AppContext: {artifact_kind}",
            "source_workflow": source_workflow,
            "source_chat_id": source_chat_id,
            "metadata": {
                "summary_payload": payload,
                "summary_format": "json",
                "artifact_contract": "mozaiksai/core/app_context",
                **dict(metadata or {}),
            },
        },
    )


def _source_scan_checksum(scan_result: SourceScanResult) -> str:
    return f"sha256:{_file_map_sha256(scan_result.file_map)}"


def _file_map_sha256(file_map: dict[str, str]) -> str:
    payload = json.dumps(
        {path: file_map[path] for path in sorted(file_map)},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _stable_source_ref_id(artifact_version_id: str) -> str:
    token = hashlib.sha256(str(artifact_version_id).encode("utf-8")).hexdigest()[:16]
    return f"src_artifact_{token}"


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


__all__ = [
    "APP_CONTEXT_GRAPH_ARTIFACT_KEY",
    "APP_CONTEXT_GRAPH_ARTIFACT_KIND",
    "APP_CONTEXT_INDEX_SCHEMA_VERSION",
    "APP_INTELLIGENCE_ARTIFACT_KEY",
    "APP_INTELLIGENCE_ARTIFACT_KIND",
    "SOURCE_CONTEXT_ARTIFACT_KEY",
    "SOURCE_CONTEXT_ARTIFACT_KIND",
    "AppContextIndex",
    "PersistedAppContextIndex",
    "index_file_map",
    "index_source_scan",
    "index_workspace_root",
    "persist_app_context_index",
    "persist_app_context_payload_artifact",
]
