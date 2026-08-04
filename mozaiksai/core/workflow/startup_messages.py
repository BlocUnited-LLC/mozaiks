from __future__ import annotations

from typing import Any, Literal, TypedDict

WorkflowInteractionMode = Literal["agent_directed", "user_guided", "backend_only"]
WorkflowLaunchBehavior = Literal["auto_start", "wait_for_user", "none"]
WorkflowHandoffStyle = Literal["continuous_chat", "separate_chat", "background"]


class WorkflowLaunchTaxonomy(TypedDict):
    startup_mode: str | None
    interaction_mode: WorkflowInteractionMode | None
    launch_behavior: WorkflowLaunchBehavior | None
    handoff_style: WorkflowHandoffStyle | None

_HIDDEN_INITIAL_MESSAGE_SENDERS = {
    "",
    "_user",
    "agentmanager",
    "chat_manager",
    "manager",
    "user",
    "userproxy",
    "userproxyagent",
}

_WORKFLOW_INTERACTION_MODES: set[str] = {
    "agent_directed",
    "user_guided",
    "backend_only",
}
_WORKFLOW_LAUNCH_BEHAVIORS: set[str] = {
    "auto_start",
    "wait_for_user",
    "none",
}
_WORKFLOW_HANDOFF_STYLES: set[str] = {
    "continuous_chat",
    "separate_chat",
    "background",
}

_STARTUP_MODE_DEFAULT_TAXONOMY: dict[str, WorkflowLaunchTaxonomy] = {
    "agentdriven": {
        "startup_mode": "agentdriven",
        "interaction_mode": "agent_directed",
        "launch_behavior": "auto_start",
        "handoff_style": "continuous_chat",
    },
    "userdriven": {
        "startup_mode": "userdriven",
        "interaction_mode": "user_guided",
        "launch_behavior": "auto_start",
        "handoff_style": "continuous_chat",
    },
    "backendonly": {
        "startup_mode": "backendonly",
        "interaction_mode": "backend_only",
        "launch_behavior": "none",
        "handoff_style": "background",
    },
}


def normalize_comparable_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def normalize_workflow_startup_mode(value: Any) -> str | None:
    normalized = str(value or "").strip().lower()
    return normalized or None


def _normalize_contract_value(value: Any, allowed: set[str]) -> str | None:
    normalized = str(value or "").strip().lower()
    if normalized in allowed:
        return normalized
    return None


def _registry_workflow_entry(workflow_name: str | None) -> Any | None:
    if not workflow_name:
        return None
    try:
        from mozaiksai.core.workflow.pack.config import (
            get_workflow_entry,
            load_global_pack_graph,
        )

        pack = load_global_pack_graph()
        if pack is None:
            return None
        return get_workflow_entry(pack, str(workflow_name))
    except Exception:
        return None


def resolve_workflow_startup_mode(workflow_name: str | None) -> str | None:
    if not workflow_name:
        return None
    try:
        from mozaiksai.core.workflow.workflow_manager import workflow_manager

        cfg = workflow_manager.get_config(str(workflow_name)) or {}
        return normalize_workflow_startup_mode(cfg.get("workflow_startup_mode"))
    except Exception:
        return None


def resolve_workflow_interaction_mode(
    workflow_startup_mode: str | None,
    *,
    interaction_mode: str | None = None,
) -> WorkflowInteractionMode | None:
    explicit = _normalize_contract_value(interaction_mode, _WORKFLOW_INTERACTION_MODES)
    if explicit:
        return explicit  # type: ignore[return-value]
    normalized_startup_mode = normalize_workflow_startup_mode(workflow_startup_mode)
    default = _STARTUP_MODE_DEFAULT_TAXONOMY.get(normalized_startup_mode or "")
    return default["interaction_mode"] if default else None


