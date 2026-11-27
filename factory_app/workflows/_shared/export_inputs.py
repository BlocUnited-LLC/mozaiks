"""Resolve the saved design and subscription inputs for Factory exports."""

from __future__ import annotations

from typing import Any

from factory_app.workflows._shared.platform.build_target import require_build_binding
from factory_app.workflows.DesignDocs.tools.save_design_doc import verify_generator_design_inputs
from mozaiksai.core.artifacts.models import BuildRecord
from mozaiksai.core.artifacts.summary_artifacts import verified_summary_payload
from mozaiksai.core.workflow.generator_support.module_entitlement_gates import (
    resolve_subscription_contract,
)
from mozaiksai.core.workflow.pack.config import get_workflow_sequence, load_global_pack_graph

_PRODUCERS = {"design_docs": "DesignDocs", "subscription_contract": "SubscriptionContractDesigner"}


def verify_subscription_design_input(subscription: BuildRecord, design_id: str) -> None:
    """A verified subscription can only follow the immutable design it actually records."""
    if subscription.canonical_inputs_version.get("design_docs") != design_id:
        raise ValueError("The saved subscription did not use the pinned design; rerun SubscriptionContractDesigner from that design")


async def load_pinned_input_record(
    *, artifact_store: Any, family: str, record_id: str, app_id: str, user_id: str,
) -> BuildRecord:
    producer = _PRODUCERS[family]
    record: BuildRecord | None = await artifact_store.get_build_record(app_id=app_id, build_record_id=record_id)
    if (record is None or record.id != record_id or record.app_id != app_id
            or record.build_family != family or record.build_key != family
            or record.lifecycle_status not in {"draft", "current"} or not record.source_chat_id
            or record.source_workflow != producer or record.commit_metadata.source_workflow != producer
            or record.commit_metadata.source_chat_id != record.source_chat_id
            or record.commit_metadata.author_user_id != user_id):
        raise ValueError(f"The pinned {family} input is unavailable or outside this build")
    return record


async def export_input_versions(
    context: Any, *, workflow: str, app_id: str, user_id: str | None, artifact_store: Any,
    additional_families: tuple[str, ...] = (),
) -> dict[str, str]:
    from mozaiksai.core.artifacts import resolve_latest_artifact_version_refs

    predecessor_workflows: set[str] = set()
    sequence_id = context.get("workflow_sequence")
    if sequence_id:
        pack = load_global_pack_graph()
        sequence = get_workflow_sequence(pack, sequence_id) if pack else None
        if sequence and any(workflow in step.workflows for step in sequence.steps):
            for step in sequence.steps:
                if workflow in step.workflows:
                    break
                predecessor_workflows.update(step.workflows)

    current_families = list(additional_families)
    owned: dict[str, str] = {}
    for family, producer in _PRODUCERS.items():
        record_id = context.get(f"{family}_artifact_version_id")
        if not record_id:
            if producer in predecessor_workflows:
                raise ValueError(f"Missing exact {family} input from the preceding {producer} stage")
            current_families.append(family)
            continue
        binding = require_build_binding(context)
        if not user_id or app_id != binding.target_app_id:
            raise ValueError(f"The pinned {family} input is unavailable or outside this build")
        record = await load_pinned_input_record(
            artifact_store=artifact_store, family=family, record_id=record_id, app_id=app_id, user_id=user_id,
        )
        if family == "design_docs":
            verify_generator_design_inputs(context, record, user_id=user_id, build_id=binding.build_id)
        else:
            if resolve_subscription_contract(context) != verified_summary_payload(record):
                raise ValueError("The subscription presented to the generator differs from its pinned input")
            if design_id := context.get("design_docs_artifact_version_id"):
                verify_subscription_design_input(record, design_id)
        owned[family] = record.id
    current = await resolve_latest_artifact_version_refs(
        app_id=app_id, artifact_kinds=current_families, artifact_store=artifact_store,
    ) if current_families else {}
    return {**current, **owned}
