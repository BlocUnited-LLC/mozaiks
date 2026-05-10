from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from factory_app.control_plane.tools.get_artifact_summary import get_artifact_summary
from factory_app.control_plane.tools.get_artifact_workspace_catalog import get_artifact_workspace_catalog
from factory_app.control_plane.tools.get_artifact_workspace_scope import get_artifact_workspace_scope
from mozaiksai.control_plane.tools.get_revision_context import get_revision_context
from mozaiksai.core.artifacts.models import (
    ArtifactLifecycleStatus,
    ArtifactValidationStatus,
    ArtifactVersionDoc,
    ChangeClassification,
    ChangeIntentDoc,
    ChangeRequestDoc,
    ImpactSetDoc,
    RefinementRequestPayload,
)
from mozaiksai.core.session.model import RevisionEntry, SequenceStatus, SessionLifecycle, SessionState
from mozaiksai.control_plane import (
    ControlPlaneArtifactChangeRoutesManifest,
    ControlPlaneArtifactRoutingManifest,
    ControlPlaneChangeRouteManifest,
    ControlPlaneHarnessManifest,
    ControlPlaneManifest,
    ControlPlaneProfileInfo,
    ControlPlanePromptsManifest,
    ControlPlaneRoutingManifest,
    ControlPlaneToolCall,
    ControlPlaneToolContext,
    ControlPlaneToolDefinition,
    ControlPlaneToolExecutor,
    ControlPlaneToolsManifest,
    LoadedControlPlanePack,
)


