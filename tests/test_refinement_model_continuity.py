from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from mozaiksai.core.artifacts.models import RefinementRequestPayload
from mozaiksai.core.session.build_binding import RunBuildBinding
from mozaiksai.core.session.model import RoutingDecision


def _binding(**updates):
    return RunBuildBinding.model_validate({
        "build_registry_id": "appreg_tracker", "target_app_id": "tracker",
        "build_id": "build_1", "phase": "genesis", **updates,
    })


def test_binding_is_strict_and_preserves_pre_picker_serialization():
    assert "refinement_model" not in _binding().model_dump()
    assert _binding(refinement_model="openai").model_dump()["refinement_model"] == "openai"
    with pytest.raises(ValidationError):
        _binding(refinement_model="unknown")


def test_saved_change_request_retains_the_selected_model():
    from mozaiksai.control_plane.implementations.refinement_router import RefinementRequest

    request = RefinementRequest(build_family="app_bundle", refinement_model="openai")
    stored = RefinementRequestPayload.model_validate(request.model_dump(mode="python"))
    restored = RefinementRequest.model_validate(stored.model_dump(mode="python"))
    assert restored.refinement_model == "openai"


@pytest.mark.asyncio
async def test_change_request_store_persists_selected_model():
    from mozaiksai.control_plane.implementations.refinement_router import RefinementRequest
    from mozaiksai.core.artifacts.models import ChangeClassification
    from mozaiksai.core.artifacts.store import BuildRecordStore

    collection = SimpleNamespace(insert_one=AsyncMock())
    store = BuildRecordStore()
    store._coll = AsyncMock(return_value=collection)
    request = RefinementRequest(build_family="app_bundle", refinement_model="openai")
    await store.create_change_request(
        app_id="app_1", build_family="app_bundle", build_key="primary", build_record_id=None,
        raw_user_request="Update title", classification=ChangeClassification.PATCH,
        refinement_request=request.model_dump(mode="python"),
        change_intent={"change_class": "patch", "rationale": "A localized change."}, impact_set={},
    )
    inserted = collection.insert_one.await_args.args[0]
    assert inserted["refinement_request"]["refinement_model"] == "openai"


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["genesis", "refinement"])
async def test_factory_agents_use_persisted_binding_model_for_all_phases(monkeypatch, phase):
    from mozaiksai.core.adapters import llm_fallback
    from mozaiksai.core.workflow import llm_config
    from mozaiksai.core.workflow.agents import factory
    from mozaiksai.core.workflow.outputs import structured
    from tests.test_structured_output_fail_closed import _FakeAgent, _patch_agent_factory

    _patch_agent_factory(
        monkeypatch, agent_config={"name": "PlainAgent", "structured_outputs_required": False}, registry={},
    )
    calls = []

    async def capture(*args, **kwargs):
        calls.append(kwargs.get("extra_config"))
        return None, {"config_list": kwargs["extra_config"]["config_list"]}

    monkeypatch.setattr(llm_config, "get_llm_config", capture)
    monkeypatch.setattr(structured, "get_llm_for_workflow", capture)
    monkeypatch.setattr(llm_fallback, "resolve_configured_provider_key", lambda *_args, **_kwargs: "test-key")
    await factory.create_agents("FactoryWorkflow", context_variables={
        "run_build_binding": _binding(phase=phase, refinement_model="openai").model_dump(),
    }, workflow_model="unlisted:must-not-override-frozen-build")
    assert len(_FakeAgent.created) == 1
    assert len(calls) == 2
    assert all(call["config_list"] == [{
        "api_type": "openai", "model": "gpt-5-nano", "api_key": "test-key",
    }] for call in calls)


