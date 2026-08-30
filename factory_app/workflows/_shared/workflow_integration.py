"""Workflow-bundle integration metadata shared by factory workflows."""

from __future__ import annotations

import re
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

import yaml

CONTRACT_VERSION = "1.0"
EVENT_PREFIXES = ("domain.", "platform.", "hosted.")


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
            return default if value is None else value
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


def _event_source(event_type: str) -> str:
    if event_type.startswith("domain."):
        return "domain"
    if event_type.startswith("platform."):
        return "platform"
    if event_type.startswith("hosted."):
        return "hosted"
    return "unknown"


def _trigger_event_type(trigger: Mapping[str, Any]) -> str | None:
    for key in ("event", "event_type", "type"):
        value = _text(trigger.get(key))
        if value and value.startswith(EVENT_PREFIXES):
            return value
    return None


def _normalize_trigger_event(raw: Any, *, default_capability_id: str | None = None) -> dict[str, Any] | None:
    if isinstance(raw, str):
        event_type = _text(raw)
        if not event_type or not event_type.startswith(EVENT_PREFIXES):
            return None
        return {
            "event_type": event_type,
            "source": _event_source(event_type),
            "capability_id": default_capability_id,
            "description": None,
        }
    if not isinstance(raw, Mapping):
        return None
    event_type = _trigger_event_type(raw)
    if not event_type:
        return None
    return {
        "event_type": event_type,
        "source": _text(raw.get("source")) or _event_source(event_type),
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
    return {
        "workflow_name": workflow_name,
        "capability_id": capability_id,
        "startup_mode": startup_mode,
        "trigger_events": trigger_events,
    }


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
    else:
        single = _normalize_workflow_item(raw)
        workflows = [single] if single else []

    if not workflows:
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
) -> dict[str, Any] | None:
    workflows: list[dict[str, Any]] = []
    for entry in bundle_entries:
        if not isinstance(entry, Mapping):
            continue
        orchestrator = _load_orchestrator(entry.get("files"))
        workflow_name = _text(orchestrator.get("workflow_name")) or _text(entry.get("workflow_name"))
        if not workflow_name:
            continue
        startup_mode = _text(orchestrator.get("workflow_startup_mode"))
        capability_id = workflow_name_to_capability_id(workflow_name)
        trigger_events = [
            event
            for event in (
                _normalize_trigger_event(item, default_capability_id=capability_id)
                for item in (orchestrator.get("triggers") if isinstance(orchestrator.get("triggers"), list) else [])  # type: ignore[union-attr]
            )
            if event is not None
        ]
        workflows.append(
            {
                "workflow_name": workflow_name,
                "capability_id": capability_id,
                "startup_mode": startup_mode,
                "trigger_events": trigger_events,
            }
        )

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
        return None

    _context_set(context_variables, "workflow_integration_metadata", normalized)
    _context_set(context_variables, "generated_workflow_integrations", normalized["workflows"])
    _context_set(context_variables, "generated_workflow_name", primary.get("workflow_name"))
    _context_set(context_variables, "generated_workflow_capability_id", primary.get("capability_id"))
    _context_set(context_variables, "generated_workflow_startup_mode", primary.get("startup_mode"))
    _context_set(context_variables, "generated_workflow_trigger_events", primary.get("trigger_events") or [])
    if normalized.get("source_artifact_version_id"):
        _context_set(context_variables, "workflow_bundle_artifact_version_id", normalized["source_artifact_version_id"])
    return normalized


def workflow_integration_metadata_from_artifact(artifact: Any) -> dict[str, Any] | None:
    from mozaiksai.core.artifacts.summary_artifacts import extract_summary_payload

    summary_payload = extract_summary_payload(artifact)
    metadata = normalize_workflow_integration_metadata(summary_payload)
    if metadata:
        artifact_id = _text(getattr(artifact, "id", None) or getattr(artifact, "_id", None))
        if artifact_id:
            metadata["source_artifact_version_id"] = artifact_id
        return metadata

    commit_metadata = getattr(artifact, "commit_metadata", None)
    raw_metadata = getattr(commit_metadata, "metadata", None)
    if not isinstance(raw_metadata, Mapping) and isinstance(artifact, Mapping):
        raw_commit = artifact.get("commit_metadata")
        if isinstance(raw_commit, Mapping):
            raw_metadata = raw_commit.get("metadata")

    metadata = normalize_workflow_integration_metadata(raw_metadata)
    if metadata:
        artifact_id = _text(getattr(artifact, "id", None) or getattr(artifact, "_id", None))
        if artifact_id:
            metadata["source_artifact_version_id"] = artifact_id
    return metadata


async def hydrate_workflow_integration_context_from_latest_artifact(
    context_variables: Any | None,
    *,
    artifact_store: Any | None = None,
) -> dict[str, Any]:
    existing = workflow_integration_metadata_from_context(context_variables)
    if existing:
        apply_workflow_integration_context(context_variables, existing)
        return {"status": "hydrated", "source": "context_variables", "workflow_count": len(existing["workflows"])}

    app_id = _text(_context_get(context_variables, "app_id"))
    if not app_id:
        return {"status": "skipped", "reason": "missing_app_id"}

    if artifact_store is None:
        try:
            from mozaiksai.core.artifacts import get_artifact_store

            artifact_store = get_artifact_store()
        except Exception as exc:
            return {"status": "skipped", "reason": "artifact_store_unavailable", "error": str(exc)}

    candidates: list[Any] = []
    refs = _context_get(context_variables, "artifact_version_refs", {})
    workflow_ref = refs.get("workflow_bundle") if isinstance(refs, dict) else None
    if workflow_ref and hasattr(artifact_store, "get_build_record"):
        try:
            artifact = await artifact_store.get_build_record(
                app_id=app_id,
                build_record_id=str(workflow_ref),
            )
            if artifact is not None:
                candidates.append(artifact)
        except Exception:
            pass

    if hasattr(artifact_store, "list_build_records"):
        try:
            from mozaiksai.core.artifacts import ArtifactLifecycleStatus

            candidates.extend(
                await artifact_store.list_build_records(
                    app_id=app_id,
                    build_family="workflow_bundle",
                    lifecycle_status=ArtifactLifecycleStatus.CURRENT,
                    limit=5,
                )
            )
        except Exception as exc:
            return {"status": "skipped", "reason": "artifact_lookup_failed", "error": str(exc)}

    for artifact in candidates:
        metadata = workflow_integration_metadata_from_artifact(artifact)
        if not metadata:
            continue
        apply_workflow_integration_context(context_variables, metadata)
        return {
            "status": "hydrated",
            "source": "workflow_bundle_artifact",
            "artifact_version_id": metadata.get("source_artifact_version_id"),
            "workflow_count": len(metadata["workflows"]),
        }

    return {"status": "skipped", "reason": "workflow_integration_metadata_absent"}


__all__ = [
    "CONTRACT_VERSION",
    "apply_workflow_integration_context",
    "extract_workflow_integration_metadata_from_bundle_entries",
    "hydrate_workflow_integration_context_from_latest_artifact",
    "normalize_workflow_integration_metadata",
    "workflow_integration_metadata_from_artifact",
    "workflow_integration_metadata_from_context",
    "workflow_name_to_capability_id",
]
