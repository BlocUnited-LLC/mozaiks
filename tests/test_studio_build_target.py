from __future__ import annotations

import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from mozaiksai.core.session.build_binding import RunBuildBinding


@pytest.fixture
def studio(monkeypatch):
    from mozaiksai.hosts import studio

    monkeypatch.setattr(studio, "_resolve_studio_scope", lambda *args, **kwargs: ("factory", "owner"))
    service = SimpleNamespace(get_app_record=AsyncMock(return_value={"app": {
        "build_registry_id": "registry_tracker", "app_id": "tracker", "chat_app_id": "factory",
    }}))
    monkeypatch.setattr(studio, "_get_app_registry_service", lambda: service)
    return studio, service


@pytest.mark.asyncio
async def test_artifact_scope_resolves_owned_target_not_host(studio):
    module, service = studio
    result = await module._resolve_studio_artifact_scope(None, build_registry_id="registry_tracker")
    assert result == ("tracker", "owner")
    service.get_app_record.assert_awaited_once_with(build_registry_id="registry_tracker", owner_user_id="owner")


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["get_app_overview", "get_build_surface"])
async def test_build_summaries_use_owned_target_without_changing_host_scope(studio, monkeypatch, endpoint):
    module, service = studio
    monkeypatch.setattr(module, "get_missing_studio_surfaces", lambda root: [])
    monkeypatch.setattr(module, "build_app_overview_summary", lambda *args, **kwargs: {
        "app": kwargs["app_record"], "studio": {},
    })
    monkeypatch.setattr(module, "load_build_state_from_db", AsyncMock(return_value={}))
    monkeypatch.setattr(module, "build_build_section", lambda *args: {})
    result = await getattr(module, endpoint)(build_registry_id="registry_tracker", principal=None)
    assert result["app"]["app_id"] == "tracker"
    assert service.get_app_record.await_args_list[0].kwargs == {
        "build_registry_id": "registry_tracker", "owner_user_id": "owner",
    }
    assert service.get_app_record.await_args_list[1].kwargs == {
        "app_id": "tracker", "owner_user_id": "owner",
    }
    service.get_app_record.reset_mock()
    service.get_app_record.return_value = {"app": None}
    with pytest.raises(HTTPException) as caught:
        await getattr(module, endpoint)(build_registry_id="foreign_registry", principal=None)
    assert caught.value.status_code == 404
    service.get_app_record.assert_awaited_once_with(
        build_registry_id="foreign_registry", owner_user_id="owner",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("record", [None, {"app_id": "tracker", "chat_app_id": "foreign_host"}])
async def test_artifact_scope_rejects_missing_or_foreign_target(studio, record):
    module, service = studio
    service.get_app_record.return_value = {"app": record}
    with pytest.raises(HTTPException) as caught:
        await module._resolve_studio_artifact_scope(None, build_registry_id="registry_tracker")
    assert caught.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("selector", [None, "../tracker", ""])
async def test_artifact_scope_never_defaults_to_host(studio, selector):
    module, service = studio
    with pytest.raises(HTTPException) as caught:
        await module._resolve_studio_artifact_scope(None, build_registry_id=selector)
    assert caught.value.status_code == 400
    service.get_app_record.assert_not_awaited()


@pytest.mark.asyncio
async def test_download_checks_artifact_and_binding(studio, monkeypatch, tmp_path):
    module, _ = studio
    archive = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("tracker/app.json", '{"appId":"tracker"}')
    binding = RunBuildBinding(build_registry_id="registry_tracker", target_app_id="tracker", build_id="build_one", phase="genesis")
    metadata = {**binding.model_dump(), "artifact_path": str(archive)}
    version = SimpleNamespace(commit_metadata=SimpleNamespace(metadata=metadata))
    store = SimpleNamespace(get_build_record=AsyncMock(return_value=version))
    monkeypatch.setattr(module, "get_artifact_store", lambda: store)
    response = await module.download_build_artifact("version_one", "registry_tracker", principal=None)
    assert response.path == archive
    assert response.filename == "tracker-version_one.zip"
    store.get_build_record.assert_awaited_once_with(app_id="tracker", build_record_id="version_one")
    metadata["target_app_id"] = "foreign_app"
    with pytest.raises(HTTPException) as caught:
        await module.download_build_artifact("version_one", "registry_tracker", principal=None)
    assert caught.value.status_code == 409


def test_promotion_does_not_touch_factory(studio, monkeypatch, tmp_path):
    module, _ = studio
    factory = tmp_path / "factory" / "app"
    factory.mkdir(parents=True)
    manifest = factory / "app.json"
    manifest.write_text('{"appId":"factory"}', encoding="utf-8")
    monkeypatch.setattr(module, "resolve_app_root", lambda: factory)
    monkeypatch.setenv("MOZAIKS_WORKSPACES_PATH", str(tmp_path / "workspaces"))
    version = SimpleNamespace(app_id="tracker", id="version_one", build_family="app_bundle")
    target = module._resolve_bundle_restore_target(version)
    assert target == tmp_path / "workspaces" / "tracker" / "version_one"
    archive = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("tracker/app.json", '{"appId":"tracker"}')
        output.writestr("tracker/workflows/Inbox/orchestrator.yaml", "name: Inbox\n")
        output.writestr("tracker/Dockerfile", "FROM scratch\n")
        output.writestr("tracker/requirements.txt", "mozaiks\n")
    module._restore_bundle_to_target(zip_path=archive, target_dir=target, workspace_layout=True)
    assert (target / "app" / "app.json").is_file()
    assert (target / "workflows" / "Inbox" / "orchestrator.yaml").is_file()
    assert (target / "Dockerfile").is_file()
    assert (target / "requirements.txt").is_file()
    assert manifest.read_text(encoding="utf-8") == '{"appId":"factory"}'


def test_promotion_rejects_root_inside_host(studio, monkeypatch, tmp_path):
    module, _ = studio
    monkeypatch.setattr(module, "resolve_app_root", lambda: tmp_path)
    monkeypatch.setenv("MOZAIKS_WORKSPACES_PATH", str(tmp_path / "workspaces"))
    with pytest.raises(HTTPException) as caught:
        module._resolve_bundle_restore_target(SimpleNamespace(app_id="tracker", id="version_one", build_family="app_bundle"))
    assert caught.value.status_code == 409


@pytest.mark.asyncio
async def test_refinement_rejects_foreign_target_before_any_models(studio, monkeypatch):
    module, service = studio
    service.get_app_record.return_value = {"app": None}
    harness = SimpleNamespace(route_refinement_request=AsyncMock())
    monkeypatch.setattr(module, "get_orchestration_control_harness", lambda: harness)
    with pytest.raises(HTTPException) as caught:
        await module.trigger_workflow(module.WorkflowTriggerRequest(
            trigger_source="refinement", build_registry_id="foreign_registry",
            trigger_payload={"refinement_request": {"raw_user_request": "Change the title"}},
        ), principal=None)
    assert caught.value.status_code == 404
    harness.route_refinement_request.assert_not_awaited()


def test_refinement_request_round_trips_execution_and_target_identity():
    from mozaiksai.control_plane.implementations.refinement_router import RefinementRequest
    from mozaiksai.core.artifacts.models import RefinementRequestPayload

    request = RefinementRequest(
        build_family="app_bundle", app_id="factory", target_app_id="tracker", user_id="owner",
        raw_user_request="Change the title", build_record_id="version_one",
    )
    stored = RefinementRequestPayload.model_validate(request.model_dump())
    assert stored.app_id == "factory"
    assert stored.target_app_id == "tracker"
    assert stored.user_id == "owner"


@pytest.mark.asyncio
@pytest.mark.parametrize("validation_status", ["failed", "skipped", "pending"])
async def test_inline_completion_requires_saved_validation(studio, monkeypatch, validation_status):
    from mozaiksai.core.artifacts import ArtifactValidationStatus

    module, service = studio
    service.update_build_status = AsyncMock()
    binding = RunBuildBinding(build_registry_id="registry_tracker", target_app_id="tracker", build_id="run_one", phase="refinement")
    version = SimpleNamespace(
        validation_status=ArtifactValidationStatus(validation_status),
        commit_metadata=SimpleNamespace(metadata=binding.model_dump()),
    )
    store = SimpleNamespace(get_build_record=AsyncMock(return_value=version))
    monkeypatch.setattr(module, "get_artifact_store", lambda: store)
    with pytest.raises(ValueError, match="has not passed validation"):
        await module._complete_inline_refinement(
            binding=binding, user_id="owner",
            result=SimpleNamespace(status="validated", metadata={"build_record_id": "version_one"}),
        )
    service.update_build_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_inline_failure_cannot_mutate_a_newer_run(studio):
    module, service = studio
    service.update_build_status = AsyncMock(return_value={"success": False})
    binding = RunBuildBinding(build_registry_id="registry_tracker", target_app_id="tracker", build_id="old_run", phase="refinement")
    await module._fail_inline_refinement(binding=binding, user_id="owner")
    service.update_build_status.assert_awaited_once_with(
        owner_user_id="owner", build_registry_id="registry_tracker",
        expected_build_id="old_run", status="needs_revision",
    )


def test_workspace_layout_is_not_nested_again(studio, tmp_path):
    module, _ = studio
    archive = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("app/app.json", '{"appId":"tracker"}')
        output.writestr("app/config/auth.yaml", "version: 1\n")
        output.writestr("Dockerfile", "FROM scratch\n")
    target = tmp_path / "workspace"
    module._restore_bundle_to_target(zip_path=archive, target_dir=target, workspace_layout=True)
    assert (target / "app/app.json").is_file()
    assert not (target / "app/app").exists()
    assert (target / "Dockerfile").is_file()


def test_inline_baseline_reports_unsafe_archive_entries(studio, tmp_path):
    module, _ = studio
    archive = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("app.json", "{}")
        output.writestr("../escape.txt", "bad")
    files, skipped = module._decode_text_bundle_entries(archive)
    assert files == {"app.json": "{}"}
    assert skipped == ["../escape.txt: unsafe_path"]
