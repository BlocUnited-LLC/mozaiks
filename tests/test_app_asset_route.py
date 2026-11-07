"""App-owned image locations resolve safely through the platform shell route."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mozaiksai.hosts.routers.shell import router


@pytest.fixture
def app_asset_client(monkeypatch, tmp_path):
    app_root = tmp_path / "app"
    (app_root / "config").mkdir(parents=True)
    (app_root / "app.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("PLATFORM_PATH", str(app_root))
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        yield client, app_root


def _manifest(app_root, entry):
    (app_root / "config" / "asset_manifest.json").write_text(
        json.dumps({"version": "1.0", "assets": [{"asset_id": "hero", **entry}]}),
        encoding="utf-8",
    )


@pytest.mark.parametrize("source", ["remote", "stock"])
@pytest.mark.parametrize("scheme", ["http", "https"])
def test_url_only_image_redirects_without_server_fetch(app_asset_client, source, scheme):
    client, app_root = app_asset_client
    url = f"{scheme}://images.example.test/hero.png?size=wide"
    _manifest(app_root, {"source": source, "path": None, "url": url})

    response = client.get("/api/app-assets/hero", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == url
    assert response.headers["cache-control"] == "no-cache"


@pytest.mark.parametrize("url", [
    "javascript:alert(1)", "data:image/svg+xml,<svg/>", "file:///secret.png",
    "//images.example.test/hero.png", "https://user:secret@images.example.test/hero.png",
    "https:///hero.png", "https://images.example.test:invalid/hero.png",
    "https://images.example.test/hero.png\r\nX-Injected: yes",
    "https://images.example.test\\@other.example.test/hero.png",
    None,
])
def test_invalid_image_locations_never_redirect(app_asset_client, url):
    client, app_root = app_asset_client
    _manifest(app_root, {"source": "remote", "path": None, "url": url})

    response = client.get("/api/app-assets/hero", follow_redirects=False)

    assert response.status_code == 404
    assert "location" not in response.headers


def test_bundle_path_takes_precedence_over_remote_url(app_asset_client):
    client, app_root = app_asset_client
    (app_root / "hero.png").write_bytes(b"bundled image")
    _manifest(app_root, {"path": "hero.png", "url": "https://images.example.test/hero.png"})

    response = client.get("/api/app-assets/hero", follow_redirects=False)

    assert response.status_code == 200
    assert response.content == b"bundled image"
    assert "location" not in response.headers


@pytest.mark.parametrize("asset_id", ["-hero", "hero.", "CON.png", "a" * 129])
def test_route_rejects_ids_outside_the_manifest_contract(app_asset_client, asset_id):
    client, _ = app_asset_client
    assert client.get(f"/api/app-assets/{asset_id}").status_code == 400