def resolve_workflow_launch_behavior(
    workflow_startup_mode: str | None,
    *,
    launch_behavior: str | None = None,
) -> WorkflowLaunchBehavior | None:
    explicit = _normalize_contract_value(launch_behavior, _WORKFLOW_LAUNCH_BEHAVIORS)
    if explicit:
        return explicit  # type: ignore[return-value]
    normalized_startup_mode = normalize_workflow_startup_mode(workflow_startup_mode)
    default = _STARTUP_MODE_DEFAULT_TAXONOMY.get(normalized_startup_mode or "")
    return default["launch_behavior"] if default else None


def resolve_workflow_handoff_style(
    workflow_startup_mode: str | None,
    *,
    handoff_style: str | None = None,
) -> WorkflowHandoffStyle | None:
    explicit = _normalize_contract_value(handoff_style, _WORKFLOW_HANDOFF_STYLES)
    if explicit:
        return explicit  # type: ignore[return-value]
    normalized_startup_mode = normalize_workflow_startup_mode(workflow_startup_mode)
    default = _STARTUP_MODE_DEFAULT_TAXONOMY.get(normalized_startup_mode or "")
    return default["handoff_style"] if default else None


def resolve_workflow_launch_taxonomy(
    workflow_name: str | None,
    *,
    workflow_startup_mode: str | None = None,
) -> WorkflowLaunchTaxonomy:
    """Resolve the first-party conversation launch contract for a workflow."""
    entry = _registry_workflow_entry(workflow_name)
    startup_mode = (
        workflow_startup_mode
        or getattr(entry, "startup_mode", None)
        or resolve_workflow_startup_mode(workflow_name)
    )
    normalized_startup_mode = normalize_workflow_startup_mode(startup_mode)
    return {
        "startup_mode": normalized_startup_mode,
        "interaction_mode": resolve_workflow_interaction_mode(
            normalized_startup_mode,
            interaction_mode=getattr(entry, "interaction_mode", None),
        ),
        "launch_behavior": resolve_workflow_launch_behavior(
            normalized_startup_mode,
            launch_behavior=getattr(entry, "launch_behavior", None),
        ),
        "handoff_style": resolve_workflow_handoff_style(
            normalized_startup_mode,
            handoff_style=getattr(entry, "handoff_style", None),
        ),
    }


def should_autostart_empty_workflow(
    workflow_startup_mode: str | None,
    *,
    launch_behavior: str | None = None,
) -> bool:
    """Return whether an empty websocket workflow chat should begin immediately."""
    return (
        resolve_workflow_launch_behavior(
            workflow_startup_mode,
            launch_behavior=launch_behavior,
        )
        == "auto_start"
    )


def resolve_hidden_initial_message(
    workflow_name: str | None,
    *,
    workflow_startup_mode: str | None = None,
) -> str | None:
    normalized_startup_mode = normalize_workflow_startup_mode(
        workflow_startup_mode or resolve_workflow_startup_mode(workflow_name) or ""
    )
    if normalized_startup_mode and normalized_startup_mode != "agentdriven":
        return None
    if not workflow_name:
        return None
    try:
        from mozaiksai.core.workflow.workflow_manager import workflow_manager

        cfg = workflow_manager.get_config(str(workflow_name)) or {}
        initial_message = normalize_comparable_text(cfg.get("initial_message"))
        return initial_message or None
    except Exception:
        return None


def matches_hidden_initial_message(
    *,
    workflow_name: str | None,
    role: str | None,
    content: Any,
    agent_name: str | None,
    workflow_startup_mode: str | None = None,
) -> bool:
    hidden_initial_message = resolve_hidden_initial_message(
        workflow_name,
        workflow_startup_mode=workflow_startup_mode,
    )
    if not hidden_initial_message:
        return False

    normalized_content = normalize_comparable_text(content)
    if not normalized_content or normalized_content != hidden_initial_message:
        return False

    normalized_role = str(role or "").strip().lower()
    normalized_agent_name = str(agent_name or "").strip().lower()
    return (
        normalized_role == "user"
        and normalized_agent_name in _HIDDEN_INITIAL_MESSAGE_SENDERS
    )
