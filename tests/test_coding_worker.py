from __future__ import annotations

from pathlib import Path

import pytest

from mozaiksai.control_plane import (
    CodingWorkerRequest,
    ControlPlaneCapabilityConfig,
    ControlPlaneCheckpointManifest,
    ControlPlaneConfig,
    ControlPlaneHarnessManifest,
    ControlPlaneManifest,
    ControlPlaneProfileInfo,
    ControlPlanePromptDefinition,
    ControlPlanePromptsManifest,
    ControlPlaneToolDefinition,
    ControlPlaneToolResult,
    ControlPlaneToolsManifest,
    LoadedControlPlanePack,
    ScopedRefinementCodingWorker,
)


class _FakeCapabilityService:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def generate_json_completion(self, **kwargs):  # noqa: ANN003
        self.calls.append(kwargs)
        return {
            "content": (
                '{"summary":"Patch the dashboard file.",'
                '"owned_paths":["app/ui/pages/Dashboard.jsx"],'
                '"updated_files":{"app/ui/pages/Dashboard.jsx":"export default function Dashboard() { return \\"patched\\"; }"},'
                '"validation_strategy":"local",'
                '"validation_commands":["npm run build"],'
                '"start_preview":false,'
                '"needs_human_review":false,'
                '"rationale":"Single-file UI patch."}'
            ),
            "parsed": {
                "summary": "Patch the dashboard file.",
                "owned_paths": ["app/ui/pages/Dashboard.jsx"],
                "updated_files": {
                    "app/ui/pages/Dashboard.jsx": 'export default function Dashboard() { return "patched"; }'
                },
                "validation_strategy": "local",
                "validation_commands": ["npm run build"],
                "start_preview": False,
                "needs_human_review": False,
                "rationale": "Single-file UI patch.",
            },
            "usage": {},
        }


class _FakeToolExecutor:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def execute_tool(self, call, *, context=None):  # noqa: ANN001, ANN003
        self.calls.append({"call": call, "context": context})
        return ControlPlaneToolResult(success=True, output={"tool_id": call.tool_id, "artifact_version_id": context.artifact_version_id})


async def _fake_validation_runner(**kwargs):  # noqa: ANN003
    return {
        "success": True,
        "validation_strategy": kwargs["validation_strategy"],
        "validation_status": "passed",
        "preview_url": None,
        "errors": [],
        "warnings": [],
    }


class _FakeArtifactStore:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create_artifact_version(self, **kwargs):  # noqa: ANN003
        self.calls.append(dict(kwargs))
        return type("ArtifactVersion", (), {"id": "av_child_1"})()


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
                    id="coding_refinement",
                    event="coding_requested",
                    entrypoint="mozaiksai.control_plane.implementations.coding_worker:ScopedRefinementCodingWorker",
                    prompt_id="coding_refinement_system",
                    tool_ids=["get_revision_context", "get_artifact_summary"],
                )
            ],
        ),
        prompts=ControlPlanePromptsManifest(
            schema_version="mozaiks.control_plane.prompts",
            prompts=[
                ControlPlanePromptDefinition(
                    id="coding_refinement_system",
                    content="coding system prompt from pack",
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
                    available_to=["coding_requested"],
                ),
                ControlPlaneToolDefinition(
                    id="get_revision_context",
                    kind="context_tool",
                    description="revision context",
                    entrypoint="example.tools:get_revision_context",
                    available_to=["coding_requested"],
                ),
            ],
        ),
    )


