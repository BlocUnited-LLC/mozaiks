"""
Data & Persistence Module - Clean Architecture
Provides database access and real-time AG2 persistence utilities.
"""

from .models import WorkflowStatus, ChatSessionDoc

ChatSession = ChatSessionDoc  # Backward compatibility alias
from .persistence import (
    PersistenceManager,
    AG2PersistenceManager,
    get_db_manager,
)
from .themes import (
    ThemeManager,
    ThemeResponse,
    ThemeUpdateRequest,
    ThemeValidationResult,
    ThemeValidationError,
    validate_theme_update,
    validate_full_theme,
    auto_validate_theme,
    summarize_validation,
    validate_theme,
)

__all__ = [
    "WorkflowStatus",
    "ChatSessionDoc",
    "ChatSession",
    "PersistenceManager",
    "AG2PersistenceManager",
    "get_db_manager",
    "ThemeManager",
    "ThemeResponse",
    "ThemeUpdateRequest",
    "ThemeValidationResult",
    "ThemeValidationError",
    "validate_theme_update",
    "validate_full_theme",
    "auto_validate_theme",
    "summarize_validation",
    "validate_theme",
]
