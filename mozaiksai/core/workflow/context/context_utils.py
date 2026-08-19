# ==============================================================================
# FILE: mozaiksai/core/workflow/context/context_utils.py
# DESCRIPTION: Context variable exposure utilities for agent system messages
# ==============================================================================
"""
Context exposure utilities for mozaiksai agent factory.

Purpose:
- Convert context containers to dictionaries
- Stringify context values for display
- Render context exposure fragments with templates
- Build context exposure update hooks for AG2 agents
- Merge message parts with placement control

Extracted from agents/factory.py to improve modularity and maintainability.
"""

from __future__ import annotations

import logging
import string
from collections import defaultdict
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    'context_to_dict',
    'apply_context_exposures',
]


# ==============================================================================
# CONTEXT CONVERSION UTILITIES
# ==============================================================================

def context_to_dict(container: Any) -> dict[str, Any]:
    """Convert context container to dictionary.
    
    Supports detached context surfaces only:
    - Objects with snapshot() method
    - Objects with to_dict() method
    - Plain dictionaries
    
    Args:
        container: Context container object
        
    Returns:
        Dictionary representation of context
    """
    if hasattr(container, "snapshot") and callable(getattr(container, "snapshot", None)):
        try:
            snap = container.snapshot()
            if isinstance(snap, dict):
                return snap
        except Exception:  # pragma: no cover
            pass
    try:
        if hasattr(container, "to_dict"):
            data = container.to_dict()
            return dict(data) if isinstance(data, dict) else {}
    except Exception:  # pragma: no cover
        pass
    if isinstance(container, dict):
        return dict(container)
    return {}


def stringify_context_value(value: Any, null_label: str | None) -> str:
    """Convert context value to display string.
    
    Args:
        value: Context value to stringify
        null_label: Label to use for None values (defaults to "None")
        
    Returns:
        String representation of value
    """
    if value is None:
        return null_label if null_label is not None else "None"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def format_template(template: str, mapping: dict[str, Any]) -> str:
    """Format template string with variable mapping.
    
    Args:
        template: Template string with {variable} placeholders
        mapping: Dictionary of variable names to values
        
    Returns:
        Formatted string
    """
    formatter = string.Formatter()
    try:
        return formatter.vformat(template, (), mapping)
    except Exception:  # pragma: no cover
        return template


# ==============================================================================
# EXPOSURE RENDERING
# ==============================================================================

def render_exposure_fragment(
    exposure: dict[str, Any],
    context_dict: dict[str, Any],
    fallback_variables: list[str],
) -> str:
    """Render a single exposure fragment with template and variables.
    
    Args:
        exposure: Exposure configuration dict with optional keys:
            - variables: List of variable names to expose
            - template: Optional template string
            - null_label: Label for null values
            - header: Optional header text
        context_dict: Current context variable values
        fallback_variables: Default variables if exposure.variables is empty
        
    Returns:
        Rendered exposure fragment string
    """
    if not isinstance(exposure, dict):
        return ""

    raw_variables = exposure.get("variables") or fallback_variables or []
    variables = [str(var).strip() for var in raw_variables if isinstance(var, str) and str(var).strip()]
    if not variables:
        return ""

    null_label = exposure.get("null_label")
    mapping = defaultdict(lambda: stringify_context_value(None, null_label))

    for key, value in context_dict.items():
        mapping[key] = stringify_context_value(value, null_label if key in variables else None)

    for var in variables:
        if var not in mapping:
            mapping[var] = stringify_context_value(context_dict.get(var), null_label)

    template = exposure.get("template")
    if template:
        rendered_body = format_template(str(template), mapping)
    else:
        rendered_body = "\n".join(f"{var.upper()}: {mapping[var]}" for var in variables)

    if not isinstance(rendered_body, str) or not rendered_body.strip():
        return ""

    header = exposure.get("header")
    if isinstance(header, str) and header.strip():
        return f"{header.strip()}\n{rendered_body.strip()}"

    return rendered_body.strip()



# ==============================================================================
# MESSAGE COMPOSITION
# ==============================================================================

def merge_message_parts(existing: str, fragment: str, placement: str) -> str:
    """Merge new fragment into existing message with placement control.
    
    Args:
        existing: Existing message content
        fragment: New fragment to add
        placement: Placement mode - "replace", "prepend", or "append"
        
    Returns:
        Merged message string
    """
    placement_mode = (placement or "append").lower() if isinstance(placement, str) else "append"
    fragment = fragment.strip() if isinstance(fragment, str) else ""
    existing = existing.strip() if isinstance(existing, str) else ""
    if not fragment:
        return existing
    if placement_mode == "replace":
        return fragment
    if placement_mode == "prepend":
        parts = [fragment, existing]
    else:
        parts = [existing, fragment]
    joined = "\n\n".join(part for part in parts if part)
    return joined


def apply_context_exposures(
    base_message: str,
    exposures: list[dict[str, Any]],
    context_dict: dict[str, Any],
    fallback_variables: list[str],
) -> str:
    """Apply all context exposures to base message.
    
    Args:
        base_message: Base system message
        exposures: List of exposure configurations
        context_dict: Current context variable values
        fallback_variables: Default variables if no exposures configured
        
    Returns:
        Message with all exposures applied
    """
    effective_exposures: list[dict[str, Any]] = [
        exposure for exposure in exposures if isinstance(exposure, dict)
    ]
    if not effective_exposures and fallback_variables:
        effective_exposures = [{"variables": list(fallback_variables)}]

    message = base_message or ""
    for exposure in effective_exposures:
        fragment = render_exposure_fragment(exposure, context_dict, fallback_variables)
        placement = exposure.get("placement", "append") if isinstance(exposure, dict) else "append"
        message = merge_message_parts(message, fragment, placement)
    return message or base_message or ""


