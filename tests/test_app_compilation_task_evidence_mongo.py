"""Original execution evidence survives real artifact persistence and fresh checks."""

from __future__ import annotations

import hashlib
import zipfile
from copy import deepcopy
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from factory_app.workflows.AppGenerator.tools import generate_and_download as download
from factory_app.workflows.AppGenerator.tools.app_validation import run_app_bundle_acceptance_gate
from mozaiksai.core.validation.compilation_inputs import resolve_app_compilation_inputs
from tests.test_app_compilation_inputs import _settle_five_tasks
from tests.test_app_compilation_inputs import saved as _saved
from tests.test_build_record_indexes_mongo import records as _records

saved = _saved
records = _records


@pytest.mark.asyncio
async def test_five_task_factory_registration_and_child_replay_keep_fresh_validation(saved, records, monkeypatch):
    memory, context, original = saved
    store, database = records
    files = {**await _settle_five_tasks(context, monkeypatch), "app.json": '{"appId":"target"}'}
    original_results = deepcopy(context["app_task_batch_results"])
    archive_path = Path(original.commit_metadata.metadata["artifact_path"])
    with zipfile.ZipFile(archive_path, "w") as archive:
        for path, content in files.items():
            archive.writestr(f"bundle/{path}", content)
    await database.ArtifactVersions.insert_one(memory.records["design"].model_dump(by_alias=True))
    context["app_bundle_acceptance_status"] = "passed"
    monkeypatch.setattr("mozaiksai.core.artifacts.resolve_latest_artifact_version_refs", AsyncMock(return_value={}))
    parent = await download._register_app_bundle_artifact_version(
        app_id="target", user_id="owner", workflow_name="AppGenerator", chat_id="original-chat",
        bundle_name="bundle", zip_path=archive_path,
        context_variables=context, artifact_store=store,
    )
    assert parent is not None
    resolve_args = {"app_id": "target", "owner_user_id": "owner", "build_registry_id": "registry", "artifact_store": store}
    approval = await resolve_app_compilation_inputs(build_record_id=parent.id, **resolve_args)
    assert approval is not None
    assert approval.context_for("target")["app_task_batch_results"] == original_results
    files["modules/records/backend/part0.py"] = "# authorized candidate edit — 星\n"
    child_path = archive_path.with_name("child.zip")
    with zipfile.ZipFile(child_path, "w") as archive:
        for path, content in files.items():
            archive.writestr(f"bundle/{path}", content)
    child_sha = hashlib.sha256(child_path.read_bytes()).hexdigest()
    metadata = deepcopy(parent.commit_metadata.metadata)
    metadata.update(build_id="refinement", phase="refinement",
                    artifact_path=str(child_path), compilation_inputs=approval.for_child(child_sha))
    child = await store.create_build_record(
        app_id="target", build_family="app_bundle", build_key="app_bundle", source_workflow="control_plane_coding",
        parent_build_record_id=parent.id, files_manifest=[{
            "path": "bundle/bundle.zip", "sha256": child_sha, "size_bytes": child_path.stat().st_size,
            "content_type": "application/zip",
        }],
        canonical_inputs_version=parent.canonical_inputs_version,
        commit_metadata={"author_user_id": "owner", "metadata": metadata},
    )
    child_approval = await resolve_app_compilation_inputs(build_record_id=child.id, **resolve_args)
    assert child_approval is not None
    assert child_approval.context_for("target")["app_task_batch_results"] == original_results
    assert await database.ArtifactVersions.count_documents({"build_family": "app_bundle"}) == 2
    for override in ({"owner_user_id": "foreign"}, {"app_id": "foreign"}, {"build_registry_id": "foreign"}):
        with pytest.raises(ValueError):
            await resolve_app_compilation_inputs(build_record_id=child.id, **{**resolve_args, **override})

    # Candidate files can legitimately change while historical task evidence remains exact.
    files["config/auth.yaml"] = "authorization:\n  roles:\n    - id: member\n      grants: [records.read]\n"
    candidate_context = child_approval.context_for("target")
    assert "app_bundle_acceptance_result" not in candidate_context
    rejected = await run_app_bundle_acceptance_gate(files=files, context_variables=candidate_context,
                                                   capability_packs=candidate_context["capability_packs"])
    assert candidate_context["integration_test_result"]["planned_completeness"] == {
        "passed": True, "diagnostics": [], "failed_tests": [],
    }
    assert rejected["passed"] is False
    assert any("config/auth.yaml" in str(item) for item in rejected["failed_tests"])
    assert not any(item.get("test", "").startswith("TASK_") for item in rejected["failed_tests"])
    assert child_approval.context_for("target")["app_task_batch_results"] == original_results

    del files["modules/records/backend/part1.py"]
    missing_context = child_approval.context_for("target")
    missing = await run_app_bundle_acceptance_gate(files=files, context_variables=missing_context,
                                                  capability_packs=missing_context["capability_packs"])
    assert any(item.get("test") == "PLANNED_ARTIFACT_MISSING"
               and item.get("path") == "modules/records/backend/part1.py" for item in missing["failed_tests"])
