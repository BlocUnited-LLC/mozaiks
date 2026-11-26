import hashlib
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from factory_app.workflows._shared.export_inputs import export_input_versions
from factory_app.workflows._shared.workflow_integration import read_verified_workflow_archive
from factory_app.workflows.AgentGenerator.tools.generate_and_download import (
    _register_workflow_bundle_artifact_version,
)
from factory_app.workflows.AppGenerator.tools.generate_and_download import (
    _register_app_bundle_artifact_version,
)
from mozaiksai.core.artifacts.models import BuildRecord
from tests.factory_export_fixture import draft_inputs

ROOT = Path(__file__).resolve().parents[1] / "factory_app" / "workflows"


@pytest.mark.parametrize("workflow,writer", [("AgentGenerator", _register_workflow_bundle_artifact_version),
                                            ("AppGenerator", _register_app_bundle_artifact_version)])
@pytest.mark.asyncio
async def test_export_uses_actual_draft_predecessors_over_unrelated_current(monkeypatch, tmp_path, workflow, writer):
    records, context = draft_inputs()
    archive = tmp_path / "bundle.zip"
    archive.write_bytes(b"actual output")
    store = AsyncMock()
    store.get_build_record.side_effect = lambda *, app_id, build_record_id: records[build_record_id]
    store.create_build_record.side_effect = lambda **kwargs: BuildRecord(
        id="candidate", version_number=1, lineage_root_id="candidate", **kwargs,
    )
    async def latest(*, artifact_kinds, **kwargs):
        assert set(artifact_kinds) <= {"theme_capture"}
        return {family: f"current-{family}" for family in artifact_kinds}
    monkeypatch.setattr("mozaiksai.core.artifacts.resolve_latest_artifact_version_refs", latest)
    await writer(app_id="app", user_id="owner", workflow_name=workflow, chat_id="chat", bundle_name="bundle",
                 context_variables=context, zip_path=archive, artifact_store=store)
    saved = next(call.kwargs for call in store.create_build_record.await_args_list if call.kwargs["build_family"] in {"app_bundle", "workflow_bundle"})
    pins = saved["canonical_inputs_version"]
    assert {family: pins[family] for family in ("design_docs", "subscription_contract")} == {
        "design_docs": "draft-design_docs", "subscription_contract": "draft-subscription_contract"}


@pytest.mark.parametrize("damage", ["missing", "scope", "manifest", "projection", "subscription", "review", "source"])
@pytest.mark.asyncio
async def test_export_refuses_unprovable_same_build_inputs(damage, monkeypatch):
    records, context = draft_inputs()
    monkeypatch.setenv("MOZAIKS_WORKFLOWS_PATH", str(ROOT))
    design, subscription = records.values()
    if damage == "missing":
        del context["design_docs_artifact_version_id"]
    elif damage == "scope":
        subscription.commit_metadata.author_user_id = "different-owner"
    elif damage == "manifest":
        design.files_manifest[0].sha256 = "0" * 64
    elif damage == "projection":
        context["experience_spec_document"] += "other design"
    elif damage == "subscription":
        context["subscription_contract"] = {"contract_required": True}
    elif damage == "review":
        context["run_build_binding"]["build_id"] = "another-build"
    else:
        design.commit_metadata.source_chat_id = "wrong-chat"
    store = AsyncMock()
    store.get_build_record.side_effect = lambda *, app_id, build_record_id: records[build_record_id]
    with pytest.raises(ValueError):
        await export_input_versions(context, workflow="AgentGenerator", app_id="app", user_id="owner", artifact_store=store)
    store.list_build_records.assert_not_awaited()


def test_workflow_archive_digest_and_file_existence(tmp_path):
    archive = tmp_path / "pack.zip"
    data = b"recorded bytes"
    archive.write_bytes(data)
    record = BuildRecord(id="workflow", app_id="app", build_family="workflow_bundle", build_key="pack", version_number=1,
                         lineage_root_id="workflow", commit_metadata={"metadata": {"artifact_path": str(archive)}},
                         files_manifest=[{"path": "pack/pack.zip", "content_type": "application/zip",
                                         "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}])
    assert read_verified_workflow_archive(record) == data
    archive.write_bytes(data.upper())
    with pytest.raises(ValueError, match="digest"):
        read_verified_workflow_archive(record)
    archive.unlink()
    with pytest.raises(ValueError, match="Could not read"):
        read_verified_workflow_archive(record)
