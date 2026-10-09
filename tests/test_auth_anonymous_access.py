"""Authentication off grants development access per request, never per process.

``AUTH_ANON_ACCESS`` (``local`` default, ``public``, ``open``) decides whom an
explicitly unauthenticated host serves and with which privileges, and the
privilege is attached to the principal when it is minted. Implicit demo mode
(no auth configuration) serves nobody. These tests drive the real auth
dependencies, websocket helpers and privilege sites in-process: TestClient
peers are chosen with ``client=(host, port)``; nothing binds a socket.
"""

from __future__ import annotations

import ipaddress
import logging
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import Depends, FastAPI, HTTPException, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from mozaiksai.core.auth import UserPrincipal, WebSocketUser, optional_user, require_user
from mozaiksai.core.auth.adapters import registry as auth_registry
from mozaiksai.core.auth.adapters.base import AuthError
from mozaiksai.core.auth.anonymous_access import (
    MANAGEMENT_NOT_CONFIGURED_MESSAGE,
    NOT_CONFIGURED_MESSAGE,
    STUDIO_PUBLIC_MESSAGE,
    LocalityRefusal,
    is_local_client,
    local_client_refusal,
    local_only_message,
    resolve_anonymous_grant,
)
from mozaiksai.core.auth.config import clear_auth_config_cache
from mozaiksai.core.auth.dependencies import (
    is_shared_development_identity,
    resolve_scope_from_principal,
    validate_user_id_against_principal,
)
from mozaiksai.core.auth.websocket_auth import (
    accept_websocket,
    authenticate_websocket,
    authenticate_websocket_with_path_user,
)

LOCAL = ("127.0.0.1", 50000)
LOCAL_V6 = ("::1", 50000)
LOCAL_MAPPED = ("::ffff:127.0.0.1", 50000)
BRIDGE = ("172.20.0.1", 50000)  # the host browser reaching a container through Docker's gateway
LAN = ("192.168.1.20", 50000)
HERE = "http://localhost:8000"  # a base URL whose Host names this machine

_DEV_SCOPE = "workspace_support.manage"
_JWT = {
    "AUTH_PROVIDER": "jwt",
    "AUTH_JWKS_URL": "https://idp.example.invalid/jwks",
    "AUTH_ISSUER": "https://idp.example.invalid",
    "AUTH_AUDIENCE": "api",
}


@pytest.fixture(autouse=True)
def _fresh_auth_state(monkeypatch):
    for name in auth_registry._ALL_AUTH_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    clear_auth_config_cache()
    auth_registry.reset_auth_adapter()
    yield
    clear_auth_config_cache()
    auth_registry.reset_auth_adapter()


def _auth(monkeypatch, **values: str) -> None:
    """Make ``values`` the whole auth configuration of this process."""
    for name in auth_registry._ALL_AUTH_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    clear_auth_config_cache()
    auth_registry.reset_auth_adapter()


def _off(monkeypatch, access: str | None = None, **values: str) -> None:
    if access is not None:
        values["AUTH_ANON_ACCESS"] = access
    _auth(monkeypatch, AUTH_ENABLED="false", **values)


def _describe(principal: UserPrincipal | WebSocketUser | None) -> dict[str, Any] | None:
    if principal is None:
        return None
    return {
        "user_id": principal.user_id,
        "email": principal.email,
        "roles": list(principal.roles),
        "scopes": list(principal.scopes),
        "provenance": principal.auth_provenance,
        "development_access": principal.has_local_development_access,
    }


def _app() -> FastAPI:
    from mozaiksai.core.admin.router import _require_admin

    app = FastAPI()

    @app.get("/me")
    async def me(principal: UserPrincipal = Depends(require_user)):
        return _describe(principal)

    @app.get("/optional")
    async def optional(principal: UserPrincipal | None = Depends(optional_user)):
        return _describe(principal)

    @app.get("/admin")
    async def admin(principal: UserPrincipal = Depends(_require_admin)):
        return _describe(principal)

    @app.websocket("/ws/{user_id}")
    async def ws_path(websocket: WebSocket, user_id: str):
        user = await authenticate_websocket_with_path_user(websocket, user_id)
        if user is None:
            return
        await accept_websocket(websocket)
        await websocket.send_json(_describe(user))
        await websocket.close()

    @app.websocket("/ws")
    async def ws_plain(websocket: WebSocket):
        user = await authenticate_websocket(websocket)
        if user is None:
            return
        await accept_websocket(websocket)
        await websocket.send_json(_describe(user))
        await websocket.close()

    return app


def _client(peer: tuple[str, int] | None = LOCAL, *, base_url: str = HERE) -> TestClient:
    if peer is None:  # Starlette default: "testclient"
        return TestClient(_app(), raise_server_exceptions=False, base_url=base_url)
    return TestClient(_app(), raise_server_exceptions=False, client=peer, base_url=base_url)


def _ws_url(client: TestClient, path: str) -> str:
    # websocket_connect resolves a relative path against ws://testserver, not base_url.
    return "ws" + str(client.base_url).rstrip("/").removeprefix("http") + path


def _ws(client: TestClient, path: str, **kwargs: Any) -> dict[str, Any]:
    with client.websocket_connect(_ws_url(client, path), **kwargs) as websocket:
        return websocket.receive_json()


def _ws_refusal(client: TestClient, path: str, **kwargs: Any) -> WebSocketDisconnect:
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect(_ws_url(client, path), **kwargs) as websocket:
            websocket.receive_json()
    return closed.value


# --- Resolution ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, "local"), ("", "local"), ("local", "local"), ("public", "public"), (" OPEN ", "open")],
)
def test_explicit_disable_resolves_the_anonymous_access_policy(monkeypatch, raw, expected) -> None:
    environ = {"AUTH_ENABLED": "false"}
    if raw is not None:
        environ["AUTH_ANON_ACCESS"] = raw
    assert auth_registry.resolve_auth_config(environ=environ).anonymous_access == expected
    assert auth_registry.resolve_auth_config(environ={"AUTH_PROVIDER": "none"}).anonymous_access == "local"


@pytest.mark.parametrize("raw", ["everyone", "none", "Public!", "loc al"])
def test_unrecognized_anonymous_access_fails_closed(raw) -> None:
    message = f"Unrecognized AUTH_ANON_ACCESS value: {raw!r}. Use local, public or open."
    with pytest.raises(AuthError) as with_switch:
        auth_registry.resolve_auth_config(environ={"AUTH_ENABLED": "false", "AUTH_ANON_ACCESS": raw})
    assert message in str(with_switch.value)
    with pytest.raises(AuthError) as alone:
        auth_registry.resolve_auth_config(environ={"AUTH_ANON_ACCESS": raw})
    assert message in str(alone.value)


@pytest.mark.parametrize("deployed", ["production", "staging", "preview", "prod-us"])
@pytest.mark.parametrize(
    "posture",
    [
        {"AUTH_ANON_ACCESS": "local"},
        {"AUTH_ANON_ACCESS": "public"},
        {"AUTH_ANON_ACCESS": "open"},
        {"AUTH_ENABLED": "false", "AUTH_ANON_ACCESS": "public"},
        {"AUTH_ENABLED": "false", "AUTH_ANON_ACCESS": "open"},
    ],
    ids=["local_alone", "public_alone", "open_alone", "off_public", "off_open"],
)
def test_every_posture_is_refused_in_a_deployed_environment(deployed, posture) -> None:
    """The environment allowlist is unchanged: no posture, public included, runs deployed."""
    with pytest.raises(AuthError, match="Authentication-disabled operation is not permitted"):
        auth_registry.resolve_auth_config(environ={"ENV": deployed, **posture})


def test_public_access_refuses_anonymous_roles() -> None:
    with pytest.raises(AuthError, match="cannot be combined with AUTH_ANON_ROLES"):
        auth_registry.resolve_auth_config(
            environ={"AUTH_ENABLED": "false", "AUTH_ANON_ACCESS": "public", "AUTH_ANON_ROLES": "admin"}
        )
    blank = auth_registry.resolve_auth_config(
        environ={"AUTH_ENABLED": "false", "AUTH_ANON_ACCESS": "public", "AUTH_ANON_ROLES": " , "}
    )
    assert blank.anonymous_access == "public"


@pytest.mark.parametrize("access", ["local", "public", "open"])
def test_anonymous_access_alone_is_an_explicit_choice(access) -> None:
    """What a generated public app ships: AUTH_ANON_ACCESS=public and nothing else."""
    config = auth_registry.resolve_auth_config(environ={"AUTH_ANON_ACCESS": access})

    assert (config.provider, config.source, config.explicitly_disabled) == ("none", "explicit_disable", True)
    assert config.anonymous_access == access


@pytest.mark.parametrize(
    ("environ", "provider", "source"),
    [
        ({"SUPABASE_URL": "https://project.supabase.invalid"}, "supabase", "auto_detected"),
        (
            {"MOZAIKS_OIDC_AUTHORITY": "https://idp.example.invalid", "AUTH_AUDIENCE": "api"},
            "jwt",
            "auto_detected",
        ),
        (_JWT, "jwt", "explicit_provider"),
    ],
    ids=["supabase_detected", "oidc_detected", "explicit_provider"],
)
def test_provider_settings_win_over_anonymous_access_alone(environ, provider, source) -> None:
    """An operator who adds an identity provider to a public image turns authentication on."""
    config = auth_registry.resolve_auth_config(environ={**environ, "AUTH_ANON_ACCESS": "public"})

    assert (config.provider, config.source, config.enabled) == (provider, source, True)
    assert config.anonymous_access is None


def test_auth_enabled_false_stays_a_hard_off_and_reports_ignored_provider_settings() -> None:
    hard_off = auth_registry.resolve_auth_config(
        environ={"AUTH_ENABLED": "false", "AUTH_ANON_ACCESS": "public", "SUPABASE_URL": "https://x.invalid"}
    )
    assert (hard_off.enabled, hard_off.anonymous_access) == (False, "public")
    assert auth_registry.unused_provider_settings(hard_off) == ("SUPABASE_URL",)

    partial = auth_registry.resolve_auth_config(
        environ={"AUTH_ANON_ACCESS": "public", "KEYCLOAK_URL": "https://idp.example.invalid"}
    )
    assert (partial.source, partial.anonymous_access) == ("explicit_disable", "public")
    assert auth_registry.unused_provider_settings(partial) == ("KEYCLOAK_URL",)


def test_anonymous_access_never_turns_authentication_off_by_itself_when_enabled() -> None:
    with pytest.raises(AuthError, match="AUTH_ENABLED=true but no authentication provider"):
        auth_registry.resolve_auth_config(environ={"AUTH_ENABLED": "true", "AUTH_ANON_ACCESS": "public"})
    enabled = auth_registry.resolve_auth_config(environ={**_JWT, "AUTH_ANON_ACCESS": "nonsense"})
    assert enabled.enabled and enabled.anonymous_access is None
    blank = auth_registry.resolve_auth_config(environ={"AUTH_ANON_ACCESS": "  "})
    assert blank.source == "demo_default" and blank.anonymous_access is None


@pytest.mark.parametrize(
    ("environ", "grants"),
    [
        ({}, False),
        ({"ENV": "development"}, False),
        ({"AUTH_ENABLED": "false"}, True),
        ({"AUTH_ENABLED": "false", "AUTH_ANON_ACCESS": "open"}, True),
        ({"AUTH_ENABLED": "false", "AUTH_ANON_ACCESS": "public"}, False),
        ({"AUTH_ANON_ACCESS": "open"}, True),
        ({"AUTH_ANON_ACCESS": "public"}, False),
        (_JWT, False),
    ],
    ids=["demo", "demo_env_development", "local", "open", "public", "open_alone", "public_alone", "auth_on"],
)
def test_development_access_needs_explicit_disable_and_a_development_posture(environ, grants) -> None:
    assert auth_registry.resolve_auth_config(environ=environ).grants_development_access is grants


