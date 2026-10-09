import hashlib
import importlib
import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from factory_app.workflows._shared.artifact_bundle import read_artifact_bundle
from factory_app.workflows.AppGenerator.tools import (
    app_validation,
    export_app_code,
    generate_and_download,
)
from factory_app.workflows.AppGenerator.tools import hydrate_app_revision_context as revision
from factory_app.workflows.AppGenerator.tools.assemble_app_tasks import assemble_app_tasks
from factory_app.workflows.AppGenerator.tools.generate_and_download import (
    _build_generated_files_manifest,
)
from factory_app.workflows.AppGenerator.tools.task_integrity import artifact_snapshot_digest
from mozaiksai.core.artifacts import content_store
from mozaiksai.core.artifacts.models import BuildRecord, resolve_canonical_bundle_entry
from mozaiksai.core.workflow.context.adapter import create_context_container


def _replace_archive(baseline, additions):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, content in {**baseline.files, **additions}.items():
            archive.writestr(path if path.startswith("other/") else "bundle/" + path, content)
    raw = buffer.getvalue()
    baseline.content.get_bundle.return_value = raw
    baseline.artifact.files_manifest[0].sha256 = hashlib.sha256(raw).hexdigest()


@pytest.fixture
def baseline(monkeypatch):
    files = {
        "app.json": json.dumps({"appId": "customer-app", "appName": "Contact Desk"}),
        "modules/records/backend/handler.py": "UNCHANGED\n",
        "modules/records/backend/service.py": "ORIGINAL\n",
        "ui/pages/records.yaml": "page_type: record_list\n",
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, content in files.items():
            archive.writestr("bundle/" + path, content)
    raw = buffer.getvalue()
    content = SimpleNamespace(backend_name="memory", get_bundle=AsyncMock(return_value=raw))
    artifact = BuildRecord(
        id="artifact_1", app_id="customer-app", build_family="app_bundle",
        build_key="app_bundle", version_number=1, lineage_root_id="artifact_1",
        lifecycle_status="stale",
        files_manifest=[{
            "path": "bundle/bundle.zip", "sha256": hashlib.sha256(raw).hexdigest(),
            "content_type": "application/zip",
        }],
        commit_metadata={"author_user_id": "owner", "metadata": {
            "target_app_id": "customer-app", "build_registry_id": "registry_1",
            "build_id": "original-build", "phase": "genesis", "bundle_name": "bundle",
            "content_ref": "immutable-archive", "content_backend": "memory",
            "workspace_dir": "never-read-the-mutable-workspace",
        }},
    )
    store = SimpleNamespace(get_build_record=AsyncMock(return_value=artifact))
    monkeypatch.setattr(revision, "get_artifact_store", lambda: store)
    monkeypatch.setattr(content_store, "get_artifact_content_store", lambda: content)
    context = {
        "app_id": "factory-host", "user_id": "owner", "build_mode": "revision",
        "workflow_sequence": "app_revision", "artifact_version_id": "artifact_1",
        "run_build_binding": {
            "target_app_id": "customer-app", "build_registry_id": "registry_1",
            "build_id": "new-build", "phase": "refinement",
        },
    }
    return SimpleNamespace(files=files, context=context, artifact=artifact, store=store, content=content)


@pytest.mark.asyncio
@pytest.mark.parametrize("container", [False, True])
async def test_revision_reads_verified_archive_and_preserves_resumed_changes(baseline, container):
    context = baseline.context
    context["generated_files"] = {"modules/records/backend/service.py": "REPAIRED\n"}
    context["deleted_files"] = ["ui/pages/records.yaml"]
    if container:
        context = create_context_container(initial=context)
    result = await revision.hydrate_app_revision_context(context)
    assert result["status"] == "hydrated"
    assert context.get("generated_files") == {
        "app.json": baseline.files["app.json"],
        "modules/records/backend/handler.py": "UNCHANGED\n",
        "modules/records/backend/service.py": "REPAIRED\n",
    }
    baseline.store.get_build_record.assert_awaited_once_with(
        app_id="customer-app", build_record_id="artifact_1",
    )
    baseline.content.get_bundle.assert_awaited_once_with("immutable-archive")
    assert context.get("app_id") == "factory-host"


@pytest.mark.asyncio
async def test_scoped_assembly_hydrates_verified_archive_and_preserves_baseline(baseline):
    page = {
        "schema_version": "mozaiks.app_page.v1", "name": "records", "route": "/records",
        "title": "Records", "page_type": "settings", "layout": "full-width",
        "sections": [{"id": "header", "primitive": "PageHeader", "config": {"title": "Records"}}],
    }
    context = create_context_container(initial={
        **baseline.context,
        "app_build_plan": {"pages": [page], "build_tasks": [{
            "task_id": "records", "task_type": "page_bundle", "owned_paths": ["ui/pages/records.yaml"],
        }]},
        "app_task_batch_results": {"records": {"manifest": None, "pages": [page]}},
    })
    result = await assemble_app_tasks(context_variables=context)
    files = {entry["filename"]: entry["content"] for entry in result["code_files"]}
    baseline.content.get_bundle.assert_awaited_once_with("immutable-archive")
    assert yaml.safe_load(files["ui/pages/records.yaml"]) == page
    for path in baseline.files.keys() - {"ui/pages/records.yaml"}:
        assert files[path] == baseline.files[path]
    assert context.get("generated_files")["app.json"] == baseline.files["app.json"]


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [
    "missing_id", "missing_artifact", "wrong_target", "wrong_owner", "wrong_registry",
    "wrong_family", "archived", "deleted", "tampered", "missing_principal",
])
async def test_bad_revision_baseline_fails_without_mutating_generated_files(baseline, fault):
    context = baseline.context
    if fault == "missing_id":
        context["artifact_version_id"] = None
    elif fault == "missing_artifact":
        baseline.store.get_build_record.return_value = None
    elif fault == "wrong_target":
        baseline.artifact.app_id = "foreign-app"
    elif fault == "wrong_owner":
        baseline.artifact.commit_metadata.author_user_id = "foreign-owner"
    elif fault == "wrong_registry":
        baseline.artifact.commit_metadata.metadata["build_registry_id"] = "foreign-registry"
    elif fault == "wrong_family":
        baseline.artifact.build_family = "workflow_bundle"
    elif fault in {"archived", "deleted"}:
        baseline.artifact.lifecycle_status = fault
    elif fault == "tampered":
        baseline.content.get_bundle.return_value += b"tampering"
    else:
        context.pop("user_id")
    with pytest.raises(ValueError):
        await revision.hydrate_app_revision_context(context)
    assert "generated_files" not in context


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,sequence", [
    ("initial", "genesis"), ("revision", "conceptual_replan"), ("revision", "full_rebuild"),
])
async def test_new_or_explicit_rebuild_does_not_copy_old_implementation(baseline, mode, sequence):
    baseline.context.update(build_mode=mode, workflow_sequence=sequence)
    assert (await revision.hydrate_app_revision_context(baseline.context))["status"] == "skipped"
    baseline.store.get_build_record.assert_not_awaited()


