from asyncio import CancelledError
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from factory_app.workflows._shared.platform import review_continuation as review
from factory_app.workflows.AppReview.tools.review_context import build_review_summary_payload
from mozaiksai.core.artifacts import ArtifactLifecycleStatus, ArtifactValidationStatus
from mozaiksai.core.session.build_binding import RunBuildBinding

_actual_settle = review._settle_superseded_workbench


def _saved_refinement(fixture):
    _generator_source(fixture)
    request = SimpleNamespace(
        app_id="factory", user_id="owner", target_app_id="target", build_record_id="parent",
        build_family="app_bundle", build_key="app_bundle", source_surface="app_workbench",
    )
    change = SimpleNamespace(
        id="change", app_id="target", created_by_user_id="owner", build_record_id="parent",
        build_family="app_bundle", build_key="app_bundle", refinement_request=request,
    )
    session = SimpleNamespace(
        id="refinement-session", app_id="target", build_record_id="parent", result_build_record_id="child",
        change_request_id="change", status="accepted", provider="control_plane_coding",
        metadata={"coding_worker": {"eligible": True, "status": "validated", "metadata": {"build_record_id": "child"}}},
    )
    fixture.request["artifact_store"].list_refinement_sessions = AsyncMock(return_value=[session])
    fixture.request["artifact_store"].get_change_request = AsyncMock(return_value=change)
    fixture.versions["child"].lifecycle_status = ArtifactLifecycleStatus.CURRENT
    return session, change


async def _recover(fixture, **extra):
    return await review.recover_saved_app_review(
        app_id="factory", user_id="owner", binding=fixture.binding,
        registry=fixture.registry, artifact_store=fixture.request["artifact_store"],
        persistence=fixture.persistence, session_router=fixture.router, **extra,
    )


@pytest.fixture
def continuation(monkeypatch):
    binding = RunBuildBinding(
        build_registry_id="registry", target_app_id="target", build_id="refinement", phase="refinement",
    )
    source_binding = binding.model_copy(update={"build_id": "genesis", "phase": "genesis"})
    source = {"workflow_name": "AppReview", "run_build_binding": source_binding.model_dump()}
    record = {
        "app_id": "target", "chat_app_id": "factory", "lifecycle_state": "review",
        "current_build_run": {"build_id": binding.build_id, "phase": "refinement", "artifact_version_id": "child"},
    }
    def version(identifier, build_binding, parent=None):
        return SimpleNamespace(
            id=identifier, app_id="target", build_family="app_bundle", build_key="app_bundle",
            lifecycle_status=ArtifactLifecycleStatus.DRAFT, validation_status=ArtifactValidationStatus.PASSED,
            app_validation_status="passed", app_validation_strategy="e2b", parent_build_record_id=parent,
            source_workflow="AppGenerator" if identifier == "parent" else "control_plane_coding",
            source_chat_id="review-parent" if identifier == "parent" else None,
            canonical_inputs_version={},
            commit_metadata=SimpleNamespace(author_user_id="owner", metadata={
                **build_binding.model_dump(), "workspace_dir": f"/saved/{identifier}",
                "app_bundle_acceptance": {"status": "passed", "passed": True},
                "security_readiness_summary": {"must_not_copy": True},
            }),
        )
    versions = {"parent": version("parent", source_binding), "child": version("child", binding, "parent")}
    store = SimpleNamespace(get_build_record=AsyncMock(side_effect=lambda **kw: versions.get(kw["build_record_id"])))
    collection = SimpleNamespace(find_one=AsyncMock(return_value=source))
    persistence = SimpleNamespace(
        _coll=AsyncMock(return_value=collection), create_chat_session=AsyncMock(),
        persist_server_owned_session_fields=AsyncMock(), mark_chat_failed=AsyncMock(),
    )
    registry = SimpleNamespace(
        get_app_record=AsyncMock(return_value={"app": record}),
        resolve_build_binding=AsyncMock(return_value=binding),
        update_build_status=AsyncMock(return_value={"success": True}),
    )
    router = SimpleNamespace(
        route_trigger=AsyncMock(return_value=SimpleNamespace(journey_id=None)),
        bind_workflow_session=AsyncMock(),
    )
    router.for_target = lambda _: router
    hooks = SimpleNamespace(call_chat_prereqs=AsyncMock(return_value=(True, None)))
    monkeypatch.setattr(review, "get_platform_hooks", lambda: hooks)
    settle = AsyncMock()
    monkeypatch.setattr(review, "_settle_superseded_workbench", settle)
    request = dict(
        app_id="factory", user_id="owner", source_chat_id="review-parent", binding=binding,
        baseline_id="parent", result=SimpleNamespace(status="validated", metadata={"build_record_id": "child"}),
        registry=registry, artifact_store=store, persistence=persistence, session_router=router,
    )
    return SimpleNamespace(
        request=request, source=source, record=record, versions=versions, binding=binding,
        persistence=persistence, registry=registry, router=router, hooks=hooks, collection=collection,
        settle=settle,
    )


