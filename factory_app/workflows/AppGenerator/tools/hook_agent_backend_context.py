"""
Hook: Inject Agent Backend Context

Injects agent backend context (websocket_config, agent_websocket_url, agent_api_url)
into the agent's system message when available.

This enables agents to know about upstream AgentGenerator exports without
explicitly naming agents in the conversation.
"""

import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


def inject_agent_backend_context(agent, messages: List[Dict[str, Any]]) -> None:
    """
    Update agent state hook to inject agent backend context.
    
    Checks context_variables for websocket_config, agent_websocket_url, agent_api_url
    and appends them to the system message in a structured format.
    """
    try:
        context_variables = getattr(agent, "context_variables", {})
        
        # Extract agent backend context
        websocket_config = context_variables.get("websocket_config")
        agent_websocket_url = context_variables.get("agent_websocket_url")
        agent_api_url = context_variables.get("agent_api_url")
        agent_names = context_variables.get("agent_names", [])
        tool_names = context_variables.get("tool_names", [])
        
        # Only proceed if we have agent backend context
        if not any([websocket_config, agent_websocket_url, agent_api_url]):
            return
        
        # Build context string
        context_parts = []
        context_parts.append("Agent backend integration is available for this app.")
        
        if agent_websocket_url:
            context_parts.append(f"- WebSocket URL: {agent_websocket_url}")
        if agent_api_url:
            context_parts.append(f"- API URL: {agent_api_url}")
        if agent_names:
            context_parts.append(f"- Available Agents: {', '.join(agent_names)}")
        if tool_names:
            context_parts.append(f"- Available Tools: {', '.join(tool_names)}")
        
        if websocket_config and isinstance(websocket_config, dict):
            endpoints = websocket_config.get("endpoints", {})
            if endpoints:
                context_parts.append("- WebSocket Endpoints:")
                for name, ep in endpoints.items():
                    path = ep.get("path", "")
                    if path:
                        context_parts.append(f"  - {name}: {path}")
        
        context_str = "\n".join(context_parts)
        
        # Inject into system message
        header = "\n\n[AGENT BACKEND CONTEXT]"
        current_system_message = agent.system_message
        
        if "[AGENT BACKEND CONTEXT]" in current_system_message:
            # Update existing section
            parts = current_system_message.split("[AGENT BACKEND CONTEXT]")
            base_message = parts[0].strip()
            # Check if there's more content after (like [CODE CONTEXT])
            if len(parts) > 1:
                remaining = parts[1]
                # Find next section header
                next_section_start = remaining.find("\n\n[")
                if next_section_start > 0:
                    remaining_sections = remaining[next_section_start:]
                else:
                    remaining_sections = ""
                new_system_message = f"{base_message}{header}\n{context_str}{remaining_sections}"
            else:
                new_system_message = f"{base_message}{header}\n{context_str}"
        else:
            # Insert before [CODE CONTEXT] if it exists, otherwise append
            if "[CODE CONTEXT]" in current_system_message:
                parts = current_system_message.split("[CODE CONTEXT]")
                new_system_message = f"{parts[0].strip()}{header}\n{context_str}\n\n[CODE CONTEXT]{parts[1]}"
            else:
                new_system_message = f"{current_system_message}{header}\n{context_str}"
        
        if new_system_message != current_system_message:
            agent.update_system_message(new_system_message)
            logger.info(f"[{agent.name}] Injected agent backend context ({len(context_str)} chars)")
            
    except Exception as e:
        logger.error(f"[{agent.name}] Failed to inject agent backend context: {e}")


__all__ = ["inject_agent_backend_context"]
