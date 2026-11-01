"""Media publication preserves source bytes and fails closed at build ownership boundaries."""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from xml.etree import ElementTree

import pytest
import yaml

from factory_app.workflows._shared.artifact_bundle import read_artifact_bundle
from factory_app.workflows._shared.platform import current_build
from factory_app.workflows.AppMediaGenerator.tools import publish_app_media as publisher
from mozaiksai.core.artifacts.models import BuildRecord
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge, _wrap_tool_with_context
from mozaiksai.core.workflow.context.authority import build_context_authority_policy
from mozaiksai.core.workflow.context.schema import load_context_variables_config


class _Context(dict):
    def set(self, key, value):
        self[key] = value


def _planned(asset_id, *, source="generated"):
    return {"asset_id": asset_id, "kind": "illustration", "source": source, "brief": "App image", "path": None}


@pytest.fixture
def media_build(monkeypatch, tmp_path):
    binding = {"build_registry_id": "project_1", "target_app_id": "target_1", "build_id": "build_1", "phase": "genesis"}
    context = _Context(run_build_binding=binding, artifact_version_id="parent", workflow_sequence="build")
    parent_files = {
        "app.json": '{"appId":"target_1","name":"Test app"}\r\n',
        "config/asset_manifest.json": json.dumps({"version": "1.0", "assets": [_planned("hero"), _planned("photo", source="uploaded")]}),
        "ui/assets/keep.webp": b"\x00\xfforiginal image bytes",
    }
    workspace = tmp_path / "original" / "app"
    workspace.mkdir(parents=True)
    archive_path = workspace.parent / "bundle.zip"
    artifacts = {}
    registry_record = {
        "app_id": "target_1", "chat_app_id": "factory-host", "owner_user_id": "owner_1",
        "current_build_run": {"build_id": "build_1", "phase": "genesis", "artifact_version_id": "parent"},
    }
    state = SimpleNamespace(context=context, parent_files=parent_files, registry_record=registry_record, artifacts=artifacts)

    def save_parent():
        with zipfile.ZipFile(archive_path, "w") as archive:
            for path, content in parent_files.items():
                archive.writestr(f"bundle/{path}", content)
        raw = archive_path.read_bytes()
        parent = BuildRecord(
            id="parent", app_id="target_1", build_family="app_bundle", build_key="app_bundle",
            version_number=1, lineage_root_id="parent", lifecycle_status="draft",
            validation_status="passed", app_validation_status="passed", app_validation_strategy="docker",
            sandbox_session_id="parent-sandbox", sandbox_provider="docker",
            files_manifest=[{"path": "bundle/bundle.zip", "sha256": hashlib.sha256(raw).hexdigest(), "content_type": "application/zip"}],
            commit_metadata={"author_user_id": "owner_1", "metadata": {
                **binding, "bundle_name": "bundle", "artifact_path": str(archive_path), "workspace_dir": str(workspace),
                "acceptance": {"record_id": "parent"}, "accepted_file_manifest": [{"path": "app.json"}],
                "app_bundle_acceptance": {"snapshot_digest": "parent-snapshot", "status": "passed", "passed": True},
                "validation_evidence": {"completed": ["parent"]},
                "capability_packs": [{"id": "owned-pack", "version": "1"}],
            }},
        )
        artifacts[parent.id] = parent
        state.parent = parent
        state.original_archive = raw
        return parent

    async def get_record(*, owner_user_id, build_registry_id):
        return {"app": registry_record if owner_user_id == registry_record["owner_user_id"] and build_registry_id == "project_1" else None}

    async def get_artifact(*, app_id, build_record_id):
        artifact = artifacts.get(build_record_id)
        return artifact if artifact and artifact.app_id == app_id else None

    async def create_record(**kwargs):
        record_id = f"child_{len(artifacts)}"
        record = BuildRecord(id=record_id, version_number=len(artifacts) + 1, lineage_root_id="parent", **kwargs)
        artifacts[record_id] = record
        return record

    async def update_status(**kwargs):
        current = registry_record["current_build_run"]
        if current["build_id"] != kwargs["expected_build_id"] or current["artifact_version_id"] != kwargs["expected_artifact_version_id"]:
            return {"success": False}
        current.update(kwargs["current_build_run"])
        return {"success": True, "app": registry_record}

    registry = SimpleNamespace(get_app_record=AsyncMock(side_effect=get_record), update_build_status=AsyncMock(side_effect=update_status))
    store = SimpleNamespace(get_build_record=AsyncMock(side_effect=get_artifact), create_build_record=AsyncMock(side_effect=create_record))
    media = SimpleNamespace(
        list_generated_assets_for_chat=AsyncMock(return_value=[SimpleNamespace(
            asset_id="media_1", media_type="image/webp", model="test-model",
            generation_params={"task_batch_id": "app_media_jobs", "task_id": "hero"},
        )]),
        get_asset_content=AsyncMock(return_value=b"RIFF...WEBPnew image"),
    )
    monkeypatch.setattr(current_build, "AppRegistryService", lambda: registry)
    monkeypatch.setattr(current_build, "get_artifact_store", lambda: store)
    monkeypatch.setattr(publisher, "AppRegistryService", lambda: registry)
    monkeypatch.setattr(publisher, "get_artifact_store", lambda: store)
    monkeypatch.setattr(publisher, "get_artifact_content_store", lambda: SimpleNamespace(backend_name="local"))
    monkeypatch.setattr(publisher, "get_media_asset_store", lambda: media)
    monkeypatch.setattr(publisher, "active_workflow_tool_run", lambda: ("AppMediaGenerator", "factory-host", "chat_1", "owner_1"))
    validation = AsyncMock(return_value={
        "validation_status": "passed", "validation_strategy": "docker",
        "app_bundle_acceptance_result": {"status": "passed", "passed": True, "snapshot_digest": "fresh-snapshot", "validation_evidence": {"completed": ["fresh"]}},
        "app_validation_result": {"validation_status": "passed", "validation_strategy": "docker"},
    })
    monkeypatch.setattr(publisher, "validate_generated_app_candidate", validation)
    state.registry, state.store, state.media, state.save_parent = registry, store, media, save_parent
    state.validation = validation
    save_parent()
    return state