@pytest.mark.asyncio
async def test_revision_hydrates_large_text_and_keeps_binary_out_of_agent_context(baseline):
    large_svg = "<svg>" + "x" * 2_550_000 + "</svg>"
    _replace_archive(baseline, {
        "brand/favicon.svg": large_svg,
        "brand/logo.png": b"\x89PNG" + bytes(2_100_000),
        "brand/font.otf": b"OTTO" + bytes(300),
    })

    result = await revision.hydrate_app_revision_context(baseline.context)

    assert result["status"] == "hydrated"
    assert "brand/favicon.svg" not in baseline.context["generated_files"]
    assert "brand/logo.png" not in baseline.context["generated_files"]
    assert "brand/font.otf" not in baseline.context["generated_files"]
    assert (await revision.read_bound_revision_binary_assets(baseline.context)) == {
        "brand/favicon.svg": large_svg.encode("utf-8"),
        "brand/logo.png": b"\x89PNG" + bytes(2_100_000),
        "brand/font.otf": b"OTTO" + bytes(300),
    }
    security_files, _ = await read_artifact_bundle(baseline.artifact, retain_svg_text=True)
    assert security_files["brand/favicon.svg"] == large_svg
    assert "brand/logo.png" not in security_files


@pytest.mark.asyncio
async def test_revision_rejects_archive_entries_outside_its_canonical_root(baseline):
    _replace_archive(baseline, {"other/foreign.py": "VALUE = 1\n"})

    with pytest.raises(ValueError, match="outside_bundle_root"):
        await revision.hydrate_app_revision_context(baseline.context)
    assert "generated_files" not in baseline.context


