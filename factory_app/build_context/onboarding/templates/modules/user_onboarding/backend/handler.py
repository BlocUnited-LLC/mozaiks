from __future__ import annotations

from .base_handler import UserOnboardingBaseHandler


class UserOnboardingHandler(UserOnboardingBaseHandler):
    """App-owned handler — preserved across regeneration.

    Override base methods here to add app-specific pre/post logic
    (e.g., gate complete_step behind a plan check, fire analytics on dismissal).
    """
