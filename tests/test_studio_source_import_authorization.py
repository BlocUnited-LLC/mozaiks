from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import BackgroundTasks, HTTPException
from fastapi.testclient import TestClient

from mozaiksai.core.auth import UserPrincipal
from mozaiksai.hosts import platform, runtime, studio
from mozaiksai.hosts.routers import transitions


def _principal(provenance: str) -> UserPrincipal:
    return UserPrincipal(
        user_id="alice", email=None, name=None, roles=[], scopes=[], raw_claims={},
        auth_provenance=provenance,
    )


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
    studio.app.dependency_overrides[studio.require_studio_user] = lambda: _principal("token_validated")
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
    provenance = "token_validated" if authenticated else "local_development"
    args = ("app_1", body, BackgroundTasks(), _principal(provenance))

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
        asyncio.run(getattr(studio, handler_name)(
            "app_1", body, BackgroundTasks(), _principal("local_development"),
        ))
    assert exc_info.value.status_code == 403


@pytest.mark.parametrize(
    "handler_name",
    ["index_studio_app_intelligence_context", "import_studio_app_source_context"],
)
def test_public_visitor_cannot_select_studio_server_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path, handler_name: str,
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("AUTH_PROVIDER", "none")
    monkeypatch.setenv("AUTH_ANON_ACCESS", "public")
    monkeypatch.setattr(studio, "_resolve_studio_scope", lambda *_args, **_kwargs: ("app_1", "alice"))
    start_job = AsyncMock(side_effect=AssertionError("public visitor must not enqueue a source path"))
    monkeypatch.setattr(studio, "_start_studio_app_intelligence_index_job", start_job)
    body = studio.AppIntelligenceIndexRequest(source_kind="local_workspace", workspace_root=str(tmp_path))

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(getattr(studio, handler_name)(
            "app_1", body, BackgroundTasks(), _principal("anonymous"),
        ))

    assert exc_info.value.status_code == 403
    start_job.assert_not_awaited()


def test_authenticated_public_git_import_keeps_its_existing_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_PROVIDER", "jwt")
    monkeypatch.setattr(studio, "_resolve_studio_scope", lambda *_args, **_kwargs: ("app_1", "alice"))

    async def start_job(**kwargs):
        assert kwargs["body"].source_kind == "git_repository"
        return {"accepted": True}

    monkeypatch.setattr(studio, "_start_studio_app_intelligence_index_job", start_job)
    body = studio.AppIntelligenceIndexRequest(source_kind="git_repository", repo_url="https://github.com/example/app")
    assert asyncio.run(studio.import_studio_app_source_context(
        "app_1", body, BackgroundTasks(), _principal("token_validated"),
    )) == {
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
        asyncio.run(handler(body, _principal("token_validated")))
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
    studio.app.dependency_overrides[studio.require_studio_user] = lambda: _principal("token_validated")
    studio.app.dependency_overrides[transitions.require_user_scope] = lambda: _principal("token_validated")
    try:
        response = TestClient(studio.app).post(route, json=payload)
    finally:
        studio.app.dependency_overrides.pop(studio.require_studio_user, None)
        studio.app.dependency_overrides.pop(transitions.require_user_scope, None)

    assert response.status_code == 403
    dispatch.assert_not_awaited()


@pytest.mark.parametrize(
    ("access_mode", "peer", "expected_status"),
    [
        ("public", "203.0.113.10", 403),
        ("local", "127.0.0.1", 200),
    ],
)
def test_shared_transition_route_requires_request_development_access_for_local_source(
    monkeypatch: pytest.MonkeyPatch, access_mode: str, peer: str, expected_status: int,
) -> None:
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("AUTH_PROVIDER", "none")
    monkeypatch.setenv("AUTH_ANON_ACCESS", access_mode)
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    monkeypatch.setattr(
        transitions, "resolve_scope_from_principal", lambda *_args, **_kwargs: ("app_1", "alice"),
    )
    workflow_launch = SimpleNamespace(
        chat_id="chat_1", workflow_id="ExistingAppDiscovery",
        requested_workflow_id="ExistingAppDiscovery", journey_id="brownfield_app_adoption",
        websocket_url="/ws/chat_1", trigger_source="transition", routing_explanation="",
        rerouted_by_dependency=False,
    )
    dispatch = AsyncMock(return_value=SimpleNamespace(
        resolution_type="workflow", transition_id="brownfield_repo_input",
        option_id=None, journey_id="brownfield_app_adoption",
        context_variables={"repo_path": "/private/app"}, workflow_launch=workflow_launch,
    ))
    monkeypatch.setattr(transitions, "launch_transition", dispatch)
    response = TestClient(
        studio.app, raise_server_exceptions=False, client=(peer, 12345), base_url="http://localhost",
    ).post(
        "/api/transitions/resolve",
        json={
            "transition_id": "brownfield_repo_input",
            "context_variables": {"repo_path": "/private/app"},
        },
    )

    assert response.status_code == expected_status, response.text
    if expected_status == 403:
        assert "HTTP local source paths require local development access" in response.json()["detail"]
        dispatch.assert_not_awaited()
    else:
        assert response.json()["workflow_id"] == "ExistingAppDiscovery"
        dispatch.assert_awaited_once()


@pytest.mark.parametrize(
    ("route", "payload", "dependency"),
    [
        (
            "/api/chats/app_1/ExistingAppDiscovery/start",
            {"context_variables": {"discovery_inputs": {"repo_path": "/private/app"}}},
            runtime.require_user_scope,
        ),
        (
            "/api/workflows/ExistingAppDiscovery/trigger",
            {"user_id": "alice", "context": {"discovery_inputs": {"repo_path": "/private/app"}}},
            runtime.require_any_auth,
        ),
    ],
)
def test_runtime_http_launch_routes_reject_authenticated_local_source(
    monkeypatch: pytest.MonkeyPatch, route: str, payload: dict, dependency,
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_PROVIDER", "jwt")
    monkeypatch.setenv("AUTH_AUDIENCE", "studio-source-import-test")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    monkeypatch.delenv("INTERNAL_API_KEY", raising=False)
    studio.app.dependency_overrides[dependency] = lambda: _principal("token_validated")
    try:
        response = TestClient(studio.app, raise_server_exceptions=False).post(route, json=payload)
    finally:
        studio.app.dependency_overrides.pop(dependency, None)

    assert response.status_code == 403, response.text
    assert "HTTP local source paths require local development access" in response.json()["detail"]


def test_platform_chat_start_rejects_authenticated_local_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_PROVIDER", "jwt")
    monkeypatch.setenv("AUTH_AUDIENCE", "studio-source-import-test")
    hooks = SimpleNamespace(call_chat_prereqs=AsyncMock(return_value=(True, None)))
    monkeypatch.setattr(platform, "get_platform_hooks", lambda: hooks)
    request = SimpleNamespace(json=AsyncMock(return_value={
        "context_variables": {"discovery_inputs": {"repo_path": "/private/app"}},
    }))

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(platform.start_chat("app_1", "ExistingAppDiscovery", request, _principal("token_validated")))

    assert exc_info.value.status_code == 403
    hooks.call_chat_prereqs.assert_awaited_once()


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
        provenance = "token_validated" if authenticated else "local_development"
        result = asyncio.run(studio.trigger_workflow(body, _principal(provenance)))
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
        provenance = "token_validated" if authenticated else "local_development"
        result = asyncio.run(transitions.resolve_transition_route(body, _principal(provenance)))

    assert result["workflow_id"] == "ExistingAppDiscovery"
    dispatch.assert_awaited_once()
