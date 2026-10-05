"""Who an unauthenticated host serves, decided per request.

"Authentication is off" used to mean two things at once: anonymous requests
are allowed, and anonymous requests get development privileges (anonymous
roles, dev personas, trusted module dispatch, caller-named users). A process
cannot know whether it is reachable from the network: the ASGI lifespan never
sees the bind address, and containers and proxies make that address wrong in
both directions. A request does carry its peer, so the privilege is granted
per request and attached to the principal when it is minted.

The operator chooses with ``AUTH_ANON_ACCESS`` (resolved by
:func:`mozaiksai.core.auth.adapters.registry.resolve_auth_config`):

- ``local`` (default): development access, but only for requests from this
  machine (:func:`is_local_client`): a loopback peer, no forwarding header, a
  ``Host`` and ``Origin`` that name this machine, and no cross-site fetch
  without an ``Origin``. Every other request is refused.
- ``public``: every client is an anonymous visitor without development access.
- ``open``: development access for every client that can reach the host.

Implicit demo mode (no auth configuration at all) serves nobody: hosts refuse
to start in it, and a request that reaches one anyway is refused here.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any, Literal
from urllib.parse import urlsplit

from logs.logging_config import get_core_logger
from mozaiksai.core.auth.adapters.base import AuthAdapter, UserClaims
from mozaiksai.core.auth.adapters.no_auth import csv_setting_values
from mozaiksai.core.auth.adapters.registry import ResolvedAuthConfig, resolve_auth_config

logger = get_core_logger("auth.anonymous_access")

#: Provenance of an anonymous principal granted development access.
LOCAL_DEVELOPMENT_PROVENANCE = "local_development"
#: Provenance of a request-scoped dev persona (only reachable from development access).
DEV_OVERRIDE_PROVENANCE = "dev_override"
#: Provenance of an anonymous visitor without development access.
ANONYMOUS_PROVENANCE = "anonymous"
DEVELOPMENT_ACCESS_PROVENANCES: frozenset[str] = frozenset(
    {LOCAL_DEVELOPMENT_PROVENANCE, DEV_OVERRIDE_PROVENANCE}
)

#: Scope of an anonymous visitor when AUTH_ANON_SCOPES is not set.
VISITOR_DEFAULT_SCOPES: tuple[str, ...] = ("access_as_user",)

# Present on a request that came through a proxy, whatever their value.
_FORWARDING_HEADERS = (b"x-forwarded-for", b"x-real-ip", b"forwarded")
_PORT = re.compile(r"[0-9]{1,5}")

_NOT_CONFIGURED_CHOICES = (
    "Authentication is not configured. Choose one: configure an identity provider "
    "(AUTH_PROVIDER, SUPABASE_URL, KEYCLOAK_URL + KEYCLOAK_REALM, AUTH_JWKS_URL + "
    "AUTH_ISSUER, or MOZAIKS_OIDC_AUTHORITY); or set AUTH_ENABLED=false to run without "
    "authentication for this machine only (in a container, whose clients never arrive "
    "as this machine, set AUTH_ANON_ACCESS=open instead and publish its port on "
    "127.0.0.1 only)"
)
NOT_CONFIGURED_MESSAGE = (
    f"{_NOT_CONFIGURED_CHOICES}; or set AUTH_ANON_ACCESS=public to serve anonymous "
    "visitors without development access."
)
#: The same choices for a management host (Studio), which cannot serve visitors.
MANAGEMENT_NOT_CONFIGURED_MESSAGE = f"{_NOT_CONFIGURED_CHOICES}."
STUDIO_PUBLIC_MESSAGE = (
    "Studio cannot run with AUTH_ANON_ACCESS=public. Configure authentication, or use "
    "AUTH_ANON_ACCESS=local (this machine only) or AUTH_ANON_ACCESS=open (every client "
    "that can connect, for example a container published on 127.0.0.1 only)."
)
OPEN_ACCESS_WARNING = (
    "Authentication is off and AUTH_ANON_ACCESS=open: every client that can reach this "
    "server gets development access: it can act as any user with any role, including "
    "admin, credit wallets through billing fulfillment and, on Studio, create apps and "
    "start builds and previews that run code. Use it only where the network itself "
    "limits who can connect."
)

#: Which locality rule refused a request (see :func:`local_client_refusal`).
LocalityRule = Literal["peer", "forwarded", "host", "origin", "fetch-site"]

_LOCAL_ONLY_INTRO = (
    "Authentication is off and AUTH_ANON_ACCESS is local, so this server gives "
    "development access only to requests that this machine sends to it directly."
)
_OPEN_IT_LOCALLY = "Open the app at http://localhost:<port> or http://127.0.0.1:<port>."
_LOCAL_ONLY_REASONS: dict[LocalityRule, str] = {
    "peer": (
        "This request came from {observed}, another machine. "
        f"{_OPEN_IT_LOCALLY} A browser on this machine reaches a container through "
        "Docker's gateway, so a container whose port is published on 127.0.0.1 only "
        "needs AUTH_ANON_ACCESS=open. To serve other machines, configure "
        "authentication, or set AUTH_ANON_ACCESS=public (anonymous visitors without "
        "development access) or AUTH_ANON_ACCESS=open (development access for every client)."
    ),
    "forwarded": (
        "This request came through a proxy (it carries a {observed} header), so it may "
        "come from another machine. Connect to this server directly: "
        f"{_OPEN_IT_LOCALLY} Behind a reverse proxy, configure authentication, or set "
        "AUTH_ANON_ACCESS=public or AUTH_ANON_ACCESS=open."
    ),
    "host": (
        "This request was addressed to {observed}, which does not name this machine. "
        f"{_OPEN_IT_LOCALLY}"
    ),
    "origin": (
        "This request was sent by a page from {observed}, which this machine does not "
        f"serve. {_OPEN_IT_LOCALLY}"
    ),
    "fetch-site": (
        "This request was sent by a page on another site (Sec-Fetch-Site: cross-site), "
        f"which this machine does not serve. {_OPEN_IT_LOCALLY}"
    ),
}

# WebSocket close reasons travel in a close frame (at most 123 bytes).
_WS_REASON_NOT_CONFIGURED = "Authentication is not configured"
_WS_REASONS_LOCAL_ONLY: dict[LocalityRule, str] = {
    "peer": "Authentication is off; development access is for this machine only (peer is another machine)",
    "forwarded": "Authentication is off; development access is for this machine only (request came through a proxy)",
    "host": "Authentication is off; development access is for this machine only (Host is not this machine)",
    "origin": "Authentication is off; development access is for this machine only (Origin is not this machine)",
    "fetch-site": "Authentication is off; development access is for this machine only (sent by another site)",
}

GrantKind = Literal["local_development", "public_visitor", "refused"]


@dataclass(frozen=True)
class LocalityRefusal:
    """Why a request is not demonstrably from this machine."""

    rule: LocalityRule
    observed: str


@dataclass(frozen=True)
class AnonymousGrant:
    """What an unauthenticated request receives."""

    kind: GrantKind
    status_code: int | None = None
    detail: str | None = None
    ws_reason: str | None = None

    @property
    def refused(self) -> bool:
        return self.kind == "refused"

    @property
    def development_access(self) -> bool:
        return self.kind == "local_development"


_LOCAL_DEVELOPMENT = AnonymousGrant(kind="local_development")
_PUBLIC_VISITOR = AnonymousGrant(kind="public_visitor")


def _is_loopback_ip(value: str) -> bool:
    """True only for a loopback IP address, IPv4-mapped IPv6 included."""
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    # Checked explicitly: Python 3.11 reports ::ffff:127.0.0.1 as not loopback.
    return address.is_loopback or (
        isinstance(address, ipaddress.IPv6Address)
        and address.ipv4_mapped is not None
        and address.ipv4_mapped.is_loopback
    )


def _is_loopback_peer(value: object) -> bool:
    """True for an ASGI client host that is a loopback IP (brackets and zone id ignored)."""
    if not isinstance(value, str):
        return False
    host = value.strip()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    return _is_loopback_ip(host.split("%", 1)[0])


def _is_loopback_authority(value: str) -> bool:
    """True when ``host[:port]`` names this machine.

    The host is a loopback IP literal (IPv6 in brackets), ``localhost``, or a
    name under ``.localhost``, case-insensitive with one trailing dot allowed.
    """
    authority = value.strip()
    if authority.startswith("["):
        address, bracket, rest = authority[1:].partition("]")
        if not bracket or (rest and not (rest[:1] == ":" and _PORT.fullmatch(rest[1:]))):
            return False
        return _is_loopback_ip(address)
    name, colon, port = authority.partition(":")
    if colon and not _PORT.fullmatch(port):
        return False
    name = name.lower()
    if name.endswith("."):
        name = name[:-1]
    return name == "localhost" or name.endswith(".localhost") or _is_loopback_ip(name)


def _is_loopback_origin(value: str) -> bool:
    """True for an ``http``/``https`` origin whose host names this machine."""
    try:
        origin = urlsplit(value.strip())
    except ValueError:
        return False
    return (
        origin.scheme in ("http", "https")
        and "@" not in origin.netloc
        and origin.path in ("", "/")
        and not origin.query
        and not origin.fragment
        and _is_loopback_authority(origin.netloc)
    )


def _headers(scope: Mapping[str, Any], names: Iterable[bytes]) -> list[tuple[str, str]]:
    wanted = set(names)
    found: list[tuple[str, str]] = []
    for item in scope.get("headers") or ():
        try:
            name, value = item
        except (TypeError, ValueError):
            continue
        if isinstance(name, bytes) and name.lower() in wanted and isinstance(value, bytes):
            found.append((name.decode("latin-1").lower(), value.decode("latin-1")))
    return found


def shown_client_value(value: str) -> str:
    """A client-supplied value for messages and logs.

    The value is cut to 80 characters, then repr-quoted, so control characters
    are escaped and a log record stays on one line.
    """
    return repr(value if len(value) <= 80 else value[:77] + "...")


def client_host(scope: Mapping[str, Any]) -> str:
    """The peer as the server recorded it, quoted and shortened, for messages and logs.

    Behind a proxy the server trusts, the server records the peer from the
    request's own forwarding header, so the peer is client-supplied text.
    """
    client = scope.get("client") if isinstance(scope, Mapping) else None
    if isinstance(client, (tuple, list)) and client:
        return shown_client_value(str(client[0]))
    return "an unknown address"


def local_client_refusal(scope: Mapping[str, Any]) -> LocalityRefusal | None:
    """Why a request is not demonstrably from this machine; ``None`` when it is.

    A request is from this machine only when all five of these hold, checked
    in this order; the first that fails is reported:

    1. Peer: the ASGI ``client`` is a ``(host, port)`` pair whose host is a
       loopback IP address (IPv4-mapped IPv6 included). ``None``, Unix sockets
       and non-IP strings such as Starlette TestClient's ``"testclient"`` are not.
    2. Forwarding: it carries no ``X-Forwarded-For``, ``X-Real-IP`` or
       ``Forwarded`` header, whatever the value. Such a request came through a
       proxy (or claims to), and the server may already have rewritten its
       peer from that header under a proxy-trust setting this function cannot
       see. Without one, the server never rewrites, so the peer is the TCP peer.
    3. Host: a ``Host`` header names this machine (a loopback IP literal,
       ``localhost`` or a ``.localhost`` name), so a page whose own name
       resolves to 127.0.0.1 (DNS rebinding) is not served.
    4. Origin: an ``Origin`` header is an ``http``/``https`` origin on such a
       host, so a page from any other site, including ``Origin: null``, cannot
       use the browser on this machine to reach it (cross-site requests and
       WebSocket hijacking).
    5. Fetch site: a request without an ``Origin`` header does not carry
       ``Sec-Fetch-Site: cross-site``. ``same-origin``, ``same-site``,
       ``none`` and an absent header pass; a cross-site request that carries
       an ``Origin`` is judged by rule 4 alone.
    """
    if not isinstance(scope, Mapping):
        return LocalityRefusal("peer", client_host(scope))
    client = scope.get("client")
    if (
        not isinstance(client, (tuple, list))
        or len(client) != 2
        or not _is_loopback_peer(client[0])
    ):
        return LocalityRefusal("peer", client_host(scope))
    forwarded = _headers(scope, _FORWARDING_HEADERS)
    if forwarded:
        return LocalityRefusal("forwarded", forwarded[0][0])
    for _, host in _headers(scope, (b"host",)):
        if not _is_loopback_authority(host):
            return LocalityRefusal("host", shown_client_value(host))
    origins = _headers(scope, (b"origin",))
    for _, origin in origins:
        if not _is_loopback_origin(origin):
            return LocalityRefusal("origin", shown_client_value(origin))
    if not origins:
        # Browsers send no Origin on no-cors cross-site GETs (image and script
        # loads, navigations), so rule 4 never sees them, and GET module
        # dispatch and GET /api/me change state. Fetch metadata still says
        # where such a request came from.
        for _, site in _headers(scope, (b"sec-fetch-site",)):
            if site.strip().lower() == "cross-site":
                return LocalityRefusal("fetch-site", shown_client_value(site))
    return None


def is_local_client(scope: Mapping[str, Any]) -> bool:
    """True only when a request demonstrably comes from this machine (see :func:`local_client_refusal`)."""
    return local_client_refusal(scope) is None


def local_only_message(refusal: LocalityRefusal) -> str:
    """The HTTP refusal of a non-local request under ``AUTH_ANON_ACCESS=local``."""
    return f"{_LOCAL_ONLY_INTRO} {_LOCAL_ONLY_REASONS[refusal.rule].format(observed=refusal.observed)}"


def resolve_anonymous_grant(
    scope: Mapping[str, Any],
    config: ResolvedAuthConfig | None = None,
    *,
    log_refusal: bool = True,
) -> AnonymousGrant:
    """Decide what an unauthenticated request receives.

    Call only when the resolved configuration has no provider (``enabled`` is
    false); token-validating hosts never reach this.
    """
    config = config if config is not None else resolve_auth_config()
    if config.enabled:
        raise ValueError("resolve_anonymous_grant is only meaningful while authentication is off")
    if not config.explicitly_disabled:
        if log_refusal:
            logger.warning("ANONYMOUS_ACCESS_REFUSED reason=not_configured client=%s", client_host(scope))
        return AnonymousGrant(
            kind="refused",
            status_code=401,
            detail=NOT_CONFIGURED_MESSAGE,
            ws_reason=_WS_REASON_NOT_CONFIGURED,
        )
    if config.anonymous_access == "public":
        return _PUBLIC_VISITOR
    if config.anonymous_access == "open":
        return _LOCAL_DEVELOPMENT
    refusal = local_client_refusal(scope)
    if refusal is None:
        return _LOCAL_DEVELOPMENT
    if log_refusal:
        logger.warning(
            "ANONYMOUS_ACCESS_REFUSED reason=not_local rule=%s client=%s observed=%s",
            refusal.rule,
            client_host(scope),
            refusal.observed,
        )
    return AnonymousGrant(
        kind="refused",
        status_code=403,
        detail=local_only_message(refusal),
        ws_reason=_WS_REASONS_LOCAL_ONLY[refusal.rule],
    )


def _fallback_claims() -> UserClaims:
    return UserClaims(
        user_id="anonymous",
        name="Anonymous User",
        roles=[],
        scopes=list(VISITOR_DEFAULT_SCOPES),
        provider="none",
    )


def visitor_claims(claims: UserClaims, config: ResolvedAuthConfig) -> UserClaims:
    """Strip development privileges from anonymous claims.

    A visitor keeps the configured anonymous user id, and has no roles, no
    email (so no admin-allowlist promotion) and only ``AUTH_ANON_SCOPES``, or
    ``access_as_user`` when that is unset.
    """
    scopes = csv_setting_values(config.settings.get("AUTH_ANON_SCOPES")) or list(VISITOR_DEFAULT_SCOPES)
    return replace(claims, email=None, roles=[], scopes=scopes, raw_claims={})


async def anonymous_claims(
    grant: AnonymousGrant, config: ResolvedAuthConfig, adapter: AuthAdapter
) -> UserClaims:
    """Claims of the anonymous principal a non-refused grant mints from ``adapter``."""
    if grant.refused:
        raise ValueError("a refused request has no anonymous principal")
    try:
        claims = await adapter.validate_token("")
    except Exception as exc:  # noqa: BLE001 - a custom no-auth adapter may refuse empty tokens
        logger.debug("AUTH_ADAPTER_EMPTY_TOKEN_FAILED adapter=%s: %s", adapter.name, exc)
        claims = _fallback_claims()
    if grant.development_access:
        return claims
    return visitor_claims(claims, config)


__all__ = [
    "ANONYMOUS_PROVENANCE",
    "DEVELOPMENT_ACCESS_PROVENANCES",
    "DEV_OVERRIDE_PROVENANCE",
    "LOCAL_DEVELOPMENT_PROVENANCE",
    "MANAGEMENT_NOT_CONFIGURED_MESSAGE",
    "NOT_CONFIGURED_MESSAGE",
    "OPEN_ACCESS_WARNING",
    "STUDIO_PUBLIC_MESSAGE",
    "VISITOR_DEFAULT_SCOPES",
    "AnonymousGrant",
    "LocalityRefusal",
    "anonymous_claims",
    "client_host",
    "is_local_client",
    "local_client_refusal",
    "local_only_message",
    "resolve_anonymous_grant",
    "shown_client_value",
    "visitor_claims",
]
