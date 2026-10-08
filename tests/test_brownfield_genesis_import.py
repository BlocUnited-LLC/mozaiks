from __future__ import annotations

import copy
import hashlib
import io
import json
import stat
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from factory_app.refinement_harness.tools._artifact_workspace import load_artifact_workspace
from factory_app.refinement_harness.tools._bundle_workspace import load_bundle_workspace
from factory_app.workflows._shared.platform.genesis_import import (
    GenesisImportError,
    PinnedSourceProvenance,
    accept_existing_app_genesis,
    import_existing_app_genesis_draft,
    require_accepted_genesis_baseline,
)
from mozaiksai.core.artifacts import content_store as artifact_content_store
from mozaiksai.core.artifacts.content_store import (
    ContentIntegrityError,
    LocalArtifactContentStore,
    read_verified_artifact_bundle,
)
from mozaiksai.core.artifacts.models import (
    BuildRecord,
    BuildRecordStatus,
    BuildRecordValidationStatus,
)

_BUNDLE = "ExistingApp"
_PROVENANCE = PinnedSourceProvenance(
    source_id="managed/app-zero", revision_id="a" * 40, tree_id="b" * 40,
)


def _source(*, app_id: str = "mozaiks-platform", files: dict[str, bytes] | None = None,
            member_override: dict[str, zipfile.ZipInfo] | None = None):
    files = files or {
        "app.json": json.dumps({"appId": app_id, "appName": "Mozaiks"}).encode(),
        "modules/example/backend/handler.py": b"def handle():\n    return 1\n",
        "ui/public/logo.png": b"\x89PNG\r\n\x1a\n\x00\x01",
    }
    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, data in files.items():
            member = (member_override or {}).get(path, f"{_BUNDLE}/{path}")
            archive.writestr(member, data)
    raw = archive_bytes.getvalue()
    manifest = [{
        "path": f"{_BUNDLE}/{_BUNDLE}.zip", "sha256": hashlib.sha256(raw).hexdigest(),
        "size_bytes": len(raw), "content_type": "application/zip",
    }]
    manifest.extend({
        "path": f"{_BUNDLE}/{path}", "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data), "content_type": "application/json" if path == "app.json" else "application/octet-stream",
    } for path, data in files.items())
    return raw, manifest


