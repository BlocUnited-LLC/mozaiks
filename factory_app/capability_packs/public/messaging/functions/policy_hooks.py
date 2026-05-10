from __future__ import annotations

from factory_app.app.modules.communications.backend.policy import (
    validate_announcement_scope as _validate_announcement_scope,
)


def validate_announcement_scope(audience_scope: str) -> None:
    _validate_announcement_scope(audience_scope)


__all__ = ["validate_announcement_scope"]
