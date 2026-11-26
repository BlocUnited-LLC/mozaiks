"""Compile the app half of an insert-bound workflow result from the pinned workflow plan."""

from __future__ import annotations

import json
import keyword
from collections import Counter, defaultdict
from typing import Any

import yaml

from mozaiksai.core.runtime.app.module_loader import (
    ModuleCapability,
    ModuleDefinition,
    ModuleEventsManifest,
    ModuleReactionsManifest,
    validate_module_result_writes,
)
from mozaiksai.core.runtime.app.result_schema import result_object_schema
from mozaiksai.core.semantics.closed_contract_schema import import_closed_contract_schema
from mozaiksai.core.workflow.generator_support.module_action_inventory import (
    pinned_result_workflows,
)
from mozaiksai.core.workflow.generator_support.module_read_actions import _replace_functions


def compile_result_task_bindings(output: dict[str, Any], task: dict[str, Any], context: Any) -> None:
    """Wire the reviewed task's capability and reaction after module action and event closure."""
    if task.get("task_type") != "module_contract":
        return
    module = task.get("capability_pack_id")
    bindings = [(workflow, plan) for workflow in pinned_result_workflows(context)
                for plan in workflow["result_write_plans"] if plan["module_id"] == module]
    if not bindings:
        return
    root = f"modules/{module}"
    reaction_path = f"{root}/contracts/reactions.yaml"
    if not {reaction_path, f"{root}/module.yaml"}.issubset(task.get("owned_paths") or []):
        raise ValueError(f"{module}: its result-bearing module contract must own module.yaml and {reaction_path}")
    contract = output.get("module_contract") or {}
    if contract.get("module_id") != module:
        raise ValueError(f"{module}: its result-bearing task requires the matching typed module contract")
    manifest = contract["module_yaml"]
    actions = {a["id"]: a for a in manifest.get("actions", [])}
    declarations = (contract.get("events_yaml") or {}).get("events", [])
    capabilities = manifest.setdefault("capabilities", [])
    reactions = contract.get("reactions_yaml")
    if reactions is None:
        reactions = contract["reactions_yaml"] = {"schema_version": "mozaiks.reactions.v1", "reactions": []}
    flows = (context.get("app_build_plan") or {}).get("event_flows", [])
    for workflow, plan in bindings:
        event, write = plan["event_type"], plan["result_write"]
        capability, target = workflow["capability_id"], workflow["workflow_name"]
        source_action = write["source_action"]
        if not any(f.get("event_type") == event and f.get("producing_action") == source_action
                   and f.get("producer_pack_id") == module and capability in (f.get("workflow_capability_ids") or [])
                   for f in flows):
            raise ValueError(f"{module}: the build plan must bind {source_action} and {event} to {capability}")
        if (event not in (actions.get(source_action, {}).get("emits") or [])
                or not any(e.get("type") == event and e.get("producer") == module for e in declarations)):
            raise ValueError(f"{module}: {source_action} must emit the declared result event {event}")
        matching = [c for c in capabilities if c.get("capability_id") == capability]
        if matching:
            if len(matching) != 1 or matching[0].get("kind") != "workflow" or matching[0].get("target") != target:
                raise ValueError(f"{module}: conflicting workflow capability {capability} for {target}")
        else:
            capabilities.append(ModuleCapability(capability_id=capability, kind="workflow", target=target,
                                                 title=target).model_dump(exclude_none=True))
        approved_events = {owned["event_type"] for flow, owned in bindings if flow["capability_id"] == capability}
        if any((r.get("target") or {}).get("capability_id") == capability and r.get("event_type") not in approved_events
               for r in reactions.get("reactions", [])):
            raise ValueError(f"{module}: conflicting event targeting result capability {capability}")
        _bind_task_result_reaction(reactions, event, capability, write)
    ModuleReactionsManifest.model_validate(reactions)


def _bind_task_result_reaction(document: dict[str, Any], event: str, capability: str, write: dict[str, Any]) -> None:
    from factory_app.workflows.AppGenerator.tools.hook_workflow_integration_contract import (
        _reaction_id_for_event,
    )

    rows = document["reactions"]
    reaction_id = _reaction_id_for_event(capability, event, 0)
    matching = [r for r in rows if r.get("event_type") == event
                and (r.get("target") or {}).get("capability_id") == capability]
    if len(matching) > 1:
        raise ValueError(f"Duplicate result reaction for {event} and {capability}")
    if matching:
        target = matching[0]["target"]
        if target.get("kind") != "capability" or (target.get("result_write") is not None and target["result_write"] != write):
            raise ValueError(f"Conflicting result reaction for {event} and {capability}")
        target["result_write"] = write
    elif any(r.get("id") == reaction_id or (r.get("target") or {}).get("result_write") == write for r in rows):
        raise ValueError(f"Conflicting result reaction for {event} and {capability}")
    else:
        rows.append({"id": reaction_id, "event_type": event,
                     "target": {"kind": "capability", "capability_id": capability, "result_write": write}})


