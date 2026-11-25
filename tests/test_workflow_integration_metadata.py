from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml

from factory_app.workflows._shared.workflow_integration import (
    apply_workflow_integration_context,
    extract_workflow_integration_metadata_from_bundle_entries,
    hydrate_workflow_integration_context_from_latest_artifact,
    normalize_workflow_integration_metadata,
    workflow_integration_metadata_from_artifact,
    workflow_integration_source_error,
    workflow_integration_source_pin,
    workflow_name_to_capability_id,
)
from mozaiksai.core.artifacts.models import BuildRecord, BuildRecordStatus
from tests.factory_context import factory_context


def test_explicit_empty_workflow_metadata_clears_stale_runtime_bindings():
    from mozaiksai.core.workflow.context.frozen import freeze

    metadata = extract_workflow_integration_metadata_from_bundle_entries([], bundle_name="Client Ledger")
    assert metadata["workflows"] == []
    assert metadata["primary_workflow"] is None
    context = {"generated_workflow_name": "OldWorkflow", "generated_workflow_capability_id": "old"}
    assert apply_workflow_integration_context(context, freeze(metadata)) is not None
    assert context["generated_workflow_name"] is None
    assert context["generated_workflow_capability_id"] is None
    assert normalize_workflow_integration_metadata({}) is None
    assert normalize_workflow_integration_metadata({"workflows": [{}]}) is None


def _metadata() -> dict:
    return {
        "contract_version": "1.0",
        "bundle_name": "TicketOps",
        "workflows": [
            {
                "workflow_name": "TicketBatchTriageWorkflow",
                "capability_id": "ticket-batch-triage-workflow",
                "startup_mode": "BackendOnly",
                "trigger_events": [
                    {
                        "event_type": "domain.support_ticket.batch_requested",
                        "source": "domain",
                        "capability_id": "ticket-batch-triage-workflow",
                        "description": "Requests parallel support-ticket triage.",
                    }
                ],
            }
        ],
    }


class _FakeArtifactStore:
    def __init__(self, artifact) -> None:
        self.artifact = artifact
        self.list_calls = []

    async def list_build_records(self, **kwargs):
        self.list_calls.append(dict(kwargs))
        return [self.artifact]


def _record(record_id, fields, *, family="workflow_bundle", inputs=None, app_id="generated-app"):
    return BuildRecord(
        id=record_id, app_id=app_id, build_family=family, build_key="TicketOps", version_number=1,
        lineage_root_id=record_id, canonical_inputs_version=inputs or {}, commit_metadata={"metadata": fields},
    )


def test_workflow_name_to_capability_id_normalizes_pascal_case() -> None:
    assert workflow_name_to_capability_id("TicketBatchTriageWorkflow") == "ticket-batch-triage-workflow"


def test_extract_workflow_integration_metadata_from_bundle_entries() -> None:
    metadata = extract_workflow_integration_metadata_from_bundle_entries(
        [
            {
                "workflow_name": "TicketBatchTriageWorkflow",
                "files": [
                    {
                        "filename": "orchestrator.yaml",
                        "content": """
schema_version: mozaiks.orchestrator.v1
workflow_name: TicketBatchTriageWorkflow
workflow_startup_mode: BackendOnly
triggers:
  - type: event
    event: domain.support_ticket.batch_requested
    capability_id: ticket-batch-triage-workflow
    description: Requests parallel support-ticket triage.
""",
                    }
                ],
            }
        ],
        bundle_name="TicketOps",
    )

    assert metadata is not None
    assert metadata["bundle_name"] == "TicketOps"
    assert metadata["primary_workflow"]["workflow_name"] == "TicketBatchTriageWorkflow"
    assert metadata["primary_workflow"]["capability_id"] == "ticket-batch-triage-workflow"
    assert metadata["primary_workflow"]["startup_mode"] == "BackendOnly"
    assert metadata["primary_workflow"]["trigger_events"] == [
        {
            "event_type": "domain.support_ticket.batch_requested",
            "source": "domain",
            "capability_id": "ticket-batch-triage-workflow",
            "description": "Requests parallel support-ticket triage.",
        }
    ]


