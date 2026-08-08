from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from mozaiksai.control_plane import (
    CodingWorkerPlan,
    CodingWorkerRequest,
    ControlPlaneToolCall,
    FileUpdate,
    ScopedRefinementCodingWorker,
    StagedCodingWorkerResult,
    dry_run,
    run_live_staged_coding_worker,
)
from mozaiksai.control_plane.dry_run import RefinementExecutionPlan
from mozaiksai.control_plane.staging import create_refinement_staging_workspace
from scripts.smoke_refinement_live_coding_worker import (
    APP_ID,
    REQUEST_ID,
    REQUEST_TEXT,
    _manual_validation_runner,
    _SmokeArtifactStore,
    _SmokeControlPlaneToolExecutor,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "refinement_live_coding_worker_output.json"


def _require_fixture() -> dict:
    if not FIXTURE_PATH.exists():
        pytest.skip(
            f"Fixture not found: {FIXTURE_PATH.name}. "
            "Run: python scripts/smoke_refinement_live_coding_worker.py --save-fixture"
        )
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _write_source_bundle(root: Path, *, title: str = "Dashboard") -> None:
    (root / "ui" / "pages").mkdir(parents=True, exist_ok=True)
    (root / "ui" / "pages" / "dashboard.yaml").write_text(
        f"page_type: landing\ntitle: {title}\n",
        encoding="utf-8",
    )


def _build_plan(*, request_id: str, staging_root: Path, request: str, app_id: str) -> RefinementExecutionPlan:
    return dry_run.build_refinement_execution_plan_from_route(
        request=request,
        build_family="app_bundle",
        change_class="patch",
        workflow_id="AppGenerator",
        workflow_sequence="app_revision",
        affected_workflows=["AppGenerator"],
        affected_declarative_families=["app_bundle"],
        affected_bundle_paths=["ui/pages/dashboard.yaml"],
        scope_summary="Manual live smoke for a dashboard title patch.",
        app_id=app_id,
        request_id=request_id,
        execution_mode="staged",
        staging_base_path=staging_root,
    )


def test_live_run_manual_only() -> None:
    pytest.skip("manual smoke only; run scripts/smoke_refinement_live_coding_worker.py directly")


def test_fixture_replay_applies_live_worker_output(tmp_path: Path) -> None:
    fixture = _require_fixture()

    request_id = fixture["request_id"]
    app_id = fixture["app_id"]
    request = fixture["request"]
    worker_result = StagedCodingWorkerResult.model_validate(fixture["worker_result"])

    assert request == "Change the dashboard title to Reports Dashboard."
    assert worker_result.request_id == request_id
    assert worker_result.source in {"live_worker", "deterministic"}
    assert len(worker_result.changes) == 1
    assert worker_result.changes[0].path == "ui/pages/dashboard.yaml"
    assert worker_result.changes[0].new_content == "page_type: landing\ntitle: Reports Dashboard\n"
    assert fixture["coding_worker_result"]["status"] == "validated"
    assert fixture["coding_worker_result"]["validation_result"]["validation_status"] == "skipped"
    assert "artifact_persistence_error" not in fixture["coding_worker_result"]["metadata"]
    assert fixture["artifact_store"]["backend"] == "smoke_local_in_memory"
    assert fixture["artifact_store"]["attempted"] is True
    assert fixture["artifact_store"]["created_version_count"] == 1

    source_bundle = tmp_path / "source_app"
    _write_source_bundle(source_bundle)
    source_before = (source_bundle / "ui" / "pages" / "dashboard.yaml").read_text(encoding="utf-8")

    plan = _build_plan(
        request_id=request_id,
        staging_root=tmp_path / ".refinement_staging",
        request=request,
        app_id=app_id,
    )
    staging_result = create_refinement_staging_workspace(
        plan,
        source_bundle_path=source_bundle,
        staging_root=tmp_path / ".refinement_staging",
    )

    staged_result = run_live_staged_coding_worker(plan, staging_result, worker_result)

    staged_file = Path(staging_result.staging_area) / "workspace/ui/pages/dashboard.yaml"
    assert staged_file.read_text(encoding="utf-8") == "page_type: landing\ntitle: Reports Dashboard\n"
    assert (source_bundle / "ui" / "pages" / "dashboard.yaml").read_text(encoding="utf-8") == source_before
    assert staged_result.changed_files[0].path == "ui/pages/dashboard.yaml"
    assert staged_result.changed_files[0].status == "updated"
    assert staged_result.source_mutated is False

    fixture_text = json.dumps(fixture, sort_keys=True).lower()
    forbidden_terms = ["app" + " zero", "app_" + "zero", "mozaiks-" + "app", "bloc" + "united"]
    assert not any(term in fixture_text for term in forbidden_terms)
    assert "appgenerator" not in fixture_text
    assert "execute_workflow" not in fixture_text


@pytest.mark.asyncio
async def test_manual_validation_runner_skips_instead_of_failing() -> None:
    result = await _manual_validation_runner(validation_strategy="skip")

    assert result["success"] is True
    assert result["validation_status"] == "skipped"
    assert result["errors"] == []
    assert result["warnings"]


@pytest.mark.asyncio
async def test_smoke_control_plane_tool_executor_returns_structured_stub_context(tmp_path: Path) -> None:
    executor = _SmokeControlPlaneToolExecutor(
        app_id="refinement-live-worker-smoke",
        request_id=REQUEST_ID,
        request_text=REQUEST_TEXT,
        build_record_id="av_refinement_live_worker_smoke_001",
        source_file_map={"ui/pages/dashboard.yaml": "page_type: landing\ntitle: Dashboard\n"},
        plan=_build_plan(
            request_id=REQUEST_ID,
            staging_root=tmp_path / ".refinement_staging",
            request=REQUEST_TEXT,
            app_id="refinement-live-worker-smoke",
        ),
    )

    revision_context = await executor.execute_tool(
        ControlPlaneToolCall(tool_id="get_revision_context", arguments={}, target="coding_requested"),
        context={
            "checkpoint": "coding_requested",
            "app_id": "refinement-live-worker-smoke",
            "build_family": "app_bundle",
            "build_key": "app_bundle",
            "build_record_id": "av_refinement_live_worker_smoke_001",
            "requested_workflow_id": "AppGenerator",
            "source_surface": "refinement_live_coding_worker_smoke",
            "raw_user_request": REQUEST_TEXT,
            "extra": {"request_id": REQUEST_ID, "selected_file_paths": ["ui/pages/dashboard.yaml"]},
        },
    )
    assert revision_context.success is True
    assert revision_context.output["present"] is True
    assert revision_context.output["current_artifact"]["build_record_id"] == "av_refinement_live_worker_smoke_001"

    workspace_scope = await executor.execute_tool(
        ControlPlaneToolCall(tool_id="get_artifact_workspace_scope", arguments={}, target="coding_requested"),
        context={
            "checkpoint": "coding_requested",
            "app_id": "refinement-live-worker-smoke",
            "build_family": "app_bundle",
            "build_key": "app_bundle",
            "build_record_id": "av_refinement_live_worker_smoke_001",
            "requested_workflow_id": "AppGenerator",
            "source_surface": "refinement_live_coding_worker_smoke",
            "raw_user_request": REQUEST_TEXT,
            "extra": {"selected_file_paths": ["ui/pages/dashboard.yaml"]},
        },
    )
    assert workspace_scope.success is True
    assert workspace_scope.output["present"] is True
    assert workspace_scope.output["selected_scope"]["ui/pages/dashboard.yaml"]["present"] is True

    unsupported = await executor.execute_tool(
        ControlPlaneToolCall(tool_id="unknown_tool", arguments={}, target="coding_requested"),
        context={"checkpoint": "coding_requested", "app_id": "refinement-live-worker-smoke"},
    )
    assert unsupported.success is False
    assert "does not implement" in unsupported.error


@pytest.mark.asyncio
async def test_smoke_artifact_store_records_expected_artifact_save(tmp_path: Path) -> None:
    _SMOKE_PLAN = CodingWorkerPlan(
        summary="Patch the dashboard title.",
        owned_paths=["ui/pages/dashboard.yaml"],
        updated_files=[
            FileUpdate(
                path="ui/pages/dashboard.yaml",
                content="page_type: landing\ntitle: Reports Dashboard\n",
            )
        ],
        validation_strategy="skip",
        validation_commands=[],
        start_preview=False,
        needs_human_review=False,
        rationale="Single-file title update.",
    )

    class _FakeReply:
        async def content(self, *, retries: int = 0) -> Any:
            return _SMOKE_PLAN

    class _FakeAgent:
        async def ask(self, user_prompt: str, **kwargs: Any) -> _FakeReply:  # noqa: ANN003
            return _FakeReply()

    store = _SmokeArtifactStore()
    executor = _SmokeControlPlaneToolExecutor(
        app_id=APP_ID,
        request_id=REQUEST_ID,
        request_text=REQUEST_TEXT,
        build_record_id="av_refinement_live_worker_smoke_001",
        source_file_map={"ui/pages/dashboard.yaml": "page_type: landing\ntitle: Dashboard\n"},
        plan=_build_plan(request_id=REQUEST_ID, staging_root=tmp_path / ".refinement_staging", request=REQUEST_TEXT, app_id=APP_ID),
    )
    worker = ScopedRefinementCodingWorker(
        agent_factory=lambda sp, lc: _FakeAgent(),
        tool_executor=executor,
        source_validation_runner=_manual_validation_runner,
        artifact_store=store,
        output_root=tmp_path / "generated_refinements",
    )

    result = await worker.execute(
        CodingWorkerRequest(
            app_id=APP_ID,
            build_family="app_bundle",
            build_key="app_bundle",
            build_record_id="av_refinement_live_worker_smoke_001",
            requested_workflow_id="AppGenerator",
            raw_user_request=REQUEST_TEXT,
            source_surface="refinement_live_coding_worker_smoke",
            change_class="patch",
            files={"ui/pages/dashboard.yaml": "page_type: landing\ntitle: Dashboard\n"},
            validation_strategy="skip",
            context_seed={
                "request_id": REQUEST_ID,
                "refinement_lane": "ui_patch",
                "affected_bundle_paths": ["ui/pages/dashboard.yaml"],
            },
            metadata={"smoke": "refinement_live_coding_worker"},
        )
    )

    assert result.status == "validated"
    assert "artifact_persistence_error" not in result.metadata
    assert result.metadata["build_record_id"] == "av_refinement_live_worker_smoke_persisted"
    assert len(store.created_versions) == 1
    assert store.calls and store.calls[0]["build_family"] == "app_bundle"
    assert store.created_versions[0].app_id == APP_ID
    assert store.created_versions[0].build_family == "app_bundle"


