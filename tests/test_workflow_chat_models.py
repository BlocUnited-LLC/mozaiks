from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from mozaiksai.core.adapters import ag2_network_runner
from mozaiksai.core.auth import UserPrincipal
from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager
from mozaiksai.core.runtime.persistence.distributed_lock import chat_execution_lease
from mozaiksai.core.workflow import llm_config
from mozaiksai.hosts import studio_model_policy
from mozaiksai.hosts.routers import chat


@pytest.fixture
def room(monkeypatch):
    coll = SimpleNamespace(
        find_one=AsyncMock(return_value={"status": 0}),
        update_one=AsyncMock(return_value=SimpleNamespace(matched_count=1)),
    )
    monkeypatch.setattr(chat.runtime_app, "_chat_coll", AsyncMock(return_value=coll))
    monkeypatch.setattr(chat.runtime_app, "simple_transport", None)
    monkeypatch.setattr(chat.runtime_app.persistence_manager, "chat_has_resumable_run", AsyncMock(return_value=False))
    monkeypatch.setattr(ag2_network_runner, "model_change_checkpoint", AsyncMock(return_value="new"))
    monkeypatch.setenv("MOZAIKS_CHAT_LOCK_MODE", "local")
    monkeypatch.setattr(llm_config, "_load_raw_config_list", AsyncMock(return_value=[
        {"api_type": "google", "model": "no-key", "base_url": "https://unused.invalid"},
        {"api_type": "openai", "model": "model-b", "api_key": "private", "base_url": "https://internal.invalid"},
        {"api_type": "openai", "model": "model-b", "api_key": "duplicate"},
        {"api_type": "ollama", "model": "local"},
    ]))
    principal = UserPrincipal(user_id="viewer", name=None, email=None, scopes=["user"], roles=[], raw_claims={})
    return coll, ("app-1", "Workflow", "chat-1", principal)


@pytest.mark.asyncio
async def test_model_catalog_and_selection_are_scoped_and_only_return_public_fields(room):
    coll, args = room
    options = await chat.get_workflow_model(*args)
    assert options == {
        "models": [{"id": "openai:model-b", "label": "model-b"}, {"id": "ollama:local", "label": "local"}],
        "default": None, "selected": None, "locked": False,
    }
    scope = {"_id": "chat-1", "app_id": "app-1", "workflow_name": "Workflow",
             "user_id": "viewer", "transport_purpose": {"$ne": "ask_carrier"}}
    assert coll.find_one.await_args.args[0] == scope
    for selected in ("openai:model-b", None):
        coll.find_one.return_value = {"status": 0, "workflow_model": None if selected else "openai:model-b"}
        actual = await chat._workflow_model_options(*args, chat.WorkflowModelSelection(model=selected))
        assert actual == {**options, "selected": selected}
        coll.update_one.assert_awaited_with(
            {**scope, "status": 0, "run_build_binding.refinement_model": None,
             "workflow_run_model": None, "workflow_ui_state.pending_input_request": None},
            {"$set": {"workflow_model": selected, "workflow_model_rebind_pending": False}},
        )


@pytest.mark.asyncio
async def test_default_identity_and_duplicate_provider_precedence(room, monkeypatch):
    first = {"model": "shared", "api_key": "key-a", "max_tokens": 90}
    monkeypatch.setattr(llm_config, "_load_raw_config_list", AsyncMock(return_value=[
        first, {"model": "shared", "api_key": "other"}, {"api_type": "anthropic", "model": "shared", "api_key": "key-b"},
    ]))
    default, catalog = await llm_config.workflow_model_catalog()
    assert default == "openai:shared"
    assert catalog[default] is first
    assert list(catalog) == ["openai:shared", "anthropic:shared"]
    assert [m["label"] for m in (await chat.get_workflow_model(*room[1]))["models"]] == ["shared (openai)", "shared (anthropic)"]


