"""HTTP media permissions with real scoped asset reads and a local byte store.

Only the auth provider and Mongo collections are faked; the auth dependency,
media store, owner query, and response routes run normally.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mozaiksai.core.auth import dependencies as auth
from mozaiksai.core.auth.adapters.base import AuthError, UserClaims
from mozaiksai.core.data.persistence.namespaces import SYSTEM_DATABASE, RuntimeCollections
from mozaiksai.core.media.store import LocalMediaContentStore, MediaAssetStore
from mozaiksai.core.media.types import AssetVisibility
from mozaiksai.hosts.routers import media


class _Collection:
    def __init__(self, docs=()):
        self.docs = list(docs)
        self.create_index = AsyncMock()

    async def insert_one(self, doc):
        self.docs.append(doc)

    async def find_one(self, query, projection=None):
        return next((doc for doc in self.docs if all(doc.get(k) == v for k, v in query.items())), None)


@pytest.fixture
def media_http(monkeypatch, tmp_path):
    from mozaiksai.hosts import runtime

    assets = _Collection()
    chats = _Collection([{"_id": "chat-1", "app_id": "app-1", "user_id": "owner"}])
    store = MediaAssetStore(
        client={SYSTEM_DATABASE: {RuntimeCollections.MEDIA_ASSETS: assets}},
        content_store=LocalMediaContentStore(root=tmp_path),
    )
    asset = asyncio.run(store.persist_generated_binary_result(
        SimpleNamespace(data=b"private-image", metadata={"media_type": "image/png", "filename": "draft.png"}),
        app_id="app-1", source_chat_id="chat-1", source_workflow="ImageWorkflow",
    ))
    read_content = AsyncMock(wraps=store.get_asset_content)
    monkeypatch.setattr(store, "get_asset_content", read_content)
    monkeypatch.setattr(media, "get_media_asset_store", lambda: store)
    chat_collection = AsyncMock(return_value=chats)
    monkeypatch.setattr(runtime, "_chat_coll", chat_collection)
    owner = UserClaims(user_id="owner", app_id="app-1", roles=["user"])
    claims = {
        "owner": owner,
        "stranger": replace(owner, user_id="stranger"),
        "other-app": replace(owner, app_id="app-2"),
        "other-chat": replace(owner, chat_id="chat-2"),
        "bound-owner": replace(owner, chat_id="chat-1"),
        "unbound-owner": replace(owner, app_id=None),
        "no-user": replace(owner, user_id=""),
        "admin": replace(owner, user_id="admin", roles=["admin"]),
        "operator": replace(owner, user_id="operator", roles=["operator"]),
        "backer": replace(owner, user_id="backer", raw_claims={"verified_campaign_backer_assets": [asset.asset_id]}),
    }

    async def validate_token(token):
        if token not in claims:
            raise AuthError("Invalid test token")
        return claims[token]

    monkeypatch.setattr(auth, "is_auth_enabled", lambda: True)
    monkeypatch.setattr(auth, "get_auth_adapter", lambda: SimpleNamespace(name="test", validate_token=validate_token))
    app = FastAPI()
    app.include_router(media.router)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield SimpleNamespace(
            client=client, app=app, assets=assets, chats=chats, asset=asset,
            chat_collection=chat_collection, read_content=read_content,
            url=f"/api/media/assets/app-1/{asset.asset_id}/content",
            public_url=f"/api/media/public/app-1/{asset.asset_id}/content",
        )


def _headers(token="owner"):
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize("token", ["owner", "bound-owner", "unbound-owner"])
@pytest.mark.parametrize("download", [False, True])
def test_authenticated_chat_owner_can_read_without_publishing(media_http, token, download):
    before = deepcopy(media_http.assets.docs)
    response = media_http.client.get(media_http.url, params={"download": download}, headers=_headers(token))
    assert response.status_code == 200
    assert response.content == b"private-image"
    assert response.headers["cache-control"] == "private, max-age=300"
    assert response.headers["content-disposition"].startswith("attachment" if download else "inline")
    assert media_http.assets.docs == before
    assert media_http.client.get(media_http.public_url).status_code == 403
    assert media_http.client.get(media_http.public_url, headers=_headers(token)).status_code == 403


@pytest.mark.parametrize("token", ["stranger", "other-app", "other-chat", "no-user"])
def test_private_media_rejects_foreign_or_missing_principal_scope(media_http, token):
    response = media_http.client.get(media_http.url, headers=_headers(token))
    assert response.status_code == 403
    media_http.read_content.assert_not_awaited()


@pytest.mark.parametrize("change", ["missing", "wrong-app", "no-app", "wrong-owner", "no-owner", "no-source-chat"])
def test_private_media_requires_matching_persisted_chat(media_http, change):
    if change == "missing":
        media_http.chats.docs.clear()
    elif change == "no-source-chat":
        media_http.assets.docs[0]["source_chat_id"] = None
    else:
        field = "app_id" if change.endswith("app") else "user_id"
        if change.startswith("no-"):
            media_http.chats.docs[0].pop(field)
        else:
            media_http.chats.docs[0][field] = "foreign"
    # Neither provider metadata nor query parameters can nominate a different owner/chat.
    media_http.assets.docs[0]["metadata"].update({"user_id": "owner", "source_chat_id": "chat-1", "app_id": "app-1"})
    response = media_http.client.get(media_http.url, headers=_headers(), params={"user_id": "owner", "chat_id": "chat-1"})
    assert response.status_code == 403
    media_http.read_content.assert_not_awaited()


def test_asset_lookup_cannot_cross_app_boundary(media_http):
    media_http.chats.docs.append({"_id": "chat-1", "app_id": "app-2", "user_id": "owner"})
    response = media_http.client.get(media_http.url.replace("app-1", "app-2"), headers=_headers("other-app"))
    assert response.status_code == 404
    media_http.chat_collection.assert_not_awaited()
    media_http.read_content.assert_not_awaited()


@pytest.mark.parametrize("token", ["admin", "operator"])
def test_existing_privileged_private_access_does_not_require_source_chat(media_http, token):
    media_http.chats.docs.clear()
    media_http.assets.docs[0]["source_chat_id"] = None
    assert media_http.client.get(media_http.url, headers=_headers(token)).status_code == 200
    media_http.chat_collection.assert_not_awaited()


def test_missing_or_invalid_token_cannot_read_private_asset(media_http):
    assert media_http.client.get(media_http.url).status_code == 401
    assert media_http.client.get(media_http.url, headers=_headers("invalid")).status_code == 401
    media_http.read_content.assert_not_awaited()


def test_anonymous_visitor_cannot_gain_owner_access(media_http):
    anonymous = auth.UserPrincipal.from_claims(UserClaims(user_id="owner", roles=["user"], app_id="app-1"))
    media_http.app.dependency_overrides[auth.require_any_auth] = lambda: anonymous
    assert media_http.client.get(media_http.url).status_code == 403
    media_http.chat_collection.assert_not_awaited()
    media_http.read_content.assert_not_awaited()


def test_chat_lookup_failure_never_serves_content(media_http):
    media_http.chat_collection.side_effect = RuntimeError("chat store unavailable")
    assert media_http.client.get(media_http.url, headers=_headers()).status_code == 500
    media_http.read_content.assert_not_awaited()


def test_historical_media_read_does_not_resume_or_route_source_session(media_http, monkeypatch):
    from mozaiksai.core.runtime.composition.platform_hooks import get_platform_hooks
    from mozaiksai.core.session import router as session_router

    media_http.chats.docs[0].update(status=1, run_build_binding={"build_id": "historical-build"})
    resume = AsyncMock(side_effect=ValueError("The selected build session has been superseded"))
    route = AsyncMock(side_effect=AssertionError("A media read must not route execution"))
    monkeypatch.setattr(get_platform_hooks(), "call_chat_session_fields", resume)
    monkeypatch.setattr(session_router, "get_session_router_for_chat", route)
    assert media_http.client.get(media_http.url, headers=_headers()).status_code == 200
    resume.assert_not_awaited()
    route.assert_not_awaited()


@pytest.mark.parametrize("token,status", [("owner", 302), ("stranger", 403), ("other-app", 403)])
def test_private_cdn_redirect_checks_ownership_first(media_http, token, status):
    media_http.assets.docs[0]["content_ref"] = "https://cdn.example.test/private.png"
    response = media_http.client.get(media_http.url, headers=_headers(token), follow_redirects=False)
    assert response.status_code == status
    if status == 302:
        assert response.headers["location"] == "https://cdn.example.test/private.png"
    else:
        assert "location" not in response.headers
    media_http.read_content.assert_not_awaited()


@pytest.mark.parametrize("token,status", [("owner", 403), ("backer", 200), ("admin", 200), ("operator", 200)])
def test_investor_preview_keeps_existing_access_policy(media_http, token, status):
    media_http.assets.docs[0]["asset_visibility"] = AssetVisibility.INVESTOR_PREVIEW
    assert media_http.client.get(media_http.url, headers=_headers(token)).status_code == status
    assert media_http.client.get(media_http.public_url).status_code == 403
    media_http.chat_collection.assert_not_awaited()


def test_public_media_keeps_existing_access_policy(media_http):
    media_http.assets.docs[0]["asset_visibility"] = AssetVisibility.PUBLIC
    media_http.chats.docs.clear()
    assert media_http.client.get(media_http.url, headers=_headers("stranger")).status_code == 200
    assert media_http.client.get(media_http.public_url).status_code == 200
    media_http.chat_collection.assert_not_awaited()
