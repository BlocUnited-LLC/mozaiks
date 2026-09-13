import hashlib
import io
import json
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from factory_app.workflows._shared import artifact_bundle
from factory_app.workflows.AppGenerator.tools import hydrate_app_revision_context as revision
from factory_app.workflows.AppGenerator.tools.assemble_app_tasks import assemble_app_tasks
from mozaiksai.core.artifacts.models import BuildRecord
from mozaiksai.core.workflow.context.adapter import create_context_container


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
    monkeypatch.setattr(artifact_bundle, "get_artifact_content_store", lambda: content)
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
