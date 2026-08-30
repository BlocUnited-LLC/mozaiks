from __future__ import annotations

import os
from typing import Literal

DATABASE_STARTUP_POLICY_ENV = "MOZAIKS_DATABASE_STARTUP_POLICY"
DatabaseStartupPolicy = Literal["best_effort", "required"]
MONGO_CONNECTION_ENV_NAMES = ("MONGO_URI", "MONGODB_URI", "MONGO_URL")


class DatabaseStartupPolicyError(ValueError):
    """Raised when database startup policy configuration is invalid."""


def get_database_startup_policy() -> DatabaseStartupPolicy:
    raw = (os.getenv(DATABASE_STARTUP_POLICY_ENV) or "best_effort").strip().lower()
    if raw in {"best_effort", "required"}:
        return raw  # type: ignore[return-value]
    raise DatabaseStartupPolicyError(
        f"{DATABASE_STARTUP_POLICY_ENV} must be 'best_effort' or 'required', got {raw!r}"
    )


def database_persistence_is_enabled(policy: DatabaseStartupPolicy) -> bool:
    """Return whether startup must establish persistence readiness."""
    if policy == "required":
        return True
    environment = os.getenv("ENV", os.getenv("ENVIRONMENT", "")).strip().lower()
    if environment == "production":
        return True
    return any((os.getenv(name) or "").strip() for name in MONGO_CONNECTION_ENV_NAMES)


__all__ = [
    "DATABASE_STARTUP_POLICY_ENV",
    "MONGO_CONNECTION_ENV_NAMES",
    "DatabaseStartupPolicy",
    "DatabaseStartupPolicyError",
    "database_persistence_is_enabled",
    "get_database_startup_policy",
]