@pytest.mark.asyncio
async def test_launch_freezes_selection_in_server_binding_and_rejects_source_changes(monkeypatch):
    from mozaiksai.core.session import launcher

    hooks = SimpleNamespace(
        call_chat_prereqs=AsyncMock(return_value=(True, None)),
        call_chat_session_fields=AsyncMock(return_value={"run_build_binding": _binding().model_dump()}),
    )
    router = SimpleNamespace(route_trigger=AsyncMock(return_value=RoutingDecision(
        workflow_id="ValueEngine", requested_workflow_id="ValueEngine",
    )))
    router.for_target = lambda _target: router
    monkeypatch.setattr(launcher, "get_platform_hooks", lambda: hooks)
    monkeypatch.setattr(launcher, "_context_authority_policy_for_workflow", lambda _workflow: SimpleNamespace(
        build_context_keys=set(),
    ))
    monkeypatch.setattr(launcher, "admit_launch_context", lambda _workflow, context, **_kwargs: context)
    prepared = await launcher.prepare_routed_workflow_launch(
        workflow_id="ValueEngine", app_id="factory", user_id="owner", session_router=router,
        trigger_source="transition", refinement_model="openai",
    )
    assert prepared.session_fields["run_build_binding"]["refinement_model"] == "openai"

    with pytest.raises(ValueError, match="source build session"):
        await launcher.prepare_routed_workflow_launch(
            workflow_id="ValueEngine", app_id="factory", user_id="owner", session_router=router,
            trigger_source="transition", source_chat_id="previous_chat", refinement_model="gemini",
        )

    hooks.call_chat_session_fields.return_value = {
        "run_build_binding": _binding(phase="refinement", build_id="build_2").model_dump(),
    }
    new_revision = await launcher.prepare_routed_workflow_launch(
        workflow_id="ValueEngine", app_id="factory", user_id="owner", session_router=router,
        trigger_source="refinement", source_chat_id="previous_chat", refinement_model="gemini",
    )
    assert new_revision.session_fields["run_build_binding"]["refinement_model"] == "gemini"


@pytest.mark.asyncio
async def test_transition_model_selection_is_studio_only_and_preserves_policy_errors(monkeypatch):
    from mozaiksai.hosts import studio_model_policy
    from mozaiksai.hosts.routers import transitions

    body = transitions.TransitionResolveRequest(transition_id="app_type_selector", refinement_model="openai")
    principal = SimpleNamespace(auth_provenance="token_validated")
    monkeypatch.setattr(transitions, "resolve_scope_from_principal", lambda *_args, **_kwargs: ("factory", "owner"))
    policy = AsyncMock(side_effect=HTTPException(422, "model unavailable"))
    monkeypatch.setattr(studio_model_policy, "select_studio_model", policy)

    with pytest.raises(HTTPException) as exc_info:
        await transitions.resolve_transition_route(body, principal, SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(mozaiks_repo_host_bootstrap_hosts={"platform"})),
        ))
    assert exc_info.value.status_code == 403
    policy.assert_not_awaited()

    visitor = SimpleNamespace(auth_provenance="anonymous")
    with pytest.raises(HTTPException) as exc_info:
        await transitions.resolve_transition_route(body, visitor, SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(mozaiks_repo_host_bootstrap_hosts={"studio"})),
        ))
    assert exc_info.value.status_code == 403
    policy.assert_not_awaited()

    with pytest.raises(HTTPException) as exc_info:
        await transitions.resolve_transition_route(body, principal, SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(mozaiks_repo_host_bootstrap_hosts={"studio"})),
        ))
    assert exc_info.value.status_code == 422
    policy.assert_awaited_once()