def test_apply_workflow_integration_context_sets_canonical_handoff_fields() -> None:
    context: dict = {}

    normalized = apply_workflow_integration_context(context, _metadata())

    assert normalized is not None
    assert context["generated_workflow_name"] == "TicketBatchTriageWorkflow"
    assert context["generated_workflow_capability_id"] == "ticket-batch-triage-workflow"
    assert context["generated_workflow_startup_mode"] == "BackendOnly"
    assert context["generated_workflow_trigger_events"][0]["event_type"] == "domain.support_ticket.batch_requested"
    assert context["workflow_integration_metadata"]["primary_workflow"]["workflow_name"] == "TicketBatchTriageWorkflow"


def test_export_rejects_conflicting_capability_ids_for_one_workflow() -> None:
    orchestrator = {
        "workflow_name": "Review",
        "triggers": [
            {"type": "event", "event": "domain.orders.submitted", "capability_id": "review"},
            {"type": "event", "event": "domain.orders.escalated", "capability_id": "other-review"},
        ],
    }
    with pytest.raises(ValueError, match="Review.*one workflow capability_id.*other-review.*review"):
        extract_workflow_integration_metadata_from_bundle_entries([
            {"workflow_name": "Review", "files": [{"filename": "orchestrator.yaml", "content": yaml.safe_dump(orchestrator)}]}
        ])


def test_export_requires_an_explicit_capability_on_each_event_but_reads_older_metadata() -> None:
    event = "domain.orders.escalated"
    name = "OrderReview"
    orchestrator = {"workflow_name": name, "triggers": [
        {"type": "event", "event": "domain.orders.submitted", "capability_id": "review-order"},
        {"type": "event", "event": event},
    ]}
    with pytest.raises(ValueError, match="domain.orders.escalated.*requires its own capability_id"):
        extract_workflow_integration_metadata_from_bundle_entries([
            {"workflow_name": name, "files": [{"filename": "orchestrator.yaml", "content": yaml.safe_dump(orchestrator)}]}
        ])
    saved = normalize_workflow_integration_metadata({
        "workflow_name": name, "capability_id": "review-order", "trigger_events": [{"event_type": event}],
    })
    assert saved["workflows"][0]["trigger_events"][0]["capability_id"] == "review-order"


def test_exported_bindings_use_named_event_triggers_only() -> None:
    orchestrator = {
        "workflow_name": "Review",
        "triggers": [
            {"event": "domain.orders.submitted", "capability_id": "review-order"},
            {"type": "event", "capability_id": "family-default"},
            {"type": "chat", "event": "domain.orders.renamed", "capability_id": "chat-only"},
        ],
    }
    metadata = extract_workflow_integration_metadata_from_bundle_entries([
        {"workflow_name": "Review", "files": [{"filename": "orchestrator.yaml", "content": yaml.safe_dump(orchestrator)}]}
    ])
    workflow = metadata["workflows"][0]
    assert workflow["capability_id"] == "review-order"
    assert [event["event_type"] for event in workflow["trigger_events"]] == ["domain.orders.submitted"]


def test_hydrate_workflow_integration_context_from_latest_artifact() -> None:
    artifact = _record("av_workflow_bundle_1", {"summary_payload": {"workflow_integration_metadata": _metadata()}}, app_id="app-1")
    store = _FakeArtifactStore(artifact)
    context = factory_context({"app_id": "app-1"})

    result = asyncio.run(
        hydrate_workflow_integration_context_from_latest_artifact(
            context,
            artifact_store=store,
        )
    )

    assert result == {
        "status": "hydrated",
        "source": "workflow_bundle_artifact",
        "artifact_version_id": "av_workflow_bundle_1",
        "workflow_count": 1,
    }
    assert context["workflow_bundle_artifact_version_id"] == "av_workflow_bundle_1"
    assert context["generated_workflow_capability_id"] == "ticket-batch-triage-workflow"
    assert store.list_calls[0]["build_family"] == "workflow_bundle"


@pytest.mark.parametrize("pinned", [False, True], ids=["primary-export-projection", "pin-replaces-stale-catalog"])
def test_resumed_worker_hydrates_the_complete_bundle(pinned: bool) -> None:
    current = _metadata()
    current["workflows"].append({"workflow_name": "NotifyOwners", "capability_id": "notify-owners"})
    bundle = _record("bundle-v2", {"summary_payload": current})
    store = _FakeArtifactStore(bundle)
    store.get_build_record = AsyncMock(return_value=bundle)
    context = factory_context({"generated_workflow_name": "Old", "generated_workflow_capability_id": "old"})
    if pinned:
        context.update(artifact_kind="workflow_bundle", artifact_version_id="bundle-v2")
        context["workflow_integration_metadata"] = {**_metadata(), "source_artifact_version_id": "bundle-v1"}
    result = asyncio.run(hydrate_workflow_integration_context_from_latest_artifact(context, artifact_store=store))
    assert result["workflow_count"] == 2
    assert context["workflow_bundle_artifact_version_id"] == "bundle-v2"
    assert [w["capability_id"] for w in context["generated_workflow_integrations"]] == [
        "ticket-batch-triage-workflow", "notify-owners",
    ]
    if pinned:
        assert not store.list_calls
        store.get_build_record.assert_awaited_once_with(app_id="generated-app", build_record_id="bundle-v2")


