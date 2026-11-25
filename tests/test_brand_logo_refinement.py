"""A Studio logo choice belongs to the pinned app build and its source chat."""
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from factory_app.workflows._shared import brand_revision
from factory_app.workflows._shared.artifact_bundle import read_artifact_bundle
from factory_app.workflows.BrandAssetGeneratorWorkflow.tools import stage_logo as logo
from mozaiksai.core.runtime.app.theme_contract import APP_THEME_PATH
from tests.test_app_media_publishing import media_build as media_build


@pytest.fixture
def selection(media_build, monkeypatch):
    fixture = media_build
    fixture.parent_files["brand/theme_config.json"] = json.dumps({"assets": {"logo": None}, "theme": {"density": "compact"}})
    fixture.save_parent()
    context = fixture.context
    context["run_build_binding"] = {**context["run_build_binding"], "phase": "refinement", "build_id": "new-build"}
    context.update(change_request_id="change", workflow_sequence="logo_revision")
    run = ("BrandAssetGeneratorWorkflow", "factory-host", "logo-chat", "owner_1")
    current = fixture.registry_record["current_build_run"]
    current.update(build_id="new-build", phase="refinement", active_workflow_id=run[0], active_chat_id=run[2])
    current.pop("artifact_version_id")
    fixture.registry_record["lifecycle_state"] = "building"
    async def update(**kwargs):
        assert kwargs["expected_build_id"] == current["build_id"]
        assert kwargs["expected_active_chat_id"] == current["active_chat_id"]
        assert kwargs["expected_lifecycle_state"] == fixture.registry_record["lifecycle_state"]
        current["artifact_version_id"] = kwargs["artifact_version_id"]
        fixture.registry_record["lifecycle_state"] = kwargs["status"]
        return {"success": True}

    fixture.registry.update_build_status.side_effect = update
    change = SimpleNamespace(
        id="change", app_id="target_1", build_family="app_bundle", build_key="app_bundle",
        created_by_user_id=run[3], build_record_id="parent",
        router_decision={"workflow_sequence": "logo_revision"},
    )
    async def release(*, app_id, build_record_id, validation_status):
        child = fixture.artifacts[build_record_id]
        assert child.app_id == app_id and current["artifact_version_id"] == child.id
        assert child.validation_status == "pending"
        child.validation_status = validation_status
        return True

    async def reject(*, app_id, artifact_version_id, reason):
        child = fixture.artifacts[artifact_version_id]
        assert child.app_id == app_id and reason
        assert child.validation_status == "pending"
        child.lifecycle_status = "archived"
        return True

    fixture.store.set_validation_status_for_build_record = AsyncMock(side_effect=release)
    fixture.store.reject_artifact_version = AsyncMock(side_effect=reject)
    fixture.store.get_change_request = AsyncMock(return_value=change)
    monkeypatch.setattr(brand_revision, "AppRegistryService", lambda: fixture.registry)
    monkeypatch.setattr(brand_revision, "get_artifact_store", lambda: fixture.store)
    monkeypatch.setattr(logo, "get_media_asset_store", lambda: fixture.media)
    monkeypatch.setattr(brand_revision, "validate_generated_app_candidate", fixture.validation)
    monkeypatch.setattr(brand_revision, "resolve_app_compilation_inputs", AsyncMock(return_value=None))
    data = b"test-owned-image"
    fixture.media.get_asset_content.return_value = data
    asset = SimpleNamespace(
        asset_id="source-image", app_id=run[1], source_chat_id=run[2], source_workflow=run[0],
        media_type="image/png", sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data),
    )
    return fixture, context, run, asset, data, change


@pytest.mark.asyncio
async def test_selected_logo_can_brand_an_app_using_platform_defaults(selection):
    fixture, context, run, asset, data, _ = selection
    del fixture.parent_files[APP_THEME_PATH]
    fixture.save_parent()
    result = await logo.stage_logo(context, asset=asset, run=run, guard=lambda: None)
    files, diagnostics = await read_artifact_bundle(fixture.artifacts[result["draft_build_record_id"]], include_binary=True)
    assert not diagnostics
    theme = json.loads(files[APP_THEME_PATH])
    assert set(theme) == {"assets"}
    assert files[f"brand/assets/{theme['assets']['logo']}"] == data


