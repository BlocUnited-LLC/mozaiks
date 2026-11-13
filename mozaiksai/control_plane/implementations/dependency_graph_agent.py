"""Refinement specialist over bounded evidence from one verified app build."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Sequence
from typing import Any

from factory_app.refinement_harness.tools._context_graph import load_context_graph_for_tool
from mozaiksai.control_plane.config import ControlPlaneConfig, load_control_plane_config
from mozaiksai.control_plane.context_graph.assessment import (
    MAX_GRAPH_PROMPT_BYTES,
    DependencyGraphAssessment,
    DependencyGraphContext,
    GraphDependency,
)
from mozaiksai.control_plane.context_graph.query import build_context_graph_catalog
from mozaiksai.control_plane.contracts import ControlPlaneToolContext
from mozaiksai.control_plane.loader import load_selected_refinement_harness
from mozaiksai.control_plane.schema import LoadedControlPlanePack
from mozaiksai.core.adapters.ag2_agent_runner import AG2StructuredAgentRunner
from mozaiksai.core.app_context.impact_analysis import analyze_impact, build_graph_snapshot_identity
from mozaiksai.core.app_context.models import (
    AppContextGraph,
    GraphEdgeType,
    GraphNodeType,
    ImpactQuery,
)
from mozaiksai.core.app_context.scan_policy import safe_scan_relpath
from mozaiksai.core.app_context.source_corpus import classify_source_role
from mozaiksai.core.artifacts.models import BuildRecordStatus
from mozaiksai.core.artifacts.store import ArtifactStore
from mozaiksai.core.session.build_binding import RunBuildBinding
from mozaiksai.core.usage.context import AuxiliaryUsageContext, resolve_auxiliary_usage_context
from mozaiksai.core.utils.sequences import dedupe_strings

logger = logging.getLogger(__name__)

_EVENT = "dependency_graph_requested"
_DEPENDENCY_EDGES = {
    GraphEdgeType.IMPORTS, GraphEdgeType.CALLS, GraphEdgeType.REFERENCES,
    GraphEdgeType.DEPENDS_ON, GraphEdgeType.RENDERS, GraphEdgeType.IMPLEMENTS_CAPABILITY,
}
_MAX_FILES = 24
_MAX_EDGES = 40
_MAX_PATH_CHARS = 384
_ASSESSMENT_TIMEOUT_SECONDS = 30


class DependencyGraphAgent:
    def __init__(
        self,
        *,
        agent_runner: AG2StructuredAgentRunner | None = None,
        config_loader: Any = load_control_plane_config,
        pack_loader: Any = load_selected_refinement_harness,
        artifact_store: ArtifactStore | None = None,
    ) -> None:
        self._agent_runner = agent_runner or AG2StructuredAgentRunner()
        self._config_loader = config_loader
        self._pack_loader = pack_loader
        self._artifact_store = artifact_store

    async def analyze(
        self,
        *,
        context: ControlPlaneToolContext,
        usage_context: AuxiliaryUsageContext | None = None,
        run_build_binding: RunBuildBinding | None = None,
        selected_paths: Sequence[str] = (),
    ) -> DependencyGraphContext | None:
        config = ControlPlaneConfig.model_validate(self._config_loader())
        if (
            not config.enabled or not config.dependency_graph.enabled
            or not context.build_record_id or context.build_family != "app_bundle"
            or not context.raw_user_request.strip() or not context.app_id or not context.user_id
        ):
            return None
        if context.artifact_app_id != context.app_id and usage_context is None:
            return None
        usage = resolve_auxiliary_usage_context(
            app_id=context.app_id,
            user_id=context.user_id,
            context=usage_context,
            target_app_id=context.artifact_app_id,
            run_build_binding=run_build_binding,
        )
        pack = LoadedControlPlanePack.model_validate(self._pack_loader())
        checkpoint = pack.checkpoint_by_event(_EVENT)
        if checkpoint is None or not checkpoint.prompt_id:
            return None
        prompt = pack.prompt_by_id(checkpoint.prompt_id)
        if prompt is None:
            return None

        loaded = await load_context_graph_for_tool(
            context=context, artifact_store=self._artifact_store, require_verified=True,
        )
        if not loaded.get("present"):
            return None
        provenance = loaded["provenance"]
        graph = loaded["graph"]
        artifact = loaded["artifact"]
        if artifact.lifecycle_status in {BuildRecordStatus.ARCHIVED, BuildRecordStatus.DELETED}:
            return None
        if usage.run_build_binding is not None:
            binding = usage.run_build_binding
            metadata = artifact.commit_metadata.metadata
            if (
                binding.phase != "refinement"
                or metadata.get("target_app_id") != binding.target_app_id
                or metadata.get("build_registry_id") != binding.build_registry_id
                or artifact.commit_metadata.author_user_id != usage.user_id
            ):
                return None
        if (
            not provenance["verified"] or graph.app_id != context.artifact_app_id
            or provenance["app_id"] != graph.app_id
            or provenance["build_record_id"] != context.build_record_id
        ):
            return None
        evidence = _build_evidence(
            graph=graph, provenance=provenance, file_map=loaded["file_map"],
            health=loaded["workspace"].get("scan_health") or {},
            raw_request=context.raw_user_request, selected_paths=selected_paths,
        )
        if evidence is None:
            return None
        user_prompt = _model_input(evidence, context.raw_user_request)
        try:
            async with asyncio.timeout(_ASSESSMENT_TIMEOUT_SECONDS):
                assessment = await self._agent_runner.run(
                    agent_name="DependencyGraphAgent",
                    system_prompt=prompt.content,
                    user_prompt=user_prompt,
                    llm_config=config.resolve_capability_llm_config("dependency_graph"),
                    response_schema=DependencyGraphAssessment,
                    usage_context=usage,
                    retry_count=0,
                    schema_validation_retries=0,
                )
        except Exception as exc:
            logger.warning("Dependency graph assessment unavailable (%s)", type(exc).__name__)
            return evidence

        available = set(evidence.files)
        recommended: list[str] = []
        tests: list[str] = []
        for candidates, field, output in (
            (assessment.recommended_paths, "recommended_paths", recommended),
            (assessment.test_paths, "test_paths", tests),
        ):
            for path in dedupe_strings(candidates):
                if path not in available or (field == "test_paths" and classify_source_role(path=path) != "test"):
                    continue
                updated = evidence.model_copy(update={field: (*output, path)})
                if len(updated.model_dump_json().encode("utf-8")) <= MAX_GRAPH_PROMPT_BYTES:
                    evidence = updated
                    output.append(path)
        return evidence


def _build_evidence(
    *,
    graph: AppContextGraph,
    provenance: dict[str, Any],
    file_map: dict[str, str],
    health: dict[str, Any],
    raw_request: str,
    selected_paths: Sequence[str],
) -> DependencyGraphContext | None:
    paths = {
        node.node_id: path for node in graph.nodes
        if isinstance(path := node.metadata.get("path"), str)
        and path in file_map
        and path != "." and safe_scan_relpath(path) == path
    }
    file_ids = {
        paths[node.node_id]: node.node_id for node in graph.nodes
        if node.node_type == GraphNodeType.FILE and node.node_id in paths
        and len(paths[node.node_id]) <= _MAX_PATH_CHARS
    }
    requested_paths = dedupe_strings(selected_paths)
    selected = [path for path in requested_paths if path in file_ids][:8]
    if not requested_paths:
        catalog = build_context_graph_catalog(
            graph=graph, raw_user_request=raw_request[:2000], file_map=file_map,
            max_nodes=0, max_edges=0,
        )
        selected = [item["path"] for item in catalog["candidate_files"] if item["path"] in file_ids][:4]
    if not selected or not graph.graph_hash:
        return None
    snapshot = build_graph_snapshot_identity(graph)
    report = analyze_impact(
        graph=graph, snapshot_identity=snapshot,
        query=ImpactQuery(
            snapshot_digest=snapshot.identity_digest,
            seed_node_ids=[file_ids[path] for path in selected],
            allowed_edge_types=sorted(_DEPENDENCY_EDGES | {GraphEdgeType.DEFINES}),
            allowed_node_kinds=[GraphNodeType.FILE],
            max_depth=3,
            max_results=_MAX_FILES + 1,
        ),
    )
    reachable = dedupe_strings([*selected, *report.affected_files])
    files = tuple(path for path in reachable if path in file_ids)[:_MAX_FILES]
    available = set(files)
    relations: set[tuple[str, str, GraphEdgeType]] = set()
    outside_neighborhood = False
    for edge in graph.edges:
        source = paths.get(edge.source_node_id)
        target = paths.get(edge.target_node_id)
        if (
            source is not None and target is not None and source != target
            and edge.edge_type in _DEPENDENCY_EDGES
        ):
            if source in available and target in available:
                relations.add((source, target, edge.edge_type))
            elif source in available or target in available:
                outside_neighborhood = True
    dependencies = tuple(
        GraphDependency(source_path=source, target_path=target, kind=kind)
        for source, target, kind in sorted(relations)[:_MAX_EDGES]
    )
    parse_counts = health.get("parse_coverage") or {}
    evidence = DependencyGraphContext(
        app_id=provenance["app_id"], build_record_id=provenance["build_record_id"],
        build_family=provenance["build_family"], build_key=provenance["build_key"],
        bundle_digest=provenance["bundle_digest"], graph_hash=graph.graph_hash,
        files=files, dependencies=dependencies,
        coverage_incomplete=bool(
            report.truncated or outside_neighborhood or len(reachable) > _MAX_FILES or len(relations) > _MAX_EDGES
            or len(requested_paths) > len(selected)
            or health.get("limit_reached") or health.get("skipped", {}).get("large_file")
            or health.get("artifact_source_incomplete")
            or parse_counts.get("partial") or parse_counts.get("failed")
        ),
    )
    while len(_model_input(evidence, raw_request).encode("utf-8")) > MAX_GRAPH_PROMPT_BYTES:
        if evidence.dependencies:
            evidence = evidence.model_copy(update={"dependencies": evidence.dependencies[:-1], "coverage_incomplete": True})
        elif len(evidence.files) > 1:
            evidence = evidence.model_copy(update={"files": evidence.files[:-1], "coverage_incomplete": True})
        else:
            return None
    return evidence


def _model_input(evidence: DependencyGraphContext, raw_request: str) -> str:
    return json.dumps(
        {"request": raw_request[:2000], "evidence": evidence.model_dump(mode="json")},
        ensure_ascii=False, separators=(",", ":"),
    )