@pytest.mark.asyncio
async def test_publish_preserves_parent_bytes_and_registers_owned_draft_child(media_build):
    result = await publisher.publish_app_media(media_build.context)
    assert result["published"] is True
    child = media_build.artifacts[result["artifact_version_id"]]
    assert (child.app_id, child.parent_build_record_id, child.lifecycle_status) == ("target_1", "parent", "draft")
    assert child.commit_metadata.author_user_id == "owner_1"
    assert "acceptance" not in child.commit_metadata.metadata
    assert child.commit_metadata.metadata["app_bundle_acceptance"]["snapshot_digest"] == "fresh-snapshot"
    assert child.commit_metadata.metadata["validation_evidence"] == {"completed": ["fresh"]}
    assert child.sandbox_session_id is None and child.sandbox_provider is None
    assert Path(media_build.parent.commit_metadata.metadata["artifact_path"]).read_bytes() == media_build.original_archive
    files, diagnostics = await read_artifact_bundle(child, include_binary=True)
    assert diagnostics == []
    assert files["app.json"] == media_build.parent_files["app.json"]
    assert files["ui/assets/keep.webp"] == media_build.parent_files["ui/assets/keep.webp"]
    assert files["ui/assets/hero.webp"] == b"RIFF...WEBPnew image"
    assert b"Photo to upload" in files["ui/assets/placeholders/photo.svg"]
    manifest = json.loads(files["config/asset_manifest.json"])
    assert [entry["path"] for entry in manifest["assets"]] == ["ui/assets/hero.webp", "ui/assets/placeholders/photo.svg"]
    assert media_build.registry_record["current_build_run"]["artifact_version_id"] == child.id
    assert media_build.context["artifact_version_id"] == child.id
    assert media_build.context["app_media_complete"] is True
    media_build.media.list_generated_assets_for_chat.assert_awaited_once_with(app_id="factory-host", chat_id="chat_1", limit=200)
    media_build.registry.update_build_status.assert_awaited_once()
    assert media_build.registry.update_build_status.call_args.kwargs["expected_artifact_version_id"] == "parent"
    media_build.validation.assert_awaited_once()
    validation_call = media_build.validation.call_args.kwargs
    assert validation_call["app_id"] == "target_1"
    assert validation_call["capability_packs"] == [{"id": "owned-pack", "version": "1"}]
    assert validation_call["files"]["ui/assets/hero.webp"] == b"RIFF...WEBPnew image"
    assert validation_call["files"]["ui/assets/keep.webp"] == media_build.parent_files["ui/assets/keep.webp"]

    rerun = await publisher.publish_app_media(media_build.context)
    assert rerun["reason"] == "no_media_changes"
    assert media_build.store.create_build_record.await_count == 1
    assert media_build.validation.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("fault,code", [
    ("owner", "target_unavailable"), ("host", "target_unavailable"), ("target", "target_unavailable"),
    ("build", "build_superseded"), ("selector", "artifact_not_current"), ("author", "artifact_binding_mismatch"),
])
async def test_publish_refuses_foreign_or_superseded_source_before_media_read(media_build, fault, code):
    if fault == "owner":
        media_build.registry_record["owner_user_id"] = "someone_else"
    elif fault == "host":
        media_build.registry_record["chat_app_id"] = "other_host"
    elif fault == "target":
        media_build.registry_record["app_id"] = "other_target"
    elif fault == "build":
        media_build.registry_record["current_build_run"]["build_id"] = "build_new"
    elif fault == "selector":
        media_build.context["artifact_version_id"] = "other_artifact"
    else:
        media_build.parent.commit_metadata.author_user_id = "someone_else"
    result = await publisher.publish_app_media(media_build.context)
    assert result == {"published": False, "error": f"app_media_source_{code}"}
    media_build.media.list_generated_assets_for_chat.assert_not_awaited()
    media_build.store.create_build_record.assert_not_awaited()
    media_build.registry.update_build_status.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("asset_id", ["../../../../outside", "..\\..\\outside", "C:outside", "CON"])
