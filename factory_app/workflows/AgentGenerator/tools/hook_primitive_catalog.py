from __future__ import annotations

import logging
from typing import Any, Dict, List

from mozaiksai.core.workflow.ui_primitives import (
    format_component_ui_primitive_guidance,
)
from mozaiksai.core.workflow.transition_ui_catalog import (
    format_transition_ui_catalog_guidance,
)
from mozaiksai.core.workflow.workflow_ui_catalog import (
    format_workflow_ui_catalog_guidance,
)

logger = logging.getLogger(__name__)

_HEADER = "[SHIPPED UI PRIMITIVES]"
_TARGET_AGENTS = {"ToolPlanningAgent", "ToolsManagerAgent", "UIFileGenerator"}
_WORKFLOW_HEADER = "[WORKFLOW UI PRIMITIVE CATALOG]"
_TRANSITION_HEADER = "[TRANSITION UI PRIMITIVE CATALOG]"
_TRANSITION_TARGET_AGENTS = {"PackMetadataAgent", "UIFileGenerator"}


def _apply_system_message(agent: Any, message: str) -> None:
    updater = getattr(agent, "update_system_message", None)
    if callable(updater):
        updater(message)
    elif hasattr(agent, "_system_message"):
        agent._system_message = message
    else:
        setattr(agent, "_system_message", message)
    setattr(agent, "_mozaiks_base_system_message", message)


def _update_section(agent: Any, header: str, body: str) -> None:
    current = getattr(agent, "system_message", None) or getattr(agent, "_system_message", "") or ""
    section = f"{header}\n{body}"
    if header in current:
        prefix = current.split(header, 1)[0].rstrip()
        new_message = f"{prefix}\n\n{section}" if prefix else section
    else:
        new_message = f"{current}\n\n{section}" if current else section

    if new_message == current:
        return

    _apply_system_message(agent, new_message)


def inject_primitive_catalog(agent: Any, messages: List[Dict[str, Any]]) -> None:
    agent_name = getattr(agent, "name", "")
    if agent_name not in _TARGET_AGENTS and agent_name not in _TRANSITION_TARGET_AGENTS:
        return

    try:
        if agent_name in _TARGET_AGENTS:
            guidance = format_component_ui_primitive_guidance()
            rules = [
                "Rules:",
                "- Use only the shipped primitive names listed here.",
                "- If the desired UI is richer, compose it from these primitives instead of inventing a new primitive name.",
                "- If upstream context requests an unknown primitive, treat that as invalid rather than silently substituting.",
            ]
            if agent_name == "ToolPlanningAgent":
                rules.append("- `primitives_hint` must contain only names from this catalog.")
                rules.append("- When the workflow primitive catalog says a surface maps to a shipped shared component, prefer that component directly instead of inventing a wrapper name.")
            if agent_name == "UIFileGenerator":
                rules.append("- Generated React imports must come only from this catalog.")
                rules.append("- If the workflow primitive catalog maps a surface to a shipped shared component, do not generate bespoke React for it unless a thin wrapper is explicitly required.")

            body = f"{guidance}\n\n" + "\n".join(rules)
            _update_section(agent, _HEADER, body)
            _update_section(agent, _WORKFLOW_HEADER, format_workflow_ui_catalog_guidance())

        if agent_name in _TRANSITION_TARGET_AGENTS:
            _update_section(agent, _TRANSITION_HEADER, format_transition_ui_catalog_guidance())

        logger.info("[%s] Injected UI primitive catalogs", agent_name)
    except Exception as exc:
        logger.error("[%s] Failed to inject primitive catalog: %s", agent_name, exc)


__all__ = ["inject_primitive_catalog"]