@pytest.mark.asyncio
async def test_removed_unlisted_and_pinned_models(room):
    coll, args = room
    coll.find_one.return_value = {"workflow_model": "old:model"}
    assert (await chat.get_workflow_model(*args))["selected"] == "old:model"
    with pytest.raises(HTTPException) as exc:
        await chat._workflow_model_options(*args, chat.WorkflowModelSelection(model="unconfigured"))
    assert exc.value.status_code == 422
    coll.find_one.return_value = {"run_build_binding": {"refinement_model": "gemini"}}
    assert (await chat.get_workflow_model(*args)) == {
        "locked": True, "selected": "google:gemini-3.8-flash", "default": "google:gemini-3.8-flash",
        "models": [{"id": "google:gemini-3.8-flash", "label": "gemini-3.8-flash"}],
    }
    with pytest.raises(HTTPException) as exc:
        await chat._workflow_model_options(*args, chat.WorkflowModelSelection(model=None))
    assert exc.value.status_code == 409
    coll.find_one.return_value = None
    with pytest.raises(HTTPException) as exc:
        await chat.get_workflow_model(*args)
    assert exc.value.status_code == 404
    coll.update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_input_requests_and_active_turns_cannot_change_model(room, monkeypatch):
    coll, args = room
    transport = SimpleNamespace(_input_request_registries={"chat-1": {"request-1": object()}}, get_live_ag2_workflow_run=lambda _: None)
    monkeypatch.setattr(chat.runtime_app, "simple_transport", transport)
    choice = chat.WorkflowModelSelection(model="ollama:local")
    with pytest.raises(HTTPException) as exc:
        await chat._workflow_model_options(*args, choice)
    assert exc.value.status_code == 409
    transport._input_request_registries.clear()
    monkeypatch.setenv("DISTRIBUTED_LOCK_MAX_RETRIES", "0")
    async with chat_execution_lease(app_id="app-1", chat_id="chat-1"):
        with pytest.raises(HTTPException) as exc:
            await chat._workflow_model_options(*args, choice)
        assert exc.value.status_code == 409
    coll.update_one.assert_not_awaited()
    coll.update_one.return_value.matched_count = 0
    with pytest.raises(HTTPException) as exc:
        await chat._workflow_model_options(*args, choice)
    assert exc.value.status_code == 409


def test_client_cannot_supply_its_own_provider_configuration():
    with pytest.raises(ValidationError):
        chat.WorkflowModelSelection(model="ollama:local", base_url="https://elsewhere.invalid")


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/chats/model/app-1/Workflow/chat-1", "/api/chats/model-options/app-1/Workflow"])
@pytest.mark.parametrize("header,allowed", [("app-1", True), ("app-2", False), ("model", False)])
async def test_gateway_model_route_checks_the_scoped_app_header(path, header, allowed, monkeypatch):
    monkeypatch.setattr(chat.runtime_app, "_ENFORCE_PRINCIPAL_HEADERS", True)
    request = SimpleNamespace(headers={"x-app-id": header}, url=SimpleNamespace(path=path))
    next_route = AsyncMock(return_value="allowed")
    response = await chat.runtime_app.principal_header_middleware(request, next_route)
    if allowed:
        assert response == "allowed"
        next_route.assert_awaited_once_with(request)
    else:
        assert response.status_code == 403
        next_route.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("checkpoint,started,live_idle,accepted", [
    ("idle", True, None, True), ("idle", True, True, True), ("idle", True, False, False),
    ("busy", True, None, False), ("new", True, True, False), ("idle", False, None, False),
])
async def test_put_checks_durable_and_local_position_before_mutating(room, monkeypatch, checkpoint, started, live_idle, accepted):
    coll, args = room
    coll.find_one.return_value = {"workflow_model": "removed-model", "status": 0}
    monkeypatch.setattr(ag2_network_runner, "model_change_checkpoint", AsyncMock(return_value=checkpoint))
    monkeypatch.setattr(chat.runtime_app.persistence_manager, "chat_has_resumable_run", AsyncMock(return_value=started))
    live = SimpleNamespace(can_rebind_model=AsyncMock(return_value=live_idle)) if live_idle is not None else None
    monkeypatch.setattr(chat.runtime_app, "simple_transport", SimpleNamespace(
        get_live_ag2_workflow_run=lambda _: live, _input_request_registries={},
    ))
    if accepted:
        response = await chat._workflow_model_options(*args, chat.WorkflowModelSelection(model=None))
        assert response["selected"] is None
        assert coll.update_one.await_args.args[1]["$set"]["workflow_model_rebind_pending"] is True
    else:
        with pytest.raises(HTTPException) as exc:
            await chat._workflow_model_options(*args, chat.WorkflowModelSelection(model=None))
        assert exc.value.status_code == 409
        coll.update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_owner_hint_only_overrides_shared_development_principal(room):
    coll, args = room
    with pytest.raises(HTTPException) as exc:
        await chat.get_workflow_model(*args, user_hint="someone-else")
    assert exc.value.status_code == 403
    coll.find_one.assert_not_awaited()
    args[-1].user_id = "anonymous"
    args[-1].auth_provenance = "local_development"
    await chat.get_workflow_model(*args, user_hint="local-owner")
    assert coll.find_one.await_args.args[0]["user_id"] == "local-owner"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [1, 2])
