from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from factory_app.control_plane.tools.get_artifact_summary import get_artifact_summary
from factory_app.control_plane.tools.get_artifact_workspace_catalog import get_artifact_workspace_catalog
from factory_app.control_plane.tools.get_artifact_workspace_scope import get_artifact_workspace_scope
from factory_app.control_plane.tools.get_build_state import get_build_state
from factory_app.control_plane.tools.get_concept_overview import get_concept_overview
from factory_app.control_plane.tools.get_design_summary import get_design_summary
from mozaiksai.core.artifacts.models import (
    ArtifactLifecycleStatus,
    ArtifactValidationStatus,
    ArtifactVersionDoc,
    ChangeClassification,
)
from mozaiksai.control_plane import (
    ControlPlaneHarnessManifest,
    ControlPlaneManifest,
    ControlPlaneProfileInfo,
    ControlPlanePromptsManifest,
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
            schema_version="mozaiks.control_plane.v1",
            profile=ControlPlaneProfileInfo(
                id="test",
                display_name="Test",
                description="Test",
            ),
            harness=ControlPlaneHarnessManifest(implementation="example.harness:Harness"),
        ),
        prompts=ControlPlanePromptsManifest(
            schema_version="mozaiks.control_plane.prompts.v1",
            prompts=[],
        ),
        tools=ControlPlaneToolsManifest(
            schema_version="mozaiks.control_plane.tools.v1",
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


class _FakeBuilderStore:
    async def get_concept(self, *, app_id: str):
        return {
            "app_id": app_id,
            "app_name": "Investor Hub",
            "ConceptOverview": "A platform for investors to evaluate startup deals.",
            "Blueprint": {
                "value_proposition": "Speed up investor diligence.",
                "target_user": "Analyst",
                "core_features": ["deal rooms", "notes", "approvals"],
            },
            "capability_pack_hints": ["crm", "reporting"],
            "surface_candidate_hints": ["dashboard", "deal_detail"],
            "agentic_capabilities": ["workflow_review"],
            "status": "draft",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }

    async def list_design_docs(self, *, app_id: str):
        return [
            {
                "kind": "backend",
                "status": "succeeded",
                "stage": "draft",
                "content": "Backend design for investor workflows.",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "surface_map": {"surfaces": [{"surface_id": "deal_workflow"}, {"surface_id": "dashboard"}]},
            },
            {
                "kind": "ui_schema",
                "status": "succeeded",
                "stage": "draft",
                "content": "UI schema for the dashboard.",
                "updated_at": "2026-01-01T00:05:00+00:00",
            },
        ]

    async def get_latest_database_intent(self, *, app_id: str):
        return {
            "updated_at": "2026-01-01T00:10:00+00:00",
            "database_intent_bundle": {
                "artifact_version_id": "av_200",
                "surfaces": [{"surface_id": "deal_workflow"}],
                "shared_collections": [{"name": "users"}],
                "policies": {"default_scope_field": "app_id"},
            },
        }

    async def get_build_plan(self, *, app_id: str):
        return {
            "build_plan_id": "plan_123",
            "tasks": [{"task_id": "t1"}, {"task_id": "t2"}],
            "entities": [{"name": "Deal"}, {"name": "Investor"}],
        }

    async def get_theme_capture(self, *, app_id: str):
        return {"app_url": "https://example.com", "identity": {"brand_name": "Investor Hub", "tone": "serious"}}


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


@pytest.mark.asyncio
async def test_factory_control_plane_context_tools_summarize_canonical_state() -> None:
    context = ControlPlaneToolContext(
        checkpoint="request_submitted",
        app_id="app_1",
        artifact_kind="app_bundle",
        artifact_key="app_bundle",
        artifact_version_id="av_123",
        raw_user_request="Add exports for investors",
    )

    concept = await get_concept_overview(context=context, store=_FakeBuilderStore())
    design = await get_design_summary(context=context, store=_FakeBuilderStore())
    build_state = await get_build_state(context=context, store=_FakeBuilderStore())
    artifact = await get_artifact_summary(context=context, artifact_store=_FakeArtifactStore())

    assert concept["present"] is True
    assert concept["app_name"] == "Investor Hub"
    assert design["present"] is True
    assert "backend" in design["document_kinds"]
    assert design["surface_ids"] == ["dashboard", "deal_workflow"]
    assert build_state["build_plan"]["task_count"] == 2
    assert build_state["theme_capture"]["identity"]["brand_name"] == "Investor Hub"
    assert artifact["present"] is True
    assert artifact["artifact_version_id"] == "av_123"
    assert artifact["recent_change_classes"] == ["feature", "patch"]


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
