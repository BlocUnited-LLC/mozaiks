"""Compare an accepted app module with its reviewed workflow-result feature."""

from __future__ import annotations

import json
from typing import Any

import yaml

from factory_app.workflows._shared.workflow_integration import (
    WorkflowResultWritePlan,
    workflow_result_write_plans,
)
from mozaiksai.core.runtime.app.module_loader import (
    ActionDef,
    ModuleDefinition,
    ModuleEventsManifest,
    ModuleReactionsManifest,
    resolve_result_write_collection,
    validate_result_collection_schema,
    validate_result_event_record_key,
)
from mozaiksai.core.runtime.app.result_schema import result_object_schema
from mozaiksai.core.runtime.persistence.collection_writes import (
    canonical_write_action_id,
    canonical_write_fields,
    canonical_write_input_schema,
    record_id_field,
)
from mozaiksai.core.runtime.persistence.intent_loader import iter_data_contract_collections
from mozaiksai.core.semantics.closed_contract_schema import import_closed_contract_schema
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.generator_support.design_access_policy import (
    resolve_design_access_policy,
)
from mozaiksai.core.workflow.generator_support.module_action_inventory import (
    canonical_write_actions_for_surface,
    collection_has_canonical_writes,
    pack_owned_outputs,
    pinned_result_workflows,
)

from .workflow_result_writes import workflow_result_files


def _public_write_changes(
    plan: WorkflowResultWritePlan, approved: set[str], collection: dict[str, Any], actions: dict[str, ActionDef],
) -> bool:
    """Only explicit lifecycle operations add canonical public writes to the obligation."""
    write = plan.result_write
    operations = canonical_write_fields(collection)
    source = actions.get(write.source_action)
    if source and source.api_surface not in (None, "public"):
        raise ValueError(f"{plan.module_id}: existing result producer is not a public write")
    if operations is None:
        if collection_has_canonical_writes(collection):
            raise ValueError(f"{plan.module_id}: declare explicit public write fields to keep the result private")
        if not source:
            raise ValueError(f"{plan.module_id}: cannot establish the implementation of {write.source_action}")
        return False
    create = canonical_write_action_id(collection["entity"], "create")
    if write.source_action == create and "create" not in operations:
        raise ValueError(f"{plan.module_id}: the reviewed collection disables the result producer {create}")
    if not source and write.source_action != create:
        raise ValueError(f"{plan.module_id}: custom result producer must be present on the accepted module")
    changes = False
    for operation, writable in operations.items():
        if set(writable).intersection(write.fields):
            raise ValueError(f"{plan.module_id}: the workflow result fields must be private to its receiver")
        if operation not in {"create", "update"}:
            continue
        name = canonical_write_action_id(collection["entity"], operation)
        action = actions.get(name)
        if name not in approved or (action and action.api_surface not in (None, "public")):
            raise ValueError(f"{plan.module_id}: {name} must be a reviewed public write on its own collection")
        expected = canonical_write_input_schema(operation, collection["fields"], writable, write.record_key)
        if not action or import_closed_contract_schema(action.input_schema) != import_closed_contract_schema(expected):
            changes = True
    return changes


