from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from mozaiksai.core.adapters import ag2_network_runner
from mozaiksai.core.auth import UserPrincipal
from mozaiksai.core.runtime.persistence.distributed_lock import chat_execution_lease
from mozaiksai.core.workflow import llm_config
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
            {**scope, "status": 0, "run_build_binding.refinement_model": None, "workflow_ui_state.pending_input_request": None},
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
@pytest.mark.parametrize("header,allowed", [("app-1", True), ("app-2", False), ("model", False)])
async def test_gateway_model_route_checks_the_scoped_app_header(header, allowed, monkeypatch):
    monkeypatch.setattr(chat.runtime_app, "_ENFORCE_PRINCIPAL_HEADERS", True)
    request = SimpleNamespace(headers={"x-app-id": header}, url=SimpleNamespace(path="/api/chats/model/app-1/Workflow/chat-1"))
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
