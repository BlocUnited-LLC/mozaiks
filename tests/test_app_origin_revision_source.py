from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from factory_app.workflows._shared.context_graph import load_context_graph_context as graph
from factory_app.workflows._shared.context_graph.hook_context_graph import (
    inject_context_graph_context,
)
from factory_app.workflows.AppGenerator.tools import generate_and_download
from factory_app.workflows.AppGenerator.tools import hydrate_app_revision_context as revision
from factory_app.workflows.AppGenerator.tools.assemble_app_tasks import _verified_revision_source
from mozaiksai.control_plane.contracts import build_acceptance_evidence
from mozaiksai.core import artifacts
from mozaiksai.core.artifacts import content_store
from mozaiksai.core.artifacts.models import BuildRecord
from mozaiksai.core.workflow.context.adapter import create_context_container
from tests.test_appgenerator_revision_baseline import baseline as baseline
from tests.test_workflow_dependency_graph_context import _agent, _snapshot


def _app_origin(baseline):
    artifact = baseline.artifact
    artifact.validation_status = "passed"
    artifact.commit_metadata.metadata["acceptance"] = build_acceptance_evidence(
        artifact, accepted_by="owner", accepted_at="2026-11-07T12:00:00Z", transition="studio_accept",
    )
    baseline.context.update(
        workflow_sequence="design_revision", artifact_kind="app_bundle", artifact_version_id="new-workflow",
        upstream_workflow_bundle_artifact_version_id="new-workflow",
        refinement_request_meta={"build_family": "app_bundle", "build_key": "app_bundle", "build_record_id": artifact.id},
    )


@pytest.mark.asyncio
async def test_app_origin_design_revision_hydrates_selected_archive_and_preserves_export(baseline, monkeypatch, tmp_path):
    _app_origin(baseline)
    context = create_context_container(initial=baseline.context)
    result = await revision.hydrate_app_revision_context(context)

    assert result["artifact_version_id"] == baseline.artifact.id
    assert context.get("revision_source_artifact_version_id") == baseline.artifact.id
    assert await _verified_revision_source({}, context) == baseline.files
    assert context.get("artifact_version_id") == "new-workflow"
    assert context.get("upstream_workflow_bundle_artifact_version_id") == "new-workflow"
    archive = tmp_path / "bundle.zip"
    archive.write_bytes(baseline.content.get_bundle.return_value)
    context.set("app_bundle_acceptance_result", {
        "workflow_integration": {"passed": True, "metadata": {"source_artifact_version_id": "new-workflow"}},
    })

    async def create_record(**fields):
        return BuildRecord(id="new-app-draft", version_number=2, lineage_root_id=baseline.artifact.id, **fields)

    monkeypatch.setattr(artifacts, "resolve_latest_artifact_version_refs", AsyncMock(return_value={"design_docs": "new-design"}))
    monkeypatch.setattr(content_store, "get_artifact_content_store", lambda: SimpleNamespace(backend_name="local"))
    monkeypatch.setattr(generate_and_download, "_register_greenfield_app_context_for_bundle", AsyncMock())
    draft = await generate_and_download._register_app_bundle_artifact_version(
        app_id="customer-app", user_id="owner", workflow_name="AppGenerator", chat_id="chat",
        bundle_name="bundle", zip_path=archive, context_variables=context,
        artifact_store=SimpleNamespace(create_build_record=create_record),
    )
    assert draft.parent_build_record_id == baseline.artifact.id
    assert draft.canonical_inputs_version == {"design_docs": "new-design", "workflow_bundle": "new-workflow"}
    assert context.get("artifact_version_id") == draft.id
    assert revision.revision_source_artifact_id(context) == baseline.artifact.id
    assert context.get("run_build_binding")["build_id"] == "new-build"


@pytest.mark.asyncio
@pytest.mark.parametrize("fault,error", [
    ("foreign_owner", "revision_baseline_owner_mismatch"),
    ("unaccepted", "revision_baseline_not_accepted"),
    ("missing_reference", "revision_baseline_binding_missing"),
    ("corrupt_archive", "artifact_bundle_digest_mismatch"),
])
async def test_app_origin_design_revision_requires_selected_accepted_archive(baseline, fault, error):
    _app_origin(baseline)
    if fault == "foreign_owner":
        baseline.artifact.commit_metadata.author_user_id = "another-owner"
    elif fault == "unaccepted":
        baseline.artifact.commit_metadata.metadata.pop("acceptance")
    elif fault == "missing_reference":
        baseline.context["refinement_request_meta"].pop("build_record_id")
    else:
        baseline.content.get_bundle.return_value += b"tampered"

    with pytest.raises(ValueError, match=error):
        await revision.hydrate_app_revision_context(baseline.context)
    assert baseline.context.get("revision_first_generation") is False
    assert "generated_files" not in baseline.context


@pytest.mark.asyncio
async def test_app_origin_rejects_a_conflicting_pin_before_reading_any_source(baseline, monkeypatch):
    from factory_app.workflows._shared.revision_app_source import read_accepted_design_app_files

    _app_origin(baseline)
    baseline.context["revision_source_artifact_version_id"] = "another-app-version"
    source = AsyncMock()
    monkeypatch.setattr(graph, "load_context_graph_for_tool", source)
    for read in [graph.load_context_graph_context, revision.hydrate_app_revision_context, read_accepted_design_app_files]:
        with pytest.raises(ValueError, match="revision_baseline_selection_mismatch"):
            await read(baseline.context)
    baseline.store.get_build_record.assert_not_awaited()
    source.assert_not_awaited()
    assert "generated_files" not in baseline.context


@pytest.mark.asyncio
@pytest.mark.parametrize("workflow,export_kind", [("AgentGenerator", "design_docs"), ("AppGenerator", "workflow_bundle")])
async def test_app_origin_graph_tracks_request_after_export_without_a_hydration_pin(monkeypatch, workflow, export_kind):
    snapshot = _snapshot("selected-app")
    source = AsyncMock(return_value=snapshot)
    analyze = AsyncMock(return_value=None)
    monkeypatch.setattr(graph, "load_context_graph_for_tool", source)
    monkeypatch.setattr(graph, "DependencyGraphAgent", lambda: SimpleNamespace(analyze=analyze))
    metadata = {"build_family": "app_bundle", "build_key": "app_bundle", "build_record_id": "selected-app"}
    context = create_context_container(initial={
        "app_id": "factory", "user_id": "owner", "workflow_name": workflow, "build_mode": "revision",
        "workflow_sequence": "design_revision", "artifact_kind": export_kind, "artifact_key": export_kind,
        "artifact_version_id": "upstream-export", "refinement_request_meta": metadata,
        "refinement_request": "change how tasks are listed",
        "run_build_binding": {"target_app_id": "customer", "build_registry_id": "registry", "build_id": "new-build", "phase": "refinement"},
    })
    await graph.load_context_graph_context(context)
    for invoked in [source, analyze]:
        selector = invoked.await_args.kwargs["context"]
        assert (selector.build_family, selector.build_key, selector.build_record_id) == ("app_bundle", "app_bundle", "selected-app")
    agent = _agent(context.snapshot())
    inject_context_graph_context(agent, [])
    assert "modules/tasks/backend/service.py" in agent.system_message
    assert "context_graph_source_changed" not in agent.system_message
    assert context.get("context_graph_pack")["bundle_digest"] == snapshot["provenance"]["bundle_digest"]
