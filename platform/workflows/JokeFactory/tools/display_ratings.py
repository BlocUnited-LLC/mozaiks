"""
Display Ratings Tool (UI Tool)
Returns UI component data for displaying joke ratings.
"""

from typing import Any, Dict, List, Optional
import logging

logger = logging.getLogger(__name__)


async def display_ratings(
    ratings: List[Dict[str, Any]],
    context_variables: Dict[str, Any],
    **kwargs
) -> Dict[str, Any]:
    """
    Generate UI component data for displaying joke ratings.

    This is a UI_Tool that returns data for the JokeRatingsCard component.

    Args:
        ratings: List of rating objects
        context_variables: Current context variable state

    Returns:
        UI component specification
    """
    try:
        # Calculate average rating
        if ratings:
            avg_rating = sum(r.get("rating", 0) for r in ratings) / len(ratings)
        else:
            avg_rating = 0

        # Determine overall verdict
        if avg_rating >= 4.5:
            verdict = "🎉 Comedy Gold!"
        elif avg_rating >= 3.5:
            verdict = "👏 Solid Performance!"
        elif avg_rating >= 2.5:
            verdict = "😄 Not Bad!"
        else:
            verdict = "🎭 Room for Improvement"

        return {
            "success": True,
            "ui_component": "JokeRatingsCard",
            "ui_mode": "inline",
            "ui_data": {
                "ratings": ratings,
                "average_rating": round(avg_rating, 1),
                "total_jokes": len(ratings),
                "verdict": verdict,
            },
        }

    except Exception as e:
        logger.error(f"Failed to display ratings: {e}")
        return {
            "success": False,
            "error": str(e),
        }
