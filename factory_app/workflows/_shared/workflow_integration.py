"""Workflow-bundle integration metadata shared by factory workflows."""

from __future__ import annotations

import re
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from mozaiksai.core.artifacts.models import BuildRecordStatus
from mozaiksai.core.runtime.app.module_loader import (
    CANONICAL_EVENT_PREFIX_LABEL,
    CANONICAL_EVENT_PREFIXES,
    ModuleReactionResultWrite,
)
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.generator_support.module_entitlement_gates import (
    MISSING_FEATURE_SELECTION_MESSAGE,
    resolve_subscription_contract,
)

CONTRACT_VERSION = "1.0"
_RETIRED_STATUSES = {BuildRecordStatus.ARCHIVED, BuildRecordStatus.DELETED}


class WorkflowEventBindingError(ValueError):
    """A generated orchestrator's event bindings need repair before export."""

    def __init__(self, workflow_name: str, message: str) -> None:
        self.workflow_name = workflow_name
        super().__init__(message)


class WorkflowResultWritePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    module_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    result_write: ModuleReactionResultWrite


def workflow_result_write_plans(entry: Mapping[str, Any]) -> list[WorkflowResultWritePlan]:
    return TypeAdapter(list[WorkflowResultWritePlan]).validate_python(entry.get("result_write_plans", []))


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _context_get(context_variables: Any | None, key: str, default: Any = None) -> Any:
    if context_variables is None:
        return default
    if hasattr(context_variables, "get"):
        try:
            value = context_variables.get(key)
            # Live containers freeze reads; a frozen trigger list fails the
            # `isinstance(..., list)` below and was written back as [].
            return default if value is None else detach(value)
        except Exception:
            pass
    data = getattr(context_variables, "data", None)
    if isinstance(data, dict):
        return data.get(key, default)
    if isinstance(context_variables, dict):
        return context_variables.get(key, default)
    return default


def _context_set(context_variables: Any | None, key: str, value: Any) -> None:
    if context_variables is None:
        return
    if hasattr(context_variables, "set"):
        try:
            context_variables.set(key, value)
            return
        except Exception:
            pass
    data = getattr(context_variables, "data", None)
    if isinstance(data, dict):
        data[key] = value
        return
    if isinstance(context_variables, dict):
        context_variables[key] = value


def workflow_name_to_capability_id(workflow_name: str) -> str:
    text = re.sub(r"(.)([A-Z][a-z]+)", r"\1-\2", str(workflow_name or "").strip())
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", text)
    text = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return text or "generated-workflow"


def workflow_trigger_event_type(trigger: Any) -> str | None:
    if not isinstance(trigger, Mapping):
        return None
    for key in ("event", "event_type", "type"):
        value = _text(trigger.get(key))
        if value and value.startswith(CANONICAL_EVENT_PREFIXES):
            return value
    return None


def _normalize_trigger_event(raw: Any, *, default_capability_id: str | None = None) -> dict[str, Any] | None:
    if isinstance(raw, str):
        event_type = _text(raw)
        if not event_type or not event_type.startswith(CANONICAL_EVENT_PREFIXES):
            return None
        return {
            "event_type": event_type,
            "source": event_type.partition(".")[0],
            "capability_id": default_capability_id,
            "description": None,
        }
    if not isinstance(raw, Mapping):
        return None
    event_type = workflow_trigger_event_type(raw)
    if not event_type:
        return None
    return {
        "event_type": event_type,
        "source": _text(raw.get("source")) or event_type.partition(".")[0],
        "capability_id": _text(raw.get("capability_id")) or default_capability_id,
        "description": _text(raw.get("description")),
    }