def _source_content_digest(manifest: list[dict[str, object]]) -> str:
    digests = {
        str(entry["path"]).removeprefix(f"{_BUNDLE}/"): entry["sha256"]
        for entry in manifest if entry["path"] != f"{_BUNDLE}/{_BUNDLE}.zip"
    }
    return hashlib.sha256(json.dumps(digests, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _successful_gate_result(manifest: list[dict[str, object]]) -> dict[str, object]:
    return {
        "status": "passed", "passed": True, "snapshot_digest": "e" * 64,
        "checks": [
            {"id": "app_runtime_load", "status": "passed", "passed": True},
            {"id": "app_runtime_smoke", "status": "passed", "passed": True},
        ],
        "app_runtime_load": {"status": "passed", "passed": True},
        "app_runtime_smoke": {
            "status": "passed", "passed": True,
            "results": [{"check": "boot.http_ready", "status": "passed"}],
            "checks": [{"id": "app_runtime_smoke", "status": "passed", "passed": True}],
            "validator_image_id": "sha256:" + "a" * 64,
            "source_content_sha256": _source_content_digest(manifest),
            "observer_origin": "trusted_external_probe_v1",
            "observer_run_id": "b" * 32,
            "observed_boot": {"check": "boot.http_ready", "status": "passed"},
            "observer_unverified_checks": [],
        },
    }


@pytest.fixture
def trusted_observer_test_override(monkeypatch):
    from factory_app.workflows._shared.platform import genesis_import

    preflight = AsyncMock(return_value="sha256:" + "a" * 64)
    monkeypatch.setattr(genesis_import, "_preflight_imported_smoke", preflight)
    return preflight


@pytest.fixture
def import_state(tmp_path, monkeypatch):
    registry_row = {
        "build_registry_id": "appreg_1", "owner_user_id": "owner_1",
        "app_id": "mozaiks-platform", "chat_app_id": "mozaiks-platform",
        "lifecycle_state": "draft", "artifact_version_id": None,
        "current_build_run": None,
    }
    async def reserve_genesis_import(*, build_registry_id, owner_user_id, app_id, chat_app_id, claim):
        if (registry_row["build_registry_id"] != build_registry_id
                or registry_row["owner_user_id"] != owner_user_id
                or registry_row["app_id"] != app_id
                or registry_row["chat_app_id"] != chat_app_id
                or registry_row["lifecycle_state"] != "draft"
                or registry_row.get("current_build_run") is not None):
            return None
        claim_doc = claim.model_dump(mode="json")
        existing = registry_row.get("genesis_import")
        if existing is not None and existing != claim_doc:
            return None
        registry_row["genesis_import"] = claim_doc
        return dict(registry_row)

    async def accept_genesis_import(*, build_registry_id, owner_user_id, app_id, chat_app_id, claim, receipt):
        if (registry_row["build_registry_id"] != build_registry_id
                or registry_row["owner_user_id"] != owner_user_id
                or registry_row["app_id"] != app_id
                or registry_row["chat_app_id"] != chat_app_id):
            return None
        claim_doc = claim.model_dump(mode="json")
        state = registry_row.get("genesis_import")
        if state == claim_doc:
            registry_row["genesis_import"] = {
                **claim_doc, "status": "accepted", "acceptance": receipt.model_dump(mode="python"),
            }
        elif (state is None or state.get("status") != "accepted"
              or state.get("acceptance", {}).get("validation_sha256") != receipt.validation_sha256):
            return None
        return dict(registry_row)

    registry = SimpleNamespace(
        get_app_record=AsyncMock(side_effect=lambda **_kwargs: {"app": dict(registry_row)}),
        reserve_genesis_import=AsyncMock(side_effect=reserve_genesis_import),
        accept_genesis_import=AsyncMock(side_effect=accept_genesis_import),
    )
    records: dict[str, BuildRecord] = {}

    async def get_build_record(*, app_id, build_record_id):
        record = records.get(build_record_id)
        return record if record is not None and record.app_id == app_id else None

    async def list_build_records(**_kwargs):
        return list(records.values())

    async def create_build_record(**kwargs):
        record = BuildRecord(
            _id=kwargs["build_record_id"], app_id=kwargs["app_id"],
            build_family=kwargs["build_family"], build_key=kwargs["build_key"],
            version_number=1, lineage_root_id=kwargs["build_record_id"],
            parent_build_record_id=kwargs["parent_build_record_id"],
            files_manifest=kwargs["files_manifest"],
            lifecycle_status=kwargs["lifecycle_status"],
            validation_status=kwargs["validation_status"],
            commit_metadata=kwargs["commit_metadata"],
        )
        records[record.id] = record
        return record

    async def mark_genesis_build_record_validated(*, app_id, build_record_id, validation):
        record = await get_build_record(app_id=app_id, build_record_id=build_record_id)
        if record is None or record.lifecycle_status != BuildRecordStatus.DRAFT:
            return record
        record.commit_metadata.metadata["genesis_validation"] = validation
        record.validation_status = BuildRecordValidationStatus.PASSED
        record.app_validation_status = "passed"
        return record

    async def accept_genesis_build_record(*, app_id, build_record_id, validation_sha256):
        record = await get_build_record(app_id=app_id, build_record_id=build_record_id)
        if (record is not None and record.lifecycle_status == BuildRecordStatus.DRAFT
                and record.commit_metadata.metadata.get("genesis_validation", {}).get("sha256") == validation_sha256):
            record.lifecycle_status = BuildRecordStatus.CURRENT
        return record

    store = SimpleNamespace(
        get_build_record=AsyncMock(side_effect=get_build_record),
        list_build_records=AsyncMock(side_effect=list_build_records),
        create_build_record=AsyncMock(side_effect=create_build_record),
        mark_genesis_build_record_validated=AsyncMock(side_effect=mark_genesis_build_record_validated),
        accept_genesis_build_record=AsyncMock(side_effect=accept_genesis_build_record),
    )
    content = LocalArtifactContentStore(tmp_path)
    monkeypatch.setattr(artifact_content_store, "get_artifact_content_store", lambda: content)
    return registry_row, registry, store, content


async def _import(state, raw, manifest, *, provenance=_PROVENANCE):
    _row, registry, store, content = state
    return await import_existing_app_genesis_draft(
        owner_user_id="owner_1", execution_app_id="mozaiks-platform",
        build_registry_id="appreg_1", bundle_name=_BUNDLE,
        bundle_bytes=raw, files_manifest=manifest, source_provenance=provenance,
        registry_service=registry, record_store=store, content_store=content,
    )


@pytest.mark.asyncio
async def test_exact_existing_source_is_draft_pending_and_idempotent(import_state):
    raw, manifest = _source()
    first = await _import(import_state, raw, manifest)
    second = await _import(import_state, raw, list(reversed(manifest)))
    assert first is second
    assert first.lifecycle_status == BuildRecordStatus.DRAFT
    assert first.validation_status == BuildRecordValidationStatus.PENDING
    assert first.parent_build_record_id is None
    assert first.source_workflow is None
    assert first.app_id == "mozaiks-platform"
    assert first.commit_metadata.metadata["build_registry_id"] == "appreg_1"
    assert first.commit_metadata.metadata["brownfield_genesis"] == _PROVENANCE.model_dump()
    assert first.commit_metadata.metadata["content_digest"] == hashlib.sha256(raw).hexdigest()
    assert first.commit_metadata.metadata["genesis_import_claim_sha256"] == hashlib.sha256(
        json.dumps(import_state[0]["genesis_import"], sort_keys=True, separators=(",", ":")).encode(),
    ).hexdigest()
    assert await read_verified_artifact_bundle(first) == raw
    assert import_state[2].create_build_record.await_count == 1
    assert import_state[0]["artifact_version_id"] is None
    assert import_state[0]["current_build_run"] is None
    assert import_state[0]["genesis_import"]["status"] == "reserved"
    assert import_state[0]["genesis_import"]["build_record_id"] == first.id
    assert import_state[1].reserve_genesis_import.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("owner_user_id", "other-owner"), ("chat_app_id", "other-host"),
    ("lifecycle_state", "active"), ("artifact_version_id", "av_current"),
    ("bundle_path", "generated/apps/old/app"),
    ("current_build_run", {"build_id": "build_1"}),
])
async def test_wrong_or_active_factory_target_cannot_receive_import(import_state, field, value):
    import_state[0][field] = value
    raw, manifest = _source()
    with pytest.raises(GenesisImportError, match="Factory target"):
        await _import(import_state, raw, manifest)
    import_state[2].create_build_record.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_app_zero_target_id_is_not_a_source_baseline(import_state):
    import_state[0]["app_id"] = "mozaiks-platform-app-zero"
    raw, manifest = _source(app_id="mozaiks-platform")
    with pytest.raises(GenesisImportError, match="root app.json does not match"):
        await _import(import_state, raw, manifest)
    import_state[2].create_build_record.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["missing", "extra", "digest", "size", "archive_digest"])
