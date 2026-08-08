from __future__ import annotations

from pathlib import Path
from typing import Any

from mozaiksai.control_plane import dry_run
from mozaiksai.control_plane.app_context import (
    APP_CONTEXT_GRAPH_LOAD_WARNING,
    AppContextGraphLookupResult,
    AppContextRefSummary,
    AppContextSummary,
    get_app_context_graph_for_version,
    get_current_app_context_graph,
)
from mozaiksai.core.app_context.models import (
    AppContextGraph,
    AppContextGraphEdge,
    AppContextGraphNode,
    AppContextMode,
    AppContextStaleStatus,
    AppContextVersion,
    ArtifactRef,
    GraphEdgeType,
    GraphNodeType,
)
from mozaiksai.core.artifacts.models import (
    BuildRecordStatus,
    BuildRecordValidationStatus,
    BuildRecord,
)

ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = ROOT / "factory_app" / "app"


class _MemoryArtifactStore:
    def __init__(self, versions: dict[str, BuildRecord] | None = None) -> None:
        self.versions = versions or {}

    async def get_build_record(
        self,
        *,
        app_id: str,
        build_record_id: str,
    ) -> BuildRecord | None:
        artifact = self.versions.get(build_record_id)
        if artifact is None or artifact.app_id != app_id:
            return None
        return artifact

    async def list_build_records(self, *, app_id: str, build_family=None, build_key=None, limit=50, **_kwargs):
        versions = [
            artifact
            for artifact in self.versions.values()
            if artifact.app_id == app_id
            and (build_family is None or artifact.build_family == build_family)
            and (build_key is None or artifact.build_key == build_key)
        ]
        return versions[:limit]


class _FailingArtifactStore:
    async def get_build_record(self, **_kwargs: Any) -> BuildRecord | None:
        raise RuntimeError("store offline")