def test_anonymous_access_is_part_of_the_adapter_identity() -> None:
    local = auth_registry.resolve_auth_config(environ={"AUTH_ENABLED": "false"})
    public = auth_registry.resolve_auth_config(environ={"AUTH_ENABLED": "false", "AUTH_ANON_ACCESS": "public"})
    assert local.fingerprint != public.fingerprint


def test_resolved_config_rejects_an_explicit_disable_without_an_access_policy() -> None:
    config = auth_registry.resolve_auth_config(environ={"AUTH_ENABLED": "false"})
    with pytest.raises(AuthError, match="anonymous access is resolved exactly"):
        auth_registry.ResolvedAuthConfig(
            provider="none", enabled=False, explicitly_disabled=True, source="explicit_disable",
            environment=config.environment, settings=config.settings, fingerprint=config.fingerprint,
        )


# --- Locality: peer, forwarding, Host, Origin -------------------------------------


def _scope(client: Any, *headers: tuple[str, str]) -> dict[str, Any]:
    return {
        "type": "http",
        "client": client,
        "headers": [(name.lower().encode("latin-1"), value.encode("latin-1")) for name, value in headers],
    }


@pytest.mark.parametrize(
    ("client", "local"),
    [
        (("127.0.0.1", 1), True),
        (("127.0.0.2", 1), True),
        (("::1", 1), True),
        (("[::1]", 1), True),
        (("::1%lo0", 1), True),
        (("::ffff:127.0.0.1", 1), True),
        (["127.0.0.1", 1], True),
        (("testclient", 50000), False),
        (None, False),
        (("localhost", 1), False),
        (("0.0.0.0", 1), False),
        (("::", 1), False),
        (("172.20.0.1", 1), False),
        (("::ffff:172.20.0.1", 1), False),
        (("192.168.1.20", 1), False),
        (("127.0.0.1",), False),
        (("127.0.0.1", 1, 2), False),
        ("127.0.0.1", False),
    ],
)
def test_only_a_loopback_ip_peer_is_local(client, local) -> None:
    assert is_local_client(_scope(client)) is local
    if not local:
        assert local_client_refusal(_scope(client)).rule == "peer"


def test_ipv4_mapped_loopback_is_checked_explicitly(monkeypatch) -> None:
    """Python 3.11 (the e2b sandbox image) says ::ffff:127.0.0.1 is not loopback."""
    monkeypatch.setattr(ipaddress.IPv6Address, "is_loopback", property(lambda self: self._ip == 1))
    assert ipaddress.ip_address("::ffff:127.0.0.1").is_loopback is False

    assert is_local_client(_scope(("::ffff:127.0.0.1", 1)))
    assert is_local_client(_scope(("::1", 1)))
    assert not is_local_client(_scope(("::ffff:10.0.0.1", 1)))
    assert is_local_client(_scope(("127.0.0.1", 1), ("Host", "[::ffff:127.0.0.1]:8000")))


@pytest.mark.parametrize(
    "headers",
    [
        (("X-Forwarded-For", "127.0.0.1"),),
        (("X-Forwarded-For", "::1"),),
        (("X-Forwarded-For", "203.0.113.9"),),
        (("X-Forwarded-For", ""),),
        (("X-Real-IP", "127.0.0.1"),),
        (("x-real-ip", ""),),
        (("Forwarded", "for=127.0.0.1"),),
    ],
)
def test_any_forwarding_header_makes_a_request_not_local(headers) -> None:
    """With such a header the peer may already be rewritten by a trust setting the app cannot see."""
    refusal = local_client_refusal(_scope(("127.0.0.1", 1), *headers))

    assert refusal == LocalityRefusal("forwarded", headers[0][0].lower())


@pytest.mark.asyncio
@pytest.mark.parametrize("trusted", ["*", "172.20.0.4"], ids=["trust_all_cli_flag", "specific_forwarder"])
async def test_a_remote_peer_rewritten_to_loopback_by_the_server_is_not_local(trusted) -> None:
    """uvicorn rewrites the peer from X-Forwarded-For for a trusted forwarder
    (--forwarded-allow-ips, FORWARDED_ALLOW_IPS); the header that did it stays."""
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    seen: dict[str, Any] = {}

    async def app(scope, receive, send):
        seen.update(scope)

    middleware = ProxyHeadersMiddleware(app, trusted_hosts=trusted)
    await middleware(_scope(("172.20.0.4", 40000), ("X-Forwarded-For", "127.0.0.1")), None, None)

    assert seen["client"][0] == "127.0.0.1"  # the spoof worked at the server layer ...
    assert local_client_refusal(seen).rule == "forwarded"  # ... and is refused here


def test_trust_settings_alone_do_not_refuse_a_direct_loopback_request(monkeypatch) -> None:
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "*")
    assert is_local_client(_scope(("127.0.0.1", 1), ("Host", "localhost:8000")))


@pytest.mark.parametrize(
    ("host", "local"),
    [
        ("localhost", True),
        ("LOCALHOST:8000", True),
        ("localhost.", True),
        ("LOCALHOST.", True),
        ("localhost.:3000", True),
        ("app.localhost", True),
        ("app.localhost:3000", True),
        ("127.0.0.1", True),
        ("127.0.0.1:8000", True),
        ("127.9.9.9:1", True),
        ("[::1]", True),
        ("[::1]:8000", True),
        ("[::ffff:127.0.0.1]", True),
        ("evil.example", False),
        ("attacker.example", False),
        ("localhost.evil.example", False),
        ("localhost:8000.attacker.example", False),
        ("127.0.0.1.nip.io", False),
        ("0.0.0.0", False),
        ("0.0.0.0:8000", False),
        ("127.1", False),
        ("2130706433", False),
        ("localhost..", False),
        ("::1", False),
        ("[::1", False),
        ("[::1]8000", False),
        ("localhost:abc", False),
        ("localhost:123456", False),
        ("192.168.1.20:8000", False),
        ("testserver", False),
        ("[::ffff:10.0.0.1]", False),
        ("", False),
    ],
)
def test_the_host_header_must_name_this_machine(host, local) -> None:
    """DNS rebinding: a page whose name resolves to 127.0.0.1 still sends its own name."""
    refusal = local_client_refusal(_scope(("127.0.0.1", 1), ("Host", host)))

    assert (refusal is None) is local
    if not local:
        assert refusal == LocalityRefusal("host", repr(host))


@pytest.mark.parametrize(
    ("origin", "local"),
    [
        ("http://localhost:3000", True),
        ("http://127.0.0.1:5173", True),
        ("https://localhost", True),
        ("https://127.0.0.1", True),
        ("http://[::1]:5173", True),
        ("http://app.localhost", True),
        ("HTTP://LOCALHOST:3000", True),
        ("http://localhost:3000/", True),
        ("null", False),
        ("http://evil.example", False),
        ("http://localhost.evil.example", False),
        ("http://localhost:3000.evil.example", False),
        ("https://127.0.0.1.nip.io", False),
        ("file://", False),
        ("chrome-extension://abcdef", False),
        ("vscode-webview://1a2b3c", False),
        ("ws://localhost:8000", False),
        ("ftp://localhost", False),
        ("http://user@localhost", False),
        ("http://user:pass@localhost:3000", False),
        ("http://localhost/path", False),
        ("http://localhost:3000?x=1", False),
        ("http://localhost:3000#x", False),
        ("http://[::1", False),
        ("", False),
    ],
)
def test_the_origin_header_must_be_a_page_of_this_machine(origin, local) -> None:
    """Cross-site requests and WebSocket hijacking from a site the developer visits."""
    refusal = local_client_refusal(_scope(("127.0.0.1", 1), ("Host", "localhost:8000"), ("Origin", origin)))

    assert (refusal is None) is local
    if not local:
        assert refusal == LocalityRefusal("origin", repr(origin))


def test_a_request_without_host_or_origin_from_loopback_is_local() -> None:
    assert is_local_client(_scope(("127.0.0.1", 1)))


@pytest.mark.parametrize(
    ("headers", "refusal"),
    [
        ((("Sec-Fetch-Site", "cross-site"),), LocalityRefusal("fetch-site", "'cross-site'")),
        ((("Sec-Fetch-Site", " Cross-Site "),), LocalityRefusal("fetch-site", "' Cross-Site '")),
        ((("Sec-Fetch-Site", "same-site"), ("Sec-Fetch-Site", "cross-site")), LocalityRefusal("fetch-site", "'cross-site'")),
        ((("Sec-Fetch-Site", "same-origin"),), None),
        ((("Sec-Fetch-Site", "same-site"),), None),
        ((("Sec-Fetch-Site", "none"),), None),
        ((), None),
        ((("Sec-Fetch-Site", "cross-site"), ("Origin", "http://127.0.0.1:3000")), None),
        (
            (("Sec-Fetch-Site", "cross-site"), ("Origin", "http://evil.example")),
            LocalityRefusal("origin", "'http://evil.example'"),
        ),
    ],
    ids=[
        "cross_site_without_origin", "cross_site_mixed_case", "any_cross_site_value",
        "same_origin", "same_site", "none", "absent",
        "cross_site_with_local_origin", "cross_site_with_foreign_origin",
    ],
)
def test_a_cross_site_fetch_without_an_origin_is_not_local(headers, refusal) -> None:
    """Browsers send no Origin on no-cors cross-site GETs (image and script
    loads, navigations); fetch metadata still names their site."""
    assert local_client_refusal(_scope(("127.0.0.1", 1), ("Host", "localhost:8000"), *headers)) == refusal


# --- Grants ------------------------------------------------------------------------


def _grant(environ: dict[str, str], client: Any, *headers: tuple[str, str]):
    config = auth_registry.resolve_auth_config(environ=environ)
    return resolve_anonymous_grant(_scope(client, *headers), config)


def test_grants_per_posture() -> None:
    demo = _grant({}, LOCAL)
    assert demo.refused and demo.status_code == 401 and demo.detail == NOT_CONFIGURED_MESSAGE

    local = {"AUTH_ENABLED": "false"}
    assert _grant(local, LOCAL).development_access
    assert _grant(local, LOCAL_MAPPED, ("Host", "127.0.0.1:8000")).development_access
    refused = _grant(local, BRIDGE)
    assert refused.refused and refused.status_code == 403
    assert refused.detail == local_only_message(LocalityRefusal("peer", "'172.20.0.1'"))
    proxied = _grant(local, LOCAL, ("X-Forwarded-For", "127.0.0.1"))
    assert proxied.detail == local_only_message(LocalityRefusal("forwarded", "x-forwarded-for"))
    assert _grant({"AUTH_ANON_ACCESS": "local"}, LOCAL).development_access

    public = {"AUTH_ENABLED": "false", "AUTH_ANON_ACCESS": "public"}
    assert _grant(public, LAN).kind == "public_visitor"
    assert _grant(public, LOCAL).kind == "public_visitor"

    open_ = {"AUTH_ENABLED": "false", "AUTH_ANON_ACCESS": "open"}
    assert _grant(open_, LAN, ("Origin", "http://evil.example")).development_access

    with pytest.raises(ValueError):
        _grant(_JWT, LOCAL)


@pytest.mark.parametrize(
    ("headers", "rule", "says"),
    [
        ((), "peer", "This request came from '172.20.0.1', another machine."),
        ((("X-Real-IP", "10.0.0.7"),), "forwarded", "came through a proxy (it carries a x-real-ip header)"),
        ((("Host", "rebind.example"),), "host", "addressed to 'rebind.example', which does not name this machine"),
        ((("Origin", "null"),), "origin", "sent by a page from 'null', which this machine does not serve"),
        (
            (("Sec-Fetch-Site", "cross-site"),),
            "fetch-site",
            "This request was sent by a page on another site (Sec-Fetch-Site: cross-site), "
            "which this machine does not serve.",
        ),
    ],
)
def test_each_refusal_names_its_rule_and_its_fix(headers, rule, says) -> None:
    peer = BRIDGE if rule == "peer" else LOCAL
    grant = _grant({"AUTH_ENABLED": "false"}, peer, *headers)

    assert grant.refused
    assert grant.detail.startswith("Authentication is off and AUTH_ANON_ACCESS is local")
    assert says in grant.detail
    assert "http://localhost:<port> or http://127.0.0.1:<port>" in grant.detail
    assert grant.ws_reason.endswith(")") and len(grant.ws_reason.encode()) <= 123
    if rule == "peer":
        assert "AUTH_ANON_ACCESS=open" in grant.detail and "AUTH_ANON_ACCESS=public" in grant.detail


