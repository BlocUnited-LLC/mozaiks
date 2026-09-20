"""Provider-neutral app authentication declarations and public shell projection."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Any, Literal, Self
from urllib.parse import unquote, urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from mozaiksai.core.auth.adapters.registry import get_auth_adapter, resolve_auth_config
from mozaiksai.core.runtime.app.paths import APP_AUTH_CONFIG_PATH

NonEmptyText = Annotated[str, Field(min_length=1, pattern=r"\S")]
AuthMode = Literal["brokered_oidc", "public_self_signup", "private_workspace", "enterprise_sso", "multi_provider"]
LoginMethodKind = Literal["oidc_redirect", "create_account", "enterprise_sso"]
APP_AUTH_COMPONENTS = frozenset({"LoginPage", "AuthCallbackPage"})


class _ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class AuthRoutes(_ContractModel):
    login: str
    callback: str
    logout: str
    post_login_default: str

    @field_validator("*")
    @classmethod
    def app_local_route(cls, value: str) -> str:
        decoded = unquote(value)
        if (
            not value.startswith("/")
            or not decoded.startswith("/")
            or decoded.startswith("//")
            or "\\" in decoded
            or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in decoded)
            or any(part in {".", ".."} for part in urlsplit(decoded).path.split("/"))
        ):
            raise ValueError("must be an app-local route")
        return value


class AuthFrontend(_ContractModel):
    adapter: Literal["oidc_pkce"]
    client_id_env: Literal["VITE_OIDC_CLIENT_ID"]
    authority_env: Literal["VITE_OIDC_AUTHORITY"]
    discovery_url_env: Literal["VITE_OIDC_DISCOVERY_URL"]
    redirect_uri_env: Literal["VITE_OIDC_REDIRECT_URI"]
    scope_env: Literal["VITE_OIDC_SCOPE"]
    default_scopes: list[NonEmptyText]

    @field_validator("default_scopes")
    @classmethod
    def oidc_scopes(cls, value: list[str]) -> list[str]:
        if not {"openid", "profile", "email"}.issubset(value):
            raise ValueError("must include openid, profile, and email")
        if len(value) != len(set(value)):
            raise ValueError("scopes must be unique")
        return value


class AuthRuntime(_ContractModel):
    provider_env: Literal["AUTH_PROVIDER"]
    enabled_env: Literal["AUTH_ENABLED"]
    authority_env: Literal["MOZAIKS_OIDC_AUTHORITY"]
    discovery_url_env: Literal["MOZAIKS_OIDC_DISCOVERY_URL"]
    issuer_env: Literal["AUTH_ISSUER"]
    jwks_url_env: Literal["AUTH_JWKS_URL"]


class IdentityProvider(_ContractModel):
    id: NonEmptyText
    label: NonEmptyText | None = None
    provider_role: Literal["upstream_oidc_provider"] = "upstream_oidc_provider"


class LoginMethod(_ContractModel):
    id: NonEmptyText
    kind: LoginMethodKind
    label: NonEmptyText
    primary: bool = False
    provider_id: NonEmptyText | None = None


class AuthCustomization(_ContractModel):
    # Visual intent only; executable pages still register through the normal UI seam.
    login_theme_source: str = "brand/theme_config.json"
    upstream_provider_setup: Literal["host_or_operator"] = "host_or_operator"

    @field_validator("login_theme_source")
    @classmethod
    def app_owned_visual_reference(cls, value: str) -> str:
        decoded = unquote(value)
        if (
            not decoded
            or decoded.startswith("/")
            or any(char in decoded for char in ("\\", ":", "?", "#"))
            or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in decoded)
            or any(part in {".", ".."} for part in decoded.split("/"))
        ):
            raise ValueError("must be an app-owned relative visual reference")
        return value


class AppAuthContract(_ContractModel):
    schema_version: Literal["mozaiks.auth.v1"]
    auth_required: Literal[True]
    strategy: Literal["oidc"]
    mode: AuthMode = "brokered_oidc"
    signup_enabled: bool = False
    routes: AuthRoutes
    frontend: AuthFrontend
    runtime: AuthRuntime
    identity_providers: list[IdentityProvider] = Field(default_factory=list)
    login_methods: list[LoginMethod] = Field(default_factory=list)
    customization: AuthCustomization = Field(default_factory=AuthCustomization)

    @field_validator("auth_required", mode="before")
    @classmethod
    def boolean_auth_intent(cls, value: object) -> object:
        if type(value) is not bool:
            raise ValueError("must be a boolean")
        return value

    @model_validator(mode="after")
    def canonical_references(self) -> Self:
        provider_ids = [provider.id for provider in self.identity_providers]
        method_ids = [method.id for method in self.login_methods]
        if len(provider_ids) != len(set(provider_ids)) or len(method_ids) != len(set(method_ids)):
            raise ValueError("identity provider and login method ids must be unique")
        if any(method.provider_id is not None and method.provider_id not in provider_ids for method in self.login_methods):
            raise ValueError("login method provider_id must reference a declared identity provider")
        document = self.model_dump_json().lower()
        if "http://" in document or "https://" in document:
            raise ValueError("provider URLs must be supplied by env handles")
        return self


class AppAuthContractError(ValueError):
    """Invalid app authentication declaration; diagnostics omit submitted values."""


def validate_app_auth_contract(value: object) -> AppAuthContract:
    try:
        return AppAuthContract.model_validate(value)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in issue['loc']) or 'contract'}: {issue['msg']}"
            for issue in exc.errors(include_input=False)
        )
        raise AppAuthContractError(f"Invalid {APP_AUTH_CONFIG_PATH}: {problems}") from None


def load_app_auth_contract(app_root: Path, *, auth_required: bool = False) -> AppAuthContract | None:
    if type(auth_required) is not bool:
        raise AppAuthContractError("app.json.authRequired must be a boolean")
    path = Path(app_root) / APP_AUTH_CONFIG_PATH
    if not path.exists():
        if auth_required:
            raise AppAuthContractError(f"app.json.authRequired=true requires {APP_AUTH_CONFIG_PATH}")
        return None
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError):
        raise AppAuthContractError(f"Cannot read valid YAML from {APP_AUTH_CONFIG_PATH}") from None
    contract = validate_app_auth_contract(value)
    if not auth_required:
        raise AppAuthContractError(f"{APP_AUTH_CONFIG_PATH} requires app.json.authRequired=true")
    return contract


def app_auth_route_entries(contract: AppAuthContract) -> list[dict[str, Any]]:
    """Normal app route declarations for the shared browser auth pages."""
    return [
        {
            "path": path, "component": component, "label": title,
            "meta": {"requiresAuth": False, "appShell": False, "title": title},
        }
        for path, component, title in (
            (contract.routes.login, "LoginPage", "Sign in"),
            (contract.routes.callback, "AuthCallbackPage", "Completing sign in"),
        )
    ]


def validate_app_auth_route_bindings(contract: AppAuthContract, pages: object) -> None:
    """Auth entry routes must exist and permit an unauthenticated browser."""
    if not isinstance(pages, list):
        raise AppAuthContractError("ui/route_manifest.json must declare auth routes in pages")
    if contract.routes.login == contract.routes.callback:
        raise AppAuthContractError("Auth login and callback routes must be distinct")
    for field in ("login", "callback"):
        path = getattr(contract.routes, field)
        matches = [page for page in pages if isinstance(page, dict) and page.get("path") == path]
        if len(matches) != 1:
            raise AppAuthContractError(f"Auth routes.{field} must resolve to exactly one declared UI route")
        page = matches[0]
        meta = page.get("meta")
        if not isinstance(page.get("component"), str) or not page["component"].strip():
            raise AppAuthContractError(f"Auth routes.{field} must bind a registered component")
        if not isinstance(meta, dict) or meta.get("requiresAuth") is not False:
            raise AppAuthContractError(f"Auth routes.{field} must declare meta.requiresAuth=false")
        if any(meta.get(key) for key in ("requiresRole", "requiredRole", "roles", "routeAuth")):
            raise AppAuthContractError(f"Auth routes.{field} cannot require roles or route authorization")


def compose_app_auth_routes(contract: AppAuthContract, pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add shared auth route declarations while preserving valid app custom pages."""
    composed = list(pages)
    declared_paths = {page.get("path") for page in pages if isinstance(page, dict)}
    composed.extend(page for page in app_auth_route_entries(contract) if page["path"] not in declared_paths)
    validate_app_auth_route_bindings(contract, composed)
    return composed


