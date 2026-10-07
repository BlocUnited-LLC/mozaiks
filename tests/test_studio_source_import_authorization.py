from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import BackgroundTasks, HTTPException
from fastapi.testclient import TestClient

from mozaiksai.hosts import studio
from mozaiksai.hosts.routers import transitions


@pytest.mark.parametrize("route", ["app-intelligence/index", "source-import"])
def test_authenticated_http_request_cannot_enqueue_arbitrary_server_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path, route: str
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_PROVIDER", "jwt")
    monkeypatch.setenv("AUTH_AUDIENCE", "studio-source-import-test")
    monkeypatch.setattr(studio, "_resolve_studio_scope", lambda *_args, **_kwargs: ("app_1", "alice"))

    async def unexpected_job(**_kwargs):
        raise AssertionError("local workspace job must not be enqueued")

    monkeypatch.setattr(studio, "_start_studio_app_intelligence_index_job", unexpected_job)
    studio.app.dependency_overrides[studio.require_studio_user] = lambda: object()
    try:
        response = TestClient(studio.app).post(
            f"/api/studio/apps/app_1/context/{route}",
            json={"source_kind": "local_workspace", "workspace_root": str(tmp_path)},
        )
    finally:
        studio.app.dependency_overrides.pop(studio.require_studio_user, None)

    assert response.status_code == 403


@pytest.mark.parametrize(
    "handler_name",
    ["index_studio_app_intelligence_context", "import_studio_app_source_context"],
)
@pytest.mark.parametrize("authenticated", [True, False])
def test_http_local_workspace_requires_explicit_local_auth_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    handler_name: str,
    authenticated: bool,
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "true" if authenticated else "false")
    monkeypatch.setenv("AUTH_PROVIDER", "jwt" if authenticated else "none")
    monkeypatch.setenv("AUTH_AUDIENCE", "studio-source-import-test")
    monkeypatch.setattr(studio, "_resolve_studio_scope", lambda *_args, **_kwargs: ("app_1", "alice"))
    started: list[str] = []

    async def start_job(**kwargs):
        started.append(kwargs["body"].workspace_root)
        return {"accepted": True}

    monkeypatch.setattr(studio, "_start_studio_app_intelligence_index_job", start_job)
    body = studio.AppIntelligenceIndexRequest(source_kind="local_workspace", workspace_root=str(tmp_path))
    handler = getattr(studio, handler_name)
    args = ("app_1", body, BackgroundTasks(), object())

    if authenticated:
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(handler(*args))
        assert exc_info.value.status_code == 403
        assert started == []
    else:
        assert asyncio.run(handler(*args)) == {"accepted": True}
        assert started == [str(tmp_path)]


@pytest.mark.parametrize(
    "handler_name",
    ["index_studio_app_intelligence_context", "import_studio_app_source_context"],
)
def test_http_local_workspace_rejects_implicit_demo_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path, handler_name: str
) -> None:
    monkeypatch.delenv("AUTH_ENABLED", raising=False)
    monkeypatch.delenv("AUTH_PROVIDER", raising=False)
    monkeypatch.delenv("AUTH_ANON_ACCESS", raising=False)
    monkeypatch.delenv("AUTH_JWKS_URL", raising=False)
    monkeypatch.delenv("AUTH_ISSUER", raising=False)
    monkeypatch.setattr(studio, "_resolve_studio_scope", lambda *_args, **_kwargs: ("app_1", "alice"))

    async def unexpected_job(**_kwargs):
        raise AssertionError("local workspace job must not be enqueued")

    monkeypatch.setattr(studio, "_start_studio_app_intelligence_index_job", unexpected_job)
    body = studio.AppIntelligenceIndexRequest(source_kind="local_workspace", workspace_root=str(tmp_path))
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(getattr(studio, handler_name)("app_1", body, BackgroundTasks(), object()))
    assert exc_info.value.status_code == 403


