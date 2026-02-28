"""
Theme management module.

Handles app theme customization and validation.
"""

from .theme_manager import ThemeManager, ThemeResponse, ThemeUpdateRequest
from .theme_validation import (
    ThemeValidationResult,
    ThemeValidationError,
    validate_theme,
    validate_theme_update,
    validate_full_theme,
    auto_validate_theme,
    summarize_validation,
)

__all__ = [
    'ThemeManager',
    'ThemeResponse',
    'ThemeUpdateRequest',
    'ThemeValidationResult',
    'ThemeValidationError',
    'validate_theme',
    'validate_theme_update',
    'validate_full_theme',
    'auto_validate_theme',
    'summarize_validation',
]