async def test_incomplete_or_changed_manifest_creates_no_record(import_state, fault):
    raw, manifest = _source()
    if fault == "missing":
        manifest.pop()
    elif fault == "extra":
        manifest.append({"path": f"{_BUNDLE}/never.py", "sha256": "0" * 64,
                         "size_bytes": 1, "content_type": "text/x-python"})
    elif fault == "digest":
        manifest[1]["sha256"] = "0" * 64
    elif fault == "size":
        manifest[1]["size_bytes"] += 1
    else:
        manifest[0]["sha256"] = "0" * 64
    with pytest.raises(GenesisImportError):
        await _import(import_state, raw, manifest)
    import_state[2].create_build_record.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("member", [
    "../escape.py", "/absolute.py", "C:/drive.py", "Other/file.py",
    f"{_BUNDLE}/ui/CON.txt", f"{_BUNDLE}/ui/secret:stream",
    f"{_BUNDLE}/ui/trailing.",
])
async def test_unsafe_or_outside_archive_member_creates_no_record(import_state, member):
    raw, manifest = _source(member_override={"modules/example/backend/handler.py": member})
    with pytest.raises(GenesisImportError):
        await _import(import_state, raw, manifest)
    import_state[2].create_build_record.assert_not_awaited()


@pytest.mark.asyncio
async def test_symlink_archive_member_creates_no_record(import_state):
    link = zipfile.ZipInfo(f"{_BUNDLE}/modules/example/backend/handler.py")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    raw, manifest = _source(member_override={"modules/example/backend/handler.py": link})
    with pytest.raises(GenesisImportError, match="unsafe"):
        await _import(import_state, raw, manifest)
    import_state[2].create_build_record.assert_not_awaited()


@pytest.mark.asyncio
async def test_unsupported_binary_source_creates_no_record(import_state):
    raw, manifest = _source(files={
        "app.json": b'{"appId":"mozaiks-platform","appName":"Mozaiks"}',
        "opaque.bin": b"\x00\xff",
    })
    with pytest.raises(GenesisImportError, match="unsupported or incomplete"):
        await _import(import_state, raw, manifest)
    import_state[2].create_build_record.assert_not_awaited()


@pytest.mark.asyncio
async def test_same_pinned_revision_with_changed_bytes_cannot_create_second_draft(import_state):
    raw, manifest = _source()
    await _import(import_state, raw, manifest)
    changed, changed_manifest = _source(files={
        "app.json": b'{"appId":"mozaiks-platform","appName":"Changed"}',
    })
    with pytest.raises(GenesisImportError, match="different facts"):
        await _import(import_state, changed, changed_manifest)
    assert import_state[2].create_build_record.await_count == 1


@pytest.mark.asyncio
async def test_different_revision_cannot_create_second_genesis_draft(import_state):
    raw, manifest = _source()
    await _import(import_state, raw, manifest)
    different = _PROVENANCE.model_copy(update={"revision_id": "c" * 40})
    with pytest.raises(GenesisImportError, match="different facts"):
        await _import(import_state, raw, manifest, provenance=different)
    assert import_state[2].create_build_record.await_count == 1


@pytest.mark.asyncio
async def test_conflicting_registry_reservation_stops_before_blob_or_record(import_state, monkeypatch):
    raw, manifest = _source()
    import_state[0]["genesis_import"] = {"status": "reserved", "build_record_id": "av_other"}
    write_blob = AsyncMock()
    monkeypatch.setattr(import_state[3], "put_blob", write_blob)
    with pytest.raises(GenesisImportError, match="reserved another Genesis source"):
        await _import(import_state, raw, manifest)
    write_blob.assert_not_awaited()
    import_state[2].create_build_record.assert_not_awaited()


@pytest.mark.asyncio
async def test_registry_change_after_blob_write_stops_before_record(import_state, monkeypatch):
    raw, manifest = _source()
    content = import_state[3]
    original_put_blob = content.put_blob

    async def put_then_supersede(data, *, expected_digest):
        digest = await original_put_blob(data, expected_digest=expected_digest)
        import_state[0]["lifecycle_state"] = "building"
        return digest

    monkeypatch.setattr(content, "put_blob", put_then_supersede)
    with pytest.raises(GenesisImportError, match="changed during source import"):
        await _import(import_state, raw, manifest)
    import_state[2].create_build_record.assert_not_awaited()


@pytest.mark.asyncio
async def test_corrupt_immutable_source_blob_cannot_be_read_as_baseline(import_state, tmp_path):
    raw, manifest = _source()
    draft = await _import(import_state, raw, manifest)
    digest = draft.commit_metadata.metadata["content_digest"]
    (tmp_path / "sha256" / digest[:2] / digest).write_bytes(b"changed")
    with pytest.raises(ContentIntegrityError):
        await read_verified_artifact_bundle(draft)


@pytest.mark.asyncio
async def test_ambiguous_content_authority_cannot_be_read_as_baseline(import_state):
    raw, manifest = _source()
    draft = await _import(import_state, raw, manifest)
    draft.commit_metadata.metadata["content_ref"] = "untrusted-path"
    with pytest.raises(ContentIntegrityError, match="authority_ambiguous"):
        await read_verified_artifact_bundle(draft)


@pytest.mark.asyncio
async def test_harness_workspaces_read_verified_digest_before_mutable_workspace(import_state, tmp_path):
    raw, manifest = _source()
    draft = await _import(import_state, raw, manifest)
    mutable = tmp_path / "mutable"
    mutable.mkdir()
    (mutable / "app.json").write_text('{"appId":"changed"}', encoding="utf-8")
    draft.commit_metadata.metadata["workspace_dir"] = str(mutable)

    artifact_workspace = await load_artifact_workspace(
        artifact_store=import_state[2], app_id=draft.app_id, build_record_id=draft.id,
    )
    bundle_workspace = await load_bundle_workspace(
        record_store=import_state[2], app_id=draft.app_id, build_record_id=draft.id,
    )
    for workspace in (artifact_workspace, bundle_workspace):
        assert workspace["present"] is True
        assert workspace["source"] == "content_digest:local"
        assert json.loads(workspace["file_map"]["app.json"])["appId"] == "mozaiks-platform"