def _load_orchestrator(files: Any) -> dict[str, Any]:
    if not isinstance(files, list):
        return {}
    for file_entry in files:
        if not isinstance(file_entry, Mapping):
            continue
        filename = _text(file_entry.get("filename") or file_entry.get("path"))
        if filename != "orchestrator.yaml":
            continue
        try:
            parsed = yaml.safe_load(str(file_entry.get("content") or "")) or {}
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _normalize_workflow_item(raw: Mapping[str, Any]) -> dict[str, Any] | None:
    workflow_name = _text(raw.get("workflow_name") or raw.get("name") or raw.get("id"))
    if not workflow_name:
        return None
    startup_mode = _text(raw.get("startup_mode") or raw.get("workflow_startup_mode"))
    capability_id = _text(raw.get("capability_id")) or workflow_name_to_capability_id(workflow_name)
    raw_events = raw.get("trigger_events") or raw.get("input_events") or []
    trigger_events = [
        event
        for event in (
            _normalize_trigger_event(item, default_capability_id=capability_id)
            for item in (raw_events if isinstance(raw_events, list) else [])
        )
        if event is not None
    ]
    workflow = {
        "workflow_name": workflow_name,
        "capability_id": capability_id,
        "startup_mode": startup_mode,
        "trigger_events": trigger_events,
    }
    plans = workflow_result_write_plans(raw)
    if plans:
        events = {event["event_type"] for event in trigger_events}
        selected = {plan.event_type for plan in plans}
        if (startup_mode != "BackendOnly" or len(selected) != len(plans) or selected != events):
            raise WorkflowEventBindingError(workflow_name, "Automatic result writes require one plan for each declared BackendOnly event trigger")
        workflow["result_write_plans"] = [plan.model_dump(mode="json") for plan in plans]
    design_surface_id = _text(raw.get("design_surface_id"))
    if design_surface_id:
        workflow["design_surface_id"] = design_surface_id
    return workflow


def validate_pricing_workflow_features(context_variables: Any | None) -> None:
    """Reject workflow pricing selections until launch enforces plan grants."""
    contract = resolve_subscription_contract({
        key: _context_get(context_variables, key)
        for key in ("subscription_contract", "subscription_contract_artifact")
    })
    if not contract or not contract.get("contract_required"):
        return
    selections = contract.get("selected_features_by_plan")
    if not isinstance(selections, Mapping):
        raise ValueError(MISSING_FEATURE_SELECTION_MESSAGE)
    selected_workflows = sorted({
        feature
        for features in selections.values()
        for feature in (features if isinstance(features, (list, tuple)) else [])
        if isinstance(feature, str) and feature.startswith("workflow.")
    })
    if selected_workflows or contract.get("workflow_contract_updates"):
        raise ValueError(
            "Workflow features are unavailable for pricing until workflow launch enforces plan "
            f"grants (issue #770): {selected_workflows}. Re-run SubscriptionContractDesigner "
            "without workflow pricing features."
        )


def _primary_workflow(workflows: list[dict[str, Any]]) -> dict[str, Any] | None:
    for workflow in workflows:
        if workflow.get("trigger_events"):
            return workflow
    return workflows[0] if workflows else None


def normalize_workflow_integration_metadata(
    raw: Any,
    *,
    bundle_name: str | None = None,
) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping):
        return None
    raw = detach(raw)
    nested = raw.get("workflow_integration_metadata")
    if isinstance(nested, Mapping):
        raw = nested

    workflows_raw = raw.get("workflows")
    if isinstance(workflows_raw, list):
        workflows = [
            workflow
            for workflow in (_normalize_workflow_item(item) for item in workflows_raw if isinstance(item, Mapping))
            if workflow is not None
        ]
        if len(workflows) != len(workflows_raw):
            return None
    else:
        single = _normalize_workflow_item(raw)
        workflows = [single] if single else []

    if not workflows and workflows_raw != []:
        return None

    primary = _primary_workflow(workflows)
    metadata = {
        "contract_version": str(raw.get("contract_version") or CONTRACT_VERSION),
        "bundle_name": _text(raw.get("bundle_name")) or _text(bundle_name),
        "workflows": workflows,
        "primary_workflow": deepcopy(primary) if primary else None,
    }
    for key in ("source", "source_artifact_version_id", "source_chat_id"):
        value = _text(raw.get(key))
        if value:
            metadata[key] = value
    return metadata