# --- Principal minting over HTTP -----------------------------------------------------


@pytest.mark.parametrize("peer", [LOCAL, LOCAL_V6, LOCAL_MAPPED])
def test_local_posture_gives_this_machine_development_access(monkeypatch, peer) -> None:
    _off(monkeypatch, AUTH_ANON_ROLES="admin,user")

    body = _client(peer).get("/me", headers={"Origin": "http://localhost:3000"}).json()

    assert body["user_id"] == "anonymous"
    assert body["roles"] == ["admin", "user"]
    assert _DEV_SCOPE in body["scopes"]
    assert body["provenance"] == "local_development"
    assert body["development_access"] is True


@pytest.mark.parametrize("path", ["/me", "/admin"])
@pytest.mark.parametrize("peer", [BRIDGE, LAN, None], ids=["docker_gateway", "lan", "testclient"])
def test_local_posture_refuses_every_other_client(monkeypatch, peer, path) -> None:
    _off(monkeypatch, AUTH_ANON_ROLES="admin,user")

    response = _client(peer).get(path)

    assert response.status_code == 403
    assert response.json()["detail"].startswith("Authentication is off and AUTH_ANON_ACCESS is local")
    assert f"This request came from {peer[0] if peer else 'testclient'!r}, another machine." in response.json()["detail"]


@pytest.mark.parametrize(
    "headers",
    [{"X-Forwarded-For": "192.168.1.20"}, {"X-Forwarded-For": "127.0.0.1"}, {"Forwarded": "for=127.0.0.1"}],
)
def test_local_posture_refuses_a_proxied_request(monkeypatch, headers) -> None:
    """The web shell dev server marks only other machines; any marker refuses."""
    _off(monkeypatch)

    response = _client(LOCAL).get("/me", headers=headers)

    assert response.status_code == 403
    assert "came through a proxy" in response.json()["detail"]


@pytest.mark.parametrize(
    ("base_url", "headers", "rule_text"),
    [
        ("http://rebind.example:8000", {}, "addressed to 'rebind.example:8000'"),
        (HERE, {"Origin": "http://evil.example"}, "sent by a page from 'http://evil.example'"),
        (HERE, {"Origin": "null"}, "sent by a page from 'null'"),
    ],
    ids=["dns_rebinding_host", "cross_site_origin", "opaque_origin"],
)
def test_local_posture_refuses_a_browser_request_from_another_site(monkeypatch, base_url, headers, rule_text) -> None:
    _off(monkeypatch, AUTH_ANON_ROLES="admin")

    for path in ("/me", "/admin"):
        response = _client(LOCAL, base_url=base_url).get(path, headers=headers)
        assert response.status_code == 403
        assert rule_text in response.json()["detail"]
    persona = _client(LOCAL, base_url=base_url).get("/me?dev_user_id=mallory&dev_roles=admin", headers=headers)
    assert persona.status_code == 403


def test_dev_personas_need_development_access(monkeypatch) -> None:
    persona = {"X-Mozaiks-Dev-User-Id": "mallory", "X-Mozaiks-Dev-Roles": "admin"}

    _off(monkeypatch)
    local = _client(LOCAL).get("/me", headers=persona).json()
    assert (local["user_id"], local["roles"], local["provenance"]) == ("mallory", ["admin"], "dev_override")
    assert _client(BRIDGE).get("/me", headers=persona).status_code == 403
    assert _client(LOCAL).get("/me?dev_user_id=mallory&dev_roles=admin", headers={"X-Forwarded-For": "10.0.0.9"}).status_code == 403

    _off(monkeypatch, "public")
    visitor = _client(LAN).get("/me?dev_user_id=mallory&dev_roles=admin", headers=persona).json()
    assert (visitor["user_id"], visitor["roles"], visitor["provenance"]) == ("anonymous", [], "anonymous")

    _off(monkeypatch, "open")
    remote = _client(LAN).get("/me", headers=persona).json()
    assert (remote["user_id"], remote["provenance"]) == ("mallory", "dev_override")


def test_public_visitor_carries_no_development_privilege(monkeypatch) -> None:
    _off(monkeypatch, "public", AUTH_ANON_EMAIL="boss@example.com")

    body = _client(LAN).get("/me").json()

    assert body == {
        "user_id": "anonymous",
        "email": None,
        "roles": [],
        "scopes": ["access_as_user"],
        "provenance": "anonymous",
        "development_access": False,
    }


def test_public_visitor_scopes_come_from_auth_anon_scopes(monkeypatch) -> None:
    _off(monkeypatch, "public", AUTH_ANON_SCOPES="reports.read, reports.write")

    assert _client(LAN).get("/me").json()["scopes"] == ["reports.read", "reports.write"]


def test_public_image_posture_alone_serves_visitors(monkeypatch) -> None:
    _auth(monkeypatch, AUTH_ANON_ACCESS="public")

    assert _client(LAN).get("/me").json()["provenance"] == "anonymous"
    assert _client(LAN).get("/admin").status_code == 403


def test_open_posture_gives_every_client_development_access(monkeypatch) -> None:
    _off(monkeypatch, "open", AUTH_ANON_ROLES="admin")

    body = _client(LAN, base_url="http://lan-box.example").get("/me", headers={"Origin": "http://elsewhere.example"}).json()

    assert (body["roles"], body["provenance"]) == (["admin"], "local_development")


@pytest.mark.parametrize("environ", [{}, {"ENV": "development"}, {"KEYCLOAK_URL": "https://idp.example.invalid"}])
@pytest.mark.parametrize("path", ["/me", "/admin"])
def test_implicit_demo_mode_refuses_every_request(monkeypatch, environ, path) -> None:
    _auth(monkeypatch, **environ)

    response = _client(LOCAL).get(path)

    assert response.status_code == 401
    assert response.json()["detail"] == NOT_CONFIGURED_MESSAGE


def test_authentication_on_is_unchanged_for_every_client(monkeypatch) -> None:
    _auth(monkeypatch, **_JWT, AUTH_ANON_ACCESS="open")

    for peer in (LOCAL, LAN, None):
        response = _client(peer).get("/me", headers={"X-Mozaiks-Dev-User-Id": "mallory"})
        assert response.status_code == 401
        assert response.json()["detail"] == "Missing authorization token"
    assert _client(LAN).get("/optional").json() is None


# --- optional_user: a refused request is a request without credentials ---------------


@pytest.mark.parametrize(
    ("environ", "peer"),
    [
        ({"AUTH_ENABLED": "false"}, LAN),
        ({"AUTH_ENABLED": "false"}, None),
        ({}, LOCAL),
    ],
    ids=["local_posture_lan", "local_posture_testclient", "demo"],
)
def test_optional_user_is_none_when_the_policy_refuses(monkeypatch, environ, peer) -> None:
    _auth(monkeypatch, **environ)

    response = _client(peer).get("/optional")

    assert response.status_code == 200 and response.json() is None
    assert _client(peer).get("/me").status_code in (401, 403)


def test_optional_user_mints_like_require_user_otherwise(monkeypatch) -> None:
    _off(monkeypatch)
    assert _client(LOCAL).get("/optional").json()["provenance"] == "local_development"
    _off(monkeypatch, "public")
    assert _client(LAN).get("/optional").json()["provenance"] == "anonymous"


# --- WebSockets --------------------------------------------------------------------


def test_websocket_path_user_with_development_access(monkeypatch) -> None:
    _off(monkeypatch)

    body = _ws(_client(LOCAL), "/ws/victim", headers={"Origin": "http://localhost:3000"})

    assert (body["user_id"], body["provenance"]) == ("victim", "local_development")


@pytest.mark.parametrize("peer", [BRIDGE, None], ids=["docker_gateway", "testclient"])
def test_websocket_local_posture_closes_other_clients_before_accept(monkeypatch, peer) -> None:
    _off(monkeypatch)

    for path in ("/ws/victim", "/ws"):
        closed = _ws_refusal(_client(peer), path)
        assert closed.code == 1008
        assert closed.reason == (
            "Authentication is off; development access is for this machine only (peer is another machine)"
        )


@pytest.mark.parametrize(
    ("base_url", "headers", "reason"),
    [
        (HERE, {"Origin": "http://evil.example"}, "(Origin is not this machine)"),
        (HERE, {"X-Forwarded-For": "127.0.0.1"}, "(request came through a proxy)"),
        ("http://rebind.example", {}, "(Host is not this machine)"),
    ],
    ids=["cross_site_websocket_hijacking", "proxied", "dns_rebinding"],
)
def test_websocket_local_posture_refuses_a_foreign_page_or_proxy(monkeypatch, base_url, headers, reason) -> None:
    _off(monkeypatch)

    for path in ("/ws/victim", "/ws"):
        closed = _ws_refusal(_client(LOCAL, base_url=base_url), path, headers=headers)
        assert closed.code == 1008 and closed.reason.endswith(reason)


def test_websocket_visitor_is_bound_to_the_visitor_identity(monkeypatch, caplog) -> None:
    from mozaiksai.core.auth import websocket_auth

    _off(monkeypatch, "public")

    body = _ws(_client(LAN), "/ws/anonymous")
    assert (body["user_id"], body["scopes"], body["provenance"]) == ("anonymous", ["access_as_user"], "anonymous")

    long_id = "v" * 120
    with caplog.at_level(logging.WARNING, logger=websocket_auth.logger.name):
        closed = _ws_refusal(_client(LAN), "/ws/victim")
        _ws_refusal(_client(LAN), f"/ws/{long_id}")
    assert (closed.code, closed.reason) == (1008, "user_id mismatch")
    # The path user id is client-supplied: logged quoted and shortened.
    assert [record.getMessage() for record in caplog.records if "visitor tried" in record.getMessage()] == [
        "WebSocket visitor tried to connect as path user 'victim'",
        f"WebSocket visitor tried to connect as path user {long_id[:77] + '...'!r}",
    ]

    plain = _ws(_client(LAN), "/ws")
    assert (plain["roles"], plain["scopes"]) == ([], ["access_as_user"])


def test_websocket_open_posture_and_demo_mode(monkeypatch) -> None:
    _off(monkeypatch, "open")
    assert _ws(_client(LAN), "/ws/victim", headers={"Origin": "http://evil.example"})["user_id"] == "victim"
    assert _DEV_SCOPE in _ws(_client(LAN), "/ws")["scopes"]

    _auth(monkeypatch, ENV="development")
    closed = _ws_refusal(_client(LOCAL), "/ws/victim")
    assert (closed.code, closed.reason) == (1008, "Authentication is not configured")


# --- Privilege sites ---------------------------------------------------------------


def _principal(user_id: str = "anonymous", provenance: str = "anonymous", **overrides: Any) -> UserPrincipal:
    return UserPrincipal(
        user_id=user_id, email=None, name=None, roles=[], scopes=["access_as_user"],
        raw_claims={}, provider="none", auth_provenance=provenance, **overrides,
    )


def test_admin_gate_has_no_pass_through_for_auth_off(monkeypatch) -> None:
    """Admin comes from roles or the email allowlist only (router.py pass-through removed)."""
    _off(monkeypatch)
    assert _client(LOCAL).get("/admin").status_code == 403

    _off(monkeypatch, AUTH_ANON_ROLES="admin,user")
    assert _client(LOCAL).get("/admin").status_code == 200

    _off(monkeypatch, "open", AUTH_ANON_ROLES="admin")
    assert _client(LAN).get("/admin").status_code == 200