@pytest.mark.asyncio
async def test_harness_workspaces_fail_closed_on_corrupt_digest(import_state, tmp_path):
    raw, manifest = _source()
    draft = await _import(import_state, raw, manifest)
    digest = draft.commit_metadata.metadata["content_digest"]
    (tmp_path / "sha256" / digest[:2] / digest).write_bytes(b"changed")
    artifact_workspace = await load_artifact_workspace(
        artifact_store=import_state[2], app_id=draft.app_id, build_record_id=draft.id,
    )
    bundle_workspace = await load_bundle_workspace(
        record_store=import_state[2], app_id=draft.app_id, build_record_id=draft.id,
    )
    assert artifact_workspace["reason"] == "content_digest_unavailable_or_invalid"
    assert bundle_workspace["reason"] == "content_digest_unavailable_or_invalid"


@pytest.mark.asyncio
async def test_owner_can_download_reserved_exact_genesis_for_review(import_state, monkeypatch, tmp_path):
    from mozaiksai.core.auth import reset_auth_adapter
    from mozaiksai.hosts import studio

    raw, manifest = _source()
    draft = await _import(import_state, raw, manifest)
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    reset_auth_adapter()
    monkeypatch.setattr(studio, "_resolve_studio_scope", lambda *_args, **_kwargs: (
        "mozaiks-platform", "owner_1",
    ))
    monkeypatch.setattr(studio, "_get_app_registry_service", lambda: import_state[1])
    monkeypatch.setattr(studio, "get_artifact_store", lambda: import_state[2])
    path = f"/api/studio/build/artifacts/{draft.id}/download?build_registry_id=appreg_1"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=studio.app), base_url="http://test") as client:
        response = await client.get(path)
        assert response.status_code == 200, response.text
        assert response.content == raw
        assert response.headers["content-type"] == "application/zip"
        import_state[0]["owner_user_id"] = "other_owner"
        assert (await client.get(path)).status_code == 409
        import_state[0]["owner_user_id"] = "owner_1"
        digest = draft.commit_metadata.metadata["content_digest"]
        (tmp_path / "sha256" / digest[:2] / digest).write_bytes(b"changed")
        assert (await client.get(path)).status_code == 409


@pytest.mark.asyncio
async def test_duplicate_archive_member_is_rejected_before_persistence(import_state):
    with pytest.warns(UserWarning, match="Duplicate name"):
        raw, manifest = _source(member_override={
            "modules/example/backend/handler.py": f"{_BUNDLE}/app.json",
        })
    with pytest.raises(GenesisImportError, match="duplicate member"):
        await _import(import_state, raw, manifest)
    import_state[2].create_build_record.assert_not_awaited()


@pytest.mark.asyncio
async def test_file_directory_collision_is_rejected_before_persistence(import_state):
    raw, manifest = _source(files={
        "app.json": b'{"appId":"mozaiks-platform","appName":"Mozaiks"}',
        "data": b"not a directory",
        "data/contract.json": b"{}",
    })
    with pytest.raises(GenesisImportError, match="colliding paths"):
        await _import(import_state, raw, manifest)
    import_state[2].create_build_record.assert_not_awaited()


async def _accept(state, record, *, bundle_sha256=None, manifest_sha256=None):
    row, registry, store, _content = state
    claim = row["genesis_import"]
    return await accept_existing_app_genesis(
        owner_user_id="owner_1", execution_app_id="mozaiks-platform",
        build_registry_id="appreg_1", build_record_id=record.id,
        reviewed_bundle_sha256=bundle_sha256 or claim["bundle_sha256"],
        reviewed_manifest_sha256=manifest_sha256 or claim["manifest_sha256"],
        registry_service=registry, record_store=store,
    )