@pytest.mark.asyncio
async def test_saved_accepted_refinement_recovers_pending_generation_without_model_work(continuation):
    fixture = continuation
    _saved_refinement(fixture)
    before = deepcopy(fixture.source)
    result = await _recover(fixture)
    assert result["artifact_version_id"] == "child"
    assert result["source_chat_id"] == "review-parent"
    assert fixture.source == before
    fixture.persistence.create_chat_session.assert_awaited_once()
    fixture.settle.assert_awaited_once()
    assert fixture.request["artifact_store"].list_refinement_sessions.await_args.kwargs == {
        "app_id": "target", "result_build_record_id": "child", "limit": 2,
    }


@pytest.mark.asyncio
async def test_saved_surface_result_uses_its_persisted_success(continuation):
    fixture = continuation
    session, _ = _saved_refinement(fixture)
    session.provider = "contract_surface_regeneration"
    session.metadata = {"surface_result": {"status": "success", "metadata": {"build_record_id": "child"}}}
    assert (await _recover(fixture))["artifact_version_id"] == "child"


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", [
    "host", "current_build", "missing_child", "missing_parent", "source_owner", "no_session", "duplicate_session",
    "session_app", "session_parent", "session_child", "session_status", "missing_change", "change_owner",
    "change_parent", "request_target", "request_owner", "request_parent", "request_surface", "provider",
    "worker_status", "worker_child", "worker_invalid", "child_failed", "child_stale", "caller_overrides",
])
async def test_saved_refinement_recovery_rejects_incomplete_or_foreign_records(continuation, damage):
    fixture = continuation
    session, change = _saved_refinement(fixture)
    store = fixture.request["artifact_store"]
    if damage == "host":
        fixture.record["chat_app_id"] = "other"
    elif damage == "current_build":
        fixture.record["current_build_run"]["build_id"] = "newer"
    elif damage == "missing_child":
        fixture.versions.pop("child")
    elif damage == "missing_parent":
        fixture.versions.pop("parent")
    elif damage == "source_owner":
        fixture.collection.find_one.return_value = None
    elif damage in {"no_session", "duplicate_session"}:
        store.list_refinement_sessions.return_value = [] if damage == "no_session" else [session, session]
    elif damage.startswith("session_"):
        setattr(session, {"session_app": "app_id", "session_parent": "build_record_id",
                          "session_child": "result_build_record_id", "session_status": "status"}[damage], "other")
    elif damage == "missing_change":
        store.get_change_request.return_value = None
    elif damage in {"change_owner", "change_parent"}:
        setattr(change, "created_by_user_id" if damage == "change_owner" else "build_record_id", "other")
    elif damage.startswith("request_"):
        setattr(change.refinement_request, {"request_target": "target_app_id", "request_owner": "user_id",
                                           "request_parent": "build_record_id", "request_surface": "source_surface"}[damage], "other")
    elif damage == "provider":
        session.provider = "other"
    elif damage == "worker_status":
        session.metadata["coding_worker"]["status"] = "failed"
    elif damage == "worker_child":
        session.metadata["coding_worker"]["metadata"]["build_record_id"] = "other"
    elif damage == "worker_invalid":
        session.metadata["coding_worker"]["unexpected"] = True
    elif damage == "child_failed":
        fixture.versions["child"].app_validation_status = "failed"
    elif damage == "child_stale":
        fixture.versions["child"].lifecycle_status = ArtifactLifecycleStatus.STALE
    with pytest.raises(ValueError):
        await _recover(fixture, has_launch_overrides=damage == "caller_overrides")
    fixture.persistence.create_chat_session.assert_not_awaited()
    fixture.registry.update_build_status.assert_not_awaited()
    fixture.settle.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("ordinary", ["genesis", "root", "workflow_parent", "completed_source", "failed_source", "manual_review"])
