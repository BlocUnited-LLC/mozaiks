# ==============================================================================
# FILE: mozaiksai/core/workflow/orchestration_utils.py
# DESCRIPTION: Workflow orchestration configuration helpers.
# ==============================================================================

"""
Orchestration Utilities

Helper functions for workflow orchestration.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


# ===================================================================
# CONFIG NORMALIZATION
# ===================================================================

def _normalize_human_in_the_loop(value) -> bool:
    """Normalize human_in_the_loop config values to a strict boolean.

    Accepts booleans directly, and common string/int representations:
    - True-y: "true", "yes", "1", "on", "always"
    - False-y: "false", "no", "0", "of", "never"
    Any other value defaults to False.
    """
    if isinstance(value, bool):
        return value
    try:
        if isinstance(value, (int, float)):
            return bool(int(value))
    except Exception:
        pass
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"true", "yes", "1", "on", "always"}:
            return True
        if v in {"false", "no", "0", "of", "never"}:
            return False
    return False


def _load_workflow_config(workflow_name: str) -> dict[str, Any]:
    """Load workflow configuration block."""
    from .workflow_manager import workflow_manager
    config = workflow_manager.get_config(workflow_name)
    return {
        "config": config,
        "max_turns": config.get("max_turns", 50),
        "orchestration_pattern": config.get("orchestration_pattern", "ag2_network"),
        "workflow_startup_mode": config.get("workflow_startup_mode", "AgentDriven"),
        "human_in_loop": _normalize_human_in_the_loop(config.get("human_in_the_loop", False)),
        "initial_agent_name": config.get("initial_agent", None),
    }


