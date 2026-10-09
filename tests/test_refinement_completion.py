from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mozaiksai.control_plane import refinement_tracking
from mozaiksai.control_plane.config import ControlPlaneConfig
from mozaiksai.control_plane.contracts import (
    ContractSurfacePlan,
    ProposedFileChange,
    StagedPatchProposal,
    SurfacePlanExecutionResult,
)
from mozaiksai.control_plane.implementations import coding_worker as coding_module
from mozaiksai.control_plane.implementations import orchestration_control
from mozaiksai.control_plane.implementations.coding_worker import ScopedRefinementCodingWorker
from mozaiksai.control_plane.implementations.refinement_router import (
    ChangeIntent,
    ImpactSet,
    RefinementRequest,
    RefinementRoutingDecision,
)
from mozaiksai.core.artifacts.models import BuildRecord
from mozaiksai.core.session.build_binding import RunBuildBinding


def _candidate_validation(status):
    return {
        "validation_status": status,
        "app_bundle_acceptance_result": {
            "status": "skipped" if status == "skipped" else "passed",
            "passed": status != "skipped",
        },
        "app_validation_result": {
            "validation_status": status,
            "validation_strategy": "skip" if status == "skipped" else "docker",
        },
    }


def _saved_candidate(**kwargs):
    return BuildRecord(id="candidate", version_number=1, lineage_root_id="parent", **kwargs)


def _saved_surface_candidate(**kwargs):
    return BuildRecord(id="surface-candidate", version_number=1, lineage_root_id="parent", **kwargs)


def _artifact_store(create):
    parent = BuildRecord(
        id="parent", app_id="customer", build_family="app_bundle", build_key="app_bundle",
        version_number=1, lineage_root_id="parent",
    )
    return SimpleNamespace(create_build_record=create, get_build_record=AsyncMock(return_value=parent))


def _config():
    return ControlPlaneConfig(
        enabled=True,
        llm_profiles={"codegen": {"llm_config": {"model": "test-model"}}},
        coding={"enabled": True, "llm_profile": "codegen"},
    )


def _request(**updates):
    refinement = RefinementRequest(
        app_id="studio", target_app_id="customer", build_family="app_bundle",
        build_record_id="parent", raw_user_request="Update title", extra={"request_id": "request-1"},
    )
    harness = orchestration_control.OrchestrationControlHarness(config_loader=_config)
    request = harness.build_coding_request(
        refinement_request=refinement,
        routing_decision=RefinementRoutingDecision(
            workflow_id="AppGenerator", refinement_request=refinement,
            change_intent=ChangeIntent(change_class="patch", rationale="Title change"),
            impact_set=ImpactSet(),
        ),
        payload={"files": {"ui/page.json": '{"title":"Before"}'}},
    )
    assert request is not None
    return request.model_copy(update=updates)


