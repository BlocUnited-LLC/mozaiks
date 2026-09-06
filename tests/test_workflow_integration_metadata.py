from __future__ import annotations

import asyncio
from types import SimpleNamespace

from factory_app.workflows._shared.workflow_integration import (
    apply_workflow_integration_context,
    extract_workflow_integration_metadata_from_bundle_entries,
    hydrate_workflow_integration_context_from_latest_artifact,
    workflow_name_to_capability_id,
)


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


def test_hydrate_workflow_integration_context_from_latest_artifact() -> None:
    artifact = SimpleNamespace(
        id="av_workflow_bundle_1",
        commit_metadata=SimpleNamespace(
            metadata={
                "summary_payload": {
                    "workflow_integration_metadata": _metadata(),
                }
            }
        ),
    )
    store = _FakeArtifactStore(artifact)
    context = {"app_id": "app-1"}

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