async def test_publish_refuses_unsafe_manifest_ids_before_any_output(media_build, asset_id):
    media_build.parent_files["config/asset_manifest.json"] = json.dumps({"version": "1.0", "assets": [_planned(asset_id, source="uploaded")]})
    media_build.save_parent()
    result = await publisher.publish_app_media(media_build.context)
    assert result == {"published": False, "error": "app_media_source_asset_manifest_invalid"}
    media_build.store.create_build_record.assert_not_awaited()
    media_build.media.list_generated_assets_for_chat.assert_not_awaited()
    assert not list(Path(media_build.parent.commit_metadata.metadata["workspace_dir"]).parent.glob("media_*"))


@pytest.mark.parametrize("path", ["../outside.svg", "/outside.svg", "ui/assets/../../outside.svg", "ui\\assets\\image.webp", "C:/outside.svg"])
def test_stage_checks_every_path_before_writing_any_file(tmp_path, path):
    app_dir = tmp_path / "app"
    with pytest.raises(ValueError):
        publisher._stage(app_dir, {"app.json": "{}", path: b"bad"})
    assert not app_dir.exists()
    assert not (tmp_path / "outside.svg").exists()


def test_stage_refuses_case_collisions_before_writing(tmp_path):
    with pytest.raises(ValueError):
        publisher._stage(tmp_path / "app", {"ui/assets/hero.webp": b"one", "ui/assets/HERO.webp": b"two"})
    assert not (tmp_path / "app").exists()


def test_placeholder_truncates_text_without_cutting_an_xml_entity():
    label = "a" * 119 + "& cafe"
    svg = ElementTree.fromstring(publisher._placeholder_svg({"asset_id": "photo", "alt": label}))
    assert svg.attrib["aria-label"] == f"Photo to upload: {label[:120]}"


