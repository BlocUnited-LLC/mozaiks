"""
Hook: Inject Joke Preferences
Injects user preferences into JokeWriterAgent context before it runs.
"""

from typing import Any, Dict, Optional
import logging

logger = logging.getLogger(__name__)


def inject_joke_preferences(
    agent,
    messages: list,
    context_variables: Dict[str, Any],
    **kwargs
) -> Dict[str, Any]:
    """
    Inject joke preferences into the agent's context.

    This hook runs before JokeWriterAgent generates jokes,
    ensuring it has access to the user's preferences.

    Args:
        agent: The agent being updated
        messages: Current message history
        context_variables: Current context variable state

    Returns:
        Updated state with preference injection
    """
    joke_style = context_variables.get("joke_style", "general")
    joke_topic = context_variables.get("joke_topic", "anything")
    joke_count = context_variables.get("joke_count", 3)

    # Build preference summary
    preference_summary = f"""

[JOKE PREFERENCES]
- Style: {joke_style}
- Topic: {joke_topic}
- Count: {joke_count} jokes requested

Please generate exactly {joke_count} original {joke_style} jokes about {joke_topic}.
"""

    logger.info(f"Injecting preferences: style={joke_style}, topic={joke_topic}, count={joke_count}")

    return {
        "context_injection": preference_summary,
        "context_updates": {},
    }