def _result_binding_missing(
    plan: WorkflowResultWritePlan, workflow: dict[str, Any], definition: ModuleDefinition,
    reactions: ModuleReactionsManifest, allowed_events: set[str],
) -> bool:
    write = plan.result_write
    schema = workflow["result_output_schema"]
    receiver = next((a for a in definition.actions if a.id == write.action), None)
    if receiver and (receiver.api_surface != "internal" or receiver.permissions or receiver.emits
                     or receiver.entitlement_gate or receiver.ask_context_safe
                     or import_closed_contract_schema(result_object_schema(receiver.input_schema)) != import_closed_contract_schema(schema)):
        raise ValueError(f"{plan.module_id}: accepted receiver {write.action} conflicts with the pinned result")
    capability = next((c for c in definition.capabilities if c.capability_id == workflow["capability_id"]), None)
    if capability and (capability.kind != "workflow" or capability.target != workflow["workflow_name"]):
        raise ValueError(f"{plan.module_id}: accepted capability targets a different workflow")
    matching = []
    for reaction in reactions.reactions:
        target = reaction.target
        if target.capability_id == workflow["capability_id"]:
            if reaction.event_type not in allowed_events:
                raise ValueError(f"{plan.module_id}: accepted capability has an unreviewed event {reaction.event_type}")
            if reaction.event_type == plan.event_type:
                matching.append(reaction)
                if target.kind != "capability" or (target.result_write and target.result_write != write):
                    raise ValueError(f"{plan.module_id}: conflicting accepted result reaction for {plan.event_type}")
                continue
        other = target.result_write
        if other and (other.source_action == write.source_action or other.action == write.action):
            raise ValueError(f"{plan.module_id}: accepted result source or receiver belongs to another reaction")
    if len(matching) > 1:
        raise ValueError(f"{plan.module_id}: duplicate accepted result reaction for {plan.event_type}")
    return not receiver or not capability or not matching or not matching[0].target.result_write


def _required_owners(
    files: dict[str, Any], plan: WorkflowResultWritePlan, workflow: dict[str, Any], approved: set[str],
    collection: dict[str, Any], before: dict[str, Any] | None, allowed_events: set[str],
) -> dict[str, bool]:
    module, event, write = plan.module_id, plan.event_type, plan.result_write
    if not before or any(before.get(k) != collection.get(k) for k in ("scope", "tenancy", "entity", "owner_field")):
        raise ValueError(f"{module}: result write must retain the accepted collection and its owner")
    fields = {f["name"]: f for f in collection["fields"]}
    old = {f["name"]: f for f in before["fields"]}
    if (record_id_field(collection, list(fields)) != write.record_key or record_id_field(before, list(old)) != write.record_key
            or old[write.record_key]["type"] != "string"):
        raise ValueError(f"{module}: result write must retain the accepted record identity")
    validate_result_collection_schema(collection, write, workflow["result_output_schema"])
    root = f"modules/{module}"
    definition = ModuleDefinition.model_validate(yaml.safe_load(files.get(f"{root}/module.yaml", "")))
    if definition.name != module or definition.module.owner not in (None, "app"):
        raise ValueError(f"{root}: accepted module identity or ownership differs from the app-owned design")
    actions = {a.id: a for a in definition.actions}
    if write.action in approved:
        raise ValueError(f"{module}: result receiver cannot also be a reviewed public write")
    service = _public_write_changes(plan, approved, collection, actions)
    raw = files.get(f"{root}/contracts/events.yaml")
    events = ModuleEventsManifest.model_validate(yaml.safe_load(raw)).events if raw else []
    declared = next((e for e in events if e.type == event), None)
    if declared:
        if declared.producer != module:
            raise ValueError(f"{module}: wrong producer for accepted source event {event}")
        validate_result_event_record_key(declared.payload_schema, write.record_key)
    source = actions.get(write.source_action)
    service |= not declared or not source or event not in source.emits
    raw = files.get(f"{root}/contracts/reactions.yaml")
    reactions = ModuleReactionsManifest.model_validate(yaml.safe_load(raw)) if raw else ModuleReactionsManifest(
        schema_version="mozaiks.reactions.v1", reactions=[],
    )
    binding = _result_binding_missing(plan, workflow, definition, reactions, allowed_events)
    # Compare only storage semantics. Descriptions or omitted optional metadata do not require new models.
    def shape(field: dict[str, Any]) -> tuple:
        return (field["type"], bool(field.get("required")), bool(field.get("nullable")), field.get("enum"), field.get("default"))
    models = any(name not in old or shape(old[name]) != shape(field) for name, field in fields.items())
    return {"module_contract": service or binding, "data_models": models, "business_services": service}


