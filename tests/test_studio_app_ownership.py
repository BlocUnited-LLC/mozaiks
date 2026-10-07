from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from mozaiksai.control_plane.app_context import AppContextGraphLookupResult, AppContextSummary
from mozaiksai.core.app_context.refresh import (
    BROWNFIELD_DISCOVERY_REFRESH_SEQUENCE,
    ContextRefreshPlan,
)
from mozaiksai.core.auth.adapters import registry as auth_registry
from mozaiksai.core.auth.adapters.jwt_adapter import GenericJWTAdapter, JWTAdapterConfig
from mozaiksai.core.auth.config import clear_auth_config_cache


@pytest.fixture
def studio_client(monkeypatch):
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_PROVIDER", "jwt")
    monkeypatch.setenv("AUTH_AUDIENCE", "studio-ownership-test")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")

    from mozaiksai.hosts import studio

    signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key()))
    jwk.update({"kid": "studio-ownership-test", "alg": "RS256", "use": "sig"})
    adapter = GenericJWTAdapter(config=JWTAdapterConfig(
        jwks_url="https://auth.test/jwks",
        issuer="https://auth.test",
        audience="studio-ownership-test",
    ))
    monkeypatch.setattr(adapter, "_get_jwks_client_async", AsyncMock(return_value=SimpleNamespace(
        get_signing_key=AsyncMock(return_value=jwk),
    )))
    monkeypatch.setattr("mozaiksai.core.auth.dependencies.get_auth_adapter", lambda: adapter)

    class Registry:
        def __init__(self):
            self.lookups = []

        async def get_app_record(self, *, app_id: str, owner_user_id: str):
            self.lookups.append((app_id, owner_user_id))
            owned = (app_id, owner_user_id) in {
                ("alice-app", "alice"),
                ("bob-app", "bob"),
            }
            record = {"app_id": app_id, "owner_user_id": owner_user_id} if owned else None
            return {"app": record}

    registry = Registry()
    monkeypatch.setattr(studio, "_get_app_registry_service", lambda: registry)

    def headers(user_id: str, app_id: str | None = None) -> dict[str, str]:
        now = datetime.now(UTC)
        claims = {
            "sub": user_id,
            "iss": "https://auth.test",
            "aud": "studio-ownership-test",
            "iat": now,
            "exp": now + timedelta(minutes=5),
        }
        if app_id:
            claims["app_id"] = app_id
        token = jwt.encode(
            claims,
            signing_key,
            algorithm="RS256",
            headers={"kid": "studio-ownership-test", "typ": "at+jwt"},
        )
        return {"Authorization": f"Bearer {token}"}

    return studio, TestClient(studio.app, raise_server_exceptions=False), headers, registry


def _selected_app_routes(app_id: str):
    connector = "/api/studio/integrations/connectors"
    context = f"/api/studio/apps/{app_id}/context"
    query = f"?app_id={app_id}"
    plan = ContextRefreshPlan(
        app_id=app_id,
        target_source_refs=[],
        workflow_sequence=BROWNFIELD_DISCOVERY_REFRESH_SEQUENCE,
        mutation_allowed=False,
    ).model_dump(mode="json")
    return [
        ("GET", "/api/studio/dashboard" + query, None),
        ("GET", "/api/studio/integrations" + query, None),
        ("GET", connector + query, None),
        ("POST", connector + query, {"service": "analytics_provider"}),
        ("PATCH", connector + "/analytics_provider" + query, {"notes": "updated"}),
        ("POST", connector + "/analytics_provider/health-check" + query, None),
        ("DELETE", connector + "/analytics_provider" + query, None),
        ("GET", context, None),
        ("POST", context + "/app-intelligence/index", {
            "source_kind": "git_repository", "repo_url": "https://github.com/example/app",
        }),
        ("POST", context + "/source-import", {
            "source_kind": "git_repository", "repo_url": "https://github.com/example/app",
        }),
        ("GET", context + "/app-intelligence/index/latest", None),
        ("GET", context + "/app-intelligence/index/job-1", None),
        ("POST", context + "/validation/run", {"confirm_execution": True}),
        ("POST", context + "/refresh-plan", {"reason": "refresh"}),
        ("POST", context + "/refresh-launch", {"plan": plan, "confirm_launch": True}),
        ("POST", context + "/refresh-complete", {"plan": plan}),
        ("POST", context + "/override", {
            "request_id": "req-1",
            "original_policy_decision": "block_requires_context_refresh",
            "override_decision": "allow_with_warning",
            "reason": "reviewed",
            "reviewer": "bob",
        }),
    ]


def test_selected_app_routes_require_registry_owner_with_signed_tokens(studio_client, monkeypatch) -> None:
    studio, client, headers, registry = studio_client
    routes = _selected_app_routes("alice-app")
    assert len(routes) == 17
    health_check = AsyncMock()
    monkeypatch.setattr(studio, "run_connector_health_check", health_check)

    for method, path, body in routes:
        other = client.request(method, path, headers=headers("bob"), json=body)
        assert other.status_code == 404, (method, path, other.text)

        bound_to_target = client.request(
            method, path, headers=headers("bob", "alice-app"), json=body,
        )
        assert bound_to_target.status_code == 404, (method, path, bound_to_target.text)

        bound_elsewhere = client.request(
            method, path, headers=headers("bob", "bob-app"), json=body,
        )
        assert bound_elsewhere.status_code == 403, (method, path, bound_elsewhere.text)

    assert registry.lookups == [("alice-app", "bob")] * (2 * len(routes))
    health_check.assert_not_awaited()


