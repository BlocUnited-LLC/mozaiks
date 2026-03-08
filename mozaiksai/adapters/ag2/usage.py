"""AG2 usage summary adapter.

Wraps ``autogen.gather_usage_summary`` so that the engine orchestration
layer does not need a direct ``autogen`` import for usage reconciliation.
"""

from __future__ import annotations

from typing import Any, Dict, List


def collect_usage_summary(agents: List[Any]) -> Dict[str, Any]:
    """Gather token-usage and cost summary from a list of AG2 agents.

    Thin wrapper around ``autogen.gather_usage_summary``.  Keeping the
    ``autogen`` import here ensures the engine layer stays AG2-free.

    Parameters
    ----------
    agents : list
        AG2 ``ConversableAgent`` instances from the current run.

    Returns
    -------
    dict
        Usage summary dict with keys ``total_cost``,
        ``usage_including_cached``, ``usage_excluding_cached``.
    """
    try:
        from autogen import gather_usage_summary  # type: ignore[import]
        return gather_usage_summary(agents)  # type: ignore[return-value]
    except ImportError:
        return {}


__all__ = ["collect_usage_summary"]
