from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from mozaiksai.control_plane import (
    ArtifactKind,
    ArtifactScopeProposer,
    ChangeClass,
    ChangeIntent,
    ControlPlaneCapabilityConfig,
    ControlPlaneCheckpointManifest,
    ControlPlaneConfig,
    ControlPlaneHarnessManifest,
    ControlPlaneManifest,
    ControlPlanePoliciesManifest,
    ControlPlaneProfileInfo,
    ControlPlanePromptDefinition,
    ControlPlanePromptsManifest,
    ControlPlaneScopePolicyManifest,
    ControlPlaneToolDefinition,
    ControlPlaneToolResult,
    ControlPlaneToolsManifest,
    ImpactSet,
    LoadedControlPlanePack,
    RefinementRequest,
    RefinementRoutingDecision,
)


class _FakeCapabilityService:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def generate_json_completion(self, **kwargs):  # noqa: ANN003
        self.calls.append(kwargs)
        return {
            "content": (
                '{"resolution":"scoped_files","selected_paths":["app/ui/pages/Dashboard.jsx"],'
                '"rationale":"Dashboard is the primary surface for this export copy change.",'
                '"confidence":0.92,"clarification_question":null,"signals":["dashboard_surface","copy_change"]}'
            ),
            "parsed": {
                "resolution": "scoped_files",
                "selected_paths": ["app/ui/pages/Dashboard.jsx"],
                "rationale": "Dashboard is the primary surface for this export copy change.",
                "confidence": 0.92,
                "clarification_question": None,
                "signals": ["dashboard_surface", "copy_change"],
            },
            "usage": {},
        }


class _FakeToolExecutor:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def execute_tool(self, call, *, context=None):  # noqa: ANN001, ANN003
        self.calls.append({"call": call, "context": context})
        if call.tool_id == "get_artifact_workspace_catalog":
            return ControlPlaneToolResult(
                success=True,
                output={
                    "present": True,
                    "file_tree": ["app/ui/pages/Dashboard.jsx", "app/ui/components/ExportPanel.jsx"],
                    "matches": [{"path": "app/ui/pages/Dashboard.jsx", "score": 10}],
                },
            )
        return ControlPlaneToolResult(success=True, output={"present": True})


class _FakeArtifactStore:
    async def get_artifact_version(self, *, app_id: str, artifact_version_id: str):
        return SimpleNamespace(
            id=artifact_version_id,
            artifact_kind="app_bundle",
            artifact_key="app_bundle",
            commit_metadata=SimpleNamespace(
                metadata={
                    "workspace_dir": str(
                        (Path(__file__).resolve().parent / "fixtures" / "scope_workspace").resolve()
                    )
                }
            ),
        )


def _enabled_control_plane() -> ControlPlaneConfig:
    return ControlPlaneConfig(
        enabled=True,
        coding=ControlPlaneCapabilityConfig(
            enabled=True,
            llm_config={"model": "gpt-5.2-codex", "temperature": 0.1},
        ),
    )


def _pack() -> LoadedControlPlanePack:
    return LoadedControlPlanePack(
        path=Path("factory_app/control_plane"),
        manifest=ControlPlaneManifest(
            schema_version="mozaiks.control_plane",
            profile=ControlPlaneProfileInfo(
                id="factory_app",
                display_name="Factory App Harness",
                description="App-zero declarative control-plane pack for the first-party Mozaiks build experience.",
            ),
            harness=ControlPlaneHarnessManifest(
                implementation="mozaiksai.control_plane.implementations.orchestration_control:OrchestrationControlHarness",
                supported_trigger_sources=["refinement"],
            ),
            checkpoints=[
                ControlPlaneCheckpointManifest(
                    id="scope_selection",
                    event="scope_requested",
                    entrypoint="mozaiksai.control_plane.implementations.scope_proposer:ArtifactScopeProposer",
                    prompt_id="coding_scope_selection_system",
                    tool_ids=["get_revision_context", "get_artifact_summary", "get_artifact_workspace_catalog"],
                )
            ],
        ),
        prompts=ControlPlanePromptsManifest(
            schema_version="mozaiks.control_plane.prompts",
            prompts=[
                ControlPlanePromptDefinition(
                    id="coding_scope_selection_system",
                    content="scope selection prompt from pack",
                )
            ],
        ),
        tools=ControlPlaneToolsManifest(
            schema_version="mozaiks.control_plane.tools",
            tools=[
                ControlPlaneToolDefinition(
                    id="get_artifact_summary",
                    kind="context_tool",
                    description="artifact summary",
                    entrypoint="example.tools:get_artifact_summary",
                    available_to=["scope_requested"],
                ),
                ControlPlaneToolDefinition(
                    id="get_revision_context",
                    kind="context_tool",
                    description="revision context",
                    entrypoint="example.tools:get_revision_context",
                    available_to=["scope_requested"],
                ),
                ControlPlaneToolDefinition(
                    id="get_artifact_workspace_catalog",
                    kind="context_tool",
                    description="workspace catalog",
                    entrypoint="example.tools:get_artifact_workspace_catalog",
                    available_to=["scope_requested"],
                ),
            ],
        ),
        policies=ControlPlanePoliciesManifest(
            scope=ControlPlaneScopePolicyManifest(
                max_selected_paths=3,
                auto_apply_max_paths=1,
                overflow_behavior="clarify",
            )
        ),
    )


