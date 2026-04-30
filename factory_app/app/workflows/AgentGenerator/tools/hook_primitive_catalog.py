from __future__ import annotations

import logging
from typing import Any, Dict, List

from mozaiksai.core.workflow.ui_primitives import (
    format_component_ui_primitive_guidance,
)

logger = logging.getLogger(__name__)

_HEADER = "[SHIPPED UI PRIMITIVES]"
_TARGET_AGENTS = {"ToolPlanningAgent", "UIFileGenerator"}


def _update_section(agent: Any, header: str, body: str) -> None:
    current = getattr(agent, "system_message", "") or ""
    section = f"{header}\n{body}"
    if header in current:
        prefix = current.split(header, 1)[0].rstrip()
        new_message = f"{prefix}\n\n{section}" if prefix else section
    else:
        new_message = f"{current}\n\n{section}" if current else section

    if new_message == current:
        return

    updater = getattr(agent, "update_system_message", None)
    if callable(updater):
        updater(new_message)
    else:
        agent.system_message = new_message


def inject_primitive_catalog(agent: Any, messages: List[Dict[str, Any]]) -> None:
    agent_name = getattr(agent, "name", "")
    if agent_name not in _TARGET_AGENTS:
        return

    try:
        guidance = format_component_ui_primitive_guidance()
        rules = [
            "Rules:",
            "- Use only the shipped primitive names listed here.",
            "- If the desired UI is richer, compose it from these primitives instead of inventing a new primitive name.",
            "- If upstream context requests an unknown primitive, treat that as invalid rather than silently substituting.",
        ]
        if agent_name == "ToolPlanningAgent":
            rules.append("- `primitives_hint` must contain only names from this catalog.")
        if agent_name == "UIFileGenerator":
            rules.append("- Generated React imports must come only from this catalog.")

        body = f"{guidance}\n\n" + "\n".join(rules)
        _update_section(agent, _HEADER, body)
        logger.info("[%s] Injected shipped UI primitive catalog", agent_name)
    except Exception as exc:
        logger.error("[%s] Failed to inject primitive catalog: %s", agent_name, exc)


__all__ = ["inject_primitive_catalog"]