async def test_settled_chat_cannot_change_models(room, status):
    coll, args = room
    coll.find_one.return_value = {"status": status}
    with pytest.raises(HTTPException) as exc:
        await chat._workflow_model_options(*args, chat.WorkflowModelSelection(model="ollama:local"))
    assert exc.value.status_code == 409
    coll.update_one.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("configured,default", [({"gemini", "openai"}, "gemini"), ({"openai"}, "openai"), ({"gemini"}, "gemini")])
async def test_run_model_options_share_the_studio_default_and_provider_availability(room, monkeypatch, configured, default):
    _, (app_id, workflow, _, principal) = room
    monkeypatch.setattr(studio_model_policy, "_load_policy", AsyncMock(return_value=(studio_model_policy.StudioModelPolicy(), False)))
    monkeypatch.setattr(studio_model_policy, "_configured_models", lambda: configured)
    options = await chat.get_workflow_run_model_options(app_id, workflow, principal)
    assert options["default"] == default
    assert {model["id"] for model in options["models"]} == configured
    assert all(set(model) == {"label", "id"} for model in options["models"])
    with pytest.raises(HTTPException) as exc:
        await chat.get_workflow_run_model_options(app_id, workflow, principal, user_hint="someone-else")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_locked_workflow_model_cannot_change_across_turns(room, monkeypatch):
    coll, args = room
    coll.find_one.return_value = {"status": 0, "workflow_run_model": "openai", "workflow_model": "google:mutable"}
    options = await chat.get_workflow_model(*args)
    assert options == {
        "locked": True, "selected": "openai", "default": "openai",
        "models": [{"id": "openai", "label": "GPT-5 Nano"}],
    }
    with pytest.raises(HTTPException) as exc:
        await chat.set_workflow_model(*args[:3], chat.WorkflowModelSelection(model=None), args[3])
    assert exc.value.status_code == 409
    coll.update_one.assert_not_awaited()
    persistence = AG2PersistenceManager()
    monkeypatch.setattr(persistence, "_coll", AsyncMock(return_value=coll))
    preference = await persistence.assert_chat_resumable("chat-1", "app-1")
    assert preference is not None
    assert preference.selected == "openai"
    assert preference.rebind_pending is False


