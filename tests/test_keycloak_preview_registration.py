"""Offline checks for the host-only Keycloak preview registration boundary."""

from __future__ import annotations

import copy
import json
from urllib.parse import parse_qs
from uuid import uuid4

import httpx
import pytest

from mozaiksai.core.sandbox import keycloak_registration as registration

SECRET = "registrar-secret-never-forwarded"
TOKEN = "registrar-access-token-never-forwarded"


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for key in list(registration.os.environ):
        if key.startswith(("AUTH_", "KEYCLOAK_", "MOZAIKS_OIDC_", "MOZAIKS_KEYCLOAK_", "MOZAIKS_PREVIEW_ENV_")):
            monkeypatch.delenv(key)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_PROVIDER", "keycloak")
    monkeypatch.setenv("KEYCLOAK_URL", "http://127.0.0.1:8180")
    monkeypatch.setenv("KEYCLOAK_REALM", "studio")
    monkeypatch.setenv("KEYCLOAK_CLIENT_ID", "studio-api")


@pytest.fixture
def registrar(monkeypatch):
    monkeypatch.setenv(registration._CLIENT_ENV, "preview-registrar")
    monkeypatch.setenv(registration._SECRET_ENV, SECRET)
    return registration.resolve_keycloak_preview_registrar()


class KeycloakStub:
    def __init__(self):
        self.clients = {}
        self.requests = []
        self.replace_id = False
        self.keep_deleted = False
        self.extra_scope = None
        self.created = []

    def handle(self, request):
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/token"):
            assert parse_qs(request.content.decode()) == {
                "grant_type": ["client_credentials"], "client_id": ["preview-registrar"],
                "client_secret": [SECRET],
            }
            return httpx.Response(200, json={"access_token": TOKEN})
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        assert SECRET not in request.content.decode()
        root = "/admin/realms/studio/clients"
        assert path.startswith(root)
        suffix = path.removeprefix(root).strip("/")
        if not suffix and request.method == "GET":
            assert dict(request.url.params) == {
                "clientId": request.url.params["clientId"], "search": "false", "max": "2",
            }
            return httpx.Response(200, json=[
                item for item in self.clients.values() if item["clientId"] == request.url.params["clientId"]
            ])
        if not suffix and request.method == "POST":
            body = json.loads(request.content)
            if self.replace_id:
                body["id"] = str(uuid4())
            self.clients[body["id"]] = body
            self.created.append(copy.deepcopy(body))
            return httpx.Response(201)
        identifier, _, scope_kind = suffix.partition("/")
        if identifier not in self.clients:
            return httpx.Response(404)
        if scope_kind:
            names = ["basic", "profile", "email"] if scope_kind == "default-client-scopes" else []
            if self.extra_scope:
                names.append(self.extra_scope)
            return httpx.Response(200, json=[{"name": name} for name in names])
        if request.method == "GET":
            return httpx.Response(200, json=self.clients[identifier])
        if request.method == "PUT":
            self.clients[identifier] = json.loads(request.content)
            return httpx.Response(204)
        if request.method == "DELETE":
            if not self.keep_deleted:
                del self.clients[identifier]
            return httpx.Response(204)
        raise AssertionError(f"Unexpected method: {request.method}")


@pytest.fixture
def keycloak(monkeypatch):
    stub = KeycloakStub()
    original = httpx.AsyncClient

    def client(**kwargs):
        assert kwargs["follow_redirects"] is False
        assert kwargs["trust_env"] is False
        assert kwargs["timeout"].connect == 3
        return original(transport=httpx.MockTransport(stub.handle), **kwargs)

    monkeypatch.setattr(registration.httpx, "AsyncClient", client)
    return stub


def register_args(sandbox_id=None):
    return {
        "sandbox_id": sandbox_id or uuid4().hex, "target_app_id": "notes-preview",
        "preview_url": "http://127.0.0.1:34567", "callback_path": "/auth/callback",
    }


