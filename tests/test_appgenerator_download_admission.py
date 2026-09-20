"""Download and export consume the same artifacts admitted by Factory tools."""

import zipfile
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from factory_app.workflows.AppGenerator.tools import (
    app_validation,
    export_app_code,
    generate_and_download,
)
from factory_app.workflows.AppGenerator.tools.code_file_utils import save_generated_code
from factory_app.workflows.AppGenerator.tools.task_integrity import (
    artifact_snapshot_digest,
    task_allowed_paths,
)
from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager
from tests.test_appplan_materialization_acceptance import _Context, _materialize_plan_bundle

_SCHEMAS = "modules/reports/backend/schemas.py"
_SERVICE = "modules/reports/backend/service.py"


def _select_repair(context, task_id, request_id):
    tasks = context.get("app_task_batch_items")
    task = next(item for item in tasks if item["task_id"] == task_id)
    context.set("bundle_repair_target", task["initial_agent"])
    context.set("bundle_repair_result", {"active": {
        "task_id": task_id,
        "target_agent": task["initial_agent"],
        "allowed_paths": task_allowed_paths(task, tasks),
        "request_id": request_id,
        "status": "selected",
    }})


def _external_boundaries(monkeypatch, tmp_path, historical_outputs):
    history = AsyncMock(return_value=historical_outputs)
    monkeypatch.setattr(AG2PersistenceManager, "gather_latest_agent_jsons", history)
    artifact = AsyncMock(return_value=None)
    monkeypatch.setattr(generate_and_download, "_register_app_bundle_artifact_version", artifact)
    registry = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(generate_and_download.AppRegistryService, "update_build_status", registry)
    ui = AsyncMock(return_value={"status": "success", "action": "export_to_github"})
    monkeypatch.setattr(generate_and_download, "use_ui_tool", ui)
    monkeypatch.setattr(generate_and_download, "_resolve_generated_artifacts_root", lambda: tmp_path / "download")
    monkeypatch.setattr(generate_and_download, "get_latest_workflow_export", AsyncMock(return_value=None))
    monkeypatch.setattr(export_app_code, "get_latest_workflow_export", AsyncMock(return_value=None))
    external_export = AsyncMock(return_value=SimpleNamespace(
        success=True, repo_url="https://example.invalid/offline/app", base_commit_sha="offline",
        job_id=None, repo_full_name="offline/app", workflow_run_url=None, deployment_url=None,
        model_dump=lambda: {"success": True},
    ))
    monkeypatch.setattr(export_app_code.export_to_github_tool, "execute", external_export)
    snapshot_store = AsyncMock(return_value="offline-snapshot")
    monkeypatch.setattr(export_app_code, "persist_snapshot", snapshot_store)
    monkeypatch.setattr(export_app_code, "record_workflow_export", AsyncMock())
    return SimpleNamespace(
        history=history, artifact=artifact, registry=registry, ui=ui,
        external_export=external_export, snapshot_store=snapshot_store,
    )


async def _download_and_assert_snapshot(context, boundaries):
    result = await generate_and_download.generate_and_download(
        {}, "Review the admitted app bundle.", context_variables=context,
    )
    assert result["status"] == "success", result
    boundaries.external_export.assert_awaited_once()
    boundaries.snapshot_store.assert_awaited_once()
    accepted = context.get("app_bundle_acceptance_result")
    assert accepted["passed"] is True, accepted
    final_files = context.get("generated_files")
    assert boundaries.ui.await_args.kwargs["payload"]["generated_files"] == final_files
    archive_path = Path(result["bundle_zip"])
    with zipfile.ZipFile(archive_path) as archive:
        prefix = archive_path.stem + "/"
        archived = {
            item.filename.removeprefix(prefix): archive.read(item).decode("utf-8")
            for item in archive.infolist()
        }
    assert archived == final_files
    assert accepted["snapshot_digest"] == artifact_snapshot_digest(context, archived)
    assert export_app_code.resolve_export_gate(context, files=archived)["allow_export"] is True
    assert boundaries.external_export.await_args.kwargs["bundle_path"] == str(archive_path)
    return archived


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["write", "delete"])
async def test_rejected_service_candidate_cannot_reenter_download_from_agent_history(monkeypatch, tmp_path, operation):
    files, _, _, context, _ = await _materialize_plan_bundle(tmp_path=tmp_path / "fixture")
    evidence = deepcopy(context.get("app_task_batch_results"))
    _select_repair(context, "task_reports_services", "reject-service")
    candidate = {"code_files": [{"filename": _SERVICE, "content": files[_SERVICE] + "# Unadmitted change\n"}]}
    if operation == "write":
        candidate["code_files"].append({"filename": _SCHEMAS, "content": files[_SCHEMAS] + "# Foreign change\n"})
    else:
        candidate["deleted_files"] = [_SCHEMAS]
    before = {key: deepcopy(context.get(key)) for key in ("generated_files", "code_files", "deleted_files")}
    context.set("structured_output", candidate)

    saved = save_generated_code(context)

    assert saved["status"] == "rejected"
    assert _SCHEMAS in saved["error"]
    assert context.get("bundle_repair_result")["active"]["status"] == "rejected"
    for key, value in before.items():
        assert context.get(key) == value
    boundaries = _external_boundaries(monkeypatch, tmp_path, {"ServiceAgent": candidate})

    archived = await _download_and_assert_snapshot(context, boundaries)

    assert archived[_SCHEMAS] == files[_SCHEMAS]
    assert archived[_SERVICE] == files[_SERVICE]
    assert context.get("app_task_batch_results") == evidence
    boundaries.history.assert_not_awaited()


