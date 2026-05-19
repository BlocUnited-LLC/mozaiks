from __future__ import annotations

import os
from typing import Literal

DATABASE_STARTUP_POLICY_ENV = "MOZAIKS_DATABASE_STARTUP_POLICY"
DatabaseStartupPolicy = Literal["best_effort", "required"]


class DatabaseStartupPolicyError(ValueError):
    """Raised when database startup policy configuration is invalid."""


def get_database_startup_policy() -> DatabaseStartupPolicy:
    raw = (os.getenv(DATABASE_STARTUP_POLICY_ENV) or "best_effort").strip().lower()
    if raw in {"best_effort", "required"}:
        return raw
    raise DatabaseStartupPolicyError(
        f"{DATABASE_STARTUP_POLICY_ENV} must be 'best_effort' or 'required', got {raw!r}"
    )


__all__ = [
    "DATABASE_STARTUP_POLICY_ENV",
    "DatabaseStartupPolicy",
    "DatabaseStartupPolicyError",
    "get_database_startup_policy",
]
