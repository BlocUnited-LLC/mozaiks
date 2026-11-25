"""Project the generated workflow bundle's bindings into AppGenerator worker prompts."""

from __future__ import annotations

import json
import re
from typing import Any

from factory_app.workflows._shared.hook_utils import update_agent_section
from factory_app.workflows._shared.workflow_integration import (
    workflow_integration_metadata_from_context,
    workflow_integration_source_error,
    workflow_integration_source_pin,
)

SECTION_HEADER = "[WORKFLOW INTEGRATION CONTRACT]"


def inject_workflow_integration_contract(agent, messages: list[dict[str, Any]]) -> None:
    """Render the event and launch contracts from every orchestrator in the bundle."""
    context = agent.context_variables
    metadata = workflow_integration_metadata_from_context(context)
    pin = workflow_integration_source_pin(context)
    if workflow_integration_source_error(context) or (pin and pin != (metadata or {}).get("source_artifact_version_id")):
        update_agent_section(
            agent, SECTION_HEADER, "The pinned workflow bundle is unavailable. Leave workflow targets empty until it is loaded."
        )
        return
    workflows = metadata["workflows"] if metadata else []
    if agent.name == "ServiceAgent":
        workflows = [workflow for workflow in workflows if workflow.get("result_write_plans")]
        if not workflows and SECTION_HEADER not in agent.system_message:
            return
    if not workflows:
        if metadata is not None or SECTION_HEADER in agent.system_message:
            update_agent_section(agent, SECTION_HEADER, "No generated workflows are bound to this build.")
        return

    blocks = []
    for workflow in workflows:
        name = workflow["workflow_name"]
        capability_id = workflow["capability_id"]
        startup_mode = workflow["startup_mode"] or "UserDriven"
        events = workflow["trigger_events"]
        if agent.name == "AppPlanAgent":
            block = _build_app_plan_block(name, capability_id, startup_mode, events)
        elif agent.name == "ConfigMiddlewareAgent":
            block = f"module.yaml workflow target: {name}\n" + _build_config_middleware_block(capability_id, events)
        else:
            block = _build_generic_block(name, capability_id, startup_mode)
        for plan in workflow.get("result_write_plans", []):
            write = plan["result_write"]
            block += (
                f"\nApproved result for {plan['event_type']} → {capability_id}, owning module {plan['module_id']}:"
                f"\n  target.result_write: {json.dumps(write, sort_keys=True)}"
                f"\n  Input schema compiled from the workflow result agent: {json.dumps(workflow['result_output_schema'], sort_keys=True)}"
                f"\n  {write['source_action']} inserts into private per-user collection {write['collection']},"
                f" then emits the event. Its payload_schema requires string {write['record_key']} from that insert."
                f"\n  Declare {write['action']} as internal, permissions [], emits [], no entitlement; its closed"
                f" object input has exactly these required fields: {', '.join(write['fields'])}."
                "\n  Declare the record key and result fields in the collection. The thin action handler delegates"
                " to its module service, which returns await ctx.commit_workflow_result with only those result"
                " fields. The host binds the originating record and owner. Include the module, data contract,"
                " event, reaction, service, and any requested page changes in the build plan."
            )
        blocks.append(block)
    update_agent_section(agent, SECTION_HEADER, "\n\n".join(blocks))


def _build_app_plan_block(workflow_name: str, capability_id: str, startup_mode: str, trigger_events: list) -> str:
    expectation = {
        "AgentDriven": "agent speaks first after launch",
        "UserDriven": "user speaks first after launch",
        "BackendOnly": "background workflow with no direct page launch",
    }.get(startup_mode, startup_mode)
    lines = [
        f"workflow_name  : {workflow_name}",
        f"capability_id  : {capability_id}",
        f"startup_mode   : {startup_mode} ({expectation})",
        "HARD CONSTRAINTS",
        "Include this capability_id in workflow_capability_ids only for the following event types.",
    ]
    for event in trigger_events:
        event_type = event["event_type"] if isinstance(event, dict) else event
        lines.append(f"  - event_type: {event_type}  →  workflow capability: {capability_id}")
    if not trigger_events:
        lines.append("  trigger_events: (none declared)")
    return "\n".join(lines)


def _build_config_middleware_block(capability_id: str, trigger_events: list) -> str:
    lines = [
        f"Workflow capability_id: {capability_id}",
        "HARD CONSTRAINT: emit these reaction entries only in the module contract assigned the binding.",
    ]
    for index, event in enumerate(trigger_events):
        event_type = event["event_type"] if isinstance(event, dict) else event
        lines.extend([
            f"  - id: {_reaction_id_for_event(capability_id, event_type, index)}",
            f"    event_type: {event_type}",
            "    target:",
            "      kind: capability",
            f"      capability_id: {capability_id}",
        ])
    if not trigger_events:
        lines.append("  trigger_events: (none declared)")
    return "\n".join(lines)


def _reaction_id_for_event(capability_id: str, event_type: str, index: int) -> str:
    token = re.sub(r"[^a-zA-Z0-9]+", "_", f"{capability_id}_{event_type}").strip("_").lower()
    return token or f"workflow_reaction_{index + 1}"


def _build_generic_block(workflow_name: str, capability_id: str, startup_mode: str) -> str:
    return "\n".join([
        f"AgentGenerator workflow: {workflow_name}",
        f"  workflow_id   : {workflow_name}  (use for direct page launches)",
        f"  capability_id : {capability_id}  (use in workflow_capability_ids)",
        f"  startup_mode  : {startup_mode}  (runtime-owned launch behavior)",
    ])


__all__ = ["inject_workflow_integration_contract"]