def _proposal(**updates):
    values = {
        "proposal_id": "proposal-1", "provider_id": "test-provider", "status": "completed",
        "summary": "Update title", "rationale": "Requested title change",
        "owned_paths": ["ui/page.json"],
        "changed_files": [ProposedFileChange(path="ui/page.json", content='{"title":"After"}')],
        "usage": {"total_tokens": 17},
    }
    return StagedPatchProposal(**{**values, **updates})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("validation_status", "coding_status", "event_kind", "outcome"),
    [
        ("passed", "validated", "completed", "ok"),
        ("failed", "failed", "failed", "error"),
        ("skipped", "planned", "planned", "skipped"),
        ("pending", "planned", "planned", "skipped"),
        ("warning", "planned", "planned", "skipped"),
    ],
)
async def test_worker_validation_and_saved_draft_agree_with_completion_event(
    monkeypatch, tmp_path, validation_status, coding_status, event_kind, outcome,
):
    recorded = AsyncMock()
    monkeypatch.setattr(orchestration_control, "record_refinement_event", recorded)
    store = _artifact_store(AsyncMock(side_effect=_saved_candidate))
    worker = ScopedRefinementCodingWorker(
        provider=SimpleNamespace(execute=AsyncMock(return_value=_proposal())),
        config_loader=_config, artifact_store=store, output_root=tmp_path,
        candidate_validation_runner=AsyncMock(return_value=_candidate_validation(validation_status)),
    )
    harness = orchestration_control.OrchestrationControlHarness(coding_worker=worker, config_loader=_config)

    result = await harness.execute_coding_request(_request(validation_strategy="docker"))

    assert result.status == coding_status
    assert result.metadata["build_record_id"] == "candidate"
    saved = store.create_build_record.await_args.kwargs
    assert saved["lifecycle_status"].value == "draft"
    assert (saved["validation_status"].value == "passed") == (validation_status == "passed")
    event = recorded.await_args.kwargs
    assert recorded.await_count == 1
    assert (event["event_kind"], event["outcome"]) == (event_kind, outcome)
    assert event["request_id"] == "request-1"
    assert event["metadata"]["validation_status"] == validation_status
    assert event["metadata"]["build_record_id"] == "candidate"
    assert event["metadata"]["target_app_id"] == "customer"
    assert event["metadata"]["token_count"] == 17