@pytest.mark.asyncio
@pytest.mark.parametrize("path,reason", [
    ("brand/broken.svg", "non_text_source"),
    ("brand/too-large.png", "file_too_large"),
])
async def test_revision_rejects_malformed_or_oversized_opaque_assets(baseline, path, reason):
    content = b"\xff" if path.endswith(".svg") else bytes(8_000_001)
    _replace_archive(baseline, {path: content})

    with pytest.raises(ValueError, match=reason):
        await revision.hydrate_app_revision_context(baseline.context)
    assert "generated_files" not in baseline.context


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [
    "brand/x:payload.png", "brand/CON.png", "brand/COM¹.png", "brand/LPT².png",
])
async def test_revision_rejects_nonportable_opaque_asset_name(baseline, path):
    _replace_archive(baseline, {path: b"\x89PNG"})

    with pytest.raises(ValueError, match="unsafe_path"):
        await revision.hydrate_app_revision_context(baseline.context)
    assert "generated_files" not in baseline.context


@pytest.mark.asyncio
async def test_revision_rejects_casefold_colliding_opaque_asset_names(baseline):
    _replace_archive(baseline, {
        "brand/Logo.png": b"first", "brand/logo.png": b"second",
    })

    with pytest.raises(ValueError, match="duplicate_path"):
        await revision.hydrate_app_revision_context(baseline.context)
    assert "generated_files" not in baseline.context


def _download_context(baseline, *, deleted=(), replacement=None):
    files = dict(baseline.files)
    if replacement is not None:
        files["brand/logo.png"] = replacement
    return create_context_container(initial={
        **baseline.context,
        "chat_id": "revision-chat", "app_name": "RevisedApp",
        "generated_files": files,
        "deleted_files": list(deleted),
    })


def _stub_download_boundaries(monkeypatch, tmp_path):
    monkeypatch.setenv("MOZAIKS_GENERATED_ARTIFACTS_PATH", str(tmp_path / "generated"))
    monkeypatch.setattr(generate_and_download, "_inject_agent_context_env", AsyncMock())
    async def accept_source(*, files, context_variables, **_kwargs):
        try:
            _, evidence = await revision.revision_asset_evidence(context_variables, files)
        except (OSError, ValueError) as exc:
            return {"passed": False, "status": "failed", "bundle_scan": {"errors": [str(exc)]}}
        context_variables.set("revision_source_artifact_version_id", evidence["source_artifact_version_id"])
        context_variables.set("revision_asset_evidence", evidence)
        context_variables.set("generated_files", files)
        context_variables.set("app_build_plan", {"build_tasks": []})
        context_variables.set("app_validation_status", "passed")
        context_variables.set("integration_tests_passed", True)
        context_variables.set("app_bundle_acceptance_status", "passed")
        context_variables.set("app_bundle_acceptance_result", {
            "snapshot_digest": artifact_snapshot_digest(context_variables, files),
        })
        return {"passed": True, "status": "passed", "bundle_scan": {"errors": []}}

    monkeypatch.setattr(generate_and_download, "run_app_bundle_acceptance_gate", accept_source)
    monkeypatch.setattr(generate_and_download, "resolve_export_gate", lambda *_args, **_kwargs: {
        "allow_export": True, "reasons": [],
    })
    register = generate_and_download._register_app_bundle_artifact_version
    monkeypatch.setattr(generate_and_download, "_register_app_bundle_artifact_version", AsyncMock(return_value=None))
    registry = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(generate_and_download.AppRegistryService, "update_build_status", registry)
    ui = AsyncMock(return_value={"status": "completed", "action": "continue"})
    monkeypatch.setattr(generate_and_download, "use_ui_tool", ui)
    return SimpleNamespace(registry=registry, ui=ui, register=register)