@pytest.mark.asyncio
@pytest.mark.parametrize("host_name", ["runtime", "platform"])
async def test_start_chat_binds_explicit_model_before_returning_session(room, monkeypatch, host_name):
    from mozaiksai.core import session
    from mozaiksai.hosts import platform

    coll, (app_id, workflow, _, principal) = room
    coll.find_one.return_value = None
    create = AsyncMock()
    monkeypatch.setattr(session, "create_routed_chat_session", create)
    monkeypatch.setattr(session, "get_session_router", lambda: None)
    monkeypatch.setattr(session, "get_session_router_for_chat", AsyncMock())
    monkeypatch.setattr(platform, "create_routed_chat_session", create)
    monkeypatch.setattr(platform.get_platform_hooks(), "call_chat_prereqs", AsyncMock(return_value=(True, None)))
    monkeypatch.setattr(studio_model_policy, "_load_policy", AsyncMock(return_value=(studio_model_policy.StudioModelPolicy(), False)))
    monkeypatch.setattr(studio_model_policy, "_configured_models", lambda: {"openai"})
    seed = AsyncMock(return_value=None)
    monkeypatch.setattr(chat.runtime_app.persistence_manager, "get_or_assign_cache_seed", seed)
    monkeypatch.setattr(platform.persistence_manager, "get_or_assign_cache_seed", seed)
    request = SimpleNamespace(json=AsyncMock(return_value={"run_model": "openai", "client_request_id": "launch-1"}))
    host = platform if host_name == "platform" else chat.runtime_app

    started = await host.start_chat(app_id, workflow, request, principal)
    assert started["success"] is True
    assert create.await_args.kwargs["run_model"] == "openai"
    assert create.await_args.kwargs["chat_id"] == started["chat_id"]
    assert "workflow_run_model" not in create.await_args.kwargs["context_variables"]

    coll.find_one.return_value = {"chat_id": started["chat_id"], "workflow_run_model": "openai"}
    reused = await host.start_chat(app_id, workflow, request, principal)
    assert reused["reused"] is True
    assert reused["chat_id"] == started["chat_id"]

    coll.find_one.return_value["workflow_run_model"] = "gemini"
    with pytest.raises(HTTPException) as exc:
        await host.start_chat(app_id, workflow, request, principal)
    assert exc.value.status_code == 409

    request.json.return_value["run_model"] = "gemini"
    with pytest.raises(HTTPException) as exc:
        await host.start_chat(app_id, workflow, request, principal)
    assert exc.value.status_code == 422
    create.assert_awaited_once()


@pytest.mark.asyncio
async def test_bound_model_resolves_one_provider_without_a_catalog_fallback(monkeypatch):
    keys = {"google": "fake-google", "openai": "fake-openai"}
    monkeypatch.setattr(llm_config, "resolve_configured_provider_key", keys.get)
    fallback = AsyncMock()
    monkeypatch.setattr(llm_config, "workflow_model_catalog", fallback)
    gemini = await llm_config.resolve_workflow_model("gemini")
    openai = await llm_config.resolve_workflow_model("openai")
    assert gemini == {"api_type": "google", "model": "gemini-3.8-flash", "api_key": "fake-google"}
    assert openai == {"api_type": "openai", "model": "gpt-5-nano", "api_key": "fake-openai"}
    del keys["openai"]
    assert await llm_config.resolve_workflow_model("openai") is None
    fallback.assert_not_awaited()


@pytest.mark.asyncio
async def test_first_visible_workflow_session_already_has_its_immutable_model(room, monkeypatch):
    coll, (app_id, workflow, chat_id, principal) = room
    coll.find_one.return_value = None
    coll.insert_one = AsyncMock()
    manager = AG2PersistenceManager()
    monkeypatch.setattr(manager, "_coll", AsyncMock(return_value=coll))
    scope = {"chat_id": chat_id, "app_id": app_id, "workflow_name": workflow, "user_id": principal.user_id}

    await manager.create_chat_session(**scope, run_model="gemini")
    inserted = coll.insert_one.await_args.args[0]
    assert inserted == {
        **inserted, "_id": chat_id, "app_id": app_id, "user_id": principal.user_id,
        "workflow_name": workflow, "status": 0, "workflow_run_model": "gemini",
    }

    coll.find_one.return_value = inserted
    preference = await manager.assert_chat_resumable(chat_id, app_id)
    assert preference is not None and preference.selected == "gemini"
    await manager.create_chat_session(**scope, run_model="gemini")
    with pytest.raises(ValueError, match="different run binding"):
        await manager.create_chat_session(**scope, run_model="openai")
    with pytest.raises(ValueError, match="server-owned"):
        await manager.create_chat_session(**scope, extra_fields={"workflow_run_model": "openai"})
    with pytest.raises(ValueError, match="only be bound"):
        await manager.persist_server_owned_session_fields(**scope, fields={"workflow_run_model": "openai"})
    coll.insert_one.assert_awaited_once()
    coll.update_one.assert_not_awaited()
