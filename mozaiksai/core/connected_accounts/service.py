"""Connect, use and disconnect end users' third-party accounts in one app.

Three parties, three powers:

* **The end user** connects or disconnects their own account and sees only
  their own connections.
* **The app's admins** decide which declared connections the app offers,
  see whether the app's OAuth client is configured, and see aggregate counts.
  They never see another user's token or account.
* **Plugins** obtain a short-lived access token for the calling user only, for
  the scopes their own declaration lists (:mod:`.runtime`).

Platform operators have no path to a user's grant: no admin or operator
surface returns tokens, and server-owned dispatches cannot obtain one.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from logs.logging_config import get_core_logger
from mozaiksai.core.audit.audit_logger import AuditEventKind, AuditRecord, get_audit_logger
from mozaiksai.core.secrets.app_secrets import (
    SecretResolutionError,
    inspect_secret_config,
    resolve_secret,
)
from mozaiksai.core.secrets.connector_vault import (
    ConnectorVaultBackend,
    get_connector_vault_backend,
)

from . import oauth
from .definitions import (
    AccountConnection,
    AccountConnectionsConfigError,
    load_account_connections,
    load_module_account_connections,
)
from .errors import (
    AccountConnectionRequired,
    AccountProviderUnavailable,
    UnknownAccountConnection,
)
from .providers import AccountProvider, AccountProviderError, get_provider, known_providers
from .store import ConnectedAccountStore

logger = get_core_logger("connected_accounts")

FLOW_TTL = timedelta(minutes=10)
FLOW_RESULT_TTL = timedelta(minutes=5)
# Refresh tokens stay valid until revoked; keep the vault record well past Google's
# six-month inactivity window so an unused grant ends at the provider, not here. A grant
# in use is re-stored before its record would expire.
REFRESH_TOKEN_VAULT_TTL_DAYS = 400
_VAULT_RENEW_WITHIN = timedelta(days=30)
# How long another replica's change to the app's offering may take to apply here.
OFFERING_CACHE_SECONDS = 15.0
CLIENT_CREDENTIALS_CACHE_SECONDS = 600.0
_ACCESS_TOKEN_EXPIRY_MARGIN_SECONDS = 60

BINDING_REFUSED_MESSAGE = (
    "This connection was started from a different browser or a different address. Start it again "
    "from the same address."
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class StartedFlow:
    authorize_url: str
    flow_id: str
    binding: str


@dataclass(frozen=True)
class CompletedFlow:
    status: str  # connected | failed
    message: str
    # The binding cookie to clear: set only when the request proved it holds this flow's binding.
    clear_cookie: str | None


@dataclass
class _CachedToken:
    access_token: str
    expires_at: float
    scopes: frozenset[str]


class ConnectedAccountsService:
    def __init__(
        self,
        *,
        app_id: str,
        app_root: str | Path,
        modules: Iterable[tuple[str, Path]] | None = None,
        store: ConnectedAccountStore | None = None,
        vault: ConnectorVaultBackend | None = None,
    ) -> None:
        """*modules* are the ``(module_id, module_dir)`` pairs the host loaded; without them
        every module under ``<app_root>/modules`` is read."""
        self.app_id = str(app_id)
        self.app_root = Path(app_root)
        self._store = store or ConnectedAccountStore()
        self._vault = vault
        # Keyed by (user_id, provider_id, connection_id): each connection gets a token for its own scopes.
        self._tokens: dict[tuple[str, str, str], _CachedToken] = {}
        self._locks: dict[tuple[str, str, str], asyncio.Lock] = {}
        self._withheld: frozenset[str] = frozenset()
        self._withheld_read_at: float | None = None
        self._credentials: dict[str, tuple[float, tuple[str, str]]] = {}
        self.config_error: str | None = None
        try:
            self._declared = (
                load_module_account_connections(modules) if modules is not None
                else load_account_connections(self.app_root)
            )
        except AccountConnectionsConfigError as exc:
            # A broken declaration offers nothing rather than something unintended.
            logger.error("CONNECTED_ACCOUNTS_CONFIG_INVALID: %s", exc)
            self.config_error = str(exc)
            self._declared = ()

    # -- configuration -----------------------------------------------------------------

    @property
    def declared(self) -> tuple[AccountConnection, ...]:
        return self._declared

    def _declared_connection(self, connection_id: str) -> AccountConnection | None:
        return next((connection for connection in self._declared if connection.id == connection_id), None)

    async def _withheld_ids(self) -> frozenset[str]:
        clock = time.monotonic()
        if self._withheld_read_at is None or clock - self._withheld_read_at > OFFERING_CACHE_SECONDS:
            self._withheld = await self._store.withheld_connections(app_id=self.app_id)
            self._withheld_read_at = clock
        return self._withheld

    async def offers(self, connection_id: str) -> bool:
        return self._declared_connection(connection_id) is not None and connection_id not in await self._withheld_ids()

    async def offered(self) -> tuple[AccountConnection, ...]:
        withheld = await self._withheld_ids()
        return tuple(connection for connection in self._declared if connection.id not in withheld)

    def _vault_backend(self) -> ConnectorVaultBackend:
        if self._vault is None:
            self._vault = get_connector_vault_backend()
        return self._vault

    def client_configured(self, provider: AccountProvider) -> bool:
        """Whether the app's OAuth client for *provider* is configured, without reading its values."""
        try:
            return all(
                inspect_secret_config(name, app_root=self.app_root).configured
                for name in (provider.client_id_env, provider.client_secret_env)
            )
        except Exception:  # an invalid secrets contract configures nothing
            return False

    async def client_credentials(self, provider: AccountProvider) -> tuple[str, str] | None:
        """The app's OAuth client for *provider*, or None when it is not configured."""
        cached = self._credentials.get(provider.id)
        if cached is not None and time.monotonic() - cached[0] < CLIENT_CREDENTIALS_CACHE_SECONDS:
            return cached[1]

        def resolve() -> tuple[str, str]:
            return (
                resolve_secret(provider.client_id_env, app_root=self.app_root),
                resolve_secret(provider.client_secret_env, app_root=self.app_root),
            )

        try:
            # A Key Vault reference is resolved over the network; keep it off the event loop.
            client_id, client_secret = await asyncio.to_thread(resolve)
        except SecretResolutionError:
            return None
        if not client_id or not client_secret:
            return None
        self._credentials[provider.id] = (time.monotonic(), (client_id, client_secret))
        return client_id, client_secret

    def _grant_scope_id(self, user_id: str) -> str:
        # Opaque, so no user id or email appears in vault secret names or metadata.
        return "u-" + _digest(json.dumps([self.app_id, user_id]))[:40]

    def _forget_tokens(self, user_id: str, provider_id: str) -> None:
        for key in [key for key in self._tokens if key[0] == user_id and key[1] == provider_id]:
            del self._tokens[key]

    # -- the user's view -------------------------------------------------------------

    async def list_for_user(self, user_id: str) -> dict[str, Any]:
        records = {
            str(row.get("provider")): row
            for row in await self._store.list_connections(app_id=self.app_id, user_id=user_id)
        }
        connections: list[dict[str, Any]] = []
        for connection in await self.offered():
            provider = get_provider(connection.provider)
            record = records.get(connection.provider)
            granted = set(record.get("granted_scopes") or []) if record else set()
            if not self.client_configured(provider):
                status = "unavailable"
            elif record is None:
                status = "not_connected"
            elif record.get("status") == "needs_reconnect":
                status = "reconnect_required"
            elif not set(connection.scopes) <= granted:
                status = "not_connected"
            else:
                status = "connected"
            connections.append({**connection.summary(), "provider_label": provider.label, "status": status})
        # Every account the user has connected, including one whose connections the app
        # no longer offers: the user can always see and disconnect their own grants.
        providers = []
        for provider_id, record in sorted(records.items()):
            try:
                label = get_provider(provider_id).label
            except AccountProviderError:
                label = provider_id
            providers.append({
                "provider": provider_id,
                "label": label,
                "account_email": record.get("account_email"),
                "status": "reconnect_required" if record.get("status") == "needs_reconnect" else "connected",
                "connected_at": _iso(record.get("connected_at")),
            })
        return {"connections": connections, "providers": providers}

    async def start(self, *, user_id: str, connection_id: str, redirect_uri: str, binding_cookie: str) -> StartedFlow:
        """Begin connecting *connection_id* for *user_id*.

        *binding_cookie* names the cookie the caller sets to the returned
        ``binding``; the callback must present exactly that cookie.
        """
        connection = self._declared_connection(connection_id)
        if connection is None or not await self.offers(connection_id):
            raise UnknownAccountConnection(connection_id)
        provider = get_provider(connection.provider)
        credentials = await self.client_credentials(provider)
        if credentials is None:
            raise AccountConnectionRequired(
                connection_id=connection.id, provider=connection.provider, label=connection.label, reason="unavailable",
            )
        state = secrets.token_urlsafe(32)
        flow_id = secrets.token_urlsafe(24)
        binding = secrets.token_urlsafe(32)
        verifier, challenge = oauth.pkce_pair()
        scopes = list(dict.fromkeys((*provider.identity_scopes, *connection.scopes)))
        now = _now()
        await self._store.create_flow({
            "state_digest": _digest(state),
            "flow_id": flow_id,
            "owner_user_id": user_id,
            "app_id": self.app_id,
            "connection_id": connection.id,
            "provider": connection.provider,
            "binding_digest": _digest(binding),
            "binding_cookie": binding_cookie,
            "code_verifier": verifier,
            "requested_scopes": scopes,
            "redirect_uri": redirect_uri,
            "status": "pending",
            "created_at": now,
            "updated_at": now,
            "expires_at": now + FLOW_TTL,
        })
        url = oauth.authorize_url(
            provider, client_id=credentials[0], redirect_uri=redirect_uri, scopes=scopes,
            state=state, code_challenge=challenge,
        )
        return StartedFlow(authorize_url=url, flow_id=flow_id, binding=binding)

    async def complete(
        self, *, state: str, code: str, error: str, read_cookie: Callable[[str], list[str]],
    ) -> CompletedFlow:
        """Finish a flow on the provider's callback. Every failure ends the flow; nothing is retried.

        *read_cookie* returns every value the callback request carries for a cookie name.
        """
        if not state:
            return CompletedFlow("failed", "The connection response was incomplete. Start the connection again.", None)
        now = _now()
        flow = await self._store.claim_flow(state_digest=_digest(state), now=now)
        if flow is None or flow.get("app_id") != self.app_id:
            return CompletedFlow("failed", "This connection request is invalid or has expired. Start it again.", None)
        cookie_name = str(flow.get("binding_cookie") or "")
        bindings = read_cookie(cookie_name) if cookie_name else []
        if len(bindings) != 1 or not hmac.compare_digest(_digest(bindings[0]), str(flow.get("binding_digest") or "")):
            logger.warning("CONNECTED_ACCOUNT_BINDING_REFUSED: provider=%s", flow.get("provider"))
            # Whatever binding cookie this browser holds belongs to another flow: leave it.
            return await self._fail(flow, BINDING_REFUSED_MESSAGE, now, clear_cookie=None)
        connection = self._declared_connection(str(flow.get("connection_id") or ""))
        if connection is None or not await self.offers(connection.id):
            return await self._fail(flow, "This app no longer offers that connection.", now, clear_cookie=cookie_name)
        if error:
            return await self._fail(flow, "Access was not granted. You can close this window.", now, clear_cookie=cookie_name)
        if not code:
            return await self._fail(
                flow, "The connection response was incomplete. Start the connection again.", now, clear_cookie=cookie_name,
            )
        provider = get_provider(connection.provider)
        credentials = await self.client_credentials(provider)
        if credentials is None:
            return await self._fail(
                flow, f"This app's {provider.label} connection is not configured.", now, clear_cookie=cookie_name,
            )
        try:
            tokens = await oauth.exchange_code(
                provider,
                client_id=credentials[0],
                client_secret=credentials[1],
                redirect_uri=str(flow.get("redirect_uri") or ""),
                code=code,
                code_verifier=str(flow.get("code_verifier") or ""),
                requested_scopes=list(flow.get("requested_scopes") or []),
            )
        except oauth.AccountTokenError as exc:
            logger.warning("CONNECTED_ACCOUNT_EXCHANGE_FAILED: provider=%s reason=%s", provider.id, exc.reason)
            return await self._fail(
                flow, f"{provider.label} did not complete the connection. Start it again.", now, clear_cookie=cookie_name,
            )
        user_id = str(flow.get("owner_user_id") or "")
        prior = await self._store.get_connection(app_id=self.app_id, user_id=user_id, provider=provider.id)

        async def discard() -> None:
            # Revoking any token of a grant ends the whole grant at the provider, including
            # access the user gave this app earlier; only a brand-new grant is revoked.
            if prior is None:
                await oauth.revoke(provider, tokens.refresh_token or tokens.access_token)

        if not set(connection.scopes) <= tokens.scopes:
            # The user unticked a permission on the consent screen: keep nothing from this attempt.
            await discard()
            return await self._fail(
                flow, f"{connection.label} needs every permission it asked for. Start again and allow them all.",
                now, clear_cookie=cookie_name,
            )
        if not tokens.refresh_token:
            await discard()
            return await self._fail(
                flow, f"{provider.label} did not grant lasting access. Start the connection again.", now,
                clear_cookie=cookie_name,
            )
        stored = await self._vault_backend().store_secret(
            scope="user",
            scope_id=self._grant_scope_id(user_id),
            service=provider.id,
            secret_value=json.dumps({"refresh_token": tokens.refresh_token}),
            display_name=f"{provider.label} connected account",
            ttl_days=REFRESH_TOKEN_VAULT_TTL_DAYS,
        )
        if not stored.get("success"):
            logger.error("CONNECTED_ACCOUNT_VAULT_WRITE_FAILED: provider=%s", provider.id)
            await discard()
            return await self._fail(flow, "The connection could not be saved. Try again later.", now, clear_cookie=cookie_name)
        claims = tokens.id_token_claims
        email = claims.get("email") if claims.get("email_verified", True) else None
        await self._store.save_connection(
            app_id=self.app_id,
            user_id=user_id,
            provider=provider.id,
            granted_scopes=sorted(tokens.scopes),
            account_email=str(email) if isinstance(email, str) else None,
            account_subject=str(claims.get("sub")) if claims.get("sub") else None,
            now=now,
        )
        # Tokens minted for this provider before the new grant carry the old scopes.
        self._forget_tokens(user_id, provider.id)
        await self._store.finish_flow(
            flow_id=str(flow["flow_id"]), status="connected", message=f"{connection.label} connected.",
            now=now, keep_for=FLOW_RESULT_TTL,
        )
        await self._audit(AuditEventKind.ACCOUNT_CONNECTED, user_id, provider.id, extra={
            "connection_id": connection.id, "scopes": sorted(tokens.scopes),
        })
        return CompletedFlow("connected", f"{connection.label} connected.", cookie_name)

    async def _fail(self, flow: dict[str, Any], message: str, now: datetime, *, clear_cookie: str | None) -> CompletedFlow:
        await self._store.finish_flow(
            flow_id=str(flow["flow_id"]), status="failed", message=message, now=now, keep_for=FLOW_RESULT_TTL,
        )
        await self._audit(
            AuditEventKind.ACCOUNT_CONNECTION_FAIL, str(flow.get("owner_user_id") or ""), str(flow.get("provider") or ""),
            outcome="fail", extra={"connection_id": flow.get("connection_id")},
        )
        return CompletedFlow("failed", message, clear_cookie)

    async def flow_status(self, *, user_id: str, flow_id: str) -> dict[str, Any]:
        flow = await self._store.get_flow(flow_id=flow_id, owner_user_id=user_id, app_id=self.app_id)
        if flow is None:
            return {"status": "unknown"}
        status = str(flow.get("status") or "pending")
        body: dict[str, Any] = {"status": "pending" if status == "completing" else status}
        if status in {"connected", "failed"}:
            body["message"] = flow.get("message")
        return body

    async def disconnect(self, *, user_id: str, provider_id: str) -> dict[str, Any]:
        """Revoke the user's grant at the provider, then forget it here.

        Local removal happens even when the provider cannot be reached or is no
        longer known, so a user can always take their grant out of the app.
        """
        provider_id = str(provider_id or "").strip()
        try:
            provider: AccountProvider | None = get_provider(provider_id)
        except AccountProviderError:
            provider = None
        vault = self._vault_backend()
        scope_id = self._grant_scope_id(user_id)
        revoked = False
        secret = await vault.get_secret(scope="user", scope_id=scope_id, service=provider_id)
        if secret.get("status") == "found":
            try:
                refresh_token = json.loads(str(secret.get("secret_value") or "{}")).get("refresh_token")
            except ValueError:
                refresh_token = None
            if refresh_token and provider is not None:
                revoked = await oauth.revoke(provider, refresh_token)
            await vault.delete_secret(scope="user", scope_id=scope_id, service=provider_id)
        removed = await self._store.delete_connection(app_id=self.app_id, user_id=user_id, provider=provider_id)
        self._forget_tokens(user_id, provider_id)
        found = removed or secret.get("status") == "found"
        if found:
            await self._audit(
                AuditEventKind.ACCOUNT_DISCONNECTED, user_id, provider_id, extra={"revoked_at_provider": revoked},
            )
        return {"disconnected": found, "revoked_at_provider": revoked}

    # -- account lifecycle -------------------------------------------------------------

    async def delete_user_data(self, *, user_id: str) -> dict[str, Any]:
        """Account deletion: revoke and forget every grant the user gave this app."""
        records = await self._store.list_connections(app_id=self.app_id, user_id=user_id)
        # A grant can be in the vault without a record (a save interrupted midway).
        providers = sorted({str(row.get("provider") or "") for row in records} | set(known_providers()))
        disconnected = revoked = 0
        for provider_id in providers:
            if not provider_id:
                continue
            result = await self.disconnect(user_id=user_id, provider_id=provider_id)
            disconnected += int(result["disconnected"])
            revoked += int(result["revoked_at_provider"])
        flows = await self._store.delete_flows_for_user(app_id=self.app_id, user_id=user_id)
        return {"deleted_count": disconnected + flows, "revoked_at_provider": revoked}

    async def export_user_data(self, *, user_id: str) -> list[dict[str, Any]]:
        """The user's connection records for a data export. Never a token."""
        rows = await self._store.list_connections(app_id=self.app_id, user_id=user_id)
        return [
            {
                "provider": row.get("provider"),
                "account_email": row.get("account_email"),
                "granted_scopes": list(row.get("granted_scopes") or []),
                "status": row.get("status"),
                "connected_at": _iso(row.get("connected_at")),
                "updated_at": _iso(row.get("updated_at")),
            }
            for row in rows
        ]

    # -- plugins ---------------------------------------------------------------------

    async def access_token(self, *, user_id: str, connection_id: str, refresh: bool = False) -> str:
        """A fresh access token for *user_id*, limited to what *connection_id* declares.

        *refresh* skips the cached token, for a caller whose provider call was
        just refused with it. Callers must have established that *user_id* is the
        requesting user (see
        :func:`mozaiksai.core.connected_accounts.runtime.account_access_token`).
        """
        connection = self._declared_connection(connection_id)
        if connection is None:
            raise UnknownAccountConnection(connection_id)

        def required(reason: str, missing: tuple[str, ...] = ()) -> AccountConnectionRequired:
            return AccountConnectionRequired(
                connection_id=connection.id, provider=connection.provider, label=connection.label,
                reason=reason, missing_scopes=missing,
            )

        if not await self.offers(connection.id):
            raise required("unavailable")
        provider = get_provider(connection.provider)
        credentials = await self.client_credentials(provider)
        if credentials is None:
            raise required("unavailable")
        record = await self._store.get_connection(app_id=self.app_id, user_id=user_id, provider=provider.id)
        if record is None:
            raise required("not_connected")
        if record.get("status") == "needs_reconnect":
            raise required("reconnect_required")
        needed = set(connection.scopes)
        missing = tuple(sorted(needed - set(record.get("granted_scopes") or [])))
        if missing:
            raise required("missing_scopes", missing)
        key = (user_id, provider.id, connection.id)
        refused = self._tokens.get(key) if refresh else None

        def usable(cached: _CachedToken | None) -> bool:
            return (
                cached is not None and cached is not refused and needed <= cached.scopes
                and cached.expires_at - _ACCESS_TOKEN_EXPIRY_MARGIN_SECONDS > time.time()
            )

        cached = self._tokens.get(key)
        if usable(cached):
            return cached.access_token  # type: ignore[union-attr]
        async with self._locks.setdefault(key, asyncio.Lock()):
            # Another caller may have refreshed while this one waited.
            cached = self._tokens.get(key)
            if usable(cached):
                return cached.access_token  # type: ignore[union-attr]
            return await self._refresh(user_id, provider, credentials, connection, required)

    async def _refresh(
        self,
        user_id: str,
        provider: AccountProvider,
        credentials: tuple[str, str],
        connection: AccountConnection,
        required: Callable[..., AccountConnectionRequired],
    ) -> str:
        vault = self._vault_backend()
        scope_id = self._grant_scope_id(user_id)
        secret = await vault.get_secret(scope="user", scope_id=scope_id, service=provider.id)
        if secret.get("status") == "not_found":
            await self._store.mark_needs_reconnect(app_id=self.app_id, user_id=user_id, provider=provider.id, now=_now())
            raise required("reconnect_required")
        if secret.get("status") != "found":
            raise AccountProviderUnavailable("The stored connection could not be read.")
        try:
            refresh_token = json.loads(str(secret.get("secret_value") or "{}")).get("refresh_token")
        except ValueError:
            refresh_token = None
        if not refresh_token:
            raise AccountProviderUnavailable("The stored connection is unreadable.")
        try:
            tokens = await oauth.refresh_access_token(
                provider, client_id=credentials[0], client_secret=credentials[1],
                refresh_token=refresh_token, requested_scopes=list(connection.scopes),
            )
        except oauth.AccountTokenError as exc:
            if exc.reason == "invalid_grant":
                # The user revoked access at the provider, or the grant expired.
                await self._store.mark_needs_reconnect(app_id=self.app_id, user_id=user_id, provider=provider.id, now=_now())
                self._forget_tokens(user_id, provider.id)
                raise required("reconnect_required") from exc
            if exc.reason == "invalid_scope":
                # The grant no longer covers this connection (the user removed a permission at the provider).
                raise required("missing_scopes", tuple(connection.scopes)) from exc
            raise AccountProviderUnavailable(str(exc)) from exc
        rotated = bool(tokens.refresh_token) and tokens.refresh_token != refresh_token
        if rotated or _expires_soon(secret.get("expires_at")):
            await vault.store_secret(
                scope="user", scope_id=scope_id, service=provider.id,
                secret_value=json.dumps({"refresh_token": tokens.refresh_token if rotated else refresh_token}),
                display_name=f"{provider.label} connected account", ttl_days=REFRESH_TOKEN_VAULT_TTL_DAYS,
            )
        if not set(connection.scopes) <= tokens.scopes:
            raise required("missing_scopes", tuple(sorted(set(connection.scopes) - tokens.scopes)))
        self._tokens[(user_id, provider.id, connection.id)] = _CachedToken(
            tokens.access_token, tokens.expires_at, tokens.scopes,
        )
        return tokens.access_token

    # -- the app's admins --------------------------------------------------------------

    async def admin_summary(self, *, redirect_uri: str | None = None) -> dict[str, Any]:
        """What the app's admins may see: configuration and aggregate counts, never identities or tokens."""
        counts = await self._store.count_connections(app_id=self.app_id)
        withheld = await self._withheld_ids()
        rows = []
        for connection in self._declared:
            row: dict[str, Any] = {**connection.summary(), "offered": connection.id not in withheld}
            try:
                provider = get_provider(connection.provider)
            except AccountProviderError as exc:
                rows.append({**row, "provider_label": connection.provider, "client_configured": False, "error": str(exc)})
                continue
            rows.append({
                **row,
                "provider_label": provider.label,
                "client_configured": self.client_configured(provider),
                # Setting names only, so an admin knows what to configure; never their values.
                "client_settings": [provider.client_id_env, provider.client_secret_env],
            })
        return {
            "connections": rows,
            "providers": [{"provider": provider, **values} for provider, values in sorted(counts.items())],
            # Register this in the provider's console as the OAuth client's redirect URI.
            "redirect_uri": redirect_uri,
            "config_error": self.config_error,
        }

    async def set_offered(self, *, connection_id: str, offered: bool, actor_id: str) -> dict[str, Any]:
        """Offer or withhold a declared connection. Users' existing grants stay theirs to disconnect."""
        connection = self._declared_connection(connection_id)
        if connection is None:
            raise UnknownAccountConnection(connection_id)
        await self._store.set_offered(app_id=self.app_id, connection_id=connection.id, offered=offered, now=_now())
        self._withheld_read_at = None
        if not offered:
            for key in [key for key in self._tokens if key[2] == connection.id]:
                del self._tokens[key]
        await get_audit_logger().log(AuditRecord(
            kind=AuditEventKind.ADMIN_WRITE,
            actor_id=actor_id,
            app_id=self.app_id,
            resource="connected_accounts.offering",
            action=connection.id,
            inputs_hash=_digest(json.dumps({"connection_id": connection.id, "offered": offered}, sort_keys=True)),
            extra={"offered": offered},
        ))
        return {"id": connection.id, "offered": offered}

    # -- audit -------------------------------------------------------------------------

    async def _audit(
        self, kind: AuditEventKind, user_id: str, provider: str, *, outcome: str = "ok", extra: dict[str, Any] | None = None,
    ) -> None:
        await get_audit_logger().log(AuditRecord(
            kind=kind,
            actor_id=user_id,
            app_id=self.app_id,
            resource="connected_accounts",
            action=provider,
            outcome=outcome,
            extra=dict(extra or {}),
        ))


def _expires_soon(value: Any) -> bool:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return False
    if not isinstance(value, datetime):
        return False
    expires_at = value if value.tzinfo else value.replace(tzinfo=UTC)
    return expires_at - _now() < _VAULT_RENEW_WITHIN


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=UTC)).isoformat()
    return str(value) if value else None


_SERVICE: ConnectedAccountsService | None = None


def configure_connected_accounts(service: ConnectedAccountsService | None) -> None:
    """Install the host's service (once per app process); ``None`` clears it."""
    global _SERVICE
    _SERVICE = service


def get_connected_accounts_service() -> ConnectedAccountsService | None:
    return _SERVICE


__all__ = [
    "BINDING_REFUSED_MESSAGE",
    "CompletedFlow",
    "ConnectedAccountsService",
    "StartedFlow",
    "configure_connected_accounts",
    "get_connected_accounts_service",
]