async def test_ordinary_saved_review_does_not_recover_a_waiter(continuation, ordinary):
    fixture = continuation
    _saved_refinement(fixture)
    if ordinary == "genesis":
        fixture.binding = fixture.binding.model_copy(update={"phase": "genesis"})
        fixture.record["current_build_run"]["phase"] = "genesis"
    elif ordinary == "root":
        fixture.versions["child"].parent_build_record_id = None
    elif ordinary == "workflow_parent":
        fixture.versions["parent"].build_family = "workflow_bundle"
    elif ordinary in {"completed_source", "failed_source"}:
        fixture.source["status"] = 1 if ordinary == "completed_source" else 2
    else:
        fixture.record.update(active_chat_id="manual-review", active_workflow_id="AppReview")
        fixture.collection.find_one.return_value = {"trigger_meta": {"trigger_source": "manual"}}
    assert await _recover(fixture) is None
    fixture.persistence.create_chat_session.assert_not_awaited()
    fixture.registry.update_build_status.assert_not_awaited()
    fixture.settle.assert_not_awaited()


@pytest.mark.asyncio
async def test_partial_saved_successor_marker_cannot_fall_through_to_another_chat(continuation):
    fixture = continuation
    fixture.record.update(active_chat_id="saved", active_workflow_id="AppReview")
    fixture.collection.find_one.return_value = {"trigger_meta": {"source_tool_call_id": "event"}}
    with pytest.raises(ValueError, match="incomplete"):
        await _recover(fixture)
    fixture.persistence.create_chat_session.assert_not_awaited()
    fixture.registry.update_build_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_validated_child_gets_one_bound_review_session_without_execution(continuation):
    fixture = continuation
    before = deepcopy(fixture.source)
    descriptor = await review.continue_inline_app_review(**fixture.request)
    assert descriptor["artifact_version_id"] == "child"
    assert descriptor["target_app_id"] == "target"
    assert descriptor["lifecycle_state"] == "review"
    assert descriptor["source_chat_id"] == "review-parent"
    assert descriptor["websocket_url"] == f'/ws/AppReview/factory/{descriptor["chat_id"]}/owner'
    assert fixture.source == before
    fixture.persistence.create_chat_session.assert_awaited_once()
    created = fixture.persistence.create_chat_session.await_args.kwargs
    context = created["extra_fields"]
    assert context["artifact_version_id"] == "child"
    assert context["bundle_path"] == "/saved/child"
    assert context["security_readiness_summary"] == {}
    assert "run_build_binding" not in context
    fields = fixture.persistence.persist_server_owned_session_fields.await_args.kwargs["fields"]
    assert fields == {"run_build_binding": fixture.binding.model_dump()}
    summary = build_review_summary_payload({**context, **fields, "app_id": "factory"})
    assert summary["can_promote"] is True
    assert fixture.router.route_trigger.await_args.kwargs["contribution"].require_exact_route is True
    fixture.router.bind_workflow_session.assert_awaited_once()
    fixture.persistence.mark_chat_failed.assert_not_awaited()
    assert fixture.collection.find_one.await_args.args[0]["user_id"] == "owner"


