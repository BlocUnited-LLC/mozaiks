"""Factory for A2A remote agents.

Creates AG2 ``A2aRemoteAgent`` instances from declarative workflow config.
These agents proxy all LLM interaction to a remote A2A-protocol server,
forwarding ``StreamEvent`` tokens through the IOStream bridge in real time.

Workflow config schema for A2A agents:
::

    {
      "name": "RemoteAnalyst",
      "type": "a2a_remote",
      "a2a_url": "https://analyst-service.example.com/a2a",
      "a2a_card_url": "https://analyst-service.example.com/.well-known/agent.json",
      "system_message": "Optional system context for this agent.",
      "max_reconnects": 3,
      "polling_interval": 0.5
    }

Either ``a2a_url`` or ``a2a_card_url`` must be provided.  The card URL
points to a standard A2A Agent Card (JSON) that the SDK will parse for
the agent name, description, and endpoint.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Supported agent type identifiers that indicate an A2A remote agent.
_A2A_TYPE_STRINGS = frozenset({"a2a_remote", "a2a", "remote"})


def is_a2a_agent_config(agent_config: Dict[str, Any]) -> bool:
    """Return True if the agent config declares a remote A2A agent."""
    agent_type = str(agent_config.get("type", "")).strip().lower()
    return agent_type in _A2A_TYPE_STRINGS


def create_a2a_agent(
    agent_name: str,
    agent_config: Dict[str, Any],
    *,
    context_variables: Any = None,
) -> Any:
    """Create an ``A2aRemoteAgent`` from workflow agent config.

    Parameters
    ----------
    agent_name : str
        Logical agent name within the workflow.
    agent_config : dict
        The agent's entry from the workflow ``agents`` list/dict.  Must
        contain either ``a2a_url`` or ``a2a_card_url``.
    context_variables : Any, optional
        AG2 context variables to attach.

    Returns
    -------
    A2aRemoteAgent
        An AG2 ConversableAgent subclass configured to proxy to the
        remote A2A server.

    Raises
    ------
    ImportError
        If the AG2 A2A extras are not installed.
    ValueError
        If the config is missing both ``a2a_url`` and ``a2a_card_url``.
    """
    try:
        from autogen.a2a.client import A2aRemoteAgent  # type: ignore
    except ImportError as exc:
        raise ImportError(
            "A2A remote agent support requires AG2 with A2A extras.  "
            "Install with: pip install 'ag2[a2a]'"
        ) from exc

    url: Optional[str] = agent_config.get("a2a_url") or os.getenv("MOZAIKS_A2A_DEFAULT_URL")
    card_url: Optional[str] = agent_config.get("a2a_card_url")

    max_reconnects = int(agent_config.get("max_reconnects", 3))
    polling_interval = float(agent_config.get("polling_interval", 0.5))
    silent = agent_config.get("silent", None)

    if card_url:
        # Prefer Agent Card discovery (provides name, description, endpoint)
        try:
            from autogen.a2a.client import A2aRemoteAgent as _A2A  # type: ignore

            agent = _A2A.from_card_url(
                card_url,
                name=agent_name,
                silent=silent,
                max_reconnects=max_reconnects,
                polling_interval=polling_interval,
            )
            logger.info(
                "[A2A] Created remote agent '%s' from card URL %s",
                agent_name,
                card_url,
            )
            _tag_agent(agent, agent_config, context_variables)
            return agent
        except AttributeError:
            # from_card_url may not exist in all AG2 versions; fall through
            logger.warning("[A2A] from_card_url not available; falling back to direct URL")

    if not url:
        raise ValueError(
            f"A2A agent '{agent_name}' requires either 'a2a_url' or 'a2a_card_url' "
            f"in workflow config (or set MOZAIKS_A2A_DEFAULT_URL env var)."
        )

    agent = A2aRemoteAgent(
        url=url,
        name=agent_name,
        silent=silent,
        max_reconnects=max_reconnects,
        polling_interval=polling_interval,
    )
    logger.info("[A2A] Created remote agent '%s' -> %s", agent_name, url)
    _tag_agent(agent, agent_config, context_variables)
    return agent


def _tag_agent(agent: Any, config: Dict[str, Any], context_variables: Any) -> None:
    """Apply Mozaiks runtime metadata to the agent."""
    setattr(agent, "_mozaiks_auto_tool_mode", False)
    setattr(agent, "_mozaiks_a2a_remote", True)
    setattr(agent, "_mozaiks_a2a_url", config.get("a2a_url", ""))

    if context_variables is not None and hasattr(agent, "context_variables"):
        try:
            agent.context_variables = context_variables
        except Exception:
            pass
