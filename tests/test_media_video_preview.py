from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from mozaiksai.core.media.http import media_content_response
from mozaiksai.core.media.types import GeneratedMediaAsset, MediaKind
from mozaiksai.hosts.routers import media as media_router


def _asset() -> GeneratedMediaAsset:
    return GeneratedMediaAsset(
        _id="media_1",
        asset_id="media_1",
        app_id="app_1",
        kind=MediaKind.VIDEO,
        media_type="video/mp4",
        filename="preview.mp4",
        size_bytes=10,
        sha256="0" * 64,
        content_ref="content-ref",
        content_backend="local",
    )


class _AssetStore:
    def __init__(self) -> None:
        self.content_reads = 0

    async def get_asset(self, *, app_id: str, asset_id: str) -> GeneratedMediaAsset:
        assert (app_id, asset_id) == ("app_1", "media_1")
        return _asset()

    async def get_asset_content(self, asset: GeneratedMediaAsset) -> bytes:
        assert asset.asset_id == "media_1"
        self.content_reads += 1
        return b"0123456789"


def test_private_video_route_reads_http_range_header(monkeypatch) -> None:
    store = _AssetStore()
    monkeypatch.setattr(media_router, "get_media_asset_store", lambda: store)
    app = FastAPI()
    app.include_router(media_router.router)
    app.dependency_overrides[media_router.require_any_auth] = lambda: SimpleNamespace(roles=["operator"])

    with TestClient(app) as client:
        response = client.get(
            "/api/media/assets/app_1/media_1/content",
            headers={"Range": "bytes=2-5"},
        )

    assert response.status_code == 206
    assert response.content == b"2345"
    assert response.headers["content-range"] == "bytes 2-5/10"


@pytest.mark.asyncio
async def test_private_video_route_serves_seekable_range_after_authorization(monkeypatch) -> None:
    store = _AssetStore()
    monkeypatch.setattr(media_router, "get_media_asset_store", lambda: store)

    response = await media_router.get_media_asset_content(
        "app_1",
        "media_1",
        download=False,
        range_header="bytes=2-5",
        principal=SimpleNamespace(roles=["operator"]),
    )

    assert response.status_code == 206
    assert response.body == b"2345"
    assert response.headers["content-range"] == "bytes 2-5/10"
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-type"] == "video/mp4"
    assert response.headers["cache-control"].startswith("private")
    assert store.content_reads == 1


@pytest.mark.asyncio
async def test_private_video_route_rejects_non_operator_before_reading_bytes(monkeypatch) -> None:
    store = _AssetStore()
    monkeypatch.setattr(media_router, "get_media_asset_store", lambda: store)

    with pytest.raises(HTTPException) as error:
        await media_router.get_media_asset_content(
            "app_1",
            "media_1",
            download=False,
            range_header="bytes=0-1",
            principal=SimpleNamespace(roles=["member"]),
        )

    assert error.value.status_code == 403
    assert store.content_reads == 0


@pytest.mark.parametrize(
    ("range_header", "body", "content_range"),
    [
        ("bytes=6-", b"6789", "bytes 6-9/10"),
        ("bytes=-3", b"789", "bytes 7-9/10"),
        ("bytes=0-99", b"0123456789", "bytes 0-9/10"),
    ],
)
def test_media_response_supports_browser_byte_ranges(range_header, body, content_range) -> None:
    response = media_content_response(
        b"0123456789",
        media_type="video/mp4",
        filename="preview.mp4",
        range_header=range_header,
    )

    assert response.status_code == 206
    assert response.body == body
    assert response.headers["content-range"] == content_range
    assert response.headers["content-length"] == str(len(body))


@pytest.mark.parametrize(
    "range_header",
    ["bytes=10-", "bytes=4-2", "bytes=-0", "bytes=0-1,4-5", f"bytes={'9' * 21}-"],
)
def test_media_response_rejects_unsatisfiable_or_multiple_ranges(range_header) -> None:
    with pytest.raises(HTTPException) as error:
        media_content_response(
            b"0123456789",
            media_type="video/mp4",
            filename="preview.mp4",
            range_header=range_header,
        )

    assert error.value.status_code == 416
    assert error.value.headers == {"Content-Range": "bytes */10", "Accept-Ranges": "bytes"}


def test_media_response_sanitizes_download_filename() -> None:
    response = media_content_response(
        b"video",
        media_type="video/mp4",
        filename='evil\r\nheader.mp4',
        download=True,
    )

    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="evil-header.mp4"'