@pytest.mark.asyncio
async def test_publish_refuses_overwriting_an_existing_bundle_asset(media_build):
    media_build.parent_files["ui/assets/HERO.webp"] = b"keep this image"
    media_build.save_parent()
    result = await publisher.publish_app_media(media_build.context)
    assert result == {"published": False, "error": "app_media_source_asset_path_collision"}
    media_build.store.create_build_record.assert_not_awaited()
    assert Path(media_build.parent.commit_metadata.metadata["artifact_path"]).read_bytes() == media_build.original_archive


@pytest.mark.asyncio
async def test_publish_does_not_replace_a_newer_artifact_in_the_same_build(media_build):
    async def read_media(_asset):
        media_build.registry_record["current_build_run"]["artifact_version_id"] = "newer_same_build"
        return b"RIFF...WEBPnew image"

    media_build.media.get_asset_content.side_effect = read_media
    result = await publisher.publish_app_media(media_build.context)
    assert result == {"published": False, "error": "app_media_source_build_superseded"}
    assert media_build.registry_record["current_build_run"]["artifact_version_id"] == "newer_same_build"
    assert media_build.context["artifact_version_id"] == "parent"


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["failed", "skipped", "acceptance", "build"])
async def test_failed_or_incomplete_fresh_validation_leaves_parent_untouched(media_build, fault):
    result = media_build.validation.return_value
    if fault in {"failed", "skipped"}:
        result["validation_status"] = fault
    elif fault == "acceptance":
        result["app_bundle_acceptance_result"]["passed"] = False
    else:
        result["app_validation_result"]["validation_status"] = "failed"
    outcome = await publisher.publish_app_media(media_build.context)
    assert outcome == {"published": False, "error": "app_media_source_validation_failed"}
    assert media_build.context["artifact_version_id"] == "parent"
    assert media_build.registry_record["current_build_run"]["artifact_version_id"] == "parent"
    media_build.store.create_build_record.assert_not_awaited()
    media_build.registry.update_build_status.assert_not_awaited()
    assert not list(Path(media_build.parent.commit_metadata.metadata["workspace_dir"]).parent.glob("media_*"))


@pytest.mark.asyncio
async def test_registry_service_forwards_expected_source_version():
    from factory_app.app.modules.app_registry.backend.service import AppRegistryService

    repo = SimpleNamespace(update_lifecycle_state=AsyncMock(return_value={"app_id": "target_1"}))
    service = AppRegistryService(repo=repo)
    result = await service.update_build_status(
        owner_user_id="owner_1", build_registry_id="project_1", status="review",
        expected_build_id="build_1", expected_artifact_version_id="parent",
    )
    assert result["success"] is True
    assert repo.update_lifecycle_state.call_args.kwargs["expected_artifact_version_id"] == "parent"


@pytest.mark.asyncio
async def test_publish_refreshes_review_fields_under_runtime_context_authority(media_build):
    config_path = Path(__file__).resolve().parents[1] / "factory_app/workflows/AppMediaGenerator/context_variables.yaml"
    definitions = load_context_variables_config(yaml.safe_load(config_path.read_text(encoding="utf-8"))).definitions
    policy = build_context_authority_policy(workflow_name="AppMediaGenerator", definitions=definitions)
    bridge = ContextVariablesBridge({
        **media_build.context, "app_id": "factory-host", "user_id": "owner_1",
        "app_validation_preview_url": "http://old-parent-preview.invalid",
    }, authority_policy=policy)
    bridge._bind_run(("AppMediaGenerator", "factory-host", "chat_1"), policy)
    result = await _wrap_tool_with_context(publisher.publish_app_media, bridge)()
    assert result["published"] is True
    assert bridge.get("app_validation_status") == "passed"
    assert bridge.get("app_validation_strategy_used") == "docker"
    assert bridge.get("app_validation_preview_url") is None
    assert bridge.get("integration_tests_passed") is True
    assert bridge.get("app_bundle_acceptance_status") == "passed"
