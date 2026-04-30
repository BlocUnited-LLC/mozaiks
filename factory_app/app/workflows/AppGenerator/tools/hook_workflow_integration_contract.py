"""
Hook: Inject Workflow Integration Contract

Fires as an update_agent_state hook on AppPlanAgent and ConfigMiddlewareAgent.

When AgentGenerator has produced a workflow for this app, this hook injects a
typed [WORKFLOW INTEGRATION CONTRACT] section directly into the agent system
message — before every reply — so the agent uses the correct capability_id,
startup_mode, and trigger_events without relying on prompt instructions alone.

Why a hook instead of prompt injection via context_variables:
- Context variables are interpolated at prompt-build time as plain text in a
  bulleted list. The LLM can drift, paraphrase, or ignore them.
- This hook fires unconditionally before every reply. It appends a directive
  block with exact values and explicit hard-constraint rules. The agent sees
  it as part of its system message — not as contextual background info.
- For AppPlanAgent: emits the exact capability_id to use in workflow_capability_ids
  and the exact trigger_events to pass downstream.
- For ConfigMiddlewareAgent: emits the exact subscriptions.yaml entries to generate,
  pre-formatted, so the agent only needs to copy the structure.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

SECTION_HEADER = "[WORKFLOW INTEGRATION CONTRACT]"


def inject_workflow_integration_contract(agent, messages: List[Dict[str, Any]]) -> None:
    """
    update_agent_state hook: inject the AgentGenerator workflow integration contract.

    Reads generated_workflow_* context variables and appends a [WORKFLOW INTEGRATION
    CONTRACT] block to the agent system message. The block contains the exact
    capability_id, startup_mode, and trigger_events the agent must use — no inference
    or derivation allowed.

    Exits silently when no AgentGenerator workflow exists for this app.
    """
    try:
        context_variables = getattr(agent, "context_variables", {})

        capability_id: str | None = context_variables.get("generated_workflow_capability_id")
        if not capability_id:
            return  # No workflow generated for this app — nothing to inject.

        workflow_name: str = context_variables.get("generated_workflow_name") or capability_id
        startup_mode: str = context_variables.get("generated_workflow_startup_mode") or "UserDriven"
        trigger_events: list = context_variables.get("generated_workflow_trigger_events") or []

        if agent.name == "AppPlanAgent":
            content = _build_app_plan_block(workflow_name, capability_id, startup_mode, trigger_events)
        elif agent.name == "ConfigMiddlewareAgent":
            content = _build_config_middleware_block(capability_id, trigger_events)
        else:
            content = _build_generic_block(workflow_name, capability_id, startup_mode)

        current = agent.system_message

        if SECTION_HEADER in current:
            # Replace the existing section (idempotent across repeated hook calls).
            pre, _, rest = current.partition(SECTION_HEADER)
            next_section = rest.find("\n\n[")
            after = rest[next_section:] if next_section > 0 else ""
            new_message = f"{pre.rstrip()}\n\n{SECTION_HEADER}\n{content}{after}"
        else:
            new_message = f"{current}\n\n{SECTION_HEADER}\n{content}"

        if new_message != current:
            agent.update_system_message(new_message)
            logger.info(
                "[%s] Injected workflow integration contract (capability_id=%s, startup_mode=%s, trigger_events=%d)",
                agent.name,
                capability_id,
                startup_mode,
                len(trigger_events),
            )

    except Exception as exc:
        logger.error("[%s] Failed to inject workflow integration contract: %s", agent.name, exc)


# ---------------------------------------------------------------------------
# Per-agent content builders
# ---------------------------------------------------------------------------

def _build_app_plan_block(
    workflow_name: str,
    capability_id: str,
    startup_mode: str,
    trigger_events: list,
) -> str:
    """
    Contract block for AppPlanAgent.

    Provides the exact capability_id to embed in workflow_capability_ids, the
    ui_open_behavior derived from startup_mode, and the trigger_events list to
    forward in every module_contract build task initial_message.
    """
    ui_behavior = (
        "SEND an initial trigger message when opening the chat session (AgentDriven)"
        if startup_mode == "AgentDriven"
        else "open the channel and WAIT — do not send an initial message (UserDriven)"
    )

    lines = [
        "A workflow has been produced by AgentGenerator. Use the values below verbatim.",
        "Do not derive, infer, rename, or alter them.",
        "",
        f"  workflow_name  : {workflow_name}",
        f"  capability_id  : {capability_id}",
        f"  startup_mode   : {startup_mode}",
        f"  ui_open_behavior: app UI must {ui_behavior}",
    ]

    if trigger_events:
        lines += [
            "",
            "  trigger_events (include this full list in every module_contract build task initial_message):",
        ]
        for ev in trigger_events:
            event_type = ev.get("event_type", str(ev)) if isinstance(ev, dict) else str(ev)
            lines.append(f"    - event_type: {event_type}  →  workflow capability: {capability_id}")
    else:
        lines += [
            "",
            "  trigger_events: (none declared — no workflow subscriptions are required)",
        ]

    lines += [
        "",
        "HARD CONSTRAINTS — violating any of these is an error:",
        f'  1. workflow_capability_ids in ALL event_flows MUST be ["{capability_id}"].',
        f'     Never use the raw workflow name "{workflow_name}" in workflow_capability_ids.',
        "  2. Every module_contract build task initial_message MUST include the trigger_events list above.",
        "  3. startup_mode determines the UI chat session open behavior — do not override it.",
    ]

    return "\n".join(lines)


def _build_config_middleware_block(capability_id: str, trigger_events: list) -> str:
    """
    Contract block for ConfigMiddlewareAgent.

    Provides the exact subscriptions.yaml entries to generate, pre-formatted,
    so the agent does not need to derive them from natural-language instructions.
    """
    lines = [
        "An AgentGenerator workflow must be wired into this module's subscriptions.yaml.",
        "",
        f"  capability_id (subscription target): {capability_id}",
        "  Use this value as target.capability_id — never the raw workflow name.",
    ]

    if trigger_events:
        lines += [
            "",
            "  subscriptions.yaml entries to generate (one per trigger event listed below):",
        ]
        for ev in trigger_events:
            event_type = ev.get("event_type", str(ev)) if isinstance(ev, dict) else str(ev)
            lines += [
                f"    - event_type: {event_type}",
                f"      target:",
                f"        kind: capability",
                f"        capability_id: {capability_id}",
            ]
    else:
        lines += [
            "",
            "  trigger_events: (none declared — no workflow subscription entries are needed)",
        ]

    lines += [
        "",
        "HARD CONSTRAINT: subscription targets that invoke a workflow MUST use",
        f'  target.kind: capability and target.capability_id: "{capability_id}".',
        "  Do not use a raw workflow name as a target.",
    ]

    return "\n".join(lines)


def _build_generic_block(workflow_name: str, capability_id: str, startup_mode: str) -> str:
    return "\n".join([
        f"AgentGenerator workflow: {workflow_name}",
        f"  capability_id : {capability_id}  (use in workflow_capability_ids — not the workflow name)",
        f"  startup_mode  : {startup_mode}",
    ])


__all__ = ["inject_workflow_integration_contract"]