@pytest.mark.asyncio
async def test_coding_worker_executes_for_scoped_patch_request(tmp_path: Path) -> None:
    service = _FakeCapabilityService()
    tool_executor = _FakeToolExecutor()
    artifact_store = _FakeArtifactStore()
    worker = ScopedRefinementCodingWorker(
        capability_service=service,
        config_loader=_enabled_control_plane,
        pack_loader=_pack,
        tool_executor=tool_executor,
        validation_runner=_fake_validation_runner,
        artifact_store=artifact_store,
        output_root=tmp_path,
    )

    result = await worker.execute(
        CodingWorkerRequest(
            app_id="app_1",
            artifact_kind="app_bundle",
            artifact_key="app_bundle",
            artifact_version_id="av_123",
            requested_workflow_id="AppGenerator",
            raw_user_request="Fix the dashboard spacing",
            source_surface="app_build",
            change_class="patch",
            files={"app/ui/pages/Dashboard.jsx": "export default function Dashboard() {}"},
            validation_strategy="local",
            context_seed={"change_class": "patch"},
        )
    )

    assert result.eligible is True
    assert result.status == "validated"
    assert result.plan is not None
    assert result.plan.validation_strategy == "local"
    assert result.plan.updated_files["app/ui/pages/Dashboard.jsx"].endswith('"patched"; }')
    assert result.applied_files["app/ui/pages/Dashboard.jsx"].endswith('"patched"; }')
    assert result.validation_result["validation_status"] == "passed"
    assert result.metadata["artifact_version_id"] == "av_child_1"
    assert result.metadata["bundle_mode"] == "workspace_snapshot"
    assert service.calls[0]["system_prompt"] == "coding system prompt from pack"
    assert '"input_files"' in service.calls[0]["user_prompt"]
    assert '"control_plane_context"' in service.calls[0]["user_prompt"]
    assert len(tool_executor.calls) == 2
    assert artifact_store.calls[0]["parent_version_id"] == "av_123"
    assert artifact_store.calls[0]["artifact_kind"] == "app_bundle"
    assert artifact_store.calls[0]["lifecycle_status"].value == "draft"
    assert artifact_store.calls[0]["validation_status"].value == "passed"
    assert artifact_store.calls[0]["commit_metadata"]["metadata"]["applied_paths"] == [
        "app/ui/pages/Dashboard.jsx"
    ]


@pytest.mark.asyncio
async def test_coding_worker_rejects_non_patch_requests() -> None:
    worker = ScopedRefinementCodingWorker(
        capability_service=_FakeCapabilityService(),
        config_loader=_enabled_control_plane,
        pack_loader=_pack,
        tool_executor=_FakeToolExecutor(),
        validation_runner=_fake_validation_runner,
    )

    result = await worker.execute(
        CodingWorkerRequest(
            app_id="app_1",
            artifact_kind="app_bundle",
            artifact_key="app_bundle",
            artifact_version_id="av_123",
            requested_workflow_id="AppGenerator",
            raw_user_request="Add a brand new approvals capability",
            source_surface="app_build",
            change_class="feature",
            files={"app/ui/pages/Dashboard.jsx": "export default function Dashboard() {}"},
        )
    )

    assert result.eligible is False
    assert result.status == "ineligible"
    assert "patch refinements" in str(result.blocked_reason)


@pytest.mark.asyncio
async def test_coding_worker_fails_when_model_edits_outside_scoped_files(tmp_path: Path) -> None:
    class _BadCapabilityService:
        async def generate_json_completion(self, **kwargs):  # noqa: ANN003
            return {
                "content": '{"summary":"Bad edit","owned_paths":["app/ui/pages/Other.jsx"],"updated_files":{"app/ui/pages/Other.jsx":"x"},"validation_strategy":"skip","validation_commands":[],"start_preview":false,"needs_human_review":false,"rationale":"bad"}',
                "parsed": {
                    "summary": "Bad edit",
                    "owned_paths": ["app/ui/pages/Other.jsx"],
                    "updated_files": {"app/ui/pages/Other.jsx": "x"},
                    "validation_strategy": "skip",
                    "validation_commands": [],
                    "start_preview": False,
                    "needs_human_review": False,
                    "rationale": "bad",
                },
                "usage": {},
            }

    worker = ScopedRefinementCodingWorker(
        capability_service=_BadCapabilityService(),
        config_loader=_enabled_control_plane,
        pack_loader=_pack,
        tool_executor=_FakeToolExecutor(),
        validation_runner=_fake_validation_runner,
        artifact_store=_FakeArtifactStore(),
        output_root=tmp_path,
    )

    result = await worker.execute(
        CodingWorkerRequest(
            app_id="app_1",
            artifact_kind="app_bundle",
            artifact_key="app_bundle",
            artifact_version_id="av_123",
            requested_workflow_id="AppGenerator",
            raw_user_request="Fix the dashboard spacing",
            source_surface="app_build",
            change_class="patch",
            files={"app/ui/pages/Dashboard.jsx": "export default function Dashboard() {}"},
            validation_strategy="skip",
        )
    )

    assert result.eligible is True
    assert result.status == "failed"
    assert "outside the explicit scoped files" in str(result.error)
