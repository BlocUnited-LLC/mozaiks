"""Settings bind exact saved inputs and keep the existing bounded refinement path."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mozaiksai.control_plane import RefinementRequest
from mozaiksai.core.artifacts import BuildRecord
from mozaiksai.core.auth import UserPrincipal
from tests import test_studio_owner_billing

dashboard_portal = test_studio_owner_billing.dashboard_portal
saved_billing_build = test_studio_owner_billing.saved_billing_build


@pytest.fixture
def settings_build(saved_billing_build, monkeypatch):
    from factory_app.workflows._shared import subscription_revision

    # This suite isolates selector/launch authority. Exact content/digest capture has its own offline tests.
    monkeypatch.setattr(subscription_revision, "capture_subscription_revision_baseline", AsyncMock(return_value={
        "design_record": {"_id": "design-selected"}, "selected_catalog": {"label": "Notes plans"},
    }))
    portal, save = saved_billing_build
    bundle, store = save({"schema_version": "mozaiks.subscriptions.v1", "label": "Notes plans", "default_plan_id": "free", "plans": [
        {"plan_id": "free", "label": "Free"},
    ]})
    bundle.commit_metadata.author_user_id = "alice"
    bundle.lifecycle_status = "draft"  # A validated saved draft is an intentional settings baseline.
    bundle.canonical_inputs_version = {"subscription_contract": "contract-selected"}
    contract = BuildRecord(
        id="contract-selected", app_id="notes", build_family="subscription_contract", build_key="subscription_contract",
        version_number=2, lineage_root_id="contract-one", lifecycle_status="current", validation_status="passed",
        commit_metadata={"author_user_id": "alice", "metadata": {"summary_format": "json", "summary_payload": {"contract_required": True}}},
    )
    versions = {bundle.id: bundle, contract.id: contract, "contract-newer": contract.model_copy(update={"id": "contract-newer", "version_number": 3})}
    store.get_build_record = AsyncMock(side_effect=lambda *, app_id, build_record_id: versions.get(build_record_id) if app_id == "notes" else None)
    store.create_change_request = AsyncMock(return_value=SimpleNamespace(id="change-settings"))
    store.update_change_request_router_decision = AsyncMock(return_value=True)
    principal = UserPrincipal(user_id="alice", app_id="studio", roles=["user"], email=None, name=None, scopes=[], raw_claims={}, auth_provenance="token_validated")
    response = portal.client.get("/api/studio/apps/hosted-notes/billing")
    assert response.status_code == 200, response.text
    projection = response.json()["settings_refinement"]
    return SimpleNamespace(portal=portal, bundle=bundle, contract=contract, versions=versions, store=store, principal=principal, descriptor=projection)


def request_body(build):
    descriptor = build.descriptor
    return {"trigger_source": "refinement", "app_id": "studio", "build_registry_id": "reg-notes",
            "settings_selection": descriptor["selection"], "trigger_payload": {"refinement_request": {
                "artifact_kind": descriptor["artifact_kind"], "artifact_key": descriptor["artifact_key"],
                "artifact_version_id": descriptor["selection"]["artifact_version_id"],
                "raw_user_request": "Change the Notes customer plan price.", "source_surface": "subscription_settings",
            }}}


@pytest.fixture
def settings_trigger(settings_build, monkeypatch):
    from mozaiksai.core.runtime.composition import platform_hooks
    from mozaiksai.hosts import studio

    harness = SimpleNamespace(route_refinement_request=AsyncMock(), coding_enabled=lambda: False)
    def normalize(**kwargs):
        return RefinementRequest(**deepcopy(kwargs["payload"]["refinement_request"]),
            app_id=kwargs["app_id"], target_app_id=kwargs["target_app_id"], user_id=kwargs["user_id"])
    harness.request_from_payload = normalize
    monkeypatch.setattr(studio, "get_orchestration_control_harness", lambda: harness)
    monkeypatch.setattr(platform_hooks, "get_platform_hooks", lambda: SimpleNamespace(call_chat_prereqs=AsyncMock(return_value=(True, None))))
    monkeypatch.setattr(studio, "select_studio_model", AsyncMock(return_value=None))
    monkeypatch.setattr(studio, "get_session_router", lambda: SimpleNamespace(for_target=lambda _: SimpleNamespace()))
    return settings_build, harness


def test_catalog_projects_pinned_contract_not_newer_global_input(settings_build):
    build = settings_build
    assert build.descriptor == {"selection": {
        "kind": "subscription", "dashboard_app_id": "hosted-notes", "build_registry_id": "reg-notes",
        "build_id": "build-one", "app_bundle_id": "version-one", "artifact_version_id": "contract-selected",
    }, "app_id": "studio", "artifact_kind": "subscription_contract", "artifact_key": "subscription_contract"}
    assert all(call.kwargs["build_record_id"] != "contract-newer" for call in build.store.get_build_record.await_args_list)


@pytest.mark.parametrize("field,value", [("app_id", "foreign"), ("build_family", "app_bundle"), ("lifecycle_status", "superseded")])
def test_catalog_keeps_saved_prices_but_has_no_fake_edit_for_unusable_contract(settings_build, field, value):
    setattr(settings_build.contract, field, value)
    response = settings_build.portal.client.get("/api/studio/apps/hosted-notes/billing")
    assert response.status_code == 200
    assert response.json()["status"] == "configured"
    assert "settings_refinement" not in response.json()


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("build_id", "newer-run"), ("artifact_version_id", "newer-bundle")])
async def test_changed_build_is_refused_before_classifier_or_write(settings_trigger, field, value):
    from fastapi import HTTPException

    from mozaiksai.hosts import studio

    build, harness = settings_trigger
    build.portal.record["current_build_run"][field] = value
    with pytest.raises(HTTPException) as failure:
        await studio.trigger_workflow(studio.WorkflowTriggerRequest.model_validate(request_body(build)), build.principal)
    assert failure.value.status_code == 409
    harness.route_refinement_request.assert_not_awaited()
    build.store.create_change_request.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("build_registry_id", "foreign-registry"), ("dashboard_app_id", "foreign-dashboard"), ("artifact_version_id", "contract-newer")])
async def test_cross_app_or_replaced_input_is_refused_before_classifier(settings_trigger, field, value):
    from fastapi import HTTPException

    from mozaiksai.hosts import studio

    build, harness = settings_trigger
    body = deepcopy(request_body(build))
    body["settings_selection"][field] = value
    with pytest.raises(HTTPException) as failure:
        await studio.trigger_workflow(studio.WorkflowTriggerRequest.model_validate(body), build.principal)
    assert failure.value.status_code in {404, 409}
    harness.route_refinement_request.assert_not_awaited()
    build.store.create_change_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_settings_cannot_launch_genesis_or_a_broader_route(settings_trigger):
    from fastapi import HTTPException

    from mozaiksai.hosts import studio

    build, harness = settings_trigger
    harness.route_refinement_request.return_value = SimpleNamespace(is_full_restart=True, workflow_sequence="full_rebuild", workflow_id="ValueEngine")
    with pytest.raises(HTTPException) as failure:
        await studio.trigger_workflow(studio.WorkflowTriggerRequest.model_validate(request_body(build)), build.principal)
    assert failure.value.status_code == 409
    assert "broader app change" in failure.value.detail
    build.store.create_change_request.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("sequence", ["subscription_patch", "subscription_revision"])
async def test_both_subscription_routes_keep_exact_baseline_at_allocation(settings_trigger, monkeypatch, sequence):
    from fastapi import HTTPException

    from mozaiksai.hosts import studio

    build, harness = settings_trigger
    intent = SimpleNamespace(branding_intent=None, requires_workflow_revision=False, change_class=SimpleNamespace(value="patch"), model_dump=lambda **_: {})
    harness.route_refinement_request.return_value = SimpleNamespace(
        is_full_restart=False, workflow_sequence=sequence, workflow_id="SubscriptionContractDesigner", change_intent=intent,
        context_seed={}, impact_set=SimpleNamespace(model_dump=lambda **_: {}), explanation="Bounded subscription edit",
    )
    decision = SimpleNamespace(requires_confirmation=False, decision_type="workflow_reentry", model_dump=lambda **_: {})
    harness.build_harness_decision = lambda _: decision
    seen = {}
    async def allocation(**kwargs):
        seen.update(kwargs)
        raise ValueError("Registered build changed before refinement could start")
    launch = AsyncMock()
    monkeypatch.setattr(studio, "prepare_routed_workflow_launch", allocation)
    monkeypatch.setattr(studio, "launch_prepared_workflow", launch)
    with pytest.raises(HTTPException) as failure:
        await studio.trigger_workflow(studio.WorkflowTriggerRequest.model_validate(request_body(build)), build.principal)
    assert failure.value.status_code == 409
    assert seen["expected_baseline"].model_dump() == {"build_id": "build-one", "artifact_version_id": "version-one"}
    assert seen["journey_id"] == sequence
    assert seen["routing_contribution"].workflow_id == "SubscriptionContractDesigner"
    assert seen["routing_contribution"].require_exact_route is True
    assert seen["routing_contribution"].context_seed["design_docs_artifact_version_id"] == "design-selected"
    assert seen["routing_contribution"].context_seed["subscription_revision_baseline"]["selected_catalog"]["label"] == "Notes plans"
    assert seen["extra_trigger_meta"]["settings_selection"] == build.descriptor["selection"]
    launch.assert_not_awaited()


@pytest.mark.asyncio
async def test_forged_unvalidated_baseline_has_no_settings_action(settings_trigger):
    from fastapi import HTTPException

    from mozaiksai.hosts import studio

    build, harness = settings_trigger
    build.bundle.app_validation_status = "failed"
    with pytest.raises(HTTPException) as failure:
        await studio.trigger_workflow(studio.WorkflowTriggerRequest.model_validate(request_body(build)), build.principal)
    assert failure.value.status_code == 409
    harness.route_refinement_request.assert_not_awaited()


@pytest.mark.parametrize("kind", ["branding", "subscription"])
def test_settings_pause_requires_current_owned_settings_chat(settings_build, monkeypatch, kind):
    from mozaiksai.core.transport.simple_transport import SimpleTransport
    from mozaiksai.hosts import studio

    build = settings_build
    run = {"build_id": "edit-build", "phase": "refinement", "active_chat_id": "edit-chat", "active_workflow_id": "ThemeCapture"}
    build.portal.record["current_build_run"] = run
    saved = {"run_build_binding": {"build_registry_id": "reg-notes", "target_app_id": "notes", "build_id": "edit-build", "phase": "refinement"},
             "trigger_meta": {"settings_selection": {**build.descriptor["selection"], "kind": kind}}}
    registry = studio._get_app_registry_service()
    registry.repo = SimpleNamespace(get_owned_settings_session=AsyncMock(return_value=saved))
    pause = AsyncMock(return_value=True)
    monkeypatch.setattr(SimpleTransport, "get_instance", AsyncMock(return_value=SimpleNamespace(pause_background_workflow=pause)))
    url = "/api/studio/apps/hosted-notes/refinement/pause"
    assert build.portal.client.post(url, json={"kind": kind, "chat_id": "foreign-chat"}).status_code == 409
    pause.assert_not_awaited()
    response = build.portal.client.post(url, json={"kind": kind, "chat_id": "edit-chat"})
    assert response.status_code == 200, response.text
    assert response.json() == {"status": "paused", "chat_id": "edit-chat"}
    pause.assert_awaited_once_with(chat_id="edit-chat", reason="owner_settings_pause", wait_for_completion=True)
    build.portal.state["user"] = "bob"
    assert build.portal.client.post(url, json={"kind": kind, "chat_id": "edit-chat"}).status_code == 404
    assert pause.await_count == 1


def test_settings_pause_does_not_ack_an_inactive_or_failed_pause(settings_build, monkeypatch):
    from mozaiksai.core.transport.simple_transport import SimpleTransport
    from mozaiksai.hosts import studio

    build = settings_build
    build.portal.record["current_build_run"] = {"build_id": "edit-build", "phase": "refinement", "active_chat_id": "edit-chat", "active_workflow_id": "SubscriptionContractDesigner"}
    saved = {"run_build_binding": {"build_registry_id": "reg-notes", "target_app_id": "notes", "build_id": "edit-build", "phase": "refinement"},
             "trigger_meta": {"settings_selection": build.descriptor["selection"]}}
    studio._get_app_registry_service().repo = SimpleNamespace(get_owned_settings_session=AsyncMock(return_value=saved))
    pause = AsyncMock(return_value=False)
    monkeypatch.setattr(SimpleTransport, "get_instance", AsyncMock(return_value=SimpleNamespace(pause_background_workflow=pause)))
    url = "/api/studio/apps/hosted-notes/refinement/pause"
    body = {"kind": "subscription", "chat_id": "edit-chat"}
    assert build.portal.client.post(url, json=body).status_code == 409
    pause.side_effect = RuntimeError("pause failed")
    assert build.portal.client.post(url, json=body).status_code == 503


@pytest.mark.asyncio
async def test_branding_color_proposer_cannot_expand_beyond_saved_theme(settings_trigger, monkeypatch):
    from fastapi import HTTPException

    from mozaiksai.hosts import studio

    build, harness = settings_trigger
    body = request_body(build)
    body["settings_selection"] = {**body["settings_selection"], "kind": "branding", "artifact_version_id": "version-one"}
    body["trigger_payload"] = {"refinement_request": {
        "artifact_kind": "app_bundle", "artifact_key": "app_bundle", "artifact_version_id": "version-one",
        "raw_user_request": "Change the primary color.", "source_surface": "branding_settings",
    }, "coding_request": {}}
    harness.coding_enabled = lambda: True
    intent = SimpleNamespace(branding_intent="theme_color_patch", requires_workflow_revision=False, change_class=SimpleNamespace(value="patch"))
    harness.route_refinement_request.return_value = SimpleNamespace(
        is_full_restart=False, workflow_sequence="app_revision", workflow_id="AppGenerator", change_intent=intent, context_seed={},
    )
    decision = SimpleNamespace(requires_confirmation=False, decision_type="auto_patch", selected_paths=["ui/pages/PrivateAdmin.jsx"])
    harness.build_harness_decision = lambda _: decision
    harness.build_coding_request = lambda **_: SimpleNamespace(files={})
    harness.prepare_coding_request = AsyncMock(return_value=(SimpleNamespace(files={"ui/pages/PrivateAdmin.jsx": "forbidden"}), decision))
    harness.execute_coding_request = AsyncMock()
    registry = studio._get_app_registry_service()
    registry.begin_refinement_run = AsyncMock()
    with pytest.raises(HTTPException) as failure:
        await studio.trigger_workflow(studio.WorkflowTriggerRequest.model_validate(body), build.principal)
    assert failure.value.status_code == 409
    assert "theme settings" in failure.value.detail
    registry.begin_refinement_run.assert_not_awaited()
    harness.execute_coding_request.assert_not_awaited()
    build.store.create_change_request.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper", ["omit", "swap"])
async def test_settings_confirmation_cannot_drop_or_swap_server_selection(settings_trigger, monkeypatch, tamper):
    from fastapi import HTTPException

    from mozaiksai.hosts import studio

    build, harness = settings_trigger
    body = deepcopy(request_body(build))
    normalized = RefinementRequest(**deepcopy(body["trigger_payload"]["refinement_request"]), app_id="studio", target_app_id="notes", user_id="alice")
    pending = {"decision_id": "change-one", "change_request_id": "change-one", "revision_id": "revision-one",
               "metadata": {"source_chat_id": None, "settings_selection": deepcopy(build.descriptor["selection"])},
               "trigger_payload": {"refinement_request": normalized.model_dump()},
               "actions": [{"action_id": "confirm_recommended_workflow"}]}
    if tamper == "omit":
        body.pop("settings_selection")
    else:
        pending["metadata"]["settings_selection"]["kind"] = "branding"
    body["trigger_payload"].update(change_request_id="change-one", revision_id="revision-one", harness_action={"action_id": "confirm_recommended_workflow"})
    snapshot = {"active_revision_id": "revision-one", "pending_harness_decision": pending}
    router = SimpleNamespace(get_session_snapshot=AsyncMock(return_value=snapshot))
    monkeypatch.setattr(studio, "get_session_router", lambda: SimpleNamespace(for_target=lambda _: router))
    build.store.get_change_request = AsyncMock(return_value=SimpleNamespace(id="change-one", created_by_user_id="alice"))
    with pytest.raises(HTTPException) as failure:
        await studio.trigger_workflow(studio.WorkflowTriggerRequest.model_validate(body), build.principal)
    assert failure.value.status_code == 409
    assert "different app settings" in failure.value.detail
    harness.route_refinement_request.assert_not_awaited()
    build.store.create_change_request.assert_not_awaited()