@pytest.mark.asyncio
async def test_generator_waiting_on_exact_baseline_gets_new_review_binding(continuation):
    fixture = continuation
    _generator_source(fixture)
    before = deepcopy(fixture.source)
    descriptor = await review.continue_inline_app_review(**fixture.request)
    assert descriptor is not None
    assert descriptor["artifact_version_id"] == "child"
    assert descriptor["chat_id"] != "review-parent"
    assert fixture.source == before
    fields = fixture.persistence.persist_server_owned_session_fields.await_args.kwargs["fields"]
    assert fields == {"run_build_binding": fixture.binding.model_dump()}
    assert fixture.registry.update_build_status.await_args.kwargs["expected_artifact_version_id"] == "child"
    fixture.settle.assert_awaited_once_with(
        app_id="factory", user_id="owner", source_chat_id="review-parent", event_id="workbench", baseline_id="parent",
        persistence=fixture.persistence,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status,candidate", [("failed", True), ("planned", True), ("failed", False)])
async def test_unsuccessful_edit_has_current_binding_and_actionable_review(continuation, status, candidate):
    fixture = continuation
    fixture.record["lifecycle_state"] = "needs_revision"
    fixture.request["result"].status = status
    if candidate:
        fixture.versions["child"].validation_status = ArtifactValidationStatus.FAILED
        fixture.versions["child"].app_validation_status = "failed"
    else:
        fixture.request["result"].metadata = {}
        fixture.record["current_build_run"].pop("artifact_version_id")
    descriptor = await review.continue_inline_app_review(**fixture.request)
    assert descriptor["artifact_version_id"] == ("child" if candidate else "parent")
    assert descriptor["notice"]
    assert descriptor["lifecycle_state"] == "needs_revision"
    context = fixture.persistence.create_chat_session.await_args.kwargs["extra_fields"]
    assert context["lifecycle_state"] == "needs_revision"
    summary = build_review_summary_payload({**context, "run_build_binding": fixture.binding.model_dump()})
    assert summary["can_promote"] is False
    assert summary["can_revise"] is True
    assert "lifecycle_not_review" in summary["promotion_blockers"]


@pytest.mark.asyncio
@pytest.mark.parametrize("source", [None, "discovery"])
async def test_non_review_requests_do_not_create_conversations(continuation, source):
    fixture = continuation
    if source is None:
        fixture.request["source_chat_id"] = None
    else:
        fixture.source["workflow_name"] = "ExistingAppDiscovery"
    assert await review.continue_inline_app_review(**fixture.request) is None
    fixture.persistence.create_chat_session.assert_not_awaited()
    fixture.registry.update_build_status.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["source_owner", "source_target", "host", "target", "superseded", "already_open", "parent", "candidate_binding", "candidate_selection", "missing", "unvalidated"])
async def test_continuation_refuses_unverified_or_superseded_lineage(continuation, damage):
    fixture = continuation
    if damage == "source_owner":
        fixture.collection.find_one.return_value = None
    elif damage == "source_target":
        fixture.source["run_build_binding"]["target_app_id"] = "other"
    elif damage in {"host", "target"}:
        fixture.record["chat_app_id" if damage == "host" else "app_id"] = "other"
    elif damage == "superseded":
        fixture.record["current_build_run"]["build_id"] = "later"
    elif damage == "already_open":
        fixture.record.update(active_workflow_id="AppReview", active_chat_id="winner")
    elif damage == "parent":
        fixture.versions["child"].parent_build_record_id = "other"
    elif damage == "candidate_binding":
        fixture.versions["child"].commit_metadata.metadata["build_id"] = "other"
    elif damage == "candidate_selection":
        fixture.record["current_build_run"]["artifact_version_id"] = "other"
    elif damage == "missing":
        fixture.versions.pop("child")
    else:
        fixture.versions["child"].app_validation_status = "failed"
    with pytest.raises(ValueError):
        await review.continue_inline_app_review(**fixture.request)
    fixture.persistence.create_chat_session.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", ["prerequisite", "dependency", "binding"])
