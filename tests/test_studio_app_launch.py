import hashlib
import io
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from mozaiksai.core.artifacts import BuildRecord
from mozaiksai.hosts import studio


@pytest.fixture
def selected_app(monkeypatch, tmp_path):
    archive = tmp_path / "Notes.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("Notes/app.json", '{"appId":"notes"}')
        bundle.writestr("Notes/config/shell.json", "{}")
    content = archive.read_bytes()
    record = {
        "build_registry_id": "reg_notes", "app_id": "notes", "chat_app_id": "factory",
        "current_build_run": {"build_id": "build_1", "artifact_version_id": "version_1"},
    }
    version = BuildRecord(
        _id="version_1", app_id="notes", build_family="app_bundle", build_key="app_bundle",
        version_number=1, lineage_root_id="version_1", validation_status="passed", app_validation_status="passed",
        files_manifest=[{"path": "Notes/Notes.zip", "sha256": hashlib.sha256(content).hexdigest(),
                         "size_bytes": len(content), "content_type": "application/zip"}],
        commit_metadata={"metadata": {
            "target_app_id": "notes", "build_registry_id": "reg_notes", "build_id": "build_1", "phase": "genesis",
            "bundle_name": "Notes", "artifact_path": str(archive),
        }},
    )
    monkeypatch.setattr(studio, "_dashboard_host_scope", lambda principal: ("factory", principal))
    monkeypatch.setattr(studio.app.state, "list_studio_app_bindings", None, raising=False)
    monkeypatch.setattr(studio.app.state, "resolve_studio_dashboard_app_id", None, raising=False)

    async def get_app_record(*, app_id, owner_user_id):
        return {"app": record if (app_id, owner_user_id) == ("notes", "owner") else None}

    monkeypatch.setattr(studio, "_get_app_registry_service", lambda: SimpleNamespace(get_app_record=get_app_record))
    read = AsyncMock(return_value=version)
    monkeypatch.setattr(studio, "get_artifact_store", lambda: SimpleNamespace(get_build_record=read))
    return record, version, archive, read


@pytest.mark.asyncio
async def test_owner_downloads_exact_appgenerator_archive(selected_app):
    _, _, archive, read = selected_app
    result = await studio.get_dashboard_app_launch("notes", principal="owner")
    assert result == {"status": "ready", "artifact_version_id": "version_1", "filename": "notes-version_1.zip"}
    response = await studio.download_dashboard_app_bundle("notes", result["artifact_version_id"], principal="owner")
    assert response.headers["Content-Disposition"] == 'attachment; filename="notes-version_1.zip"'
    assert response.body == archive.read_bytes()
    with zipfile.ZipFile(io.BytesIO(response.body)) as bundle:
        assert bundle.read("Notes/app.json") == b'{"appId":"notes"}'
    read.assert_awaited_with(app_id="notes", build_record_id="version_1")


@pytest.mark.asyncio
async def test_launch_authorizes_metadata_and_rechecks_owner_on_download(selected_app):
    *_, read = selected_app
    for app_id, actor in [("notes", "second_owner"), ("other_app", "owner")]:
        with pytest.raises(HTTPException) as summary:
            await studio.get_dashboard_app_launch(app_id, principal=actor)
        with pytest.raises(HTTPException) as download:
            await studio.download_dashboard_app_bundle(app_id, "version_1", principal=actor)
        assert summary.value.status_code == download.value.status_code == 404
    read.assert_not_awaited()


@pytest.mark.asyncio
async def test_previous_artifact_is_unavailable_during_a_new_build(selected_app):
    record, _, _, read = selected_app
    record.update(artifact_version_id="version_1", current_build_run={"build_id": "new_run"})
    assert await studio.get_dashboard_app_launch("notes", principal="owner") == {"status": "no_build"}
    with pytest.raises(HTTPException) as download:
        await studio.download_dashboard_app_bundle("notes", "version_1", principal="owner")
    assert download.value.status_code == 409
    read.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["registry", "run", "foreign_target", "import", "retired", "family"])
