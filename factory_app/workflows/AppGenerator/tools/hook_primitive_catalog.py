from __future__ import annotations

import logging
from typing import Any

from mozaiksai.core.workflow.ui_primitives import format_generated_page_ui_primitive_guidance
from mozaiksai.core.workflow.ui_surface_taxonomy import format_ui_surface_taxonomy_guidance

logger = logging.getLogger(__name__)

_HEADER = "[SHIPPED PAGE PRIMITIVES]"
_SURFACE_HEADER = "[UI SURFACE TAXONOMY]"


def _apply_system_message(agent: Any, message: str) -> None:
    updater = getattr(agent, "update_system_message", None)
    if callable(updater):
        updater(message)
    elif hasattr(agent, "_system_message"):
        agent._system_message = message
    else:
        agent._system_message = message
    agent._mozaiks_base_system_message = message


def _update_section(agent: Any, header: str, body: str) -> None:
    current = getattr(agent, "system_message", None) or getattr(agent, "_system_message", "") or ""
    section = f"{header}\n{body}"
    if header in current:
        pre, _, rest = current.partition(header)
        next_section_idx = rest.find("\n\n[")
        after = rest[next_section_idx:] if next_section_idx > 0 else ""
        new_message = f"{pre.rstrip()}\n\n{section}{after}"
    else:
        new_message = f"{current}\n\n{section}" if current else section

    if new_message == current:
        return

    _apply_system_message(agent, new_message)


def inject_primitive_catalog(agent: Any, messages: list[dict[str, Any]]) -> None:
    agent_name = getattr(agent, "name", "")
    if agent_name != "AppSchemaAgent":
        return

    try:
        body = (
            f"{format_generated_page_ui_primitive_guidance()}\n\n"
            "Rules:\n"
            "- Do not emit primitive names outside this shipped catalog.\n"
            "- Validate both top-level page sections and nested Grid child primitives against this catalog.\n"
            "- If the page needs a richer UX, compose it from these shipped primitives instead of inventing a new primitive."
        )
        _update_section(agent, _SURFACE_HEADER, format_ui_surface_taxonomy_guidance())
        _update_section(agent, _HEADER, body)
        logger.info("[%s] Injected shipped page primitive catalog", agent_name)
    except Exception as exc:
        logger.error("[%s] Failed to inject primitive catalog: %s", agent_name, exc)
        _update_section(
            agent,
            _HEADER,
            "WARNING: Shipped primitive catalog could not be loaded. "
            "Do NOT emit any primitive names - escalate to the user before generating UI.",
        )


__all__ = ["inject_primitive_catalog"]