async def test_continuation_preserves_launch_gates(continuation, gate):
    fixture = continuation
    if gate == "prerequisite":
        fixture.hooks.call_chat_prereqs.return_value = (False, "denied")
    elif gate == "dependency":
        fixture.router.route_trigger.side_effect = ValueError("missing prerequisite")
    else:
        fixture.registry.resolve_build_binding.side_effect = ValueError("superseded")
    with pytest.raises(ValueError):
        await review.continue_inline_app_review(**fixture.request)
    fixture.persistence.create_chat_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_cas_failure_terminates_unlaunched_session_and_returns_no_descriptor(continuation):
    fixture = continuation
    fixture.registry.update_build_status.return_value = {"success": False}
    with pytest.raises(ValueError, match="changed"):
        await review.continue_inline_app_review(**fixture.request)
    chat_id = fixture.persistence.create_chat_session.await_args.kwargs["chat_id"]
    fixture.persistence.mark_chat_failed.assert_awaited_once_with(chat_id, app_id="factory")
    fixture.router.bind_workflow_session.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [RuntimeError("binding write failed"), CancelledError()])
async def test_incomplete_session_creation_is_closed_before_registration(continuation, error):
    fixture = continuation
    fixture.persistence.persist_server_owned_session_fields.side_effect = error
    with pytest.raises(type(error)):
        await review.continue_inline_app_review(**fixture.request)
    chat_id = fixture.persistence.create_chat_session.await_args.kwargs["chat_id"]
    fixture.persistence.mark_chat_failed.assert_awaited_once_with(chat_id, app_id="factory")
    fixture.registry.update_build_status.assert_not_awaited()
    fixture.router.bind_workflow_session.assert_not_awaited()


def _generator_source(fixture):
    fixture.source.update(workflow_name="AppGenerator", status=0, workflow_ui_state={"tool_calls": {
        "workbench": {"tool_name": "AppWorkbench", "tool_call_id": "workbench",
                      "tool_call_status": "pending", "awaiting_response": True,
                      "tool_call_completed": False, "payload": {
                          "artifact_version_id": "parent", "build_registry_id": "registry",
                          "target_app_id": "target", "source_chat_id": "review-parent",
                      }},
    }})


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["terminal", "missing", "resolved", "completed", "not_waiting", "foreign_event", "duplicate", "baseline_owner", "baseline_source", "baseline_build", "child_owner", "phase"])
async def test_generator_successor_requires_exact_owned_pending_baseline(continuation, damage):
    fixture = continuation
    _generator_source(fixture)
    call = fixture.source["workflow_ui_state"]["tool_calls"]["workbench"]
    if damage == "terminal":
        fixture.source["status"] = 1
    elif damage == "missing":
        fixture.source["workflow_ui_state"] = {}
    elif damage == "resolved":
        call["tool_call_status"] = "completed"
    elif damage == "completed":
        call["tool_call_completed"] = True
    elif damage == "not_waiting":
        call["awaiting_response"] = False
    elif damage == "foreign_event":
        call["payload"]["source_chat_id"] = "another"
    elif damage == "duplicate":
        fixture.source["workflow_ui_state"]["tool_calls"]["other"] = {**call, "tool_call_id": "another"}
    elif damage == "baseline_owner":
        fixture.versions["parent"].commit_metadata.author_user_id = "other"
    elif damage == "baseline_source":
        fixture.versions["parent"].source_chat_id = "other"
    elif damage == "baseline_build":
        fixture.versions["parent"].commit_metadata.metadata["build_id"] = "other"
    elif damage == "child_owner":
        fixture.versions["child"].commit_metadata.author_user_id = "other"
    else:
        fixture.record["current_build_run"]["phase"] = "genesis"
    with pytest.raises(ValueError):
        await review.continue_inline_app_review(**fixture.request)
    fixture.persistence.create_chat_session.assert_not_awaited()
    fixture.settle.assert_not_awaited()