@pytest.mark.parametrize("replace_id", [False, True])
async def test_registration_exact_isolation_reuse_and_idempotent_cleanup(registrar, keycloak, replace_id):
    keycloak.replace_id = replace_id
    args = register_args()
    env = await registrar.register(**args)
    assert await registrar.register(**args) == env
    assert len(keycloak.created) == 1
    body = keycloak.created[0]
    assert body["redirectUris"] == ["http://127.0.0.1:34567/auth/callback"]
    assert body["webOrigins"] == ["http://127.0.0.1:34567"]
    assert body["attributes"]["pkce.code.challenge.method"] == "S256"
    assert body["defaultClientScopes"] == ["basic", "profile", "email"]
    assert body["optionalClientScopes"] == []
    for option in ("fullScopeAllowed", "implicitFlowEnabled", "directAccessGrantsEnabled", "serviceAccountsEnabled"):
        assert body[option] is False
    assert body["publicClient"] is True
    audience, app = body["protocolMappers"]
    assert audience["config"] == {
        "included.custom.audience": env["AUTH_AUDIENCE"],
        "access.token.claim": "true", "id.token.claim": "false",
    }
    assert app["config"]["claim.value"] == "notes-preview"
    assert env["AUTH_AUDIENCE"] != env["VITE_OIDC_CLIENT_ID"] != "studio-api"
    assert env["AUTH_AUDIENCE_MODE"] == "single"
    assert env["AUTH_ISSUER"] == env["VITE_OIDC_AUTHORITY"] == registrar.issuer
    assert env["AUTH_ACCESS_TOKEN_TYPE_CLAIM"] == "typ"
    assert env["AUTH_ACCESS_TOKEN_TYPE_VALUE"] == "Bearer"
    assert env["VITE_OIDC_SCOPE"] == "openid profile email"
    assert env["VITE_OIDC_DISCOVERY_URL"] == env["MOZAIKS_OIDC_DISCOVERY_URL"] == ""
    assert SECRET not in repr(env) and TOKEN not in repr(env)
    await registrar.remove(sandbox_id=args["sandbox_id"])
    await registrar.remove(sandbox_id=args["sandbox_id"])
    assert keycloak.clients == {}


async def test_distinct_previews_do_not_share_client_or_audience(registrar, keycloak):
    first = await registrar.register(**register_args())
    second = await registrar.register(**register_args())
    assert first["AUTH_AUDIENCE"] != second["AUTH_AUDIENCE"]
    assert first["VITE_OIDC_CLIENT_ID"] != second["VITE_OIDC_CLIENT_ID"]


@pytest.mark.parametrize("field", ["mozaiks.preview.sandbox_id", "mozaiks.preview.registrar"])
async def test_foreign_client_is_never_updated_or_deleted(registrar, keycloak, field):
    args = register_args()
    await registrar.register(**args)
    body = next(iter(keycloak.clients.values()))
    body["attributes"][field] = "foreign"
    keycloak.requests.clear()
    with pytest.raises(registration.KeycloakPreviewRegistrationError, match="ownership"):
        await registrar.register(**args)
    with pytest.raises(registration.KeycloakPreviewRegistrationError, match="ownership"):
        await registrar.remove(sandbox_id=args["sandbox_id"])
    assert all(request.method not in {"PUT", "DELETE"} for request in keycloak.requests)


async def test_existing_preview_cannot_change_target(registrar, keycloak):
    args = register_args()
    await registrar.register(**args)
    args["target_app_id"] = "other-app"
    with pytest.raises(registration.KeycloakPreviewRegistrationError, match="target identity"):
        await registrar.register(**args)


async def test_unconfirmed_cleanup_is_an_error(registrar, keycloak):
    args = register_args()
    await registrar.register(**args)
    keycloak.keep_deleted = True
    with pytest.raises(registration.KeycloakPreviewRegistrationError, match="cleanup was not confirmed"):
        await registrar.remove(sandbox_id=args["sandbox_id"])


async def test_inherited_extra_scope_fails_before_runtime_configuration(registrar, keycloak):
    keycloak.extra_scope = "roles"
    with pytest.raises(registration.KeycloakPreviewRegistrationError, match="unexpected client scopes"):
        await registrar.register(**register_args())


@pytest.mark.parametrize("changes", [
    {"sandbox_id": "untrusted-id"},
    {"target_app_id": "../app"},
    {"preview_url": "https://preview.example.com"},
    {"preview_url": "http://127.0.0.1:34567/evil"},
    {"preview_url": "http://user:password@127.0.0.1:34567"},
    {"preview_url": "http://127.0.0.1:34567?next=evil"},
    {"callback_path": "//evil.example/callback"},
    {"callback_path": "/auth/*"},
    {"callback_path": "/auth/callback?next=elsewhere"},
    {"callback_path": "/auth/callback#fragment"},
    {"callback_path": "/auth/%2fcallback"},
    {"callback_path": "/auth/../callback"},
])
async def test_invalid_registration_stops_before_network(registrar, keycloak, changes):
    with pytest.raises(registration.KeycloakPreviewRegistrationError):
        await registrar.register(**{**register_args(), **changes})
    assert keycloak.requests == []