@pytest.mark.asyncio
async def test_owner_review_accepts_exact_validated_genesis_without_deployment(
    import_state, monkeypatch, trusted_observer_test_override,
):
    from factory_app.workflows.AppGenerator.tools import app_validation

    monkeypatch.setattr(app_validation.app_runtime_smoke, "run_contained_imported_app_runtime_smoke", AsyncMock(), raising=False)
    raw, manifest = _source()
    draft = await _import(import_state, raw, manifest)
    gate = AsyncMock(return_value=_successful_gate_result(manifest))
    monkeypatch.setattr(app_validation, "run_app_bundle_acceptance_gate", gate)
    with pytest.raises(GenesisImportError, match="no matching accepted review receipt"):
        await require_accepted_genesis_baseline(
            draft, owner_user_id="owner_1", execution_app_id="mozaiks-platform",
            build_registry_id="appreg_1", registry_service=import_state[1],
        )
    accepted = await _accept(import_state, draft)
    assert gate.await_args.kwargs["contained_imported_source"] is True
    assert accepted.lifecycle_status == BuildRecordStatus.CURRENT
    assert accepted.validation_status == BuildRecordValidationStatus.PASSED
    assert accepted.app_validation_status == "passed"
    assert accepted.commit_metadata.metadata["genesis_validation"]["validator_image_id"] == "sha256:" + "a" * 64
    assert accepted.commit_metadata.metadata["genesis_validation"]["source_content_sha256"] == _source_content_digest(manifest)
    assert accepted.commit_metadata.metadata["genesis_validation"]["observed_boot"] == {
        "check": "boot.http_ready", "status": "passed",
    }
    assert accepted.commit_metadata.metadata["genesis_validation"]["observer_origin"] == "trusted_external_probe_v1"
    assert accepted.commit_metadata.metadata["genesis_validation"]["observer_run_id"] == "b" * 32
    assert accepted.commit_metadata.metadata["genesis_validation"]["observer_unverified_checks"] == []
    assert import_state[0]["genesis_import"]["acceptance"]["accepted_by"] == "owner_1"
    assert import_state[0]["genesis_import"]["status"] == "accepted"
    assert import_state[0]["lifecycle_state"] == "draft"
    assert import_state[0]["artifact_version_id"] is None
    assert import_state[0]["current_build_run"] is None
    assert (await _accept(import_state, draft)) is accepted
    gate.assert_awaited_once()
    import_state[0]["current_build_run"] = {"build_id": "later-refinement"}
    accepted.lifecycle_status = BuildRecordStatus.SUPERSEDED
    await require_accepted_genesis_baseline(
        accepted, owner_user_id="owner_1", execution_app_id="mozaiks-platform",
        build_registry_id="appreg_1", registry_service=import_state[1],
    )
    trusted_observer_test_override.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_trusted_image_blocks_genesis_before_source_staging(import_state, monkeypatch):
    from factory_app.workflows._shared.platform import genesis_import
    from factory_app.workflows.AppGenerator.tools import app_runtime_smoke, app_validation

    monkeypatch.setattr(app_validation.app_runtime_smoke, "run_contained_imported_app_runtime_smoke", AsyncMock(), raising=False)
    raw, manifest = _source()
    draft = await _import(import_state, raw, manifest)
    gate = AsyncMock(return_value=_successful_gate_result(manifest))
    monkeypatch.setattr(app_validation, "run_app_bundle_acceptance_gate", gate)
    monkeypatch.setattr(
        app_runtime_smoke, "preflight_contained_imported_smoke",
        Mock(side_effect=RuntimeError("contained validator image identity is unconfigured")),
    )
    host_staging = AsyncMock(side_effect=AssertionError("source was staged before trusted validation"))
    monkeypatch.setattr(genesis_import, "_verified_source_files", host_staging)
    with pytest.raises(GenesisImportError, match="trusted imported Genesis validation observer is unavailable"):
        await _accept(import_state, draft)
    host_staging.assert_not_awaited()
    gate.assert_not_awaited()
    assert import_state[0]["genesis_import"]["status"] == "reserved"
    assert draft.validation_status == BuildRecordValidationStatus.PENDING


@pytest.mark.asyncio
async def test_genesis_acceptance_rejects_wrong_review_digest_and_failed_runtime_gate(
    import_state, monkeypatch, trusted_observer_test_override,
):
    from factory_app.workflows.AppGenerator.tools import app_validation

    monkeypatch.setattr(app_validation.app_runtime_smoke, "run_contained_imported_app_runtime_smoke", AsyncMock(), raising=False)
    raw, manifest = _source()
    draft = await _import(import_state, raw, manifest)
    gate = AsyncMock(return_value={"status": "failed", "passed": False, "checks": []})
    monkeypatch.setattr(app_validation, "run_app_bundle_acceptance_gate", gate)
    with pytest.raises(GenesisImportError, match="reviewed source digests"):
        await _accept(import_state, draft, bundle_sha256="f" * 64)
    with pytest.raises(GenesisImportError, match="reviewed source digests"):
        await _accept(import_state, draft, manifest_sha256="f" * 64)
    gate.assert_not_awaited()
    with pytest.raises(GenesisImportError, match="runtime validation"):
        await _accept(import_state, draft)
    assert import_state[0]["genesis_import"]["status"] == "reserved"
    assert draft.lifecycle_status == BuildRecordStatus.DRAFT
    import_state[2].mark_genesis_build_record_validated.assert_not_awaited()