@pytest.mark.asyncio
async def test_revision_final_zip_preserves_exact_binary_and_canonical_manifest(baseline, monkeypatch, tmp_path):
    binary = b"\x89PNG" + bytes(2_100_000)
    svg = b"<svg>" + b"x" * 2_550_000 + b"</svg>"
    _replace_archive(baseline, {"brand/logo.png": binary, "brand/favicon.svg": svg})
    context = _download_context(baseline)
    boundaries = _stub_download_boundaries(monkeypatch, tmp_path)

    result = await generate_and_download.generate_and_download({}, "Ready", context_variables=context)

    assert result["status"] == "success", result
    archive_path = Path(result["bundle_zip"])
    with zipfile.ZipFile(archive_path) as archive:
        entries = set(archive.namelist())
        assert "RevisedApp/app.json" in entries
        assert archive.read("RevisedApp/brand/favicon.svg") == svg
        assert archive.read("RevisedApp/brand/logo.png") == binary
    manifest = _build_generated_files_manifest(
        bundle_name="RevisedApp", app_dir=Path(result["bundle_dir"]), written_paths=result["files_written"],
    )
    binary_manifest = [entry for entry in manifest if entry["path"] == "RevisedApp/brand/logo.png"]
    assert binary_manifest == [{
        "path": "RevisedApp/brand/logo.png", "sha256": hashlib.sha256(binary).hexdigest(),
        "size_bytes": len(binary), "content_type": "application/octet-stream",
    }]
    recorded = []

    async def create_record(**kwargs):
        recorded.append(kwargs)
        return BuildRecord(
            id="revised-record", app_id=kwargs["app_id"], build_family=kwargs["build_family"],
            build_key=kwargs["build_key"], version_number=2, lineage_root_id="artifact_1",
            files_manifest=kwargs["files_manifest"], commit_metadata=kwargs["commit_metadata"],
        )

    artifacts = importlib.import_module("mozaiksai.core.artifacts")
    monkeypatch.setattr(artifacts, "resolve_latest_artifact_version_refs", AsyncMock(return_value={}))
    monkeypatch.setattr(generate_and_download, "_register_greenfield_app_context_for_bundle", AsyncMock())
    record = await boundaries.register(
        app_id="customer-app", user_id="owner", workflow_name="AppGenerator", chat_id="revision-chat",
        bundle_name="RevisedApp", zip_path=archive_path, app_dir=Path(result["bundle_dir"]),
        written_paths=result["files_written"], context_variables=context,
        artifact_store=SimpleNamespace(create_build_record=create_record),
    )
    canonical = resolve_canonical_bundle_entry(record)
    assert canonical.path == "RevisedApp/RevisedApp.zip"
    assert canonical.sha256 == hashlib.sha256(archive_path.read_bytes()).hexdigest()
    assert recorded[0]["parent_build_record_id"] == "artifact_1"
    assert [entry.model_dump() for entry in record.files_manifest[1:]] == manifest
    boundaries.registry.assert_awaited_once()
    boundaries.ui.assert_awaited_once()


@pytest.mark.asyncio
async def test_revision_opaque_deletion_fails_acceptance_before_zip(baseline, monkeypatch, tmp_path):
    _replace_archive(baseline, {"brand/logo.png": b"\x89PNG"})
    context = _download_context(baseline, deleted=["brand/logo.png"])
    boundaries = _stub_download_boundaries(monkeypatch, tmp_path)

    result = await generate_and_download.generate_and_download({}, "Ready", context_variables=context)

    assert result["status"] == "error" and result["outcome"] == "blocked"
    assert "revision_binary_deletion_unowned" in result["bundle_errors"][0]
    assert not list(tmp_path.rglob("*.zip"))
    boundaries.registry.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["late_tombstone", "changed_source_bytes"])
async def test_revision_rejects_source_changes_after_acceptance_before_zip(
    baseline, monkeypatch, tmp_path, mutation,
):
    _replace_archive(baseline, {"brand/logo.png": b"\x89PNG-original"})
    context = _download_context(baseline)
    boundaries = _stub_download_boundaries(monkeypatch, tmp_path)
    accepted = generate_and_download.run_app_bundle_acceptance_gate

    async def mutate_after_acceptance(**kwargs):
        result = await accepted(**kwargs)
        if mutation == "late_tombstone":
            context.set("deleted_files", ["brand/logo.png"])
        else:
            _replace_archive(baseline, {"brand/logo.png": b"\x89PNG-changed"})
        return result

    monkeypatch.setattr(generate_and_download, "run_app_bundle_acceptance_gate", mutate_after_acceptance)
    monkeypatch.setattr(generate_and_download, "resolve_export_gate", export_app_code.resolve_export_gate)

    result = await generate_and_download.generate_and_download({}, "Ready", context_variables=context)

    assert result["status"] == "error" and result["outcome"] == "blocked"
    assert not list(tmp_path.rglob("*.zip"))
    boundaries.registry.assert_not_awaited()