def test_missing_pinned_workflow_bundle_never_substitutes_another_catalog() -> None:
    other = _record("other", {"summary_payload": _metadata()})
    store = _FakeArtifactStore(other)
    store.get_build_record = AsyncMock(return_value=None)
    context = factory_context({"artifact_kind": "workflow_bundle", "artifact_version_id": "missing"})
    apply_workflow_integration_context(context, {**_metadata(), "source_artifact_version_id": "old"})
    with pytest.raises(ValueError, match="missing.*no integration metadata"):
        asyncio.run(hydrate_workflow_integration_context_from_latest_artifact(context, artifact_store=store))
    assert not store.list_calls
    assert context["generated_workflow_integrations"] == []
    assert context["workflow_bundle_artifact_version_id"] is None


@pytest.mark.parametrize(("family", "fields"), [
    ("workflow_bundle", {"workflow_name": "PackWithTwoWorkflows", "workflow_integration_metadata": None}),
    ("workflow_bundle", {"summary_payload": {"name": "PackWithTwoWorkflows"}}),
    ("workflow_bundle", {"summary_payload": {"workflows": [{"name": "GuessedCapability"}]}}),
    ("workflow_bundle", {"workflows": [{"workflow_name": "Review", "capability_id": "review",
                                        "trigger_events": [{"event_type": "orders.future"}]}]}),
    ("app_bundle", {"summary_payload": {"name": "Invoice App"}}),
    ("app_bundle", {"workflow_integration_metadata": _metadata()}),
    ("workflow_bundle", {"workflow_integration_metadata": {**_metadata(), "source_artifact_version_id": "elsewhere"}}),
])
def test_pinned_record_requires_authentic_workflow_catalog(family, fields):
    record = _record("pin", fields, family=family)
    store = _FakeArtifactStore(record)
    store.get_build_record = AsyncMock(return_value=record)
    context = factory_context({"artifact_kind": "workflow_bundle", "artifact_version_id": "pin"})
    apply_workflow_integration_context(context, {**_metadata(), "source_artifact_version_id": "pin"})
    with pytest.raises(ValueError, match="pin.*no integration metadata"):
        asyncio.run(hydrate_workflow_integration_context_from_latest_artifact(context, artifact_store=store))
    assert store.get_build_record.await_count == 1 and not store.list_calls
    assert context["generated_workflow_integrations"] == [] and context["workflow_bundle_artifact_version_id"] is None


@pytest.mark.parametrize("workflows", [_metadata()["workflows"], []])
def test_saved_workflow_bundle_reads_exact_catalog_on_model_and_storage_document(workflows):
    record = _record("saved", {"workflow_name": "PackName", "workflow_integration_metadata": {"workflows": workflows}})
    for stored in [record, record.model_dump(by_alias=True)]:
        result = workflow_integration_metadata_from_artifact(stored)
        assert result["workflows"] == workflows
        assert result["source_artifact_version_id"] == "saved"


@pytest.mark.parametrize("status", list(BuildRecordStatus))
def test_pinned_workflow_lifecycle_matches_owned_revision_sources(status):
    record = _record("saved", {"workflow_integration_metadata": _metadata()})
    record.lifecycle_status = status
    for stored in [record, record.model_dump(by_alias=True)]:
        metadata = workflow_integration_metadata_from_artifact(stored)
        if status in {BuildRecordStatus.ARCHIVED, BuildRecordStatus.DELETED}:
            assert metadata is None
        else:
            assert metadata["source_artifact_version_id"] == "saved"


