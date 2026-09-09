"""Canonical deployment-environment vocabulary for the Mozaiks runtime.

One place interprets the deployment environment. Callers must not re-read or
re-parse ``ENV``/``ENVIRONMENT`` for policy decisions — they consume these
helpers so every boundary (auth resolution, startup validation, dispatch
authority) agrees on what environment the process is running in.

Existing repository vocabulary: the environment name comes from ``ENV`` with
``ENVIRONMENT`` as fallback, lowercased; an unset value means local/developer
operation. Only deployed, protected environments carry mandatory security
policy — everything else follows the local development contract.
"""

from __future__ import annotations

import os

# Deployed environments in which security-sensitive bypasses (for example
# no-auth operation) are never permitted. Common short aliases are included
# so a "prod"/"stage" spelling is protected rather than silently treated as
# local development — widening protection is fail-closed; narrowing is not.
PROTECTED_ENVIRONMENTS: frozenset[str] = frozenset({"production", "prod", "staging", "stage"})


def deployment_environment() -> str:
    """Return the canonical deployment environment name.

    Reads ``ENV`` first, then ``ENVIRONMENT``; strips and lowercases.
    Returns ``""`` when neither is set (local/developer operation).
    """
    return os.getenv("ENV", os.getenv("ENVIRONMENT", "")).strip().lower()


def is_protected_environment(environment: str | None = None) -> bool:
    """True when the (given or current) environment is a protected deployment.

    Protected environments reject authentication-disabled operation at every
    mandatory boundary, independent of startup-check mode. Unknown or unset
    environment names follow the existing local-development contract and are
    not protected.
    """
    env = deployment_environment() if environment is None else environment.strip().lower()
    return env in PROTECTED_ENVIRONMENTS


__all__ = [
    "PROTECTED_ENVIRONMENTS",
    "deployment_environment",
    "is_protected_environment",
]
