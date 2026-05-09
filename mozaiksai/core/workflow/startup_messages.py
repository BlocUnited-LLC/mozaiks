from __future__ import annotations

from typing import Any, Optional


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


def normalize_comparable_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def resolve_workflow_startup_mode(workflow_name: Optional[str]) -> Optional[str]:
    if not workflow_name:
        return None
    try:
        from mozaiksai.core.workflow.workflow_manager import workflow_manager

        cfg = workflow_manager.get_config(str(workflow_name)) or {}
        startup_mode = str(cfg.get("workflow_startup_mode") or "").strip().lower()
        return startup_mode or None
    except Exception:
        return None


def resolve_hidden_initial_message(
    workflow_name: Optional[str],
    *,
    workflow_startup_mode: Optional[str] = None,
) -> Optional[str]:
    normalized_startup_mode = str(
        workflow_startup_mode or resolve_workflow_startup_mode(workflow_name) or ""
    ).strip().lower()
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
    workflow_name: Optional[str],
    role: Optional[str],
    content: Any,
    agent_name: Optional[str],
    workflow_startup_mode: Optional[str] = None,
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