@pytest.mark.parametrize("input_id", ["earlier-workflow", None])
def test_app_refinement_reads_workflow_lineage_from_selected_parent(input_id):
    inputs = {"workflow_bundle": input_id} if input_id else {}
    baseline = _record("app-v2", {}, family="app_bundle", inputs=inputs)
    bundle = _record("earlier-workflow", {"workflow_integration_metadata": _metadata()})
    store = _FakeArtifactStore(_record("other-latest-workflow", {"workflow_integration_metadata": _metadata()}))
    store.get_build_record = AsyncMock(side_effect=[baseline, bundle])
    context = factory_context({"artifact_kind": "app_bundle", "artifact_version_id": "app-v2",
                               "artifact_version_refs": {"workflow_bundle": "unrelated"}})
    assert workflow_integration_source_error(context)
    result = asyncio.run(hydrate_workflow_integration_context_from_latest_artifact(context, artifact_store=store))
    assert result["status"] == ("hydrated" if input_id else "skipped")
    assert context["workflow_bundle_artifact_version_id"] == input_id
    assert workflow_integration_source_error(context) is None and workflow_integration_source_pin(context) == input_id
    assert not store.list_calls


def test_corrupt_explicit_app_source_leaves_no_verified_baseline():
    store = _FakeArtifactStore(None)
    store.get_build_record = AsyncMock(return_value=_record("wrong", {}, family="app_bundle"))
    context = factory_context({"artifact_kind": "app_bundle", "artifact_version_id": "app-v2",
                               "workflow_integration_source_app": {"build_record_id": "app-v2", "workflow_bundle_id": None}})
    with pytest.raises(ValueError, match="Cannot resolve workflow inputs"):
        asyncio.run(hydrate_workflow_integration_context_from_latest_artifact(context, artifact_store=store))
    assert workflow_integration_source_error(context)


def test_same_build_workflow_output_precedes_revision_baseline():
    record = _record("new", {"workflow_integration_metadata": _metadata()})
    store = _FakeArtifactStore(record)
    store.get_build_record = AsyncMock(return_value=record)
    context = factory_context({"artifact_kind": "app_bundle", "artifact_version_id": "old-app",
                               "revision_source_artifact_version_id": "old-app",
                               "upstream_workflow_bundle_artifact_version_id": "new"})
    result = asyncio.run(hydrate_workflow_integration_context_from_latest_artifact(context, artifact_store=store))
    assert result["artifact_version_id"] == "new" and workflow_integration_source_pin(context) == "new"
    assert workflow_integration_source_error(context) is None
    store.get_build_record.assert_awaited_once_with(app_id="generated-app", build_record_id="new")


@pytest.mark.asyncio
async def test_new_workflow_result_is_selected_by_the_runtime_build_identity(monkeypatch):
    from mozaiksai.core.workflow.context.schema import ContextVariableDefinition
    from mozaiksai.core.workflow.context.variables import _load_data_reference_value
    from tests.test_context_data_reference_ordering import _Collection, _install_collection

    name = "upstream_workflow_bundle_artifact_version_id"
    path = Path(__file__).resolve().parents[1] / "factory_app/workflows/AppGenerator/context_variables.yaml"
    definition = ContextVariableDefinition.model_validate(yaml.safe_load(path.read_text(encoding="utf-8"))["definitions"][name])
    collection = _Collection([{"_id": "new", "version_number": 1, "created_at": 1}])
    _install_collection(monkeypatch, collection)
    result = await _load_data_reference_value(
        name=name, definition=definition, context=factory_context(), app_id="factory-test", default_database_name=None,
    )
    assert result == "new" and definition.authorization
    assert collection.query == {
        "app_id": "generated-app", "build_family": "workflow_bundle", "source_workflow": "AgentGenerator",
        "commit_metadata.metadata.build_id": "build-test", "commit_metadata.metadata.build_registry_id": "registry-test",
        "lifecycle_status": {"$in": ["current", "draft"]},
    }


def test_hydration_only_reuses_direct_bundle_files_without_a_pin():
    context = factory_context({"workflow_integration_metadata": {**_metadata(), "source": "workflow_bundle_files"}})
    store = _FakeArtifactStore(None)
    result = asyncio.run(hydrate_workflow_integration_context_from_latest_artifact(context, artifact_store=store))
    assert result["source"] == "context_variables" and not store.list_calls
    context["artifact_kind"] = "workflow_bundle"
    context["artifact_version_id"] = "pin"
    store.get_build_record = AsyncMock(return_value=_record("wrong-id", {"workflow_integration_metadata": _metadata()}))
    with pytest.raises(ValueError, match="pin.*no integration metadata"):
        asyncio.run(hydrate_workflow_integration_context_from_latest_artifact(context, artifact_store=store))
    assert context["generated_workflow_integrations"] == []
