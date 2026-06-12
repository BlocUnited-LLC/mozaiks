"""
Shared utilities for factory workflow prompt middleware functions.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_FACTORY_BUILD_CONTEXT_ROOT = _REPO_ROOT / "factory_app" / "build_context"


def workflow_context_path(workflow_name: str, *relative_parts: str) -> Path:
    """Return a path inside a workflow's factory-owned build context."""

    workflow = str(workflow_name or "").strip()
    if not workflow:
        raise ValueError("workflow_name is required")
    parts = [str(part).strip() for part in relative_parts if str(part).strip()]
    if not parts:
        raise ValueError("at least one relative build-context path part is required")
    return _FACTORY_BUILD_CONTEXT_ROOT / workflow / Path(*parts)


def update_agent_section(agent: Any, header: str, body: str) -> None:
    """Append or replace a named ``[HEADER]`` section in the agent system message.

    Replaces the section in-place when the header already exists, preserving any
    sections that follow it.  Appends when the header is new.

    Args:
        agent:  The AG2 agent whose system message will be updated.
        header: The bracketed section header, e.g. ``"[MY SECTION]"``.
        body:   The section body text (no header prefix).
    """
    try:
        current: str = (
            getattr(agent, "system_message", None)
            or getattr(agent, "_system_message", "")
            or ""
        )
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

        updater = getattr(agent, "update_system_message", None)
        if callable(updater):
            updater(new_message)
        elif hasattr(agent, "_system_message"):
            agent._system_message = new_message
        else:
            agent._system_message = new_message

    except Exception as exc:
        logger.error(
            "[%s] Failed to update system message section %s: %s",
            getattr(agent, "name", "?"),
            header,
            exc,
        )


__all__ = ["update_agent_section", "workflow_context_path"]

