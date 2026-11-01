"""Asset identity and page references fail before bundle acceptance or serving."""

from __future__ import annotations

import json

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import scan_app_contracts
from factory_app.workflows.AppGenerator.tools.save_app_schema import _validate_asset_manifest
from mozaiksai.core.runtime.app.asset_manifest import (
    asset_manifest_index,
    validate_asset_id,
    validate_asset_url,
)
from mozaiksai.core.runtime.app.page_schema import (
    PageSchemaValidationError,
    load_app_page_schemas,
    validate_page_schema,
)


def _page(*, nested=False):
    image = {"id": "picture", "primitive": "Image", "config": {"asset_id": "hero"}}
    section = {"id": "grid", "primitive": "Grid", "config": {"columns": 2, "children": [image]}} if nested else image
    return {"schema_version": "mozaiks.app_page.v1", "name": "home", "route": "/", "title": "Home",
            "page_type": "landing", "layout": "full-width", "sections": [section]}


def _manifest(*ids):
    return {"version": "1.0", "assets": [
        {"asset_id": asset_id, "kind": "illustration", "source": "generated", "brief": "Brand art"}
        for asset_id in ids
    ]}


@pytest.mark.parametrize("asset_id", ["../escape", "a/b", "a\\b", "", ".", "..", " hero", "hero.",
                                      "CON", "nul.txt", "LPT1", "com9.photo", "x" * 129, "a:b"])
def test_unsafe_ids_are_rejected_by_manifest_and_page_contracts(asset_id):
    with pytest.raises(ValueError, match="asset_id"):
        _validate_asset_manifest(_manifest(asset_id))
    page = _page()
    page["sections"][0]["config"]["asset_id"] = asset_id
    with pytest.raises(PageSchemaValidationError):
        validate_page_schema(page)


@pytest.mark.parametrize("asset_id", ["hero", "Home.hero-1_v2", "1", "x" * 128])
def test_portable_asset_ids_are_preserved(asset_id):
    assert validate_asset_id(asset_id) == asset_id


@pytest.mark.parametrize("ids", [("hero", "hero"), ("hero", "HERO")])
def test_manifest_rejects_duplicate_file_identities(ids):
    with pytest.raises(ValueError, match="unique asset_id"):
        _validate_asset_manifest(_manifest(*ids))


@pytest.mark.parametrize("url", ["javascript:alert(1)", "data:image/png;base64,AA==", "//cdn.example/image.png",
                                "https://user:pass@cdn.example/image.png", "https:///image.png",
                                "https://cdn.example:invalid/image.png", "https://cdn.example/white space.png",
                                "https://cdn.example/control\x7f.png", "https://cdn.example/control\x80.png",
                                "https://cdn.example\\@other.example/image.png"])
def test_unsafe_remote_urls_are_rejected(url):
    with pytest.raises(ValueError, match="HTTP"):
        validate_asset_url(url)
    manifest = _manifest("hero")
    manifest["assets"][0]["url"] = url
    with pytest.raises(ValueError, match="HTTP"):
        asset_manifest_index(manifest)


@pytest.mark.parametrize("nested", [False, True])
def test_bundle_scanner_requires_image_reference_closure(nested):
    files = {"ui/pages/home.yaml": yaml.safe_dump(_page(nested=nested))}
    assert any("page_schema.unknown_asset" in error for error in scan_app_contracts(files))
    files["config/asset_manifest.json"] = json.dumps(_manifest("other"))
    assert any("page_schema.unknown_asset" in error for error in scan_app_contracts(files))
    files["config/asset_manifest.json"] = json.dumps(_manifest("hero"))
    assert scan_app_contracts(files) == []


def test_platform_page_loader_checks_existing_manifest_and_nested_image(tmp_path):
    (tmp_path / "ui/pages").mkdir(parents=True)
    (tmp_path / "ui/pages/home.yaml").write_text(yaml.safe_dump(_page(nested=True)), encoding="utf-8")
    with pytest.raises(PageSchemaValidationError) as error:
        load_app_page_schemas(tmp_path)
    assert error.value.diagnostics[0].code == "page_schema.unknown_asset"
    (tmp_path / "config").mkdir()
    (tmp_path / "config/asset_manifest.json").write_text(json.dumps(_manifest("hero")), encoding="utf-8")
    assert list(load_app_page_schemas(tmp_path)) == ["home"]


def test_bundle_scanner_rejects_duplicate_manifest_even_without_image_pages():
    errors = scan_app_contracts({"config/asset_manifest.json": json.dumps(_manifest("hero", "HERO"))})
    assert any("unique asset_id" in error for error in errors)