def extract_workflow_integration_metadata_from_bundle_entries(
    bundle_entries: list[dict[str, Any]],
    *,
    bundle_name: str | None = None,
    context_variables: Any | None = None,
) -> dict[str, Any] | None:
    validate_pricing_workflow_features(context_variables)
    workflows: list[dict[str, Any]] = []
    event_workflows: dict[tuple[str, str], str] = {}
    for entry in bundle_entries:
        if not isinstance(entry, Mapping):
            continue
        orchestrator = _load_orchestrator(entry.get("files"))
        workflow_name = _text(orchestrator.get("workflow_name")) or _text(entry.get("workflow_name"))
        if not workflow_name:
            continue
        startup_mode = _text(orchestrator.get("workflow_startup_mode"))
        triggers = orchestrator.get("triggers")
        if not isinstance(triggers, list):
            triggers = []
        trigger_events = []
        for trigger in triggers:
            if not isinstance(trigger, Mapping) or trigger.get("type") not in (None, "event"):
                continue
            event = _normalize_trigger_event(trigger)
            if event is None:
                if trigger.get("event"):
                    raise WorkflowEventBindingError(
                        workflow_name,
                        f"Event {trigger['event']!r} in {workflow_name!r} must use {CANONICAL_EVENT_PREFIX_LABEL}.",
                    )
                continue
            if not event["capability_id"]:
                raise WorkflowEventBindingError(
                    workflow_name, f"Event {event['event_type']!r} in {workflow_name!r} requires its own capability_id."
                )
            trigger_events.append(event)
        capability_ids = {event["capability_id"] for event in trigger_events}
        if len(capability_ids) > 1:
            raise WorkflowEventBindingError(
                workflow_name,
                f"Event triggers for {workflow_name!r} must use one workflow capability_id. Found {sorted(capability_ids)}."
            )
        capability_id = next(iter(capability_ids), None) or workflow_name_to_capability_id(workflow_name)
        for event in trigger_events:
            binding = (event["event_type"], capability_id)
            if binding in event_workflows:
                raise WorkflowEventBindingError(
                    workflow_name,
                    f"Duplicate event binding {binding!r} in {event_workflows[binding]!r} and {workflow_name!r}."
                )
            event_workflows[binding] = workflow_name
        workflow = {
            "workflow_name": workflow_name,
            "capability_id": capability_id,
            "startup_mode": startup_mode,
            "trigger_events": trigger_events,
        }
        if entry.get("result_write_plans") is not None:
            workflow["result_write_plans"] = entry["result_write_plans"]
        workflows.append(workflow)

    if bundle_entries and len(workflows) != len(bundle_entries):
        return None
    return normalize_workflow_integration_metadata(
        {
            "contract_version": CONTRACT_VERSION,
            "bundle_name": bundle_name,
            "workflows": workflows,
            "source": "workflow_bundle_files",
        },
        bundle_name=bundle_name,
    )


def workflow_integration_metadata_from_context(context_variables: Any | None) -> dict[str, Any] | None:
    raw_metadata = _context_get(context_variables, "workflow_integration_metadata")
    metadata = normalize_workflow_integration_metadata(raw_metadata)
    if metadata:
        return metadata

    workflow_name = _text(_context_get(context_variables, "generated_workflow_name"))
    capability_id = _text(_context_get(context_variables, "generated_workflow_capability_id"))
    if not workflow_name and not capability_id:
        return None

    trigger_events = _context_get(context_variables, "generated_workflow_trigger_events", [])
    return normalize_workflow_integration_metadata(
        {
            "contract_version": CONTRACT_VERSION,
            "source": "context_variables",
            "workflows": [
                {
                    "workflow_name": workflow_name or capability_id,
                    "capability_id": capability_id or workflow_name_to_capability_id(str(workflow_name)),
                    "startup_mode": _context_get(context_variables, "generated_workflow_startup_mode"),
                    "trigger_events": trigger_events if isinstance(trigger_events, list) else [],
                }
            ],
        }
    )


def apply_workflow_integration_context(
    context_variables: Any | None,
    metadata: Any,
) -> dict[str, Any] | None:
    normalized = normalize_workflow_integration_metadata(metadata)
    if not normalized:
        return None
    primary = normalized.get("primary_workflow")
    if not isinstance(primary, dict):
        primary = {}

    _context_set(context_variables, "workflow_integration_metadata", normalized)
    _context_set(context_variables, "generated_workflow_integrations", normalized["workflows"])
    _context_set(context_variables, "generated_workflow_name", primary.get("workflow_name"))
    _context_set(context_variables, "generated_workflow_capability_id", primary.get("capability_id"))
    _context_set(context_variables, "generated_workflow_startup_mode", primary.get("startup_mode"))
    _context_set(context_variables, "generated_workflow_trigger_events", primary.get("trigger_events") or [])
    _context_set(context_variables, "workflow_bundle_artifact_version_id", normalized.get("source_artifact_version_id"))
    return normalized


