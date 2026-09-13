from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from factory_app.workflows.SecurityReadiness.tools.record_security_findings import (
    record_security_findings,
)
from mozaiksai.control_plane.contracts import ControlPlaneToolContext
from mozaiksai.control_plane.implementations.orchestration_control import (
    OrchestrationControlHarness,
)
from mozaiksai.control_plane.implementations.refinement_router import (
    RefinementRequest,
    RefinementTriggerRouteResolver,
)
from mozaiksai.control_plane.revision_context import assemble_revision_context
from mozaiksai.core.session.build_binding import RunBuildBinding
from tests.test_control_plane_tools import _revision_pack, _RevisionArtifactStore
from tests.test_surface_regeneration_worker import (
    ContractSurfacePlan,
    SurfacePlanExecutionResult,
    _make_refinement_request,
    _make_routing_decision,
    _make_surface,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("host_app_id", [None, "execution-host"])
async def test_revision_context_never_uses_target_as_session_host(host_app_id):
    sessions = SimpleNamespace(load=AsyncMock(return_value=None))
    result = await assemble_revision_context(
        context=ControlPlaneToolContext(
            app_id=host_app_id, target_app_id="artifact-target", user_id="owner",
            build_family="business_plan_bundle", build_record_id="av_bp_2",
        ),
        session_store=sessions,
        artifact_store=_RevisionArtifactStore(),
        pack_loader=_revision_pack,
    )
    assert result["present"] is True
    assert result["session"] == {"present": False}
    if host_app_id:
        sessions.load.assert_awaited_once_with(
            app_id=host_app_id, user_id="owner", target_app_id="artifact-target",
        )
    else:
        sessions.load.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("host_app_id", [None, "", "execution-host"])
async def test_refinement_artifact_lookups_keep_target_scope_and_host_guard(monkeypatch, host_app_id):
    store = SimpleNamespace(
        get_build_record=AsyncMock(return_value=None),
        get_stale_artifact_families=AsyncMock(return_value=[]),
    )
    monkeypatch.setattr("mozaiksai.core.artifacts.store.ArtifactStore", lambda: store)
    request = RefinementRequest(
        app_id=host_app_id, target_app_id="artifact-target",
        build_family="app_bundle", build_record_id="record-one",
    )
    resolver = RefinementTriggerRouteResolver(classifier=SimpleNamespace())
    assert await resolver._manifest_paths_for_request(request) == []
    assert await resolver._stale_route(request) is None
    if host_app_id:
        store.get_build_record.assert_awaited_once_with(
            app_id="artifact-target", build_record_id="record-one",
        )
        store.get_stale_artifact_families.assert_awaited_once_with(app_id="artifact-target")
    else:
        store.get_build_record.assert_not_awaited()
        store.get_stale_artifact_families.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("host_app_id", [None, ""])
async def test_surface_finalization_requires_host_before_worker_dispatch(host_app_id):
    worker = SimpleNamespace(finalize_proposal=AsyncMock())
    harness = OrchestrationControlHarness(coding_worker=worker)
    path = "modules/x/backend/service.py"
    request = _make_refinement_request().model_copy(update={
        "app_id": host_app_id, "target_app_id": "artifact-target", "build_record_id": "parent",
    })
    plan = ContractSurfacePlan(
        summary="Update service", build_family="app_bundle", change_class="feature",
        surfaces=[_make_surface("module_action", "x", [path])],
    )
    with pytest.raises(ValueError, match="execution host app_id"):
        await harness.finalize_surface_output(
            plan=plan,
            result=SurfacePlanExecutionResult(status="success", all_files={path: "VALUE = 2\n"}),
            refinement_request=request, routing_decision=_make_routing_decision(),
            workspace_files={path: "VALUE = 1\n"},
            run_build_binding=RunBuildBinding(
                target_app_id="artifact-target", build_registry_id="registry",
                build_id="revision", phase="refinement",
            ),
        )
    worker.finalize_proposal.assert_not_awaited()


@pytest.mark.asyncio
async def test_security_source_error_returns_concrete_unmodified_failure(monkeypatch):
    dispatch = AsyncMock()
    monkeypatch.setattr(
        "factory_app.workflows.SecurityReadiness.tools.record_security_findings.dispatch_workflow_module_action",
        dispatch,
    )
    inspected = {"source_error": "security_source_unavailable", "success": False, "persisted": False}
    context = {"security_readiness_summary": inspected, "security_readiness_recorded": True}
    result = await record_security_findings(context_variables=context)
    assert type(result) is dict
    assert result == inspected
    assert context["security_readiness_summary"] == inspected
    assert context["security_readiness_recorded"] is False
    dispatch.assert_not_awaited()