def _request() -> RefinementRequest:
    return RefinementRequest(
        request_kind="refinement",
        artifact_kind=ArtifactKind.APP_BUNDLE,
        artifact_key="app_bundle",
        artifact_version_id="av_123",
        raw_user_request="Change the dashboard title copy to say Builder Workspace.",
        source_surface="app_workbench",
        app_id="app_1",
        requested_workflow_id="AppGenerator",
    )


def _routing_decision() -> RefinementRoutingDecision:
    return RefinementRoutingDecision(
        workflow_id="AppGenerator",
        refinement_request=_request(),
        context_seed={},
        is_full_restart=False,
        explanation="Scoped patch stays in AppGenerator.",
        change_intent=ChangeIntent(
            change_class=ChangeClass.PATCH,
            source="llm",
            signals=["copy_change"],
            rationale="Single surface copy update.",
            confidence=0.93,
            requires_concept_revision=False,
            touches_app_bundle=True,
            touches_workflow_bundle=False,
            touches_design_docs=False,
            touches_concept=False,
        ),
        impact_set=ImpactSet(
            affected_workflows=["AppGenerator"],
            affected_bundle_paths=[],
            affected_declarative_families=["app_bundle"],
            requires_replanning=False,
            requires_rebuild=True,
            restart_from="AppGenerator",
            scope_summary="Scoped patch to current app bundle.",
        ),
    )


@pytest.mark.asyncio
async def test_scope_proposer_selects_paths_and_materializes_files(tmp_path: Path) -> None:
    workspace_root = tmp_path / "scope_workspace" / "app" / "ui" / "pages"
    workspace_root.mkdir(parents=True, exist_ok=True)
    dashboard_path = workspace_root / "Dashboard.jsx"
    dashboard_path.write_text(
        'export default function Dashboard() { return <h1>Old Title</h1>; }\n',
        encoding="utf-8",
    )

    class _LocalArtifactStore(_FakeArtifactStore):
        async def get_artifact_version(self, *, app_id: str, artifact_version_id: str):
            return SimpleNamespace(
                id=artifact_version_id,
                artifact_kind="app_bundle",
                artifact_key="app_bundle",
                commit_metadata=SimpleNamespace(
                    metadata={"workspace_dir": str((tmp_path / "scope_workspace").resolve())}
                ),
            )

    service = _FakeCapabilityService()
    tool_executor = _FakeToolExecutor()
    proposer = ArtifactScopeProposer(
        capability_service=service,
        config_loader=_enabled_control_plane,
        pack_loader=_pack,
        tool_executor=tool_executor,
        artifact_store=_LocalArtifactStore(),
    )

    proposal = await proposer.propose(
        refinement_request=_request(),
        routing_decision=_routing_decision(),
        payload={},
    )
    files = await proposer.materialize_files(
        refinement_request=_request(),
        selected_paths=proposal.selected_paths,
    )

    assert proposal.resolution == "scoped_files"
    assert proposal.selected_paths == ["app/ui/pages/Dashboard.jsx"]
    assert files["app/ui/pages/Dashboard.jsx"].startswith("export default function Dashboard")
    assert service.calls[0]["system_prompt"] == "scope selection prompt from pack"
    assert '"control_plane_context"' in service.calls[0]["user_prompt"]
    assert len(tool_executor.calls) == 3


@pytest.mark.asyncio
async def test_scope_proposer_clarifies_when_selected_paths_exceed_policy_limit() -> None:
    class _WideScopeService(_FakeCapabilityService):
        async def generate_json_completion(self, **kwargs):  # noqa: ANN003
            self.calls.append(kwargs)
            return {
                "parsed": {
                    "resolution": "scoped_files",
                    "selected_paths": [
                        "app/ui/pages/Dashboard.jsx",
                        "app/ui/components/ExportPanel.jsx",
                        "app/ui/components/Header.jsx",
                        "app/ui/components/Footer.jsx",
                    ],
                    "rationale": "Several dashboard-adjacent files look affected.",
                    "confidence": 0.88,
                    "clarification_question": None,
                    "signals": ["multi_file_scope"],
                }
            }

    pack = _pack().model_copy(
        update={
            "policies": ControlPlanePoliciesManifest(
                scope=ControlPlaneScopePolicyManifest(
                    max_selected_paths=3,
                    auto_apply_max_paths=1,
                    overflow_behavior="clarify",
                )
            )
        }
    )
    proposer = ArtifactScopeProposer(
        capability_service=_WideScopeService(),
        config_loader=_enabled_control_plane,
        pack_loader=lambda: pack,
        tool_executor=_FakeToolExecutor(),
        artifact_store=_FakeArtifactStore(),
    )

    proposal = await proposer.propose(
        refinement_request=_request(),
        routing_decision=_routing_decision(),
        payload={},
    )

    assert proposal.resolution == "clarify"
    assert proposal.clarification_question is not None
    assert "scope_limit_exceeded" in proposal.signals