def _source_app_id(context_variables: Any) -> str | None:
    if (
        _context_get(context_variables, "artifact_kind") == "workflow_bundle"
        or _context_get(context_variables, "upstream_workflow_bundle_artifact_version_id")
    ):
        return None
    baseline = _text(_context_get(context_variables, "revision_source_artifact_version_id"))
    if baseline:
        return baseline
    if _context_get(context_variables, "artifact_kind") == "app_bundle":
        request = _context_get(context_variables, "refinement_request_meta", {})
        if isinstance(request, Mapping) and request.get("build_family") == "app_bundle":
            baseline = _text(request.get("build_record_id"))
        return baseline or _text(_context_get(context_variables, "artifact_version_id"))
    return None


def workflow_integration_source_error(context_variables: Any) -> str | None:
    baseline_id = _source_app_id(context_variables)
    if baseline_id:
        receipt = _context_get(context_variables, "workflow_integration_source_app")
        if not isinstance(receipt, Mapping) or receipt.get("build_record_id") != baseline_id:
            return f"Workflow inputs for source app bundle {baseline_id!r} have not been verified."
    return None


def workflow_integration_source_pin(context_variables: Any) -> str | None:
    upstream = _text(_context_get(context_variables, "upstream_workflow_bundle_artifact_version_id"))
    if upstream:
        return upstream
    request = _context_get(context_variables, "refinement_request_meta", {})
    if isinstance(request, Mapping) and request.get("build_family") == "workflow_bundle":
        source = _text(request.get("build_record_id"))
        if source:
            return source
    if _context_get(context_variables, "artifact_kind") == "workflow_bundle":
        source = _text(_context_get(context_variables, "artifact_version_id"))
        if source:
            return source
    baseline_id = _source_app_id(context_variables)
    receipt = _context_get(context_variables, "workflow_integration_source_app")
    if baseline_id and isinstance(receipt, Mapping) and receipt.get("build_record_id") == baseline_id:
        return _text(receipt.get("workflow_bundle_id"))
    refs = _context_get(context_variables, "artifact_version_refs", {})
    return _text(refs.get("workflow_bundle")) if isinstance(refs, Mapping) else None


def _declared_workflow_catalog(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping) or not isinstance(raw.get("workflows"), list):
        return None
    for item in raw["workflows"]:
        if not isinstance(item, Mapping) or not _text(item.get("workflow_name")) or not _text(item.get("capability_id")):
            return None
        events = item.get("trigger_events", item.get("input_events", []))
        if not isinstance(events, list) or any(_normalize_trigger_event(event) is None for event in events):
            return None
    return normalize_workflow_integration_metadata(raw)


def workflow_integration_metadata_from_artifact(artifact: Any) -> dict[str, Any] | None:
    if isinstance(artifact, Mapping):
        family = artifact.get("build_family")
        status = artifact.get("lifecycle_status")
        artifact_id = _text(artifact.get("_id") or artifact.get("id"))
        commit = artifact.get("commit_metadata")
    else:
        family = getattr(artifact, "build_family", None)
        status = getattr(artifact, "lifecycle_status", None)
        artifact_id = _text(getattr(artifact, "id", None))
        commit = getattr(artifact, "commit_metadata", None)
    if family != "workflow_bundle" or not artifact_id or status in _RETIRED_STATUSES:
        return None
    fields = commit.get("metadata") if isinstance(commit, Mapping) else getattr(commit, "metadata", None)
    if not isinstance(fields, Mapping):
        return None
    envelope = fields if "workflow_integration_metadata" in fields else fields.get("summary_payload", fields)
    if not isinstance(envelope, Mapping):
        return None
    catalog = envelope.get("workflow_integration_metadata", envelope)
    metadata = _declared_workflow_catalog(catalog)
    if not metadata or metadata.get("source_artifact_version_id") not in (None, artifact_id):
        return None
    metadata["source_artifact_version_id"] = artifact_id
    return metadata