@pytest.mark.asyncio
async def test_generator_cas_loss_does_not_settle_old_waiter(continuation):
    fixture = continuation
    _generator_source(fixture)
    fixture.registry.update_build_status.return_value = {"success": False}
    with pytest.raises(ValueError, match="changed"):
        await review.continue_inline_app_review(**fixture.request)
    fixture.settle.assert_not_awaited()
    fixture.persistence.mark_chat_failed.assert_awaited_once()
    assert fixture.persistence.mark_chat_failed.await_args.args[0] != "review-parent"


@pytest.mark.asyncio
async def test_failed_source_notification_keeps_committed_successor_recoverable(continuation):
    fixture = continuation
    _generator_source(fixture)
    async def settle(**_):
        fixture.registry.update_build_status.assert_awaited_once()
        fixture.router.bind_workflow_session.assert_awaited_once()
        raise RuntimeError("transport unavailable")
    fixture.settle.side_effect = settle
    with pytest.raises(RuntimeError, match="transport unavailable"):
        await review.continue_inline_app_review(**fixture.request)
    fixture.persistence.mark_chat_failed.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("waiter", ["live", "cold", "resolved", "foreign", "wrong_tool", "wrong_workflow", "foreign_owner", "rejected"])
async def test_supersession_settles_only_exact_internal_waiter(monkeypatch, waiter):
    from mozaiksai.core.transport.simple_transport import SimpleTransport
    from mozaiksai.core.transport.ui_tools import UIToolsMixin

    class Transport(UIToolsMixin):
        def __init__(self):
            self.connections = {}
            self._ui_tool_metadata = {}
            self.pending_tool_call_responses = {}
            self._buffered_tool_call_responses = {}
            self._resolved_tool_call_ids = {}
        async def _finalize_tool_call_response(self, **kwargs):
            self.finalized = kwargs

    import asyncio
    monkeypatch.setenv("MOZAIKS_CHAT_LOCK_MODE", "local")
    transport = Transport()
    if waiter != "cold":
        transport._ui_tool_metadata["event"] = {"chat_id": "other" if waiter == "foreign" else "source", "tool_name": "AppWorkbench"}
        transport.pending_tool_call_responses["event"] = asyncio.get_running_loop().create_future()
    if waiter == "resolved":
        transport._mark_tool_call_response_resolved("event")
    if waiter == "wrong_tool":
        transport._ui_tool_metadata["event"]["tool_name"] = "Other"
    if waiter == "wrong_workflow":
        transport._ui_tool_metadata["event"]["workflow_name"] = "Other"
    if waiter == "foreign_owner":
        transport.connections["source"] = {"app_id": "factory", "user_id": "other"}
    if waiter == "rejected":
        transport._ui_tool_metadata["event"]["workflow_primitive"] = "outcome_feedback"
    monkeypatch.setattr(SimpleTransport, "get_instance", AsyncMock(return_value=transport))
    source = {}
    _generator_source(SimpleNamespace(source=source))
    call = source["workflow_ui_state"]["tool_calls"]["workbench"]
    call["tool_call_id"] = "event"
    call["payload"]["source_chat_id"] = "source"
    source["run_build_binding"] = {"build_registry_id": "registry", "target_app_id": "target", "build_id": "genesis", "phase": "genesis"}
    persistence = SimpleNamespace(mark_chat_failed=AsyncMock(return_value=True),
        persist_context_variables=AsyncMock(), _coll=AsyncMock(return_value=SimpleNamespace(find_one=AsyncMock(return_value=source))))
    if waiter in {"foreign", "wrong_tool", "wrong_workflow", "foreign_owner", "rejected"}:
        with pytest.raises(ValueError, match="transport"):
            await review._settle_superseded_workbench(
                app_id="factory", user_id="owner", source_chat_id="source", event_id="event", baseline_id="parent", persistence=persistence,
            )
        assert not transport.pending_tool_call_responses["event"].done()
        persistence.mark_chat_failed.assert_not_awaited()
        return
    # Use the production helper, not the fixture's settlement stub.
    await review._settle_superseded_workbench(
        app_id="factory", user_id="owner", source_chat_id="source", event_id="event", baseline_id="parent", persistence=persistence,
    )
    if waiter == "live":
        result = transport.pending_tool_call_responses["event"].result()
        assert result == {"status": "superseded", "approved": False, "data": {"artifact_version_id": "parent"}}
        assert transport.finalized["response_data"] == result
        await review._settle_superseded_workbench(
            app_id="factory", user_id="owner", source_chat_id="source", event_id="event", baseline_id="parent", persistence=persistence,
        )
        persistence.mark_chat_failed.assert_not_awaited()
    elif waiter == "cold":
        persistence.mark_chat_failed.assert_awaited_once_with("source", app_id="factory")
        fields = persistence.persist_context_variables.await_args.kwargs
        assert fields["workflow_name"] == "AppGenerator"
        assert fields["variables"]["app_bundle_export_outcome"] == "superseded"
        assert "superseded" in fields["variables"]["app_build_failure_message"]
        assert fields["variables"]["generated_app_zip"] is None
    else:
        assert not transport.pending_tool_call_responses["event"].done()
        persistence.mark_chat_failed.assert_not_awaited()


