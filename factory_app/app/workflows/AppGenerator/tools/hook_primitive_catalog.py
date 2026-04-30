from __future__ import annotations

import logging
from typing import Any, Dict, List

from mozaiksai.core.workflow.ui_primitives import format_page_ui_primitive_guidance

logger = logging.getLogger(__name__)

_HEADER = "[SHIPPED PAGE PRIMITIVES]"


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
    if agent_name != "AppSchemaAgent":
        return

    try:
        body = (
            f"{format_page_ui_primitive_guidance()}\n\n"
            "Rules:\n"
            "- Do not emit primitive names outside this shipped catalog.\n"
            "- Validate both top-level page sections and nested Grid child primitives against this catalog.\n"
            "- If the page needs a richer UX, compose it from these shipped primitives instead of inventing a new primitive."
        )
        _update_section(agent, _HEADER, body)
        logger.info("[%s] Injected shipped page primitive catalog", agent_name)
    except Exception as exc:
        logger.error("[%s] Failed to inject primitive catalog: %s", agent_name, exc)


__all__ = ["inject_primitive_catalog"]