@pytest.mark.asyncio
async def test_confirmed_logo_creates_only_one_owned_draft(selection):
    fixture, context, run, asset, data, _ = selection
    prior_evidence = {"applied_paths", "staged_file_sha256", "coding_provider", "refinement_scope", "source_surface",
                      "refinement", "carry_forward_report", "accepted_file_manifest", "acceptance"}
    fixture.parent.commit_metadata.metadata.update(dict.fromkeys(prior_evidence, "parent evidence"))
    result = await logo.stage_logo(context, asset=asset, run=run, guard=lambda: None)
    child = fixture.artifacts[result["draft_build_record_id"]]
    assert child.parent_build_record_id == "parent"
    assert child.source_chat_id == run[2] and child.source_workflow == run[0]
    assert child.commit_metadata.metadata["build_id"] == "new-build"
    assert not prior_evidence.intersection(child.commit_metadata.metadata)
    assert child.commit_metadata.metadata["capability_packs"] == fixture.parent.commit_metadata.metadata["capability_packs"]
    assert child.canonical_inputs_version == fixture.parent.canonical_inputs_version
    assert child.app_id == "target_1" and child.lifecycle_status == "draft"
    assert child.validation_status == "passed"
    files, diagnostics = await read_artifact_bundle(child, include_binary=True)
    assert not diagnostics
    theme = json.loads(files["brand/theme_config.json"])
    assert theme["theme"] == {"density": "compact"}
    assert files[f"brand/assets/{theme['assets']['logo']}"] == data
    assert all(files[k] == v for k, v in fixture.parent_files.items() if k != "brand/theme_config.json")
    assert (await logo.stage_logo(context, asset=asset, run=run, guard=lambda: None)) == result
    fixture.validation.assert_awaited_once()
    fixture.store.create_build_record.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [
    "foreign_chat", "wrong_workflow", "changed_build", "wrong_request", "wrong_digest", "revoked",
    "wrong_owner", "wrong_target", "wrong_registry", "other_app_change",
])
async def test_invalid_logo_never_stages_a_child(selection, fault):
    fixture, context, run, asset, _, change = selection
    def guard():
        if fault == "revoked":
            raise PermissionError("Workflow scope changed")

    if fault == "foreign_chat":
        asset.source_chat_id = "some-other-chat"
    elif fault == "wrong_workflow":
        asset.source_workflow = "another-workflow"
    elif fault == "changed_build":
        fixture.registry_record["current_build_run"]["build_id"] = "newer"
    elif fault == "wrong_request":
        change.build_record_id = "unrelated"
    elif fault == "wrong_digest":
        asset.sha256 = "incorrect"
    elif fault == "wrong_owner":
        fixture.registry_record["owner_user_id"] = "another_owner"
    elif fault == "wrong_target":
        fixture.registry_record["app_id"] = "other_target"
    elif fault == "wrong_registry":
        fixture.parent.commit_metadata.metadata["build_registry_id"] = "other_registry"
    elif fault == "other_app_change":
        change.app_id = "other_target"
    with pytest.raises((ValueError, PermissionError)):
        await logo.stage_logo(context, asset=asset, run=run, guard=guard)
    fixture.store.create_build_record.assert_not_awaited()
    fixture.registry.update_build_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_losing_logo_is_never_available_for_acceptance(selection):
    fixture, context, run, asset, _, _ = selection
    fixture.registry.update_build_status.side_effect = None
    fixture.registry.update_build_status.return_value = {"success": False}
    with pytest.raises(ValueError, match="changed while validating"):
        await logo.stage_logo(context, asset=asset, run=run, guard=lambda: None)
    (child,) = (record for record in fixture.artifacts.values() if record.parent_build_record_id)
    assert child.validation_status == "pending" and child.lifecycle_status == "archived"
    fixture.store.set_validation_status_for_build_record.assert_not_awaited()


def test_actual_brand_tool_schema_excludes_server_context(monkeypatch):
    from pathlib import Path

    from ag2.tools.final import FunctionTool

    from mozaiksai.core.workflow.agents import tools
    from mozaiksai.core.workflow.agents.factory import _wrap_tool_with_context

    workflow = "BrandAssetGeneratorWorkflow"
    folder = Path(__file__).resolve().parents[1] / "factory_app" / "workflows" / workflow
    monkeypatch.setattr(tools.workflow_manager, "resolve_workflow_path", lambda name: folder)
    (promote,) = tools.load_agent_tool_functions(workflow)["BrandDesignerAgent"]
    registered = FunctionTool.ensure_tool(_wrap_tool_with_context(promote, {}))
    assert registered.name == "promote_brand_asset"
    schema = registered.schema.function.parameters
    assert set(schema["properties"]) == {"asset_id", "role", "alt_text"}
    assert set(schema["required"]) == {"asset_id", "role"}


@pytest.mark.asyncio
async def test_logo_assignment_can_resume_after_release_interruption(selection, monkeypatch):
    from mozaiksai.hosts import studio

    fixture, context, run, asset, _, _ = selection
    release = fixture.store.set_validation_status_for_build_record
    original = release.side_effect
    release.side_effect = RuntimeError("interrupted after registry assigned the draft")
    with pytest.raises(RuntimeError, match="interrupted"):
        await logo.stage_logo(context, asset=asset, run=run, guard=lambda: None)
    child = fixture.artifacts[fixture.registry_record["current_build_run"]["artifact_version_id"]]
    assert child.validation_status == "pending"
    with pytest.raises(HTTPException) as gated:
        studio._enforce_artifact_validation_gate(child, action="accepted")
    assert gated.value.status_code == 409
    monkeypatch.setattr(studio, "_resolve_studio_artifact_scope", AsyncMock(return_value=(child.app_id, run[3])))
    monkeypatch.setattr(studio, "get_artifact_store", lambda: fixture.store)
    with pytest.raises(HTTPException) as refused:
        await studio.revalidate_refinement_draft(child.id, "project_1", principal=SimpleNamespace(user_id=run[3]))
    assert refused.value.status_code == 409 and "not eligible" in refused.value.detail

    release.side_effect = original
    result = await logo.stage_logo(context, asset=asset, run=run, guard=lambda: None)
    assert result["draft_build_record_id"] == child.id and child.validation_status == "passed"
    fixture.store.create_build_record.assert_awaited_once()


