"""
pattern_selection tool - stores AG2 pattern selection from PatternAgent.

Caches the selected pattern so update_agent_state hooks can inject pattern-specific
guidance into downstream agent prompts.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Dict, Optional

_logger = logging.getLogger("tools.pattern_selection")


def _cache_context_value(context_variables: Any, key: str, value: Any) -> None:
    if not context_variables:
        return
    try:
        setter = getattr(context_variables, "set", None)
        if callable(setter):
            setter(key, value)
            return
    except Exception as exc:  # pragma: no cover - defensive logging
        _logger.debug("Unable to cache %s via context_variables.set: %s", key, exc)

    try:
        data = getattr(context_variables, "data", None)
        if isinstance(data, dict):
            data[key] = value
    except Exception as exc:  # pragma: no cover - defensive logging
        _logger.debug("Unable to cache %s via context_variables.data: %s", key, exc)


def pattern_selection(
    *,
    PatternSelection: Annotated[Optional[Dict[str, Any]], "Pattern selection payload"],
    context_variables: Annotated[Optional[Any], "Context variables provided by AG2"] = None,
) -> str:
    """Persist pattern selection for downstream prompt injections."""

    if not PatternSelection or not isinstance(PatternSelection, dict):
        _logger.warning("pattern_selection called with no PatternSelection data")
        return "No pattern selection provided"

    _cache_context_value(context_variables, "PatternSelection", PatternSelection)

    is_multi = bool(PatternSelection.get("is_multi_workflow"))
    pack_name = PatternSelection.get("pack_name")
    if not isinstance(pack_name, str) or not pack_name.strip():
        pack_name = None

    workflows = PatternSelection.get("workflows")
    if not isinstance(workflows, list):
        workflows = []

    _cache_context_value(context_variables, "is_multi_workflow", is_multi)
    _cache_context_value(context_variables, "pack_name", pack_name)
    _cache_context_value(context_variables, "workflows_spec", workflows)

    _logger.info(
        "Cached PatternSelection: multi=%s pack=%s workflows=%d",
        is_multi,
        pack_name,
        len(workflows),
    )

    if is_multi:
        return f"Selected Pack: {pack_name or 'Unnamed Pack'} ({len(workflows)} workflows)"

    primary = None
    for wf in workflows:
        if isinstance(wf, dict) and wf.get("role") == "primary":
            primary = wf
            break
    if primary is None and workflows and isinstance(workflows[0], dict):
        primary = workflows[0]

    if not primary:
        return f"Selected Pack: {pack_name or 'Unnamed Pack'}"

    wf_name = primary.get("name") if isinstance(primary.get("name"), str) else "(unnamed)"
    pattern_id = primary.get("pattern_id")
    if not isinstance(pattern_id, int):
        pattern_id = None
    pattern_name = (
        primary.get("pattern_name") if isinstance(primary.get("pattern_name"), str) else "Unknown"
    )

    return f"Selected Workflow: {wf_name} — Pattern {pattern_id or '?'} ({pattern_name})"