def test_owned_app_reaches_every_selected_route_with_signed_token(studio_client, monkeypatch) -> None:
    studio, client, headers, registry = studio_client
    app_id = "alice-app"
    routes = _selected_app_routes(app_id)

    class ConnectorStore:
        SCOPE_APP = "app"

        async def get(self, **kwargs):
            return {"service": kwargs["service"], "health": {}}

        async def upsert(self, **kwargs):
            return {"service": kwargs["service"], "health": {}}

    summary = AppContextSummary(app_id=app_id, available=False)

    def dumped(*args, **kwargs):
        return SimpleNamespace(model_dump=lambda **_: {"app_id": app_id})

    monkeypatch.setattr(studio, "ConnectorStore", ConnectorStore)
    monkeypatch.setattr(studio, "build_integrations_summary", AsyncMock(return_value={"app_id": app_id}))
    monkeypatch.setattr(studio, "list_connectors", AsyncMock(return_value=[]))
    monkeypatch.setattr(studio, "patch_connector", AsyncMock(return_value={"service": "analytics_provider", "health": {}}))
    monkeypatch.setattr(studio, "run_connector_health_check", AsyncMock(return_value={"status": "healthy"}))
    monkeypatch.setattr(studio, "delete_connector", AsyncMock(return_value={"deleted": True}))
    monkeypatch.setattr(studio, "get_artifact_store", lambda: object())
    monkeypatch.setattr(studio, "get_current_app_context_summary", AsyncMock(return_value=summary))
    monkeypatch.setattr(studio, "get_current_app_context_graph", AsyncMock(return_value=AppContextGraphLookupResult(graph=None)))
    monkeypatch.setattr(studio, "get_latest_app_intelligence_index_job", AsyncMock(return_value=None))
    monkeypatch.setattr(studio, "get_app_intelligence_index_job", AsyncMock(return_value=object()))
    monkeypatch.setattr(studio, "public_app_intelligence_index_job", lambda job: {"job_id": "job-1"} if job else None)
    monkeypatch.setattr(studio, "_start_studio_app_intelligence_index_job", AsyncMock(return_value={"app_id": app_id, "accepted": True}))
    monkeypatch.setattr(studio, "run_current_app_source_validation", AsyncMock(side_effect=dumped))
    monkeypatch.setattr(studio, "build_context_refresh_request", dumped)
    monkeypatch.setattr(studio, "build_context_refresh_plan", dumped)
    monkeypatch.setattr(studio, "get_session_router", lambda: object())
    monkeypatch.setattr(studio, "launch_context_refresh_plan", AsyncMock(side_effect=dumped))
    monkeypatch.setattr(studio, "complete_context_refresh", AsyncMock(side_effect=dumped))

    for method, path, body in routes:
        for bound_app_id in (None, app_id):
            response = client.request(method, path, headers=headers("alice", bound_app_id), json=body)
            expected = 202 if path.endswith(("/app-intelligence/index", "/source-import")) else 200
            assert response.status_code == expected, (method, path, bound_app_id, response.text)

    assert registry.lookups == [(app_id, "alice")] * (2 * len(routes))


def test_owned_and_implicit_app_scopes_keep_their_existing_response(studio_client, monkeypatch) -> None:
    studio, client, headers, registry = studio_client
    monkeypatch.setattr(studio, "build_integrations_summary", AsyncMock(return_value={"app_id": "alice-app"}))
    monkeypatch.setattr(studio, "list_connectors", AsyncMock(return_value=[]))

    owned = client.get("/api/studio/integrations?app_id=alice-app", headers=headers("alice"))
    assert owned.status_code == 200
    assert ("alice-app", "alice") in registry.lookups

    default_dashboard = client.get("/api/studio/dashboard", headers=headers("bob"))
    default_integrations = client.get("/api/studio/integrations", headers=headers("bob"))
    default_connectors = client.get("/api/studio/integrations/connectors", headers=headers("bob"))
    assert [response.status_code for response in (
        default_dashboard, default_integrations, default_connectors,
    )] == [200, 200, 200]
    assert default_connectors.json()["connectors"] == []
    assert registry.lookups == [("alice-app", "alice")]

    claimed_without_selector = client.get("/api/studio/dashboard", headers=headers("bob", "alice-app"))
    assert claimed_without_selector.status_code == 404
    assert registry.lookups[-1] == ("alice-app", "bob")


def test_local_development_uses_the_single_developer_as_registry_owner(monkeypatch) -> None:
    from mozaiksai.hosts import studio

    for name in auth_registry._ALL_AUTH_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("AUTH_ANON_ACCESS", "local")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    monkeypatch.setattr(studio.platform_app, "_DEFAULT_PROFILE_USER_ID", "demo-user")
    clear_auth_config_cache()
    auth_registry.reset_auth_adapter()

    class Registry:
        def __init__(self):
            self.lookups = []

        async def get_app_record(self, *, app_id: str, owner_user_id: str):
            self.lookups.append((app_id, owner_user_id))
            record = {"app_id": app_id} if (app_id, owner_user_id) == ("owned-app", "demo-user") else None
            return {"app": record}

    registry = Registry()
    monkeypatch.setattr(studio, "_get_app_registry_service", lambda: registry)
    client = TestClient(
        studio.app, raise_server_exceptions=False,
        client=("127.0.0.1", 50000), base_url="http://localhost:8000",
    )
    try:
        owned = client.get("/api/studio/dashboard?app_id=owned-app")
        foreign = client.get("/api/studio/dashboard?app_id=foreign-app")
        assert (owned.status_code, foreign.status_code) == (200, 404)
        assert registry.lookups == [("owned-app", "demo-user"), ("foreign-app", "demo-user")]
    finally:
        clear_auth_config_cache()
        auth_registry.reset_auth_adapter()
