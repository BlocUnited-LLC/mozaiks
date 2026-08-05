from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from mozaiksai.control_plane import (
    CodingWorkerPlan,
    CodingWorkerRequest,
    ControlPlaneCapabilityConfig,
    ControlPlaneCheckpointManifest,
    ControlPlaneConfig,
    ControlPlaneManifest,
    ControlPlanePromptDefinition,
    ControlPlanePromptsManifest,
    ControlPlaneToolDefinition,
    ControlPlaneToolResult,
    ControlPlaneToolsManifest,
    FileUpdate,
    LoadedControlPlanePack,
    ScopedRefinementCodingWorker,
)

# ---------------------------------------------------------------------------
# Fake AG2 agent infrastructure
# ---------------------------------------------------------------------------

_GOOD_PLAN = CodingWorkerPlan(
    summary="Patch the dashboard file.",
    owned_paths=["app/ui/pages/Dashboard.jsx"],
    updated_files=[
        FileUpdate(
            path="app/ui/pages/Dashboard.jsx",
            content='export default function Dashboard() { return "patched"; }',
        )
    ],
    validation_strategy="local",
    validation_commands=["npm run build"],
    start_preview=False,
    needs_human_review=False,
    rationale="Single-file UI patch.",
)

_BAD_PLAN = CodingWorkerPlan(
    summary="Bad edit",
    owned_paths=["app/ui/pages/Other.jsx"],
    updated_files=[FileUpdate(path="app/ui/pages/Other.jsx", content="x")],
    validation_strategy="skip",
    validation_commands=[],
    start_preview=False,
    needs_human_review=False,
    rationale="bad",
)


class _FakeReply:
    def __init__(self, result: Any) -> None:
        self._result = result
        self.body = result.model_dump_json() if hasattr(result, "model_dump_json") else str(result)

    async def content(self, *, retries: int = 0) -> Any:
        return self._result


class _FakeAgent:
    """Records ask() calls and returns a preset CodingWorkerPlan."""

    def __init__(self, system_prompt: str, llm_config: dict[str, Any], plan: CodingWorkerPlan = _GOOD_PLAN) -> None:
        self.system_prompt = system_prompt
        self.llm_config = llm_config
        self.plan = plan
        self.calls: list[dict] = []

    async def ask(self, user_prompt: str, **kwargs: Any) -> _FakeReply:
        self.calls.append({"user_prompt": user_prompt, **kwargs})
        return _FakeReply(self.plan)


class _FakeToolExecutor:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def execute_tool(self, call, *, context=None):  # noqa: ANN001, ANN003
        self.calls.append({"call": call, "context": context})
        return ControlPlaneToolResult(success=True, output={"tool_id": call.tool_id, "artifact_version_id": context.artifact_version_id})