def test_authenticated_public_git_import_keeps_its_existing_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_PROVIDER", "jwt")
    monkeypatch.setattr(studio, "_resolve_studio_scope", lambda *_args, **_kwargs: ("app_1", "alice"))

    async def start_job(**kwargs):
        assert kwargs["body"].source_kind == "git_repository"
        return {"accepted": True}

    monkeypatch.setattr(studio, "_start_studio_app_intelligence_index_job", start_job)
    body = studio.AppIntelligenceIndexRequest(source_kind="git_repository", repo_url="https://github.com/example/app")
    assert asyncio.run(studio.import_studio_app_source_context("app_1", body, BackgroundTasks(), object())) == {
        "accepted": True
    }


@pytest.mark.parametrize(
    "context_variables",
    [
        {"repo_path": "/private/app"},
        {"frontend_repo_path": "/private/frontend"},
        {"backend_repo_path": "/private/backend"},
        {"uploaded_openapi_path": "/private/openapi.json"},
        {"host_app_source": "workspace_app"},
        {"discovery_inputs": {"repo_path": "/private/app"}},
        {"discovery_inputs": {"frontend_repo_path": "/private/frontend"}},
        {"discovery_inputs": {"backend_repo_path": "/private/backend"}},
        {"discovery_inputs": {"uploaded_openapi_path": "/private/openapi.json"}},
        {"discovery_inputs": {"host_app_source": "workspace_app"}},
        {"discovery_inputs": json.dumps({"repo_path": "/private/app"})},
    ],
)
@pytest.mark.parametrize("entrypoint", ["workflow_trigger", "transition_resolve"])
def test_authenticated_workflow_routes_reject_caller_selected_server_sources_before_dispatch(
    monkeypatch: pytest.MonkeyPatch, context_variables: dict, entrypoint: str
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_PROVIDER", "jwt")
    monkeypatch.setenv("AUTH_AUDIENCE", "studio-source-import-test")
    dispatch = AsyncMock(side_effect=AssertionError("source path must not reach workflow dispatch"))

    if entrypoint == "workflow_trigger":
        monkeypatch.setattr(studio, "_resolve_studio_scope", lambda *_args, **_kwargs: ("app_1", "alice"))
        monkeypatch.setattr(studio, "prepare_routed_workflow_launch", dispatch)

        def unexpected_router():
            raise AssertionError("source path must not reach the session router")

        monkeypatch.setattr(studio, "get_session_router", unexpected_router)
        handler = studio.trigger_workflow
        body = studio.WorkflowTriggerRequest(
            workflow_id="ExistingAppDiscovery", context_variables=context_variables,
        )
    else:
        monkeypatch.setattr(
            transitions, "resolve_scope_from_principal", lambda *_args, **_kwargs: ("app_1", "alice"),
        )
        monkeypatch.setattr(transitions, "launch_transition", dispatch)
        handler = transitions.resolve_transition_route
        body = transitions.TransitionResolveRequest(
            transition_id="brownfield_repo_input", context_variables=context_variables,
        )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(handler(body, object()))
    assert exc_info.value.status_code == 403
    dispatch.assert_not_awaited()


@pytest.mark.parametrize(
    ("route", "payload"),
    [
        (
            "/api/workflows/trigger",
            {"workflow_id": "ExistingAppDiscovery", "context_variables": {
                "discovery_inputs": {"backend_repo_path": "/private/backend"},
            }},
        ),
        (
            "/api/transitions/resolve",
            {"transition_id": "brownfield_repo_input", "context_variables": {
                "discovery_inputs": {"backend_repo_path": "/private/backend"},
            }},
        ),
    ],
)
def test_authenticated_http_workflow_entrypoints_reject_local_sources(
    monkeypatch: pytest.MonkeyPatch, route: str, payload: dict
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_PROVIDER", "jwt")
    monkeypatch.setenv("AUTH_AUDIENCE", "studio-source-import-test")
    monkeypatch.setattr(studio, "_resolve_studio_scope", lambda *_args, **_kwargs: ("app_1", "alice"))
    monkeypatch.setattr(
        transitions, "resolve_scope_from_principal", lambda *_args, **_kwargs: ("app_1", "alice"),
    )
    dispatch = AsyncMock(side_effect=AssertionError("source path must not reach workflow dispatch"))
    monkeypatch.setattr(studio, "prepare_routed_workflow_launch", dispatch)
    monkeypatch.setattr(transitions, "launch_transition", dispatch)
    studio.app.dependency_overrides[studio.require_studio_user] = lambda: object()
    studio.app.dependency_overrides[transitions.require_user_scope] = lambda: object()
    try:
        response = TestClient(studio.app).post(route, json=payload)
    finally:
        studio.app.dependency_overrides.pop(studio.require_studio_user, None)
        studio.app.dependency_overrides.pop(transitions.require_user_scope, None)

    assert response.status_code == 403
    dispatch.assert_not_awaited()


@pytest.mark.parametrize("entrypoint", ["workflow_trigger", "transition_resolve"])
@pytest.mark.parametrize(
    ("authenticated", "context_variables"),
    [
        (False, {"discovery_inputs": {"repo_path": "/local/app"}}),
        (True, {"discovery_inputs": {"github_repo": "example/app"}}),
    ],
)
def test_workflow_routes_dispatch_authorized_discovery_inputs(
    monkeypatch: pytest.MonkeyPatch, entrypoint: str, authenticated: bool, context_variables: dict
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "true" if authenticated else "false")
    monkeypatch.setenv("AUTH_PROVIDER", "jwt" if authenticated else "none")
    monkeypatch.setenv("AUTH_AUDIENCE", "studio-source-import-test")
    workflow_launch = SimpleNamespace(
        chat_id="chat_1", workflow_id="ExistingAppDiscovery",
        requested_workflow_id="ExistingAppDiscovery", journey_id="brownfield_app_adoption",
        websocket_url="/ws/chat_1", trigger_source="manual", routing_explanation="",
        rerouted_by_dependency=False,
    )

    if entrypoint == "workflow_trigger":
        monkeypatch.setattr(studio, "_resolve_studio_scope", lambda *_args, **_kwargs: ("app_1", "alice"))
        monkeypatch.setattr(studio, "get_session_router", lambda: object())
        monkeypatch.setattr(studio, "get_orchestration_control_harness", lambda: object())
        dispatch = AsyncMock(return_value=SimpleNamespace(
            workflow_id="ExistingAppDiscovery", routing_decision=SimpleNamespace(),
        ))
        monkeypatch.setattr(studio, "prepare_routed_workflow_launch", dispatch)
        monkeypatch.setattr(studio, "launch_prepared_workflow", AsyncMock(return_value=workflow_launch))
        body = studio.WorkflowTriggerRequest(
            workflow_id="ExistingAppDiscovery", context_variables=context_variables,
        )
        result = asyncio.run(studio.trigger_workflow(body, object()))
    else:
        monkeypatch.setattr(
            transitions, "resolve_scope_from_principal", lambda *_args, **_kwargs: ("app_1", "alice"),
        )
        dispatch = AsyncMock(return_value=SimpleNamespace(
            resolution_type="workflow", transition_id="brownfield_repo_input",
            option_id=None, journey_id="brownfield_app_adoption",
            context_variables=context_variables, workflow_launch=workflow_launch,
        ))
        monkeypatch.setattr(transitions, "launch_transition", dispatch)
        body = transitions.TransitionResolveRequest(
            transition_id="brownfield_repo_input", context_variables=context_variables,
        )
        result = asyncio.run(transitions.resolve_transition_route(body, object()))

    assert result["workflow_id"] == "ExistingAppDiscovery"
    dispatch.assert_awaited_once()
