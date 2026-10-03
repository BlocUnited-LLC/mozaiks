from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mozaiksai.control_plane.config import ControlPlaneConfig
from mozaiksai.control_plane.contracts import (
    CodingWorkerRequest,
    ProposedFileChange,
    StagedPatchProposal,
)
from mozaiksai.control_plane.implementations import orchestration_control
from mozaiksai.control_plane.implementations.coding_worker import ScopedRefinementCodingWorker


def _config():
    return ControlPlaneConfig(enabled=True, coding={"enabled": True})


def _request(**updates):
    return CodingWorkerRequest(
        app_id="studio", target_app_id="customer", build_family="app_bundle",
        build_record_id="parent", change_class="patch", raw_user_request="Update title",
        files={"ui/page.json": '{"title":"Before"}'},
        context_seed={"refinement_request": {"request_id": "request-1"}},
        **updates,
    )


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
    store = SimpleNamespace(create_build_record=AsyncMock(return_value=SimpleNamespace(id="candidate")))
    worker = ScopedRefinementCodingWorker(
        provider=SimpleNamespace(execute=AsyncMock(return_value=_proposal())),
        config_loader=_config, artifact_store=store, output_root=tmp_path,
        source_validation_runner=AsyncMock(return_value={"validation_status": validation_status}),
    )
    harness = orchestration_control.OrchestrationControlHarness(coding_worker=worker, config_loader=_config)

    result = await harness.execute_coding_request(_request(validation_strategy="local"))

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
        artifact_store=SimpleNamespace(create_build_record=AsyncMock(side_effect=RuntimeError("store unavailable"))),
        source_validation_runner=AsyncMock(return_value={"validation_status": "passed"}),
    )
    harness = orchestration_control.OrchestrationControlHarness(coding_worker=worker, config_loader=_config)

    result = await harness.execute_coding_request(_request())

    assert result.status == "failed"
    event = recorded.await_args.kwargs
    assert event["event_kind"] == "failed"
    assert event["outcome"] == "error"
    assert "ARTIFACT_PERSISTENCE_FAILED" in event["error"]


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
@pytest.mark.parametrize("defect", ["empty", "duplicate", "unowned", "outside_scope", "unsupported_validation"])
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
    else:
        request = _request(validation_strategy="unsupported")
    validate = AsyncMock()
    store = SimpleNamespace(create_build_record=AsyncMock())
    worker = ScopedRefinementCodingWorker(
        source_validation_runner=validate, artifact_store=store, output_root=tmp_path,
    )

    result = await worker.finalize_proposal(request, proposal)

    assert result.status == "failed"
    assert result.error
    assert not result.applied_files
    validate.assert_not_awaited()
    store.create_build_record.assert_not_awaited()
