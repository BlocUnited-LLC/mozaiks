"""Fail closed before the repo-local production Compose stack imports a realm."""

from __future__ import annotations

import ipaddress
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit


def _https_endpoint(
    value: object, label: str, *, origin: bool = False
) -> tuple[str, str, int | None]:
    if not isinstance(value, str) or not value or any(char.isspace() for char in value):
        raise ValueError(f"{label} must be a nonempty HTTPS URL")
    if "*" in value or "\\" in value:
        raise ValueError(f"{label} must be an exact HTTPS URL without wildcards")
    try:
        parts = urlsplit(value)
        host = parts.hostname
        port = parts.port
    except ValueError as exc:
        raise ValueError(f"{label} has an invalid URL") from exc
    if (
        parts.scheme.lower() != "https"
        or not host
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or "%" in parts.netloc
    ):
        raise ValueError(
            f"{label} must be an exact HTTPS URL without credentials, query, or fragment"
        )
    host = host.lower().rstrip(".")
    if host == "localhost" or host.endswith(".localhost") or host == "localhost.localdomain":
        raise ValueError(f"{label} cannot use localhost")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and (
        address.is_loopback or address.is_unspecified or address.is_link_local
    ):
        raise ValueError(f"{label} cannot use a loopback or link-local address")
    if origin and parts.path not in ("", "/"):
        raise ValueError(f"{label} must contain only an HTTPS origin")
    return "https", host, port


def validate_realm_import(path: Path, keycloak_hostname: str) -> None:
    if not keycloak_hostname:
        raise ValueError("KC_HOSTNAME must be set to the public Keycloak hostname")
    hostname_url = (
        keycloak_hostname if "://" in keycloak_hostname else f"https://{keycloak_hostname}"
    )
    _https_endpoint(hostname_url, "KC_HOSTNAME", origin=True)

    try:
        realm = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("production realm import must be an existing UTF-8 JSON file") from exc
    if (
        not isinstance(realm, dict)
        or realm.get("realm") != "mozaiks"
        or realm.get("enabled") is not True
    ):
        raise ValueError("production realm import must enable the mozaiks realm")
    if realm.get("sslRequired") not in {"external", "all"}:
        raise ValueError("production realm import must require TLS for external clients")
    clients = realm.get("clients")
    if not isinstance(clients, list) or not clients:
        raise ValueError("production realm import must include an OIDC browser client")

    browser_clients = 0
    for index, client in enumerate(clients):
        if not isinstance(client, dict):
            raise ValueError(f"clients[{index}] must be an object")
        label = f"clients[{index}]"
        for field in ("rootUrl", "baseUrl", "adminUrl"):
            if field in client:
                _https_endpoint(client[field], f"{label}.{field}")
        redirects = client.get("redirectUris", [])
        origins = client.get("webOrigins", [])
        if not isinstance(redirects, list) or not isinstance(origins, list):
            raise ValueError(f"{label} redirectUris and webOrigins must be lists")
        redirect_origins = {
            _https_endpoint(value, f"{label}.redirectUris[{number}]")
            for number, value in enumerate(redirects)
        }
        web_origins = {
            _https_endpoint(value, f"{label}.webOrigins[{number}]", origin=True)
            for number, value in enumerate(origins)
        }
        if redirect_origins and not redirect_origins.issubset(web_origins):
            raise ValueError(f"{label} webOrigins must include every redirect URI origin")
        if (
            client.get("enabled") is True
            and client.get("protocol") == "openid-connect"
            and client.get("publicClient") is True
            and client.get("standardFlowEnabled") is True
        ):
            if not redirect_origins:
                raise ValueError(f"{label} browser client requires an HTTPS redirect URI")
            browser_clients += 1
    if not browser_clients:
        raise ValueError(
            "production realm import must include an enabled public OIDC browser client"
        )


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: validate_prod_realm_import.py REALM_JSON", file=sys.stderr)
        return 2
    try:
        validate_realm_import(Path(sys.argv[1]), os.environ.get("KC_HOSTNAME", ""))
    except ValueError as exc:
        print(f"Production realm import rejected: {exc}", file=sys.stderr)
        return 1
    print("Production realm import accepted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