@pytest.mark.asyncio
async def test_cold_settlement_cannot_terminalize_an_execution_lease_holder(continuation, monkeypatch):
    from mozaiksai.core.runtime.persistence.distributed_lock import (
        LockAcquisitionError,
        chat_execution_lease,
    )
    from mozaiksai.core.transport.simple_transport import SimpleTransport

    fixture = continuation
    _generator_source(fixture)
    # Restore the real helper instead of the fixture's notification stub.
    monkeypatch.setattr(review, "_settle_superseded_workbench", _actual_settle)
    monkeypatch.setenv("MOZAIKS_CHAT_LOCK_MODE", "local")
    monkeypatch.setenv("DISTRIBUTED_LOCK_MAX_RETRIES", "0")
    monkeypatch.setattr(SimpleTransport, "get_instance", AsyncMock(return_value=SimpleNamespace(connections={}, _ui_tool_metadata={})))
    sessions = {"review-parent": fixture.source}
    fixture.collection.find_one.side_effect = lambda query, projection: sessions.get(query["_id"])
    async def create(**kwargs):
        sessions[kwargs["chat_id"]] = {**kwargs["extra_fields"], "workflow_name": kwargs["workflow_name"], "status": 0}
    async def fields(**kwargs):
        sessions[kwargs["chat_id"]].update(kwargs["fields"])
    async def select(**kwargs):
        fixture.record.update(active_chat_id=kwargs["active_chat_id"], active_workflow_id="AppReview")
        return {"success": True}
    fixture.persistence.create_chat_session.side_effect = create
    fixture.persistence.persist_server_owned_session_fields.side_effect = fields
    fixture.persistence.persist_context_variables = AsyncMock()
    fixture.persistence.mark_chat_failed.return_value = True
    fixture.registry.update_build_status.side_effect = select
    async with chat_execution_lease(app_id="factory", chat_id="review-parent"):
        with pytest.raises(LockAcquisitionError):
            await review.continue_inline_app_review(**fixture.request)
        selected = fixture.record["active_chat_id"]
        assert selected != "review-parent"
        fixture.persistence.mark_chat_failed.assert_not_awaited()
        fixture.persistence.persist_context_variables.assert_not_awaited()
    result = await review.continue_inline_app_review(**fixture.request)
    assert result["chat_id"] == selected
    fixture.persistence.create_chat_session.assert_awaited_once()
    fixture.persistence.mark_chat_failed.assert_awaited_once_with("review-parent", app_id="factory")


@pytest.mark.asyncio
async def test_cold_settlement_requires_available_execution_authority(monkeypatch):
    from mozaiksai.core.runtime.persistence import distributed_lock as locks
    from mozaiksai.core.transport.simple_transport import SimpleTransport

    monkeypatch.setenv("MOZAIKS_CHAT_LOCK_MODE", "required")
    monkeypatch.setattr(locks, "_get_lock_collection", lambda: None)
    monkeypatch.setattr(SimpleTransport, "get_instance", AsyncMock(return_value=SimpleNamespace(connections={}, _ui_tool_metadata={})))
    persistence = SimpleNamespace(_coll=AsyncMock(), mark_chat_failed=AsyncMock(), persist_context_variables=AsyncMock())
    with pytest.raises(locks.ChatLockAuthorityUnavailableError):
        await _actual_settle(app_id="factory", user_id="owner", source_chat_id="source", event_id="event", baseline_id="parent", persistence=persistence)
    persistence._coll.assert_not_awaited()
    persistence.mark_chat_failed.assert_not_awaited()
    persistence.persist_context_variables.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_step", ["bind", "settle", "cancel"])