@pytest.mark.asyncio
async def test_superseded_logo_cannot_be_accepted_even_with_passed_validation(selection, monkeypatch):
    from contextlib import nullcontext

    from mozaiksai.hosts import studio

    fixture, context, run, asset, _, _ = selection
    result = await logo.stage_logo(context, asset=asset, run=run, guard=lambda: None)
    child = fixture.artifacts[result["draft_build_record_id"]]
    fixture.registry_record["current_build_run"]["artifact_version_id"] = "someone-selected-another-draft"
    monkeypatch.setattr(studio, "_resolve_studio_artifact_scope", AsyncMock(return_value=(child.app_id, run[3])))
    monkeypatch.setattr(studio, "get_artifact_store", lambda: fixture.store)
    monkeypatch.setattr(studio, "_get_app_registry_service", lambda: fixture.registry)
    monkeypatch.setattr(studio, "_verified_review_snapshots", AsyncMock(return_value={}))
    monkeypatch.setattr(studio, "_workspace_acceptance_guard", lambda *args: nullcontext())
    monkeypatch.setattr(studio, "_require_current_app_owned_pack_code", AsyncMock())
    monkeypatch.setattr(studio, "_require_current_build_in_lineage", AsyncMock())
    fixture.store.accept_build_record = AsyncMock()
    with pytest.raises(HTTPException) as refused:
        await studio.accept_build_artifact_version(
            child.id, build_registry_id="project_1", principal=SimpleNamespace(user_id=run[3]),
        )
    assert refused.value.status_code == 409 and "not the current build" in refused.value.detail
    fixture.store.accept_build_record.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", [
    "valid", "missing_parent_theme", "corrupt_parent_theme", "other_chat", "different_theme", "existing_other_summary",
])
async def test_theme_overlay_pins_only_its_owned_capture_in_the_child(selection, case):
    fixture, context, (_, host, chat, user), _, _, change = selection
    sequence, workflow = "app_theme_revision", "ThemeCapture"
    change.router_decision["workflow_sequence"] = sequence
    run = (workflow, host, chat, user)
    current = fixture.registry_record["current_build_run"]
    current.update(active_workflow_id=workflow, artifact_version_id="theme-1")
    if case == "missing_parent_theme":
        fixture.parent_files.pop(APP_THEME_PATH)
        fixture.save_parent()
    elif case == "corrupt_parent_theme":
        fixture.parent_files[APP_THEME_PATH] = "invalid theme JSON"
        fixture.save_parent()
    new_theme = {"theme": {"appearance": "dark"}, "assets": {"logo": None}}
    capture = fixture.parent.model_copy(deep=True, update={
        "id": "theme-1", "build_family": "theme_capture", "build_key": "theme_capture",
        "source_workflow": workflow, "source_chat_id": "other" if case == "other_chat" else chat,
    })
    capture.commit_metadata.metadata = {"summary_payload": {"theme_config": new_theme}}
    fixture.artifacts[capture.id] = capture
    if case == "existing_other_summary":
        current["artifact_version_id"] = "some-other-theme"

    async def apply(files):
        files[APP_THEME_PATH] = json.dumps({} if case == "different_theme" else new_theme)
        return {}

    async def stage():
        return await brand_revision.stage_brand_revision(
            context, run=run, workflow_sequence=sequence, selection={"theme_capture_id": capture.id},
            apply=apply, guard=lambda: None, theme_capture=capture,
        )

    if case not in {"valid", "missing_parent_theme"}:
        with pytest.raises(ValueError):
            await stage()
        fixture.store.create_build_record.assert_not_awaited()
        fixture.registry.update_build_status.assert_not_awaited()
        return

    child = await stage()
    assert child.id != capture.id and child.build_family == child.build_key == "app_bundle"
    assert child.parent_build_record_id == fixture.parent.id
    assert child.canonical_inputs_version == {**fixture.parent.canonical_inputs_version, "theme_capture": capture.id}
    assert child.commit_metadata.metadata["media_overlay"] == {"theme_capture_id": capture.id, "change_request_id": change.id}
    assert fixture.registry.update_build_status.await_args.kwargs["expected_artifact_version_id"] == capture.id
    files, diagnostics = await read_artifact_bundle(child, include_binary=True)
    assert not diagnostics and json.loads(files[APP_THEME_PATH]) == new_theme
    assert {p: v for p, v in files.items() if p != APP_THEME_PATH} == {
        p: v for p, v in fixture.parent_files.items() if p != APP_THEME_PATH
    }
    assert (await stage()).id == child.id
    fixture.store.create_build_record.assert_awaited_once()