@pytest.mark.asyncio
async def test_studio_create_resolves_policy_default_when_selection_is_omitted(monkeypatch):
    from mozaiksai.hosts import studio_model_policy
    from mozaiksai.hosts.routers import transitions

    principal = SimpleNamespace(auth_provenance="local_development")
    monkeypatch.setattr(transitions, "resolve_scope_from_principal", lambda *_args, **_kwargs: ("factory", "owner"))
    policy = AsyncMock(return_value="gemini")
    monkeypatch.setattr(studio_model_policy, "select_studio_model", policy)
    launch = AsyncMock(return_value=SimpleNamespace(
        resolution_type="transition", option_id="brownfield_app", journey_id="brownfield_app_adoption",
        next_transition_id="brownfield_path_selector", transition=SimpleNamespace(model_dump=lambda **_: {}),
        context_variables={},
    ))
    monkeypatch.setattr(transitions, "launch_transition", launch)
    await transitions.resolve_transition_route(
        transitions.TransitionResolveRequest(transition_id="app_type_selector"), principal,
        SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(mozaiks_repo_host_bootstrap_hosts={"studio"}))),
    )
    policy.assert_awaited_once_with(principal, None)
    assert launch.await_args.kwargs["refinement_model"] == "gemini"


@pytest.mark.asyncio
@pytest.mark.parametrize("existing", [False, True])
async def test_direct_studio_factory_start_preserves_the_run_binding(monkeypatch, existing):
    from mozaiksai.core import session
    from mozaiksai.hosts import platform, studio_model_policy

    current = _binding(refinement_model="openai" if existing else None)
    collection = SimpleNamespace(find_one=AsyncMock(return_value={
        "_id": "old_chat", "run_build_binding": current.model_dump(),
    } if existing else None))
    hooks = SimpleNamespace(
        call_chat_prereqs=AsyncMock(return_value=(True, None)),
        call_chat_session_fields=AsyncMock(return_value={"run_build_binding": current.model_dump()}),
    )
    monkeypatch.setattr(platform, "validate_path_app_id", lambda *_args: None)
    monkeypatch.setattr(platform, "_validate_user_id_against_principal", lambda *_args, **_kwargs: "owner")
    monkeypatch.setattr(platform, "_resolve_requested_workflow_name", lambda workflow: workflow)
    monkeypatch.setattr(platform, "authorize_http_workflow_source_paths", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(platform, "validate_context_for_workflow", lambda _workflow, context: context)
    monkeypatch.setattr(platform, "get_platform_hooks", lambda: hooks)
    monkeypatch.setattr(platform.runtime_app, "_chat_coll", AsyncMock(return_value=collection))
    monkeypatch.setattr(platform.persistence_manager, "get_or_assign_cache_seed", AsyncMock(return_value=1))
    created = AsyncMock()
    monkeypatch.setattr(platform, "create_routed_chat_session", created)
    monkeypatch.setattr(session, "get_session_router", lambda: object())
    select = AsyncMock(return_value="gemini")
    monkeypatch.setattr(studio_model_policy, "select_studio_model", select)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(mozaiks_repo_host_bootstrap_hosts={"studio"})),
        json=AsyncMock(return_value={"source_chat_id": "old_chat"} if existing else {}),
    )
    principal = SimpleNamespace(user_id="owner", auth_provenance="local_development")
    await platform.start_chat("factory", "ValueEngine", request, principal)
    binding = created.await_args.kwargs["session_fields"]["run_build_binding"]
    assert binding["refinement_model"] == ("openai" if existing else "gemini")
    if existing:
        select.assert_not_awaited()
        hooks.call_chat_session_fields.reset_mock()
        request.json.return_value["trigger_meta"] = {"refinement_model": "gemini"}
        with pytest.raises(HTTPException) as exc_info:
            await platform.start_chat("factory", "ValueEngine", request, principal)
        assert exc_info.value.status_code == 409
        hooks.call_chat_session_fields.assert_not_awaited()
    else:
        select.assert_awaited_once()
        hooks.call_chat_session_fields.reset_mock()
        select.side_effect = HTTPException(422, "model unavailable")
        with pytest.raises(HTTPException) as exc_info:
            await platform.start_chat("factory", "ValueEngine", request, principal)
        assert exc_info.value.status_code == 422
        hooks.call_chat_session_fields.assert_not_awaited()
