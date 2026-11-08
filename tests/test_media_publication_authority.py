"""Publication is a server-owned visibility decision, never provider metadata."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mozaiksai.core.media import store as media_store
from mozaiksai.core.media.store import LocalMediaContentStore, MediaAssetStore
from mozaiksai.core.media.types import AssetVisibility
from mozaiksai.core.transport import simple_transport
from mozaiksai.hosts.routers import media
from tests.test_media_multimodal import _Client, _Collection


@pytest.fixture
def publication_case(monkeypatch, tmp_path):
    collection = _Collection()
    store = MediaAssetStore(client=_Client(collection), content_store=LocalMediaContentStore(root=tmp_path))
    metadata = {
        "media_type": "image/png", "filename": "draft.png", "provider_detail": "keep",
        "promotion_status": "promoted", "promoted_at": "forged", "promoted_by": "stranger",
        "last_promotion_target": "campaign_asset", "promoted_targets": ["campaign_asset"],
        "asset_visibility": "public", "source_chat_id": "caller-chat", "app_id": "caller-app",
    }
    asset = asyncio.run(store.persist_generated_binary_result(
        SimpleNamespace(data=b"private-draft", metadata=metadata),
        app_id="app-1", source_chat_id="source-chat",
    ))
    monkeypatch.setattr(media, "get_media_asset_store", lambda: store)
    monkeypatch.setattr(media_store, "get_media_asset_store", lambda: store)
    app = FastAPI()
    app.include_router(media.router)
    with TestClient(app) as client:
        yield SimpleNamespace(
            store=store, collection=collection, asset=asset, metadata=metadata,
            client=client, url=f"/api/media/public/app-1/{asset.asset_id}/content",
        )


def test_provider_cannot_authorize_publication(publication_case):
    case = publication_case
    assert case.client.get(case.url).status_code == 403
    assert case.asset.asset_visibility == AssetVisibility.PRIVATE
    assert case.asset.metadata == {
        "media_type": "image/png", "filename": "draft.png", "provider_detail": "keep",
        "source_chat_id": "caller-chat", "app_id": "caller-app",
    }
    assert case.metadata["promotion_status"] == "promoted"  # The provider object is not mutated.


@pytest.mark.parametrize("visibility", [AssetVisibility.PRIVATE, AssetVisibility.INVESTOR_PREVIEW])
def test_legacy_promotion_metadata_cannot_override_private_visibility(publication_case, visibility):
    case = publication_case
    case.collection.docs[0]["metadata"]["promotion_status"] = "promoted"
    case.collection.docs[0]["asset_visibility"] = visibility
    assert case.client.get(case.url).status_code == 403


def test_canonical_promotion_publishes_and_later_visibility_change_revokes(publication_case):
    case = publication_case
    promoted = asyncio.run(case.store.mark_asset_promoted(
        app_id="app-1", asset_id=case.asset.asset_id,
        promotion_target="brand_asset", promoted_by="owner",
    ))
    assert promoted.asset_visibility == AssetVisibility.PUBLIC
    assert promoted.metadata["promotion_status"] == "promoted"
    assert promoted.metadata["promoted_by"] == "owner"
    assert promoted.metadata["promoted_targets"] == ["brand_asset"]
    assert case.client.get(case.url).content == b"private-draft"
    for visibility in (AssetVisibility.PRIVATE, AssetVisibility.INVESTOR_PREVIEW):
        asyncio.run(case.store.update_asset_visibility(app_id="app-1", asset_id=case.asset.asset_id, visibility=visibility))
        assert case.client.get(case.url).status_code == 403


@pytest.mark.parametrize("asset_app,source_chat,allowed", [
    ("app-1", "source-chat", True), ("app-1", "foreign-chat", False),
    ("app-1", None, False), ("app-2", "source-chat", False),
])
@pytest.mark.asyncio
async def test_websocket_promotes_only_its_authenticated_source_chat(monkeypatch, publication_case, asset_app, source_chat, allowed):
    case = publication_case
    document = case.collection.docs[0]
    document["app_id"] = asset_app
    document["source_chat_id"] = source_chat
    before = deepcopy(document)
    update = AsyncMock(wraps=case.collection.update_one)
    monkeypatch.setattr(case.collection, "update_one", update)
    send = AsyncMock()
    monkeypatch.setattr(simple_transport, "send_event_envelope", send)
    transport = simple_transport.SimpleTransport.__new__(simple_transport.SimpleTransport)
    transport.connections = {"source-chat": {"app_id": "app-1", "user_id": "owner"}}
    await transport._handle_artifact_action({"data": {
        "action": "media.promote", "action_id": "review-1",
        "params": {"asset_id": case.asset.asset_id, "promotion_target": "artifact",
                   "app_id": asset_app, "source_chat_id": source_chat, "user_id": "forged"},
    }}, "source-chat", object())
    result = send.await_args_list[-1].args[1]
    assert result["type"] == ("artifact.action.completed" if allowed else "artifact.action.failed")
    assert update.await_args.args[0] == {
        "asset_id": case.asset.asset_id, "app_id": "app-1", "source_chat_id": "source-chat",
    }
    if allowed:
        assert document["asset_visibility"] == AssetVisibility.PUBLIC
        assert document["metadata"]["promoted_by"] == "owner"
        assert case.client.get(case.url).status_code == 200
    else:
        assert document == before
        assert case.client.get(case.url).status_code in {403, 404}


@pytest.mark.asyncio
async def test_expected_source_scope_cannot_be_empty(publication_case):
    case = publication_case
    before = deepcopy(case.collection.docs)
    with pytest.raises(ValueError, match="source_chat_id"):
        await case.store.mark_asset_promoted(
            app_id="app-1", asset_id=case.asset.asset_id, promotion_target="artifact",
            expected_source_chat_id=" ",
        )
    assert case.collection.docs == before