async def _fake_source_validation_runner(**kwargs):  # noqa: ANN003
    assert kwargs["app_id"] == "app_1"
    assert "app/ui/pages/Dashboard.jsx" in kwargs["overlay_files"]
    return {
        "success": True,
        "validation_status": "passed",
        "execution_mode": "isolated_workspace_copy",
        "overlay_file_count": len(kwargs["overlay_files"]),
        "command_results": [],
        "fallback_checks": [],
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
        path=Path("factory_app/refinement_harness"),
        manifest=ControlPlaneManifest(
            schema_version="mozaiks.refinement_harness.v1",
            checkpoints=[
                ControlPlaneCheckpointManifest(
                    event="coding_requested",
                    prompt_id="coding_refinement_system",
                    tool_ids=["get_revision_context", "get_artifact_summary"],
                )
            ],
        ),
        prompts=ControlPlanePromptsManifest(
            schema_version="mozaiks.refinement_harness.v1.prompts",
            prompts=[
                ControlPlanePromptDefinition(
                    id="coding_refinement_system",
                    content="coding system prompt from pack",
                )
            ],
        ),
        tools=ControlPlaneToolsManifest(
            schema_version="mozaiks.refinement_harness.tools.v1",
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
    created: list[_FakeAgent] = []
    tool_executor = _FakeToolExecutor()
    artifact_store = _FakeArtifactStore()

    def capturing_factory(system_prompt: str, llm_config: dict) -> _FakeAgent:
        a = _FakeAgent(system_prompt, llm_config)
        created.append(a)
        return a

    worker = ScopedRefinementCodingWorker(
        agent_factory=capturing_factory,
        config_loader=_enabled_control_plane,
        pack_loader=_pack,
        tool_executor=tool_executor,
        source_validation_runner=_fake_source_validation_runner,
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
    updated_dict = {fu.path: fu.content for fu in result.plan.updated_files}
    assert updated_dict["app/ui/pages/Dashboard.jsx"].endswith('"patched"; }')
    assert result.applied_files["app/ui/pages/Dashboard.jsx"].endswith('"patched"; }')
    assert result.validation_result["validation_status"] == "passed"
    assert result.validation_result["validation_strategy"] == "local"
    assert result.validation_result["overlay_file_count"] == 1
    assert result.metadata["build_record_id"] == "av_child_1"
    assert result.metadata["bundle_mode"] == "staged_refinement_bundle"
    assert result.metadata["source_validation_status"] == "passed"

    assert len(created) == 1
    assert created[0].system_prompt == "coding system prompt from pack"
    assert created[0].llm_config == {"model": "gpt-5.2-codex", "temperature": 0.1}
    assert '"input_files"' in created[0].calls[0]["user_prompt"]
    assert '"refinement_context"' in created[0].calls[0]["user_prompt"]

    assert len(tool_executor.calls) == 2
    assert artifact_store.calls[0]["parent_version_id"] == "av_123"
    assert artifact_store.calls[0]["artifact_kind"] == "app_bundle"
    assert artifact_store.calls[0]["lifecycle_status"].value == "draft"
    assert artifact_store.calls[0]["validation_status"].value == "passed"
    assert artifact_store.calls[0]["commit_metadata"]["metadata"]["applied_paths"] == [
        "app/ui/pages/Dashboard.jsx"
    ]
    assert artifact_store.calls[0]["commit_metadata"]["metadata"]["source_validation_result"]["validation_status"] == "passed"


@pytest.mark.asyncio
async def test_coding_worker_rejects_non_patch_requests() -> None:
    worker = ScopedRefinementCodingWorker(
        agent_factory=lambda sp, lc: _FakeAgent(sp, lc),
        config_loader=_enabled_control_plane,
        pack_loader=_pack,
        tool_executor=_FakeToolExecutor(),
        source_validation_runner=_fake_source_validation_runner,
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
    worker = ScopedRefinementCodingWorker(
        agent_factory=lambda sp, lc: _FakeAgent(sp, lc, plan=_BAD_PLAN),
        config_loader=_enabled_control_plane,
        pack_loader=_pack,
        tool_executor=_FakeToolExecutor(),
        source_validation_runner=_fake_source_validation_runner,
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


@pytest.mark.asyncio
async def test_coding_worker_surfaces_artifact_persistence_errors(tmp_path: Path) -> None:
    class _BrokenArtifactStore:
        async def create_artifact_version(self, **kwargs):  # noqa: ANN003
            raise RuntimeError('artifact store unavailable')

    worker = ScopedRefinementCodingWorker(
        agent_factory=lambda sp, lc: _FakeAgent(sp, lc),
        config_loader=_enabled_control_plane,
        pack_loader=_pack,
        tool_executor=_FakeToolExecutor(),
        source_validation_runner=_fake_source_validation_runner,
        artifact_store=_BrokenArtifactStore(),
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

    assert result.status == "failed"
    assert result.error is not None and "ARTIFACT_PERSISTENCE_FAILED" in result.error
    assert "ARTIFACT_PERSISTENCE_FAILED" in result.metadata["artifact_persistence_error"]
