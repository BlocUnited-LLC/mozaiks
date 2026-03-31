"""
Update Gallery Tool (UI Tool - Artifact)
Updates the JokeGallery artifact panel with all generated jokes.
"""

from typing import Any, Dict, List, Optional
import logging

logger = logging.getLogger(__name__)


async def update_gallery(
    jokes: List[Dict[str, Any]],
    context_variables: Dict[str, Any],
    **kwargs
) -> Dict[str, Any]:
    """
    Update the JokeGallery artifact with current jokes.

    This is a UI_Tool with mode=artifact that populates the
    JokeGallery component in the artifact panel.

    Args:
        jokes: List of joke objects with text, style, topic
        context_variables: Current context variable state

    Returns:
        UI component specification for artifact mode
    """
    try:
        # Gather all jokes from context
        all_jokes = context_variables.get("jokes_written", [])
        if jokes:
            all_jokes = all_jokes + jokes

        # Gather ratings if available
        ratings = context_variables.get("joke_ratings", [])

        # Calculate session stats
        session_stats = {
            "total_jokes": len(all_jokes),
            "average_rating": 0,
            "top_style": None,
        }

        # Calculate average rating
        if ratings:
            total_rating = sum(r.get("rating", 0) for r in ratings)
            session_stats["average_rating"] = round(total_rating / len(ratings), 1)

        # Find top style
        if all_jokes:
            style_counts = {}
            for joke in all_jokes:
                style = joke.get("style", "general")
                style_counts[style] = style_counts.get(style, 0) + 1
            if style_counts:
                session_stats["top_style"] = max(style_counts, key=style_counts.get)

        logger.info(f"Updating gallery with {len(all_jokes)} jokes")

        return {
            "success": True,
            "ui_component": "JokeGallery",
            "ui_mode": "artifact",
            "ui_data": {
                "jokes": all_jokes,
                "ratings": ratings,
                "session_stats": session_stats,
            },
        }

    except Exception as e:
        logger.error(f"Failed to update gallery: {e}")
        return {
            "success": False,
            "error": str(e),
        }