def test_admin_email_allowlist_never_promotes_a_visitor(monkeypatch) -> None:
    from mozaiksai.core.admin import router as admin_router

    monkeypatch.setattr(admin_router, "is_admin_by_email", lambda email: email == "boss@example.com")

    _off(monkeypatch, AUTH_ANON_EMAIL="boss@example.com")
    assert _client(LOCAL).get("/admin").status_code == 200

    _off(monkeypatch, "public", AUTH_ANON_EMAIL="boss@example.com")
    assert _client(LAN).get("/admin").status_code == 403

    # A persona may claim an allowlisted email only with development access.
    persona = {"X-Mozaiks-Dev-User-Id": "mallory", "X-Mozaiks-Dev-Email": "boss@example.com"}
    _off(monkeypatch)
    assert _client(LOCAL).get("/admin", headers=persona).status_code == 200
    assert _client(BRIDGE).get("/admin", headers=persona).status_code == 403
    _off(monkeypatch, "public")
    assert _client(LAN).get("/admin", headers=persona).status_code == 403
    assert _client(LAN).get("/me", headers=persona).json()["email"] is None


def test_only_the_shared_development_identity_names_other_users() -> None:
    development = _principal(provenance="local_development")
    visitor = _principal(provenance="anonymous")
    persona = _principal("mallory", provenance="dev_override")
    hosted_dev_user = _principal("dev-user", provenance="local_development")

    assert is_shared_development_identity(development)
    assert not is_shared_development_identity(visitor)
    assert not is_shared_development_identity(persona)
    assert not is_shared_development_identity(hosted_dev_user)

    assert resolve_scope_from_principal(development, default_user_id="demo-user") == ("default", "demo-user")
    assert resolve_scope_from_principal(development, user_id="victim") == ("default", "victim")
    assert resolve_scope_from_principal(visitor, default_user_id="demo-user") == ("default", "anonymous")
    with pytest.raises(HTTPException) as refused:
        resolve_scope_from_principal(visitor, user_id="victim")
    assert refused.value.status_code == 403
    # A local identity with its own id (AUTH_ANON_USER_ID) acts as itself, as before.
    assert validate_user_id_against_principal(hosted_dev_user) == "dev-user"
    with pytest.raises(HTTPException):
        validate_user_id_against_principal(hosted_dev_user, body_user_id="victim")


def test_a_token_whose_subject_is_anonymous_acts_only_as_itself() -> None:
    """The one change with authentication on: the user id "anonymous" in a
    validated token used to let the caller name any user and see every owner."""
    token_user = _principal(provenance="token_validated")

    assert not is_shared_development_identity(token_user)
    assert resolve_scope_from_principal(token_user, default_user_id="demo-user") == ("default", "anonymous")
    with pytest.raises(HTTPException) as refused:
        validate_user_id_against_principal(token_user, path_user_id="victim")
    assert refused.value.status_code == 403


@pytest.mark.parametrize(
    ("principal", "sees_every_owner"),
    [
        (_principal(provenance="local_development"), True),
        (_principal(provenance="anonymous"), False),
        (_principal("mallory", provenance="dev_override"), False),
        (_principal("owner", provenance="token_validated"), False),
        (_principal(provenance="token_validated"), False),
    ],
    ids=["shared_development_identity", "visitor", "persona", "token_user", "token_subject_anonymous"],
)
def test_chat_listing_scopes_every_principal_but_the_shared_development_identity(
    monkeypatch, principal, sees_every_owner
) -> None:
    from mozaiksai.hosts import runtime as runtime_app
    from mozaiksai.hosts.routers import chat as chat_router

    queries: list[dict[str, Any]] = []

    class _Cursor:
        def sort(self, *_args):
            return self

        async def to_list(self, length):
            return []

    class _Chats:
        def find(self, query):
            queries.append(query)
            return _Cursor()

    async def chat_coll():
        return _Chats()

    monkeypatch.setattr(runtime_app, "_chat_coll", chat_coll)
    app = FastAPI()
    app.include_router(chat_router.router)
    app.dependency_overrides[chat_router.require_user_scope] = lambda: principal

    assert TestClient(app).get("/api/chats/app-1/Generator").status_code == 200
    assert ("user_id" not in queries[0]) is sees_every_owner
    if not sees_every_owner:
        assert queries[0]["user_id"] == principal.user_id


# A chat record owned by "victim": served to its owner and to the shared
# development identity, refused to every other principal, a visitor included.
_VICTIMS_RECORD = pytest.mark.parametrize(
    ("principal", "served"),
    [
        (_principal("victim", provenance="token_validated"), True),
        (_principal(provenance="local_development"), True),
        (_principal(provenance="anonymous"), False),
        (_principal("mallory", provenance="dev_override"), False),
        (_principal(provenance="token_validated"), False),
    ],
    ids=["owner", "shared_development_identity", "visitor", "persona", "token_subject_anonymous"],
)


@_VICTIMS_RECORD
def test_general_chat_transcript_scopes_every_principal_but_the_shared_development_identity(
    monkeypatch, principal, served
) -> None:
    from mozaiksai.hosts.routers import sessions as sessions_router

    class _Persistence:
        async def fetch_general_chat_transcript(self, **query):
            return {"chat_id": query["general_chat_id"], "user_id": "victim", "messages": [{"content": "private"}]}

    monkeypatch.setattr(sessions_router, "persistence_manager", _Persistence())
    app = FastAPI()
    app.include_router(sessions_router.router)
    app.dependency_overrides[sessions_router.require_user_scope] = lambda: principal

    response = TestClient(app).get("/api/general_chats/transcript/app-1/chat-1")

    if served:
        assert response.status_code == 200
        assert response.json()["messages"] == [{"content": "private", "timestamp": None}]
    else:
        assert (response.status_code, response.json()) == (403, {"detail": "Forbidden"})


_ACTION_SERVED = (200, {"status": "success", "result": {"applied": True}})
_CHAT_NOT_FOUND = (404, {"detail": "Chat not found"})


def _component_actions(monkeypatch, principal: UserPrincipal) -> tuple[Any, list[str]]:
    """Post component actions as ``principal``: victim owns chat-1 and mallory chat-2, both in app-1."""
    from mozaiksai.hosts import runtime as runtime_app

    chats = (
        {"_id": "chat-1", "user_id": "victim", "app_id": "app-1"},
        {"_id": "chat-2", "user_id": "mallory", "app_id": "app-1"},
    )
    applied: list[str] = []

    class _Chats:
        async def find_one(self, query, projection=None):
            # A record matches only when it equals every field the query names.
            return next((dict(chat) for chat in chats if all(chat.get(k) == v for k, v in query.items())), None)

    async def chat_coll():
        return _Chats()

    class _Transport:
        async def process_component_action(self, **action):
            applied.append(f"{action['app_id']}/{action['chat_id']}")
            return {"applied": True}

    monkeypatch.setattr(runtime_app, "_chat_coll", chat_coll)
    monkeypatch.setattr(runtime_app, "simple_transport", _Transport())
    monkeypatch.setitem(runtime_app.app.dependency_overrides, runtime_app.require_user_scope, lambda: principal)
    client = TestClient(runtime_app.app, raise_server_exceptions=False, base_url=HERE)

    def act(app_id: str, chat_id: str) -> tuple[int, Any]:
        response = client.post(
            f"/chat/{app_id}/{chat_id}/component_action", json={"component_id": "approval", "action_type": "approve"}
        )
        return response.status_code, response.json()

    return act, applied


@_VICTIMS_RECORD
def test_component_action_scopes_every_principal_but_the_shared_development_identity(
    monkeypatch, principal, served
) -> None:
    act, applied = _component_actions(monkeypatch, principal)

    if served:
        assert act("app-1", "chat-1") == _ACTION_SERVED
        assert applied == ["app-1/chat-1"]
    else:
        assert act("app-1", "chat-1") == _CHAT_NOT_FOUND
        assert applied == []


def test_component_action_ownership_names_the_chat_and_its_app(monkeypatch) -> None:
    act, applied = _component_actions(monkeypatch, _principal("victim", provenance="token_validated"))

    assert act("app-1", "chat-1") == _ACTION_SERVED
    assert act("app-2", "chat-1") == _CHAT_NOT_FOUND  # the owner's chat, under another app
    assert act("app-1", "chat-2") == _CHAT_NOT_FOUND  # another owner's chat in the same app
    assert applied == ["app-1/chat-1"]


def _mint_local_development_authority():
    from mozaiksai.core.runtime.composition.module_authority import ModuleDispatchAuthority

    return ModuleDispatchAuthority(kind="local_development", permission_mode="trusted_bypass", reason="test")


@pytest.mark.parametrize(
    ("environ", "allowed"),
    [
        ({"AUTH_ENABLED": "false"}, True),
        ({"AUTH_ENABLED": "false", "AUTH_ANON_ACCESS": "open"}, True),
        ({"AUTH_ANON_ACCESS": "open"}, True),
        ({"AUTH_ENABLED": "false", "AUTH_ANON_ACCESS": "public"}, False),
        ({"AUTH_ANON_ACCESS": "public"}, False),
        ({}, False),
        ({"ENV": "development"}, False),
        (_JWT, False),
        ({"ENV": "production", "AUTH_ENABLED": "false"}, False),
        ({"AUTH_ENABLED": "false", "AUTH_ANON_ACCESS": "bogus"}, False),
    ],
    ids=["local", "open", "open_alone", "public", "public_alone", "demo", "demo_env_development",
         "auth_on", "production_off", "invalid"],
)
def test_local_development_dispatch_authority_needs_a_development_posture(monkeypatch, environ, allowed) -> None:
    _auth(monkeypatch, **environ)

    if allowed:
        assert _mint_local_development_authority().permission_mode == "trusted_bypass"
    else:
        with pytest.raises(ValueError, match="local_development dispatch authority is not available"):
            _mint_local_development_authority()


def test_local_development_dispatch_authority_follows_a_replaced_auth_mode_predicate(monkeypatch) -> None:
    """A host (mozaiks-app's dispatch-authority test) that replaces
    registry.is_auth_enabled still decides "auth is on"."""
    _off(monkeypatch, "open", ENV="development", ENVIRONMENT="development")
    assert _mint_local_development_authority().permission_mode == "trusted_bypass"

    monkeypatch.setattr("mozaiksai.core.auth.adapters.registry.is_auth_enabled", lambda: True)
    with pytest.raises(ValueError, match="local_development"):
        _mint_local_development_authority()


def test_local_development_dispatch_authority_is_refused_in_production_before_resolution(monkeypatch) -> None:
    """A deployed environment answers first: no AuthError from resolving a no-auth configuration there."""
    _off(monkeypatch, ENV="production", ENVIRONMENT="production")
    with pytest.raises(AuthError):
        auth_registry.resolve_auth_config()

    with pytest.raises(ValueError, match="local_development"):
        _mint_local_development_authority()


class _Orders:
    async def authority(self, ctx):
        authority = ctx.dispatch_authority
        return {
            "kind": authority.kind,
            "mode": authority.permission_mode,
            "permissions": list(authority.permissions),
            "user_id": ctx.user_id,
        }

    async def manage(self, ctx):
        return {"managed": True}


class _NoPlanEntitlements:
    """An entitlement port with no active plan: every gated capability is denied."""

    async def check(self, capability_id, *, app_id, user_id=None, tenant_id=None, workspace_id=None):
        from mozaiksai.core.ports.entitlement import EntitlementResult

        return EntitlementResult(granted=False, reason="no_grant")