async def test_launch_rejects_noncurrent_or_unrelated_artifact(selected_app, changed):
    record, version, _, _ = selected_app
    if changed == "registry":
        record["build_registry_id"] = "other_registry"
    elif changed == "run":
        record["current_build_run"]["build_id"] = "later_build"
    elif changed == "foreign_target":
        version.commit_metadata.metadata["target_app_id"] = "other_app"
    elif changed == "import":
        version.commit_metadata.metadata["bundle_mode"] = "brownfield_genesis_import"
    elif changed == "retired":
        version.lifecycle_status = "archived"
    else:
        version.build_family = "workflow_bundle"
    with pytest.raises(HTTPException) as download:
        await studio.download_dashboard_app_bundle("notes", "version_1", principal="owner")
    assert download.value.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize(("validation", "whole_app"), [("failed", "passed"), ("passed", "skipped")])
async def test_launch_requires_both_validations(selected_app, validation, whole_app):
    _, version, _, _ = selected_app
    version.validation_status = validation
    version.app_validation_status = whole_app
    assert await studio.get_dashboard_app_launch("notes", principal="owner") == {"status": "validation_required"}
    with pytest.raises(HTTPException) as download:
        await studio.download_dashboard_app_bundle("notes", "version_1", principal="owner")
    assert download.value.status_code == 409


@pytest.mark.asyncio
async def test_download_checks_displayed_version_and_exact_content(selected_app):
    _, _, archive, _ = selected_app
    with pytest.raises(HTTPException) as changed:
        await studio.download_dashboard_app_bundle("notes", "old_version", principal="owner")
    assert changed.value.status_code == 409
    archive.write_bytes(b"replaced")
    with pytest.raises(HTTPException) as corrupt:
        await studio.download_dashboard_app_bundle("notes", "version_1", principal="owner")
    assert corrupt.value.status_code == 409


@pytest.mark.asyncio
async def test_launch_exports_archives_above_the_default_review_read_limit(selected_app):
    _, version, archive, _ = selected_app
    with zipfile.ZipFile(archive, "a", compression=zipfile.ZIP_STORED) as bundle:
        for index in range(5):
            bundle.writestr(f"Notes/assets/image-{index}.png", bytes(6_500_000))
    entry = version.files_manifest[0]
    entry.size_bytes = archive.stat().st_size
    with archive.open("rb") as source:
        entry.sha256 = hashlib.file_digest(source, "sha256").hexdigest()
    assert 32_000_000 < entry.size_bytes < studio.APP_BUNDLE_MAX_TOTAL_BYTES
    assert (await studio.get_dashboard_app_launch("notes", principal="owner"))["status"] == "ready"
    response = await studio.download_dashboard_app_bundle("notes", "version_1", principal="owner")
    assert hashlib.sha256(response.body).hexdigest() == entry.sha256
    with pytest.raises(HTTPException) as ordinary_review:
        await studio._verified_app_bundle(version)
    assert ordinary_review.value.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["no_digest", "oversize", "no_content", "ambiguous_reference"])
async def test_launch_does_not_advertise_a_known_unexportable_archive(selected_app, defect):
    _, version, _, _ = selected_app
    if defect == "no_digest":
        version.files_manifest[0].sha256 = None
    elif defect == "oversize":
        version.files_manifest[0].size_bytes = studio.APP_BUNDLE_MAX_TOTAL_BYTES + 1
    elif defect == "no_content":
        del version.commit_metadata.metadata["artifact_path"]
    else:
        version.commit_metadata.metadata.update(content_digest="a" * 64, content_ref="other")
    with pytest.raises(HTTPException) as unavailable:
        await studio.get_dashboard_app_launch("notes", principal="owner")
    with pytest.raises(HTTPException) as download:
        await studio.download_dashboard_app_bundle("notes", "version_1", principal="owner")
    assert unavailable.value.status_code == download.value.status_code == 409
