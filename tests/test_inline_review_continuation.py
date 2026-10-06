from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from factory_app.workflows._shared.platform import review_continuation as review
from factory_app.workflows.AppReview.tools.review_context import build_review_summary_payload
from mozaiksai.core.artifacts import ArtifactLifecycleStatus, ArtifactValidationStatus
from mozaiksai.core.session.build_binding import RunBuildBinding


@pytest.fixture
def continuation(monkeypatch):
    binding = RunBuildBinding(
        build_registry_id="registry", target_app_id="target", build_id="refinement", phase="refinement",
    )
    source_binding = binding.model_copy(update={"build_id": "genesis", "phase": "genesis"})
    source = {"workflow_name": "AppReview", "run_build_binding": source_binding.model_dump()}
    record = {
        "app_id": "target", "chat_app_id": "factory", "lifecycle_state": "review",
        "current_build_run": {"build_id": binding.build_id, "artifact_version_id": "child"},
    }
    def version(identifier, build_binding, parent=None):
        return SimpleNamespace(
            id=identifier, app_id="target", build_family="app_bundle", build_key="app_bundle",
            lifecycle_status=ArtifactLifecycleStatus.DRAFT, validation_status=ArtifactValidationStatus.PASSED,
            app_validation_status="passed", app_validation_strategy="e2b", parent_build_record_id=parent,
            commit_metadata=SimpleNamespace(metadata={
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
    request = dict(
        app_id="factory", user_id="owner", source_chat_id="review-parent", binding=binding,
        baseline_id="parent", result=SimpleNamespace(status="validated", metadata={"build_record_id": "child"}),
        registry=registry, artifact_store=store, persistence=persistence, session_router=router,
    )
    return SimpleNamespace(
        request=request, source=source, record=record, versions=versions, binding=binding,
        persistence=persistence, registry=registry, router=router, hooks=hooks, collection=collection,
    )


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
@pytest.mark.parametrize("source", [None, "generator"])
async def test_non_review_requests_do_not_create_conversations(continuation, source):
    fixture = continuation
    if source is None:
        fixture.request["source_chat_id"] = None
    else:
        fixture.source["workflow_name"] = "AppGenerator"
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