def _module_client(
    peer: tuple[str, int] | None,
    *,
    base_url: str = HERE,
    entitlements: Any = None,
    persistence_principal: Any = None,
) -> TestClient:
    from mozaiksai.core.runtime.composition.executor_registry import ExecutorRegistry
    from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor
    from mozaiksai.hosts.routers import modules as module_router

    executor = ModuleExecutor(entitlement_checker=entitlements or _NoPlanEntitlements())
    executor.register(
        "orders",
        _Orders(),
        action_method_map={
            "authority": "authority", "manage": "manage", "catalog": "authority", "premium": "authority",
        },
        action_permissions={"manage": ["orders.manage"]},
        action_entitlements={"premium": "orders.premium"},
    )
    registry = ExecutorRegistry()
    registry.register(executor)
    app = FastAPI()
    app.state.loaded_app_id = "orders-app"
    app.state.executor_registry = registry
    app.state.module_action_surfaces = {
        "orders": {"authority": None, "manage": None, "catalog": "public", "premium": None}
    }
    app.state.failed_module_names = []
    app.include_router(module_router.router)
    app.dependency_overrides[module_router.module_dispatch_environment] = lambda: module_router.ModuleDispatchEnvironment(
        authentication_enabled=auth_registry.is_auth_enabled(),
        platform_hooks=module_router.get_platform_hooks(),
        record_invocation=lambda **_: None,
        persistence_principal=persistence_principal or (lambda principal: None),
    )
    if peer is None:
        return TestClient(app, raise_server_exceptions=False, base_url=base_url)
    return TestClient(app, raise_server_exceptions=False, client=peer, base_url=base_url)


def test_module_dispatch_trusts_only_development_access(monkeypatch) -> None:
    _off(monkeypatch)
    local = _module_client(LOCAL)
    assert local.post("/api/modules/orders/authority", json={}).json() == {
        "kind": "local_development", "mode": "trusted_bypass", "permissions": [], "user_id": "anonymous",
    }
    assert local.post("/api/modules/orders/manage", json={}).status_code == 200
    assert local.post(
        "/api/modules/orders/authority", json={"context": {"user_id": "victim"}}
    ).json()["user_id"] == "victim"

    _off(monkeypatch, "open")
    assert _module_client(LAN).post("/api/modules/orders/authority", json={}).json()["mode"] == "trusted_bypass"


@pytest.mark.parametrize(
    ("environ", "peer", "status", "detail_start"),
    [
        ({"AUTH_ENABLED": "false"}, BRIDGE, 403, "Authentication is off and AUTH_ANON_ACCESS is local"),
        ({"AUTH_ENABLED": "false"}, None, 403, "Authentication is off and AUTH_ANON_ACCESS is local"),
        ({}, LOCAL, 401, "Authentication is not configured"),
    ],
    ids=["local_posture_bridge", "local_posture_testclient", "demo"],
)
def test_module_dispatch_treats_a_refused_request_like_one_without_credentials(
    monkeypatch, environ, peer, status, detail_start
) -> None:
    """optional_user gives None; a non-public action then needs a principal
    whatever the auth mode, and a public action runs enforced, never trusted."""
    _auth(monkeypatch, **environ)
    client = _module_client(peer)

    for action in ("authority", "manage"):
        refused = client.post(f"/api/modules/orders/{action}", json={})
        assert refused.status_code == status
        assert refused.json()["detail"].startswith(detail_start)
    assert client.get("/api/modules/orders/authority").status_code == status

    public = client.post("/api/modules/orders/catalog", json={})
    assert public.status_code == 200
    assert public.json() == {"kind": "public_http", "mode": "enforce", "permissions": [], "user_id": None}


def test_module_dispatch_without_a_principal_is_unchanged_with_authentication_on(monkeypatch) -> None:
    _auth(monkeypatch, **_JWT)
    client = _module_client(LAN)

    refused = client.post("/api/modules/orders/authority", json={})
    assert (refused.status_code, refused.json()["detail"]) == (401, "Missing authorization token")
    assert client.post("/api/modules/orders/catalog", json={}).json()["kind"] == "public_http"


def test_module_dispatch_enforces_a_public_visitor(monkeypatch) -> None:
    _off(monkeypatch, "public")
    visitor = _module_client(LAN)

    assert visitor.post("/api/modules/orders/authority", json={}).json() == {
        "kind": "public_http", "mode": "enforce", "permissions": ["access_as_user"], "user_id": "anonymous",
    }
    denied = visitor.post("/api/modules/orders/manage", json={})
    assert denied.status_code == 403
    assert denied.json()["detail"]["error_code"] == "PERMISSION_DENIED"
    forged = visitor.post("/api/modules/orders/authority", json={"context": {"user_id": "victim"}})
    assert forged.status_code == 403
    assert visitor.get("/api/modules/orders/authority?user_id=victim").status_code == 403


def test_module_dispatch_checks_entitlement_gates_for_everyone_but_development_access(monkeypatch) -> None:
    _off(monkeypatch, "public")
    gated = _module_client(LAN).post("/api/modules/orders/premium", json={})
    assert gated.status_code == 402
    assert gated.json()["detail"]["error_code"] == "ENTITLEMENT_REQUIRED"

    _off(monkeypatch)
    assert _module_client(LOCAL).post("/api/modules/orders/premium", json={}).json()["mode"] == "trusted_bypass"


class _RecordingEntitlements(_NoPlanEntitlements):
    def __init__(self) -> None:
        self.checked: list[dict[str, Any]] = []

    async def check(self, capability_id, *, app_id, user_id=None, tenant_id=None, workspace_id=None):
        self.checked.append({"user_id": user_id, "tenant_id": tenant_id, "workspace_id": workspace_id})
        return await super().check(capability_id, app_id=app_id)


def test_a_visitor_entitlement_gate_is_checked_against_the_shared_visitor_identity(monkeypatch) -> None:
    """Visitors share one identity, and their gates are checked against it: a
    tenant or workspace named in the request never selects the plan."""
    from mozaiksai.core.runtime.persistence.adapter import PersistencePrincipal

    _off(monkeypatch, "public")
    entitlements = _RecordingEntitlements()
    visitor = _module_client(
        LAN, entitlements=entitlements, persistence_principal=PersistencePrincipal.from_authenticated_user
    )

    gated = visitor.post(
        "/api/modules/orders/premium?tenant_id=paying-tenant&workspace_id=paying-workspace",
        json={"context": {"tenant_id": "paying-tenant", "workspace_id": "paying-workspace"}},
    )

    assert gated.status_code == 402
    assert entitlements.checked == [{"user_id": "anonymous", "tenant_id": None, "workspace_id": "development"}]


def test_module_dispatch_trusts_only_a_minted_principal(monkeypatch) -> None:
    """Trusted dispatch needs a UserPrincipal minted with development access. An
    object that merely claims the provenance (a duck-typed principal from a host
    hook or a dependency override) is enforced like any other caller."""
    from mozaiksai.hosts.routers import modules as module_router

    _off(monkeypatch, "open")
    client = _module_client(LAN)
    claimant = SimpleNamespace(
        user_id="anonymous", app_id=None, tenant_id=None, workspace_id=None,
        roles=["admin"], scopes=["access_as_user"],
        auth_provenance="local_development", has_local_development_access=True,
    )
    client.app.dependency_overrides[module_router.optional_user] = lambda: claimant

    assert client.post("/api/modules/orders/authority", json={}).json() == {
        "kind": "public_http", "mode": "enforce", "permissions": ["access_as_user"], "user_id": "anonymous",
    }
    assert client.post("/api/modules/orders/manage", json={}).status_code == 403
    assert client.post("/api/modules/orders/premium", json={}).status_code == 402


def test_privilege_is_bound_when_the_principal_is_minted(monkeypatch) -> None:
    """Changing the environment later never upgrades an existing principal."""
    from mozaiksai.hosts.routers.billing import _authorize_fulfillment

    minted: dict[str, UserPrincipal] = {}
    app = FastAPI()

    @app.get("/mint")
    async def mint(principal: UserPrincipal = Depends(require_user)):
        minted["principal"] = principal
        return {}

    _off(monkeypatch, "public")
    assert TestClient(app, client=LAN, base_url=HERE).get("/mint").status_code == 200
    visitor = minted["principal"]

    _off(monkeypatch, "open", AUTH_ANON_ROLES="admin,user")
    assert (visitor.auth_provenance, visitor.roles) == ("anonymous", [])
    assert not visitor.has_local_development_access
    assert not is_shared_development_identity(visitor)
    with pytest.raises(HTTPException) as refused:
        _authorize_fulfillment(SimpleNamespace(headers={}), visitor)
    assert refused.value.status_code == 403
    with pytest.raises(HTTPException) as named:
        resolve_scope_from_principal(visitor, user_id="victim")
    assert named.value.status_code == 403


def test_persistence_ownership_requires_an_explicit_disable(monkeypatch) -> None:
    from mozaiksai.core.runtime.persistence.adapter import PersistencePrincipal

    _off(monkeypatch, "public")
    visitor = PersistencePrincipal.from_authenticated_user(_principal())
    assert (visitor.user_id, visitor.workspace_id, visitor.source) == ("anonymous", "development", "development")
    ws_visitor = PersistencePrincipal.from_websocket_user(
        WebSocketUser(user_id="anonymous", email=None, name=None, roles=[], scopes=[], raw_claims={}, provider="none")
    )
    assert ws_visitor.user_id == "anonymous"

    _auth(monkeypatch, ENV="development")
    assert PersistencePrincipal.from_authenticated_user(_principal(provenance="local_development")) is None


@pytest.mark.asyncio
async def test_ui_tool_owner_waiver_needs_the_shared_development_identity() -> None:
    from tests.test_ui_response_ownership import _Transport

    def transport() -> _Transport:
        instance = _Transport()
        session = {"_id": "chat-owner", "app_id": "app-owner", "user_id": "owner"}

        async def find_one(*_args, **_kwargs):
            return session

        collection = SimpleNamespace(find_one=find_one)

        async def coll():
            return collection

        instance._get_or_create_persistence_manager = lambda: SimpleNamespace(_coll=coll)
        instance._ui_tool_metadata["evt-owned"] = {"chat_id": "chat-owner", "tool_name": "Card", "display": "artifact"}
        return instance

    async def answers(principal) -> bool:
        return await transport().submit_tool_call_response_for_user(
            "evt-owned", {"action": "approve"}, principal=principal
        )

    assert await answers(_principal(provenance="local_development")) is True
    assert await answers(_principal(provenance="anonymous")) is False
    assert await answers(_principal(provenance="token_validated")) is False
    assert await answers(
        WebSocketUser(
            user_id="anonymous", email=None, name=None, roles=[], scopes=[], raw_claims={},
            provider="none", auth_provenance="local_development",
        )
    ) is False


@pytest.mark.asyncio
async def test_workflow_tool_permissions_follow_the_socket_principal(monkeypatch) -> None:
    from mozaiksai.core.workflow.module_tools import session_permissions

    _off(monkeypatch)

    def socket_user(provenance: str, scopes: list[str]) -> WebSocketUser:
        return WebSocketUser(
            user_id="anonymous", email=None, name=None, roles=[], scopes=scopes, raw_claims={},
            provider="none", auth_provenance=provenance,
        )

    assert _DEV_SCOPE in await session_permissions(socket_user("local_development", ["access_as_user"]))
    assert await session_permissions(socket_user("anonymous", ["access_as_user"])) == ["access_as_user"]
    assert await session_permissions(socket_user("token_validated", ["orders.read"])) == ["orders.read"]


def test_billing_fulfillment_local_branch_needs_development_access(monkeypatch) -> None:
    from mozaiksai.hosts.routers.billing import _authorize_fulfillment

    request = SimpleNamespace(headers={})
    _off(monkeypatch)
    assert _authorize_fulfillment(request, _principal(provenance="local_development")) == "anonymous"
    assert _authorize_fulfillment(request, _principal("mallory", provenance="dev_override")) == "mallory"
    for principal in (_principal(provenance="anonymous"), None):
        with pytest.raises(HTTPException) as refused:
            _authorize_fulfillment(request, principal)
        assert refused.value.status_code == 403

    _auth(monkeypatch, ENV="development")  # demo mode is never an explicit disable
    with pytest.raises(HTTPException):
        _authorize_fulfillment(request, _principal(provenance="local_development"))