@pytest.mark.asyncio
async def test_later_owned_repair_survives_older_filtered_foreign_readback(monkeypatch, tmp_path):
    files, _, _, context, _ = await _materialize_plan_bundle(tmp_path=tmp_path / "fixture")
    evidence = deepcopy(context.get("app_task_batch_results"))
    repaired_service = files[_SERVICE] + "# Admitted service correction\n"
    service_candidate = {"code_files": [
        {"filename": _SERVICE, "content": repaired_service},
        {"filename": _SCHEMAS, "content": files[_SCHEMAS]},
    ]}
    _select_repair(context, "task_reports_services", "service-correction")
    context.set("structured_output", service_candidate)

    saved = save_generated_code(context)

    assert saved["saved_files"] == [_SERVICE]
    assert context.get("bundle_repair_result")["active"]["status"] == "responded"
    repaired_schemas = files[_SCHEMAS] + "# Admitted model correction\n"
    model_candidate = {"code_files": [{"filename": _SCHEMAS, "content": repaired_schemas}]}
    _select_repair(context, "task_reports_models", "model-correction")
    context.set("structured_output", model_candidate)
    assert save_generated_code(context)["saved_files"] == [_SCHEMAS]
    boundaries = _external_boundaries(monkeypatch, tmp_path, {
        "ModelAgent": model_candidate, "ServiceAgent": service_candidate,
    })

    archived = await _download_and_assert_snapshot(context, boundaries)

    assert archived[_SCHEMAS] == repaired_schemas
    assert archived[_SERVICE] == repaired_service
    for path, content in files.items():
        if path not in {_SCHEMAS, _SERVICE, "app.json"}:
            assert archived[path] == content
    assert context.get("app_task_batch_results") == evidence
    boundaries.history.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["planned_schema", "all_files"])
async def test_missing_admitted_artifacts_cannot_be_refilled_from_history_or_disk(monkeypatch, tmp_path, missing):
    files, app_root, _, context, _ = await _materialize_plan_bundle(tmp_path=tmp_path / "fixture")
    retained = {} if missing == "all_files" else {path: content for path, content in files.items() if path != _SCHEMAS}
    context.set("generated_files", retained)
    context.set("code_files", [{"filename": path, "content": content} for path, content in retained.items()])
    context.set("generated_app_dir", str(app_root))
    assert (app_root / _SCHEMAS).is_file()
    boundaries = _external_boundaries(monkeypatch, tmp_path, {"ModelAgent": {
        "code_files": [{"filename": path, "content": content} for path, content in files.items()],
    }})

    result = await generate_and_download.generate_and_download(
        {}, "Review the admitted app bundle.", context_variables=context,
    )

    assert result["status"] == "error", result
    boundaries.history.assert_not_awaited()
    boundaries.artifact.assert_not_awaited()
    boundaries.registry.assert_not_awaited()
    boundaries.ui.assert_not_awaited()
    boundaries.external_export.assert_not_awaited()
    assert not list((tmp_path / "download").rglob("*.zip"))
    if missing == "planned_schema":
        accepted = result["app_bundle_acceptance_result"]
        assert accepted["passed"] is False
        assert _SCHEMAS in str(accepted["planned_completeness"])


@pytest.mark.asyncio
@pytest.mark.parametrize("task_results", [None, {}])
async def test_missing_execution_evidence_cannot_admit_history_during_acceptance(monkeypatch, tmp_path, task_results):
    files, _, _, context, _ = await _materialize_plan_bundle(tmp_path=tmp_path / "fixture")
    context.set("generated_files", deepcopy(files))
    context.set("code_files", [])
    if task_results is None:
        context.data.pop("app_task_batch_results")
    else:
        context.set("app_task_batch_results", task_results)
    history = AsyncMock(return_value={"ServiceAgent": {
        "code_files": [{"filename": _SCHEMAS, "content": files[_SCHEMAS] + "# Foreign write\n"}],
        "deleted_files": ["app.json"],
    }})
    monkeypatch.setattr(AG2PersistenceManager, "gather_latest_agent_jsons", history)

    accepted = await app_validation.run_app_bundle_acceptance_gate(context_variables=context)

    assert accepted["passed"] is False
    assert accepted["planned_completeness"]["passed"] is False
    assert context.get("generated_files") == files
    history.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_empty_validation_input_does_not_fall_back_to_context_history_or_disk(monkeypatch, tmp_path):
    (tmp_path / "app.json").write_text('{"appName":"Old disk snapshot"}', encoding="utf-8")
    context = _Context({
        "chat_id": "empty-validation-chat",
        "generated_files": {"app.json": '{"appName":"Admitted snapshot"}'},
        "generated_app_dir": str(tmp_path),
    })
    before = deepcopy(context.get("generated_files"))
    history = AsyncMock(return_value={"ServiceAgent": {
        "code_files": [{"filename": "app.json", "content": '{"appName":"Historical output"}'}],
    }})
    monkeypatch.setattr(AG2PersistenceManager, "gather_latest_agent_jsons", history)

    result = await app_validation.validate_app_build(
        files={}, validation_strategy="skip", context_variables=context,
    )

    assert result["validation_status"] == "failed"
    assert result["errors"] == ["No files provided/resolved for app validation"]
    assert context.get("generated_files") == before
    history.assert_not_awaited()