@pytest.mark.parametrize("entrypoint", ["inline", "saved"])
async def test_committed_successor_is_reused_after_interrupted_notification(continuation, failed_step, entrypoint):
    fixture = continuation
    _generator_source(fixture)
    sessions = {"review-parent": fixture.source}
    fixture.collection.find_one.side_effect = lambda query, projection: sessions.get(query["_id"])
    async def create(**kwargs):
        sessions[kwargs["chat_id"]] = {
            **kwargs["extra_fields"], "workflow_name": kwargs["workflow_name"], "status": 0,
        }
    async def fields(**kwargs):
        sessions[kwargs["chat_id"]].update(kwargs["fields"])
    async def select(**kwargs):
        fixture.record.update(active_chat_id=kwargs["active_chat_id"], active_workflow_id="AppReview")
        return {"success": True}
    fixture.persistence.create_chat_session.side_effect = create
    fixture.persistence.persist_server_owned_session_fields.side_effect = fields
    fixture.registry.update_build_status.side_effect = select
    error = CancelledError() if failed_step == "cancel" else RuntimeError("notification interrupted")
    target = fixture.router.bind_workflow_session if failed_step == "bind" else fixture.settle
    target.side_effect = error
    with pytest.raises(type(error)):
        await review.continue_inline_app_review(**fixture.request)
    selected = fixture.record["active_chat_id"]
    target.side_effect = None
    descriptor = await (_recover(fixture) if entrypoint == "saved" else review.continue_inline_app_review(**fixture.request))
    assert descriptor["chat_id"] == selected
    fixture.persistence.create_chat_session.assert_awaited_once()
    fixture.persistence.mark_chat_failed.assert_not_awaited()
    assert descriptor["artifact_version_id"] == "child"
    assert sessions[selected]["run_build_binding"] == fixture.binding.model_dump()
    assert fixture.source["run_build_binding"]["build_id"] == "genesis"


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["source", "baseline", "binding", "artifact", "completed", "event"])
@pytest.mark.parametrize("entrypoint", ["inline", "saved"])
async def test_recovery_cannot_adopt_another_selected_review(continuation, damage, entrypoint):
    fixture = continuation
    _generator_source(fixture)
    fixture.record.update(active_chat_id="saved", active_workflow_id="AppReview")
    successor = {"workflow_name": "AppReview", "status": 0,
                 "run_build_binding": fixture.binding.model_dump(), "artifact_version_id": "child",
                 "trigger_meta": {"source_chat_id": "review-parent", "source_artifact_version_id": "parent", "source_tool_call_id": "workbench"}}
    if damage == "source":
        successor["trigger_meta"]["source_chat_id"] = "other"
    elif damage == "baseline":
        successor["trigger_meta"]["source_artifact_version_id"] = "other"
    elif damage == "binding":
        successor["run_build_binding"]["build_id"] = "other"
    elif damage == "artifact":
        successor["artifact_version_id"] = "other"
    elif damage == "completed":
        successor["status"] = 1
    else:
        successor["trigger_meta"].pop("source_tool_call_id")
    fixture.collection.find_one.side_effect = lambda query, projection: {"review-parent": fixture.source, "saved": successor}.get(query["_id"])
    with pytest.raises(ValueError):
        await (_recover(fixture) if entrypoint == "saved" else review.continue_inline_app_review(**fixture.request))
    fixture.persistence.create_chat_session.assert_not_awaited()
    fixture.registry.update_build_status.assert_not_awaited()
    fixture.settle.assert_not_awaited()
