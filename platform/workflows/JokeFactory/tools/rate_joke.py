"""
Rate Joke Tool
Records ratings and feedback for jokes.
"""

from typing import Any, Dict, Optional
import logging

logger = logging.getLogger(__name__)


async def rate_joke(
    joke_index: int,
    rating: int,
    feedback: str,
    context_variables: Dict[str, Any],
    **kwargs
) -> Dict[str, Any]:
    """
    Save a joke rating.

    Args:
        joke_index: Index of the joke being rated
        rating: Numeric rating (1-5)
        feedback: Brief feedback comment
        context_variables: Current context variable state

    Returns:
        Confirmation of the rating
    """
    try:
        # Validate rating
        rating = max(1, min(5, rating))

        # Create emoji representation
        emoji_map = {
            5: "😂😂😂",
            4: "😂😂",
            3: "😂",
            2: "😊",
            1: "😐",
        }
        emoji_rating = emoji_map.get(rating, "😊")

        rating_record = {
            "joke_index": joke_index,
            "rating": rating,
            "emoji_rating": emoji_rating,
            "feedback": feedback,
        }

        logger.info(f"Rated joke #{joke_index}: {rating}/5 - {feedback}")

        return {
            "success": True,
            "rating": rating_record,
            "message": f"Joke #{joke_index + 1} rated: {emoji_rating}",
        }

    except Exception as e:
        logger.error(f"Failed to rate joke: {e}")
        return {
            "success": False,
            "error": str(e),
        }
