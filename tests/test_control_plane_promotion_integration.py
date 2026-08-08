"""End-to-end integration tests for the control-plane promotion flow.

Covers the full chain:
  route → plan → stage → code → persist → staging application

These tests use real filesystem I/O (tmp_path) and a realistic in-process
artifact store. No LLM calls are made — the coding agent is injected as a
fake that returns a deterministic CodingWorkerPlan.

The flow under test:
  1. build_refinement_execution_plan_from_route  →  RefinementExecutionPlan
  2. create_refinement_staging_workspace          →  RefinementStagingResult
  3. ScopedRefinementCodingWorker.execute()       →  CodingWorkerResult
     (internally calls artifact_store.create_build_record)
  4. Derive StagedCodingWorkerResult from the worker output
  5. run_live_staged_coding_worker               →  ScopedRefinementResult
     (writes changed files into staging area, never touches source)

Each test verifies a distinct production concern:
  - artifact version fields (parent, lifecycle, validation, applied paths)
  - source bundle immutability throughout
  - staging area content matches the coding worker plan exactly
  - metadata flows end-to-end (request_id, build_record_id)
  - error paths (out-of-scope edits, broken artifact store)
"""
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
from mozaiksai.control_plane.dry_run import build_refinement_execution_plan_from_route
from mozaiksai.control_plane.staged_coding_worker import (
    StagedCodingWorkerChange,
    StagedCodingWorkerResult,
    run_live_staged_coding_worker,
)
from mozaiksai.control_plane.staging import create_refinement_staging_workspace

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

_APP_ID = "cp-promotion-integration"
_build_record_id = "av_parent_001"
_DASHBOARD_PATH = "ui/pages/dashboard.yaml"
_DASHBOARD_ORIGINAL = "page_type: landing\ntitle: Dashboard\n"
_DASHBOARD_UPDATED = "page_type: landing\ntitle: Reports Dashboard\n"

# ---------------------------------------------------------------------------
# Fake infrastructure
# ---------------------------------------------------------------------------


class _FakeReply:
    def __init__(self, result: Any) -> None:
        self._result = result

    async def content(self, *, retries: int = 0) -> Any:
        return self._result


class _FakeAgent:
    def __init__(self, system_prompt: str, llm_config: dict[str, Any], plan: CodingWorkerPlan) -> None:
        self.system_prompt = system_prompt
        self.llm_config = llm_config
        self.plan = plan
        self.calls: list[str] = []

    async def ask(self, user_prompt: str, **kwargs: Any) -> _FakeReply:
        self.calls.append(user_prompt)
        return _FakeReply(self.plan)


class _BuildRecord:
    def __init__(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)
        self.id = kwargs.get("id", "av_child_001")


class _CapturingArtifactStore:
    """Realistic artifact store that records the full create call and returns a typed stub."""

    def __init__(self, *, child_id: str = "av_child_001") -> None:
        self.calls: list[dict[str, Any]] = []
        self._child_id = child_id

    async def create_build_record(self, **kwargs: Any) -> _BuildRecord:
        self.calls.append(dict(kwargs))
        return _BuildRecord(id=self._child_id, **kwargs)

    @property
    def last_call(self) -> dict[str, Any]:
        assert self.calls, "No create_build_record calls recorded"
        return self.calls[-1]


class _FakeToolExecutor:
    async def execute_tool(self, call: Any, *, context: Any = None) -> ControlPlaneToolResult:
        return ControlPlaneToolResult(
            success=True,
            output={"tool_id": call.tool_id, "build_record_id": context.build_record_id},
        )


