"""Check, before a host starts, whom it will serve without authentication.

With authentication off the runtime decides per request who is served
(``AUTH_ANON_ACCESS``, see ``mozaiksai.core.auth.anonymous_access``):

- ``local`` (the default, and what a fresh scaffold uses): development access,
  for requests from this machine only. The anonymous user has the roles in
  ``AUTH_ANON_ROLES`` (a fresh scaffold grants ``admin``) and a request may
  claim any user and any roles, so that one browser can try several users.
- ``public``: every client is an anonymous visitor without development access.
- ``open``: development access for every client that can reach the host.

The runtime enforces this on every request and refuses to start with no auth
configuration at all, whatever started it. Commands that start a host check
the same resolution of the environment the host will receive first, so the
operator gets the answer before anything starts: no auth configuration and
``local`` on an address other machines can reach are refused, ``open`` on such
an address is a warning, and ``public`` is announced.
"""

from __future__ import annotations

import ipaddress
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

_RULE = "=" * 78
_PROVIDER_CHOICES = (
    "AUTH_PROVIDER, or SUPABASE_URL, KEYCLOAK_URL + KEYCLOAK_REALM, AUTH_JWKS_URL + "
    "AUTH_ISSUER, or MOZAIKS_OIDC_AUTHORITY"
)


def is_loopback(bind_host: str) -> bool:
    """True when only this machine can reach a server bound to ``bind_host``."""
    host = bind_host.strip().strip("[]")
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class UnauthenticatedStart:
    """What a command must do before it starts a host."""

    refusal: str | None = None
    notice: str | None = None


def _boxed(lines: list[str]) -> str:
    return "\n".join([_RULE, lines[0], *(f"  {line}" for line in lines[1:]), _RULE])


def _ignored_provider_settings(config) -> list[str]:
    from mozaiksai.core.auth.adapters.registry import unused_provider_settings

    switches = [
        f"{name}={config.settings[name].strip()}"
        for name in ("AUTH_ENABLED", "AUTH_PROVIDER")
        if config.settings.get(name, "").strip()
    ]
    unused = ", ".join(unused_provider_settings(config))
    if unused and switches:
        verb = "overrides" if len(switches) == 1 else "override"
        return [
            f"{' and '.join(switches)} {verb} the provider settings present in the "
            f"environment ({unused}): they are ignored."
        ]
    if unused:
        return [
            f"The provider settings present in the environment ({unused}) do not select a "
            "provider on their own: they are ignored."
        ]
    return []


def _switches(config) -> str:
    """The settings that turned authentication off, as the operator wrote them."""
    named = [
        f"{name}={config.settings[name].strip()}"
        for name in ("AUTH_ENABLED", "AUTH_PROVIDER")
        if config.settings.get(name, "").strip()
    ]
    return " and ".join(named) or f"AUTH_ANON_ACCESS={config.settings['AUTH_ANON_ACCESS'].strip()}"


def assess_unauthenticated_start(
    bind_host: str, *, environ: Mapping[str, str], env_file: Path, host: str
) -> UnauthenticatedStart:
    """Decide whether a ``host`` started on ``bind_host`` with ``environ`` may start.

    Nothing to say when authentication is on, or when the runtime rejects the
    configuration: the host then refuses to start and reports that itself.
    """
    from mozaiksai.core.auth.adapters.base import AuthError
    from mozaiksai.core.auth.adapters.no_auth import NoAuthAdapter
    from mozaiksai.core.auth.adapters.registry import resolve_auth_config
    from mozaiksai.core.auth.anonymous_access import (
        MANAGEMENT_NOT_CONFIGURED_MESSAGE,
        NOT_CONFIGURED_MESSAGE,
        STUDIO_PUBLIC_MESSAGE,
    )

    try:
        config = resolve_auth_config(environ=environ)
    except AuthError:
        return UnauthenticatedStart()
    if config.enabled:
        return UnauthenticatedStart()

    serves_visitors = host != "studio"
    if not config.explicitly_disabled:
        return UnauthenticatedStart(
            refusal=_boxed(
                [
                    "Error: the server was not started.",
                    NOT_CONFIGURED_MESSAGE if serves_visitors else MANAGEMENT_NOT_CONFIGURED_MESSAGE,
                    *_ignored_provider_settings(config),
                    f"Set these in the environment or in {env_file}.",
                ]
            )
        )

    access = config.anonymous_access
    if access == "public":
        if not serves_visitors:
            return UnauthenticatedStart(
                refusal=_boxed(
                    [
                        "Error: the server was not started.",
                        STUDIO_PUBLIC_MESSAGE,
                        *_ignored_provider_settings(config),
                    ]
                )
            )
        return UnauthenticatedStart(
            notice="\n".join(
                [
                    "Authentication is off with AUTH_ANON_ACCESS=public: every client is served "
                    "as an anonymous visitor without development access.",
                    *_ignored_provider_settings(config),
                ]
            )
        )

    if is_loopback(bind_host):
        return UnauthenticatedStart()

    if access == "local":
        off = _switches(config)
        why = f"Authentication is off ({off})"
        if not off.startswith("AUTH_ANON_ACCESS="):
            why += " and AUTH_ANON_ACCESS is local"
        return UnauthenticatedStart(
            refusal=_boxed(
                [
                    f"Error: the server was not started on {bind_host}.",
                    f"{why}, so the server answers only requests from this machine, but "
                    f"--listen {bind_host} accepts connections from other machines.",
                    *_ignored_provider_settings(config),
                    "Choose one:",
                    "- listen on this machine only: --listen 127.0.0.1;",
                    f"- turn authentication on: set AUTH_ENABLED=true and configure an identity "
                    f"provider ({_PROVIDER_CHOICES}) in the environment or in {env_file};",
                    *(
                        ["- serve anonymous visitors without development access: AUTH_ANON_ACCESS=public;"]
                        if serves_visitors
                        else []
                    ),
                    "- give every client that can connect development access: AUTH_ANON_ACCESS=open "
                    "(for example a container whose port is published only on 127.0.0.1).",
                ]
            )
        )

    roles = NoAuthAdapter(settings=config.settings).validate_token_sync("").roles
    anonymous = (
        "Requests that claim no identity are the anonymous user, with the roles in "
        f"AUTH_ANON_ROLES: {', '.join(roles)}."
        if roles
        else "Requests that claim no identity are the anonymous user. AUTH_ANON_ROLES is "
        "empty, which does not limit access while development access is granted."
    )
    return UnauthenticatedStart(
        notice=_boxed(
            [
                f"WARNING: authentication is off with AUTH_ANON_ACCESS=open, and the server is "
                f"about to listen on {bind_host}, which other machines can reach.",
                "Every client that can reach it gets development access: it can act as any user "
                "with any role, including admin, credit wallets through billing fulfillment and, "
                "on Studio, create apps and start builds and previews that run code.",
                anonymous,
                *_ignored_provider_settings(config),
                "To turn authentication on, set AUTH_ENABLED=true and configure an identity provider "
                f"({_PROVIDER_CHOICES}) in the environment or in {env_file}.",
                *(
                    ["To serve other machines without development access, set AUTH_ANON_ACCESS=public."]
                    if serves_visitors
                    else []
                ),
            ]
        )
    )


def check_unauthenticated_start(
    bind_host: str, *, environ: Mapping[str, str], env_file: Path, host: str
) -> str | None:
    """Print the notice, if any, to stderr; return the refusal text, if any."""
    start = assess_unauthenticated_start(bind_host, environ=environ, env_file=env_file, host=host)
    if start.notice is not None:
        print(start.notice, file=sys.stderr, flush=True)
    return start.refusal
