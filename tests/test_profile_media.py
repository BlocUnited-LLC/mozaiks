from copy import deepcopy
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from mozaiksai.core.auth import UserPrincipal, require_any_auth
from mozaiksai.core.profile.media import normalize_profile_image
from mozaiksai.hosts import platform


def image_bytes():
    output = BytesIO()
    Image.new("RGB", (24, 12), "teal").save(output, "PNG")
    return output.getvalue()


@pytest.fixture
def media_client(monkeypatch, tmp_path):
    from mozaiksai.core.media import store
    monkeypatch.setattr(store, "_content_store", store.LocalMediaContentStore(root=tmp_path))
    docs = {}

    class Collection:
        async def find_one(self, query, *args):
            return deepcopy(docs.get(query.get("_id")))

        async def update_one(self, query, update, upsert=False):
            record = docs.setdefault(query["_id"], {})
            record.update(update.get("$setOnInsert", {}) if not record else {})
            record.update(update.get("$set", {}))

    async def collection():
        return Collection()

    monkeypatch.setattr(platform, "_account_profile_collection", collection)
    app = FastAPI()
    for route in platform.app.routes:
        if getattr(route, "path", "").startswith(("/api/me/media/", "/api/users/{user_id}/media/")):
            app.add_api_route(route.path, route.endpoint, methods=list(route.methods))
    principal = UserPrincipal(user_id="alice", app_id="app-a", email="alice@example.test",
                              name="Alice", roles=[], scopes=[], raw_claims={})
    app.dependency_overrides[require_any_auth] = lambda: principal
    return TestClient(app), principal, docs


@pytest.mark.parametrize("kind", ["avatar", "cover"])
def test_profile_upload_read_replacement_and_app_isolation(media_client, kind):
    client, principal, docs = media_client
    response = client.put(f"/api/me/media/{kind}", content=image_bytes())
    assert response.status_code == 200, response.text
    profile = response.json()
    url = profile[f"{kind}_url"]
    assert f"media_{kind}" not in profile
    image = client.get(url)
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/jpeg"
    assert image.headers["cache-control"] == "private, no-store"
    assert Image.open(BytesIO(image.content)).size == (24, 12)
    principal.app_id = "app-b"
    assert client.get(url).status_code == 404
    principal.app_id = "app-a"
    assert client.get(url.replace(url.rsplit('/', 1)[1], 'wrong')).status_code == 404
    before = deepcopy(docs)
    assert client.put(f"/api/me/media/{kind}", content=b"<svg/>").status_code == 422
    assert docs == before
    stored = next(iter(docs.values()))[f"media_{kind}"]["content_ref"]
    Path(stored).unlink()
    assert client.get(url).status_code == 404


def test_profile_upload_refuses_unknown_kind_and_oversize(media_client):
    client, _, docs = media_client
    assert client.put('/api/me/media/unknown', content=image_bytes()).status_code == 404
    assert client.put('/api/me/media/avatar', content=b'x' * (5 * 1024 * 1024 + 1)).status_code == 413
    assert not docs


def test_image_decoder_refuses_disguised_and_large_images():
    with pytest.raises(ValueError):
        normalize_profile_image(b'not an image', 'avatar')
    output = BytesIO()
    Image.new('RGB', (4001, 4000)).save(output, 'PNG')
    with pytest.raises(ValueError, match='16 million'):
        normalize_profile_image(output.getvalue(), 'cover')
