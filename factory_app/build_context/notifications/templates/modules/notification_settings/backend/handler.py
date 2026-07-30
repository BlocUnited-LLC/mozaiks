from __future__ import annotations

from .base_handler import NotificationSettingsBaseHandler


class NotificationSettingsHandler(NotificationSettingsBaseHandler):
    """App-owned handler — preserved across regeneration.

    Override base methods here to add app-specific pre/post logic
    (e.g., enforce plan-gated channels, validate phone numbers for SMS).
    """