async def _skip_validation(**kwargs: Any) -> dict[str, Any]:
    return {
        "success": True,
        "validation_status": "skipped",
        "execution_mode": "not_executed",
        "overlay_file_count": len(kwargs.get("overlay_files") or {}),
        "command_results": [],
        "fallback_checks": [],
        "warnings": ["Validation skipped in integration test."],
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pack() -> LoadedControlPlanePack:
    return LoadedControlPlanePack(
        path=Path("factory_app/refinement_harness"),
        manifest=ControlPlaneManifest(
            schema_version="mozaiks.refinement_harness.v1",
            checkpoints=[
                ControlPlaneCheckpointManifest(
                    event="coding_requested",
                    prompt_id="coding_refinement_system",
                    tool_ids=["get_revision_context"],
                )
            ],
        ),
        prompts=ControlPlanePromptsManifest(
            schema_version="mozaiks.refinement_harness.v1.prompts",
            prompts=[
                ControlPlanePromptDefinition(
                    id="coding_refinement_system",
                    content="You are a scoped code editor. Edit only the files listed.",
                )
            ],
        ),
        tools=ControlPlaneToolsManifest(
            schema_version="mozaiks.refinement_harness.tools.v1",
            tools=[
                ControlPlaneToolDefinition(
                    id="get_revision_context",
                    kind="context_tool",
                    description="revision context",
                    entrypoint="example.tools:get_revision_context",
                    available_to=["coding_requested"],
                )
            ],
        ),
    )


def _config() -> ControlPlaneConfig:
    return ControlPlaneConfig(
        enabled=True,
        coding=ControlPlaneCapabilityConfig(
            enabled=True,
            llm_config={"model": "gpt-4o", "temperature": 0.1},
        ),
    )


def _dashboard_plan() -> CodingWorkerPlan:
    return CodingWorkerPlan(
        summary="Update dashboard title to Reports Dashboard.",
        owned_paths=[_DASHBOARD_PATH],
        updated_files=[FileUpdate(path=_DASHBOARD_PATH, content=_DASHBOARD_UPDATED)],
        validation_strategy="skip",
        validation_commands=[],
        start_preview=False,
        needs_human_review=False,
        rationale="Single-field title update.",
    )


def _write_source_bundle(root: Path) -> None:
    (root / "ui" / "pages").mkdir(parents=True, exist_ok=True)
    (root / "ui" / "pages" / "dashboard.yaml").write_text(_DASHBOARD_ORIGINAL, encoding="utf-8")


def _make_request(*, request_id: str, staging_root: Path) -> CodingWorkerRequest:
    return CodingWorkerRequest(
        app_id=_APP_ID,
        build_family="app_bundle",
        build_key="app_bundle",
        build_record_id=_build_record_id,
        requested_workflow_id="AppGenerator",
        raw_user_request="Change the dashboard title to Reports Dashboard.",
        source_surface="cp_promotion_integration_test",
        change_class="patch",
        files={_DASHBOARD_PATH: _DASHBOARD_ORIGINAL},
        validation_strategy="skip",
        context_seed={
            "request_id": request_id,
            "affected_bundle_paths": [_DASHBOARD_PATH],
        },
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_plan_stage_code_persist_chain(tmp_path: Path) -> None:
    """Full chain: plan → stage → coding worker → artifact store.

    Verifies that the artifact store receives all required fields and that
    the coding worker result carries the persisted version ID in metadata.
    """
    source = tmp_path / "source"
    staging_root = tmp_path / ".staging"
    _write_source_bundle(source)

    # Step 1: build execution plan
    plan = build_refinement_execution_plan_from_route(
        request="Change the dashboard title to Reports Dashboard.",
        build_family="app_bundle",
        change_class="patch",
        workflow_id="AppGenerator",
        workflow_sequence="app_revision",
        affected_workflows=["AppGenerator"],
        affected_declarative_families=["app_bundle"],
        affected_bundle_paths=[_DASHBOARD_PATH],
        scope_summary="Single-file title patch.",
        app_id=_APP_ID,
        execution_mode="staged",
        staging_base_path=staging_root,
    )

    assert plan.change_class == "patch"
    assert plan.execution_mode == "staged"
    assert plan.app_id == _APP_ID
    assert _DASHBOARD_PATH in plan.affected_bundle_paths

    # Step 2: create staging workspace
    staging_result = create_refinement_staging_workspace(
        plan,
        source_bundle_path=source,
        staging_root=staging_root,
    )

    assert staging_result.request_id == plan.request_id
    staged_dashboard_path = Path(staging_result.staging_area) / "workspace" / _DASHBOARD_PATH
    assert staged_dashboard_path.exists()
    assert staged_dashboard_path.read_text(encoding="utf-8") == _DASHBOARD_ORIGINAL

    # Step 3: run coding worker with fake agent
    artifact_store = _CapturingArtifactStore(child_id="av_child_promotion_001")
    agent_instances: list[_FakeAgent] = []

    def _factory(system_prompt: str, llm_config: dict[str, Any]) -> _FakeAgent:
        agent = _FakeAgent(system_prompt, llm_config, _dashboard_plan())
        agent_instances.append(agent)
        return agent

    worker = ScopedRefinementCodingWorker(
        agent_factory=_factory,
        config_loader=_config,
        pack_loader=_pack,
        tool_executor=_FakeToolExecutor(),
        source_validation_runner=_skip_validation,
        artifact_store=artifact_store,
        output_root=tmp_path / "output",
    )

    result = await worker.execute(_make_request(request_id=plan.request_id, staging_root=staging_root))

    # Coding worker result assertions
    assert result.eligible is True
    assert result.status == "validated"
    assert result.applied_files[_DASHBOARD_PATH] == _DASHBOARD_UPDATED
    assert result.metadata["build_record_id"] == "av_child_promotion_001"
    assert "artifact_persistence_error" not in result.metadata

    # Artifact store call assertions — the full promotion contract
    call = artifact_store.last_call
    assert call["parent_build_record_id"] == _build_record_id
    assert call["build_family"] == "app_bundle"
    assert call["build_key"] == "app_bundle"
    assert call["lifecycle_status"].value == "draft"
    assert call["validation_status"].value in {"passed", "skipped"}
    applied = call["commit_metadata"]["metadata"]["applied_paths"]
    assert _DASHBOARD_PATH in applied

    # Source bundle must be unmutated
    source_content = (source / "ui" / "pages" / "dashboard.yaml").read_text(encoding="utf-8")
    assert source_content == _DASHBOARD_ORIGINAL


@pytest.mark.asyncio
async def test_staging_workspace_apply_writes_to_staging_only(tmp_path: Path) -> None:
    """Staging application must write changed files to staging area only.

    Verifies that after run_live_staged_coding_worker:
    - the staged file has the new content
    - the source bundle file is unchanged
    - ScopedRefinementResult carries the right metadata
    """
    source = tmp_path / "source"
    staging_root = tmp_path / ".staging"
    _write_source_bundle(source)

    plan = build_refinement_execution_plan_from_route(
        request="Change the dashboard title to Reports Dashboard.",
        build_family="app_bundle",
        change_class="patch",
        workflow_id="AppGenerator",
        workflow_sequence="app_revision",
        affected_workflows=["AppGenerator"],
        affected_declarative_families=["app_bundle"],
        affected_bundle_paths=[_DASHBOARD_PATH],
        scope_summary="Single-file title patch.",
        app_id=_APP_ID,
        execution_mode="staged",
        staging_base_path=staging_root,
    )

    staging_result = create_refinement_staging_workspace(
        plan,
        source_bundle_path=source,
        staging_root=staging_root,
    )

    worker_result = StagedCodingWorkerResult(
        request_id=plan.request_id,
        changes=[
            StagedCodingWorkerChange(
                path=_DASHBOARD_PATH,
                new_content=_DASHBOARD_UPDATED,
                reason="Title updated as requested.",
            )
        ],
        source="live_worker",
    )

    staged_result = run_live_staged_coding_worker(plan, staging_result, worker_result)

    # Staging area must have the new content
    staged_file = Path(staging_result.staging_area) / "workspace" / _DASHBOARD_PATH
    assert staged_file.read_text(encoding="utf-8") == _DASHBOARD_UPDATED

    # Source must be untouched
    assert (source / "ui" / "pages" / "dashboard.yaml").read_text(encoding="utf-8") == _DASHBOARD_ORIGINAL

    # Result contract
    assert staged_result.source_mutated is False
    assert staged_result.mutation_scope == "staging_only"
    assert staged_result.execution_mode == "scoped_staging"
    assert staged_result.request_id == plan.request_id
    changed = {f.path: f for f in staged_result.changed_files}
    assert _DASHBOARD_PATH in changed
    assert changed[_DASHBOARD_PATH].status == "updated"


@pytest.mark.asyncio
async def test_artifact_store_fields_match_promotion_contract(tmp_path: Path) -> None:
    """Artifact store receives all fields required for promotion tracking.

    Verifies commit_metadata shape, applied_paths list, and that the
    child version ID flows back through result.metadata.
    """
    artifact_store = _CapturingArtifactStore(child_id="av_child_promo_contract")

    worker = ScopedRefinementCodingWorker(
        agent_factory=lambda sp, lc: _FakeAgent(sp, lc, _dashboard_plan()),
        config_loader=_config,
        pack_loader=_pack,
        tool_executor=_FakeToolExecutor(),
        source_validation_runner=_skip_validation,
        artifact_store=artifact_store,
        output_root=tmp_path / "output",
    )

    request_id = "promo-contract-test-001"
    result = await worker.execute(
        CodingWorkerRequest(
            app_id=_APP_ID,
            build_family="app_bundle",
            build_key="app_bundle",
            build_record_id="av_parent_promo",
            requested_workflow_id="AppGenerator",
            raw_user_request="Change the dashboard title to Reports Dashboard.",
            source_surface="cp_promotion_integration_test",
            change_class="patch",
            files={_DASHBOARD_PATH: _DASHBOARD_ORIGINAL},
            validation_strategy="skip",
            context_seed={
                "request_id": request_id,
                "affected_bundle_paths": [_DASHBOARD_PATH],
            },
        )
    )

    assert result.status == "validated"
    assert result.metadata["build_record_id"] == "av_child_promo_contract"

    call = artifact_store.last_call
    # Parent lineage
    assert call["parent_build_record_id"] == "av_parent_promo"

    # Commit metadata must carry applied_paths for promotion audit
    meta = call["commit_metadata"]["metadata"]
    assert "applied_paths" in meta
    assert _DASHBOARD_PATH in meta["applied_paths"]
    assert "bundle_mode" in meta

    # lifecycle draft — not yet promoted
    assert call["lifecycle_status"].value == "draft"


@pytest.mark.asyncio
async def test_broken_artifact_store_sets_failed_status_and_surfaces_error(tmp_path: Path) -> None:
    """Artifact store failure must flip the coding result to 'failed'.

    The worker validates and writes files successfully, then fails to persist.
    Result.status becomes 'failed' so the control plane does not treat this as
    a successful validated artifact.  The persistence error is recorded in both
    result.error and result.metadata so operators can diagnose the root cause.
    """

    class _BrokenStore:
        async def create_build_record(self, **kwargs: Any) -> None:
            raise RuntimeError("artifact store unavailable")

    worker = ScopedRefinementCodingWorker(
        agent_factory=lambda sp, lc: _FakeAgent(sp, lc, _dashboard_plan()),
        config_loader=_config,
        pack_loader=_pack,
        tool_executor=_FakeToolExecutor(),
        source_validation_runner=_skip_validation,
        artifact_store=_BrokenStore(),
        output_root=tmp_path / "output",
    )

    result = await worker.execute(
        CodingWorkerRequest(
            app_id=_APP_ID,
            build_family="app_bundle",
            build_key="app_bundle",
            build_record_id="av_parent_broken",
            requested_workflow_id="AppGenerator",
            raw_user_request="Change the dashboard title to Reports Dashboard.",
            source_surface="cp_promotion_integration_test",
            change_class="patch",
            files={_DASHBOARD_PATH: _DASHBOARD_ORIGINAL},
            validation_strategy="skip",
            context_seed={"affected_bundle_paths": [_DASHBOARD_PATH]},
        )
    )

    # Status is failed — artifact was not persisted despite valid code change
    assert result.status == "failed"
    # Error identifies persistence as the failure cause
    assert result.error is not None
    assert "ARTIFACT_PERSISTENCE_FAILED" in result.error
    assert "artifact store unavailable" in result.error
    # Metadata also carries the raw error for diagnostics
    assert "ARTIFACT_PERSISTENCE_FAILED" in result.metadata["artifact_persistence_error"]
    # applied_files still carries the generated content for inspection
    assert result.applied_files[_DASHBOARD_PATH] == _DASHBOARD_UPDATED


@pytest.mark.asyncio
async def test_request_id_threads_through_all_layers(tmp_path: Path) -> None:
    """The same request_id must flow through plan, staging, worker result, and staged result."""
    source = tmp_path / "source"
    staging_root = tmp_path / ".staging"
    _write_source_bundle(source)

    plan = build_refinement_execution_plan_from_route(
        request="Change the dashboard title to Reports Dashboard.",
        build_family="app_bundle",
        change_class="patch",
        workflow_id="AppGenerator",
        workflow_sequence="app_revision",
        affected_bundle_paths=[_DASHBOARD_PATH],
        scope_summary="Single-file title patch.",
        app_id=_APP_ID,
        execution_mode="staged",
        staging_base_path=staging_root,
    )

    staging_result = create_refinement_staging_workspace(
        plan,
        source_bundle_path=source,
        staging_root=staging_root,
    )

    # Both staging and plan must share the same request_id
    assert staging_result.request_id == plan.request_id

    worker_result = StagedCodingWorkerResult(
        request_id=plan.request_id,
        changes=[
            StagedCodingWorkerChange(path=_DASHBOARD_PATH, new_content=_DASHBOARD_UPDATED)
        ],
        source="live_worker",
    )

    staged_result = run_live_staged_coding_worker(plan, staging_result, worker_result)

    # ScopedRefinementResult must echo the same request_id
    assert staged_result.request_id == plan.request_id

    # Mismatched request_id must be rejected
    bad_worker_result = StagedCodingWorkerResult(
        request_id="WRONG_ID",
        changes=[StagedCodingWorkerChange(path=_DASHBOARD_PATH, new_content=_DASHBOARD_UPDATED)],
        source="live_worker",
    )
    with pytest.raises(ValueError, match="request_id must match"):
        run_live_staged_coding_worker(plan, staging_result, bad_worker_result)


def test_dry_run_plan_does_not_produce_staging_area(tmp_path: Path) -> None:
    """dry_run execution mode must set execution_mode='dry_run' and no staging_area."""
    plan = build_refinement_execution_plan_from_route(
        request="Change the dashboard title to Reports Dashboard.",
        build_family="app_bundle",
        change_class="patch",
        workflow_id="AppGenerator",
        workflow_sequence="app_revision",
        affected_bundle_paths=[_DASHBOARD_PATH],
        scope_summary="Single-file title patch.",
        app_id=_APP_ID,
        execution_mode="dry_run",
    )

    assert plan.execution_mode == "dry_run"
    assert plan.mutation_allowed is False
    assert plan.generated_files_changed is False
    # dry_run plan must not have a staging area path
    assert plan.staging_area is None


def test_dry_run_plan_carries_required_metadata(tmp_path: Path) -> None:
    """A dry_run plan must carry all fields needed for the caller to decide next action."""
    plan = build_refinement_execution_plan_from_route(
        request="Add an archive action to the projects module.",
        build_family="app_bundle",
        change_class="feature",
        workflow_id="AppGenerator",
        workflow_sequence="app_revision",
        affected_workflows=["AppGenerator"],
        affected_declarative_families=["app_bundle", "module_contract"],
        affected_bundle_paths=[
            "modules/projects/module.yaml",
            "modules/projects/backend/handler.py",
            "modules/projects/backend/service.py",
        ],
        scope_summary="Projects module needs new archive_project action.",
        app_id=_APP_ID,
        execution_mode="dry_run",
    )

    assert plan.change_class == "feature"
    assert plan.workflow_id == "AppGenerator"
    assert plan.workflow_sequence == "app_revision"
    assert plan.app_id == _APP_ID
    assert "modules/projects/module.yaml" in plan.affected_bundle_paths
    assert "modules/projects/backend/handler.py" in plan.affected_bundle_paths
    assert "app_bundle" in plan.affected_declarative_families
    assert "module_contract" in plan.affected_declarative_families
    assert plan.profiles is not None
    assert plan.validation_plan is not None
    assert plan.request_id  # non-empty auto-generated


def test_staged_plan_carries_staging_root_in_output_workspace(tmp_path: Path) -> None:
    """staged execution mode must record the output workspace path in the plan."""
    staging_root = tmp_path / ".staging"
    plan = build_refinement_execution_plan_from_route(
        request="Change the dashboard title to Reports Dashboard.",
        build_family="app_bundle",
        change_class="patch",
        workflow_id="AppGenerator",
        workflow_sequence="app_revision",
        affected_bundle_paths=[_DASHBOARD_PATH],
        scope_summary="Single-file title patch.",
        app_id=_APP_ID,
        execution_mode="staged",
        staging_base_path=staging_root,
    )

    assert plan.execution_mode == "staged"
    assert plan.output_workspace is not None
    # Normalise separators before comparing (Windows uses backslash, plan uses forward slash)
    assert staging_root.as_posix() in plan.output_workspace.replace("\\", "/")


def test_staging_workspace_skips_secret_files(tmp_path: Path) -> None:
    """Staging must skip secret files and record them with status=skipped_secret."""
    source = tmp_path / "source"
    staging_root = tmp_path / ".staging"
    (source / "ui" / "pages").mkdir(parents=True, exist_ok=True)
    (source / "ui" / "pages" / "dashboard.yaml").write_text(_DASHBOARD_ORIGINAL, encoding="utf-8")
    (source / ".env").write_text("OPENAI_API_KEY=sk-test\n", encoding="utf-8")

    plan = build_refinement_execution_plan_from_route(
        request="Change the dashboard title to Reports Dashboard.",
        build_family="app_bundle",
        change_class="patch",
        workflow_id="AppGenerator",
        workflow_sequence="app_revision",
        affected_bundle_paths=[_DASHBOARD_PATH, ".env"],
        scope_summary="title patch with accidental secret path",
        app_id=_APP_ID,
        execution_mode="staged",
        staging_base_path=staging_root,
    )

    staging_result = create_refinement_staging_workspace(
        plan,
        source_bundle_path=source,
        staging_root=staging_root,
    )

    statuses = {f.path: f.status for f in staging_result.files}
    # dashboard.yaml must be staged
    assert statuses.get(_DASHBOARD_PATH) == "copied"
    # .env must be excluded as a secret path
    assert statuses.get(".env") == "skipped_secret"

    # .env must not appear in the staging workspace
    assert not (Path(staging_result.staging_area) / "workspace" / ".env").exists()


@pytest.mark.asyncio
async def test_coding_worker_rejects_out_of_scope_edits_in_integration(tmp_path: Path) -> None:
    """The coding worker must reject a plan that edits files outside the scoped set.

    This verifies the scope guard fires even in the integrated path where
    the files dict is used to derive the allowed paths.
    """
    out_of_scope_plan = CodingWorkerPlan(
        summary="Bad edit outside scope.",
        owned_paths=["ui/pages/other.yaml"],
        updated_files=[FileUpdate(path="ui/pages/other.yaml", content="page_type: other\n")],
        validation_strategy="skip",
        validation_commands=[],
        start_preview=False,
        needs_human_review=False,
        rationale="test",
    )

    worker = ScopedRefinementCodingWorker(
        agent_factory=lambda sp, lc: _FakeAgent(sp, lc, out_of_scope_plan),
        config_loader=_config,
        pack_loader=_pack,
        tool_executor=_FakeToolExecutor(),
        source_validation_runner=_skip_validation,
        artifact_store=_CapturingArtifactStore(),
        output_root=tmp_path / "output",
    )

    result = await worker.execute(
        CodingWorkerRequest(
            app_id=_APP_ID,
            build_family="app_bundle",
            build_key="app_bundle",
            build_record_id="av_scope_guard",
            requested_workflow_id="AppGenerator",
            raw_user_request="Change the dashboard title.",
            source_surface="cp_promotion_integration_test",
            change_class="patch",
            files={_DASHBOARD_PATH: _DASHBOARD_ORIGINAL},
            validation_strategy="skip",
        )
    )

    assert result.eligible is True
    assert result.status == "failed"
    assert "outside the explicit scoped files" in str(result.error)