def _node(
    node_id: str,
    node_type: GraphNodeType,
    *,
    label: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> AppContextGraphNode:
    return AppContextGraphNode(
        node_id=node_id,
        node_type=node_type,
        label=label,
        source_ref_id="src_app_bundle",
        build_record_id="av_graph",
        stale_status=AppContextStaleStatus.CURRENT,
        metadata=metadata or {},
    )


def _edge(source: str, target: str, edge_type: GraphEdgeType) -> AppContextGraphEdge:
    return AppContextGraphEdge(
        edge_id=f"edge:{edge_type.value}:{source}:{target}",
        edge_type=edge_type,
        source_node_id=source,
        target_node_id=target,
        source_ref_id="src_app_bundle",
        build_record_id="av_graph",
        stale_status=AppContextStaleStatus.CURRENT,
    )


def _graph(stale_status: AppContextStaleStatus = AppContextStaleStatus.CURRENT) -> AppContextGraph:
    return AppContextGraph(
        graph_id="graph_sample_app",
        app_id="sample_app",
        stale_status=stale_status,
        graph_hash="sha256:test",
        nodes=[
            _node("route:dashboard", GraphNodeType.ROUTE, label="dashboard route"),
            _node(
                "page:dashboard",
                GraphNodeType.COMPONENT,
                label="dashboard page",
                metadata={"path": "ui/pages/dashboard.yaml"},
            ),
            _node(
                "component:dashboard_layout",
                GraphNodeType.COMPONENT,
                label="DashboardLayout",
                metadata={"path": "ui/pages/custom/DashboardLayout.jsx"},
            ),
        ],
        edges=[
            _edge("route:dashboard", "page:dashboard", GraphEdgeType.RENDERS),
            _edge("page:dashboard", "component:dashboard_layout", GraphEdgeType.RENDERS),
        ],
    )


def _summary(stale_status: str = "current") -> AppContextSummary:
    return AppContextSummary(
        app_id="sample_app",
        available=True,
        context_version_id="ctx_sample",
        mode="greenfield",
        stale_status=stale_status,
        artifact_refs=[
            AppContextRefSummary(
                ref_id="av_graph",
                kind="app_context_graph",
                target="app_context_graph",
            )
        ],
    )


def _graph_artifact(graph: AppContextGraph) -> BuildRecord:
    return BuildRecord(
        _id="av_graph",
        app_id="sample_app",
        build_family="app_context_graph",
        build_key="app_context_graph",
        version_number=1,
        lineage_root_id="av_graph",
        lifecycle_status=BuildRecordStatus.DRAFT,
        validation_status=BuildRecordValidationStatus.PENDING,
        commit_metadata={"metadata": {"summary_payload": graph.model_dump(mode="json")}},
    )


def _context_version_artifact(context_version: AppContextVersion) -> BuildRecord:
    return BuildRecord(
        _id="av_ctx_1",
        app_id="sample_app",
        build_family="app_context_version",
        build_key="app_context_version",
        version_number=1,
        lineage_root_id="av_ctx_1",
        lifecycle_status=BuildRecordStatus.DRAFT,
        validation_status=BuildRecordValidationStatus.PENDING,
        commit_metadata={"metadata": {"summary_payload": context_version.model_dump(mode="json")}},
    )


async def test_current_app_context_graph_loads_from_artifact_payload() -> None:
    graph = _graph()
    result = await get_current_app_context_graph(
        app_id="sample_app",
        app_context_summary=_summary(),
        artifact_store=_MemoryArtifactStore({"av_graph": _graph_artifact(graph)}),
    )

    assert result.graph is not None
    assert result.graph.graph_id == "graph_sample_app"
    assert result.warnings == []


async def test_app_context_graph_loads_for_specific_context_version() -> None:
    graph = _graph()
    context_version = AppContextVersion(
        context_version_id="ctx_before_refresh",
        app_id="sample_app",
        mode=AppContextMode.BROWNFIELD,
        artifact_refs=[
            ArtifactRef(
                build_family="app_context_graph",
                build_key="app_context_graph",
                build_record_id="av_graph",
            )
        ],
        graph_snapshot_ref="av_graph",
        stale_status=AppContextStaleStatus.STALE,
    )
    result = await get_app_context_graph_for_version(
        app_id="sample_app",
        context_version_id="ctx_before_refresh",
        artifact_store=_MemoryArtifactStore(
            {
                "av_ctx_1": _context_version_artifact(context_version),
                "av_graph": _graph_artifact(graph),
            }
        ),
    )

    assert result.graph is not None
    assert result.graph.graph_id == "graph_sample_app"
    assert result.warnings == []


async def test_execution_plan_attaches_app_context_impact_hints(monkeypatch) -> None:
    async def _current_summary(**_kwargs: Any) -> AppContextSummary:
        return _summary()

    async def _current_graph(**_kwargs: Any) -> AppContextGraphLookupResult:
        return AppContextGraphLookupResult(graph=_graph())

    monkeypatch.setattr(dry_run, "get_current_app_context_summary", _current_summary)
    monkeypatch.setattr(dry_run, "get_current_app_context_graph", _current_graph)

    plan = await dry_run.build_refinement_execution_plan(
        request="Change dashboard route layout.",
        change_class="design",
        app_root=APP_ROOT,
        app_id="sample_app",
    )

    assert plan.app_context_impact_hints is not None
    assert plan.app_context_impact_hints.available is True
    assert "ui/pages/custom/DashboardLayout.jsx" in plan.app_context_impact_hints.additional_path_hints


async def test_dry_run_plan_attaches_app_context_impact_hints(monkeypatch) -> None:
    async def _current_summary(**_kwargs: Any) -> AppContextSummary:
        return _summary()

    async def _current_graph(**_kwargs: Any) -> AppContextGraphLookupResult:
        return AppContextGraphLookupResult(graph=_graph())

    monkeypatch.setattr(dry_run, "get_current_app_context_summary", _current_summary)
    monkeypatch.setattr(dry_run, "get_current_app_context_graph", _current_graph)

    plan = await dry_run.build_refinement_dry_run_plan(
        request="Change dashboard route layout.",
        change_class="design",
        app_root=APP_ROOT,
        app_id="sample_app",
    )

    assert plan.app_context_impact_hints is not None
    assert plan.app_context_impact_hints.available is True


async def test_missing_graph_does_not_fail_planning(monkeypatch) -> None:
    async def _current_summary(**_kwargs: Any) -> AppContextSummary:
        return _summary()

    async def _missing_graph(**_kwargs: Any) -> AppContextGraphLookupResult:
        return AppContextGraphLookupResult(graph=None)

    monkeypatch.setattr(dry_run, "get_current_app_context_summary", _current_summary)
    monkeypatch.setattr(dry_run, "get_current_app_context_graph", _missing_graph)

    plan = await dry_run.build_refinement_execution_plan(
        request="Change dashboard route layout.",
        change_class="design",
        app_root=APP_ROOT,
        app_id="sample_app",
    )

    assert plan.app_context_impact_hints is not None
    assert plan.app_context_impact_hints.available is False
    assert plan.workflow_sequence == "app_surface_revision"


async def test_stale_graph_warns_without_changing_affected_paths(monkeypatch) -> None:
    async def _current_summary(**_kwargs: Any) -> AppContextSummary:
        return _summary()

    async def _stale_graph(**_kwargs: Any) -> AppContextGraphLookupResult:
        return AppContextGraphLookupResult(graph=_graph(AppContextStaleStatus.STALE))

    monkeypatch.setattr(dry_run, "get_current_app_context_summary", _current_summary)
    monkeypatch.setattr(dry_run, "get_current_app_context_graph", _stale_graph)

    fresh_plan = await dry_run.build_refinement_execution_plan(
        request="Change dashboard route layout.",
        change_class="design",
        app_root=APP_ROOT,
        app_id="sample_app",
    )
    stale_paths = list(fresh_plan.affected_bundle_paths)

    assert fresh_plan.app_context_impact_hints is not None
    assert fresh_plan.app_context_impact_hints.stale_graph_warning
    assert fresh_plan.app_context_impact_hints.additional_path_hints == []
    assert fresh_plan.affected_bundle_paths == stale_paths


async def test_route_selection_and_affected_paths_stay_unchanged_with_graph(monkeypatch) -> None:
    async def _current_summary(**_kwargs: Any) -> AppContextSummary:
        return _summary()

    async def _missing_graph(**_kwargs: Any) -> AppContextGraphLookupResult:
        return AppContextGraphLookupResult(graph=None)

    async def _current_graph(**_kwargs: Any) -> AppContextGraphLookupResult:
        return AppContextGraphLookupResult(graph=_graph())

    monkeypatch.setattr(dry_run, "get_current_app_context_summary", _current_summary)
    monkeypatch.setattr(dry_run, "get_current_app_context_graph", _missing_graph)
    without_graph = await dry_run.build_refinement_execution_plan(
        request="Change dashboard route layout.",
        change_class="design",
        app_root=APP_ROOT,
        app_id="sample_app",
    )

    monkeypatch.setattr(dry_run, "get_current_app_context_graph", _current_graph)
    with_graph = await dry_run.build_refinement_execution_plan(
        request="Change dashboard route layout.",
        change_class="design",
        app_root=APP_ROOT,
        app_id="sample_app",
    )

    assert with_graph.workflow_sequence == without_graph.workflow_sequence
    assert with_graph.target_workflow == without_graph.target_workflow
    assert with_graph.affected_bundle_paths == without_graph.affected_bundle_paths


async def test_artifact_store_load_failure_becomes_plan_warning(monkeypatch) -> None:
    async def _current_summary(**_kwargs: Any) -> AppContextSummary:
        return _summary()

    async def _failed_graph_lookup(**_kwargs: Any) -> AppContextGraphLookupResult:
        return await get_current_app_context_graph(
            app_id="sample_app",
            app_context_summary=_summary(),
            artifact_store=_FailingArtifactStore(),
        )

    monkeypatch.setattr(dry_run, "get_current_app_context_summary", _current_summary)
    monkeypatch.setattr(dry_run, "get_current_app_context_graph", _failed_graph_lookup)

    plan = await dry_run.build_refinement_execution_plan(
        request="Change dashboard route layout.",
        change_class="design",
        app_root=APP_ROOT,
        app_id="sample_app",
    )

    assert plan.app_context_impact_hints is not None
    assert plan.app_context_impact_hints.available is False
    assert any(APP_CONTEXT_GRAPH_LOAD_WARNING in warning for warning in plan.warnings)


def test_app_context_impact_integration_has_no_graph_database_or_proprietary_terms() -> None:
    paths = [
        ROOT / "mozaiksai/control_plane/app_context.py",
        ROOT / "mozaiksai/control_plane/dry_run.py",
        ROOT / "tests/test_control_plane_app_context_impact_integration.py",
    ]
    forbidden_terms = (
        "Fal" + "kor",
        "app " + "zero",
        "app_" + "zero",
        "mozaiks" + "-app",
        "mozaiks" + "pay",
    )

    for path in paths:
        text = path.read_text(encoding="utf-8")
        for term in forbidden_terms:
            assert term.lower() not in text.lower()