@pytest.mark.asyncio
async def test_control_plane_tool_executor_resolves_pack_declared_tool(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "example_tool.py").write_text(
        "\n".join(
            [
                "async def echo_tool(*, context=None):",
                "    return {'checkpoint': context.checkpoint, 'artifact_kind': context.artifact_kind}",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    pack = LoadedControlPlanePack(
        path=tmp_path,
        manifest=ControlPlaneManifest(
            schema_version="mozaiks.control_plane",
            profile=ControlPlaneProfileInfo(
                id="test",
                display_name="Test",
                description="Test",
            ),
            harness=ControlPlaneHarnessManifest(implementation="example.harness:Harness"),
        ),
        prompts=ControlPlanePromptsManifest(
            schema_version="mozaiks.control_plane.prompts",
            prompts=[],
        ),
        tools=ControlPlaneToolsManifest(
            schema_version="mozaiks.control_plane.tools",
            tools=[
                ControlPlaneToolDefinition(
                    id="echo_tool",
                    kind="context_tool",
                    description="Echo context",
                    entrypoint="example_tool:echo_tool",
                    available_to=["request_submitted"],
                )
            ],
        ),
    )
    executor = ControlPlaneToolExecutor(pack_loader=lambda: pack)

    result = await executor.execute_tool(
        ControlPlaneToolCall(tool_id="echo_tool", target="request_submitted"),
        context=ControlPlaneToolContext(checkpoint="request_submitted", artifact_kind="app_bundle"),
    )

    assert result.success is True
    assert result.output == {"checkpoint": "request_submitted", "artifact_kind": "app_bundle"}


class _FakeChangeRequest:
    def __init__(self, classification: ChangeClassification) -> None:
        self.classification = classification


class _FakeArtifactStore:
    async def get_artifact_version(self, *, app_id: str, artifact_version_id: str):
        return ArtifactVersionDoc(
            _id=artifact_version_id,
            app_id=app_id,
            artifact_kind="app_bundle",
            artifact_key="app_bundle",
            version_number=4,
            lineage_root_id="av_root",
            parent_version_id="av_parent",
            lifecycle_status=ArtifactLifecycleStatus.CURRENT,
            validation_status=ArtifactValidationStatus.PASSED,
            source_workflow="AppGenerator",
            commit_metadata={
                "metadata": {},
            },
        )

    async def list_artifact_versions(self, **kwargs):  # noqa: ANN003
        return []

    async def list_change_requests(self, *, app_id: str, artifact_version_id: str, limit: int):
        return [
            _FakeChangeRequest(ChangeClassification.FEATURE),
            _FakeChangeRequest(ChangeClassification.PATCH),
        ]


class _FakeSessionStore:
    async def load(self, *, app_id: str, user_id: str):
        return SessionState(
            session_id=f"session_router::{app_id}::{user_id}",
            app_id=app_id,
            user_id=user_id,
            sequence_status=SequenceStatus.REVISING,
            active_revision_id="rev_1",
            active_change_request_id="cr_1",
            current_revision_scope="core",
            revision_origin_workflow="FinalMemoAssembly",
            restart_from_workflow="MarketResearch",
            lifecycle_state=SessionLifecycle.ACTIVE,
            current_workflow_id="FinalMemoAssembly",
            current_chat_id="chat_1",
            journey_key="plan_build",
            journey_position=7,
            journey_total_steps=8,
            artifact_version_refs={
                "business_plan_bundle": "av_bp_2",
                "executive_summary": "av_exec_1",
            },
            stale_layers={"financial_model": "cr_1"},
            revision_history=[
                RevisionEntry(
                    revision_id="rev_1",
                    change_request_id="cr_1",
                    scope="core",
                    origin_workflow="FinalMemoAssembly",
                    target_workflow="MarketResearch",
                    from_version_refs={"business_plan_bundle": "av_bp_1"},
                )
            ],
        )


def _revision_pack() -> LoadedControlPlanePack:
    return LoadedControlPlanePack(
        path=Path("control_plane"),
        manifest=ControlPlaneManifest(
            schema_version="mozaiks.control_plane",
            profile=ControlPlaneProfileInfo(id="business_plan", display_name="Business Plan", description="Business plan"),
            harness=ControlPlaneHarnessManifest(implementation="example.harness:Harness"),
            routing=ControlPlaneRoutingManifest(
                default_artifact_kind="business_plan_bundle",
                artifacts=[
                    ControlPlaneArtifactRoutingManifest(
                        artifact_kind="business_plan_bundle",
                        label="business plan bundle",
                        routes=ControlPlaneArtifactChangeRoutesManifest(
                            patch=ControlPlaneChangeRouteManifest(route_to="FinalMemoAssembly"),
                            design=ControlPlaneChangeRouteManifest(route_to="ExecutiveSummary"),
                            feature=ControlPlaneChangeRouteManifest(route_to="BusinessModel"),
                            core=ControlPlaneChangeRouteManifest(route_to="MarketResearch"),
                        ),
                    ),
                    ControlPlaneArtifactRoutingManifest(
                        artifact_kind="executive_summary",
                        label="executive summary",
                        routes=ControlPlaneArtifactChangeRoutesManifest(
                            patch=ControlPlaneChangeRouteManifest(route_to="ExecutiveSummary"),
                            design=ControlPlaneChangeRouteManifest(route_to="ExecutiveSummary"),
                            feature=ControlPlaneChangeRouteManifest(route_to="ExecutiveSummary"),
                            core=ControlPlaneChangeRouteManifest(route_to="MarketResearch"),
                        ),
                    ),
                ],
            ),
        ),
        prompts=ControlPlanePromptsManifest(schema_version="mozaiks.control_plane.prompts", prompts=[]),
        tools=ControlPlaneToolsManifest(schema_version="mozaiks.control_plane.tools", tools=[]),
    )


class _RevisionArtifactStore(_FakeArtifactStore):
    async def get_artifact_version(self, *, app_id: str, artifact_version_id: str):
        if artifact_version_id == "av_bp_2":
            return ArtifactVersionDoc(
                _id=artifact_version_id,
                app_id=app_id,
                artifact_kind="business_plan_bundle",
                artifact_key="business_plan_bundle",
                version_number=2,
                lineage_root_id="av_bp_root",
                parent_version_id="av_bp_1",
                canonical_inputs_version={
                    "market_research": "av_market_1",
                    "customer_persona": "av_persona_1",
                },
                lifecycle_status=ArtifactLifecycleStatus.CURRENT,
                validation_status=ArtifactValidationStatus.PASSED,
                source_workflow="FinalMemoAssembly",
                commit_metadata={
                    "metadata": {
                        "summary_payload": {
                            "title": "Investor Memo",
                            "target_customer": "Enterprise banks",
                        }
                    }
                },
            )
        if artifact_version_id == "av_exec_1":
            return ArtifactVersionDoc(
                _id=artifact_version_id,
                app_id=app_id,
                artifact_kind="executive_summary",
                artifact_key="executive_summary",
                version_number=1,
                lineage_root_id="av_exec_root",
                parent_version_id=None,
                lifecycle_status=ArtifactLifecycleStatus.CURRENT,
                validation_status=ArtifactValidationStatus.PASSED,
                source_workflow="ExecutiveSummary",
                commit_metadata={"metadata": {}},
            )
        if artifact_version_id == "av_market_1":
            return ArtifactVersionDoc(
                _id=artifact_version_id,
                app_id=app_id,
                artifact_kind="market_research",
                artifact_key="market_research",
                version_number=1,
                lineage_root_id="av_market_1",
                lifecycle_status=ArtifactLifecycleStatus.CURRENT,
                validation_status=ArtifactValidationStatus.SKIPPED,
                source_workflow="MarketResearch",
                commit_metadata={
                    "metadata": {
                        "summary_payload": {
                            "segment": "Enterprise banking",
                            "summary": "Large regulated institutions with lengthy sales cycles.",
                        }
                    }
                },
            )
        if artifact_version_id == "av_persona_1":
            return ArtifactVersionDoc(
                _id=artifact_version_id,
                app_id=app_id,
                artifact_kind="customer_persona",
                artifact_key="customer_persona",
                version_number=1,
                lineage_root_id="av_persona_1",
                lifecycle_status=ArtifactLifecycleStatus.CURRENT,
                validation_status=ArtifactValidationStatus.SKIPPED,
                source_workflow="CustomerPersona",
                commit_metadata={
                    "metadata": {
                        "summary_payload": {
                            "buyer": "Innovation lead at a major bank",
                        }
                    }
                },
            )
        return None

    async def list_artifact_versions(self, **kwargs):  # noqa: ANN003
        artifact_kind = kwargs.get("artifact_kind")
        if artifact_kind == "business_plan_bundle":
            artifact = await self.get_artifact_version(app_id=kwargs["app_id"], artifact_version_id="av_bp_2")
            return [artifact] if artifact is not None else []
        if artifact_kind == "executive_summary":
            artifact = await self.get_artifact_version(app_id=kwargs["app_id"], artifact_version_id="av_exec_1")
            return [artifact] if artifact is not None else []
        if artifact_kind == "market_research":
            artifact = await self.get_artifact_version(app_id=kwargs["app_id"], artifact_version_id="av_market_1")
            return [artifact] if artifact is not None else []
        if artifact_kind == "customer_persona":
            artifact = await self.get_artifact_version(app_id=kwargs["app_id"], artifact_version_id="av_persona_1")
            return [artifact] if artifact is not None else []
        return []

    async def get_change_request(self, *, app_id: str, change_request_id: str):
        return ChangeRequestDoc(
            _id=change_request_id,
            app_id=app_id,
            artifact_kind="business_plan_bundle",
            artifact_key="business_plan_bundle",
            artifact_version_id="av_bp_2",
            raw_user_request="Target enterprise banks instead of small businesses.",
            classification=ChangeClassification.CORE,
            refinement_request=RefinementRequestPayload(
                artifact_kind="business_plan_bundle",
                artifact_key="business_plan_bundle",
                artifact_version_id="av_bp_2",
                raw_user_request="Target enterprise banks instead of small businesses.",
                app_id=app_id,
            ),
            change_intent=ChangeIntentDoc(
                change_class=ChangeClassification.CORE,
                rationale="Target customer shifted upstream.",
                source="llm",
                signals=["target_customer_change"],
                requires_concept_revision=True,
            ),
            impact_set=ImpactSetDoc(
                affected_workflows=["MarketResearch", "CustomerPersona", "ExecutiveSummary"],
                affected_declarative_families=["market_research", "customer_persona", "business_plan_bundle"],
                requires_replanning=True,
                requires_rebuild=True,
                restart_from="MarketResearch",
                scope_summary="Reopen upstream strategy workflows.",
            ),
        )

    async def list_change_requests(self, *, app_id: str, artifact_version_id: str, limit: int):
        return [
            await self.get_change_request(app_id=app_id, change_request_id="cr_1"),
        ]


@pytest.mark.asyncio
async def test_revision_context_tool_assembles_runtime_session_and_artifact_state() -> None:
    context = ControlPlaneToolContext(
        checkpoint="request_submitted",
        app_id="app_1",
        user_id="user_1",
        artifact_kind="business_plan_bundle",
        artifact_key="business_plan_bundle",
        artifact_version_id="av_bp_2",
        requested_workflow_id="FinalMemoAssembly",
        raw_user_request="Target enterprise banks instead of small businesses.",
    )

    revision_context = await get_revision_context(
        context=context,
        session_store=_FakeSessionStore(),
        artifact_store=_RevisionArtifactStore(),
        pack_loader=_revision_pack,
    )

    assert revision_context["present"] is True
    assert revision_context["routing"]["current_artifact"]["routes"]["core"] == "MarketResearch"
    assert revision_context["session"]["sequence_status"] == "revising"
    assert revision_context["session"]["active_change_request_id"] == "cr_1"
    assert revision_context["current_artifact"]["artifact_version_id"] == "av_bp_2"
    assert revision_context["current_artifact"]["summary_payload"]["target_customer"] == "Enterprise banks"
    assert revision_context["current_artifact"]["input_artifacts"]["market_research"]["artifact_version_id"] == "av_market_1"
    assert revision_context["active_change_request"]["classification"] == "core"
    assert revision_context["tracked_artifacts"][0]["artifact_kind"] == "business_plan_bundle"
    assert any(
        artifact["artifact_kind"] == "market_research"
        and artifact["summary_payload"]["segment"] == "Enterprise banking"
        for artifact in revision_context["tracked_artifacts"]
        if artifact.get("present")
    )
    assert revision_context["recent_change_requests"][0]["change_request_id"] == "cr_1"


@pytest.mark.asyncio
async def test_workspace_scope_tool_reads_artifact_zip_and_related_files(tmp_path: Path) -> None:
    zip_path = tmp_path / "artifact.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "GeneratedApp/src/App.jsx",
            "import Header from './Header';\nexport default function App() { return <Header />; }\n",
        )
        archive.writestr(
            "GeneratedApp/src/Header.jsx",
            "export default function Header() { return <h1>Header</h1>; }\n",
        )
        archive.writestr("GeneratedApp/package.json", '{"name":"demo"}\n')

    class _ZipArtifactStore(_FakeArtifactStore):
        async def get_artifact_version(self, *, app_id: str, artifact_version_id: str):
            return ArtifactVersionDoc(
                _id=artifact_version_id,
                app_id=app_id,
                artifact_kind="app_bundle",
                artifact_key="app_bundle",
                version_number=2,
                lineage_root_id="av_root",
                parent_version_id="av_parent",
                lifecycle_status=ArtifactLifecycleStatus.CURRENT,
                validation_status=ArtifactValidationStatus.PASSED,
                source_workflow="AppGenerator",
                commit_metadata={
                    "metadata": {"artifact_path": str(zip_path)},
                },
            )

    scope = await get_artifact_workspace_scope(
        context=ControlPlaneToolContext(
            checkpoint="coding_requested",
            app_id="app_1",
            artifact_kind="app_bundle",
            artifact_key="app_bundle",
            artifact_version_id="av_zip_1",
            extra={"selected_file_paths": ["src/App.jsx"]},
        ),
        artifact_store=_ZipArtifactStore(),
    )

    assert scope["present"] is True
    assert scope["source"] == "artifact_zip"
    assert "src/App.jsx" in scope["file_tree"]
    assert scope["selected_scope"]["src/App.jsx"]["present"] is True
    assert "src/Header.jsx" in scope["selected_scope"]["src/App.jsx"]["resolved_related_files"]
    assert scope["related_file_previews"][0]["path"] in {"src/Header.jsx", "package.json"}


@pytest.mark.asyncio
async def test_workspace_catalog_tool_ranks_request_matched_files(tmp_path: Path) -> None:
    zip_path = tmp_path / "artifact.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "GeneratedApp/app/ui/pages/Dashboard.jsx",
            "export default function Dashboard() { return <div>Dashboard analytics export panel</div>; }\n",
        )
        archive.writestr(
            "GeneratedApp/app/ui/components/ExportPanel.jsx",
            "export default function ExportPanel() { return <button>Export CSV</button>; }\n",
        )
        archive.writestr(
            "GeneratedApp/app/ui/components/Header.jsx",
            "export default function Header() { return <h1>Header</h1>; }\n",
        )

    class _ZipArtifactStore(_FakeArtifactStore):
        async def get_artifact_version(self, *, app_id: str, artifact_version_id: str):
            return ArtifactVersionDoc(
                _id=artifact_version_id,
                app_id=app_id,
                artifact_kind="app_bundle",
                artifact_key="app_bundle",
                version_number=5,
                lineage_root_id="av_root",
                parent_version_id="av_parent",
                lifecycle_status=ArtifactLifecycleStatus.CURRENT,
                validation_status=ArtifactValidationStatus.PASSED,
                source_workflow="AppGenerator",
                commit_metadata={
                    "metadata": {"artifact_path": str(zip_path)},
                },
            )

    catalog = await get_artifact_workspace_catalog(
        context=ControlPlaneToolContext(
            checkpoint="scope_requested",
            app_id="app_1",
            artifact_kind="app_bundle",
            artifact_key="app_bundle",
            artifact_version_id="av_zip_2",
            raw_user_request="Add export csv controls to the dashboard",
        ),
        artifact_store=_ZipArtifactStore(),
    )

    assert catalog["present"] is True
    assert "dashboard" in catalog["request_keywords"]
    assert any(match["path"] == "app/ui/pages/Dashboard.jsx" for match in catalog["matches"])
    assert any(match["path"] == "app/ui/components/ExportPanel.jsx" for match in catalog["matches"])
