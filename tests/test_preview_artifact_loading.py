import hashlib
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from factory_app.workflows._shared import artifact_bundle
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
async def test_preview_rejects_rootless_refinement_zip_even_with_a_matching_archive_digest(bundle):
    artifact, archive = bundle
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("app.json", '{"appId":"tracker"}')
    artifact.commit_metadata.metadata["bundle_mode"] = "staged_refinement_bundle"
    artifact.files_manifest[0].sha256 = hashlib.sha256(archive.read_bytes()).hexdigest()

    files, diagnostics = await read_artifact_bundle(artifact, include_binary=True)
    assert files == {}
    assert diagnostics == [{"path":"app.json", "code":"outside_bundle_root", "blocking":True}]


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


@pytest.fixture
def pinned_workflow(tmp_path, bundle):
    app, _ = bundle
    app.commit_metadata.author_user_id = "owner"
    app.canonical_inputs_version["workflow_bundle"] = "reviewed-workflow"
    archive = tmp_path / "workflow.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("Suggest/orchestrator.yaml", "workflow_name: Suggest\ntriggers:\n- type: event\n  event: domain.item.created\n  capability_id: Suggest\n")
        output.writestr("Suggest/tools/save_result.py", "# generated workflow tool\n")
    raw = archive.read_bytes()
    record = BuildRecord(
        id="reviewed-workflow", app_id=app.app_id, build_family="workflow_bundle", build_key="reviewed",
        version_number=1, lineage_root_id="reviewed-workflow",
        files_manifest=[{"path": "reviewed/reviewed.zip", "sha256": hashlib.sha256(raw).hexdigest(),
                         "size_bytes": len(raw), "content_type": "application/zip"}],
        commit_metadata={"author_user_id": "owner", "metadata": {
            **app.commit_metadata.metadata, "artifact_path": str(archive), "build_id": "prior-workflow-run",
            "workflow_integration_metadata": {"workflows": [{"workflow_name": "Suggest", "capability_id": "Suggest"}]},
        }},
    )
    return record, archive


@pytest.mark.asyncio
async def test_preview_mounts_pinned_workflow_and_platform_resolves_trigger(studio, monkeypatch, bundle, pinned_workflow, tmp_path):
    from mozaiksai.core.runtime.app.paths import app_bundle_workspace_path
    from mozaiksai.hosts.platform import _load_workflow_capability_routes

    module, _ = studio
    artifact, _ = bundle
    workflow, _ = pinned_workflow
    reader = AsyncMock(side_effect=[artifact, workflow])
    monkeypatch.setattr(module, "get_artifact_store", lambda: SimpleNamespace(get_build_record=reader))
    target, files = await module._resolve_preview_artifact(None, artifact.id, "registry_tracker")
    reader.assert_any_await(app_id=target, build_record_id=workflow.id)
    assert set(files) == {"app.json", "brand/icon.png", "workflows/Inbox/orchestrator.yaml",
                          "workflows/Suggest/orchestrator.yaml", "workflows/Suggest/tools/save_result.py"}
    for path, content in files.items():
        dest = tmp_path / app_bundle_workspace_path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content if isinstance(content, bytes) else content.encode())
    routes = _load_workflow_capability_routes(tmp_path / "app")["Suggest"]
    assert [(row["workflow_id"], row["event_type"]) for row in routes] == [("Suggest", "domain.item.created")]


def test_pinned_workflow_cannot_occupy_workspace_registry(pinned_workflow):
    workflow, _ = pinned_workflow
    with pytest.raises(ValueError, match="reserved"):
        artifact_bundle.add_preview_workflow_files({}, workflow, {"workflows": [{"workflow_name": "extended_orchestration"}]})


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", [
    "missing", "wrong_record", "owner", "app", "registry", "digest", "unlisted_root", "missing_root", "conflict",
    "combined_files", "combined_bytes",
])
async def test_preview_rejects_invalid_pinned_workflow(studio, monkeypatch, bundle, pinned_workflow, damage):
    module, _ = studio
    artifact, app_archive = bundle
    workflow, archive = pinned_workflow
    if damage == "missing":
        workflow = None
    elif damage == "wrong_record":
        workflow.id = "newer-workflow"
    elif damage == "owner":
        workflow.commit_metadata.author_user_id = "another"
    elif damage == "app":
        workflow.app_id = "another"
    elif damage == "registry":
        workflow.commit_metadata.metadata["build_registry_id"] = "another"
    elif damage == "digest":
        workflow.files_manifest[0].sha256 = "a" * 64
    elif damage == "unlisted_root":
        with zipfile.ZipFile(archive, "a") as output:
            output.writestr("Unrelated/orchestrator.yaml", "workflow_name: Unrelated")
        raw = archive.read_bytes()
        workflow.files_manifest[0].sha256 = hashlib.sha256(raw).hexdigest()
        workflow.files_manifest[0].size_bytes = len(raw)
    elif damage == "missing_root":
        workflow.commit_metadata.metadata["workflow_integration_metadata"]["workflows"].append(
            {"workflow_name": "Absent", "capability_id": "Absent"}
        )
    elif damage == "conflict":
        with zipfile.ZipFile(app_archive, "a") as output:
            output.writestr("bundle/workflows/Suggest/tools/unreviewed.py", "extra")
        artifact.files_manifest[0].sha256 = hashlib.sha256(app_archive.read_bytes()).hexdigest()
    elif damage == "combined_files":
        monkeypatch.setattr(artifact_bundle, "APP_BUNDLE_MAX_FILES", 4)
    elif damage == "combined_bytes":
        with zipfile.ZipFile(app_archive, "a", compression=zipfile.ZIP_DEFLATED) as output:
            output.writestr("bundle/extra.txt", "x" * 1000)
        artifact.files_manifest[0].sha256 = hashlib.sha256(app_archive.read_bytes()).hexdigest()
        monkeypatch.setattr(artifact_bundle, "APP_BUNDLE_MAX_TOTAL_BYTES", 1100)
    reader = AsyncMock(side_effect=[artifact, workflow])
    monkeypatch.setattr(module, "get_artifact_store", lambda: SimpleNamespace(get_build_record=reader))
    with pytest.raises(HTTPException) as raised:
        await module._resolve_preview_artifact(None, artifact.id, "registry_tracker")
    assert raised.value.status_code == 422


@pytest.mark.asyncio
async def test_preview_recovery_resolves_registry_without_loading_any_artifact(studio, monkeypatch):
    module, service = studio

    def unavailable_archive():
        pytest.fail("Cleanup recovery must not depend on the selected artifact or archive")

    monkeypatch.setattr(module, "get_artifact_store", unavailable_archive)
    assert await module._resolve_preview_build(None, "registry_tracker") == "tracker"
    service.get_app_record.assert_awaited_once_with(build_registry_id="registry_tracker", owner_user_id="owner")


@pytest.mark.asyncio
@pytest.mark.parametrize("record", [None, {"app_id": "tracker", "chat_app_id": "foreign_host"}])
async def test_preview_recovery_rejects_missing_and_foreign_registry(studio, record):
    module, service = studio
    service.get_app_record.return_value = {"app": record}
    with pytest.raises(HTTPException) as raised:
        await module._resolve_preview_build(None, "registry_tracker")
    assert raised.value.status_code == 404
