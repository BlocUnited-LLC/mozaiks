"""Host-only Keycloak client provisioning for owned, local Docker previews."""

from __future__ import annotations

import asyncio
import json
import os
import re
from ipaddress import ip_address, ip_network
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import httpx

from mozaiksai.core.auth.adapters.base import AuthError
from mozaiksai.core.auth.adapters.keycloak import (
    ACCESS_TOKEN_TYPE_CLAIM,
    ACCESS_TOKEN_TYPE_VALUE,
    keycloak_issuer,
)
from mozaiksai.core.auth.adapters.registry import resolve_auth_config
from mozaiksai.core.environment import resolve_environment

_CLIENT_ENV = "MOZAIKS_KEYCLOAK_PREVIEW_REGISTRAR_CLIENT_ID"
_SECRET_ENV = "MOZAIKS_KEYCLOAK_PREVIEW_REGISTRAR_CLIENT_SECRET"
_DEFAULT_SCOPES = ["basic", "profile", "email"]
_MAX_RESPONSE_BYTES = 131072
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
_PRIVATE_IPV4_NETWORKS = tuple(ip_network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


class KeycloakPreviewRegistrationError(RuntimeError):
    """Registration or cleanup failed; no provider response or secret is exposed."""


def _safe_url(value: str, *, allow_docker_host: bool = False) -> str:
    try:
        parts = urlsplit(value)
        _ = parts.port
    except ValueError:
        raise KeycloakPreviewRegistrationError("Invalid Keycloak endpoint") from None
    local_hosts = _LOCAL_HOSTS | ({"host.docker.internal"} if allow_docker_host else set())
    local_endpoint = parts.hostname in local_hosts
    if allow_docker_host and not local_endpoint:
        try:
            address = ip_address(parts.hostname or "")
            local_endpoint = any(address in network for network in _PRIVATE_IPV4_NETWORKS)
        except ValueError:
            pass
    if (
        not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or any(char.isspace() or ord(char) < 32 for char in value)
        or any(char in value for char in "\\%*?#")
        or any(part in {".", ".."} for part in parts.path.split("/"))
        or not (
            parts.scheme == "https"
            or (
                parts.scheme == "http"
                and local_endpoint
                and not resolve_environment().is_deployed
            )
        )
    ):
        raise KeycloakPreviewRegistrationError("Keycloak requires HTTPS or local development HTTP")
    return value.rstrip("/")


def _sandbox_uuid(sandbox_id: str) -> UUID:
    try:
        identifier = UUID(sandbox_id)
    except (ValueError, AttributeError):
        raise KeycloakPreviewRegistrationError("Preview requires its canonical sandbox identity") from None
    if identifier.version != 4 or identifier.hex != sandbox_id:
        raise KeycloakPreviewRegistrationError("Preview requires its canonical sandbox identity")
    return identifier


def _callback(preview_url: str, callback_path: str) -> tuple[str, str]:
    origin = _safe_url(preview_url)
    parts = urlsplit(preview_url)
    if parts.hostname not in _LOCAL_HOSTS or not parts.port or parts.path not in {"", "/"}:
        raise KeycloakPreviewRegistrationError("Managed identity requires a Docker loopback origin")
    if (
        not re.fullmatch(r"/[A-Za-z0-9_./~-]*", callback_path)
        or callback_path.startswith("//")
        or any(part in {".", ".."} for part in callback_path.split("/"))
    ):
        raise KeycloakPreviewRegistrationError("Preview callback must be an exact app-local path")
    return origin, origin + callback_path


class KeycloakPreviewRegistrar:
    """Provision only clients owned by this registrar and a canonical sandbox ID."""

    def __init__(
        self, *, server_url: str, realm: str, client_id: str, client_secret: str,
        jwks_url: str | None = None,
    ) -> None:
        server = _safe_url(server_url)
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", realm) or realm in {".", ".."}:
            raise KeycloakPreviewRegistrationError("Invalid Keycloak realm")
        if not client_id.strip() or not client_secret.strip():
            raise KeycloakPreviewRegistrationError("Configure both Keycloak preview registrar credentials")
        self.issuer = keycloak_issuer(server, realm)
        self._admin_url = f"{server}/admin/realms/{realm}/clients"
        self._client_id = client_id
        self._client_secret = client_secret
        self._jwks_url = _safe_url(
            jwks_url or f"{self.issuer}/protocol/openid-connect/certs", allow_docker_host=True,
        )

    async def _request(
        self, client: httpx.AsyncClient, method: str, url: str,
        *, accepted: set[int], **kwargs: Any,
    ) -> tuple[int, Any]:
        try:
            async with client.stream(method, url, **kwargs) as response:
                if response.status_code not in accepted:
                    raise KeycloakPreviewRegistrationError(
                        f"Keycloak preview registration request failed (HTTP {response.status_code})"
                    )
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > _MAX_RESPONSE_BYTES:
                        raise KeycloakPreviewRegistrationError("Keycloak response exceeded its limit")
                try:
                    payload = json.loads(data) if data and response.status_code != 404 else None
                except (ValueError, UnicodeDecodeError):
                    raise KeycloakPreviewRegistrationError("Keycloak returned invalid registration data") from None
                return response.status_code, payload
        except httpx.HTTPError:
            raise KeycloakPreviewRegistrationError("Keycloak preview registration transport failed") from None

    async def _authenticate(self, client: httpx.AsyncClient) -> None:
        _, payload = await self._request(
            client, "POST", f"{self.issuer}/protocol/openid-connect/token", accepted={200},
            data={
                "grant_type": "client_credentials", "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
        )
        token = payload.get("access_token") if isinstance(payload, dict) else None
        if not isinstance(token, str) or not token or len(token) > 32768 or any(c.isspace() for c in token):
            raise KeycloakPreviewRegistrationError("Keycloak did not return a registrar access token")
        client.headers["Authorization"] = f"Bearer {token}"

    def _check_owner(self, value: Any, sandbox_id: str) -> dict[str, Any]:
        attributes = value.get("attributes") if isinstance(value, dict) else None
        if (
            not isinstance(value, dict)
            or value.get("clientId") != f"mozaiks-preview-{sandbox_id}"
            or not isinstance(value.get("id"), str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value["id"])
            or not isinstance(attributes, dict)
            or attributes.get("mozaiks.preview.sandbox_id") != sandbox_id
            or attributes.get("mozaiks.preview.registrar") != self._client_id
        ):
            raise KeycloakPreviewRegistrationError("Keycloak preview client ownership did not match")
        return value

    async def _lookup(self, client: httpx.AsyncClient, sandbox_id: str) -> dict[str, Any] | None:
        identifier = _sandbox_uuid(sandbox_id)
        status, value = await self._request(
            client, "GET", f"{self._admin_url}/{identifier}", accepted={200, 404},
        )
        if status == 200:
            return self._check_owner(value, sandbox_id)
        # Keycloak versions may assign their own internal ID on creation.
        _, matches = await self._request(
            client, "GET", self._admin_url, accepted={200},
            params={"clientId": f"mozaiks-preview-{sandbox_id}", "search": "false", "max": 2},
        )
        if not isinstance(matches, list) or len(matches) > 1:
            raise KeycloakPreviewRegistrationError("Keycloak client lookup was not exact")
        if not matches:
            return None
        match = self._check_owner(matches[0], sandbox_id)
        _, full = await self._request(
            client, "GET", f"{self._admin_url}/{match['id']}", accepted={200},
        )
        return self._check_owner(full, sandbox_id)

    def _representation(
        self, *, sandbox_id: str, target_app_id: str, origin: str, callback: str,
    ) -> dict[str, Any]:
        return {
            "id": str(_sandbox_uuid(sandbox_id)),
            "clientId": f"mozaiks-preview-{sandbox_id}",
            "name": "Mozaiks local preview", "enabled": True, "protocol": "openid-connect",
            "publicClient": True, "standardFlowEnabled": True, "implicitFlowEnabled": False,
            "directAccessGrantsEnabled": False, "serviceAccountsEnabled": False,
            "fullScopeAllowed": False, "redirectUris": [callback], "webOrigins": [origin],
            "defaultClientScopes": list(_DEFAULT_SCOPES), "optionalClientScopes": [],
            "attributes": {
                "pkce.code.challenge.method": "S256",
                "post.logout.redirect.uris": origin,
                "mozaiks.preview.sandbox_id": sandbox_id,
                "mozaiks.preview.registrar": self._client_id,
                "mozaiks.preview.target_app_id": target_app_id,
            },
            "protocolMappers": [
                {
                    "name": "Preview API audience", "protocol": "openid-connect",
                    "protocolMapper": "oidc-audience-mapper",
                    "config": {
                        "included.custom.audience": f"mozaiks-preview-api-{sandbox_id}",
                        "access.token.claim": "true", "id.token.claim": "false",
                    },
                },
                {
                    "name": "Preview app identity", "protocol": "openid-connect",
                    "protocolMapper": "oidc-hardcoded-claim-mapper",
                    "config": {
                        "claim.name": "app_id", "claim.value": target_app_id,
                        "jsonType.label": "String", "access.token.claim": "true",
                        "id.token.claim": "false", "userinfo.token.claim": "false",
                    },
                },
            ],
        }

    async def _verify(
        self, client: httpx.AsyncClient, actual: dict[str, Any], desired: dict[str, Any],
    ) -> None:
        for key, expected in desired.items():
            if key in {"id", "name", "defaultClientScopes", "optionalClientScopes", "protocolMappers"}:
                continue
            value = actual.get(key)
            if key == "attributes" and isinstance(value, dict):
                value = {name: value.get(name) for name in expected}
            if value != expected:
                raise KeycloakPreviewRegistrationError("Keycloak did not retain the isolated preview policy")
        mappers = actual.get("protocolMappers")
        if not isinstance(mappers, list) or len(mappers) != len(desired["protocolMappers"]):
            raise KeycloakPreviewRegistrationError("Keycloak did not retain the preview token mappers")
        for expected in desired["protocolMappers"]:
            if not any(
                isinstance(mapper, dict) and all(mapper.get(k) == v for k, v in expected.items())
                for mapper in mappers
            ):
                raise KeycloakPreviewRegistrationError("Keycloak did not retain the preview token mappers")
        for kind, expected_names in (("default", set(_DEFAULT_SCOPES)), ("optional", set())):
            _, scopes = await self._request(
                client, "GET", f"{self._admin_url}/{actual['id']}/{kind}-client-scopes", accepted={200},
            )
            if (
                not isinstance(scopes, list)
                or any(
                    not isinstance(scope, dict) or not isinstance(scope.get("name"), str)
                    for scope in scopes
                )
                or {scope.get("name") for scope in scopes} != expected_names
            ):
                raise KeycloakPreviewRegistrationError("Keycloak preview inherited unexpected client scopes")

    def _environment(self, sandbox_id: str) -> dict[str, str]:
        return {
            "AUTH_ENABLED": "true", "AUTH_PROVIDER": "jwt", "AUTH_ISSUER": self.issuer,
            "AUTH_JWKS_URL": self._jwks_url,
            "AUTH_AUDIENCE": f"mozaiks-preview-api-{sandbox_id}", "AUTH_AUDIENCE_MODE": "single",
            "AUTH_ACCESS_TOKEN_TYPE_CLAIM": ACCESS_TOKEN_TYPE_CLAIM,
            "AUTH_ACCESS_TOKEN_TYPE_VALUE": ACCESS_TOKEN_TYPE_VALUE,
            "AUTH_APP_ID_CLAIM": "app_id", "AUTH_SCOPES_CLAIM": "scope", "AUTH_SCOPES_FORMAT": "space",
            "MOZAIKS_OIDC_AUTHORITY": "", "MOZAIKS_OIDC_DISCOVERY_URL": "",
            "VITE_OIDC_AUTHORITY": self.issuer, "VITE_OIDC_DISCOVERY_URL": "",
            "VITE_OIDC_CLIENT_ID": f"mozaiks-preview-{sandbox_id}",
            "VITE_OIDC_SCOPE": "openid profile email",
        }

    async def register(
        self, *, sandbox_id: str, target_app_id: str, preview_url: str, callback_path: str,
    ) -> dict[str, str]:
        _sandbox_uuid(sandbox_id)
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", target_app_id):
            raise KeycloakPreviewRegistrationError("Preview requires its bound target app identity")
        origin, callback = _callback(preview_url, callback_path)
        desired = self._representation(
            sandbox_id=sandbox_id, target_app_id=target_app_id, origin=origin, callback=callback,
        )
        try:
            async with asyncio.timeout(30), httpx.AsyncClient(
                timeout=httpx.Timeout(10, connect=3), follow_redirects=False, trust_env=False,
            ) as client:
                await self._authenticate(client)
                existing = await self._lookup(client, sandbox_id)
                if existing is None:
                    await self._request(client, "POST", self._admin_url, accepted={201, 409}, json=desired)
                    existing = await self._lookup(client, sandbox_id)
                    if existing is None:
                        raise KeycloakPreviewRegistrationError("Keycloak preview creation was not confirmed")
                if existing["attributes"].get("mozaiks.preview.target_app_id") != target_app_id:
                    raise KeycloakPreviewRegistrationError("Keycloak preview target identity did not match")
                desired["id"] = existing["id"]
                await self._request(
                    client, "PUT", f"{self._admin_url}/{existing['id']}", accepted={204}, json=desired,
                )
                confirmed = await self._lookup(client, sandbox_id)
                if confirmed is None:
                    raise KeycloakPreviewRegistrationError("Keycloak preview creation was not confirmed")
                await self._verify(client, confirmed, desired)
                return self._environment(sandbox_id)
        except TimeoutError:
            raise KeycloakPreviewRegistrationError("Keycloak preview registration timed out") from None

    async def remove(self, *, sandbox_id: str) -> None:
        _sandbox_uuid(sandbox_id)
        try:
            async with asyncio.timeout(30), httpx.AsyncClient(
                timeout=httpx.Timeout(10, connect=3), follow_redirects=False, trust_env=False,
            ) as client:
                await self._authenticate(client)
                existing = await self._lookup(client, sandbox_id)
                if existing is None:
                    return
                await self._request(
                    client, "DELETE", f"{self._admin_url}/{existing['id']}", accepted={204, 404},
                )
                if await self._lookup(client, sandbox_id) is not None:
                    raise KeycloakPreviewRegistrationError("Keycloak preview cleanup was not confirmed")
        except TimeoutError:
            raise KeycloakPreviewRegistrationError("Keycloak preview cleanup timed out") from None


def keycloak_preview_registration_configured() -> bool:
    return _CLIENT_ENV in os.environ or _SECRET_ENV in os.environ


def resolve_keycloak_preview_registrar() -> KeycloakPreviewRegistrar | None:
    if not keycloak_preview_registration_configured():
        return None
    client_id, secret = os.getenv(_CLIENT_ENV, ""), os.getenv(_SECRET_ENV, "")
    if not client_id.strip() or not secret.strip():
        raise KeycloakPreviewRegistrationError("Configure both Keycloak preview registrar credentials")
    try:
        config = resolve_auth_config()
    except AuthError:
        raise KeycloakPreviewRegistrationError("Preview registration requires configured Studio Keycloak auth") from None
    if config.provider != "keycloak":
        raise KeycloakPreviewRegistrationError("Preview registration requires native Studio Keycloak auth")
    return KeycloakPreviewRegistrar(
        server_url=config.settings.get("KEYCLOAK_URL", ""), realm=config.settings.get("KEYCLOAK_REALM", ""),
        client_id=client_id, client_secret=secret,
        jwks_url=os.getenv("MOZAIKS_PREVIEW_ENV_AUTH_JWKS_URL") or None,
    )