async def build_app_auth_projection(contract: AppAuthContract | None) -> dict[str, Any]:
    """Expose public app behavior and the canonical runtime's effective auth mode."""
    config = resolve_auth_config()
    local_development = config.explicitly_disabled and config.environment.permits_no_auth
    user = None
    if local_development:
        claims = await get_auth_adapter().validate_token("")
        user = {
            "id": claims.user_id,
            "user_id": claims.user_id,
            "email": claims.email,
            "name": claims.name,
            "roles": list(claims.roles),
            "scopes": list(claims.scopes),
            "app_id": claims.app_id,
            "tenant_id": claims.tenant_id,
            "workspace_id": claims.workspace_id,
        }
    frontend = None
    if contract is not None:
        handles = contract.frontend
        frontend = {
            "authority": os.getenv(handles.authority_env, "").strip(),
            "discovery_url": os.getenv(handles.discovery_url_env, "").strip(),
            "client_id": os.getenv(handles.client_id_env, "").strip(),
            "redirect_uri": os.getenv(handles.redirect_uri_env, "").strip(),
            "scope": os.getenv(handles.scope_env, "").strip(),
        }
    return {
        "required": contract is not None,
        "contract": contract.model_dump(mode="json") if contract is not None else None,
        "frontend": frontend,
        "runtime": {
            "enabled": config.enabled,
            "provider": config.provider,
            "local_development": local_development,
            "user": user,
        },
    }