@pytest.mark.asyncio
async def test_revision_acceptance_binds_source_hashes_and_tombstones(baseline, monkeypatch):
    from scripts.smoke_appgenerator_live_acceptance import (
        build_appgenerator_acceptance_files,
        default_workflow_integration,
    )
    from tests.test_app_validation_strategy import _accept_support_tasks, _Context

    integration = default_workflow_integration()
    files = build_appgenerator_acceptance_files(integration)
    manifest = json.loads(files["app.json"])
    manifest["appId"] = "customer-app"
    files["app.json"] = json.dumps(manifest)
    baseline.files = files
    binary = b"\x89PNG" + bytes(11)
    _replace_archive(baseline, {"brand/logo.png": binary})
    context = _Context({**baseline.context, "generated_files": files, "deleted_files": []})
    context.set("generated_workflow_name", integration["workflow_name"])
    context.set("generated_workflow_capability_id", integration["capability_id"])
    context.set("generated_workflow_startup_mode", integration["startup_mode"])
    context.set("generated_workflow_trigger_events", integration["trigger_events"])
    context.set("app_validation_status", "passed")
    context.set("app_validation_strategy_used", "docker")
    _accept_support_tasks(context, files)
    monkeypatch.setattr(app_validation.app_runtime_smoke, "run_app_runtime_smoke", AsyncMock(return_value={
        "status": "passed", "passed": True, "failed_tests": [], "checks": [],
    }))

    accepted = await app_validation.run_app_bundle_acceptance_gate(files=files, context_variables=context)

    assert accepted["passed"] is True, accepted
    assert context.get("revision_asset_evidence") == {
        "source_artifact_version_id": "artifact_1",
        "opaque_assets": [{
            "path": "brand/logo.png", "sha256": hashlib.sha256(binary).hexdigest(), "size_bytes": len(binary),
        }],
        "deleted_files": [],
    }
    assert export_app_code.resolve_export_gate(context)["allow_export"] is True

    context.set("artifact_version_id", "artifact_2")
    reentered = await app_validation.run_app_bundle_acceptance_gate(files=files, context_variables=context)
    assert reentered["passed"] is True, reentered
    assert export_app_code.resolve_export_gate(context)["allow_export"] is True
    assert all(call.kwargs["build_record_id"] == "artifact_1" for call in baseline.store.get_build_record.await_args_list)

    context.set("deleted_files", ["modules/support_tickets/backend/handler.py"])
    assert export_app_code.resolve_export_gate(context)["allow_export"] is False
    context.set("deleted_files", [])
    evidence = context.get("revision_asset_evidence")
    evidence["opaque_assets"][0]["sha256"] = "0" * 64
    assert export_app_code.resolve_export_gate(context)["allow_export"] is False

    context.set("deleted_files", ["brand/logo.png"])
    denied = await app_validation.run_app_bundle_acceptance_gate(files=files, context_variables=context)
    assert denied["passed"] is False
    assert any("revision_binary_deletion_unowned" in error for error in denied["bundle_scan"]["errors"])