@pytest.mark.asyncio
async def test_shell_projects_a_development_identity_only_to_a_client_with_development_access(monkeypatch) -> None:
    from mozaiksai.core.runtime.app.auth_contract import build_app_auth_projection

    async def runtime(client_scope=None) -> dict[str, Any]:
        return (await build_app_auth_projection(None, client_scope=client_scope))["runtime"]

    _off(monkeypatch, AUTH_ANON_ROLES="admin,user")
    here = await runtime(_scope(LOCAL, ("Host", "localhost:8000")))
    assert here["local_development"] is True and here["user"]["roles"] == ["admin", "user"]
    for elsewhere in (_scope(LAN), _scope(LOCAL, ("X-Forwarded-For", "192.168.1.20"))):
        assert (await runtime(elsewhere))["local_development"] is False
        assert (await runtime(elsewhere))["user"] is None
    assert (await runtime())["local_development"] is True  # in-process callers without a request

    _off(monkeypatch, "open", AUTH_ANON_ROLES="admin")
    assert (await runtime(_scope(LAN)))["local_development"] is True

    _off(monkeypatch, "public")
    public = await runtime(_scope(LOCAL))
    assert (public["local_development"], public["user"]) == (False, None)


def test_shell_config_endpoint_answers_for_the_requesting_client(monkeypatch) -> None:
    from mozaiksai.hosts import platform as platform_app

    _off(monkeypatch, AUTH_ANON_ROLES="admin,user")

    def runtime(peer) -> dict[str, Any]:
        client = TestClient(platform_app.app, raise_server_exceptions=False, client=peer, base_url=HERE)
        response = client.get("/api/shell-config")
        assert response.status_code == 200
        return response.json()["auth"]["runtime"]

    assert runtime(LOCAL)["local_development"] is True
    assert (runtime(LAN)["local_development"], runtime(LAN)["user"]) == (False, None)


# --- Startup -----------------------------------------------------------------------


class _PingClient:
    class _Admin:
        async def command(self, cmd: str):
            return {"ok": 1}

    admin = _Admin()


@pytest.fixture
def _startup_env(monkeypatch, tmp_path):
    policy = tmp_path / "secrets.yaml"
    policy.write_text("version: 1\nprovider: {type: env}\nsecrets: []\n")
    monkeypatch.setenv("MOZAIKS_SECRETS_CONFIG_PATH", str(policy))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("MONGO_URI", "mongodb://127.0.0.1:27017")
    monkeypatch.setenv("INTERNAL_API_KEY", "test-startup-api-key-long-enough")
    for name in ("MOZAIKS_STARTUP_CHECKS", "MOZAIKS_WORKFLOWS_PATH", "ENVIRONMENT"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [None, "warn", "strict"], ids=["checks_unset", "checks_warn", "checks_strict"])
@pytest.mark.parametrize("environ", [{}, {"ENV": "development"}, {"KEYCLOAK_URL": "https://idp.example.invalid"}])
async def test_startup_refuses_when_authentication_is_not_configured(monkeypatch, _startup_env, environ, mode) -> None:
    """Every MOZAIKS_STARTUP_CHECKS mode: the refusal is not a check that warn mode downgrades."""
    from mozaiksai.core.startup.validation import StartupConfigError, run_startup_checks

    _auth(monkeypatch, **environ)
    if mode is not None:
        monkeypatch.setenv("MOZAIKS_STARTUP_CHECKS", mode)

    with pytest.raises(StartupConfigError) as refused:
        await run_startup_checks(_mongo_client=_PingClient())
    assert str(refused.value) == NOT_CONFIGURED_MESSAGE
    assert "AUTH_ANON_ACCESS=open" in str(refused.value)  # the container hint


@pytest.mark.asyncio
async def test_startup_announces_the_posture_and_warns_for_open(monkeypatch, _startup_env, caplog) -> None:
    from mozaiksai.core.startup.validation import run_startup_checks

    _off(monkeypatch, "open")
    with caplog.at_level(logging.INFO, logger="mozaiksai.startup.validation"):
        await run_startup_checks(_mongo_client=_PingClient())

    messages = [record.getMessage() for record in caplog.records]
    assert "STARTUP_CHECK_OK: authentication is off; anonymous access is open" in messages
    assert any(
        record.levelno == logging.WARNING and "AUTH_ANON_ACCESS=open" in record.getMessage()
        for record in caplog.records
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("environ", "ignored"),
    [
        ({"AUTH_ENABLED": "false", "SUPABASE_URL": "https://project.supabase.invalid"}, "SUPABASE_URL"),
        ({"AUTH_ANON_ACCESS": "public", "KEYCLOAK_URL": "https://idp.example.invalid"}, "KEYCLOAK_URL"),
    ],
    ids=["hard_off", "incomplete_provider"],
)
async def test_startup_warns_about_provider_settings_it_ignores(monkeypatch, _startup_env, caplog, environ, ignored) -> None:
    from mozaiksai.core.startup.validation import run_startup_checks

    _auth(monkeypatch, **environ)
    with caplog.at_level(logging.WARNING, logger="mozaiksai.startup.validation"):
        await run_startup_checks(_mongo_client=_PingClient())

    assert any(
        record.levelno == logging.WARNING
        and f"identity provider settings in the environment are ignored: {ignored}." in record.getMessage()
        for record in caplog.records
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "environ",
    [{"AUTH_ENABLED": "false"}, {"AUTH_ENABLED": "false", "AUTH_ANON_ACCESS": "public"}, {"AUTH_ANON_ACCESS": "public"}],
)
async def test_startup_accepts_an_explicit_posture(monkeypatch, _startup_env, caplog, environ) -> None:
    from mozaiksai.core.startup.validation import run_startup_checks

    _auth(monkeypatch, **environ)

    with caplog.at_level(logging.WARNING, logger="mozaiksai.startup.validation"):
        assert await run_startup_checks(_mongo_client=_PingClient()) == []
    assert not any("are ignored" in record.getMessage() for record in caplog.records)


# --- Studio ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("environ", "message"),
    [
        ({}, MANAGEMENT_NOT_CONFIGURED_MESSAGE),
        ({"AUTH_ENABLED": "false", "AUTH_ANON_ACCESS": "public"}, STUDIO_PUBLIC_MESSAGE),
        ({"AUTH_ANON_ACCESS": "public"}, STUDIO_PUBLIC_MESSAGE),
    ],
    ids=["not_configured", "public", "public_alone"],
)
def test_studio_refuses_a_posture_it_cannot_serve(monkeypatch, environ, message) -> None:
    from mozaiksai.core.startup.validation import (
        StartupConfigError,
        require_management_auth_posture,
    )

    _auth(monkeypatch, **environ)

    with pytest.raises(StartupConfigError) as refused:
        require_management_auth_posture()
    assert str(refused.value) == message
    assert "AUTH_ANON_ACCESS=public" not in MANAGEMENT_NOT_CONFIGURED_MESSAGE


@pytest.mark.parametrize(
    "environ",
    [{"AUTH_ENABLED": "false"}, {"AUTH_ANON_ACCESS": "open"}, _JWT, {"AUTH_ENABLED": "false", "AUTH_ANON_ACCESS": "x"}],
    ids=["local", "open", "auth_on", "invalid_reported_by_runtime_checks"],
)
def test_studio_accepts_every_other_posture(monkeypatch, environ) -> None:
    from mozaiksai.core.startup.validation import require_management_auth_posture

    _auth(monkeypatch, **environ)

    assert require_management_auth_posture() is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("environ", "message"),
    [({}, MANAGEMENT_NOT_CONFIGURED_MESSAGE), ({"AUTH_ANON_ACCESS": "public"}, STUDIO_PUBLIC_MESSAGE)],
    ids=["not_configured", "public"],
)
async def test_studio_refuses_before_any_other_startup_work(monkeypatch, environ, message) -> None:
    """Studio's own message, not the runtime's generic one (which offers public)."""
    from mozaiksai.core.startup.validation import StartupConfigError
    from mozaiksai.hosts import studio

    _auth(monkeypatch, **environ)

    with pytest.raises(StartupConfigError) as refused:
        async with studio.app.router.lifespan_context(studio.app):
            pass
    assert str(refused.value) == message


#: Studio routes that serve a caller before sign-in and depend on no principal.
#: The shell configuration tells the browser how to sign in, and projects a
#: development identity only to a client with development access.
_STUDIO_ROUTES_WITHOUT_A_PRINCIPAL = {(("GET",), "/api/shell-config")}


def _defined_in_studio(endpoint: Any) -> bool:
    """True when a route endpoint is defined in ``mozaiksai/hosts/studio.py``.

    Matched by module name as well as by source file: a decorator that wraps an
    endpoint without ``functools.wraps`` hides the original from
    ``inspect.unwrap``, so the code object found is the wrapper's, in the
    decorator's file, while a hand-copied ``__module__`` still names Studio.
    """
    import inspect
    from pathlib import Path

    from mozaiksai.hosts import studio

    target = inspect.unwrap(endpoint)
    if getattr(target, "__module__", None) == studio.__name__:
        return True
    code = getattr(target, "__code__", None)
    return code is not None and Path(code.co_filename).resolve() == Path(studio.__file__).resolve()


def test_studio_route_match_survives_a_wrapper_without_functools_wraps() -> None:
    from mozaiksai.hosts import studio

    def wrap_by_hand(endpoint):
        async def wrapper(*args, **kwargs):
            return await endpoint(*args, **kwargs)

        wrapper.__module__ = endpoint.__module__
        wrapper.__name__ = endpoint.__name__
        return wrapper

    from pathlib import Path

    wrapped = wrap_by_hand(studio.get_workspace_apps)
    assert not hasattr(wrapped, "__wrapped__")
    # The source-file match alone would miss it: the code is this test's.
    assert Path(wrapped.__code__.co_filename).resolve() != Path(studio.__file__).resolve()
    assert _defined_in_studio(wrapped)
    assert _defined_in_studio(studio.get_workspace_apps)
    assert not _defined_in_studio(wrap_by_hand)


def test_every_studio_route_refuses_an_anonymous_visitor(monkeypatch) -> None:
    """Request-level refusal, for a Studio composed without its lifespan."""
    from fastapi.routing import APIRoute

    from mozaiksai.core.auth import optional_user, require_any_auth, require_user_scope
    from mozaiksai.hosts import studio

    principal_dependencies = {require_user, require_user_scope, require_any_auth, optional_user}
    defined: set[tuple[tuple[str, ...], str]] = set()
    guarded: set[tuple[tuple[str, ...], str]] = set()
    for route in studio.app.routes:
        if not isinstance(route, APIRoute):
            continue
        key = (tuple(sorted(route.methods)), route.path)
        calls = {dependency.call for dependency in route.dependant.dependencies}
        if studio.require_studio_user in calls:
            guarded.add(key)
        if not _defined_in_studio(route.endpoint):
            continue
        defined.add(key)
        assert not calls & principal_dependencies, f"{key}: depends on a principal without require_studio_user"
        if key in _STUDIO_ROUTES_WITHOUT_A_PRINCIPAL:
            assert not calls, f"{key}: listed as serving a caller before sign-in"
    assert _STUDIO_ROUTES_WITHOUT_A_PRINCIPAL <= defined
    assert guarded == defined - _STUDIO_ROUTES_WITHOUT_A_PRINCIPAL

    _off(monkeypatch, "public")
    visitor = TestClient(studio.app, raise_server_exceptions=False, client=LAN, base_url=HERE)
    for method, path in (
        ("GET", "/api/studio/apps"),
        ("POST", "/api/studio/apps"),
        ("GET", "/api/studio/build/history"),
        ("POST", "/api/studio/build/restore"),
    ):
        response = visitor.request(method, path, json={})
        assert (response.status_code, response.json()["detail"]) == (403, STUDIO_PUBLIC_MESSAGE)


