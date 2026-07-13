from __future__ import annotations

import os

# Registry mode controls whether secret presence is checked from the environment.
# In hosted multi-tenant deployments where secrets are not accessible per-tenant
# from os.environ, set MOZAIKS_INTEGRATIONS_REGISTRY_MODE=catalog_only to prevent
# false configured/missing readings.
_REGISTRY_MODE_ENV = "MOZAIKS_INTEGRATIONS_REGISTRY_MODE"
_MODE_CATALOG_ONLY = "catalog_only"


def get_registry_mode() -> str:
    return os.environ.get(_REGISTRY_MODE_ENV, "live").lower()


def is_catalog_only_mode() -> bool:
    return get_registry_mode() == _MODE_CATALOG_ONLY


def check_secret_presence(secret_names: list[str]) -> dict[str, bool]:
    """Return a mapping of secret name → whether it is set (non-empty) in the environment.

    Never returns secret values — only boolean presence.
    """
    if is_catalog_only_mode():
        return {name: False for name in secret_names}
    return {name: bool(os.environ.get(name, "").strip()) for name in secret_names}


def derive_status(required_secrets: list[str]) -> tuple[str, list[str]]:
    """Derive status and list of missing required secrets.

    Returns:
        (status, missing_secret_names)
        status is one of: "configured", "partial", "missing", "unknown"
    """
    if not required_secrets:
        # No secrets needed — always configured regardless of registry mode.
        return "configured", []

    if is_catalog_only_mode():
        return "unknown", []

    presence = check_secret_presence(required_secrets)
    missing = [name for name, present in presence.items() if not present]

    if not missing:
        return "configured", []
    if len(missing) < len(required_secrets):
        return "partial", missing
    return "missing", missing