async def hydrate_workflow_integration_context_from_latest_artifact(
    context_variables: Any | None,
    *,
    artifact_store: Any | None = None,
) -> dict[str, Any]:
    workflow_ref = workflow_integration_source_pin(context_variables)
    baseline_id = _source_app_id(context_variables)
    if baseline_id:
        _context_set(context_variables, "workflow_integration_source_app", None)
    existing_catalog = _context_get(context_variables, "workflow_integration_metadata")
    existing = _declared_workflow_catalog(existing_catalog)
    if (
        not workflow_ref and not baseline_id and existing and existing.get("source") == "workflow_bundle_files"
        and not existing.get("source_artifact_version_id")
    ):
        apply_workflow_integration_context(context_variables, existing)
        return {"status": "hydrated", "source": "context_variables", "workflow_count": len(existing["workflows"])}
    if workflow_ref or baseline_id or (existing_catalog and (not existing or existing.get("source") != "context_variables")):
        # Lifecycle hooks may log lookup failures and continue into workers.
        apply_workflow_integration_context(context_variables, {"workflows": []})
        _context_set(context_variables, "workflow_integration_metadata", None)

    from factory_app.workflows._shared.platform.build_target import require_build_binding

    app_id = require_build_binding(context_variables).target_app_id

    if artifact_store is None:
        try:
            from mozaiksai.core.artifacts import get_artifact_store

            artifact_store = get_artifact_store()
        except Exception as exc:
            if workflow_ref or baseline_id:
                raise RuntimeError(f"Cannot load workflow integration for build {(workflow_ref or baseline_id)!r}.") from exc
            return {"status": "skipped", "reason": "artifact_store_unavailable", "error": str(exc)}

    if baseline_id:
        baseline = await artifact_store.get_build_record(app_id=app_id, build_record_id=baseline_id)
        if (
            baseline is None or baseline.id != baseline_id or baseline.app_id != app_id
            or baseline.build_family != "app_bundle" or baseline.lifecycle_status in _RETIRED_STATUSES
        ):
            raise ValueError(f"Cannot resolve workflow inputs for app bundle {baseline_id!r}.")
        input_id = _text(baseline.canonical_inputs_version.get("workflow_bundle"))
        _context_set(context_variables, "workflow_integration_source_app", {
            "build_record_id": baseline_id, "workflow_bundle_id": input_id,
        })
        workflow_ref = workflow_integration_source_pin(context_variables)
        if not workflow_ref:
            return {"status": "skipped", "reason": "workflow_integration_metadata_absent"}

    candidates: list[Any] = []
    if workflow_ref:
        artifact = await artifact_store.get_build_record(app_id=app_id, build_record_id=str(workflow_ref))
        candidates.append(artifact)
    elif hasattr(artifact_store, "list_build_records"):
        try:
            from mozaiksai.core.artifacts import ArtifactLifecycleStatus

            candidates.extend(
                await artifact_store.list_build_records(
                    app_id=app_id,
                    build_family="workflow_bundle",
                    lifecycle_status=ArtifactLifecycleStatus.CURRENT,
                    limit=1,
                )
            )
        except Exception as exc:
            return {"status": "skipped", "reason": "artifact_lookup_failed", "error": str(exc)}

    for artifact in candidates:
        record_app = artifact.get("app_id") if isinstance(artifact, Mapping) else getattr(artifact, "app_id", None)
        if record_app != app_id:
            continue
        metadata = workflow_integration_metadata_from_artifact(artifact)
        if not metadata or (workflow_ref and metadata.get("source_artifact_version_id") != workflow_ref):
            continue
        apply_workflow_integration_context(context_variables, metadata)
        return {
            "status": "hydrated",
            "source": "workflow_bundle_artifact",
            "artifact_version_id": metadata.get("source_artifact_version_id"),
            "workflow_count": len(metadata["workflows"]),
        }

    if workflow_ref:
        raise ValueError(f"Workflow bundle {workflow_ref!r} has no integration metadata for this build.")
    if candidates:
        apply_workflow_integration_context(context_variables, {"workflows": []})
        _context_set(context_variables, "workflow_integration_metadata", None)
        return {"status": "skipped", "reason": "workflow_integration_metadata_absent"}
    projected = workflow_integration_metadata_from_context(context_variables)
    if projected:
        apply_workflow_integration_context(context_variables, projected)
        return {"status": "hydrated", "source": "context_variables", "workflow_count": len(projected["workflows"])}
    return {"status": "skipped", "reason": "workflow_integration_metadata_absent"}


__all__ = [
    "CONTRACT_VERSION",
    "apply_workflow_integration_context",
    "extract_workflow_integration_metadata_from_bundle_entries",
    "hydrate_workflow_integration_context_from_latest_artifact",
    "normalize_workflow_integration_metadata",
    "validate_pricing_workflow_features",
    "workflow_integration_metadata_from_artifact",
    "workflow_integration_source_error",
    "workflow_integration_source_pin",
    "workflow_integration_metadata_from_context",
    "workflow_name_to_capability_id",
    "workflow_trigger_event_type",
]
