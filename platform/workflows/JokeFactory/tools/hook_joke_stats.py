"""
Hook: Joke Statistics
Tracks joke generation statistics before messages are sent.
"""

from typing import Any, Dict, Optional
import logging

logger = logging.getLogger(__name__)


def update_joke_statistics(
    message: str,
    agent_name: str,
    context_variables: Dict[str, Any],
    **kwargs
) -> Dict[str, Any]:
    """
    Update joke statistics before sending critic's message.

    This hook runs before JokeCriticAgent sends its review,
    allowing us to track statistics.

    Args:
        message: The message about to be sent
        agent_name: Name of the sending agent
        context_variables: Current context variable state

    Returns:
        Updated message and context (if needed)
    """
    session_count = context_variables.get("session_joke_count", 0)

    # Add session stats footer
    stats_footer = f"\n\n---\n📊 *Session stats: {session_count} jokes generated*"

    logger.info(f"Adding stats footer: {session_count} jokes in session")

    return {
        "message": message + stats_footer,
        "context_updates": {},
    }