@pytest.mark.asyncio
async def test_genesis_acceptance_requires_exact_source_and_validator_evidence(
    import_state, monkeypatch, trusted_observer_test_override,
):
    from factory_app.workflows.AppGenerator.tools import app_validation

    monkeypatch.setattr(app_validation.app_runtime_smoke, "run_contained_imported_app_runtime_smoke", AsyncMock(), raising=False)
    raw, manifest = _source()
    draft = await _import(import_state, raw, manifest)
    gate_result = _successful_gate_result(manifest)
    gate_result["app_runtime_smoke"]["source_content_sha256"] = "f" * 64
    monkeypatch.setattr(app_validation, "run_app_bundle_acceptance_gate", AsyncMock(return_value=gate_result))
    with pytest.raises(GenesisImportError, match="verified source or validator identity"):
        await _accept(import_state, draft)
    assert import_state[0]["genesis_import"]["status"] == "reserved"
    import_state[2].mark_genesis_build_record_validated.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_evidence,expected_error", [
    ("missing_snapshot", "snapshot digest"),
    ("malformed_snapshot", "snapshot digest"),
    ("empty_checks", "passed checks"),
    ("missing_runtime_check", "required runtime checks"),
    ("missing_boot", "trusted observer boot evidence"),
    ("failed_boot", "trusted observer boot evidence"),
    ("missing_smoke_status", "trusted observer boot evidence"),
    ("missing_smoke_check", "trusted observer boot evidence"),
    ("missing_observer_origin", "trusted observer boot evidence"),
    ("invalid_observer_run_id", "trusted observer boot evidence"),
    ("missing_observed_boot", "trusted observer boot evidence"),
    ("unverified_event_rejection", "unverified runtime checks"),
    ("missing_unverified_checks", "unverified runtime checks"),
])
async def test_genesis_acceptance_rejects_incomplete_passed_gate(
    import_state, monkeypatch, trusted_observer_test_override, missing_evidence, expected_error,
):
    from factory_app.workflows.AppGenerator.tools import app_validation

    monkeypatch.setattr(app_validation.app_runtime_smoke, "run_contained_imported_app_runtime_smoke", AsyncMock(), raising=False)
    raw, manifest = _source()
    draft = await _import(import_state, raw, manifest)
    gate_result = _successful_gate_result(manifest)
    if missing_evidence == "missing_snapshot":
        gate_result.pop("snapshot_digest")
    elif missing_evidence == "malformed_snapshot":
        gate_result["snapshot_digest"] = "not-a-digest"
    elif missing_evidence == "empty_checks":
        gate_result["checks"] = []
    elif missing_evidence == "missing_runtime_check":
        gate_result["checks"] = gate_result["checks"][:1]
    elif missing_evidence == "missing_boot":
        gate_result["app_runtime_smoke"]["results"] = []
    elif missing_evidence == "failed_boot":
        gate_result["app_runtime_smoke"]["results"][0]["status"] = "failed"
    elif missing_evidence == "missing_smoke_status":
        gate_result["app_runtime_smoke"].pop("status")
    elif missing_evidence == "missing_smoke_check":
        gate_result["app_runtime_smoke"]["checks"] = []
    elif missing_evidence == "missing_observer_origin":
        gate_result["app_runtime_smoke"].pop("observer_origin")
    elif missing_evidence == "invalid_observer_run_id":
        gate_result["app_runtime_smoke"]["observer_run_id"] = "not-a-run-id"
    elif missing_evidence == "missing_observed_boot":
        gate_result["app_runtime_smoke"].pop("observed_boot")
    elif missing_evidence == "unverified_event_rejection":
        gate_result["app_runtime_smoke"]["observer_unverified_checks"] = ["event_rejection"]
    elif missing_evidence == "missing_unverified_checks":
        gate_result["app_runtime_smoke"].pop("observer_unverified_checks")
    monkeypatch.setattr(app_validation, "run_app_bundle_acceptance_gate", AsyncMock(return_value=gate_result))
    with pytest.raises(GenesisImportError, match=expected_error):
        await _accept(import_state, draft)
    assert import_state[0]["genesis_import"]["status"] == "reserved"
    assert draft.validation_status == BuildRecordValidationStatus.PENDING
    import_state[2].mark_genesis_build_record_validated.assert_not_awaited()


@pytest.mark.asyncio
async def test_contained_observer_unverified_event_rejection_never_writes_acceptance(
    import_state, monkeypatch, trusted_observer_test_override,
):
    from factory_app.workflows.AppGenerator.tools import app_validation

    monkeypatch.setattr(app_validation.app_runtime_smoke, "run_contained_imported_app_runtime_smoke", AsyncMock())
    raw, manifest = _source()
    draft = await _import(import_state, raw, manifest)
    gate_result = _successful_gate_result(manifest)
    gate_result["app_runtime_smoke"]["observer_unverified_checks"] = ["event_rejection"]
    monkeypatch.setattr(app_validation, "run_app_bundle_acceptance_gate", AsyncMock(return_value=gate_result))
    with pytest.raises(GenesisImportError, match="unverified runtime checks"):
        await _accept(import_state, draft)
    import_state[2].mark_genesis_build_record_validated.assert_not_awaited()
    import_state[1].accept_genesis_import.assert_not_awaited()
    assert import_state[0]["genesis_import"]["status"] == "reserved"


@pytest.mark.asyncio
@pytest.mark.parametrize("field,replacement", [
    ("snapshot_digest", "f" * 64),
    ("validator_image_id", "sha256:" + "f" * 64),
    ("observed_boot", {"check": "boot.http_ready", "status": "failed"}),
    ("observer_origin", "unknown_probe"),
    ("observer_run_id", "not-a-run-id"),
    ("observer_unverified_checks", ["event_rejection"]),
])
async def test_genesis_baseline_rejects_changed_evidence_with_original_receipt(
    import_state, monkeypatch, trusted_observer_test_override, field, replacement,
):
    from factory_app.workflows.AppGenerator.tools import app_validation

    monkeypatch.setattr(app_validation.app_runtime_smoke, "run_contained_imported_app_runtime_smoke", AsyncMock(), raising=False)
    raw, manifest = _source()
    draft = await _import(import_state, raw, manifest)
    monkeypatch.setattr(app_validation, "run_app_bundle_acceptance_gate", AsyncMock(
        return_value=_successful_gate_result(manifest),
    ))
    accepted = await _accept(import_state, draft)
    original = copy.deepcopy(accepted.commit_metadata.metadata["genesis_validation"])
    accepted.commit_metadata.metadata["genesis_validation"][field] = replacement
    with pytest.raises(GenesisImportError, match="no matching accepted review receipt"):
        await require_accepted_genesis_baseline(
            accepted, owner_user_id="owner_1", execution_app_id="mozaiks-platform",
            build_registry_id="appreg_1", registry_service=import_state[1],
        )
    accepted.commit_metadata.metadata["genesis_validation"] = original
    await require_accepted_genesis_baseline(
        accepted, owner_user_id="owner_1", execution_app_id="mozaiks-platform",
        build_registry_id="appreg_1", registry_service=import_state[1],
    )


