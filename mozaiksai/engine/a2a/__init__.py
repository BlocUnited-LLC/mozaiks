"""A2A (Agent-to-Agent) remote agent support for Mozaiks.

This package provides utilities for creating AG2 ``A2aRemoteAgent`` instances
that proxy to remote A2A-protocol servers.  Workflows can declare remote
agents in their ``agents.json`` configuration using ``"type": "a2a_remote"``.

Usage in workflow config:
::

    {
      "agents": [
        {
          "name": "RemoteAnalyst",
          "type": "a2a_remote",
          "a2a_url": "https://analyst-service.example.com/a2a",
          "system_message": "You are a remote analysis agent."
        }
      ]
    }

The runtime resolves ``a2a_remote`` type agents via :func:`create_a2a_agent`
and injects them into the workflow alongside local ConversableAgents.
"""

from mozaiksai.engine.a2a.remote import create_a2a_agent, is_a2a_agent_config

__all__ = [
    "create_a2a_agent",
    "is_a2a_agent_config",
]