def workflow_result_files(files: dict[str, str], metadata: dict[str, Any] | None) -> dict[str, str]:
    """Return only missing canonical output bytes. Conflicting declarations fail assembly."""
    plans: dict[str, dict[tuple[str, str], dict[str, Any]]] = defaultdict(dict)
    result_schemas = {}
    for workflow in (metadata or {}).get("workflows", []):
        for plan in workflow.get("result_write_plans", []):
            key = (plan["event_type"], workflow["capability_id"])
            bindings = plans[plan["module_id"]]
            if key in bindings:
                raise ValueError(f"Duplicate workflow result plan for {key}")
            bindings[key] = plan["result_write"]
            result_schemas[plan["module_id"], key] = workflow["result_output_schema"]
    if plans and not (metadata or {}).get("source_artifact_version_id"):
        raise ValueError("A workflow result plan requires the registered workflow bundle ID")

    changes: dict[str, str] = {}
    for path, content in files.items():
        parts = path.split("/")
        if (len(parts) != 4 or parts[0] != "modules" or parts[2:] != ["contracts", "reactions.yaml"]
                or (parts[1] not in plans and "result_write" not in content)):
            continue
        module = parts[1]
        expected = plans.pop(module, {})
        raw = yaml.safe_load(content)
        reactions = ModuleReactionsManifest.model_validate(raw)
        observed = set()
        modified = False
        for index, reaction in enumerate(reactions.reactions):
            target = reaction.target
            key = (reaction.event_type, target.capability_id)
            approved = (
                expected.get((reaction.event_type, target.capability_id))
                if target.kind == "capability" and target.capability_id is not None else None
            )
            if target.result_write is not None and target.result_write.model_dump(mode="json") != approved:
                raise ValueError(f"{path}: result_write does not match its pinned workflow plan for {key}")
            if approved is None:
                continue
            if key in observed:
                raise ValueError(f"{path}: duplicate result_write reaction for {key}")
            observed.add(key)
            if target.result_write is None:
                raw["reactions"][index]["target"]["result_write"] = approved
                modified = True
        if expected.keys() - observed:
            raise ValueError(f"{path}: missing owning capability reaction for {sorted(expected.keys() - observed)}")
        if not expected:
            continue
        if modified:
            changes[path] = yaml.safe_dump(raw, sort_keys=False, allow_unicode=True)
            reactions = ModuleReactionsManifest.model_validate(raw)
        root = f"modules/{module}"
        definition = ModuleDefinition.model_validate(yaml.safe_load(files.get(f"{root}/module.yaml", "")))
        if definition.name != module:
            raise ValueError(f"{root}: module.id must match the result owner")
        events = ModuleEventsManifest.model_validate(yaml.safe_load(files.get(f"{root}/contracts/events.yaml", "")))
        validate_module_result_writes(definition, reactions, events, json.loads(files.get("data/contract.json", "null")))
        actions = {action.id: action for action in definition.actions}
        for key, write in expected.items():
            receiving = result_object_schema(actions[write["action"]].input_schema)
            producer = result_schemas[module, key]
            if import_closed_contract_schema(receiving) != import_closed_contract_schema(producer):
                raise ValueError(f"{root}: input_schema of {write['action']!r} must match the compiled workflow result schema")
        changes.update(_receiver_files(files, definition, {write["action"] for write in expected.values()}))
    if plans:
        raise ValueError(f"Missing owning capability reactions.yaml for result_write in {', '.join(sorted(plans))}")
    return changes


def _receiver_files(files: dict[str, str], module: ModuleDefinition, action_ids: set[str]) -> dict[str, str]:
    entry, _, cls = module.module.handler.partition(":")
    if entry != "backend.handler" or not cls.isidentifier() or keyword.iskeyword(cls):
        raise ValueError(f"{module.name}: result actions need a declared backend.handler:ClassName")
    methods = Counter(action.handler_method for action in module.actions)
    handlers, services = {}, {}
    for action in module.actions:
        if action.id not in action_ids:
            continue
        method = action.handler_method
        if not method.isidentifier() or keyword.iskeyword(method) or method.startswith("_") or methods[method] != 1:
            raise ValueError(f"{module.name}: result action needs a distinct public Python handler method")
        handlers[method] = (
            f"async def {method}(self, ctx, **values):\n"
            "    from . import service\n"
            f"    return await service.{method}(ctx, values)\n"
        )
        services[method] = (
            f"async def {method}(ctx, values):\n"
            "    return await ctx.commit_workflow_result(values)\n"
        )
    changes = {}
    for filename, functions, class_name in [("handler.py", handlers, cls), ("service.py", services, None)]:
        path = f"modules/{module.name}/backend/{filename}"
        if path not in files:
            raise ValueError(f"{path}: missing source for the declared workflow result action")
        compiled = _replace_functions(files[path], functions, path=path, class_name=class_name)
        if compiled != files[path]:
            changes[path] = compiled
    return changes
