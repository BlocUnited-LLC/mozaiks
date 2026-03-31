"""
Save Jokes Tool
Persists generated jokes to context variables.
"""

from typing import Any, Dict, List, Optional
import logging

logger = logging.getLogger(__name__)


async def save_jokes(
    jokes: List[Dict[str, Any]],
    context_variables: Dict[str, Any],
    **kwargs
) -> Dict[str, Any]:
    """
    Save generated jokes to context variables.

    Args:
        jokes: List of joke objects with text, style, topic
        context_variables: Current context variable state

    Returns:
        Updated context and confirmation message
    """
    try:
        # Get existing jokes or initialize
        existing_jokes = context_variables.get("jokes_written", [])

        # Add new jokes
        all_jokes = existing_jokes + jokes

        # Update session count
        session_count = context_variables.get("session_joke_count", 0)
        new_count = session_count + len(jokes)

        return {
            "success": True,
            "context_updates": {
                "jokes_written": all_jokes,
                "session_joke_count": new_count,
            },
            "message": f"Saved {len(jokes)} jokes. Total this session: {new_count}",
        }

    except Exception as e:
        logger.error(f"Failed to save jokes: {e}")
        return {
            "success": False,
            "error": str(e),
        }