def test_resolver_requires_explicit_pair_and_native_studio_provider(monkeypatch):
    assert registration.resolve_keycloak_preview_registrar() is None
    monkeypatch.setenv(registration._CLIENT_ENV, "preview-registrar")
    with pytest.raises(registration.KeycloakPreviewRegistrationError, match="both"):
        registration.resolve_keycloak_preview_registrar()
    monkeypatch.setenv(registration._SECRET_ENV, SECRET)
    monkeypatch.setenv("AUTH_PROVIDER", "jwt")
    monkeypatch.setenv("AUTH_ISSUER", "https://issuer.example")
    monkeypatch.setenv("AUTH_JWKS_URL", "https://issuer.example/certs")
    monkeypatch.setenv("AUTH_AUDIENCE", "studio-api")
    with pytest.raises(registration.KeycloakPreviewRegistrationError, match="native Studio"):
        registration.resolve_keycloak_preview_registrar()


@pytest.mark.parametrize("endpoint", [
    "http://keycloak.example", "https://user:password@keycloak.example",
    "https://keycloak.example?query=secret", "https://keycloak.example/#fragment",
])
def test_registrar_rejects_unsafe_credential_endpoints(endpoint):
    with pytest.raises(registration.KeycloakPreviewRegistrationError):
        registration.KeycloakPreviewRegistrar(
            server_url=endpoint, realm="studio", client_id="registrar", client_secret=SECRET,
        )


def test_deployed_environment_rejects_plain_http(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    with pytest.raises(registration.KeycloakPreviewRegistrationError):
        registration.KeycloakPreviewRegistrar(
            server_url="http://localhost:8180", realm="studio", client_id="registrar", client_secret=SECRET,
        )


@pytest.mark.parametrize("host", ["host.docker.internal", "192.168.247.3", "172.18.0.4", "10.1.2.3"])
async def test_only_explicit_jwks_handle_reaches_sandbox(monkeypatch, keycloak, registrar, host):
    monkeypatch.setenv("MOZAIKS_PREVIEW_ENV_AUTH_JWKS_URL", f"http://{host}:8180/realms/studio/protocol/openid-connect/certs")
    configured = registration.resolve_keycloak_preview_registrar()
    env = await configured.register(**register_args())
    assert env["AUTH_JWKS_URL"].startswith(f"http://{host}:8180/")
    assert env["AUTH_ISSUER"] == registrar.issuer


@pytest.mark.parametrize("host", ["169.254.169.254", "8.8.8.8", "192.0.2.1", "0.0.0.0"])
def test_explicit_http_jwks_rejects_non_docker_addresses(monkeypatch, registrar, host):
    monkeypatch.setenv("MOZAIKS_PREVIEW_ENV_AUTH_JWKS_URL", f"http://{host}/certs")
    with pytest.raises(registration.KeycloakPreviewRegistrationError):
        registration.resolve_keycloak_preview_registrar()


def test_private_bridge_address_is_not_an_admin_credential_endpoint():
    with pytest.raises(registration.KeycloakPreviewRegistrationError):
        registration.KeycloakPreviewRegistrar(
            server_url="http://192.168.247.3:8080", realm="studio", client_id="registrar", client_secret=SECRET,
        )


@pytest.mark.parametrize("response", [
    httpx.Response(302, headers={"location": "https://evil.example"}),
    httpx.Response(403, text=SECRET),
    httpx.Response(200, content=b"x" * 131073),
    httpx.Response(200, json={"access_token": ""}),
])
async def test_provider_errors_are_bounded_and_redacted(registrar, monkeypatch, response):
    original = httpx.AsyncClient
    requests = []

    def handler(request):
        requests.append(request)
        return response

    monkeypatch.setattr(registration.httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    with pytest.raises(registration.KeycloakPreviewRegistrationError) as error:
        await registrar.register(**register_args())
    assert SECRET not in str(error.value) and TOKEN not in str(error.value)
    assert len(requests) == 1