@pytest.mark.asyncio
async def test_genesis_acceptance_recovers_validation_before_registry_receipt(
    import_state, monkeypatch, trusted_observer_test_override,
):
    from factory_app.workflows.AppGenerator.tools import app_validation

    monkeypatch.setattr(app_validation.app_runtime_smoke, "run_contained_imported_app_runtime_smoke", AsyncMock(), raising=False)
    raw, manifest = _source()
    draft = await _import(import_state, raw, manifest)
    gate = AsyncMock(return_value=_successful_gate_result(manifest))
    monkeypatch.setattr(app_validation, "run_app_bundle_acceptance_gate", gate)
    registry = import_state[1]
    accept_claim = registry.accept_genesis_import.side_effect
    registry.accept_genesis_import.side_effect = AsyncMock(return_value=None)
    with pytest.raises(GenesisImportError, match="Factory target changed"):
        await _accept(import_state, draft)
    assert import_state[0]["genesis_import"]["status"] == "reserved"
    assert draft.validation_status == BuildRecordValidationStatus.PASSED
    saved = copy.deepcopy(draft.commit_metadata.metadata["genesis_validation"])
    registry.accept_genesis_import.side_effect = accept_claim
    accepted = await _accept(import_state, draft)
    assert accepted.lifecycle_status == BuildRecordStatus.CURRENT
    assert accepted.commit_metadata.metadata["genesis_validation"] == saved
    assert import_state[0]["genesis_import"]["acceptance"]["validation_sha256"] == saved["sha256"]
    gate.assert_awaited_once()
    assert trusted_observer_test_override.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["evidence", "image"])
async def test_genesis_acceptance_rejects_changed_saved_validation_before_receipt(
    import_state, monkeypatch, trusted_observer_test_override, mismatch,
):
    from factory_app.workflows._shared.platform import genesis_import
    from factory_app.workflows.AppGenerator.tools import app_validation

    monkeypatch.setattr(app_validation.app_runtime_smoke, "run_contained_imported_app_runtime_smoke", AsyncMock(), raising=False)
    raw, manifest = _source()
    draft = await _import(import_state, raw, manifest)
    gate = AsyncMock(return_value=_successful_gate_result(manifest))
    monkeypatch.setattr(app_validation, "run_app_bundle_acceptance_gate", gate)
    registry = import_state[1]
    registry.accept_genesis_import.side_effect = AsyncMock(return_value=None)
    with pytest.raises(GenesisImportError, match="Factory target changed"):
        await _accept(import_state, draft)
    if mismatch == "evidence":
        draft.commit_metadata.metadata["genesis_validation"]["observer_unverified_checks"] = ["event_rejection"]
    else:
        monkeypatch.setattr(genesis_import, "_preflight_imported_smoke", AsyncMock(return_value="sha256:" + "f" * 64))
    with pytest.raises(GenesisImportError, match="inconsistent validation evidence"):
        await _accept(import_state, draft)
    assert import_state[0]["genesis_import"]["status"] == "reserved"
    gate.assert_awaited_once()


@pytest.mark.asyncio
async def test_genesis_acceptance_recovers_receipt_before_artifact_status(
    import_state, monkeypatch, trusted_observer_test_override,
):
    from factory_app.workflows._shared.platform import genesis_import
    from factory_app.workflows.AppGenerator.tools import app_validation

    monkeypatch.setattr(app_validation.app_runtime_smoke, "run_contained_imported_app_runtime_smoke", AsyncMock(), raising=False)
    raw, manifest = _source()
    draft = await _import(import_state, raw, manifest)
    gate = AsyncMock(return_value=_successful_gate_result(manifest))
    monkeypatch.setattr(app_validation, "run_app_bundle_acceptance_gate", gate)
    project = import_state[2].accept_genesis_build_record
    project.side_effect = AsyncMock(return_value=draft)
    with pytest.raises(GenesisImportError, match="no matching accepted review receipt"):
        await _accept(import_state, draft)
    assert import_state[0]["genesis_import"]["status"] == "accepted"
    assert draft.lifecycle_status == BuildRecordStatus.DRAFT
    host_staging = AsyncMock(side_effect=AssertionError("accepted receipt retry staged source"))
    monkeypatch.setattr(genesis_import, "_verified_source_files", host_staging)
    evidence = draft.commit_metadata.metadata["genesis_validation"]
    original_digest = evidence["snapshot_digest"]
    evidence["snapshot_digest"] = "f" * 64
    with pytest.raises(GenesisImportError, match="validation evidence differs from review receipt"):
        await _accept(import_state, draft)
    project.assert_awaited_once()
    evidence["snapshot_digest"] = original_digest
    async def recover_project(**_kwargs):
        draft.lifecycle_status = BuildRecordStatus.CURRENT
        return draft

    project.side_effect = recover_project
    assert (await _accept(import_state, draft)).lifecycle_status == BuildRecordStatus.CURRENT
    host_staging.assert_not_awaited()
    gate.assert_awaited_once()


