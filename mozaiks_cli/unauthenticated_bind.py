"""Warn before a host without authentication listens beyond this machine.

Local development runs with authentication off. A request that claims no
identity is the anonymous user, with the roles in ``AUTH_ANON_ROLES`` (a fresh
scaffold grants ``admin``), and a request may claim any user and any roles for
itself, so that one browser can try several users. On a loopback address only
this machine can do either. On any other address everyone who can reach the
server can act as any user with any role, so commands that start a host say so
first.

The auth mode comes from the runtime's own resolution of the environment the
host will receive, so the warning describes what the host does. This is a
warning only; the command still starts the host.
"""

from __future__ import annotations

import ipaddress
import sys
from collections.abc import Mapping
from pathlib import Path

_RULE = "=" * 78


def is_loopback(bind_host: str) -> bool:
    """True when only this machine can reach a server bound to ``bind_host``."""
    host = bind_host.strip().strip("[]")
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def unauthenticated_bind_warning(
    bind_host: str, *, environ: Mapping[str, str], env_file: Path
) -> str | None:
    """Return the warning for a host started on ``bind_host`` with ``environ``.

    ``None`` when the address is loopback, when authentication is on, or when
    the runtime rejects the configuration: the host then refuses to start and
    reports that itself.
    """
    if is_loopback(bind_host):
        return None

    from mozaiksai.core.auth.adapters.base import AuthError
    from mozaiksai.core.auth.adapters.no_auth import NoAuthAdapter
    from mozaiksai.core.auth.adapters.registry import (
        resolve_auth_config,
        unused_provider_settings,
    )

    try:
        config = resolve_auth_config(environ=environ)
    except AuthError:
        return None
    if config.enabled:
        return None

    roles = NoAuthAdapter(settings=config.settings).validate_token_sync("").roles
    # The roles only describe requests that claim nothing; they limit nothing.
    anonymous = (
        "Requests that claim no identity are the anonymous user, with the roles in "
        f"AUTH_ANON_ROLES: {', '.join(roles)}."
        if roles
        else "Requests that claim no identity are the anonymous user. AUTH_ANON_ROLES is "
        "empty, which does not limit access while authentication is off."
    )
    switches = [
        f"{name}={config.settings[name].strip()}"
        for name in ("AUTH_ENABLED", "AUTH_PROVIDER")
        if config.settings.get(name, "").strip()
    ]
    lines = [
        f"WARNING: authentication is off and the server is about to listen on {bind_host}, "
        "which other machines can reach.",
        "With authentication off, anyone who can reach it can act as any user with any role, "
        "including admin.",
        anonymous,
    ]
    unused = ", ".join(unused_provider_settings(config))
    if unused and switches:
        verb = "overrides" if len(switches) == 1 else "override"
        lines.append(
            f"{' and '.join(switches)} {verb} the provider settings present in the "
            f"environment ({unused}): they are ignored."
        )
    elif unused:
        lines.append(
            f"The provider settings present in the environment ({unused}) do not select a "
            "provider on their own: they are ignored."
        )
    lines.append(
        "To turn authentication on, set AUTH_ENABLED=true and configure an identity provider "
        "(AUTH_PROVIDER, or SUPABASE_URL, KEYCLOAK_URL + KEYCLOAK_REALM, AUTH_JWKS_URL + "
        f"AUTH_ISSUER, or MOZAIKS_OIDC_AUTHORITY) in the environment or in {env_file}."
    )
    lines.append("To keep it off, listen on this machine only: --listen 127.0.0.1.")
    return "\n".join([_RULE, lines[0], *(f"  {line}" for line in lines[1:]), _RULE])


def warn_if_unauthenticated_bind(
    bind_host: str, *, environ: Mapping[str, str], env_file: Path
) -> None:
    """Print the warning to stderr when one applies."""
    warning = unauthenticated_bind_warning(bind_host, environ=environ, env_file=env_file)
    if warning is not None:
        print(warning, file=sys.stderr, flush=True)