@pytest.mark.asyncio
async def test_failed_artifact_persistence_does_not_emit_success(monkeypatch, tmp_path):
    recorded = AsyncMock()
    monkeypatch.setattr(orchestration_control, "record_refinement_event", recorded)
    worker = ScopedRefinementCodingWorker(
        provider=SimpleNamespace(execute=AsyncMock(return_value=_proposal())),
        config_loader=_config, output_root=tmp_path,
        artifact_store=_artifact_store(AsyncMock(side_effect=RuntimeError("store unavailable"))),
        candidate_validation_runner=AsyncMock(return_value=_candidate_validation("passed")),
    )
    harness = orchestration_control.OrchestrationControlHarness(coding_worker=worker, config_loader=_config)

    result = await harness.execute_coding_request(_request())

    assert result.status == "failed"
    event = recorded.await_args.kwargs
    assert event["event_kind"] == "failed"
    assert event["outcome"] == "error"
    assert "ARTIFACT_PERSISTENCE_FAILED" in event["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("store_result", ["unavailable", "missing_reference", "saved"])
async def test_configured_content_store_must_save_before_a_draft_can_be_ready(monkeypatch, tmp_path, store_result):
    recorded = AsyncMock()
    monkeypatch.setattr(orchestration_control, "record_refinement_event", recorded)
    upload = AsyncMock(return_value="bundle-ref" if store_result == "saved" else "")
    if store_result == "unavailable":
        upload.side_effect = OSError("content store unavailable")
    monkeypatch.setattr(
        coding_module, "get_artifact_content_store",
        lambda: SimpleNamespace(backend_name="gridfs", put_bundle=upload),
    )
    store = _artifact_store(AsyncMock(side_effect=_saved_candidate))
    worker = ScopedRefinementCodingWorker(
        provider=SimpleNamespace(execute=AsyncMock(return_value=_proposal())),
        config_loader=_config, artifact_store=store, output_root=tmp_path,
        candidate_validation_runner=AsyncMock(return_value=_candidate_validation("passed")),
    )
    harness = orchestration_control.OrchestrationControlHarness(coding_worker=worker, config_loader=_config)

    result = await harness.execute_coding_request(_request())

    upload.assert_awaited_once()
    if store_result == "saved":
        assert result.status == "validated"
        metadata = store.create_build_record.await_args.kwargs["commit_metadata"]["metadata"]
        assert metadata["content_ref"] == "bundle-ref"
        assert metadata["content_backend"] == "gridfs"
    else:
        assert result.status == "failed"
        assert "CONTENT_STORE_PUT_BUNDLE_FAILED" in result.error
        assert not result.metadata.get("build_record_id")
        store.create_build_record.assert_not_awaited()
        assert recorded.await_args.kwargs["event_kind"] == "failed"


@pytest.mark.asyncio
async def test_ineligible_request_never_invokes_provider_or_reports_completion(monkeypatch, tmp_path):
    recorded = AsyncMock()
    monkeypatch.setattr(orchestration_control, "record_refinement_event", recorded)
    provider = SimpleNamespace(execute=AsyncMock())
    worker = ScopedRefinementCodingWorker(provider=provider, config_loader=_config, output_root=tmp_path)
    harness = orchestration_control.OrchestrationControlHarness(coding_worker=worker, config_loader=_config)

    result = await harness.execute_coding_request(_request().model_copy(update={"build_record_id": None}))

    assert result.status == "ineligible"
    provider.execute.assert_not_awaited()
    event = recorded.await_args.kwargs
    assert event["event_kind"] == "ineligible"
    assert event["outcome"] == "skipped"
    assert event["metadata"]["blocked_reason"] == result.blocked_reason


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy", ["unsupported", "inline"])
async def test_unsupported_validation_never_invokes_either_coding_provider(tmp_path, strategy):
    provider = SimpleNamespace(execute=AsyncMock())
    acp_provider = SimpleNamespace(execute=AsyncMock())
    validate = AsyncMock()
    worker = ScopedRefinementCodingWorker(
        provider=provider, acp_provider=acp_provider, config_loader=_config,
        candidate_validation_runner=validate, output_root=tmp_path,
    )

    result = await worker.execute(_request(validation_strategy=strategy))

    assert result.status == "ineligible"
    assert result.blocked_reason == f"Unsupported coding validation strategy: {strategy}"
    provider.execute.assert_not_awaited()
    acp_provider.execute.assert_not_awaited()
    validate.assert_not_awaited()


@pytest.mark.asyncio
async def test_coding_request_preserves_derived_id_through_preparation_and_completion(monkeypatch):
    recorded = AsyncMock()
    monkeypatch.setattr(orchestration_control, "record_refinement_event", recorded)
    worker = SimpleNamespace(execute=AsyncMock(return_value=coding_module.CodingWorkerResult(
        eligible=False, status="ineligible", blocked_reason="test",
    )))
    harness = orchestration_control.OrchestrationControlHarness(coding_worker=worker, config_loader=_config)
    refinement = RefinementRequest(
        app_id="studio", target_app_id="customer", user_id="alice", build_family="app_bundle",
        build_record_id="parent", raw_user_request="Update title",
    )
    request = harness.build_coding_request(
        refinement_request=refinement,
        routing_decision=RefinementRoutingDecision(
            workflow_id="AppGenerator", refinement_request=refinement,
            change_intent=ChangeIntent(change_class="patch", rationale="Title change"),
            impact_set=ImpactSet(),
        ),
        payload={"files": {"ui/page.json": "{}"}},
    )
    assert request is not None
    assert request.context_seed["request_id"] == refinement.request_id
    assert RefinementRequest.model_validate(request.context_seed["refinement_request"]).request_id == refinement.request_id
    prepared, _ = await harness.prepare_coding_request(request)
    assert prepared is not None

    await harness.execute_coding_request(prepared)

    assert recorded.await_args.kwargs["request_id"] == refinement.request_id
    assert refinement.request_id.startswith("ref_")


@pytest.mark.asyncio
async def test_cancelled_execution_records_cancellation_and_propagates_it(monkeypatch):
    recorded = AsyncMock()
    monkeypatch.setattr(orchestration_control, "record_refinement_event", recorded)
    started = asyncio.Event()

    async def execute(request):
        started.set()
        await asyncio.Event().wait()

    harness = orchestration_control.OrchestrationControlHarness(
        coding_worker=SimpleNamespace(execute=execute), config_loader=_config,
    )
    task = asyncio.create_task(harness.execute_coding_request(_request()))
    await asyncio.wait_for(started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    recorded.assert_awaited_once_with(
        event_kind="cancelled", request_id="request-1", app_id="studio",
        change_class="patch", outcome="cancelled",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", [
    "empty", "duplicate", "unowned", "outside_scope", "unsupported_validation", "unchanged", "matches_baseline",
])
async def test_finalization_rejects_invalid_provider_output_before_validation_or_persistence(tmp_path, defect):
    proposal = _proposal()
    request = _request()
    if defect == "empty":
        proposal = _proposal(changed_files=[])
    elif defect == "duplicate":
        proposal = _proposal(changed_files=proposal.changed_files * 2)
    elif defect == "unowned":
        proposal = _proposal(owned_paths=[])
    elif defect == "outside_scope":
        proposal = _proposal(
            owned_paths=["other.json"],
            changed_files=[ProposedFileChange(path="other.json", content="{}")],
        )
    elif defect in {"unchanged", "matches_baseline"}:
        baseline = {"ui/page.json": '{"title":"Before"}'}
        if defect == "matches_baseline":
            request = request.model_copy(update={"files": {"ui/page.json": "unsaved editor text"}, "baseline_files": baseline})
        proposal = _proposal(changed_files=[ProposedFileChange(path="ui/page.json", content=baseline["ui/page.json"])])
    else:
        request = _request(validation_strategy="unsupported")
    validate = AsyncMock()
    store = SimpleNamespace(create_build_record=AsyncMock())
    worker = ScopedRefinementCodingWorker(
        candidate_validation_runner=validate, artifact_store=store, output_root=tmp_path,
    )

    result = await worker.finalize_proposal(request, proposal)

    assert result.status == "failed"
    assert result.error
    if defect in {"unchanged", "matches_baseline"}:
        assert "no effective file changes" in result.error
    if defect == "outside_scope":
        assert "other.json" in result.error
    assert not result.applied_files
    validate.assert_not_awaited()
    store.create_build_record.assert_not_awaited()


@pytest.mark.asyncio
async def test_finalization_bounds_extra_owned_paths_and_keeps_only_effective_changes(tmp_path):
    request = _request(files={"ui/page.json": '{"title":"Before"}', "ui/unchanged.json": "{}"})
    proposal = _proposal(
        owned_paths=["ui/page.json", "ui/unchanged.json", "ui/unselected.json"],
        changed_files=[
            ProposedFileChange(path="ui/page.json", content='{"title":"After"}'),
            ProposedFileChange(path="ui/unchanged.json", content="{}"),
        ],
    )
    validate = AsyncMock(return_value=_candidate_validation("skipped"))
    store = _artifact_store(AsyncMock(side_effect=_saved_candidate))
    worker = ScopedRefinementCodingWorker(
        candidate_validation_runner=validate, artifact_store=store, output_root=tmp_path,
    )

    result = await worker.finalize_proposal(request, proposal)

    assert result.status == "planned", result.error
    assert result.plan.owned_paths == ["ui/page.json", "ui/unchanged.json"]
    assert [change.path for change in result.plan.updated_files] == ["ui/page.json"]
    assert result.applied_files == {"ui/page.json": '{"title":"After"}'}
    assert result.metadata["applied_file_count"] == 1
    assert validate.await_args.kwargs["files"]["ui/unchanged.json"] == "{}"
    store.create_build_record.assert_awaited_once()


def _surface_request():
    request = _request()
    refinement = RefinementRequest.model_validate(request.context_seed["refinement_request"])
    return {
        "refinement_request": refinement,
        "routing_decision": RefinementRoutingDecision.model_validate(request.context_seed["routing_decision"]),
        "plan": ContractSurfacePlan(
            summary="Update title", change_class="patch", build_family="app_bundle",
            surfaces=[{
                "kind": "page_binding", "target_id": "page", "target_kind": "page",
                "affected_paths": ["ui/page.json"], "rationale": "Requested title change",
            }],
        ),
        "workspace_files": {"ui/page.json": '{"title":"Before"}'},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("validation_status", ["passed", "failed", "skipped", "pending"])
@pytest.mark.parametrize("persistence_fails", [False, True])
async def test_surface_finalizer_writes_real_audit_document_after_validation_and_save(
    monkeypatch, tmp_path, validation_status, persistence_fails,
):
    documents = []

    async def insert_one(document):
        documents.append(document)

    # Exercise the canonical event writer; only its database boundary is replaced.
    monkeypatch.setattr(refinement_tracking, "_get_collection", lambda: SimpleNamespace(insert_one=insert_one))
    create = AsyncMock(side_effect=_saved_surface_candidate)
    if persistence_fails:
        create.side_effect = RuntimeError("store unavailable")
    worker = ScopedRefinementCodingWorker(
        candidate_validation_runner=AsyncMock(return_value=_candidate_validation(validation_status)),
        artifact_store=_artifact_store(create), output_root=tmp_path,
    )
    harness = orchestration_control.OrchestrationControlHarness(coding_worker=worker, config_loader=_config)
    result = await harness.finalize_surface_output(
        **_surface_request(),
        result=SurfacePlanExecutionResult(status="success", all_files={"ui/page.json": '{"title":"After"}'}),
        run_build_binding=RunBuildBinding(
            build_registry_id="registry", target_app_id="customer", build_id="build", phase="refinement",
        ),
    )

    expected = "failed" if persistence_fails or validation_status == "failed" else (
        "validated" if validation_status == "passed" else "planned"
    )
    assert result.status == expected
    assert len(documents) == 1
    event = documents[0]
    assert event["request_id"] == "request-1"
    assert event["app_id"] == "studio"
    assert event["event_kind"] == ("completed" if expected == "validated" else expected)
    assert event["metadata"]["target_app_id"] == "customer"
    assert event["metadata"]["build_record_id"] == (None if persistence_fails else "surface-candidate")
    assert event["metadata"]["coding_status"] == expected
    create.assert_awaited_once()
    assert create.await_args.kwargs["lifecycle_status"].value == "draft"


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["generation", "finalization"])
@pytest.mark.parametrize("cancelled", [False, True])
async def test_surface_interruption_writes_terminal_event_and_propagates(monkeypatch, stage, cancelled):
    documents = []

    async def insert_one(document):
        documents.append(document)

    monkeypatch.setattr(refinement_tracking, "_get_collection", lambda: SimpleNamespace(insert_one=insert_one))
    error = asyncio.CancelledError() if cancelled else RuntimeError("surface unavailable")
    harness = orchestration_control.OrchestrationControlHarness(
        coding_worker=SimpleNamespace(finalize_proposal=AsyncMock(side_effect=error)),
        surface_regeneration_worker=SimpleNamespace(execute_plan=AsyncMock(side_effect=error)),
        config_loader=lambda: ControlPlaneConfig(
            enabled=True,
            llm_profiles={"codegen": {"llm_config": {"model": "test-model"}}},
            contract_surface={
                "enabled": True,
                "llm_profile": "codegen",
                "regeneration_llm_profile": "codegen",
            },
        ),
    )
    with pytest.raises(type(error)):
        if stage == "generation":
            await harness.execute_surface_plan(**_surface_request())
        else:
            await harness.finalize_surface_output(
                **_surface_request(),
                result=SurfacePlanExecutionResult(status="success", all_files={"ui/page.json": '{"title":"After"}'}),
                run_build_binding=RunBuildBinding(
                    build_registry_id="registry", target_app_id="customer", build_id="build", phase="refinement",
                ),
            )
    assert len(documents) == 1
    assert documents[0]["event_kind"] == ("cancelled" if cancelled else "failed")
    assert documents[0]["request_id"] == "request-1"