@pytest.mark.asyncio
async def test_imported_acceptance_gate_loads_source_only_in_contained_smoke(monkeypatch):
    from factory_app.workflows.AppGenerator.tools import app_validation

    in_process = AsyncMock(side_effect=AssertionError("source Python reached the Studio process"))
    smoke = AsyncMock(return_value={
        "status": "passed", "passed": True,
        "results": [{"check": "boot.http_ready", "status": "passed", "message": "Ready."}],
        "checks": [{"id": "app_runtime_smoke", "passed": True}],
        "observer_origin": "trusted_external_probe_v1",
        "observer_run_id": "b" * 32,
        "observed_boot": {"check": "boot.http_ready", "status": "passed"},
        "observer_unverified_checks": [],
        "failed_tests": [], "warnings": [],
    })
    monkeypatch.setattr(app_validation, "_app_runtime_load_result", in_process)
    monkeypatch.setattr(app_validation, "_app_runtime_smoke_result", smoke)
    result = await app_validation.run_app_bundle_acceptance_gate(
        files={"app.json": '{"appId":"mozaiks-platform","appName":"Mozaiks"}'},
        contained_imported_source=True,
    )
    assert result["app_runtime_load"]["passed"] is True
    in_process.assert_not_awaited()
    smoke.assert_awaited_once()
    assert app_validation._runtime_load_from_observer_smoke({"status": "skipped", "results": []})["status"] == "skipped"
    assert app_validation._runtime_load_from_observer_smoke({"status": "failed", "results": []})["status"] == "failed"
    assert app_validation._runtime_load_from_observer_smoke({
        "status": "passed", "passed": True,
        "results": [{"check": "boot.app_load", "status": "passed"}],
        "observer_origin": "trusted_external_probe_v1",
        "observed_boot": {"check": "boot.http_ready", "status": "passed"},
    })["status"] == "failed"
    assert app_validation._runtime_load_from_observer_smoke({"status": "passed", "results": None})["status"] == "failed"


@pytest.mark.asyncio
async def test_imported_runtime_smoke_receives_exact_binary_assets(monkeypatch):
    from factory_app.workflows.AppGenerator.tools import app_validation

    observed = {}

    async def inspect_workspace(app_root, *, mongo_uri):
        observed["app"] = (app_root / "app.json").read_text(encoding="utf-8")
        observed["asset"] = (app_root / "ui/public/logo.png").read_bytes()
        return {"status": "passed", "passed": True}

    monkeypatch.setattr(app_validation.app_runtime_smoke, "resolve_smoke_mongo_uri", lambda: None)
    monkeypatch.setattr(app_validation.app_runtime_smoke, "run_app_runtime_smoke", inspect_workspace)
    await app_validation._app_runtime_smoke_result(
        {"app.json": '{"appId":"mozaiks-platform"}'},
        binary_assets={"ui/public/logo.png": b"\x89PNG\r\n\x1a\n"},
    )
    assert observed["asset"] == b"\x89PNG\r\n\x1a\n"
    assert json.loads(observed["app"])["appId"] == "mozaiks-platform"


@pytest.mark.asyncio
async def test_contained_imported_smoke_receives_verified_text_and_binary_digests(monkeypatch):
    from factory_app.workflows.AppGenerator.tools import app_validation

    expected_files = {
        "app.json": b'{"appId":"mozaiks-platform"}',
        "ui/public/logo.png": b"\x89PNG\r\n\x1a\n",
    }

    async def inspect_workspace(app_root, *, expected_source_sha256):
        assert expected_source_sha256 == {
            path: hashlib.sha256(raw).hexdigest() for path, raw in expected_files.items()
        }
        assert {path: (app_root / path).read_bytes() for path in expected_files} == expected_files
        return {"status": "passed", "passed": True}

    monkeypatch.setattr(
        app_validation.app_runtime_smoke, "run_contained_imported_app_runtime_smoke",
        inspect_workspace, raising=False,
    )
    result = await app_validation._app_runtime_smoke_result(
        {"app.json": expected_files["app.json"].decode("utf-8")},
        binary_assets={"ui/public/logo.png": expected_files["ui/public/logo.png"]},
        contained_imported_source=True,
    )
    assert result["passed"] is True


@pytest.mark.asyncio
async def test_imported_runtime_smoke_fails_closed_without_contained_backend(monkeypatch):
    from factory_app.workflows.AppGenerator.tools import app_validation

    host_smoke = AsyncMock(side_effect=AssertionError("host Mongo smoke was invoked"))
    host_staging = Mock(side_effect=AssertionError("source was staged on Studio host"))
    monkeypatch.setattr(app_validation.app_runtime_smoke, "run_app_runtime_smoke", host_smoke)
    monkeypatch.setattr(app_validation.tempfile, "TemporaryDirectory", host_staging)
    monkeypatch.delattr(
        app_validation.app_runtime_smoke, "run_contained_imported_app_runtime_smoke", raising=False,
    )
    with pytest.raises(ValueError, match="Contained imported-source runtime smoke is unavailable"):
        await app_validation._app_runtime_smoke_result(
            {"app.json": '{"appId":"mozaiks-platform"}'}, contained_imported_source=True,
        )
    host_smoke.assert_not_awaited()
    host_staging.assert_not_called()


@pytest.mark.asyncio
async def test_genesis_owner_acceptance_stays_reserved_without_contained_backend(import_state, monkeypatch):
    from factory_app.workflows._shared.platform import genesis_import
    from factory_app.workflows.AppGenerator.tools import app_runtime_smoke

    raw, manifest = _source()
    draft = await _import(import_state, raw, manifest)
    host_smoke = AsyncMock(side_effect=AssertionError("host Mongo smoke was invoked"))
    host_staging = AsyncMock(side_effect=AssertionError("source was staged on Studio host"))
    monkeypatch.setattr(app_runtime_smoke, "run_app_runtime_smoke", host_smoke)
    monkeypatch.setattr(genesis_import, "_verified_source_files", host_staging)
    monkeypatch.delattr(app_runtime_smoke, "run_contained_imported_app_runtime_smoke", raising=False)
    with pytest.raises(ValueError, match="Contained imported-source runtime smoke is unavailable"):
        await _accept(import_state, draft)
    assert import_state[0]["genesis_import"]["status"] == "reserved"
    assert draft.lifecycle_status == BuildRecordStatus.DRAFT
    assert draft.validation_status == BuildRecordValidationStatus.PENDING
    import_state[2].mark_genesis_build_record_validated.assert_not_awaited()
    host_smoke.assert_not_awaited()
    host_staging.assert_not_awaited()