def revision_result_module_coverage(context: Any, files: dict[str, Any]) -> dict[str, dict[str, bool]]:
    """Report missing app-owned work from verified baseline bytes, before applying any candidate overlay."""
    if (context.get("revision_scope") != "feature" or context.get("workflow_sequence") != "design_revision"
            or (context.get("change_intent") or {}).get("requires_workflow_revision") is not True):
        return {}
    workflows = pinned_result_workflows(context)
    if not workflows:
        return {}
    impact = context.get("impact_set") or {}
    if (impact.get("workflow_sequence") != "design_revision" or impact.get("requires_rebuild") is not True
            or "AppGenerator" not in (impact.get("affected_workflows") or [])):
        raise ValueError("Result module work requires a reviewed design revision that rebuilds the app")
    if not context.get("design_docs_build_artifact") or resolve_design_access_policy(context) is None:
        raise ValueError("Result module coverage requires this build's reviewed DesignDocs artifact")
    surfaces = (detach(context.get("design_surface_map")) or {}).get("surfaces", [])
    desired = detach(context.get("data_contract"))
    baseline = json.loads(files.get("data/contract.json", "null"))
    existing = {(owner, coll["name"]): coll for owner, kind, coll in iter_data_contract_collections(baseline) if kind == "module"}
    coverage: dict[str, dict[str, bool]] = {}
    sources = set()
    for workflow in workflows:
        plans = workflow_result_write_plans(workflow)
        for plan in plans:
            module, event, write = plan.module_id, plan.event_type, plan.result_write
            if (module, write.source_action) in sources:
                raise ValueError(f"{module}: more than one result write uses {write.source_action}")
            sources.add((module, write.source_action))
            owned = [s for s in surfaces if s.get("owner") == "app" and s.get("surface_kind") == "module" and s.get("surface_id") == module]
            workflow_id = workflow.get("design_surface_id") or workflow["workflow_name"]
            runners = [s for s in surfaces if s.get("owner") == "app" and s.get("surface_kind") == "workflow" and s.get("surface_id") == workflow_id]
            allowed = {p.event_type for p in plans if p.module_id == module}
            if (len(owned) != 1 or len(runners) != 1 or event not in owned[0].get("events_emitted", [])
                    or event not in runners[0].get("workflow_triggers", [])):
                raise ValueError(f"{module}: result source, emitted event and workflow trigger must match the app-owned design")
            approved = set(owned[0].get("owned_mutations", [])) | set(canonical_write_actions_for_surface(owned[0], desired))
            if write.source_action not in approved:
                raise ValueError(f"{module}: workflow result producer {write.source_action} is not a reviewed write")
            coll = resolve_result_write_collection(module, write, desired)
            needs = _required_owners(files, plan, workflow, approved, coll, existing.get((module, write.collection)), allowed)
            previous = coverage.get(module, {})
            coverage[module] = {key: value or previous.get(key, False) for key, value in needs.items()}
    protected = pack_owned_outputs(context)
    for module, needs in coverage.items():
        if any(needs.values()) and f"modules/{module}/module.yaml" in protected:
            raise ValueError(f"{module}: cannot assign a pack-owned module to a result-editing worker")
    complete = {module for module, needs in coverage.items() if not any(needs.values())}
    if complete:
        selected = [{**workflow, "result_write_plans": [p for p in workflow["result_write_plans"] if p["module_id"] in complete]}
                    for workflow in workflows]
        prefixes = tuple(f"modules/{m}/" for m in complete)
        source = {k: v for k, v in files.items() if k == "data/contract.json" or k.startswith(prefixes)}
        changes = workflow_result_files(source, {"source_artifact_version_id": context.get("workflow_bundle_artifact_version_id"), "workflows": selected})
        # assembly_phase always installs deterministic receiver methods. No LLM task owns those bytes.
        if changes.keys() & protected.keys():
            raise ValueError("The workflow result adapter would replace a pack-owned file")
    return {module: needs for module, needs in coverage.items() if any(needs.values())}
