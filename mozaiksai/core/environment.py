"""Canonical deployment-environment vocabulary for the Mozaiks runtime.

One place interprets the deployment environment. Callers must not re-read or
re-parse ``ENV``/``ENVIRONMENT`` for policy decisions — they consume
:func:`resolve_environment` so every boundary (auth resolution, startup
validation, dispatch authority) agrees on what environment the process runs in.

Security decision direction — **allowlist, not denylist**:

No-auth (unauthenticated) operation is permitted ONLY in a finite set of
explicitly recognized local/development/test environments, plus the documented
completely-absent local default. Every other explicit, non-empty environment
value — including regional or custom deployment names such as ``prod-us``,
``staging-eu``, ``preview``, or ``qa`` — is treated as a deployment that must
never inherit development privilege. Unknown environments may still boot with
correctly configured authentication; they simply cannot run unauthenticated.

Canonical repository vocabulary (census of `.env.example`, host defaults, CI
workflows, deployment templates, docs, and tests):

- ``development`` — `.env.example` (both variables), runtime/observability host
  defaults
- ``local`` — Studio workspace summary default
- ``test`` — CI (`.github/workflows/ci.yml`) and release workflow, test helpers
- ``staging`` — deployment provisioning template default
- ``production`` — generated Dockerfile template, production guards

``dev``/``prod``/``stage`` are accepted only as unambiguous aliases of
``development``/``production``/``staging`` and normalize to them before any
comparison.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

ENVIRONMENT_ENV_VARS: tuple[str, str] = ("ENV", "ENVIRONMENT")

EnvironmentClassification = Literal[
    "absent",
    "local_development",
    "protected_deployment",
    "unknown_deployment",
]

# Unambiguous spelling aliases normalized before classification.
_ENVIRONMENT_ALIASES: dict[str, str] = {
    "dev": "development",
    "prod": "production",
    "stage": "staging",
}

# The finite allowlist. Only these recognized local/development/test
# environments may run without authentication.
LOCAL_ENVIRONMENTS: frozenset[str] = frozenset({"development", "local", "test"})

# Known deployed environments. Listed for clear diagnostics only — anything
# outside LOCAL_ENVIRONMENTS is already denied development privilege.
PROTECTED_ENVIRONMENTS: frozenset[str] = frozenset({"production", "staging"})


class EnvironmentConfigError(ValueError):
    """Raised when ENV and ENVIRONMENT declare conflicting environments."""


def _normalize(raw: str | None) -> str:
    """Trim, lowercase, and alias-normalize one environment value.

    Whitespace-only and empty values are absence, not an environment.
    """
    value = (raw or "").strip().lower()
    if not value:
        return ""
    return _ENVIRONMENT_ALIASES.get(value, value)


def _classify(name: str) -> EnvironmentClassification:
    if not name:
        return "absent"
    if name in LOCAL_ENVIRONMENTS:
        return "local_development"
    if name in PROTECTED_ENVIRONMENTS:
        return "protected_deployment"
    return "unknown_deployment"


@dataclass(frozen=True)
class ResolvedEnvironment:
    """Immutable canonical interpretation of the deployment environment."""

    name: str
    classification: EnvironmentClassification
    raw_env: str
    raw_environment: str

    @property
    def permits_no_auth(self) -> bool:
        """True only for recognized local/development/test environments and
        the documented completely-absent local default.

        Unknown, custom, regional, and known-deployed environment names all
        return False — no-auth privilege is never inherited by default.
        """
        return self.classification in {"absent", "local_development"}

    @property
    def is_deployed(self) -> bool:
        """True for any environment that is not local/absent."""
        return self.classification in {"protected_deployment", "unknown_deployment"}

    @property
    def display_name(self) -> str:
        return self.name or "unset"


def resolve_environment() -> ResolvedEnvironment:
    """Resolve ``ENV``/``ENVIRONMENT`` into one canonical environment.

    Resolution rules:

    1. Both values are trimmed first; whitespace-only is absence.
    2. A blank ``ENV`` never masks a non-blank ``ENVIRONMENT``.
    3. Recognized aliases normalize before comparison.
    4. If both are non-blank and semantically conflict, that is a
       configuration error — the runtime refuses to silently pick one. Two
       declarations conflict when they classify differently (for example a
       local ``ENV`` beside a deployed ``ENVIRONMENT``), or when they name two
       different deployed environments (``production`` beside ``staging``).
    5. Two different names inside the recognized local class (for example
       ``ENV=test`` with ``ENVIRONMENT=development``, as CI and a developer
       ``.env`` commonly produce together) carry the same security meaning and
       are accepted, with ``ENV`` taking precedence for the resolved name.
    6. Completely absent keeps the documented implicit local default.
    """
    raw_env = os.getenv("ENV") or ""
    raw_environment = os.getenv("ENVIRONMENT") or ""
    primary = _normalize(raw_env)
    fallback = _normalize(raw_environment)

    if primary and fallback and primary != fallback:
        primary_class = _classify(primary)
        fallback_class = _classify(fallback)
        # Same-class local declarations agree on the only thing that matters
        # here (whether unauthenticated operation is permitted). Anything else
        # — a class mismatch, or two distinct deployed environments — is a
        # genuine conflict that must never be silently resolved.
        if not (
            primary_class == "local_development" and fallback_class == "local_development"
        ):
            raise EnvironmentConfigError(
                f"Conflicting deployment environment declarations: ENV={raw_env.strip()!r} "
                f"resolves to {primary!r} ({primary_class}) but "
                f"ENVIRONMENT={raw_environment.strip()!r} resolves to {fallback!r} "
                f"({fallback_class}). Set both to the same environment (or set only "
                "one). Refusing to silently choose one of two conflicting values."
            )

    name = primary or fallback
    return ResolvedEnvironment(
        name=name,
        classification=_classify(name),
        raw_env=raw_env,
        raw_environment=raw_environment,
    )


def deployment_environment() -> str:
    """Return the canonical deployment environment name (``""`` when absent)."""
    return resolve_environment().name


def environment_permits_no_auth() -> bool:
    """True only when the current environment may run unauthenticated."""
    return resolve_environment().permits_no_auth


__all__ = [
    "ENVIRONMENT_ENV_VARS",
    "LOCAL_ENVIRONMENTS",
    "PROTECTED_ENVIRONMENTS",
    "EnvironmentClassification",
    "EnvironmentConfigError",
    "ResolvedEnvironment",
    "deployment_environment",
    "environment_permits_no_auth",
    "resolve_environment",
]
