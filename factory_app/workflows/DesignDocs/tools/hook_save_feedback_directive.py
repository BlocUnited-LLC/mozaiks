"""
Hook: Lead DesignDocsAgent's repair turn with the save rejection

Fires as a prompt middleware function on DesignDocsAgent.

A refused save routes DesignDocsAgent back to itself. The newest message on that
turn is its own rejected bundle, and the reason was only one context value among
dozens at the end of a long prompt. In the 2026-11-10 live traversal
(mozaiks#1005) the agent resubmitted the identical access_policy on every repair
attempt until the budget ran out, with the reason present in that context each time.

When the last save outcome asks for a repair, this hook puts the reason first,
as the task for the turn. It carries no authority: the save tool still
validates the next bundle, and the repair budget is unchanged.
"""

from __future__ import annotations

import logging
from typing import Any

from mozaiksai.core.workflow.context.frozen import detach

logger = logging.getLogger(__name__)

_HEADER = "[REPAIR REQUIRED]"
# The outcomes that route back to this agent for another bundle.
_REPAIR_OUTCOMES = frozenset({"revise", "review_changes"})


def _context_get(context_variables: Any, key: str) -> Any:
    getter = getattr(context_variables, "get", None)
    if callable(getter):
        return detach(getter(key))
    if isinstance(context_variables, dict):
        return context_variables.get(key)
    return None


def inject_save_feedback_directive(agent: Any, messages: list[dict[str, Any]]) -> None:
    """Prepend the last save rejection to DesignDocsAgent's system message."""
    if getattr(agent, "name", "") != "DesignDocsAgent":
        return
    context_variables = getattr(agent, "context_variables", None)
    outcome = _context_get(context_variables, "design_docs_save_outcome")
    feedback = str(_context_get(context_variables, "design_docs_save_feedback") or "").strip()
    if outcome not in _REPAIR_OUTCOMES or not feedback:
        return
    current = getattr(agent, "system_message", None) or getattr(agent, "_system_message", "") or ""
    if current.startswith(_HEADER):
        return
    body = (
        "Your previous design bundle was not saved. Reason:\n"
        f"{feedback}\n"
        "The bundle above is the rejected one. Resubmitting it unchanged is rejected again "
        "and uses up a repair attempt. Change what the reason names, keep the rest of the "
        "design, and output the complete corrected bundle."
    )
    agent.update_system_message(f"{_HEADER}\n{body}\n\n{current}")
    logger.info("[DesignDocsAgent] Led the repair turn with the save rejection (outcome=%s)", outcome)