def test_direct_studio_routes_classify_app_ownership_authority() -> None:
    """Every direct Studio route has a reviewed app-scope gate or exception."""
    import ast
    import inspect
    import textwrap

    from fastapi.routing import APIRoute

    from mozaiksai.hosts import studio

    def named_calls(endpoint: Any, *, awaited_only: bool) -> set[str]:
        source = textwrap.dedent(inspect.getsource(inspect.unwrap(endpoint)))
        names = set()
        for node in ast.walk(ast.parse(source)):
            if awaited_only:
                if not isinstance(node, ast.Await) or not isinstance(node.value, ast.Call):
                    continue
                function = node.value.func
            else:
                if not isinstance(node, ast.Call):
                    continue
                function = node.func
            if isinstance(function, ast.Name):
                names.add(function.id)
            elif isinstance(function, ast.Attribute):
                names.add(function.attr)
        return names

    owned_app_routes = {
        "GET /api/studio/dashboard",
        "GET /api/studio/integrations",
        "GET /api/studio/integrations/connectors",
        "POST /api/studio/integrations/connectors",
        "PATCH /api/studio/integrations/connectors/{service}",
        "POST /api/studio/integrations/connectors/{service}/health-check",
        "DELETE /api/studio/integrations/connectors/{service}",
        "GET /api/studio/apps/{app_id}/context",
        "POST /api/studio/apps/{app_id}/context/app-intelligence/index",
        "POST /api/studio/apps/{app_id}/context/source-import",
        "GET /api/studio/apps/{app_id}/context/app-intelligence/index/latest",
        "GET /api/studio/apps/{app_id}/context/app-intelligence/index/{job_id}",
        "POST /api/studio/apps/{app_id}/context/validation/run",
        "POST /api/studio/apps/{app_id}/context/refresh-plan",
        "POST /api/studio/apps/{app_id}/context/refresh-launch",
        "POST /api/studio/apps/{app_id}/context/refresh-complete",
        "POST /api/studio/apps/{app_id}/context/override",
    }
    existing_registry_gates = {
        "GET /api/studio/overview": "get_app_record",
        "GET /api/studio/build": "get_app_record",
        "GET /api/studio/analytics/apps/{app_id}": "_get_owned_app_record",
        "GET /api/studio/analytics/apps/{app_id}/metrics/{metric_id}": "_get_owned_app_record",
        "GET /api/studio/build/artifacts/{artifact_version_id}/download": "_resolve_studio_artifact_scope",
        "GET /api/studio/build/history": "_resolve_studio_artifact_scope",
        "GET /api/studio/build/artifacts/{artifact_version_id}/bundle": "_resolve_studio_artifact_scope",
        "GET /api/studio/build/artifacts/{artifact_version_id}/review": "_resolve_studio_artifact_scope",
        "POST /api/studio/build/artifacts/{artifact_version_id}/accept": "_resolve_studio_artifact_scope",
        "POST /api/studio/build/artifacts/{artifact_version_id}/reject": "_resolve_studio_artifact_scope",
        "POST /api/studio/build/artifacts/{artifact_version_id}/promote": "_resolve_studio_artifact_scope",
        "POST /api/studio/build/restore": "_resolve_studio_artifact_scope",
    }
    owner_scoped_registry_operations = {
        "GET /api/studio/apps": "list_apps",
        "POST /api/studio/apps": "create_app_record",
        "DELETE /api/studio/apps/{build_registry_id}": "delete_app",
        "GET /api/studio/analytics/portfolio": "list_apps",
        "PUT /api/studio/build": "ensure_status_for_app",
    }
    # Workflow triggers name the execution host. Generic launches do not use
    # the registry; registered build targets have separate owner checks.
    execution_host_routes = {
        "POST /api/workflows/trigger": "_resolve_studio_scope",
    }
    routes_without_app_scope = {"GET /api/shell-config"}

    direct_route_list = [
        route for route in studio.app.routes
        if isinstance(route, APIRoute) and _defined_in_studio(route.endpoint)
    ]
    direct_routes = {
        f"{','.join(sorted(route.methods))} {route.path}": route
        for route in direct_route_list
    }
    classified = (
        owned_app_routes
        | existing_registry_gates.keys()
        | owner_scoped_registry_operations.keys()
        | execution_host_routes.keys()
        | routes_without_app_scope
    )
    assert len(direct_route_list) == len(direct_routes) == 36
    assert direct_routes.keys() == classified
    assert len(owned_app_routes) == 17
    for key in owned_app_routes:
        assert "_resolve_owned_app_scope" in named_calls(direct_routes[key].endpoint, awaited_only=True), key
    for gates in (existing_registry_gates, owner_scoped_registry_operations):
        for key, expected_call in gates.items():
            assert expected_call in named_calls(direct_routes[key].endpoint, awaited_only=True), key
    for key, expected_call in execution_host_routes.items():
        assert expected_call in named_calls(direct_routes[key].endpoint, awaited_only=False), key


def test_studio_preview_routes_refuse_an_anonymous_visitor(monkeypatch) -> None:
    """Studio composes the preview sandbox router with a scope resolver that refuses visitors."""
    from mozaiksai.hosts import studio

    with pytest.raises(HTTPException) as refused:
        studio._resolve_studio_preview_scope(_principal(provenance="anonymous"))
    assert (refused.value.status_code, refused.value.detail) == (403, STUDIO_PUBLIC_MESSAGE)

    _off(monkeypatch, "public")
    visitor = TestClient(studio.app, raise_server_exceptions=False, client=LAN, base_url=HERE)
    response = visitor.get("/api/sandbox/sandbox-1/status")
    assert (response.status_code, response.json()["detail"]) == (403, STUDIO_PUBLIC_MESSAGE)
    recovered = visitor.get("/api/sandbox?build_registry_id=registry-one")
    assert (recovered.status_code, recovered.json()["detail"]) == (403, STUDIO_PUBLIC_MESSAGE)
    closed = _ws_refusal(visitor, "/ws/sandbox/sandbox-1")
    assert (closed.code, closed.reason) == (1008, "Sandbox not found")


# --- Fetch metadata on GET routes that change state (locality rule 5) ---------------

_CROSS_SITE = {"Sec-Fetch-Site": "cross-site"}
_PERSONA_ME = "/api/me?dev_user_id=x&dev_roles=admin"


def _platform_client(monkeypatch, peer: tuple[str, int] | None = LOCAL) -> TestClient:
    """The real platform app; the account profile write is recorded, not persisted."""
    from mozaiksai.hosts import platform as platform_app

    async def ensure_profile(principal, *, app_id, user_id):
        return {"user_id": user_id, "roles": list(principal.roles), "provenance": principal.auth_provenance}

    monkeypatch.setattr(platform_app, "_ensure_account_profile", ensure_profile)
    if peer is None:
        return TestClient(platform_app.app, raise_server_exceptions=False, base_url=HERE)
    return TestClient(platform_app.app, raise_server_exceptions=False, client=peer, base_url=HERE)


def test_local_posture_refuses_a_cross_site_get_without_an_origin(monkeypatch) -> None:
    """An image or script tag on any site the developer visits sends a GET with
    no Origin; GET /api/me takes a persona and writes the profile, and GET
    module dispatch runs the action."""
    _off(monkeypatch, AUTH_ANON_ROLES="admin,user")
    expected = local_only_message(LocalityRefusal("fetch-site", "'cross-site'"))

    me = _platform_client(monkeypatch).get(_PERSONA_ME, headers=_CROSS_SITE)
    assert (me.status_code, me.json()["detail"]) == (403, expected)
    dispatch = _module_client(LOCAL).get("/api/modules/orders/authority", headers=_CROSS_SITE)
    assert (dispatch.status_code, dispatch.json()["detail"]) == (403, expected)
    assert expected.endswith(
        "This request was sent by a page on another site (Sec-Fetch-Site: cross-site), which this "
        "machine does not serve. Open the app at http://localhost:<port> or http://127.0.0.1:<port>."
    )


@pytest.mark.parametrize(
    "headers",
    [
        {"Sec-Fetch-Site": "same-origin"},
        {"Sec-Fetch-Site": "same-site"},
        {"Sec-Fetch-Site": "none"},
        {},
        {"Sec-Fetch-Site": "cross-site", "Origin": "http://127.0.0.1:3000"},
    ],
    ids=["same_origin", "same_site", "none", "absent", "cross_site_with_local_origin"],
)
def test_local_posture_serves_a_get_from_this_machine(monkeypatch, headers) -> None:
    _off(monkeypatch, AUTH_ANON_ROLES="admin,user")

    me = _platform_client(monkeypatch).get(_PERSONA_ME, headers=headers)
    assert me.status_code == 200
    assert me.json() == {"user_id": "x", "roles": ["admin"], "provenance": "dev_override"}
    dispatch = _module_client(LOCAL).get("/api/modules/orders/authority", headers=headers)
    assert dispatch.status_code == 200
    assert (dispatch.json()["kind"], dispatch.json()["mode"]) == ("local_development", "trusted_bypass")


def test_websocket_local_posture_refuses_a_cross_site_handshake_without_an_origin(monkeypatch) -> None:
    _off(monkeypatch)

    for path in ("/ws/victim", "/ws"):
        closed = _ws_refusal(_client(LOCAL), path, headers=_CROSS_SITE)
        assert (closed.code, closed.reason) == (
            1008,
            "Authentication is off; development access is for this machine only (sent by another site)",
        )
    with_origin = {**_CROSS_SITE, "Origin": "http://localhost:3000"}
    assert _ws(_client(LOCAL), "/ws", headers=with_origin)["provenance"] == "local_development"


# --- Authentication on: every AUTH_ANON_* setting is ignored -------------------------


def _auth_on_observations(monkeypatch, **anonymous_settings: str) -> list[tuple[Any, ...]]:
    from mozaiksai.core.auth import require_role

    _auth(monkeypatch, **_JWT, **anonymous_settings)
    app = _app()

    @app.get("/role")
    async def role(principal: UserPrincipal = Depends(require_role("admin"))):
        return _describe(principal)

    persona = {
        "X-Mozaiks-Dev-User-Id": "mallory",
        "X-Mozaiks-Dev-Roles": "admin",
        "Cookie": "mozaiks_dev_user_id=mallory; mozaiks_dev_roles=admin",
    }
    observed: list[tuple[Any, ...]] = []
    for peer in (LOCAL, LAN, None):
        client = (
            TestClient(app, raise_server_exceptions=False, base_url=HERE)
            if peer is None
            else TestClient(app, raise_server_exceptions=False, client=peer, base_url=HERE)
        )
        for credentials in ({}, {"Authorization": "Bearer not-a-jwt"}, {"Authorization": "Basic not-a-credential"}):
            headers = {**persona, **credentials}
            for path in ("/me?dev_user_id=mallory&dev_roles=admin", "/optional?dev_user_id=mallory", "/role"):
                response = client.get(path, headers=headers)
                observed.append((peer, path, tuple(credentials), response.status_code, response.json()))
            for path in ("/ws/mallory", "/ws/anonymous", "/ws"):
                try:
                    observed.append((peer, path, tuple(credentials), "accepted", _ws(client, path, headers=headers)))
                except WebSocketDisconnect as closed:
                    observed.append((peer, path, tuple(credentials), closed.code, closed.reason))
    return observed


def test_anonymous_settings_change_nothing_while_authentication_is_on(monkeypatch) -> None:
    baseline = _auth_on_observations(monkeypatch)
    with_settings = _auth_on_observations(
        monkeypatch, AUTH_ANON_ACCESS="open", AUTH_ANON_ROLES="admin", AUTH_ANON_SCOPES="x"
    )

    assert with_settings == baseline
    outcomes = {(path, credentials): (status, body) for _peer, path, credentials, status, body in baseline}
    assert outcomes[("/me?dev_user_id=mallory&dev_roles=admin", ())] == (401, {"detail": "Missing authorization token"})
    assert outcomes[("/role", ())] == (401, {"detail": "Missing authorization token"})
    assert outcomes[("/optional?dev_user_id=mallory", ())] == (200, None)
    assert outcomes[("/me?dev_user_id=mallory&dev_roles=admin", ("Authorization",))][0] == 401
    assert all(status != "accepted" for _peer, _path, _credentials, status, _body in baseline)


# --- Logging --------------------------------------------------------------------------


