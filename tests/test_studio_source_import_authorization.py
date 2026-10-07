from __future__ import annotations

import asyncio

import pytest
from fastapi import BackgroundTasks, HTTPException
from fastapi.testclient import TestClient

from mozaiksai.hosts import studio


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
