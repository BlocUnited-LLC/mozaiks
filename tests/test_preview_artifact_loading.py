import hashlib
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from factory_app.workflows._shared.artifact_bundle import read_artifact_bundle
from mozaiksai.core.artifacts.models import BuildRecord
from tests import test_studio_build_target

studio = test_studio_build_target.studio


@pytest.fixture
def bundle(tmp_path):
    archive = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("bundle/app.json", '{"appId":"tracker","authRequired":false}')
        output.writestr("bundle/brand/icon.png", b"\x89PNG\r\n\x1a\n\x00asset")
        output.writestr("bundle/workflows/Inbox/orchestrator.yaml", "name: Inbox\n")
    artifact = BuildRecord(
        id="version-one", app_id="tracker", build_family="app_bundle", build_key="app_bundle",
        version_number=1, lineage_root_id="version-one",
        files_manifest=[{"path":"bundle/bundle.zip", "sha256":hashlib.sha256(archive.read_bytes()).hexdigest(), "content_type":"application/zip"}],
        commit_metadata={"metadata":{
            "bundle_name":"bundle", "artifact_path":str(archive), "build_registry_id":"registry_tracker",
            "target_app_id":"tracker", "build_id":"build-one", "phase":"genesis",
        }},
    )
    return artifact, archive


@pytest.mark.asyncio
async def test_preview_preserves_binary_assets_without_changing_scanner_input(bundle):
    artifact, _ = bundle
    text, diagnostics = await read_artifact_bundle(artifact)
    assert "brand/icon.png" not in text
    assert diagnostics == [{"path":"brand/icon.png", "code":"binary_asset", "blocking":False}]
    files, diagnostics = await read_artifact_bundle(artifact, include_binary=True)
    assert files["brand/icon.png"] == b"\x89PNG\r\n\x1a\n\x00asset"
    assert isinstance(files["app.json"], str)
    assert diagnostics == []


@pytest.mark.asyncio
async def test_preview_resolver_uses_owned_target_and_verified_archive(studio, monkeypatch, bundle):
    module, _ = studio
    artifact, archive = bundle
    store = SimpleNamespace(get_build_record=AsyncMock(return_value=artifact))
    monkeypatch.setattr(module, "get_artifact_store", lambda: store)
    target, files = await module._resolve_preview_artifact(None, "version-one", "registry_tracker")
    assert target == "tracker" and isinstance(files["brand/icon.png"], bytes)
    store.get_build_record.assert_awaited_once_with(app_id="tracker", build_record_id="version-one")
    archive.write_bytes(b"tampered")
    with pytest.raises(HTTPException) as raised:
        await module._resolve_preview_artifact(None, "version-one", "registry_tracker")
    assert raised.value.status_code == 422


@pytest.mark.asyncio
async def test_preview_rejects_cross_registry_artifact_binding(studio, monkeypatch, bundle):
    module, _ = studio
    artifact, _ = bundle
    artifact.commit_metadata.metadata["build_registry_id"] = "foreign_registry"
    monkeypatch.setattr(module, "get_artifact_store", lambda: SimpleNamespace(get_build_record=AsyncMock(return_value=artifact)))
    with pytest.raises(HTTPException) as raised:
        await module._resolve_preview_artifact(None, "version-one", "registry_tracker")
    assert raised.value.status_code == 409


@pytest.mark.asyncio
async def test_preview_never_launches_a_partially_read_archive(studio, monkeypatch, bundle):
    module, _ = studio
    artifact, archive = bundle
    with zipfile.ZipFile(archive, "a") as output:
        output.writestr("../escape.txt", "invalid")
    artifact.files_manifest[0].sha256 = hashlib.sha256(archive.read_bytes()).hexdigest()
    monkeypatch.setattr(module, "get_artifact_store", lambda: SimpleNamespace(get_build_record=AsyncMock(return_value=artifact)))
    with pytest.raises(HTTPException) as raised:
        await module._resolve_preview_artifact(None, "version-one", "registry_tracker")
    assert raised.value.status_code == 422