def test_implicit_demo_resolution_logs_at_debug_only(caplog) -> None:
    """The CLI prints its refusal box right after resolution; a WARNING saying
    "defaulting to demo mode" just above it read as a contradiction."""
    with caplog.at_level(logging.DEBUG, logger=auth_registry.logger.name):
        config = auth_registry.resolve_auth_config(environ={})

    assert config.source == "demo_default"
    demo = [record for record in caplog.records if "implicit demo mode" in record.getMessage()]
    assert [(record.levelno, record.getMessage()) for record in demo] == [
        (logging.DEBUG, "No authentication is configured (implicit demo mode); hosts refuse to start in this mode.")
    ]
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]


def test_each_refusal_is_logged_once_with_its_rule(caplog) -> None:
    from mozaiksai.core.auth import anonymous_access

    with caplog.at_level(logging.WARNING, logger=anonymous_access.logger.name):
        _grant({}, LOCAL)
        _grant({"AUTH_ENABLED": "false"}, BRIDGE)
        _grant({"AUTH_ENABLED": "false"}, LOCAL, ("X-Forwarded-For", "10.0.0.9"))
        _grant({"AUTH_ENABLED": "false"}, LOCAL, ("Host", "rebind.example"))
        _grant({"AUTH_ENABLED": "false"}, LOCAL, ("Origin", "http://evil.example"))
        _grant({"AUTH_ENABLED": "false"}, LOCAL, ("Sec-Fetch-Site", "cross-site"))
        _grant({"AUTH_ENABLED": "false"}, LOCAL)  # served: nothing logged

    refusals = [record for record in caplog.records if "ANONYMOUS_ACCESS_REFUSED" in record.getMessage()]
    assert all(record.levelno == logging.WARNING for record in refusals)
    assert [record.getMessage() for record in refusals] == [
        "ANONYMOUS_ACCESS_REFUSED reason=not_configured client='127.0.0.1'",
        "ANONYMOUS_ACCESS_REFUSED reason=not_local rule=peer client='172.20.0.1' observed='172.20.0.1'",
        "ANONYMOUS_ACCESS_REFUSED reason=not_local rule=forwarded client='127.0.0.1' observed=x-forwarded-for",
        "ANONYMOUS_ACCESS_REFUSED reason=not_local rule=host client='127.0.0.1' observed='rebind.example'",
        "ANONYMOUS_ACCESS_REFUSED reason=not_local rule=origin client='127.0.0.1' observed='http://evil.example'",
        "ANONYMOUS_ACCESS_REFUSED reason=not_local rule=fetch-site client='127.0.0.1' observed='cross-site'",
    ]


def test_a_refusal_quotes_and_shortens_the_client_address(caplog) -> None:
    """Behind a proxy the server trusts, the server records the peer from the
    request's own forwarding header, so the peer is client-supplied text: the
    log line and the refusal show it cut to 80 characters and quoted, like
    every other client-supplied value."""
    from mozaiksai.core.auth import anonymous_access

    spaced = "10.9.8.7 rule=host client=127.0.0.1"
    long_peer = "10.9.8.7-" + "x" * 111  # 120 characters
    shortened = repr(long_peer[:77] + "...")

    with caplog.at_level(logging.WARNING, logger=anonymous_access.logger.name):
        _grant({}, (spaced, 0))
        refused = _grant({"AUTH_ENABLED": "false"}, (spaced, 0))
        refused_long = _grant({"AUTH_ENABLED": "false"}, (long_peer, 0))

    refusals = [record.getMessage() for record in caplog.records if "ANONYMOUS_ACCESS_REFUSED" in record.getMessage()]
    assert refusals == [
        f"ANONYMOUS_ACCESS_REFUSED reason=not_configured client={spaced!r}",
        f"ANONYMOUS_ACCESS_REFUSED reason=not_local rule=peer client={spaced!r} observed={spaced!r}",
        f"ANONYMOUS_ACCESS_REFUSED reason=not_local rule=peer client={shortened} observed={shortened}",
    ]
    assert len(shortened) == 82  # 80 characters and the quotes
    assert f"This request came from {spaced!r}, another machine." in refused.detail
    assert f"This request came from {shortened}, another machine." in refused_long.detail
    assert long_peer not in refused_long.detail


@pytest.mark.parametrize(
    ("environ", "peer"),
    [({"AUTH_ENABLED": "false"}, LAN), ({"ENV": "development"}, LOCAL)],
    ids=["local_posture_lan", "demo"],
)
def test_the_shell_projection_logs_no_refusal(monkeypatch, caplog, environ, peer) -> None:
    """The browser fetches /api/shell-config before anything else (log_refusal=False)."""
    from mozaiksai.core.auth import anonymous_access

    _auth(monkeypatch, **environ)
    with caplog.at_level(logging.DEBUG, logger=anonymous_access.logger.name):
        response = _platform_client(monkeypatch, peer).get("/api/shell-config")

    assert response.status_code == 200
    assert response.json()["auth"]["runtime"]["user"] is None
    assert not [record for record in caplog.records if "ANONYMOUS_ACCESS_REFUSED" in record.getMessage()]


# --- Module dispatch: a named user is bound to the caller ----------------------------


def test_module_dispatch_binds_a_named_user_to_the_caller(monkeypatch) -> None:
    def calls(client: TestClient) -> list[Any]:
        return [
            client.post("/api/modules/orders/authority", json={"context": {"user_id": "victim"}}),
            client.get("/api/modules/orders/authority?user_id=victim"),
            client.post("/api/modules/orders/authority?user_id=victim", json={}),
        ]

    _off(monkeypatch, "public")
    for response in calls(_module_client(LAN)):
        assert (response.status_code, response.json()["detail"]) == (
            403,
            "Token user_id does not match request user_id",
        )

    _off(monkeypatch)
    for response in calls(_module_client(BRIDGE)):  # refused: optional_user gave None
        assert response.status_code == 403
        assert response.json()["detail"] == local_only_message(LocalityRefusal("peer", "'172.20.0.1'"))

    _auth(monkeypatch, ENV="development")
    for response in calls(_module_client(LOCAL)):  # demo: refused, optional_user gave None
        assert (response.status_code, response.json()["detail"]) == (401, NOT_CONFIGURED_MESSAGE)

    _off(monkeypatch)
    by_context, by_query, by_post_query = calls(_module_client(LOCAL))
    assert (by_context.status_code, by_context.json()["user_id"]) == (200, "victim")
    # With development access the query string names nobody: the caller is the shared identity.
    assert (by_query.status_code, by_query.json()["user_id"]) == (200, "anonymous")
    assert (by_post_query.status_code, by_post_query.json()["user_id"]) == (200, "anonymous")


# --- Programmatic workflow trigger --------------------------------------------------


def _trigger_client(monkeypatch, peer: tuple[str, int] | None, *, principal: UserPrincipal | None = None) -> TestClient:
    from mozaiksai.core.workflow.workflow_manager import workflow_manager
    from mozaiksai.hosts import runtime as runtime_app

    # No workflow is registered: a request that passes every gate gets 404.
    monkeypatch.setattr(workflow_manager, "get_all_workflow_names", lambda: [])
    if principal is not None:
        monkeypatch.setitem(runtime_app.app.dependency_overrides, runtime_app.require_any_auth, lambda: principal)
    if peer is None:
        return TestClient(runtime_app.app, raise_server_exceptions=False, base_url=HERE)
    return TestClient(runtime_app.app, raise_server_exceptions=False, client=peer, base_url=HERE)


_TRIGGER = "/api/workflows/Generator/trigger"
_TRIGGER_REFUSED = (403, "Workflow trigger requires an internal API key or development access")
_PASSED_EVERY_GATE = (404, "Workflow 'Generator' not found")


def _trigger(client: TestClient, *, key: str | None = None, user_id: str = "anonymous") -> tuple[int, Any]:
    headers = {} if key is None else {"X-Internal-API-Key": key}
    response = client.post(_TRIGGER, json={"user_id": user_id}, headers=headers)
    return response.status_code, response.json()["detail"]


def test_workflow_trigger_needs_an_internal_key_or_development_access(monkeypatch) -> None:
    monkeypatch.delenv("INTERNAL_API_KEY", raising=False)

    _off(monkeypatch, "public")
    assert _trigger(_trigger_client(monkeypatch, LAN)) == _TRIGGER_REFUSED
    assert _trigger(_trigger_client(monkeypatch, LOCAL)) == _TRIGGER_REFUSED

    monkeypatch.setenv("INTERNAL_API_KEY", "trigger-key-long-enough-for-the-check")
    visitor = _trigger_client(monkeypatch, LAN)
    assert _trigger(visitor) == (401, "Invalid internal API key")
    assert _trigger(visitor, key="trigger-key-long-enough-for-the-check") == _PASSED_EVERY_GATE

    monkeypatch.delenv("INTERNAL_API_KEY")
    _off(monkeypatch)
    assert _trigger(_trigger_client(monkeypatch, LOCAL)) == _PASSED_EVERY_GATE
    assert _trigger(_trigger_client(monkeypatch, BRIDGE))[0] == 403  # refused before the route: not local
    _off(monkeypatch, "open")
    assert _trigger(_trigger_client(monkeypatch, LAN)) == _PASSED_EVERY_GATE


def test_workflow_trigger_is_unchanged_with_authentication_on(monkeypatch) -> None:
    monkeypatch.delenv("INTERNAL_API_KEY", raising=False)
    _auth(monkeypatch, **_JWT)

    assert _trigger(_trigger_client(monkeypatch, LAN)) == (401, "Missing authorization token")
    token_user = _principal("owner", provenance="token_validated")
    assert _trigger(_trigger_client(monkeypatch, LAN, principal=token_user), user_id="owner") == _PASSED_EVERY_GATE


# --- Admin gate hint --------------------------------------------------------------------


def test_admin_refusal_says_how_to_grant_admin_with_authentication_off(monkeypatch) -> None:
    hint = (
        "Admin access required. With authentication off, give the anonymous user the admin role: "
        "set AUTH_ANON_ROLES=admin,user."
    )

    _off(monkeypatch)  # development access, no AUTH_ANON_ROLES
    assert (_client(LOCAL).get("/admin").status_code, _client(LOCAL).get("/admin").json()["detail"]) == (403, hint)
    persona = _client(LOCAL).get("/admin", headers={"X-Mozaiks-Dev-User-Id": "mallory"})
    assert (persona.status_code, persona.json()["detail"]) == (403, hint)

    _off(monkeypatch, "public")
    visitor = _client(LAN).get("/admin")
    assert (visitor.status_code, visitor.json()["detail"]) == (403, "Admin access required")


@pytest.mark.asyncio
async def test_admin_refusal_of_a_token_user_is_unchanged(monkeypatch) -> None:
    from mozaiksai.core.admin import router as admin_router

    async def token_user(request, authorization):
        return _principal("owner", provenance="token_validated")

    monkeypatch.setattr(admin_router, "require_user", token_user)
    with pytest.raises(HTTPException) as refused:
        await admin_router._require_admin(SimpleNamespace(), None)
    assert (refused.value.status_code, refused.value.detail) == (403, "Admin access required")


# --- Public visitors and AUTH_ANON_SCOPES ------------------------------------------


def test_public_access_accepts_anonymous_scopes_and_grants_exactly_them(monkeypatch) -> None:
    """Each listed scope is granted to every visitor (exact permission match), so
    list only permissions meant for the public."""
    config = auth_registry.resolve_auth_config(
        environ={"AUTH_ANON_ACCESS": "public", "AUTH_ANON_SCOPES": "reports.read, reports.write"}
    )
    assert (config.anonymous_access, config.grants_development_access) == ("public", False)

    _off(monkeypatch, "public", AUTH_ANON_SCOPES="reports.read, reports.write")
    assert _client(LAN).get("/me").json()["scopes"] == ["reports.read", "reports.write"]
    assert _ws(_client(LAN), "/ws")["scopes"] == ["reports.read", "reports.write"]
    assert _module_client(LAN).post("/api/modules/orders/authority", json={}).json()["permissions"] == [
        "reports.read",
        "reports.write",
    ]