@pytest.mark.asyncio
async def test_revision_source_survives_register_then_export(baseline, monkeypatch, tmp_path):
    binary = b"\x89PNG" + bytes(23)
    _replace_archive(baseline, {"brand/logo.png": binary})
    context = _download_context(baseline)
    boundaries = _stub_download_boundaries(monkeypatch, tmp_path)
    monkeypatch.setattr(generate_and_download, "_register_app_bundle_artifact_version", boundaries.register)
    monkeypatch.setattr(generate_and_download, "resolve_export_gate", export_app_code.resolve_export_gate)
    monkeypatch.setattr(generate_and_download, "_register_greenfield_app_context_for_bundle", AsyncMock())
    boundaries.ui.return_value = {"status": "completed", "action": "export_to_github"}

    async def create_record(**kwargs):
        assert kwargs["parent_build_record_id"] == "artifact_1"
        return BuildRecord(
            id="artifact_2", app_id=kwargs["app_id"], build_family=kwargs["build_family"],
            build_key=kwargs["build_key"], version_number=2, lineage_root_id="artifact_1",
            files_manifest=kwargs["files_manifest"], commit_metadata=kwargs["commit_metadata"],
        )

    baseline.store.create_build_record = create_record
    artifacts = importlib.import_module("mozaiksai.core.artifacts")
    monkeypatch.setattr(artifacts, "get_artifact_store", lambda: baseline.store)
    monkeypatch.setattr(artifacts, "resolve_latest_artifact_version_refs", AsyncMock(return_value={}))
    monkeypatch.setattr(export_app_code, "get_latest_workflow_export", AsyncMock(return_value=None))
    provider = AsyncMock(return_value=SimpleNamespace(success=False, model_dump=lambda: {"success": False}))
    monkeypatch.setattr(export_app_code.export_to_github_tool, "execute", provider)

    result = await generate_and_download.generate_and_download({}, "Ready", context_variables=context)

    assert context.get("artifact_version_id") == "artifact_2"
    assert context.get("revision_source_artifact_version_id") == "artifact_1"
    assert export_app_code.resolve_export_gate(context)["allow_export"] is True
    assert result["deployment"].get("blocked") is not True, result
    provider.assert_awaited_once()
    assert baseline.store.get_build_record.await_count >= 3
    assert all(call.kwargs["build_record_id"] == "artifact_1" for call in baseline.store.get_build_record.await_args_list)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["text_replacement", "tampered_source", "wrong_owner"])
async def test_revision_finalization_fails_before_writing_on_invalid_binary_source(
    baseline, monkeypatch, tmp_path, fault,
):
    _replace_archive(baseline, {"brand/logo.png": b"\x89PNG"})
    context = _download_context(baseline, replacement="forbidden" if fault == "text_replacement" else None)
    boundaries = _stub_download_boundaries(monkeypatch, tmp_path)
    if fault == "tampered_source":
        baseline.content.get_bundle.return_value += b"tampered"
    elif fault == "wrong_owner":
        baseline.artifact.commit_metadata.author_user_id = "someone-else"

    result = await generate_and_download.generate_and_download({}, "Ready", context_variables=context)

    assert result["status"] == "error" and result["outcome"] == "blocked"
    assert not list(tmp_path.rglob("*.zip"))
    boundaries.registry.assert_not_awaited()
    boundaries.ui.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [None, "changed_binary", "missing_binary", "extra_file"])
async def test_export_verifies_revision_binary_against_bound_source_before_provider_call(
    baseline, monkeypatch, tmp_path, fault,
):
    binary = b"\x89PNG" + bytes(10)
    _replace_archive(baseline, {"brand/logo.png": binary})
    context = _download_context(baseline)
    _, evidence = await revision.revision_asset_evidence(context, baseline.files)
    context.set("revision_source_artifact_version_id", evidence["source_artifact_version_id"])
    context.set("revision_asset_evidence", evidence)
    archive_path = tmp_path / "RevisedApp.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        for path, content in baseline.files.items():
            archive.writestr("RevisedApp/" + path, content)
        if fault != "missing_binary":
            archive.writestr("RevisedApp/brand/logo.png", b"different" if fault == "changed_binary" else binary)
        if fault == "extra_file":
            archive.writestr("RevisedApp/unapproved.txt", "extra")
    monkeypatch.setattr(export_app_code, "resolve_export_gate", lambda *_args, files=None, **_kwargs: {
        "allow_export": files is None or files == baseline.files,
        "reasons": [] if files is None or files == baseline.files else ["text mismatch"],
        "app_bundle_acceptance_status": "passed", "app_validation_status": "passed",
        "app_validation_strategy_used": "docker", "integration_tests_passed": True,
    })
    provider = AsyncMock(return_value=SimpleNamespace(success=False, model_dump=lambda: {"success": False}))
    monkeypatch.setattr(export_app_code.export_to_github_tool, "execute", provider)
    monkeypatch.setattr(export_app_code, "get_latest_workflow_export", AsyncMock(return_value=None))

    result = await export_app_code.export_app_code_to_github(
        app_id="customer-app", bundle_path=str(archive_path), context_variables=context,
    )

    if fault is None:
        assert result.get("blocked") is not True
        provider.assert_awaited_once()
    else:
        assert result["blocked"] is True
        provider.assert_not_awaited()
